"""GB deal pricing: solar balancing PPA fee, fixed-price solar PPA, BESS floor and toll, portfolio.

Every number this prints is written to output/gb_pricing_*.{csv,json,png}.
Assumptions are the constants below; the README lists which are measured and which are chosen.
"""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from powerfund import bess, gb, gbdata, ppa, sim

START, END = "2024-01-01", "2026-10-31"
OUT = Path(__file__).resolve().parent.parent / "output"

# chosen assumptions (README "Assumptions")
SITE_MW = 40.0  # solar farm
BESS_MW, BESS_H = 20.0, 2.0  # battery in the portfolio
RTE, MAX_CYCLES = 0.88, 1.5  # round-trip efficiency, discharge cycles per day
RESP_CAP = 0.5  # share of battery power that may sit in frequency response (contract base case)
CONTRACT_CONFIG = "2h stacked, response cap 50%"
NMAE = [0.04, 0.06, 0.08]  # site day-ahead forecast error, share of capacity, daylight
NMAE_BASE = 0.06
RHO = 0.95  # half-hourly persistence of the site-specific error
SEEDS = 30
PPA_YEARS, BESS_YEARS = 10, 5
RATE = 0.07  # discount rate
COC = 0.10  # cost of capital on tail risk
Q = 0.95  # tail for the risk charge
LEVEL_VOL = 0.15  # annual volatility of the price level
CAPTURE_DRIFT = -0.01  # relative change in the solar capture ratio per year
N_SIMS = 20_000


def load() -> pd.DataFrame:
    df = gb.fetch_gb(START, END).rename(columns={"ssp": "sp"})[["sp", "niv", "mid"]]
    df = df.join(gb.fetch_solar(START, END)).join(gbdata.fetch_pvlive(START, END))
    return df.dropna(subset=["sp", "pv_cf"])


# --- A. balancing PPA ------------------------------------------------------------


def balancing(df: pd.DataFrame) -> pd.DataFrame:
    """Imbalance cost per MWh by forecast quality: national-only and calibrated single sites."""
    d = df.dropna(subset=["mid", "solar", "solar_fc"])
    year = d.index.tz_convert("Europe/London").year
    mwh_year = (d["pv_cf"] * 0.5).groupby(year).sum()
    rows = []
    for target in [None] + NMAE:
        sigma = 0.0 if target is None else ppa.calibrate_sigma(d, target, RHO)
        annual = []
        for seed in range(1 if target is None else SEEDS):
            e = ppa.site_error_cf(d, sigma, RHO, np.random.default_rng(seed))
            cost = ppa.imbalance_cost(d, e)
            annual.append(cost.groupby(year).sum() / mwh_year)
        a = pd.concat(annual, axis=1)
        e0 = ppa.site_error_cf(d, sigma, RHO, np.random.default_rng(0))
        mean_cost = float(a.mul(mwh_year, axis=0).sum().mean() / mwh_year.sum())
        samples = a.to_numpy().ravel()
        risk = COC * (sim.cvar(samples, Q, upper=True) - mean_cost)
        rows.append(
            {
                "case": "national fleet" if target is None else f"site nMAE {target:.0%}",
                "nmae": ppa.nmae(e0, d["pv_cf"].to_numpy()),
                "sigma": sigma,
                "cost_per_mwh": mean_cost,
                **{f"cost_{y}": float(a.loc[y].mean()) for y in a.index},
                "risk_charge": risk,
                "fee": mean_cost + risk,
            }
        )
    return pd.DataFrame(rows)


def solar_shape(df: pd.DataFrame) -> pd.DataFrame:
    d = df.dropna(subset=["mid"])
    year = d.index.tz_convert("Europe/London").year
    g = d.assign(w=d["mid"] * d["pv_cf"], neg=(d["mid"] < 0)).groupby(year)
    out = pd.DataFrame(
        {
            "baseload_mid": g["mid"].mean(),
            "solar_capture": g["w"].sum() / g["pv_cf"].sum(),
            "solar_cf": g["pv_cf"].mean(),
            "negative_periods": g["neg"].sum(),
            "solar_mwh_share_at_negative": d.assign(x=d["pv_cf"] * (d["mid"] < 0)).groupby(year)["x"].sum()
            / g["pv_cf"].sum(),
        }
    )
    out["capture_ratio"] = out["solar_capture"] / out["baseload_mid"]
    return out


# --- C. battery -----------------------------------------------------------------


