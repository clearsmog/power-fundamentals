"""Power-market fundamentals metrics on hourly data.

Conventions: prices in EUR/MWh, generation and load in MW, one row per hour.
Gas-plant short-run marginal cost uses the standard CCGT assumptions behind
quoted clean spark spreads: 49.13% efficiency (HHV basis) and 0.202 tCO2 per
MWh of gas burned.
"""

import pandas as pd
import statsmodels.api as sm

CCGT_EFFICIENCY = 0.4913
GAS_EMISSION_FACTOR = 0.202  # tCO2 per MWh thermal


def capture_price(price: pd.Series, gen: pd.Series) -> float:
    """Generation-weighted average price: what a pay-as-produced asset earned per MWh."""
    ok = price.notna() & gen.notna()
    return float((price[ok] * gen[ok]).sum() / gen[ok].sum())


def capture_rate(price: pd.Series, gen: pd.Series) -> float:
    """Capture price over baseload (time-weighted) price; below 1 means cannibalisation."""
    return capture_price(price, gen) / float(price.mean())


def negative_hours(price: pd.Series) -> int:
    return int((price < 0).sum())


def gas_srmc(ttf: pd.Series, eua: pd.Series) -> pd.Series:
    """Fuel plus carbon cost of a CCGT in EUR/MWh of power."""
    return (ttf + eua * GAS_EMISSION_FACTOR) / CCGT_EFFICIENCY


def clean_spark_spread(baseload: pd.Series, ttf: pd.Series, eua: pd.Series) -> pd.Series:
    return baseload - gas_srmc(ttf, eua)


def gas_near_margin_share(price: pd.Series, srmc: pd.Series, band: float = 0.15) -> float:
    """Share of hours with price within +/- band of gas SRMC: a proxy for gas setting the price.

    A proxy only: it ignores plant efficiency dispersion, scarcity pricing and
    hours where coal or imports set the price near the same level.
    """
    ok = price.notna() & srmc.notna()
    return float(((price[ok] - srmc[ok]).abs() <= band * srmc[ok]).mean())


def by_year(df: pd.DataFrame, fn, *cols) -> pd.Series:
    return df.groupby(df.index.year).apply(lambda g: fn(*(g[c] for c in cols)))


def price_regression(df: pd.DataFrame) -> dict:
    """OLS of hourly price on gas SRMC and residual load (GW)."""
    d = df[["price", "srmc", "residual_load"]].dropna()
    X = sm.add_constant(pd.DataFrame({"srmc": d["srmc"], "residual_load_gw": d["residual_load"] / 1000}))
    res = sm.OLS(d["price"], X).fit(cov_type="HAC", cov_kwds={"maxlags": 24})
    return {
        "n": int(res.nobs),
        "r2": float(res.rsquared),
        "beta_srmc": float(res.params["srmc"]),
        "beta_residual_load_gw": float(res.params["residual_load_gw"]),
        "t_srmc": float(res.tvalues["srmc"]),
        "t_residual_load": float(res.tvalues["residual_load_gw"]),
    }
