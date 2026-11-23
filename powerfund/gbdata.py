"""Extra GB data for the deal-pricing module: PV_Live solar output and NESO response prices.

- PV_Live (Sheffield Solar, University of Sheffield): national GB solar generation
  estimate and installed capacity per half-hour, so output / capacity is a national
  solar capacity-factor profile. API v4, no key.
- NESO Enduring Auction Capability (EAC) results summary: day-ahead clearing price
  (GBP/MW/h) for each frequency-response product per 4-hour EFA block: Dynamic
  Containment (DC), Moderation (DM) and Regulation (DR). "L" products answer low
  frequency (a battery discharges, so it needs stored energy); "H" products answer
  high frequency (a battery charges, so it needs empty capacity).
"""

import time
from pathlib import Path

import pandas as pd
import requests

CACHE = Path(__file__).resolve().parent.parent / "data" / "gb_pricing"
PVLIVE = "https://api.pvlive.uk/pvlive/api/v4/pes/0"
NESO_SQL = "https://api.neso.energy/api/3/action/datastore_search_sql"
# EAC results summary resources: FY2023 archive, FY2024 archive, FY2025 archive, current year
EAC_RESOURCES = [
    "be5c6b0d-a335-4859-93f2-389585b4e9a1",
    "ab130833-3ce4-4361-90fb-69fa3cf30f15",
    "be55ee51-b79e-47da-b71e-a0f8865d9d66",
    "596f29ac-0387-4ba4-a6d3-95c243140707",
]
RESPONSE_PRODUCTS = ["DCL", "DCH", "DML", "DMH", "DRL", "DRH"]


def _cached(name: str, build) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{name}.parquet"
    if not f.exists():
        build().to_parquet(f)
    return pd.read_parquet(f)


