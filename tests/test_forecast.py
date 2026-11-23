import numpy as np
import pandas as pd

from powerfund import forecast


def _hourly(days=500, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01 23:00", periods=24 * days, freq="h", tz="UTC")
    hour = idx.hour.to_numpy()
    p = 80 + 30 * np.sin((hour - 6) / 24 * 2 * np.pi) + rng.normal(0, 5, len(idx))
    return pd.Series(p, index=idx)


def test_forecast_uses_no_future_prices():
    s = _hourly()
    start = "2024-03-01"
    f1 = forecast.lear_forecast(s, start, every=1000)
    s2 = s.copy()
    cut = pd.Timestamp("2024-03-10", tz="Europe/London").tz_convert("UTC")
    s2[s2.index >= cut] += 500  # change everything from 10 March on
    f2 = forecast.lear_forecast(s2, start, every=1000)
    # forecasts for EFA days up to 10 March only use earlier prices, so they must not move
    days = f1.index[f1.index <= pd.Timestamp("2024-03-10")]
    assert len(days) > 5
    assert np.allclose(f1.loc[days], f2.loc[days])


def test_forecast_beats_flat_mean_on_a_daily_shape():
    s = _hourly()
    f = forecast.to_hourly(forecast.lear_forecast(s, "2024-02-01"), s)
    ok = f.notna()
    assert (f[ok] - s[ok]).abs().mean() < (s[ok] - s[ok].mean()).abs().mean() / 2
