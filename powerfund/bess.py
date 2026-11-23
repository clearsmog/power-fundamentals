"""Battery (BESS) revenue model and floor / toll pricing.

Dispatch. One linear programme per day: a battery of power P (MW) and energy E (MWh)
charges and discharges against the half-hourly GB market index (MID) and may hold
capacity in the three NESO frequency-response services, each sold as a symmetric
low + high pair per 4-hour EFA block (Dynamic Containment 15 min, Moderation 30 min,
Regulation 60 min of energy each way). Holding r MW of a pair takes r MW of headroom
in both directions and r x duration MWh of stored energy and of empty space.

A plan is made on one set of prices and settled on another: perfect foresight plans on
the actual prices (an upper bound); an ex-ante plan uses a price forecast (see
`forecast.py`) and the previous day's response prices, then settles at the actual ones.
A rough Balancing Mechanism estimate (`bm_estimate`) can be added on top.

Not modelled: intraday trading, imbalance, network charges, availability; degradation
only as a cost per MWh discharged.

Contracts (owner's view; the optimiser / offtaker writes them):
  - floor: the optimiser tops annual revenue R up to F, so it pays max(F - R, 0);
  - toll: the optimiser pays a fixed fee T a year and keeps R.
"""

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from scipy.sparse import lil_matrix

from powerfund.sim import cvar, discount_factors

PAIRS = {"DC": 0.25, "DM": 0.5, "DR": 1.0}  # service pair -> hours of energy each way


def dispatch_day(
    price: np.ndarray,
    block: np.ndarray,
    resp: np.ndarray | None,
    power: float = 1.0,
    energy: float = 2.0,
    rte: float = 0.88,
    max_cycles: float = 2.0,
    deg_cost: float = 0.0,
    resp_cap: float = 1.0,
) -> dict:
    """Optimal plan for one day.

    price: (n,) GBP/MWh per half-hour; block: (n,) EFA block index 0..nb-1 per period;
    resp: (nb, 3) pair prices GBP/MW/h for DC, DM, DR (None = wholesale only).
    resp_cap: most of the power that may be held in response in any block, as a share of P.
    Returns charge c, discharge d (MW), state of charge s (MWh), response r (nb, 3) MW.
    """
    n = len(price)
    nb = int(block.max()) + 1
    k = 0 if resp is None else len(PAIRS)
    dur = np.array(list(PAIRS.values()))
    eta = np.sqrt(rte)
    # variable layout: c[0:n], d[n:2n], s[2n:3n], r[3n : 3n + nb*k]
    nv = 3 * n + nb * k
    ic, id_, is_, ir = 0, n, 2 * n, 3 * n

    cost = np.zeros(nv)
    cost[ic:ic + n] = 0.5 * price
    cost[id_:id_ + n] = -0.5 * price + 0.5 * deg_cost
    hours = np.bincount(block, minlength=nb) * 0.5
    if k:
        cost[ir:] = -(resp * hours[:, None]).ravel()

    s0 = energy / 2
    # equality: s_t - s_{t-1} - 0.5 eta c_t + 0.5 d_t / eta = 0, and s_end = s0
    A_eq = lil_matrix((n + 1, nv))
    b_eq = np.zeros(n + 1)
    for t in range(n):
        A_eq[t, is_ + t] = 1
        if t:
            A_eq[t, is_ + t - 1] = -1
        else:
            b_eq[t] = s0
        A_eq[t, ic + t] = -0.5 * eta
        A_eq[t, id_ + t] = 0.5 / eta
    A_eq[n, is_ + n - 1] = 1
    b_eq[n] = s0

    b_ub = []
    A_ub = lil_matrix((4 * n + 1 + (nb if k else 0), nv))
    for t in range(n):
        b = block[t]
        rcols = [ir + b * k + j for j in range(k)]
        # discharge headroom: d_t + sum r <= P ; charge headroom: c_t + sum r <= P
        A_ub[4 * t, id_ + t] = 1
        A_ub[4 * t + 1, ic + t] = 1
        for j, col in enumerate(rcols):
            A_ub[4 * t, col] = 1
            A_ub[4 * t + 1, col] = 1
            # energy: s_t >= sum r*dur  ->  -s_t + sum r*dur <= 0
            A_ub[4 * t + 2, col] = dur[j]
            # space: s_t + sum r*dur <= E
            A_ub[4 * t + 3, col] = dur[j]
        A_ub[4 * t + 2, is_ + t] = -1
        A_ub[4 * t + 3, is_ + t] = 1
        b_ub += [power, power, 0.0, energy]
    # throughput: sum 0.5 d_t <= cycles * E
    A_ub[4 * n, id_:id_ + n] = 0.5
    b_ub.append(max_cycles * energy)
    if k:
        for b in range(nb):
            A_ub[4 * n + 1 + b, ir + b * k:ir + (b + 1) * k] = 1
            b_ub.append(resp_cap * power)

    bounds = [(0, power)] * (2 * n) + [(0, energy)] * n + [(0, power)] * (nb * k)
    res = linprog(
        cost, A_ub=A_ub.tocsr(), b_ub=b_ub, A_eq=A_eq.tocsr(), b_eq=b_eq,
        bounds=bounds, method="highs",
    )
    if res.status != 0:
        raise RuntimeError(res.message)
    x = res.x
    r = x[ir:].reshape(nb, k) if k else np.zeros((nb, 0))
    return {"c": x[ic:ic + n], "d": x[id_:id_ + n], "s": x[is_:is_ + n], "r": r, "hours": hours,
            "block": block}