def _pvlive_month(m: pd.Period) -> pd.DataFrame:
    start = m.start_time.strftime("%Y-%m-%dT00:00:00")
    end = (m.end_time.normalize() + pd.Timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    for attempt in range(8):
        try:
            r = requests.get(
                PVLIVE,
                params={"start": start, "end": end, "extra_fields": "installedcapacity_mwp"},
                headers={"User-Agent": "Mozilla/5.0 (power-fundamentals research script)"},
                timeout=(15, 120),
            )
            break
        except requests.exceptions.ConnectionError:
            if attempt == 7:
                raise
            time.sleep(5 * (attempt + 1))
    r.raise_for_status()
    j = r.json()
    df = pd.DataFrame(j["data"], columns=j["meta"])
    # PV_Live stamps the END of each half-hour; shift to the period start used by Elexon
    df["start"] = pd.to_datetime(df["datetime_gmt"], utc=True) - pd.Timedelta(minutes=30)
    lo = pd.Timestamp(m.start_time, tz="UTC")
    hi = pd.Timestamp(m.end_time.normalize() + pd.Timedelta(days=1), tz="UTC")
    df = df[(df["start"] >= lo) & (df["start"] < hi)]
    return pd.DataFrame(
        {
            "start": df["start"],
            "pv_mw": df["generation_mw"].astype(float),
            "pv_cap_mwp": df["installedcapacity_mwp"].astype(float),
        }
    ).drop_duplicates("start")


def fetch_pvlive(start: str, end: str) -> pd.DataFrame:
    """Half-hourly national solar output (MW), installed capacity (MWp) and capacity factor."""
    parts = [
        _cached(f"pvlive_{m}", lambda m=m: _pvlive_month(m))
        for m in pd.period_range(start, end, freq="M")
    ]
    df = pd.concat(parts).set_index("start").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df["pv_cf"] = (df["pv_mw"] / df["pv_cap_mwp"]).clip(lower=0)
    return df


def _eac_query(resource: str) -> pd.DataFrame:
    rows, offset = [], 0
    products = ",".join(f"'{p}'" for p in RESPONSE_PRODUCTS)
    while True:
        sql = (
            f'SELECT "auctionProduct","deliveryStart","deliveryEnd","clearingPrice",'
            f'"clearedVolume" FROM "{resource}" WHERE "auctionProduct" IN ({products}) '
            f'ORDER BY "deliveryStart" LIMIT 10000 OFFSET {offset}'
        )
        r = requests.get(NESO_SQL, params={"sql": sql}, timeout=120)
        r.raise_for_status()
        batch = r.json()["result"]["records"]
        rows += batch
        if len(batch) < 10000:
            break
        offset += 10000
    return pd.DataFrame(rows)


def fetch_response_prices() -> pd.DataFrame:
    """Clearing price (GBP/MW/h) per response product and EFA block, wide by product.

    deliveryStart is UTC without an offset: EFA block 1 of 1 Apr 2025 (23:00 BST)
    is stamped 2025-03-31T22:00:00.
    """

    def build():
        df = pd.concat([_eac_query(r) for r in EAC_RESOURCES], ignore_index=True)
        df["clearingPrice"] = df["clearingPrice"].astype(float)
        df["clearedVolume"] = df["clearedVolume"].astype(float)
        return df

    df = _cached("eac_response", build)
    df["block_start"] = pd.to_datetime(df["deliveryStart"]).dt.tz_localize("UTC")
    df = df.drop_duplicates(
        ["auctionProduct", "block_start"], keep="last"
    )
    return df.pivot(index="block_start", columns="auctionProduct", values="clearingPrice")


LCCC_DUMP = "https://dp.lowcarboncontracts.uk/datastore/dump/b5ad240c-5b08-4686-bfb7-4568d2bda1d3"


def fetch_lccc_forecast() -> pd.DataFrame:
    """LCCC forecast market reference prices (GBP/MWh) by delivery day, period type and technology.

    Holds only the current and next CfD quarters. The Baseload Market Reference Price
    (BMRP) is LCCC's volume-weighted average of LEBA forward-season trades, so it is the
    one free, published GB forward baseload price. The IMRP rows are LCCC's forecasts of
    the day-ahead price weighted by each technology's output.
    """

    def build():
        r = requests.get(
            LCCC_DUMP, params={"format": "json"}, timeout=120,
            headers={"User-Agent": "Mozilla/5.0 (power-fundamentals research script)"},
        )
        r.raise_for_status()
        j = r.json()
        return pd.DataFrame(j["records"], columns=[f["id"] for f in j["fields"]])

    df = _cached("lccc_forecast_mrp", build)
    return pd.DataFrame(
        {
            "period": df["Quarterly_Obligation_Period"],
            "day": pd.to_datetime(df["DateTime"].str[:10]),
            "technology": df["Technology"],
            "price": df["Forecast_Reference_Price_GBP_per_MWh"].astype(float),
        }
    )


N2EX_RESOURCE = "4f27eea5-7038-4f73-9740-e3e4ad47c26a"


def fetch_n2ex() -> pd.Series:
    """N2EX GB day-ahead hourly auction price (GBP/MWh), indexed by UTC hour start.

    NESO republishes Nord Pool's GB day-ahead price (Sept 2021 onward) under the EU
    transparency rules. Delivery periods are UK local hours; on the autumn clock change
    the repeated hour appears twice, first in BST.
    """

    def build():
        rows, offset = [], 0
        while True:
            sql = (
                f'SELECT "_id","Date","Delivery Period","Price" FROM "{N2EX_RESOURCE}" '
                f'ORDER BY "_id" LIMIT 10000 OFFSET {offset}'
            )
            r = requests.get(NESO_SQL, params={"sql": sql}, timeout=120)
            r.raise_for_status()
            batch = r.json()["result"]["records"]
            rows += batch
            if len(batch) < 10000:
                return pd.DataFrame(rows)
            offset += 10000

    df = _cached("n2ex_da", build)
    df["_id"] = df["_id"].astype(int)
    df = df.sort_values("_id")
    hour = df["Delivery Period"].str[:2].astype(int)
    local = pd.to_datetime(df["Date"]) + pd.to_timedelta(hour, unit="h")
    # the repeated autumn hour: first occurrence is BST (DST), the second GMT
    first = ~local.duplicated(keep="first")
    idx = pd.DatetimeIndex(local).tz_localize(
        "Europe/London", ambiguous=first.to_numpy(), nonexistent="NaT"
    )
    s = pd.Series(df["Price"].astype(float).to_numpy(), index=idx).dropna()
    s = s[s.index.notna()]
    s.index = s.index.tz_convert("UTC")
    return s[~s.index.duplicated(keep="first")].sort_index().rename("da")
