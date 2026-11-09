"""Postcode -> delivery zone.

UK: "1", "2", "3" or "MANUAL" from data/uk_postcode_zones.csv (by postcode area letters).
EU: "1", "2" or "MANUAL" from data/eu_postcode_zones.csv (by country + postcode prefix).
"""
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


# ---------- EU ----------

def clean_eu_postcode(raw: str | None, country: str) -> str | None:
    """Uppercase, no spaces/hyphens, country prefix removed ('LV-1050' -> '1050', 'F-51300' -> '51300')."""
    if not raw:
        return None
    s = re.sub(r"[\s\-]", "", raw.upper())
    for prefix in (country, country[:1]):
        if s.startswith(prefix) and len(s) > len(prefix) and s[len(prefix)].isdigit():
            s = s[len(prefix):]
            break
    return s or None


@lru_cache(maxsize=4)
def _eu_zone_rules(path: Path = DATA_DIR / "eu_postcode_zones.csv") -> dict[str, list[tuple[str, str]]]:
    """country -> [(prefix, zone)] sorted longest prefix first; '*' = default zone."""
    df = pd.read_csv(path, dtype=str)
    rules: dict[str, list[tuple[str, str]]] = {}
    for _, row in df.iterrows():
        for prefix in row["postcode_prefixes"].split(";"):
            rules.setdefault(row["country"], []).append((prefix.strip().upper(), row["zone"].strip().upper()))
    for country in rules:
        rules[country].sort(key=lambda r: (r[0] == "*", -len(r[0])))
    return rules


def eu_zone(country: str, postcode: str | None) -> str | None:
    """Zone for an EU delivery. None if the country isn't in the sheet."""
    rules = _eu_zone_rules().get(country)
    if not rules:
        return None
    pc = clean_eu_postcode(postcode, country) or ""
    for prefix, zone in rules:
        if prefix == "*" or (pc and pc.startswith(prefix)):
            return zone
    return None


def zone_for_address(country: str | None, postcode: str | None) -> str | None:
    """UK or EU zone for a delivery address. None when it can't be worked out."""
    if country in (None, "GB"):
        pc = normalise_postcode(postcode)
        return zone_for_postcode(pc) if pc else None
    return eu_zone(country, postcode)
