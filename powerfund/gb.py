"""GB balancing and imbalance: fetch Elexon BMRS data and measure imbalance costs.

Data: Elexon Insights Solution (BMRS) API, no key needed, per half-hour
settlement period:
  - system prices (DISEBSP): the single imbalance (cash-out) price and the net
    imbalance volume (NIV) for each period;
  - market index (MID, APX provider): volume-weighted short-term traded price;
  - wind outturn (AGWS) and the day-ahead wind forecast (DGWS), onshore plus
    offshore, published by NESO under the ENTSO-E transparency rules.

Imbalance metric. A wind generator that sells its forecast F ahead at the
market index price and settles the difference to actual output A at the
system price SP earns F * MID + (A - F) * SP. Against a perfect forecast
(A * MID) the imbalance cost is (F - A) * (SP - MID). It is a cost when the
farm under-delivers while the system price is above the market (system short)
or over-delivers while it is below (system long).
"""

import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

API = "https://data.elexon.co.uk/bmrs/api/v1"
CACHE = Path(__file__).resolve().parent.parent / "data" / "gb"
MAX_DAYS = 7  # the API rejects ranges longer than 7 days inclusive


def _get(path: str, params: dict) -> dict:
    for wait in (0, 5, 20, 60):
        time.sleep(wait or 0.2)
        r = requests.get(
            f"{API}{path}", params={**params, "format": "json"}, timeout=60
        )
        if r.status_code not in (429, 500, 502, 503, 504):
            r.raise_for_status()
            return r.json()
    r.raise_for_status()


def _windows(start: str, end: str) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    days = pd.date_range(start, end, freq="D")
    return [
        (days[i], days[min(i + MAX_DAYS - 1, len(days) - 1)])
        for i in range(0, len(days), MAX_DAYS)
    ]


def _cached(name: str, month: pd.Period, build) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{name}_{month}.parquet"
    if not f.exists():
        build().to_parquet(f)
    return pd.read_parquet(f)


def _months(start: str, end: str) -> list[pd.Period]:
    return list(pd.period_range(start, end, freq="M"))


def _month_bounds(m: pd.Period, start: str, end: str) -> tuple[str, str]:
    a = max(m.start_time, pd.Timestamp(start)).strftime("%Y-%m-%d")
    b = min(m.end_time.normalize(), pd.Timestamp(end)).strftime("%Y-%m-%d")
    return a, b


def _in_month(start: pd.Series, a: str, b: str) -> pd.Series:
    """Periods starting from day a through the end of day b (UTC)."""
    lo = pd.Timestamp(a, tz="UTC")
    hi = pd.Timestamp(b, tz="UTC") + pd.Timedelta(days=1)
    return (start >= lo) & (start < hi)


def _system_prices_month(a: str, b: str) -> pd.DataFrame:
    rows = []
    for d in pd.date_range(a, b, freq="D"):
        rows += _get(f"/balancing/settlement/system-prices/{d:%Y-%m-%d}", {})["data"]
    df = pd.DataFrame(rows)
    return pd.DataFrame(
        {
            "start": pd.to_datetime(df["startTime"], utc=True),
            "ssp": df["systemSellPrice"].astype(float),
            "sbp": df["systemBuyPrice"].astype(float),
            "niv": df["netImbalanceVolume"].astype(float),
        }
    )


def _mid_month(a: str, b: str) -> pd.DataFrame:
    rows = []
    for s, e in _windows(a, b):
        rows += _get(
            "/datasets/MID",
            {
                "from": f"{s:%Y-%m-%d}T00:00Z",
                "to": f"{e + pd.Timedelta(days=1):%Y-%m-%d}T00:00Z",
            },
        )["data"]
    df = pd.DataFrame(rows)
    df = df[df["dataProvider"] == "APXMIDP"].copy()
    df["startTime"] = pd.to_datetime(df["startTime"], utc=True)
    df = df[_in_month(df["startTime"], a, b)].drop_duplicates("startTime", keep="last")
    return pd.DataFrame(
        {
            "start": df["startTime"],
            "mid": df["price"].astype(float),
            "mid_volume": df["volume"].astype(float),
        }
    )


def _wind_month(
    a: str, b: str, path: str, extra: dict, business: str = "Wind generation"
) -> pd.DataFrame:
    rows = []
    for s, e in _windows(a, b):
        # "to" only reaches the first period of its day, so ask for one extra day
        to = f"{e + pd.Timedelta(days=1):%Y-%m-%d}"
        rows += _get(path, {"from": f"{s:%Y-%m-%d}", "to": to, **extra})["data"]
    df = pd.DataFrame(rows)
    df = df[df["businessType"] == business].copy()
    df["start"] = pd.to_datetime(df["startTime"], utc=True)
    df = df[_in_month(df["start"], a, b)]
    df["publish"] = pd.to_datetime(df["publishTime"], utc=True)
    # keep the latest publication per period and type, then add onshore + offshore
    df = df.sort_values("publish").drop_duplicates(["start", "psrType"], keep="last")
    return df.groupby("start", as_index=False)["quantity"].sum()


