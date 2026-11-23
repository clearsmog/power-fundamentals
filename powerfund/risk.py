"""Market risk for a notional power and gas book: input checks, VaR and ES, back-testing,
stress scenarios, loss limits and exchange margin.

Risk factors are daily prices: German and French day-ahead baseload (EUR/MWh), TTF front
month (EUR/MWh) and the EUA proxy (EUR/t). P&L is linear in price, so a scenario's P&L is
the price change times the position.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

FACTORS = ["DE", "FR", "TTF", "EUA"]

# Notional book. Quantities in MWh (power, gas) or t (EUA); sign is long (+) or short (-).
# A sold clean spark spread (50 MW German baseload for a 30-day month, with the gas and
# carbon a 49.13% CCGT burns to make it) and a 20 MW France-over-Germany spread.
TRADES = pd.DataFrame(
    [
        ("T1", "DE", -36_000, "sell 50 MW DE baseload, month"),
        ("T2", "TTF", 73_300, "buy TTF for 36,000 MWh at 49.13% efficiency"),
        ("T3", "EUA", 14_800, "buy EUAs at 0.202 t per MWh of gas"),
        ("T4", "FR", 14_400, "buy 20 MW FR baseload, month"),
        ("T5", "DE", -14_400, "sell 20 MW DE baseload, month"),
    ],
    columns=["trade_id", "factor", "quantity", "description"],
)


def positions(trades: pd.DataFrame = TRADES) -> pd.Series:
    """Net position per risk factor."""
    return trades.groupby("factor")["quantity"].sum().reindex(FACTORS, fill_value=0.0).astype(float)


# ---------------------------------------------------------------- input data

def daily_prices(power: dict[str, pd.DataFrame], fuels_raw: pd.DataFrame) -> pd.DataFrame:
    """Business-day price panel before any filling: power is the CET-day baseload mean."""
    cols = {}
    for mk, df in power.items():
        local = df["price"].tz_convert("Europe/Berlin")
        cols[mk] = local.groupby(local.index.tz_localize(None).normalize()).mean()
    cols["TTF"] = fuels_raw["ttf"]
    cols["EUA"] = fuels_raw["eua"]
    panel = pd.DataFrame(cols)
    start, end = fuels_raw.index.min(), fuels_raw.index.max()
    days = pd.bdate_range(start, end)
    return panel.reindex(days)[FACTORS]


def input_checks(prices: pd.DataFrame, z: float = 6.0, stale_days: int = 3) -> pd.DataFrame:
    """Flags the checks a VaR run needs on its inputs, one row per factor and date.

    missing: no price on a business day. stale: the same price for `stale_days` or more
    days in a row. outlier: a daily change more than `z` robust standard deviations
    (1.4826 x median absolute deviation) from the median. non_positive: a price at or
    below zero, where a log or relative return is undefined.
    """
    rows = []
    for f in prices.columns:
        p = prices[f]
        missing = p.isna()
        run = p.ne(p.shift()).cumsum()
        stale = p.notna() & (p.groupby(run).transform("size") >= stale_days) & p.eq(p.shift())
        d = p.ffill().diff()
        mad = 1.4826 * (d - d.median()).abs().median()
        outlier = ((d - d.median()).abs() > z * mad) & ~missing
        non_pos = p <= 0
        for name, mask in (("missing", missing), ("stale", stale), ("outlier", outlier), ("non_positive", non_pos)):
            for day in p.index[mask.fillna(False)]:
                rows.append((f, day, name, p.get(day), d.get(day)))
    return pd.DataFrame(rows, columns=["factor", "date", "check", "price", "change"])


def clean(prices: pd.DataFrame) -> pd.DataFrame:
    """Fill missing prices with the last good one (rolled-forward price, zero change)."""
    return prices.ffill().dropna()


# ---------------------------------------------------------------- VaR and ES

def changes(prices: pd.DataFrame, kind: str = "absolute") -> pd.DataFrame:
    """Daily scenario moves: absolute (EUR) or relative (fraction of the prior price)."""
    if kind == "absolute":
        return prices.diff().dropna()
    if kind == "relative":
        return (prices / prices.shift() - 1).dropna()
    raise ValueError(kind)


def scenario_pnl(moves: pd.DataFrame, pos: pd.Series, prices_now: pd.Series | None = None,
                 kind: str = "absolute") -> pd.Series:
    """P&L of today's book under each historical move."""
    if kind == "absolute":
        return moves[pos.index] @ pos
    return moves[pos.index] @ (pos * prices_now[pos.index])


def var_es(pnl: pd.Series | np.ndarray, var_level: float = 0.99, es_level: float = 0.975) -> tuple[float, float]:
    """Historical VaR and ES as positive loss numbers.

    VaR is the loss at the (1 - var_level) quantile; ES the average of the losses at or
    beyond the (1 - es_level) quantile.
    """
    x = np.sort(np.asarray(pnl, dtype=float))
    var = -np.quantile(x, 1 - var_level)
    tail = x[x <= np.quantile(x, 1 - es_level)]
    return float(var), float(-tail.mean())


