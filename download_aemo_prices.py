"""Download AEMO 5-minute price and demand data and aggregate to hourly means.

Writes inputs/prices/<region>_hourly.csv with columns timestamp (NEM time,
AEST = UTC+10), rrp_per_mwh and demand_mw. Only NEM regions named in
inputs/candidate_sites.csv are downloaded; the NWIS has no public spot market.
"""

from __future__ import annotations

import argparse
import io
import time
import urllib.request

import pandas as pd

from studylib.config import PRICE_DIR, load_config, load_sites


URL = "https://aemo.com.au/aemo/data/nem/priceanddemand/PRICE_AND_DEMAND_{year}{month:02d}_{region}.csv"


def fetch_month(region: str, year: int, month: int) -> pd.DataFrame:
    request = urllib.request.Request(
        URL.format(year=year, month=month, region=region), headers={"User-Agent": "Mozilla/5.0"}
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return pd.read_csv(io.StringIO(response.read().decode("utf-8")))
        except Exception as exc:  # network retry boundary
            last_error = exc
            time.sleep(3 * 2**attempt)
    raise RuntimeError(f"AEMO download failed for {region} {year}-{month:02d}: {last_error}")


def build_region(region: str, years: list[int], force: bool, suffix: str = "hourly", allow_missing: bool = False) -> None:
    path = PRICE_DIR / f"{region}_{suffix}.csv"
    if path.exists() and not force:
        print(path)
        return
    frames = []
    for year in years:
        for month in range(1, 13):
            try:
                frames.append(fetch_month(region, year, month))
            except RuntimeError:
                if not allow_missing:
                    raise
                print(f"{region} {year}-{month:02d} not published yet; stopping there")
                break
    raw = pd.concat(frames, ignore_index=True)
    raw["SETTLEMENTDATE"] = pd.to_datetime(raw["SETTLEMENTDATE"])
    # An interval ending HH:05 ... (HH+1):00 belongs to hour HH.
    raw["timestamp"] = (raw["SETTLEMENTDATE"] - pd.Timedelta(minutes=5)).dt.floor("h")
    hourly = raw.groupby("timestamp", as_index=False).agg(
        rrp_per_mwh=("RRP", "mean"), demand_mw=("TOTALDEMAND", "mean")
    )
    hourly = hourly[hourly["timestamp"].dt.year.isin(years)]
    PRICE_DIR.mkdir(parents=True, exist_ok=True)
    hourly.to_csv(path, index=False)
    print(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    years = [int(year) for year in load_config()["economics"]["price_years"]]
    regions = sorted({site["price_region"] for site in load_sites() if site["grid"] == "NEM"})
    recent = [int(year) for year in load_config()["recent_prices"]["years"]]
    for region in regions:
        build_region(region, years, args.force)
        # Months after the base weather years, for the recent-price sensitivity.
        build_region(region, recent, args.force, suffix="recent_hourly", allow_missing=True)


if __name__ == "__main__":
    main()
