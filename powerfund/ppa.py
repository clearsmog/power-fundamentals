"""Solar PPA pricing for Great Britain: balancing (route-to-market) fee and fixed-price PPA.

Site. A notional solar farm whose output follows the national PV_Live capacity factor.

Balancing PPA. The offtaker pays the generator the market index for every MWh metered
and charges a fee for selling the generator's day-ahead forecast and carrying the
imbalance: (forecast - actual) x (system price - market index), as in `gb.py`. A single
site's forecast error = the national day-ahead solar error (Elexon) + a site-specific
error, an AR(1) shock proportional to output whose size is calibrated to an assumed
normalised MAE. The national part carries the wrong-way risk (the whole fleet
under-delivers when the system is short); the site part mostly diversifies.

Fixed-price PPA (pay-as-produced). The offtaker pays a fixed GBP/MWh for every MWh and
earns the market value of the output. Monte Carlo over a term of years: forward
baseload level x monthly shape x historical solar capture ratio x historical output,
drawn month by month from history (`sim.sample_months`), with a price-level random walk
and an optional annual drift in the capture ratio (cannibalisation).
"""

import numpy as np
import pandas as pd

from powerfund.sim import cvar, discount_factors, lookup

DAYLIGHT_CF = 0.01


def national_error_cf(df: pd.DataFrame) -> pd.Series:
    """National day-ahead solar forecast error in capacity-factor units (forecast - actual).

    Elexon's solar outturn and PV_Live differ in scale, so the Elexon error is mapped
    onto PV_Live's by the ratio of monthly energy.
    """
    month = df.index.tz_convert("Europe/London").tz_localize(None).to_period("M")
    scale = df.groupby(month)["pv_mw"].transform("sum") / df.groupby(month)["solar"].transform("sum")
    return ((df["solar_fc"] - df["solar"]) * scale / df["pv_cap_mwp"]).fillna(0.0)


def ar1(n: int, rho: float, rng) -> np.ndarray:
    """Unit-variance AR(1) series."""
    e = rng.standard_normal(n) * np.sqrt(1 - rho**2)
    z = np.empty(n)
    z[0] = rng.standard_normal()
    for t in range(1, n):
        z[t] = rho * z[t - 1] + e[t]
    return z


def site_error_cf(df: pd.DataFrame, sigma: float, rho: float, rng) -> np.ndarray:
    """Site forecast error (CF units): national error + sigma x AR(1) x output, forecast clipped to [0, 1]."""
    cf = df["pv_cf"].to_numpy()
    err = national_error_cf(df).to_numpy() + sigma * ar1(len(cf), rho, rng) * cf
    err[cf < DAYLIGHT_CF] = 0.0
    return np.clip(cf + err, 0, 1) - cf


def nmae(err_cf: np.ndarray, cf: np.ndarray) -> float:
    """Mean absolute error over daylight periods, as a share of capacity."""
    day = cf >= DAYLIGHT_CF
    return float(np.abs(err_cf[day]).mean())


def calibrate_sigma(df: pd.DataFrame, target: float, rho: float, seed: int = 0) -> float:
    """Site-noise size giving the target daylight nMAE (bisection, fixed seed)."""
    cf = df["pv_cf"].to_numpy()
    lo, hi = 0.0, 3.0
    for _ in range(40):
        mid = (lo + hi) / 2
        e = site_error_cf(df, mid, rho, np.random.default_rng(seed))
        lo, hi = (mid, hi) if nmae(e, cf) < target else (lo, mid)
    return (lo + hi) / 2


def imbalance_cost(df: pd.DataFrame, err_cf: np.ndarray) -> pd.Series:
    """Per-period imbalance cost per MW of capacity (GBP): (F - A) x 0.5 h x (SP - MID)."""
    return pd.Series(err_cf * 0.5 * (df["sp"] - df["mid"]).to_numpy(), index=df.index)


def monthly_history(df: pd.DataFrame) -> pd.DataFrame:
    """Per (year, month): baseload MID, solar capture price and ratio, capacity factor, MWh per MW."""
    d = df.dropna(subset=["mid", "pv_cf"])
    local = d.index.tz_convert("Europe/London")
    g = d.assign(year=local.year, month=local.month, w=d["mid"] * d["pv_cf"]).groupby(["year", "month"])
    out = pd.DataFrame(
        {
            "base": g["mid"].mean(),
            "capture": g["w"].sum() / g["pv_cf"].sum(),
            "cf": g["pv_cf"].mean(),
            "mwh_per_mw": g["pv_cf"].sum() * 0.5,
        }
    )
    out["kappa"] = out["capture"] / out["base"]
    return out


def month_shape(hist: pd.DataFrame) -> pd.Series:
    """Baseload of each calendar month relative to the annual mean, averaged over years."""
    m = hist["base"].groupby(level="month").mean()
    return m / m.mean()


def simulate_ppa(
    hist: pd.DataFrame, draws: np.ndarray, levels: np.ndarray, base_annual: float,
    capture_drift: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Annual market value (GBP per MW) and output (MWh per MW), each (n_sims, n_years)."""
    shape = month_shape(hist).to_numpy()
    kappa = lookup(hist["kappa"].to_dict(), draws)
    mwh = lookup(hist["mwh_per_mw"].to_dict(), draws)
    years = np.arange(draws.shape[1])[None, :, None]
    kappa = kappa * (1 + capture_drift) ** years
    price = base_annual * levels[:, :, None] * shape[None, None, :] * kappa
    return (price * mwh).sum(axis=2), mwh.sum(axis=2)


def price_fixed_ppa(
    value: np.ndarray, energy: np.ndarray, base_annual: float, imb_per_mwh: float,
    rate: float, coc: float, q: float = 0.95,
) -> dict:
    """Fixed price (GBP/MWh) an offtaker could pay, built up as a waterfall from baseload.

    fair = E[PV value] / E[PV MWh] - imbalance. The risk charge is coc x (E[NPV] -
    CVaR of NPV in the worst 1 - q of paths) at the fair price, spread over E[PV MWh].
    """
    df = discount_factors(value.shape[1], rate)
    pv_value = (value * df).sum(axis=1)
    pv_mwh = (energy * df).sum(axis=1)
    capture_price = pv_value.mean() / pv_mwh.mean()
    fair = capture_price - imb_per_mwh
    npv = pv_value - (fair + imb_per_mwh) * pv_mwh
    charge = coc * (npv.mean() - cvar(npv, q, upper=False)) / pv_mwh.mean()
    return {
        "baseload": base_annual,
        "shape_and_cannibalisation": capture_price - base_annual,
        "imbalance": -imb_per_mwh,
        "risk_charge": -charge,
        "fixed_price": fair - charge,
        "capture_ratio": capture_price / base_annual,
    }