def battery(df: pd.DataFrame) -> pd.DataFrame:
    f = OUT / "gb_pricing_bess_daily.csv"
    if f.exists():
        return pd.read_csv(f, index_col=[0, 1], parse_dates=[1])
    pairs = bess.pair_prices(gbdata.fetch_response_prices()).fillna(0.0)
    frames = []
    for name, h, pp, cap in (
        ("1h stacked", 1.0, pairs, 1.0),
        ("2h stacked", 2.0, pairs, 1.0),
        (CONTRACT_CONFIG, 2.0, pairs, RESP_CAP),
        ("2h wholesale only", 2.0, None, 1.0),
    ):
        r = bess.run_backtest(df, pp, power=1.0, energy=h, rte=RTE, max_cycles=MAX_CYCLES, resp_cap=cap)
        r["config"] = name
        frames.append(r)
        print(f"battery {name}: {len(r)} days")
    out = pd.concat(frames).reset_index().set_index(["config", "day"])
    out.to_csv(f)
    return out


def battery_monthly(daily: pd.DataFrame, config: str, col: str) -> pd.Series:
    d = daily.loc[config]
    total = d[f"{col}_wholesale"] + d[f"{col}_response"]
    return total.groupby([d.index.year, d.index.month]).sum()


def full_months(s: pd.Series, days: pd.Series) -> pd.Series:
    """Keep months with at least 27 simulated days, scaled to a full month."""
    keep = days >= 27
    return (s[keep] / days[keep] * pd.Series(
        [pd.Period(f"{y}-{m:02d}").days_in_month for y, m in s[keep].index], index=s[keep].index
    ))


# --- run ------------------------------------------------------------------------


