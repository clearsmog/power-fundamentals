"""Export compact JSON for the qiankun.co.uk case-study page."""

import json
from pathlib import Path

import pandas as pd

from powerfund import metrics as m
from scripts.run_analysis import END, MARKETS, START, load
from powerfund.data import fetch_fuels

OUT = Path(__file__).resolve().parent.parent / "output" / "web"


def r(x, n=3):
    return None if pd.isna(x) else round(float(x), n)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fuels = fetch_fuels(START, END)
    data = {mk: load(mk, fuels) for mk in MARKETS}
    months = pd.date_range(START, END, freq="MS")
    labels = [d.strftime("%b %y") for d in months]

    capture = {}
    for mk, df in data.items():
        g = df.groupby(df.index.tz_localize(None).to_period("M"))
        capture[mk] = [r(m.capture_rate(x["price"], x["solar"])) for _, x in g]

    yearly = pd.read_csv(Path(__file__).resolve().parent.parent / "output" / "yearly_fundamentals.csv")
    regs = json.loads((Path(__file__).resolve().parent.parent / "output" / "price_regression.json").read_text())

    # Clean spark spread, Germany: monthly baseload vs peak (Mon-Fri 08:00-20:00 CET)
    de = data["DE"].copy()
    peak = (de.index.dayofweek < 5) & (de.index.hour >= 8) & (de.index.hour < 20)
    de["css"] = de["price"] - de["srmc"]
    mon = de.index.tz_localize(None).to_period("M")
    css_base = de.groupby(mon)["css"].mean()
    css_peak = de[peak].groupby(mon[peak])["css"].mean()

    # Hour-of-day CSS profile (CET), Germany, by year: the gas plant's duck curve
    yrs = de.index.year
    css_hour = {int(yy): [r(v, 1) for v in de[yrs == yy].groupby(de[yrs == yy].index.hour)["css"].mean()] for yy in (2023, 2025)}
    css_pos_share = {int(k): r(v) for k, v in (de["css"] > 0).groupby(yrs).mean().items()}
    css_pos_mean = {int(k): r(v, 1) for k, v in de[de["css"] > 0].groupby(yrs[de["css"] > 0])["css"].mean().items()}

    # Merit order: Germany Q3 2026 hourly
    q = de.loc["2026-07-01":"2026-09-30"]
    scatter = [[r(a / 1000, 2), r(p, 1), r(s, 1)] for a, p, s in zip(q["residual_load"], q["price"], q["srmc"])]

    out = {
        "months": labels,
        "solarCapture": capture,
        "yearly": yearly.to_dict(orient="records"),
        "regression": regs,
        "cssBase": [r(v, 1) for v in css_base],
        "cssPeak": [r(v, 1) for v in css_peak],
        "cssPeakShareMonthsPositive": r((css_peak > 0).mean(), 3),
        "cssBaseShareMonthsPositive": r((css_base > 0).mean(), 3),
        "meritQ3_2026": scatter,
        "cssByHour": css_hour,
        "cssPositiveShare": css_pos_share,
        "cssPositiveMean": css_pos_mean,
    }
    (OUT / "power-fundamentals.json").write_text(json.dumps(out, separators=(",", ":")))
    print("peak CSS mean by year:", css_peak.groupby(css_peak.index.year).mean().round(1).to_dict())
    print("base CSS mean by year:", css_base.groupby(css_base.index.year).mean().round(1).to_dict())
    print("months peak>0:", out["cssPeakShareMonthsPositive"], "base>0:", out["cssBaseShareMonthsPositive"])
    print("size KB:", round((OUT / "power-fundamentals.json").stat().st_size / 1024, 1), "points:", len(scatter))


if __name__ == "__main__":
    main()
