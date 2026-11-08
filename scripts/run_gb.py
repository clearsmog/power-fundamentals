"""GB balancing and wind imbalance analysis (Jan 2024 to Oct 2026)."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm

from powerfund import gb

START, END = "2024-01-01", "2026-10-31"
# Elexon AGWS wind outturn is inconsistent through 2024 (e.g. Jun 2024 averages ~3.6 GW
# against ~8.3 GW in both the day-ahead forecast and FUELHH transmission wind), so the
# forecast-error analysis starts in 2025. Price analysis uses the full window.
WIND_START = "2025-01-01"
OUT = Path(__file__).resolve().parent.parent / "output"
NIV_BINS = [-np.inf, -1000, -500, -250, 0, 250, 500, 1000, np.inf]


def load() -> pd.DataFrame:
    df = gb.fetch_gb(START, END).rename(columns={"ssp": "sp"})
    df.index = df.index.tz_convert("Europe/London")
    df.loc[
        df.index < pd.Timestamp(WIND_START, tz="Europe/London"), ["wind", "wind_fc"]
    ] = np.nan
    df["spread"] = df["sp"] - df["mid"]
    df["err"] = df["wind_fc"] - df["wind"]
    return df


def niv_regression(d: pd.DataFrame) -> dict:
    """OLS of SP - MID on NIV (GW), HAC errors: how far the cash-out price moves per GW of imbalance."""
    d = d.dropna(subset=["spread", "niv"])
    X = sm.add_constant(d["niv"] / 1000)
    res = sm.OLS(d["spread"], X).fit(cov_type="HAC", cov_kwds={"maxlags": 48})
    return {
        "gbp_per_gw": float(res.params["niv"]),
        "t": float(res.tvalues["niv"]),
        "r2": float(res.rsquared),
        "n": int(res.nobs),
    }


def wind_metrics(d: pd.DataFrame) -> dict:
    """Forecast-error and imbalance-cost metrics on periods with all four series; empty if none."""
    full = d.dropna(subset=["sp", "mid", "wind", "wind_fc"])
    if full.empty:
        return {}
    mwh = (full["wind"] * 0.5).sum()
    dec = gb.cost_decomposition(full)
    n = len(full)
    cost = gb.cost_per_mwh(full)
    mid_w = (full["mid"] * full["wind"]).sum() / full["wind"].sum()
    return {
        "wind_periods": n,
        "wind_mean_mw": full["wind"].mean(),
        "wind_capture_vs_mid": mid_w / full["mid"].mean(),
        "wind_fc_mae_pct_of_mean": full["err"].abs().mean() / full["wind"].mean(),
        "wind_fc_bias_mw": full["err"].mean(),
        "corr_err_niv": full["err"].corr(full["niv"]),
        "imbalance_cost_gbp_per_mwh": cost,
        "imbalance_cost_pct_of_value": cost / mid_w,
        "bias_term_gbp_per_mwh": dec["mean_term"] * n / mwh,
        "wrong_way_term_gbp_per_mwh": dec["cov_term"] * n / mwh,
    }


def yearly(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for y, d in df.groupby(df.index.year):
        s = gb.spread_by_niv_sign(d)
        reg = niv_regression(d)
        rows.append(
            {
                "year": y,
                "periods": len(d),
                "system_price_mean": d["sp"].mean(),
                "mid_mean": d["mid"].mean(),
                "system_price_max": d["sp"].max(),
                "spread_sd": d["spread"].std(),
                "share_abs_spread_gt_50": (d["spread"].abs() > 50).mean(),
                "share_system_short": s.loc["short", "share"],
                "spread_when_short": s.loc["short", "spread"],
                "spread_when_long": s.loc["long", "spread"],
                "spread_per_gw_niv": reg["gbp_per_gw"],
                "niv_r2": reg["r2"],
                **wind_metrics(d),
            }
        )
    return pd.DataFrame(rows).set_index("year")


def niv_buckets(df: pd.DataFrame) -> pd.DataFrame:
    d = df.dropna(subset=["sp", "mid", "niv"]).copy()
    d["niv_bucket"] = pd.cut(d["niv"], NIV_BINS)
    return d.groupby("niv_bucket", observed=True).agg(
        periods=("sp", "size"),
        system_price=("sp", "mean"),
        mid=("mid", "mean"),
        spread=("spread", "mean"),
        spread_p90=("spread", lambda x: x.quantile(0.9)),
    )


def monthly_cost(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for m, d in df.groupby(df.index.tz_localize(None).to_period("M")):
        full = d.dropna(subset=["sp", "mid", "wind", "wind_fc"])
        if full.empty:
            continue
        mwh = (full["wind"] * 0.5).sum()
        dec = gb.cost_decomposition(full)
        n = len(full)
        rows.append(
            {
                "month": str(m),
                "cost_gbp_per_mwh": gb.cost_per_mwh(full),
                "bias_term": dec["mean_term"] * n / mwh,
                "wrong_way_term": dec["cov_term"] * n / mwh,
                "mid_mean": full["mid"].mean(),
            }
        )
    return pd.DataFrame(rows).set_index("month")


def charts(buckets: pd.DataFrame, monthly: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    labels = [str(i) for i in buckets.index]
    ax.bar(labels, buckets["spread"], color="#26428b")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xlabel(
        "Net imbalance volume, MWh per half hour (negative = system long, positive = short)"
    )
    ax.set_ylabel("System price minus market index (GBP/MWh)")
    ax.set_title("GB cash-out premium by system imbalance, Jan 2024 to Oct 2026")
    plt.xticks(rotation=30, ha="right")
    fig.tight_layout()
    fig.savefig(OUT / "gb_spread_by_niv.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    x = np.arange(len(monthly))
    ax.bar(
        x,
        monthly["bias_term"],
        color="#9aa7c7",
        label="Forecast bias x average premium",
    )
    ax.bar(
        x,
        monthly["wrong_way_term"],
        bottom=np.where(
            monthly["wrong_way_term"] > 0,
            monthly["bias_term"].clip(lower=0),
            monthly["bias_term"].clip(upper=0),
        ),
        color="#26428b",
        label="Wrong-way term (covariance)",
    )
    ax.plot(
        x, monthly["cost_gbp_per_mwh"], color="black", lw=1, marker=".", label="Total"
    )
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(x[::3], monthly.index[::3], rotation=45, ha="right")
    ax.set_ylabel("GBP per MWh of wind output")
    ax.set_title(
        "Imbalance cost of selling the GB day-ahead wind forecast at the market index"
    )
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "gb_wind_imbalance_cost.png", dpi=150)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    df = load()
    y = yearly(df)
    b = niv_buckets(df)
    mth = monthly_cost(df)
    y.round(4).to_csv(OUT / "gb_yearly.csv")
    b.round(2).to_csv(OUT / "gb_spread_by_niv.csv")
    mth.round(3).to_csv(OUT / "gb_wind_imbalance_monthly.csv")
    summary = {
        "price_period": [START, END],
        "wind_period": [WIND_START, END],
        "periods": len(df),
        "single_price_check_ssp_eq_sbp": bool((df["sp"] == df["sbp"]).all()),
        "niv_regression_all": niv_regression(df),
        "wind_all": wind_metrics(df),
        "worst_month": mth["cost_gbp_per_mwh"].idxmax(),
        "worst_month_cost": float(mth["cost_gbp_per_mwh"].max()),
        "worst_month_wrong_way": mth["wrong_way_term"].idxmax(),
        "worst_month_wrong_way_cost": float(mth["wrong_way_term"].max()),
    }
    (OUT / "gb_summary.json").write_text(json.dumps(summary, indent=2, default=float))
    charts(b, mth)
    print(y.round(3).T.to_string())
    print(b.round(1).to_string())
    print(json.dumps(summary, indent=2, default=float))


if __name__ == "__main__":
    main()