def ewma_vol(moves: pd.DataFrame, lam: float = 0.94) -> pd.DataFrame:
    """RiskMetrics EWMA volatility of each factor's move, known at the end of each day."""
    return np.sqrt((moves ** 2).ewm(alpha=1 - lam, adjust=False).mean())


def filtered_moves(window: pd.DataFrame, vol: pd.DataFrame, vol_now: pd.Series) -> pd.DataFrame:
    """Filtered historical simulation: rescale each past move to today's volatility.

    `vol` must be the volatility known before each move (lagged one day), so a move is
    divided by the volatility it was drawn under.
    """
    return window / vol.loc[window.index] * vol_now


def rolling_var(moves: pd.DataFrame, pos: pd.Series, window: int = 250, method: str = "hs",
                lam: float = 0.94, var_level: float = 0.99, es_level: float = 0.975) -> pd.DataFrame:
    """One-day VaR and ES forecast for each day from the prior `window` days, against the
    realised P&L of the same book that day. No look-ahead: day t uses moves up to t-1."""
    vol = ewma_vol(moves, lam)
    vol_before = vol.shift().bfill()
    out = []
    for i in range(window, len(moves)):
        hist = moves.iloc[i - window:i]
        if method == "fhs":
            hist = filtered_moves(hist, vol_before, vol.iloc[i - 1])
        var, es = var_es(scenario_pnl(hist, pos), var_level, es_level)
        out.append((moves.index[i], var, es, float(moves.iloc[i][pos.index] @ pos)))
    r = pd.DataFrame(out, columns=["date", "var", "es", "pnl"]).set_index("date")
    r["exception"] = r["pnl"] < -r["var"]
    return r


# ---------------------------------------------------------------- back-testing

def kupiec(exceptions: int, n: int, p: float = 0.01) -> tuple[float, float]:
    """Proportion-of-failures likelihood ratio and its chi-squared(1) p-value."""
    x, phat = exceptions, exceptions / n
    ll0 = (n - x) * np.log(1 - p) + x * np.log(p)
    ll1 = (n - x) * np.log(1 - phat) + x * np.log(phat) if 0 < x < n else 0.0
    lr = -2 * (ll0 - ll1)
    return float(lr), float(1 - stats.chi2.cdf(lr, 1))


def christoffersen(hits: np.ndarray) -> tuple[float, float]:
    """Independence likelihood ratio (do exceptions cluster?) and its p-value."""
    h = np.asarray(hits, dtype=int)
    a, b = h[:-1], h[1:]
    n00, n01 = int(((a == 0) & (b == 0)).sum()), int(((a == 0) & (b == 1)).sum())
    n10, n11 = int(((a == 1) & (b == 0)).sum()), int(((a == 1) & (b == 1)).sum())

    def ll(p, k0, k1):
        return (k0 * np.log(1 - p) if k0 else 0.0) + (k1 * np.log(p) if k1 else 0.0)

    pi = (n01 + n11) / (n00 + n01 + n10 + n11)
    p01 = n01 / (n00 + n01) if n00 + n01 else 0.0
    p11 = n11 / (n10 + n11) if n10 + n11 else 0.0
    lr = -2 * (ll(pi, n00 + n10, n01 + n11) - ll(p01, n00, n01) - ll(p11, n10, n11))
    return float(lr), float(1 - stats.chi2.cdf(lr, 1))


def traffic_light(exceptions_250: int) -> str:
    """Basel zone for exceptions of a 99% VaR over 250 days."""
    return "green" if exceptions_250 <= 4 else "yellow" if exceptions_250 <= 9 else "red"


def backtest_summary(r: pd.DataFrame, p: float = 0.01) -> dict:
    n, x = len(r), int(r["exception"].sum())
    lr_pof, p_pof = kupiec(x, n, p)
    lr_ind, p_ind = christoffersen(r["exception"].to_numpy())
    last = int(r["exception"].iloc[-250:].sum())
    return {"days": n, "exceptions": x, "expected": round(n * p, 1), "kupiec_lr": round(lr_pof, 2),
            "kupiec_p": round(p_pof, 3), "christoffersen_lr": round(lr_ind, 2),
            "christoffersen_p": round(p_ind, 3), "last_250_exceptions": last,
            "traffic_light_last_250": traffic_light(last),
            "mean_var": round(float(r["var"].mean())), "mean_es": round(float(r["es"].mean()))}


# ---------------------------------------------------------------- stress

