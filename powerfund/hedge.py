"""Monthly baseload hedge of a merchant solar asset, backtested on hourly data.

The asset is a notional solar plant whose hourly output follows the national
solar capacity factor (national solar output ÷ installed AC capacity). Each
month the owner sells a baseload forward for h × expected monthly output.

Revenue vs budget is the risk measure. At the start of month m the owner knows:
  expected output   E[G] = same month last year's capacity factor × capacity × hours
  forward price     F    = last month's realised baseload average (proxy: no free
                           history of EEX month futures)
  expected capture  E[c] = same month last year's solar capture rate
  budget            P    = E[G] × F × E[c]
Realised revenue is Σ price × output plus the hedge payoff h × E[G] × (F − B),
where B is the month's realised baseload average.
"""

import numpy as np
import pandas as pd
import requests

from powerfund.data import CACHE


def fetch_capacity(country: str = "de") -> pd.Series:
    """Monthly installed solar AC capacity in MW (Energy-Charts), indexed by month start."""
    f = CACHE / f"capacity_solar_{country}.parquet"
    if not f.exists():
        r = requests.get(
            "https://api.energy-charts.info/installed_power",
            params={
                "country": country,
                "time_step": "monthly",
                "installation_decommission": "false",
            },
            timeout=60,
        )
        r.raise_for_status()
        d = r.json()
        ac = next(t for t in d["production_types"] if t["name"] == "Solar AC")
        idx = pd.to_datetime(d["time"], format="%m.%Y")
        pd.DataFrame(
            {"capacity_mw": [v * 1000 for v in ac["data"]]}, index=idx
        ).to_parquet(f)
    return pd.read_parquet(f)["capacity_mw"]


def monthly_asset(
    df: pd.DataFrame, capacity_mw: pd.Series, asset_mw: float = 100.0
) -> pd.DataFrame:
    """Monthly output, baseload price, revenue and capture rate for a notional solar asset."""
    month = df.index.tz_localize(None).to_period("M").to_timestamp()
    cap = capacity_mw.reindex(month).to_numpy()
    cf = df["solar"].to_numpy() / cap
    out = pd.DataFrame(
        {"price": df["price"].to_numpy(), "gen": cf * asset_mw}, index=month
    )
    g = out.groupby(level=0)
    m = pd.DataFrame(
        {
            "gen_mwh": g["gen"].sum(),
            "hours": g["gen"].size(),
            "baseload": g["price"].mean(),
            "revenue": g.apply(lambda x: float((x["price"] * x["gen"]).sum())),
        }
    )
    m["capacity_factor"] = m["gen_mwh"] / (asset_mw * m["hours"])
    m["capture"] = m["revenue"] / (m["gen_mwh"] * m["baseload"])
    return m


def add_expectations(m: pd.DataFrame, asset_mw: float = 100.0) -> pd.DataFrame:
    """Information available at the start of each month; rows without a prior year are dropped."""
    e = m.copy()
    e["exp_gen_mwh"] = m["capacity_factor"].shift(12) * asset_mw * m["hours"]
    e["forward"] = m["baseload"].shift(1)
    e["exp_capture"] = m["capture"].shift(12)
    e["budget"] = e["exp_gen_mwh"] * e["forward"] * e["exp_capture"]
    return e.dropna()


def hedged_revenue(e: pd.DataFrame, h) -> pd.Series:
    """Realised revenue including a baseload hedge of h × expected output (h scalar or per-month Series)."""
    return e["revenue"] + h * e["exp_gen_mwh"] * (e["forward"] - e["baseload"])


def budget_variance(e: pd.DataFrame, h) -> pd.Series:
    """Revenue vs budget, as a fraction of budget (0 = on budget, -0.3 = 30% short)."""
    return hedged_revenue(e, h) / e["budget"] - 1


def hedge_ratio_sweep(
    e: pd.DataFrame, ratios=np.round(np.arange(0, 1.21, 0.1), 2)
) -> pd.DataFrame:
    rows = []
    for h in ratios:
        v = budget_variance(e, h)
        rows.append(
            {
                "hedge_ratio": float(h),
                "std": float(v.std()),
                "worst": float(v.min()),
                "mean": float(v.mean()),
            }
        )
    return pd.DataFrame(rows)


def risk_decomposition(e: pd.DataFrame) -> dict:
    """Unhedged revenue vs budget split into volume, price and shape factors.

    revenue / budget = (G / E[G]) × (B / F) × (c / E[c]) exactly, so the log of
    the ratio is the sum of three log terms. Each factor's share of the variance
    is cov(term, total) / var(total); the shares sum to 1.
    """
    terms = pd.DataFrame(
        {
            "volume": np.log(e["gen_mwh"] / e["exp_gen_mwh"]),
            "price": np.log(e["baseload"] / e["forward"]),
            "shape": np.log(e["capture"] / e["exp_capture"]),
        }
    )
    total = terms.sum(axis=1)
    return {k: float(terms[k].cov(total) / total.var()) for k in terms}
