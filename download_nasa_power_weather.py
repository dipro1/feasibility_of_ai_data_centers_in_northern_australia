"""Download hourly NASA POWER weather for every candidate site and year.

Files are written as inputs/weather/<site_id>_nasa_power_<year>.csv in local
solar time (LST). Existing files are kept unless --force is given.
"""

from __future__ import annotations

import argparse
import io
import time
import urllib.parse
import urllib.request

import pandas as pd

from studylib.config import WEATHER_DIR, load_config, load_sites


PARAMETERS = ["T2M", "T2MWET", "RH2M", "WS10M", "ALLSKY_SFC_SW_DWN", "ALLSKY_SFC_SW_DNI", "ALLSKY_SFC_SW_DIFF"]
TIME_COLUMNS = ["YEAR", "MO", "DY", "HR"]


def weather_path(site_id: str, year: int):
    return WEATHER_DIR / f"{site_id}_nasa_power_{year}.csv"


def download_year(site_id: str, year: int, latitude: float, longitude: float, force: bool = False):
    path = weather_path(site_id, year)
    if path.exists() and not force:
        return path
    query = urllib.parse.urlencode(
        {
            "parameters": ",".join(PARAMETERS),
            "community": "RE",
            "longitude": longitude,
            "latitude": latitude,
            "start": f"{year}0101",
            "end": f"{year}1231",
            "format": "CSV",
            "header": "false",
            "time-standard": "LST",
        }
    )
    url = f"https://power.larc.nasa.gov/api/temporal/hourly/point?{query}"
    expected_hours = 8784 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 8760
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=180) as response:
                payload = response.read().decode("utf-8")
            frame = pd.read_csv(io.StringIO(payload))
            missing = set(TIME_COLUMNS + PARAMETERS) - set(frame.columns)
            if missing:
                raise RuntimeError(f"NASA response is missing columns: {sorted(missing)}")
            frame = frame[TIME_COLUMNS + PARAMETERS].sort_values(TIME_COLUMNS)
            if len(frame) != expected_hours:
                raise RuntimeError(f"Expected {expected_hours} hourly rows, received {len(frame)}")
            WEATHER_DIR.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False)
            return path
        except Exception as exc:  # network retry boundary
            last_error = exc
            time.sleep(3 * 2**attempt)
    raise RuntimeError(f"NASA POWER download failed for {site_id} {year}: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    args = parser.parse_args()
    years = load_config()["renewable_bess"]["weather_years"]
    for site in load_sites():
        for year in years:
            path = download_year(site["site_id"], int(year), float(site["latitude"]), float(site["longitude"]), args.force)
            print(path, flush=True)


if __name__ == "__main__":
    main()
