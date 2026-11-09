"""Loads config.yaml into a plain dict (cached)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
DATA_DIR = ROOT / "data"


@lru_cache(maxsize=1)
def load_config(path: Path | str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
