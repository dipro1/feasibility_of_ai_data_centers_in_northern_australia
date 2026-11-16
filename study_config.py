"""Shared configuration loader for the thesis simulations."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "inputs" / "study_config.json"


def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as stream:
        return json.load(stream)
