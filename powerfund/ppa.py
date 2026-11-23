"""Solar PPA pricing for Great Britain: balancing (route-to-market) fee and fixed-price PPA.

Site. A notional solar farm whose output follows the national PV_Live capacity factor.

Balancing PPA. The offtaker pays the generator the day-ahead price for every MWh metered
and charges a fee for selling the generator's day-ahead forecast and carrying the
imbalance: (forecast - actual) x (system price - day-ahead price). A single
site's forecast error = the national day-ahead solar error (Elexon) + a site-specific
error, an AR(1) shock proportional to output whose size is calibrated to an assumed
normalised MAE. The site part is uncorrelated with the system, so it adds variance
more than expected cost.

Fixed-price PPA (pay-as-produced). The offtaker pays a fixed GBP/MWh for every MWh and
earns the market value of the output. Monte Carlo over a term of years: forward
baseload level x monthly shape x historical solar capture ratio x historical output,
drawn month by month from history (`sim.sample_months`), with a price-level random walk;
each historical capture ratio is moved to the contract year's installed solar capacity
using the measured capture-vs-capacity slope (cannibalisation).
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


def imbalance_cost(df: pd.DataFrame, err_cf: np.ndarray, price: str = "da") -> pd.Series:
    """Per-period imbalance cost per MW of capacity (GBP): (F - A) x 0.5 h x (SP - price).

    The forecast F is sold in the day-ahead auction and the miss settled at the system price.
    """
    return pd.Series(err_cf * 0.5 * (df["sp"] - df[price]).to_numpy(), index=df.index)


def monthly_history(df: pd.DataFrame, price: str = "da") -> pd.DataFrame:
    """Per (year, month): baseload, solar capture price and ratio, capacity factor, MWh per MW, installed GW."""
    d = df.dropna(subset=[price, "pv_cf"])
    local = d.index.tz_convert("Europe/London")
    g = d.assign(year=local.year, month=local.month, w=d[price] * d["pv_cf"]).groupby(["year", "month"])
    out = pd.DataFrame(
        {
            "base": g[price].mean(),
            "capture": g["w"].sum() / g["pv_cf"].sum(),
            "cf": g["pv_cf"].mean(),
            "mwh_per_mw": g["pv_cf"].sum() * 0.5,
            "cap_gw": g["pv_cap_mwp"].mean() / 1000,
            "periods": g[price].size(),
        }
    )
    out["kappa"] = out["capture"] / out["base"]
    # keep whole months only (at least 27 days of half-hours)
    return out[out["periods"] >= 27 * 48].drop(columns="periods")


def month_shape(hist: pd.DataFrame) -> pd.Series:
    """Baseload of each calendar month relative to its year's mean, averaged over complete years."""
    full = hist.groupby(level="year").filter(lambda g: len(g) == 12)
    rel = full["base"] / full["base"].groupby(level="year").transform("mean")
    m = rel.groupby(level="month").mean()
    return m / m.mean()


def capture_regression(hist: pd.DataFrame) -> dict:
    """OLS of the monthly solar capture ratio on installed solar (GW) with calendar-month effects."""
    import statsmodels.formula.api as smf

    d = hist.reset_index()
    res = smf.ols("kappa ~ cap_gw + C(month)", data=d).fit(cov_type="HAC", cov_kwds={"maxlags": 3})
    return {"per_gw": float(res.params["cap_gw"]), "se": float(res.bse["cap_gw"]),
            "t": float(res.tvalues["cap_gw"]), "r2": float(res.rsquared), "n": int(res.nobs)}


def simulate_ppa(
    hist: pd.DataFrame, draws: np.ndarray, levels: np.ndarray, base_annual: float,
    per_gw: float = 0.0, future_gw: np.ndarray | None = None,
) -> dict:
    """Simulated solar PPA paths.

    Each draw takes a historical month's capture ratio and output; the capture ratio is
    moved to the contract year's installed solar by `per_gw` x (future GW - that month's GW).
    Returns annual value (GBP/MW) and MWh/MW, the same with a flat price level, and the
    monthly arrays (n_sims, n_years, 12) for kappa and MWh.
    """
    shape = month_shape(hist).to_numpy()
    kappa = lookup(hist["kappa"].to_dict(), draws)
    mwh = lookup(hist["mwh_per_mw"].to_dict(), draws)
    if future_gw is not None and per_gw:
        gw = lookup(hist["cap_gw"].to_dict(), draws)
        kappa = np.clip(kappa + per_gw * (future_gw[None, :, None] - gw), 0.2, 1.5)
    flat = base_annual * shape[None, None, :] * kappa * mwh
    return {
        "value": (flat * levels[:, :, None]).sum(axis=2),
        "value_flat": flat.sum(axis=2),
        "energy": mwh.sum(axis=2),
        "kappa": kappa,
        "mwh": mwh,
        "shape": shape,
    }


def price_fixed_ppa(
    sim_: dict, base_annual: float, imb_per_mwh: float, rate: float, coc: float, q: float = 0.95,
) -> dict:
    """Fixed price (GBP/MWh) as a Pexapark-style waterfall from the baseload forward.

    Costs are expected values; risks come from the simulation:
      hourly profile cost  = capture price from expected monthly capture ratios and
                             output (no covariance) - baseload;
      profile/volume covariance = simulated E[PV value] / E[PV MWh] - that capture price
                             (months with more output capture less);
      balancing cost       = expected imbalance cost per MWh;
      profile & volume risk = risk charge with a flat price level;
      price risk           = total risk charge - profile & volume risk.
    A risk charge is coc x (E[NPV] - CVaR of NPV over the worst 1 - q of paths) / E[PV MWh].
    """
    n_years = sim_["energy"].shape[1]
    df = discount_factors(n_years, rate)
    pv_mwh = (sim_["energy"] * df).sum(axis=1)
    e_mwh = sim_["mwh"].mean(axis=0)  # (years, 12)
    e_kappa = sim_["kappa"].mean(axis=0)
    det_value = (base_annual * sim_["shape"][None, :] * e_kappa * e_mwh).sum(axis=1)
    det_capture = (det_value * df).sum() / (e_mwh.sum(axis=1) * df).sum()

    def charge(value):
        pv_value = (value * df).sum(axis=1)
        price = pv_value.mean() / pv_mwh.mean()
        npv = pv_value - price * pv_mwh
        return price, coc * (npv.mean() - cvar(npv, q, upper=False)) / pv_mwh.mean()

    capture_price, total_risk = charge(sim_["value"])
    _, pv_risk = charge(sim_["value_flat"])
    fixed = capture_price - imb_per_mwh - total_risk
    return {
        "baseload": base_annual,
        "hourly_profile_cost": det_capture - base_annual,
        "profile_volume_covariance": capture_price - det_capture,
        "balancing_cost": -imb_per_mwh,
        "profile_volume_risk": -pv_risk,
        "price_risk": -(total_risk - pv_risk),
        "fixed_price": fixed,
        "capture_ratio": capture_price / base_annual,
    }
