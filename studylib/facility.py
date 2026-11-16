"""Hourly AI data-centre load model.

The hourly profile is a planning abstraction. Real training clusters swing by
tens of percent within seconds (synchronous compute/communication phases,
checkpointing); that behaviour is screened separately as a voltage-step
check in the network studies, not here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import load_config


PROFILES = ("training", "inference", "mixed")


def _it_profile(weather: pd.DataFrame, utilisation: float, amplitude: float, phase_hour: float) -> np.ndarray:
    facility = load_config()["facility"]
    hour = weather["timestamp"].dt.hour.to_numpy()
    weekday = weather["timestamp"].dt.dayofweek.to_numpy()
    daily = np.clip(1.0 - amplitude + amplitude * np.sin((hour - phase_hour) / 24.0 * 2.0 * np.pi), 0.35, 1.15)
    weekly = np.where(weekday >= 5, facility["weekend_multiplier"], 1.0)
    return facility["it_capacity_mw"] * utilisation * daily * weekly


def facility_load(weather: pd.DataFrame, profile: str) -> dict[str, np.ndarray]:
    """IT load, PUE and facility (grid-side) load for a workload profile.

    "mixed" splits the IT capacity between training and inference in the
    configured proportion (facility.mixed_training_share).
    """
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {PROFILES}")
    facility = load_config()["facility"]
    training = _it_profile(weather, facility["training_utilisation"], facility["training_daily_amplitude"], 9.0)
    inference = _it_profile(weather, facility["inference_utilisation"], facility["inference_daily_amplitude"], 15.0)
    if profile == "training":
        it_mw = training
    elif profile == "inference":
        it_mw = inference
    else:
        share = facility["mixed_training_share"]
        it_mw = share * training + (1.0 - share) * inference
    pue = facility["base_pue"] + np.maximum(
        weather["temp_c"].to_numpy() - facility["pue_temperature_threshold_c"], 0.0
    ) * facility["pue_increase_per_c"]
    return {"it_mw": it_mw, "pue": pue, "facility_mw": it_mw * pue}


def cooling_water_litres(weather: pd.DataFrame, load: dict[str, np.ndarray]) -> np.ndarray:
    """Hourly cooling-water use (litres) from a heat-rejection water balance.

    All facility power ends up as heat. The evaporative share rises linearly
    from 0 at dry_cooling_limit_c to 1 at full_evaporative_c (dry coolers or
    economisers handle the rest). Evaporated water = heat / latent heat;
    blowdown = evaporation / (cycles - 1); drift is a small fraction.
    """
    water = load_config()["facility"]["water"]
    temperature = weather["temp_c"].to_numpy()
    span = water["full_evaporative_c"] - water["dry_cooling_limit_c"]
    evaporative_share = np.clip((temperature - water["dry_cooling_limit_c"]) / span, 0.0, 1.0)
    heat_mj = load["facility_mw"] * 3600.0  # MWh -> MJ per hour
    evaporation_kg = heat_mj * evaporative_share / water["latent_heat_mj_per_kg"]
    blowdown_kg = evaporation_kg / (water["cycles_of_concentration"] - 1.0)
    drift_kg = evaporation_kg * water["drift_fraction"]
    return evaporation_kg + blowdown_kg + drift_kg  # 1 kg of water = 1 litre


def climate_metrics(weather: pd.DataFrame, load: dict[str, np.ndarray]) -> dict[str, float]:
    """Cooling-climate and water indicators for site comparison."""
    facility = load_config()["facility"]
    years = weather["timestamp"].dt.year.nunique()
    it_energy_mwh = float(load["it_mw"].sum())
    water_litres = float(cooling_water_litres(weather, load).sum())
    return {
        "MeanTemperatureC": float(weather["temp_c"].mean()),
        "P99TemperatureC": float(weather["temp_c"].quantile(0.99)),
        "MeanWetBulbC": float(weather["wet_bulb_c"].mean()),
        "HoursPerYearWetBulbAboveLimit": float((weather["wet_bulb_c"] > facility["wet_bulb_evaporative_limit_c"]).sum() / years),
        "AnnualMeanPUE": float(load["facility_mw"].sum() / it_energy_mwh),
        "PeakPUE": float(load["pue"].max()),
        "PeakFacilityDemandMW": float(load["facility_mw"].max()),
        "AnnualFacilityEnergyGWh": float(load["facility_mw"].sum() / years / 1000.0),
        "AnnualWaterUseML": water_litres / years / 1e6,
        "WUELitresPerKWhIT": water_litres / (it_energy_mwh * 1000.0),
    }