# Hypothetical scenarios. Power and EUA shocks in price units; gas shocks as a fraction.
HYPOTHETICAL = {
    "Gas supply shock: TTF +50%": {"TTF": ("rel", 0.50)},
    "Gas collapse: TTF -30%": {"TTF": ("rel", -0.30)},
    "Cold snap: TTF +40%, DE and FR +EUR 60": {"TTF": ("rel", 0.40), "DE": ("abs", 60), "FR": ("abs", 60)},
    "Renewables surge: DE and FR -EUR 50": {"DE": ("abs", -50), "FR": ("abs", -50)},
    "Spread blowout: FR -EUR 40 vs DE": {"FR": ("abs", -40)},
    "Carbon sell-off: EUA -20%": {"EUA": ("rel", -0.20)},
    "Spark spread widening: DE +EUR 40, TTF -20%": {"DE": ("abs", 40), "TTF": ("rel", -0.20)},
    "Dunkelflaute: DE +EUR 130, FR +EUR 60": {"DE": ("abs", 130), "FR": ("abs", 60)},
}


def apply_shock(shock: dict, prices_now: pd.Series) -> pd.Series:
    move = pd.Series(0.0, index=prices_now.index)
    for f, (how, size) in shock.items():
        move[f] = size if how == "abs" else size * prices_now[f]
    return move


def stress_table(prices: pd.DataFrame, pos: pd.Series, horizons: tuple[int, ...] = (1, 5)) -> pd.DataFrame:
    """Hypothetical scenarios at today's prices, plus the worst historical windows for this book."""
    now = prices.iloc[-1]
    rows = [(name, "hypothetical", "", float(apply_shock(s, now)[pos.index] @ pos))
            for name, s in HYPOTHETICAL.items()]
    for h in horizons:
        pnl = prices.diff(h).dropna()[pos.index] @ pos
        day = pnl.idxmin()
        start = prices.index[prices.index.get_loc(day) - h]
        rows.append((f"Worst historical {h}-day move for this book", "historical",
                     f"{start:%Y-%m-%d} to {day:%Y-%m-%d}", float(pnl.min())))
    return pd.DataFrame(rows, columns=["scenario", "type", "window", "pnl"])


# ---------------------------------------------------------------- limits

def limit_monitor(pnl: pd.Series, stop_loss: float, peak_to_trough: float) -> pd.DataFrame:
    """Month-to-date stop-loss and peak-to-trough drawdown on cumulative P&L.

    A stop-loss breach is a month-to-date loss beyond `stop_loss`; a drawdown breach is
    cumulative P&L more than `peak_to_trough` below its running peak. One row per day.
    """
    cum = pnl.cumsum()
    mtd = pnl.groupby(pnl.index.to_period("M")).cumsum()
    drawdown = cum - cum.cummax().clip(lower=0.0)  # the peak starts at zero, before any P&L
    return pd.DataFrame({"pnl": pnl, "mtd": mtd, "drawdown": drawdown,
                         "stop_loss_breach": mtd < -stop_loss,
                         "drawdown_breach": drawdown < -peak_to_trough})


def first_breaches(mon: pd.DataFrame, col: str) -> list[pd.Timestamp]:
    """Days a limit was first breached (a breach that runs on counts once)."""
    b = mon[col]
    return list(b.index[b & ~b.shift(fill_value=False)])


# ---------------------------------------------------------------- margin

def scan_ranges(prices: pd.DataFrame, window: int = 250, days: int = 2, q: float = 0.99) -> pd.Series:
    """Per-unit initial margin, exchange style: the q quantile of absolute `days`-day price
    moves over the last `window` days."""
    moves = prices.diff(days).dropna().iloc[-window:]
    return moves.abs().quantile(q)


def margin_scenarios(prices: pd.DataFrame, pos: pd.Series, stress: pd.DataFrame) -> pd.DataFrame:
    """Cash the book needs at the exchange today and under three stresses.

    Initial margin = |position| x scan range per factor (no spread credit). Variation
    margin is the day's loss paid in cash. Price shock: the worst stress scenario's loss
    is paid as variation margin. Volatility spike: scan ranges taken from the EWMA
    volatility doubled. Margin parameter change: the exchange adds 25% to every scan range.
    """
    scan = scan_ranges(prices)
    im = float((pos.abs() * scan[pos.index]).sum())
    vol_now = ewma_vol(prices.diff().dropna()).iloc[-1]
    scan_vol = np.sqrt(2) * stats.norm.ppf(0.99) * 2 * vol_now
    worst = stress.loc[stress["pnl"].idxmin()]
    rows = [
        ("Today", im, 0.0, ""),
        ("Price shock", im, -float(worst["pnl"]), worst["scenario"]),
        ("Volatility spike (EWMA vol x2)", float((pos.abs() * scan_vol[pos.index]).sum()), 0.0, ""),
        ("Margin parameter change (+25%)", 1.25 * im, 0.0, ""),
    ]
    out = pd.DataFrame(rows, columns=["scenario", "initial_margin", "variation_margin", "driver"])
    out["cash_needed"] = out["initial_margin"] + out["variation_margin"]
    out["extra_vs_today"] = out["cash_needed"] - im
    return out