def fetch_gb(start: str, end: str) -> pd.DataFrame:
    """Half-hourly GB system price, NIV, market index, wind outturn and day-ahead wind forecast (MW)."""
    parts = {"sp": [], "mid": [], "wind": [], "fc": []}
    for m in _months(start, end):
        a, b = _month_bounds(m, start, end)
        parts["sp"].append(
            _cached("system_prices", m, lambda: _system_prices_month(a, b))
        )
        parts["mid"].append(_cached("mid", m, lambda: _mid_month(a, b)))
        parts["wind"].append(
            _cached(
                "wind_outturn",
                m,
                lambda: _wind_month(
                    a, b, "/generation/actual/per-type/wind-and-solar", {}
                ),
            )
        )
        parts["fc"].append(
            _cached(
                "wind_dayahead",
                m,
                lambda: _wind_month(
                    a,
                    b,
                    "/forecast/generation/wind-and-solar/day-ahead",
                    {"processType": "day ahead"},
                ),
            )
        )

    def cat(k):
        d = (
            pd.concat(parts[k])
            .drop_duplicates("start", keep="last")
            .set_index("start")
            .sort_index()
        )
        return d

    df = cat("sp").join(cat("mid"), how="left")
    df = df.join(cat("wind").rename(columns={"quantity": "wind"}), how="left")
    df = df.join(cat("fc").rename(columns={"quantity": "wind_fc"}), how="left")
    # an APX index with no traded volume is not a price
    df.loc[df["mid_volume"] <= 0, "mid"] = np.nan
    return df


def fetch_solar(start: str, end: str) -> pd.DataFrame:
    """Half-hourly GB solar outturn and latest day-ahead solar forecast (MW), Elexon AGWS / DGWS."""
    out, fc = [], []
    for m in _months(start, end):
        a, b = _month_bounds(m, start, end)
        out.append(
            _cached(
                "solar_outturn",
                m,
                lambda: _wind_month(
                    a, b, "/generation/actual/per-type/wind-and-solar", {},
                    business="Solar generation",
                ),
            )
        )
        fc.append(
            _cached(
                "solar_dayahead",
                m,
                lambda: _wind_month(
                    a, b, "/forecast/generation/wind-and-solar/day-ahead",
                    {"processType": "day ahead"}, business="Solar generation",
                ),
            )
        )

    def cat(parts, name):
        return (
            pd.concat(parts).drop_duplicates("start", keep="last")
            .set_index("start").sort_index()["quantity"].rename(name)
        )

    return pd.concat([cat(out, "solar"), cat(fc, "solar_fc")], axis=1)


# --- metrics -----------------------------------------------------------------


def imbalance_cost(df: pd.DataFrame) -> pd.Series:
    """Per-period imbalance cost in GBP of selling the forecast at MID, vs a perfect forecast.

    MW over a half-hour settlement period is MW * 0.5 MWh.
    """
    return (df["wind_fc"] - df["wind"]) * 0.5 * (df["sp"] - df["mid"])


def cost_per_mwh(df: pd.DataFrame) -> float:
    """Imbalance cost per MWh of actual wind output (GBP/MWh)."""
    d = df.dropna(subset=["wind", "wind_fc", "sp", "mid"])
    return float(imbalance_cost(d).sum() / (d["wind"] * 0.5).sum())


def cost_decomposition(df: pd.DataFrame) -> dict:
    """Split the mean per-period cost into mean-error x mean-spread and covariance terms.

    E[(F-A)(SP-MID)] = E[F-A] * E[SP-MID] + Cov(F-A, SP-MID). The covariance term is
    the 'wrong-way' part: wind falls short exactly when the system is short.
    """
    d = df.dropna(subset=["wind", "wind_fc", "sp", "mid"])
    err = (d["wind_fc"] - d["wind"]) * 0.5
    spread = d["sp"] - d["mid"]
    total = float((err * spread).mean())
    means = float(err.mean() * spread.mean())
    cov = float(((err - err.mean()) * (spread - spread.mean())).mean())
    return {"total": total, "mean_term": means, "cov_term": cov}


def spread_by_niv_sign(df: pd.DataFrame) -> pd.DataFrame:
    """Mean SP, MID and SP - MID when the system is short (NIV > 0) vs long (NIV < 0)."""
    d = df.dropna(subset=["sp", "mid", "niv"]).copy()
    d["state"] = np.where(d["niv"] > 0, "short", np.where(d["niv"] < 0, "long", "flat"))
    d["spread"] = d["sp"] - d["mid"]
    g = d.groupby("state").agg(
        periods=("sp", "size"),
        sp=("sp", "mean"),
        mid=("mid", "mean"),
        spread=("spread", "mean"),
    )
    g["share"] = g["periods"] / g["periods"].sum()
    return g
