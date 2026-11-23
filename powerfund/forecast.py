"""Day-ahead price forecast for the battery's ex-ante plan: a LEAR-style model.

LEAR (Lago et al. 2021, epftoolbox): one LASSO regression per delivery hour on lagged
prices and day-of-week dummies, with prices put through an asinh transform after
median / MAD scaling, recalibrated on a rolling window.

Information set for EFA day d: prices of EFA days d-1, d-2 and d-7. All of them come
from auctions held before d-1's morning auction for day d, so the plan uses nothing
published after the decision. No fundamentals (wind, demand forecasts) are used:
Elexon keeps only the latest forecast publication, which comes after the auction.
"""

import numpy as np
import pandas as pd
from sklearn.linear_model import LassoLarsIC

LAGS = (1, 2, 7)


def efa_matrix(hourly: pd.Series) -> pd.DataFrame:
    """Prices as one row of 24 hours per EFA day (23:00 UK start); 23- and 25-hour days resampled to 24."""
    local = hourly.index.tz_convert("Europe/London")
    day = pd.Index((local + pd.Timedelta(hours=1)).date)
    rows = {}
    for d, vals in hourly.groupby(day):
        v = vals.to_numpy()
        if len(v) < 20:
            continue
        rows[pd.Timestamp(d)] = v if len(v) == 24 else np.interp(
            np.linspace(0, 1, 24), np.linspace(0, 1, len(v)), v
        )
    return pd.DataFrame.from_dict(rows, orient="index").sort_index().asfreq("D")


def _features(m: pd.DataFrame) -> pd.DataFrame:
    parts = [m.shift(k).add_prefix(f"lag{k}_") for k in LAGS]
    dow = pd.get_dummies(m.index.dayofweek, prefix="dow").astype(float)
    dow.index = m.index
    return pd.concat(parts + [dow], axis=1)


def lear_forecast(hourly: pd.Series, start: str, window: int = 364, every: int = 28) -> pd.DataFrame:
    """Forecast rows (EFA day x 24 hours) from `start`, recalibrating every `every` days."""
    m = efa_matrix(hourly)
    X = _features(m)
    days = m.index[m.index >= pd.Timestamp(start)]
    out = {}
    models = None
    for i, d in enumerate(days):
        if i % every == 0 or models is None:
            train = m.index[(m.index < d) & (m.index >= d - pd.Timedelta(days=window))]
            ok = X.loc[train].notna().all(axis=1) & m.loc[train].notna().all(axis=1)
            Xt, Yt = X.loc[train][ok], m.loc[train][ok]
            med = np.nanmedian(Yt.to_numpy())
            mad = np.nanmedian(np.abs(Yt.to_numpy() - med)) * 1.4826 or 1.0
            tx = lambda a: np.arcsinh((a - med) / mad)  # noqa: E731
            lag_cols = [c for c in X.columns if c.startswith("lag")]
            Xs = Xt.copy()
            Xs[lag_cols] = tx(Xs[lag_cols])
            models = (med, mad, lag_cols, [LassoLarsIC(criterion="aic").fit(Xs, tx(Yt[h])) for h in range(24)])
        if X.loc[d].isna().any():
            continue
        med, mad, lag_cols, fits = models
        x = X.loc[[d]].copy()
        x[lag_cols] = np.arcsinh((x[lag_cols] - med) / mad)
        out[d] = [np.sinh(f.predict(x)[0]) * mad + med for f in fits]
    return pd.DataFrame.from_dict(out, orient="index")


def to_hourly(rows: pd.DataFrame, like: pd.Series) -> pd.Series:
    """Map EFA-day rows back onto the hourly index of `like` (resampling 23/25-hour days)."""
    local = like.index.tz_convert("Europe/London")
    day = pd.Index(pd.to_datetime((local + pd.Timedelta(hours=1)).date))
    out = pd.Series(np.nan, index=like.index)
    for d, pos in pd.Series(np.arange(len(like)), index=day).groupby(level=0):
        if d not in rows.index:
            continue
        v = rows.loc[d].to_numpy(dtype=float)
        n = len(pos)
        out.iloc[pos.to_numpy()] = v if n == 24 else np.interp(np.linspace(0, 1, n), np.linspace(0, 1, 24), v)
    return out
