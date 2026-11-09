"""UK postcode -> delivery zone ("1", "2", "3" or "MANUAL"), from data/uk_postcode_zones.csv."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from src.config import DATA_DIR

# Full UK postcode: outward code (area letters + district) + inward code (digit + 2 letters).
_POSTCODE_RE = re.compile(r"^([A-Z]{1,2})(\d[A-Z\d]?)\s*(\d[A-Z]{2})$")
# Outward code only, e.g. "SL1" (customers sometimes give just this).
_OUTWARD_RE = re.compile(r"^([A-Z]{1,2})(\d[A-Z\d]?)$")

MANUAL = "MANUAL"


def normalise_postcode(raw: str | None) -> str | None:
    """'sl14qp' -> 'SL1 4QP'; outward-only 'sl1' -> 'SL1'; invalid -> None."""
    if not raw:
        return None
    s = re.sub(r"\s+", "", raw.upper())
    m = _POSTCODE_RE.match(s)
    if m:
        return f"{m.group(1)}{m.group(2)} {m.group(3)}"
    m = _OUTWARD_RE.match(s)
    if m:
        return s
    return None


def postcode_area(postcode: str) -> str:
    """Letters at the start: 'SL1 4QP' -> 'SL', 'B6 7JH' -> 'B'."""
    pc = normalise_postcode(postcode)
    if pc is None:
        raise ValueError(f"Not a UK postcode: {postcode!r}")
    return re.match(r"[A-Z]+", pc).group(0)


@lru_cache(maxsize=4)
def _area_to_zone(path: Path = DATA_DIR / "uk_postcode_zones.csv") -> dict[str, str]:
    df = pd.read_csv(path, dtype=str)
    mapping: dict[str, str] = {}
    for _, row in df.iterrows():
        areas = row["postcode_areas"]
        if areas.upper().startswith("ALL OTHER"):
            continue
        for area in areas.split(";"):
            mapping[area.strip().upper()] = row["zone"].strip().upper()
    return mapping


def zone_for_postcode(postcode: str) -> str:
    """Zone from the postcode area; areas not listed are zone '3' (rest of mainland GB)."""
    return _area_to_zone().get(postcode_area(postcode), "3")
