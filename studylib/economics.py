"""Annualised cost, energy-price and emissions layers.

All values are real AUD. Network/connection charges, retail margins and
capacity-market effects are not modelled, so totals are indicative and best
used to rank designs and sites against each other.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from .config import PRICE_DIR, load_config


def capital_recovery_factor(rate: float, years: int) -> float:
    if rate == 0:
        return 1.0 / years
    growth = (1.0 + rate) ** years
    return rate * growth / (growth - 1.0)


def annualised_resource_cost(pv_mw: float, bess_power_mw: float, bess_energy_mwh: float) -> dict[str, float]:
    eco = load_config()["economics"]
    rate = eco["discount_rate"]
    pv_capex = pv_mw * 1000.0 * eco["pv_capex_per_kw"]
    bess_capex = bess_energy_mwh * 1000.0 * eco["bess_capex_per_kwh"] + bess_power_mw * 1000.0 * eco["bess_capex_per_kw"]
    pv_annual = pv_capex * capital_recovery_factor(rate, eco["pv_life_years"]) + pv_mw * 1000.0 * eco["pv_fixed_om_per_kw_year"]
    bess_annual = bess_capex * capital_recovery_factor(rate, eco["bess_life_years"]) + bess_capex * eco["bess_fixed_om_fraction_of_capex"]
    return {
        "PvCapexAUDm": pv_capex / 1e6,
        "BessCapexAUDm": bess_capex / 1e6,
        "AnnualisedResourceCostAUDm": (pv_annual + bess_annual) / 1e6,
    }


@lru_cache(maxsize=4)
def _hourly_prices(region: str) -> pd.DataFrame:
    path = PRICE_DIR / f"{region}_hourly.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing. Run download_aemo_prices.py first.")
    prices = pd.read_csv(path, parse_dates=["timestamp"])
    return prices.set_index("timestamp")


def hourly_price(timestamps: pd.Series, grid: str, price_region: str) -> np.ndarray:
    """Hourly energy price aligned to the weather timestamps.

    NEM prices are in AEST. For the Queensland sites local solar time differs
    from AEST by under five minutes, so timestamps are matched by hour. The
    NWIS has no public spot market and uses a flat configured price.
    """
    eco = load_config()["economics"]
    if grid != "NEM":
        return np.full(len(timestamps), float(eco["flat_energy_price_per_mwh"][grid]))
    prices = _hourly_prices(price_region)["rrp_per_mwh"]
    aligned = prices.reindex(pd.DatetimeIndex(timestamps)).to_numpy()
    if np.isnan(aligned).mean() > 0.01:
        raise RuntimeError(f"More than 1% of hours have no {price_region} price; check price_years against weather_years")
    return pd.Series(aligned).interpolate(limit_direction="both").to_numpy()


def regional_demand(region: str) -> pd.Series:
    return _hourly_prices(region)["demand_mw"]


def energy_and_emissions(grid_import: np.ndarray, price: np.ndarray, grid_key: str, years: int) -> dict[str, float]:
    factor = load_config()["emissions"]["grid_factor_kg_per_kwh"][grid_key]
    annual_import = float(grid_import.sum()) / years
    annual_cost = float((grid_import * price).sum()) / years
    return {
        "AnnualGridImportGWh": annual_import / 1000.0,
        "AnnualEnergyCostAUDm": annual_cost / 1e6,
        "ImportWeightedPriceAUDperMWh": annual_cost / annual_import if annual_import > 0 else 0.0,
        "AnnualScope2EmissionsKt": annual_import * factor / 1000.0,
    }


def network_charges(grid_import: np.ndarray, timestamps: pd.Series, site_id: str, years: int) -> dict[str, float]:
    """Annual Powerlink transmission charges for a site (zero where no tariff is published).

    Locational price ($/kW/month) is applied to each month's peak import;
    non-locational and common-service charges use the energy-based prices.
    """
    tariff = load_config()["economics"]["network_tariff"]
    locational = tariff["locational_price_per_kw_month"].get(site_id)
    if locational is None:
        return {"LocationalDemandChargeAUDm": 0.0, "EnergyBasedNetworkChargeAUDm": 0.0,
                "NetworkChargesAUDm": 0.0, "NetworkTariffApplied": False}
    gst = 1.1 if tariff["gst_inclusive"] else 1.0
    monthly_peak_mw = pd.Series(grid_import, index=pd.DatetimeIndex(timestamps)).resample("MS").max()
    demand_charge = float(monthly_peak_mw.sum()) * 1000.0 * locational / gst / years
    energy_c_per_kwh = tariff["non_locational_energy_c_per_kwh"] + tariff["common_service_energy_c_per_kwh"]
    energy_charge = float(grid_import.sum()) * 1000.0 * energy_c_per_kwh / 100.0 / gst / years
    return {
        "LocationalDemandChargeAUDm": demand_charge / 1e6,
        "EnergyBasedNetworkChargeAUDm": energy_charge / 1e6,
        "NetworkChargesAUDm": (demand_charge + energy_charge) / 1e6,
        "NetworkTariffApplied": True,
    }


def ppa_adjusted_energy_cost(grid_import: np.ndarray, price: np.ndarray, fraction: float, years: int) -> float:
    """Annual energy cost (AUD m) when `fraction` of grid energy is bought under a flat-price PPA."""
    ppa_price = load_config()["economics"]["ppa"]["price_per_mwh"]
    spot = float((grid_import * price).sum())
    hedged = (1.0 - fraction) * spot + fraction * float(grid_import.sum()) * ppa_price
    return hedged / years / 1e6


@lru_cache(maxsize=4)
def _grid_profile(region: str):
    """Month x hour x day-type mean intensity and zero-emission share for a region."""
    from .config import INPUTS

    path = INPUTS / "grid" / f"{region}_hourly_emissions.csv"
    if not path.exists():
        return None
    frame = pd.read_csv(path, parse_dates=["timestamp"])
    frame["month"] = frame["timestamp"].dt.month
    frame["hour"] = frame["timestamp"].dt.hour
    frame["weekend"] = frame["timestamp"].dt.dayofweek >= 5
    return frame.groupby(["month", "hour", "weekend"])[["intensity_t_per_mwh", "zero_emission_share"]].mean()


def hourly_grid_intensity(timestamps: pd.Series, grid_key: str) -> tuple[np.ndarray, np.ndarray]:
    """Hourly grid CO2-e intensity (t/MWh) and zero-emission share for the timestamps.

    Uses the recent-year month x hour x day-type profile where AEMO data exist;
    otherwise falls back to the annual factor and an unknown (NaN) clean share.
    """
    profile = _grid_profile(grid_key)
    if profile is None:
        factor = load_config()["emissions"]["grid_factor_kg_per_kwh"][grid_key]
        return np.full(len(timestamps), float(factor)), np.full(len(timestamps), np.nan)
    stamps = pd.DatetimeIndex(timestamps)
    keys = pd.MultiIndex.from_arrays([stamps.month, stamps.hour, stamps.dayofweek >= 5])
    looked_up = profile.reindex(keys)
    return looked_up["intensity_t_per_mwh"].to_numpy(), looked_up["zero_emission_share"].to_numpy()


def time_matched_metrics(result, demand: np.ndarray, timestamps: pd.Series, grid_key: str, years: int) -> dict[str, float]:
    """Hourly-matched emissions and 24/7 carbon-free-energy (CFE) score.

    CFE = clean energy serving the load in the same hour / total load, where
    clean = on-site PV (direct or via the battery) + the grid's zero-emission
    share of imported energy (battery energy charged from the grid carries the
    clean share of its charging hours).
    """
    intensity, clean_share = hourly_grid_intensity(timestamps, grid_key)
    emissions = float((result.grid * intensity).sum()) / years / 1000.0
    charged = result.pv_charge.sum() + result.grid_charge.sum()
    pv_fraction = result.pv_charge.sum() / charged if charged > 0 else 0.0
    if np.isnan(clean_share).all():
        return {"HourlyMatchedEmissionsKt": emissions, "CfeScore": np.nan}
    grid_charge_clean = (float((result.grid_charge * clean_share).sum()) / result.grid_charge.sum()
                         if result.grid_charge.sum() > 0 else 0.0)
    clean = (result.pv_to_load + result.discharge * pv_fraction
             + (result.grid - result.grid_charge) * clean_share
             + result.discharge * (1.0 - pv_fraction) * grid_charge_clean)
    return {"HourlyMatchedEmissionsKt": emissions, "CfeScore": float(clean.sum() / demand.sum())}
