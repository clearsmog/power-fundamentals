"""Fetch and cache hourly power and fuel data.

Power: Energy-Charts API (Fraunhofer ISE), CC BY 4.0. Day-ahead prices per
bidding zone and public net generation / load per country, resampled to hourly.
Fuels: Yahoo Finance front-month Dutch TTF futures (EUR/MWh) and the
SparkChange physical EUA ETC (CO2.L) as a daily carbon-price proxy.
"""

import time
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

API = "https://api.energy-charts.info"
CACHE = Path(__file__).resolve().parent.parent / "data"

# market key -> (price bidding zone, generation country code)
MARKETS = {"DE": ("DE-LU", "de"), "ES": ("ES", "es"), "FR": ("FR", "fr")}

POWER_SERIES = {
    "Solar": "solar",
    "Wind onshore": "wind_onshore",
    "Wind offshore": "wind_offshore",
    "Fossil gas": "gas",
    "Load": "load",
    "Residual load": "residual_load",
}


def _years(start: str, end: str) -> list[tuple[str, str]]:
    """Calendar-year windows clipped to [start, end]: one API call per year keeps under the rate limit."""
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    return [
        (max(s, pd.Timestamp(y, 1, 1)).strftime("%Y-%m-%d"), min(e, pd.Timestamp(y, 12, 31)).strftime("%Y-%m-%d"))
        for y in range(s.year, e.year + 1)
    ]


def _get(path: str, params: dict) -> dict:
    """GET with backoff: the public API rate-limits bursts with HTTP 429."""
    for wait in (0, 10, 30, 60, 120):
        time.sleep(wait or 2)
        r = requests.get(f"{API}/{path}", params=params, timeout=60)
        if r.status_code != 429:
            r.raise_for_status()
            return r.json()
    r.raise_for_status()


def _hourly(index_seconds: list[int], columns: dict[str, list]) -> pd.DataFrame:
    idx = pd.to_datetime(index_seconds, unit="s", utc=True)
    df = pd.DataFrame(columns, index=idx).astype(float)
    return df.resample("1h").mean()


def fetch_market(market: str, start: str, end: str) -> pd.DataFrame:
    """Hourly price (EUR/MWh) and generation/load (MW) for one market, cached per year window."""
    bzn, country = MARKETS[market]
    CACHE.mkdir(exist_ok=True)
    frames = []
    for a, b in _years(start, end):
        f = CACHE / f"{market}_{a}_{b}.parquet"
        if not f.exists():
            p = _get("price", {"bzn": bzn, "start": a, "end": b})
            price = _hourly(p["unix_seconds"], {"price": p["price"]})
            g = _get("public_power", {"country": country, "start": a, "end": b})
            cols = {
                POWER_SERIES[t["name"]]: [v if v is not None else float("nan") for v in t["data"]]
                for t in g["production_types"]
                if t["name"] in POWER_SERIES
            }
            gen = _hourly(g["unix_seconds"], cols)
            price.join(gen, how="left").to_parquet(f)
        frames.append(pd.read_parquet(f))
    df = pd.concat(frames)
    df = df[~df.index.duplicated()].sort_index()
    for c in POWER_SERIES.values():
        if c not in df:
            df[c] = 0.0
    return df


def fetch_fuels(start: str, end: str) -> pd.DataFrame:
    """Daily TTF front-month (EUR/MWh) and EUA proxy (EUR/t), forward-filled over non-trading days."""
    f = CACHE / f"fuels_{start}_{end}.parquet"
    if not f.exists():
        CACHE.mkdir(exist_ok=True)
        raw = yf.download(["TTF=F", "CO2.L"], start=start, end=end, progress=False, auto_adjust=True)["Close"]
        raw = raw.rename(columns={"TTF=F": "ttf", "CO2.L": "eua"})
        raw.index = pd.to_datetime(raw.index).tz_localize(None)
        raw.to_parquet(f)
    fuels = pd.read_parquet(f)
    days = pd.date_range(start, end, freq="D")
    return fuels.reindex(days).ffill().bfill()