def main():
    OUT.mkdir(exist_ok=True)
    df = load()
    summary: dict = {"window": [START, END], "assumptions": {
        "site_mw": SITE_MW, "bess_mw": BESS_MW, "bess_hours": BESS_H, "rte": RTE,
        "max_cycles_per_day": MAX_CYCLES, "nmae_base": NMAE_BASE, "rho": RHO,
        "ppa_years": PPA_YEARS, "bess_years": BESS_YEARS, "discount_rate": RATE,
        "cost_of_capital": COC, "tail_q": Q, "level_vol": LEVEL_VOL,
        "capture_drift": CAPTURE_DRIFT, "n_sims": N_SIMS,
    }}

    shape = solar_shape(df)
    shape.to_csv(OUT / "gb_pricing_solar_shape.csv")
    print(shape.round(3))

    bal = balancing(df)
    bal.to_csv(OUT / "gb_pricing_balancing_fee.csv", index=False)
    print(bal.round(3))
    base_row = bal[bal["case"] == f"site nMAE {NMAE_BASE:.0%}"].iloc[0]
    summary["balancing"] = bal.to_dict(orient="records")

    # C. battery backtest
    daily = battery(df)
    rows = []
    for config in daily.index.get_level_values(0).unique():
        d = daily.loc[config]
        for col, label in (("pf", "perfect foresight"), ("pe", "persistence")):
            tot = d[f"{col}_wholesale"] + d[f"{col}_response"]
            by_year = tot.groupby(d.index.year).mean() * 365
            rows.append({"config": config, "foresight": label,
                         **{f"gbp_mw_yr_{y}": v for y, v in by_year.items()},
                         "response_share": float(d[f"{col}_response"].sum() / tot.sum())})
    bt = pd.DataFrame(rows)
    bt.to_csv(OUT / "gb_pricing_bess_annual.csv", index=False)
    print(bt.round(2))
    summary["bess_backtest"] = bt.to_dict(orient="records")
    summary["bess_sep25_aug26"] = {}
    for config in daily.index.get_level_values(0).unique():
        win = daily.loc[config].loc["2025-09-01":"2026-08-31"]
        summary["bess_sep25_aug26"][config] = {
            c: float((win[f"{c}_wholesale"] + win[f"{c}_response"]).mean() * 365) for c in ("pf", "pe")
        } | {"days": int(len(win))}
    d2 = daily.loc[CONTRACT_CONFIG]

    # monthly history tables for the Monte Carlo
    hist = ppa.monthly_history(df)
    days2 = d2.groupby([d2.index.year, d2.index.month]).size()
    bess_m = full_months(battery_monthly(daily, CONTRACT_CONFIG, "pe"), days2)
    avail = {m: sorted(set(y for y, mm in hist.index if mm == m) & set(y for y, mm in bess_m.index if mm == m))
             for m in range(1, 13)}
    summary["history_years_by_month"] = {m: v for m, v in avail.items()}

    # forward baseload anchor: LCCC winter 2026/27 BMRP (LEBA forward season)
    lccc = gbdata.fetch_lccc_forecast()
    bmrp = lccc[lccc["technology"].str.startswith("Forecast BMRP") & lccc["period"].str.startswith("Determination")]
    winter_bmrp = float(bmrp.groupby("period")["price"].mean().mean())
    mshape = ppa.month_shape(hist)
    winter_factor = float(mshape.loc[[10, 11, 12, 1, 2, 3]].mean())
    base_annual = winter_bmrp / winter_factor
    summary["forward"] = {"winter_bmrp": winter_bmrp, "winter_shape_factor": winter_factor,
                          "base_annual": base_annual, "month_shape": mshape.round(4).to_dict()}

    rng = np.random.default_rng(42)
    n_years = max(PPA_YEARS, BESS_YEARS)
    draws = sim.sample_months(avail, N_SIMS, n_years, rng)
    levels = sim.level_paths(N_SIMS, n_years, LEVEL_VOL, rng)

    # B. fixed-price PPA
    imb = float(base_row["cost_per_mwh"])

    def fixed(drift=CAPTURE_DRIFT, lv=None, imb_=imb):
        lev = levels if lv is None else sim.level_paths(N_SIMS, n_years, lv, np.random.default_rng(7))
        v, e = ppa.simulate_ppa(hist, draws[:, :PPA_YEARS], lev[:, :PPA_YEARS], base_annual, drift)
        return ppa.price_fixed_ppa(v, e, base_annual, imb_, RATE, COC, Q), v, e

    ppa_base, value, energy = fixed()
    sens = {
        "base": ppa_base["fixed_price"],
        "no cannibalisation drift": fixed(drift=0.0)[0]["fixed_price"],
        "drift -2%/yr": fixed(drift=-0.02)[0]["fixed_price"],
        "level vol 10%": fixed(lv=0.10)[0]["fixed_price"],
        "level vol 25%": fixed(lv=0.25)[0]["fixed_price"],
        "site nMAE 8%": fixed(imb_=float(bal.loc[bal["case"] == "site nMAE 8%", "cost_per_mwh"].iloc[0]))[0]["fixed_price"],
    }
    summary["fixed_ppa"] = ppa_base | {"sensitivity": sens}
    print(json.dumps(summary["fixed_ppa"], indent=1))

    # C. floor and toll on the 2h battery, persistence revenues
    bess_tab = bess_m.to_dict()
    rev = sim.lookup(bess_tab, draws[:, :BESS_YEARS]).sum(axis=2) * levels[:, :BESS_YEARS]
    floors = [bess.price_floor(rev, f, RATE, COC, Q) for f in (30_000, 40_000, 50_000, 60_000)]
    toll = bess.price_toll(rev, RATE, COC, Q)
    summary["bess_contracts"] = {"floors": floors, "toll": toll,
                                 "annual_revenue_p5_p50_p95": np.quantile(rev, [0.05, 0.5, 0.95]).tolist()}
    print(pd.DataFrame(floors).round(0), toll)

    # D. portfolio: 40 MW fixed solar PPA + 20 MW / 40 MWh battery toll, same paths, PPA term
    rev10 = sim.lookup(bess_tab, draws[:, :PPA_YEARS]).sum(axis=2) * levels[:, :PPA_YEARS]
    toll10 = bess.price_toll(rev10, RATE, COC, Q)["toll"]
    dfac = sim.discount_factors(PPA_YEARS, RATE)
    pnl_solar = SITE_MW * (value - (ppa_base["fixed_price"] + imb) * energy)
    pnl_bess = BESS_MW * (rev10 - toll10)
    npv = {"solar PPA": (pnl_solar * dfac).sum(axis=1), "BESS toll": (pnl_bess * dfac).sum(axis=1)}
    npv["portfolio"] = npv["solar PPA"] + npv["BESS toll"]
    port: dict = {k: {"mean": float(v.mean()), "std": float(v.std()), "p5": float(np.quantile(v, 0.05)),
                "cvar95": sim.cvar(v, Q, upper=False)} for k, v in npv.items()}
    port["corr_npv"] = float(np.corrcoef(npv["solar PPA"], npv["BESS toll"])[0, 1])
    # hedge: sell a flat baseload forward at the anchor price; MW chosen to minimise NPV variance
    hedge_unit = (8760 * base_annual * (1 - levels[:, :PPA_YEARS]) * dfac).sum(axis=1)  # per MW sold
    hedge_mw = float(-np.cov(npv["portfolio"], hedge_unit)[0, 1] / hedge_unit.var())
    hedged = npv["portfolio"] + hedge_mw * hedge_unit
    port["baseload_hedge_mw"] = hedge_mw
    port["portfolio_hedged"] = {"mean": float(hedged.mean()), "std": float(hedged.std()),
                                "p5": float(np.quantile(hedged, 0.05)), "cvar95": sim.cvar(hedged, Q, upper=False)}
    npv["portfolio + baseload hedge"] = hedged
    # history: monthly solar capture value vs battery revenue per MW
    hv = (hist["capture"] * hist["mwh_per_mw"]).rename("solar_value")
    joint = pd.concat([hv, bess_m.rename("bess")], axis=1).dropna()
    port["corr_monthly_history"] = float(joint.corr().iloc[0, 1])
    port["corr_monthly_history_deseasonalised"] = float(
        joint.groupby(level=1).transform(lambda x: x - x.mean()).corr().iloc[0, 1]
    )
    # deterministic scenarios on expected NPV (GBP m)
    scen = {}
    for name, lv_mult, cap_mult, bess_mult in (
        ("base", 1.0, 1.0, 1.0), ("prices -30%", 0.7, 1.0, 1.0), ("prices +30%", 1.3, 1.0, 1.0),
        ("capture ratio -10%", 1.0, 0.9, 1.0), ("battery revenue -30%", 1.0, 1.0, 0.7),
    ):
        s = SITE_MW * (value * lv_mult * cap_mult - (ppa_base["fixed_price"] + imb) * energy)
        b = BESS_MW * (rev10 * lv_mult * bess_mult - toll10)
        scen[name] = {"solar": float((s * dfac).sum(axis=1).mean() / 1e6),
                      "bess": float((b * dfac).sum(axis=1).mean() / 1e6)}
        scen[name]["portfolio"] = scen[name]["solar"] + scen[name]["bess"]
    port["scenarios_gbp_m"] = scen
    port["toll_10y"] = toll10
    summary["portfolio"] = port
    print(json.dumps(port, indent=1))

    (OUT / "gb_pricing_summary.json").write_text(json.dumps(summary, indent=2, default=float))
    charts(bal, floors, npv)


