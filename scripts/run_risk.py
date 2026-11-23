"""Market risk on a notional power and gas book (Jan 2023 to Sep 2026).

    uv run python scripts/run_risk.py

Writes output/risk_*.csv and output/risk_summary.json. Uses the cached Energy-Charts and
Yahoo Finance data from run_analysis.py.
"""

import json
from pathlib import Path

import pandas as pd

from powerfund import risk
from powerfund.data import CACHE, fetch_fuels, fetch_market

START, END = "2023-01-01", "2026-09-30"
OUT = Path(__file__).resolve().parent.parent / "output"
WINDOW = 250
# Chosen limits, about 3x and 5x the average one-day 99% VaR of the book
STOP_LOSS, PEAK_TO_TROUGH = 10e6, 16e6


def load_prices() -> pd.DataFrame:
    power = {mk: fetch_market(mk, START, END) for mk in ("DE", "FR")}
    fetch_fuels(START, END)  # makes sure the raw cache exists
    raw = pd.read_parquet(CACHE / f"fuels_{START}_{END}.parquet")
    raw.index = pd.to_datetime(raw.index)
    return risk.daily_prices(power, raw)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    raw = load_prices()
    checks = risk.input_checks(raw)
    checks.to_csv(OUT / "risk_input_checks.csv", index=False)
    prices = risk.clean(raw)
    pos = risk.positions()
    moves = risk.changes(prices)

    today = {}
    hist = moves.iloc[-WINDOW:]
    var, es = risk.var_es(risk.scenario_pnl(hist, pos))
    today["hs"] = {"var_99": round(var), "es_975": round(es)}
    vol = risk.ewma_vol(moves)
    fhs = risk.filtered_moves(hist, vol.shift().bfill(), vol.iloc[-1])
    var, es = risk.var_es(risk.scenario_pnl(fhs, pos))
    today["fhs"] = {"var_99": round(var), "es_975": round(es)}
    # Contribution of each factor to the HS tail: P&L by factor on the VaR scenario day
    by_factor = hist[pos.index] * pos
    tail_day = by_factor.sum(axis=1).sort_values().index[int(0.01 * WINDOW)]
    today["var_scenario_day"] = f"{tail_day:%Y-%m-%d}"
    today["var_scenario_by_factor"] = by_factor.loc[tail_day].round().to_dict()
    standalone = {f: round(risk.var_es(hist[f] * pos[f])[0]) for f in pos.index}
    today["standalone_var_99"] = standalone
    today["diversification"] = round(sum(standalone.values()) - today["hs"]["var_99"])

    backtests, summary = {}, {}
    for method in ("hs", "fhs"):
        r = risk.rolling_var(moves, pos, WINDOW, method)
        backtests[method] = r
        summary[method] = risk.backtest_summary(r)
    bt = backtests["hs"][["pnl", "var", "es", "exception"]].join(
        backtests["fhs"][["var", "es", "exception"]], rsuffix="_fhs")
    bt.round(0).to_csv(OUT / "risk_backtest.csv")

    stress = risk.stress_table(prices, pos)
    stress.round(0).to_csv(OUT / "risk_stress.csv", index=False)

    mon = risk.limit_monitor(moves[pos.index] @ pos, STOP_LOSS, PEAK_TO_TROUGH)
    mon.round(0).to_csv(OUT / "risk_limits.csv")
    limits = {
        "stop_loss_mtd": STOP_LOSS, "peak_to_trough": PEAK_TO_TROUGH,
        "stop_loss_breaches": [f"{d:%Y-%m-%d}" for d in risk.first_breaches(mon, "stop_loss_breach")],
        "drawdown_breaches": [f"{d:%Y-%m-%d}" for d in risk.first_breaches(mon, "drawdown_breach")],
        "days_in_stop_loss_breach": int(mon["stop_loss_breach"].sum()),
        "days_in_drawdown_breach": int(mon["drawdown_breach"].sum()),
        "max_drawdown": round(float(mon["drawdown"].min())),
        "worst_day": round(float(mon["pnl"].min())), "worst_day_date": f"{mon['pnl'].idxmin():%Y-%m-%d}",
    }

    margin = risk.margin_scenarios(prices, pos, stress)
    margin.round(0).to_csv(OUT / "risk_margin.csv", index=False)

    counts = checks.groupby(["factor", "check"]).size().unstack(fill_value=0)
    result = {
        "window": f"{prices.index[0]:%Y-%m-%d} to {prices.index[-1]:%Y-%m-%d}",
        "business_days": len(prices), "positions": pos.to_dict(),
        "prices_last": prices.iloc[-1].round(2).to_dict(),
        "input_checks": counts.to_dict(orient="index"),
        "var_today": today, "backtest": summary, "limits": limits,
        "margin": margin.set_index("scenario")[["initial_margin", "variation_margin", "cash_needed"]]
        .round(0).to_dict(orient="index"),
    }
    (OUT / "risk_summary.json").write_text(json.dumps(result, indent=2, default=float))
    print(json.dumps(result, indent=2, default=float))
    print(stress.round(0).to_string(index=False))


if __name__ == "__main__":
    main()
