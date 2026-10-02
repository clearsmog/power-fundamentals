"""Build the fundamentals tables and charts for DE, ES and FR (2023 to Sep 2026)."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from powerfund import metrics as m
from powerfund.data import fetch_fuels, fetch_market

START, END = "2023-01-01", "2026-09-30"
OUT = Path(__file__).resolve().parent.parent / "output"
MARKETS = ["DE", "ES", "FR"]


def load(market: str, fuels: pd.DataFrame) -> pd.DataFrame:
    df = fetch_market(market, START, END)
    # DE, ES and FR all trade on CET: group by local delivery time, not UTC
    df.index = df.index.tz_convert("Europe/Berlin")
    df = df.loc[START:END]
    day = df.index.tz_localize(None).normalize()
    df["ttf"] = fuels["ttf"].reindex(day).to_numpy()
    df["eua"] = fuels["eua"].reindex(day).to_numpy()
    df["srmc"] = m.gas_srmc(df["ttf"], df["eua"])
    df["wind"] = df["wind_onshore"].fillna(0) + df["wind_offshore"].fillna(0)
    return df


def yearly_table(market: str, df: pd.DataFrame) -> pd.DataFrame:
    t = pd.DataFrame({
        "baseload": df.groupby(df.index.year)["price"].mean(),
        "solar_capture_rate": m.by_year(df, m.capture_rate, "price", "solar"),
        "wind_capture_rate": m.by_year(df, m.capture_rate, "price", "wind"),
        "negative_hours": m.by_year(df, m.negative_hours, "price"),
        "gas_srmc": df.groupby(df.index.year)["srmc"].mean(),
        "gas_near_margin_share": m.by_year(df, m.gas_near_margin_share, "price", "srmc"),
        "hours": df.groupby(df.index.year)["price"].count(),
    })
    t.insert(0, "market", market)
    return t


def main() -> None:
    OUT.mkdir(exist_ok=True)
    fuels = fetch_fuels(START, END)
    data = {mk: load(mk, fuels) for mk in MARKETS}

    yearly = pd.concat([yearly_table(mk, df) for mk, df in data.items()])
    yearly.index.name = "year"
    yearly.round(3).to_csv(OUT / "yearly_fundamentals.csv")

    regs = {mk: {int(y): m.price_regression(g) for y, g in df.groupby(df.index.year)} for mk, df in data.items()}
    (OUT / "price_regression.json").write_text(json.dumps(regs, indent=2))

    # Monthly solar capture rate and negative hours
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    for mk, df in data.items():
        mon = df.groupby(pd.Grouper(freq="MS"))
        mon.apply(lambda g: m.capture_rate(g["price"], g["solar"])).plot(ax=axes[0], label=mk)
        mon["price"].apply(m.negative_hours).plot(ax=axes[1], label=mk)
    axes[0].axhline(1, color="grey", lw=0.8, ls="--")
    axes[0].set_title("Monthly solar capture rate (capture price / baseload)")
    axes[1].set_title("Negative-price hours per month")
    for ax in axes:
        ax.legend()
        ax.set_xlabel("")
    fig.tight_layout()
    fig.savefig(OUT / "solar_capture_negative_hours.png", dpi=150)

    # Daily clean spark spread, Germany
    de = data["DE"]
    daily = de[["price", "ttf", "eua"]].resample("D").mean()
    daily["css"] = m.clean_spark_spread(daily["price"], daily["ttf"], daily["eua"])
    daily.to_csv(OUT / "de_daily_clean_spark_spread.csv")
    fig, ax = plt.subplots(figsize=(12, 4))
    daily["css"].rolling(30).mean().plot(ax=ax, label="DE baseload clean spark spread, 30-day mean")
    ax.axhline(0, color="grey", lw=0.8)
    ax.set_ylabel("EUR/MWh")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "de_clean_spark_spread.png", dpi=150)

    # Price vs residual load, Germany, last full quarter: the merit-order curve
    q = de.loc["2026-07-01":"2026-09-30"]
    fig, ax = plt.subplots(figsize=(7, 5))
    sc = ax.scatter(q["residual_load"] / 1000, q["price"], s=3, c=q["srmc"], cmap="viridis")
    fig.colorbar(sc, label="Gas SRMC (EUR/MWh)")
    ax.set_xlabel("Residual load (GW)")
    ax.set_ylabel("Day-ahead price (EUR/MWh)")
    ax.set_title("Germany Q3 2026: price vs residual load")
    fig.tight_layout()
    fig.savefig(OUT / "de_merit_order_q3_2026.png", dpi=150)

    print(yearly.round(2).to_string())
    print(json.dumps({mk: {y: {k: round(v, 3) for k, v in r.items()} for y, r in d.items()} for mk, d in regs.items()}, indent=1))


if __name__ == "__main__":
    main()
