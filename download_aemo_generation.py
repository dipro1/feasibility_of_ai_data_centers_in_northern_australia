"""Download AEMO per-generator metered output and emission factors.

Builds, for the configured region (QLD1):
* inputs/grid/emission_factors.csv - AEMO CDEII per-DUID CO2-e factors;
* inputs/grid/<region>_hourly_emissions.csv - hourly generation (MWh),
  emissions (t CO2-e) and intensity (t/MWh) for the most recent complete months;
* inputs/grid/solar_farm_hourly.csv - hourly output of the solar farms
  listed in solar_validation (for model validation);
* inputs/grid/cq_bus_hourly.csv - hourly net output (MWh, batteries signed)
  of the Central Queensland units mapped to multi-bus model buses in
  hourly_flow.unit_buses (for the hourly power-flow study).

Source: AEMO Dispatch SCADA archive (5-minute unit MW for every scheduled and
semi-scheduled unit, NEM time); energy per interval = MW / 12.
Existing outputs are kept unless --force is given.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import time
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

from studylib.config import INPUTS, load_config


GRID_DIR = INPUTS / "grid"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch(url: str, timeout: int = 180) -> bytes:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # network retry boundary
            last_error = exc
            time.sleep(3 * 2**attempt)
    raise RuntimeError(f"Download failed: {url}: {last_error}")


def emission_factors(region: str, url: str) -> pd.DataFrame:
    rows = []
    for record in csv.reader(io.StringIO(fetch(url).decode("utf-8"))):
        if record and record[0] == "D":
            rows.append({"station": record[4], "duid": record[5], "region": record[7],
                         "co2e_t_per_mwh": float(record[8]), "energy_source": record[9], "data_source": record[10]})
    factors = pd.DataFrame(rows).drop_duplicates("duid")
    return factors[factors["region"] == region]


def daily_archives(archive_url: str, days: int) -> list[str]:
    index = fetch(archive_url).decode("utf-8", errors="ignore")
    names = sorted(set(re.findall(r"PUBLIC_DISPATCHSCADA_(\d{8})\.zip", index)))
    return [f"{archive_url}PUBLIC_DISPATCHSCADA_{stamp}.zip" for stamp in names[-days:]]


def parse_interval(content: str, factors: dict[str, float], clean: set[str], farm_duids: set[str], totals, farms,
                   unit_bus: dict[str, str] | None = None, buses=None) -> None:
    for line in content.splitlines():
        if not line.startswith("D,DISPATCH,UNIT_SCADA"):
            continue
        parts = line.split(",")
        duid = parts[5]
        in_region = duid in factors
        mapped = unit_bus is not None and duid in unit_bus
        if not in_region and duid not in farm_duids and not mapped:
            continue
        mwh = float(parts[6] or 0.0) / 12.0
        interval_end = datetime.strptime(parts[4].strip('"'), "%Y/%m/%d %H:%M:%S")
        hour = (interval_end - timedelta(minutes=5)).replace(minute=0, second=0)
        if mapped:
            buses[(hour, unit_bus[duid])] += mwh
        if in_region and mwh > 0:
            totals[hour][0] += mwh
            totals[hour][1] += mwh * factors[duid]
            if duid in clean:
                totals[hour][2] += mwh
        if duid in farm_duids:
            farms[(hour, duid)] += max(mwh, 0.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    config = load_config()
    grid = config["grid_emissions"]
    region = grid["region"]
    emissions_path = GRID_DIR / f"{region}_hourly_emissions.csv"
    farm_path = GRID_DIR / "solar_farm_hourly.csv"
    bus_path = GRID_DIR / "cq_bus_hourly.csv"
    if emissions_path.exists() and farm_path.exists() and bus_path.exists() and not args.force:
        print(emissions_path)
        print(farm_path)
        print(bus_path)
        return
    # Only missing outputs are (re)written, so files built from an earlier 365-day
    # window are not silently replaced by a later one.
    write_emissions = args.force or not emissions_path.exists()
    write_farms = args.force or not farm_path.exists()
    unit_bus = {duid: bus for bus, duids in config["hourly_flow"]["unit_buses"].items() for duid in duids}

    GRID_DIR.mkdir(parents=True, exist_ok=True)
    factors_frame = emission_factors(region, grid["factors_url"])
    factors_frame.to_csv(GRID_DIR / "emission_factors.csv", index=False)
    # Batteries shift energy rather than generate it, so they are left out of the totals.
    storage = factors_frame["energy_source"].str.contains("Battery|Storage", case=False, na=False)
    factors = dict(zip(factors_frame.loc[~storage, "duid"], factors_frame.loc[~storage, "co2e_t_per_mwh"]))
    farm_duids = {farm["duid"] for farm in config["solar_validation"]["farms"]}

    clean = {duid for duid, factor in factors.items() if factor == 0}  # solar, wind, hydro
    totals = defaultdict(lambda: [0.0, 0.0, 0.0])
    farms = defaultdict(float)
    buses = defaultdict(float)
    urls = daily_archives(grid["archive_url"], grid["days"])
    for number, url in enumerate(urls, start=1):
        outer = zipfile.ZipFile(io.BytesIO(fetch(url)))
        for name in outer.namelist():
            inner = zipfile.ZipFile(io.BytesIO(outer.read(name)))
            for csv_name in inner.namelist():
                parse_interval(inner.read(csv_name).decode("utf-8", errors="ignore"), factors, clean, farm_duids, totals, farms,
                               unit_bus, buses)
        if number % 30 == 0 or number == len(urls):
            print(f"{number}/{len(urls)} days processed", flush=True)

    bus_frame = pd.DataFrame([{"timestamp": hour, "bus": bus, "mwh": mwh} for (hour, bus), mwh in buses.items()])
    bus_frame.pivot_table(index="timestamp", columns="bus", values="mwh", aggfunc="sum").fillna(0.0).sort_index().to_csv(bus_path)
    print(bus_path)
    if not (write_emissions or write_farms):
        return
    hourly = pd.DataFrame(
        [{"timestamp": hour, "generation_mwh": values[0], "emissions_t": values[1], "zero_emission_mwh": values[2]}
         for hour, values in totals.items()]
    ).sort_values("timestamp")
    hourly["intensity_t_per_mwh"] = hourly["emissions_t"] / hourly["generation_mwh"]
    hourly["zero_emission_share"] = hourly["zero_emission_mwh"] / hourly["generation_mwh"]
    if write_emissions:
        hourly.to_csv(emissions_path, index=False)
    if write_farms:
        farm_frame = pd.DataFrame([{"timestamp": hour, "duid": duid, "mwh": mwh} for (hour, duid), mwh in farms.items()])
        farm_frame.sort_values(["duid", "timestamp"]).to_csv(farm_path, index=False)
    print(emissions_path)
    print(farm_path)


if __name__ == "__main__":
    main()
