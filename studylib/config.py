"""Paths, configuration and candidate-site loading."""

from __future__ import annotations

import csv
import json
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "inputs"
RESULTS = ROOT / "results"
WEATHER_DIR = INPUTS / "weather"
PRICE_DIR = INPUTS / "prices"
CONFIG_PATH = INPUTS / "study_config.json"
SITES_PATH = INPUTS / "candidate_sites.csv"
SITE_CASES_PATH = INPUTS / "site_cases.csv"


@lru_cache(maxsize=1)
def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as stream:
        config = json.load(stream)
    validate_config(config)
    return config


def validate_config(config: dict) -> None:
    """Fail early with a readable message instead of deep inside a model."""
    criteria = config["network_criteria"]
    for pf in criteria["power_factor_sensitivity"]:
        if not 0 < pf <= 1:
            raise ValueError(f"power_factor_sensitivity contains invalid PF {pf}")
    if not 0 < criteria["minimum_voltage_pu"] < 1.1:
        raise ValueError("minimum_voltage_pu looks wrong")
    bess = config["renewable_bess"]
    if not 0 < bess["round_trip_efficiency"] <= 1:
        raise ValueError("round_trip_efficiency must be in (0, 1]")
    if not 0 <= bess["minimum_soc_fraction"] <= bess["initial_soc_fraction"] <= 1:
        raise ValueError("SOC fractions must satisfy 0 <= minimum <= initial <= 1")
    if bess["selected_trace_limit_mw"] not in bess["grid_limits_mw"]:
        raise ValueError("selected_trace_limit_mw must be one of grid_limits_mw")
    weights = config["scoring"]["weights"]
    if abs(sum(weights.values()) - 1.0) > 1e-6:
        raise ValueError("scoring weights must sum to 1")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def load_sites() -> list[dict[str, str]]:
    return read_csv_rows(SITES_PATH)


def site_by_id(site_id: str) -> dict[str, str]:
    for site in load_sites():
        if site["site_id"] == site_id:
            return site
    raise KeyError(f"Unknown site_id {site_id!r} in {SITES_PATH.name}")


def ner_minimum_pf(voltage_kv: float) -> float:
    """Voltage-banded minimum load power factor used as the base case."""
    bands = sorted(
        load_config()["network_criteria"]["ner_minimum_load_pf"],
        key=lambda band: band["above_kv"],
        reverse=True,
    )
    for band in bands:
        if voltage_kv > band["above_kv"]:
            return float(band["pf"])
    return float(bands[-1]["pf"])