def settle(plan: dict, price: np.ndarray, resp: np.ndarray | None, deg_cost: float = 0.0) -> dict:
    """Revenue (GBP) of a plan at the given prices, split wholesale / response."""
    wholesale = float(0.5 * (price * (plan["d"] - plan["c"])).sum() - 0.5 * deg_cost * plan["d"].sum())
    response = 0.0 if resp is None else float((resp * plan["hours"][:, None] * plan["r"]).sum())
    return {"wholesale": wholesale, "response": response}


def efa_blocks(index: pd.DatetimeIndex) -> tuple[pd.Series, np.ndarray]:
    """EFA day and block (0..5) of each half-hour. EFA days start at 23:00 UK time."""
    local = index.tz_convert("Europe/London")
    shifted = local + pd.Timedelta(hours=1)
    efa_day = pd.Series(shifted.date, index=index)
    block = (shifted.hour // 4).to_numpy()
    return efa_day, block


def pair_prices(resp_wide: pd.DataFrame) -> pd.DataFrame:
    """Symmetric pair price (L + H, GBP/MW/h) per EFA block for DC, DM, DR."""
    return pd.DataFrame(
        {p: resp_wide[f"{p}L"] + resp_wide[f"{p}H"] for p in PAIRS}, index=resp_wide.index
    )


def bm_estimate(plan: dict, power: float, da: np.ndarray, sp: np.ndarray, rate: float) -> float:
    """Rough Balancing Mechanism revenue (GBP) for a plan, after Modo Energy's dispatch-rate method.

    Spare power in each direction (after the wholesale position and response holding) is
    offered to the BM; a share `rate` of it is accepted, earning the system price's premium
    over the day-ahead price: offers when SP > DA, bids when SP < DA. Energy is assumed to
    net out over the day. An estimate, not a dispatch model.
    """
    held = plan["r"].sum(axis=1)[plan["block"]] if plan["r"].size else 0.0
    up = np.clip(power - plan["d"] + plan["c"] - held, 0, None)
    down = np.clip(power - plan["c"] + plan["d"] - held, 0, None)
    gain = up * np.clip(sp - da, 0, None) + down * np.clip(da - sp, 0, None)
    return float(rate * 0.5 * np.nansum(gain))


def run_backtest(
    actual: pd.Series,
    plan_price: pd.Series | None,
    pairs: pd.DataFrame | None,
    plan_pairs: pd.DataFrame | None = None,
    sp: pd.Series | None = None,
    bm_rate: float = 0.0,
    power: float = 1.0,
    energy: float = 2.0,
    rte: float = 0.88,
    max_cycles: float = 2.0,
    deg_cost: float = 0.0,
    resp_cap: float = 1.0,
) -> pd.DataFrame:
    """Daily revenue per MW: plan on (plan_price, plan_pairs), settle at (actual, pairs).

    Half-hourly UTC series. plan_price None = perfect foresight on prices; plan_pairs None =
    plan on the actual response prices. sp (system price) adds a BM estimate where present.
    Days with a missing plan price are skipped.
    """
    actual = actual.interpolate(limit_direction="both")
    plan_price = actual if plan_price is None else plan_price.reindex(actual.index)
    efa_day, block = efa_blocks(actual.index)

    def per_period(pp):
        if pp is None:
            return None
        ts = pd.Series(actual.index, index=actual.index)
        bstart = ts.groupby([efa_day.to_numpy(), block]).transform("min")
        return pp.reindex(pd.DatetimeIndex(bstart)).to_numpy()

    act_r = per_period(pairs)
    plan_r = act_r if plan_pairs is None else per_period(plan_pairs)
    spv = None if sp is None else sp.reindex(actual.index).to_numpy()
    a, f = actual.to_numpy(), plan_price.to_numpy()
    kw = dict(power=power, energy=energy, rte=rte, max_cycles=max_cycles,
              deg_cost=deg_cost, resp_cap=resp_cap)
    out = []
    for day, idx in pd.Series(np.arange(len(actual)), index=efa_day.values).groupby(level=0):
        pos = idx.to_numpy()
        if len(pos) < 46 or np.isnan(f[pos]).any():
            continue
        _, b = np.unique(block[pos], return_inverse=True)
        nb = b.max() + 1

        def blocks(arr):
            if arr is None:
                return None
            v = np.nan_to_num(arr[pos], nan=0.0)
            return np.array([v[b == j][0] for j in range(nb)])

        plan = dispatch_day(f[pos], b, blocks(plan_r), **kw)
        plan["block"] = b
        rev = settle(plan, a[pos], blocks(act_r), deg_cost)
        row = {"day": pd.Timestamp(day), "wholesale": rev["wholesale"], "response": rev["response"]}
        if spv is not None and not np.isnan(spv[pos]).all():
            row["bm"] = bm_estimate(plan, power, a[pos], spv[pos], bm_rate)
        out.append(row)
    return pd.DataFrame(out).set_index("day") / power


# --- contract pricing -----------------------------------------------------------


def price_floor(sims: np.ndarray, floor: float, rate: float, coc: float, q: float = 0.95) -> dict:
    """Optimiser's cost of a revenue floor, GBP/MW/yr level annuity.

    fair = E[PV of top-ups]; risk charge = coc x (CVaR_q of PV top-ups - fair), the
    cost of holding capital against the bad tail.
    """
    df = discount_factors(sims.shape[1], rate)
    pv = (np.maximum(floor - sims, 0) * df).sum(axis=1)
    ann = df.sum()
    fair = pv.mean()
    charge = coc * (cvar(pv, q, upper=True) - fair)
    return {
        "floor": floor,
        "fair_premium": fair / ann,
        "risk_charge": charge / ann,
        "premium": (fair + charge) / ann,
        "p_triggered": float((sims < floor).mean()),
    }


def price_toll(sims: np.ndarray, rate: float, coc: float, q: float = 0.95) -> dict:
    """Fixed annual toll an optimiser could pay for the battery's revenue, GBP/MW/yr."""
    df = discount_factors(sims.shape[1], rate)
    pv = (sims * df).sum(axis=1)
    ann = df.sum()
    fair = pv.mean()
    charge = coc * (fair - cvar(pv, q, upper=False))
    return {"expected_revenue": fair / ann, "risk_charge": charge / ann, "toll": (fair - charge) / ann}
