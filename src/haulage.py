"""Carrier haulage (port -> consignee), priced as MAERSK carrier haulage from data/haulage/maersk.yaml.

The shipping line sets haulage and its fuel surcharge; Maersk reviews its inland fuel % every two
weeks, so the file carries the date it was last updated and staff are warned when it's stale.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from src.config import DATA_DIR

HAULAGE_FILE = DATA_DIR / "haulage" / "maersk.yaml"


@lru_cache(maxsize=1)
def load_haulage(path: Path = HAULAGE_FILE) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def carrier_haulage(country: str | None) -> dict | None:
    """Maersk haulage settings for a country, or None if we have none."""
    h = load_haulage()
    row = h["countries"].get(country or "")
    return {**row, "carrier": h["carrier"], "fuel_review_days": h["fuel_review_days"]} if row else None


def fuel_age_days(h: dict, today: date | None = None) -> int:
    updated = h["fuel_updated"]
    updated = updated if isinstance(updated, date) else date.fromisoformat(str(updated))
    return ((today or date.today()) - updated).days


def fuel_is_stale(h: dict, today: date | None = None) -> bool:
    return fuel_age_days(h, today) > h["fuel_review_days"]
