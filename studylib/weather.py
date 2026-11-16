"""Weather loading and the pvlib solar-PV model.

NASA POWER hourly files are in local solar time (LST = UTC + longitude/15 h).
Each hourly value is treated as the average over [HR, HR+1), so solar
geometry is evaluated at the middle of the hour.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
import pvlib

from .config import ROOT, WEATHER_DIR, load_config, site_by_id


RENAME = {
    "T2M": "temp_c",
    "T2MWET": "wet_bulb_c",
    "RH2M": "relative_humidity",
    "WS10M": "wind_10m",
    "ALLSKY_SFC_SW_DWN": "ghi",
    "ALLSKY_SFC_SW_DNI": "dni",
    "ALLSKY_SFC_SW_DIFF": "dhi",
}


def read_weather_files(site_id: str, years: tuple[int, ...]) -> pd.DataFrame:
    frames = []
    for year in years:
        path = WEATHER_DIR / f"{site_id}_nasa_power_{year}.csv"
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing. Run download_nasa_power_weather.py first.")
        frames.append(pd.read_csv(path))
    raw = pd.concat(frames, ignore_index=True).replace(-999.0, np.nan)
    weather = raw.rename(columns=RENAME)
    columns = list(RENAME.values())
    missing_fraction = weather[columns].isna().mean()
    if (missing_fraction > 0.02).any():
        raise RuntimeError(f"Too many missing values in {site_id} weather: {missing_fraction.to_dict()}")
    weather[columns] = weather[columns].interpolate(limit_direction="both")
    weather["timestamp"] = pd.to_datetime(
        raw.rename(columns={"YEAR": "year", "MO": "month", "DY": "day", "HR": "hour"})[["year", "month", "day", "hour"]]
    )
    for column in ("ghi", "dni", "dhi"):
        weather[column] = weather[column].clip(lower=0.0)
    return weather[["timestamp", *columns]]


def solar_ac_per_mw(weather: pd.DataFrame, latitude: float, longitude: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (AC output per MW AC of plant, cell temperature in deg C)."""
    solar = load_config()["solar"]
    mid_hour_utc = weather["timestamp"] + pd.Timedelta(minutes=30) - pd.to_timedelta(longitude / 15.0, unit="h")
    times = pd.DatetimeIndex(mid_hour_utc).tz_localize("UTC")
    position = pvlib.solarposition.get_solarposition(times, latitude, longitude, method="nrel_numpy")
    dni_extra = pvlib.irradiance.get_extra_radiation(times)

    if solar["mount"] == "single_axis":
        tracking = pvlib.tracking.singleaxis(
            position["apparent_zenith"],
            position["azimuth"],
            axis_tilt=0.0,
            axis_azimuth=0.0,
            max_angle=solar["tracker_max_angle_deg"],
            backtrack=True,
            gcr=solar["ground_coverage_ratio"],
        )
        tilt = tracking["surface_tilt"].fillna(0.0)
        azimuth = tracking["surface_azimuth"].fillna(0.0)
    elif solar["mount"] == "fixed":
        tilt = pd.Series(abs(latitude), index=times)
        azimuth = pd.Series(0.0 if latitude < 0 else 180.0, index=times)
    else:
        raise ValueError(f"Unknown solar mount {solar['mount']!r}")

    dni = pd.Series(weather["dni"].to_numpy(), index=times)
    ghi = pd.Series(weather["ghi"].to_numpy(), index=times)
    dhi = pd.Series(weather["dhi"].to_numpy(), index=times)
    poa = pvlib.irradiance.get_total_irradiance(
        tilt, azimuth, position["apparent_zenith"], position["azimuth"],
        dni, ghi, dhi, dni_extra=dni_extra, model="haydavies",
    )
    aoi = pvlib.irradiance.aoi(tilt, azimuth, position["apparent_zenith"], position["azimuth"])
    effective = (poa["poa_direct"].fillna(0.0) * pvlib.iam.physical(aoi).fillna(0.0) + poa["poa_diffuse"].fillna(0.0)).clip(lower=0.0)
    temp_air = pd.Series(weather["temp_c"].to_numpy(), index=times)
    wind = pd.Series(weather["wind_10m"].to_numpy(), index=times)
    cell_temp = pvlib.temperature.faiman(poa["poa_global"].fillna(0.0), temp_air, wind)
    dc = pvlib.pvsystem.pvwatts_dc(effective, cell_temp, pdc0=solar["dc_ac_ratio"], gamma_pdc=solar["gamma_pdc_per_c"])
    ac = (dc * (1.0 - solar["losses_fraction"]) * solar["inverter_efficiency"]).clip(lower=0.0, upper=1.0)
    return ac.to_numpy(), cell_temp.to_numpy()


def apply_measured_irradiance(weather: pd.DataFrame, site_id: str) -> tuple[pd.DataFrame, str]:
    """Override NASA irradiance with ground-measured data where a file exists.

    Expected file: <solar.measured_data_dir>/<site_id>.csv with columns
    timestamp (local standard time, hourly), ghi, dni, dhi in W/m2 (e.g. a
    BoM or Solcast export). Hours not covered keep NASA POWER values.
    """
    directory = ROOT / load_config()["solar"]["measured_data_dir"]
    path = directory / f"{site_id}.csv"
    if not path.exists():
        return weather, "NASA POWER"
    measured = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp")
    missing = {"ghi", "dni", "dhi"} - set(measured.columns)
    if missing:
        raise ValueError(f"{path.name} is missing columns {sorted(missing)}")
    indexed = weather.set_index("timestamp")
    overlap = indexed.index.intersection(measured.index)
    for column in ("ghi", "dni", "dhi"):
        indexed.loc[overlap, column] = measured.loc[overlap, column].clip(lower=0.0)
    share = len(overlap) / len(indexed)
    return indexed.reset_index(), f"Measured ({path.name}, {share:.0%} of hours)"


@lru_cache(maxsize=32)
def _load_site_weather(site_id: str, years: tuple[int, ...], irradiance_scale: float) -> pd.DataFrame:
    site = site_by_id(site_id)
    weather = read_weather_files(site_id, years)
    weather, source = apply_measured_irradiance(weather, site_id)
    for column in ("ghi", "dni", "dhi"):
        weather[column] = weather[column] * irradiance_scale
    ac, cell_temp = solar_ac_per_mw(weather, float(site["latitude"]), float(site["longitude"]))
    weather["solar_cf"] = ac
    weather["cell_temp_c"] = cell_temp
    weather.attrs["irradiance_source"] = source
    weather.attrs["irradiance_scale"] = irradiance_scale
    return weather


def load_site_weather(site_id: str, years, irradiance_scale: float | None = None) -> pd.DataFrame:
    """Hourly weather plus PV capacity factor. Returns a copy safe to modify.

    irradiance_scale multiplies GHI/DNI/DHI (bias-correction sensitivity);
    by default the configured solar.irradiance_scale is used.
    """
    scale = load_config()["solar"]["irradiance_scale"] if irradiance_scale is None else irradiance_scale
    weather = _load_site_weather(site_id, tuple(int(year) for year in years), float(scale))
    copy = weather.copy()
    copy.attrs = dict(weather.attrs)
    return copy
