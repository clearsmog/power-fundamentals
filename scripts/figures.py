"""README figures, light and dark SVG.

    uv run python scripts/figures.py

Reads the cached data and the result files in output/ (run run_analysis.py, run_gb.py,
run_pricing.py and run_risk.py first) and writes docs/img/<name>-light.svg and -dark.svg.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("svg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter, PercentFormatter

from powerfund import hedge as H
from powerfund import metrics as m
from powerfund.data import fetch_fuels
from scripts.run_analysis import END, MARKETS, START, load

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
IMG = ROOT / "docs" / "img"

THEMES = {
    "light": dict(bg="#ffffff", text="#1f2328", muted="#59636e", grid="#e5e7eb",
                  s=["#2563eb", "#ea580c", "#059669", "#e11d48", "#7c3aed"], ref="#94a3b8",
                  pos="#059669", neg="#e11d48"),
    "dark": dict(bg="#0d1117", text="#e6edf3", muted="#9198a1", grid="#30363d",
                 s=["#60a5fa", "#fb923c", "#34d399", "#fb7185", "#a78bfa"], ref="#6e7681",
                 pos="#34d399", neg="#fb7185"),
}
plt.rcParams.update({"svg.fonttype": "path", "font.family": "sans-serif",
                     "font.sans-serif": ["Inter", "Helvetica Neue", "Arial", "DejaVu Sans"]})


def eur_m(v, _=None):
    x = v / 1e6
    return f"{x:,.0f}m" if abs(x - round(x)) < 0.05 else f"{x:,.1f}m"


def gbp_k(v, _=None):
    return f"£{v / 1e3:,.0f}k"


def style(ax, t, grid="y"):
    ax.set_facecolor(t["bg"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["grid"])
    ax.tick_params(colors=t["muted"], length=0, labelsize=9)
    ax.grid(axis=grid, color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.title.set_color(t["text"])
    ax.xaxis.label.set_color(t["muted"])
    ax.yaxis.label.set_color(t["muted"])


def title(ax, t, text, sub=None):
    ax.set_title(text, loc="left", fontsize=11, fontweight="bold", color=t["text"], pad=18 if sub else 8)
    if sub:
        ax.text(0, 1.02, sub, transform=ax.transAxes, fontsize=8.5, color=t["muted"], va="bottom")


def legend(ax, t, **kw):
    lg = ax.legend(frameon=False, fontsize=8.5, labelcolor=t["text"], **kw)
    return lg


def save(fig, name, theme):
    fig.savefig(IMG / f"{name}-{theme}.svg", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


# ------------------------------------------------------------------ figures

def fig_capture(data, yearly, t):
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 3.6), facecolor=t["bg"], gridspec_kw={"width_ratios": [1.6, 1]})
    ends = {}
    for i, (mk, df) in enumerate(data.items()):
        g = df.groupby(df.index.tz_localize(None).to_period("M"))
        s = g.apply(lambda x: m.capture_rate(x["price"], x["solar"]))
        s.index = s.index.to_timestamp()
        s = s.rolling(3, center=True, min_periods=2).mean()
        a.plot(s.index, s.to_numpy(), color=t["s"][i], lw=1.8)
        ends[mk] = (i, s.index[-1], s.iloc[-1])
    placed = []
    for mk, (i, x, y) in sorted(ends.items(), key=lambda kv: kv[1][2]):
        y = max([y] + [p + 0.045 for p in placed])
        placed.append(y)
        a.text(x, y, f" {mk}", color=t["s"][i], fontsize=9, va="center", fontweight="bold")
    a.axhline(1, color=t["ref"], lw=0.8, ls="--")
    a.set_ylim(0.2, 1.1)
    style(a, t)
    title(a, t, "Solar capture rate", "solar-weighted price / baseload, 3-month average")
    years = sorted(yearly["year"].unique())
    w = 0.26
    for i, mk in enumerate(MARKETS):
        y = yearly[yearly["market"] == mk].set_index("year")["negative_hours"].reindex(years)
        b.bar(np.arange(len(years)) + (i - 1) * w, y, w, color=t["s"][i], label=mk)
    b.set_xticks(range(len(years)), [str(y) if y < 2026 else "2026\nJan–Sep" for y in years])
    style(b, t)
    title(b, t, "Negative-price hours", "day-ahead, per year")
    legend(b, t, loc="upper left")
    fig.tight_layout()
    return fig


def fig_duck(data, t):
    de = data["DE"]
    css = de["price"] - de["srmc"]
    fig, ax = plt.subplots(figsize=(11, 3.4), facecolor=t["bg"])
    for i, yy in enumerate((2023, 2024, 2025, 2026)):
        c = css[de.index.year == yy]
        prof = c.groupby(c.index.hour).mean()
        label = f"{yy}" if yy < 2026 else "2026 (Jan–Sep)"
        ax.plot(prof.index, prof.to_numpy(), color=t["s"][i], lw=2 if yy == 2025 else 1.4, label=label,
                marker="o", ms=3)
    ax.axhline(0, color=t["ref"], lw=0.9)
    ax.set_xticks(range(0, 24, 2), [f"{h:02d}:00" for h in range(0, 24, 2)])
    ax.set_ylabel("EUR/MWh")
    style(ax, t)
    title(ax, t, "Germany: a gas plant's clean spark spread by hour of day",
          "mean of day-ahead price − gas SRMC (TTF + EUA, 49.13% efficiency), CET")
    legend(ax, t, loc="lower left", ncol=4)
    fig.tight_layout()
    return fig


def fig_hedge(data, t):
    mon = H.monthly_asset(data["DE"], H.fetch_capacity("de"))
    e = H.add_expectations(mon)
    sw = H.hedge_ratio_sweep(e)
    fig, ax = plt.subplots(figsize=(11, 3.4), facecolor=t["bg"])
    x = sw["hedge_ratio"] * 100
    ax.plot(x, sw["std"], color=t["s"][0], lw=2, marker="o", ms=4, label="Std of revenue vs budget")
    ax.plot(x, -sw["worst"], color=t["s"][3], lw=2, marker="o", ms=4, label="Worst month, shortfall")
    best = sw.loc[sw["std"].idxmin()]
    ax.annotate(f"minimum {best['std']:.1%} at {best['hedge_ratio']:.0%}", (best["hedge_ratio"] * 100, best["std"]),
                xytext=(0, -22), textcoords="offset points", ha="center", fontsize=8.5, color=t["text"])
    ax.annotate(f"merchant {sw['std'].iloc[0]:.1%}", (0, sw["std"].iloc[0]), xytext=(8, 8),
                textcoords="offset points", fontsize=8.5, color=t["text"])
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_xlabel("baseload sold forward, share of expected output")
    ax.set_ylim(0, None)
    style(ax, t)
    title(ax, t, "Hedging a notional 100 MW German solar asset with baseload",
          f"monthly, {len(e)} months Jan 2024 – Sep 2026, forward = last month's baseload (proxy)")
    legend(ax, t, loc="lower left")
    fig.tight_layout()
    return fig


def fig_cashout(t):
    niv = pd.read_csv(OUT / "gb_spread_by_niv.csv")
    yearly = pd.read_csv(OUT / "gb_yearly.csv")
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 3.6), facecolor=t["bg"], gridspec_kw={"width_ratios": [1.5, 1]})
    labels = [s.replace("(", "").replace("]", "").replace(".0", "").replace("-inf, ", "< ").replace(", inf", "+")
              .replace(", ", " to ") for s in niv["niv_bucket"]]
    colors = [t["neg"] if v < 0 else t["pos"] for v in niv["spread"]]
    a.bar(range(len(niv)), niv["spread"], color=colors, width=0.7)
    a.set_xticks(range(len(niv)), labels, rotation=30, ha="right", fontsize=8)
    a.axhline(0, color=t["ref"], lw=0.9)
    a.set_ylabel("£/MWh")
    a.set_xlabel("net imbalance volume, MWh (− long, + short)")
    style(a, t)
    title(a, t, "GB cash-out premium: system price − market index", "mean by size of imbalance, Jan 2024 – Oct 2026")
    wind = pd.read_csv(OUT / "gb_wind_imbalance_monthly.csv")
    x = pd.to_datetime(wind["month"])
    b.bar(x, wind["bias_term"], width=20, color=t["s"][0], label="forecast bias × premium")
    b.bar(x, wind["wrong_way_term"], width=20, bottom=np.where(
        np.sign(wind["wrong_way_term"]) == np.sign(wind["bias_term"]), wind["bias_term"], 0),
        color=t["s"][1], label="wrong-way covariance")
    b.axhline(0, color=t["ref"], lw=0.9)
    b.set_ylabel("£ per MWh of wind")
    b.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %y"))
    style(b, t)
    title(b, t, "National wind imbalance cost", "day-ahead forecast settled at system price, by month")
    legend(b, t, loc="lower left")
    fig.tight_layout()
    return fig


def fig_pricing(pricing, t):
    fp = pricing["fixed_ppa"]
    steps = [("Forward\nbaseload", fp["baseload"]), ("Profile,\ncannibal-\nisation", fp["hourly_profile_cost"]),
             ("Balancing", fp["balancing_cost"]), ("Price risk", fp["price_risk"]),
             ("Other risk", fp["profile_volume_covariance"] + fp["profile_volume_risk"])]
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 3.8), facecolor=t["bg"], gridspec_kw={"width_ratios": [1.15, 1]})
    level = 0.0
    for i, (lab, v) in enumerate(steps):
        bottom = level if v >= 0 or i == 0 else level + v
        a.bar(i, abs(v), bottom=0 if i == 0 else bottom, color=t["s"][0] if i == 0 else t["neg"], width=0.65)
        a.text(i, (0 if i == 0 else bottom) + abs(v) + 2, f"{v:+.1f}" if i else f"{v:.1f}", ha="center",
               fontsize=8.5, color=t["text"])
        level = v if i == 0 else level + v
    a.bar(len(steps), fp["fixed_price"], color=t["s"][2], width=0.65)
    a.text(len(steps), fp["fixed_price"] + 2, f"{fp['fixed_price']:.1f}", ha="center", fontsize=8.5,
           color=t["text"], fontweight="bold")
    a.set_xticks(range(len(steps) + 1), [s[0] for s in steps] + ["Fixed\nprice"], fontsize=8)
    a.set_ylabel("£/MWh")
    style(a, t)
    title(a, t, "10-year fixed-price solar PPA (GB, from 2027)", "Pexapark-style build-up, model price")

    bess = pd.read_csv(OUT / "gb_pricing_bess_annual.csv")
    keep = [("2h wholesale only", "ex-ante", "Wholesale only"),
            ("2h stacked, cap 50%", "persistence", "Stacked, plan on yesterday"),
            ("2h stacked, cap 50%", "ex-ante, deg 5", "Stacked, plan on forecast"),
            ("2h stacked, cap 50%", "perfect, deg 5", "Stacked, perfect foresight")]
    vals, labs = [], []
    for cfg, case, lab in keep:
        row = bess[(bess["config"] == cfg) & bess["case"].str.startswith(case)].iloc[0]
        vals.append(row["sep25_aug26"])
        labs.append(lab)
    modo = pricing["modo_comparison_sep25_aug26"]
    y = np.arange(len(vals))
    b.barh(y, vals, color=[t["ref"], t["s"][1], t["s"][0], t["ref"]], height=0.6)
    for yi, v in zip(y, vals):
        b.text(v + 800, yi, gbp_k(v), va="center", fontsize=8.5, color=t["text"])
    b.axvline(modo["modo_index_gbp_mw_yr"], color=t["s"][3], lw=1.2, ls="--")
    b.text(modo["modo_index_gbp_mw_yr"], -0.55, " Modo GB 2h index\n (incl. BM, CM)", fontsize=8,
           color=t["s"][3], va="bottom")
    b.set_yticks(y, labs, fontsize=8.5)
    b.xaxis.set_major_formatter(FuncFormatter(gbp_k))
    b.set_xlim(0, 85_000)
    style(b, t, grid="x")
    b.set_ylim(-0.7, len(vals) - 0.5)
    title(b, t, "2-hour battery revenue, £/MW/yr", "day-ahead + response, Sep 2025 – Aug 2026")
    fig.tight_layout()
    return fig


def fig_var(bt, summary, t):
    fig, ax = plt.subplots(figsize=(11, 3.8), facecolor=t["bg"])
    ax.scatter(bt.index, bt["pnl"], s=5, color=t["ref"], alpha=0.7, lw=0, label="daily P&L")
    ax.plot(bt.index, -bt["var"], color=t["s"][1], lw=1.3, label="−VaR 99%, historical simulation")
    ax.plot(bt.index, -bt["var_fhs"], color=t["s"][0], lw=1.3, label="−VaR 99%, filtered HS (EWMA)")
    ex = bt[bt["exception"]]
    ax.scatter(ex.index, ex["pnl"], s=28, facecolor="none", edgecolor=t["s"][1], lw=1.3,
               label=f"HS exceptions ({summary['hs']['exceptions']})")
    exf = bt[bt["exception_fhs"]]
    ax.scatter(exf.index, exf["pnl"], s=10, color=t["s"][0], lw=0, label=f"FHS exceptions ({summary['fhs']['exceptions']})")
    ax.yaxis.set_major_formatter(FuncFormatter(eur_m))
    ax.set_ylabel("EUR")
    style(ax, t)
    worst = bt["pnl"].idxmin()
    ax.annotate("Dunkelflaute, 12 Dec 2024", (worst, bt.loc[worst, "pnl"]), xytext=(-14, -4),
                textcoords="offset points", ha="right", va="center", fontsize=8, color=t["text"])
    title(ax, t, "One-day VaR back-test on the notional power and gas book",
          f"{summary['hs']['days']} days, 250-day window; expected exceptions {summary['hs']['expected']}")
    legend(ax, t, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=5)
    fig.tight_layout()
    return fig


def fig_stress(stress, margin, t):
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 3.9), facecolor=t["bg"], gridspec_kw={"width_ratios": [1.6, 1]})
    s = stress.sort_values("pnl")
    y = np.arange(len(s))
    colors = [t["neg"] if v < 0 else t["pos"] for v in s["pnl"]]
    a.barh(y, s["pnl"], color=colors, height=0.6)
    names = [n.replace("Worst historical", "Worst actual") for n in s["scenario"]]
    a.set_yticks(y, names, fontsize=8)
    a.axvline(0, color=t["ref"], lw=0.9)
    a.xaxis.set_major_formatter(FuncFormatter(eur_m))
    style(a, t, grid="x")
    title(a, t, "Stress scenarios on today's book, EUR", "hypothetical shocks and the worst actual 1- and 5-day moves")

    mg = margin.set_index("scenario")
    labels = ["Today", "Price shock", "Vol spike", "Margin +25%"]
    x = np.arange(len(mg))
    b.bar(x, mg["initial_margin"], color=t["s"][0], width=0.6, label="initial margin")
    b.bar(x, mg["variation_margin"], bottom=mg["initial_margin"], color=t["s"][1], width=0.6,
          label="variation margin paid")
    for xi, v in zip(x, mg["cash_needed"]):
        b.text(xi, v * 1.02, eur_m(v), ha="center", fontsize=8.5, color=t["text"])
    b.set_xticks(x, labels, fontsize=8.5)
    b.yaxis.set_major_formatter(FuncFormatter(eur_m))
    b.set_ylim(0, mg["cash_needed"].max() * 1.35)
    style(b, t)
    title(b, t, "Cash at the exchange, EUR", "all legs as cleared futures")
    legend(b, t, loc="upper left", ncol=2)
    fig.tight_layout()
    return fig


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    fuels = fetch_fuels(START, END)
    data = {mk: load(mk, fuels) for mk in MARKETS}
    yearly = pd.read_csv(OUT / "yearly_fundamentals.csv")
    pricing = json.loads((OUT / "gb_pricing_summary.json").read_text())
    bt = pd.read_csv(OUT / "risk_backtest.csv", index_col=0, parse_dates=True)
    summary = json.loads((OUT / "risk_summary.json").read_text())["backtest"]
    stress = pd.read_csv(OUT / "risk_stress.csv")
    margin = pd.read_csv(OUT / "risk_margin.csv")
    for theme, t in THEMES.items():
        save(fig_capture(data, yearly, t), "capture", theme)
        save(fig_duck(data, t), "spark-spread", theme)
        save(fig_hedge(data, t), "hedge", theme)
        save(fig_cashout(t), "gb-balancing", theme)
        save(fig_pricing(pricing, t), "gb-pricing", theme)
        save(fig_var(bt, summary, t), "var-backtest", theme)
        save(fig_stress(stress, margin, t), "stress-margin", theme)
    print(sorted(p.name for p in IMG.glob("*.svg")))


if __name__ == "__main__":
    main()
