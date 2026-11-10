"""Country destination tariffs from data/tariffs/*.yaml (e.g. Lincoln Cargo's DE/FR tariff).

A tariff applies to every port in its countries. When a country has a tariff, quote.py prices from it
(in the tariff's currency, with its own margin); otherwise it falls back to the CSV rate sheets.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from src.config import DATA_DIR

TARIFF_DIR = DATA_DIR / "tariffs"


@lru_cache(maxsize=1)
def load_tariffs(folder: Path = TARIFF_DIR) -> dict[str, dict]:
    """country code -> tariff dict."""
    by_country: dict[str, dict] = {}
    for path in sorted(folder.glob("*.yaml")):
        t = yaml.safe_load(path.read_text(encoding="utf-8"))
        t["source_file"] = path.name
        for c in t["countries"]:
            by_country[c] = t
    return by_country


def tariff_for(country: str | None) -> dict | None:
    return load_tariffs().get(country or "")


def terms_for(tariff: dict, country: str, ddp: bool) -> list[str]:
    terms = list(tariff.get("terms", [])) + list(tariff.get("country_terms", {}).get(country, []))
    if ddp:
        terms += tariff.get("ddp_terms", [])
    return terms


def port_country(port: str | None) -> str | None:
    """UN/LOCODEs start with the country code: FRLEH -> FR."""
    return port[:2].upper() if port else None


def pricing_tariff(port: str | None, delivery_country: str | None) -> dict | None:
    """The tariff that prices destination handling: the ARRIVAL PORT's country (the port may be in another
    country than the delivery, e.g. Hamburg for a delivery in France). With no port yet, the delivery country's."""
    return tariff_for(port_country(port)) if port else tariff_for(delivery_country)