def charts(bal, floors, npv):
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(bal["case"], bal["cost_per_mwh"], label="expected imbalance cost")
    ax.bar(bal["case"], bal["risk_charge"], bottom=bal["cost_per_mwh"], label="risk charge")
    ax.set_ylabel("GBP per MWh of solar output")
    ax.set_title("Solar balancing fee: national fleet vs a single 40 MW site")
    ax.set_ylim(0, 1.15 * (bal["fee"].max()))
    ax.legend(frameon=False, loc="upper left", ncols=2)
    fig.tight_layout()
    fig.savefig(OUT / "gb_pricing_balancing_fee.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    f = pd.DataFrame(floors)
    ax.plot(f["floor"] / 1e3, f["fair_premium"] / 1e3, marker="o", label="expected top-up")
    ax.plot(f["floor"] / 1e3, f["premium"] / 1e3, marker="o", label="with risk charge")
    ax.set_xlabel("Floor, GBP k/MW/yr")
    ax.set_ylabel("Annual cost to the optimiser, GBP k/MW")
    ax.set_title("2-hour battery: cost of a 5-year revenue floor\n(persistence revenues, response capped at 50% of power)", fontsize=10)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "gb_pricing_bess_floor.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 3.6))
    names = list(npv)[::-1]
    for i, k in enumerate(names):
        p5, p50, p95 = np.quantile(npv[k], [0.05, 0.5, 0.95]) / 1e6
        ax.plot([p5, p95], [i, i], lw=6, color="#9db4d0", solid_capstyle="butt")
        ax.plot([p50], [i], "o", color="#1f3b63")
    ax.set_yticks(range(len(names)), names)
    ax.axvline(0, color="grey", lw=0.8)
    ax.set_xlabel("Offtaker 10-year NPV, GBP m (bar 5th–95th percentile, dot median)")
    ax.set_title("40 MW solar PPA + 20 MW / 40 MWh battery toll", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "gb_pricing_portfolio.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
