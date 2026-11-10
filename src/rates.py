"""Loads the rate sheets and answers lookup questions.

UK sheets:  uk_destination_charges.csv, uk_delivery_rates.csv, uk_postcode_zones.csv
EU sheets:  eu_destination_charges.csv, eu_delivery_rates.csv, eu_postcode_zones.csv, eu_country_ports.csv
Shared:     rates_sea_import.csv (ocean, into UK and EU ports), origin_charges.csv, port_aliases.csv

Phase 3 needs "is this lane / port / country in the sheet?". Phase 4 adds the price lookups.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

from src.config import DATA_DIR
from src.models import Service


def service_key(service: Service, container_type: str | None = None) -> str:
    """Key used in rates_sea_import.csv: 'LCL' or 'FCL-40HC'."""
    if service is Service.LCL:
        return "LCL"
    if service is Service.FCL and container_type:
        return f"FCL-{container_type}"
    raise ValueError(f"Cannot build a rate key for {service} / {container_type}")


class RateBook:
    def __init__(self, data_dir: Path = DATA_DIR):
        def read(name: str) -> pd.DataFrame:
            return pd.read_csv(data_dir / name, dtype={"zone": str}, keep_default_na=False)

        self.sea = read("rates_sea_import.csv")
        self.origin = read("origin_charges.csv")
        self.uk_destination = read("uk_destination_charges.csv")
        self.uk_delivery = read("uk_delivery_rates.csv")
        self.eu_destination = read("eu_destination_charges.csv")
        self.eu_delivery = read("eu_delivery_rates.csv")
        self.eu_country_ports = read("eu_country_ports.csv")
        aliases = read("port_aliases.csv")
        self.port_aliases = {" ".join(a.strip().lower().replace("-", " ").split()): c.strip().upper()
                             for a, c in zip(aliases["alias"], aliases["locode"])}

    # ---------- ports & countries ----------
    def port_code(self, name: str | None) -> str | None:
        """'Ningbo' -> 'CNNGB'. Accepts a LOCODE directly. Unknown -> None."""
        if not name:
            return None
        key = " ".join(name.strip().lower().replace("-", " ").replace("_", " ").split())
        key = key.removesuffix(" port").removeprefix("port of ").removeprefix("the ").strip()
        if key.upper() in set(self.port_aliases.values()):
            return key.upper()
        return self.port_aliases.get(key)

    def uk_destination_ports(self) -> set[str]:
        ports = set(self.uk_destination["uk_port"]) | set(self.uk_delivery["uk_port"])
        return {p for p in ports if p != "ALL"}

    def eu_destination_ports(self) -> set[str]:
        return {p for p in self.eu_destination["port"] if p != "ALL"}

    def destination_ports(self) -> set[str]:
        """Every UK or EU port we can price destination charges for."""
        return self.uk_destination_ports() | self.eu_destination_ports()

    def origin_ports(self) -> set[str]:
        return set(self.sea["origin_port"])

    def priced_countries(self) -> set[str]:
        """Destination countries v1 can price: GB plus the EU countries in eu_country_ports.csv."""
        return {"GB"} | set(self.eu_country_ports["country"])

    def default_port_for_country(self, country: str | None) -> tuple[str | None, str]:
        """EU country -> (default port, note). GB has no single default (chosen by lane)."""
        row = self.eu_country_ports[self.eu_country_ports["country"] == (country or "")]
        if row.empty:
            return None, ""
        return row.iloc[0]["default_port"], row.iloc[0]["note"]

    # ---------- lanes ----------
    def _keys(self, service: Service, container_types: list[str]) -> list[str]:
        if service is Service.LCL:
            return ["LCL"]
        return [service_key(service, t) for t in container_types]

    def has_lane(self, origin: str, dest: str, service: Service, container_types: list[str] | None = None) -> bool:
        """True if every needed ocean rate exists (one per container type for FCL)."""
        keys = self._keys(service, container_types or [])
        if not keys:
            return False
        lane = self.sea[(self.sea["origin_port"] == origin) & (self.sea["destination_port"] == dest)]
        return all(k in set(lane["service"]) for k in keys)

    def destinations_for(self, origin: str, service: Service, container_types: list[str] | None = None,
                         country: str = "GB") -> list[str]:
        """Ports in `country` that have a rate from this origin for this service."""
        dests = sorted(set(self.sea.loc[self.sea["origin_port"] == origin, "destination_port"]))
        return [d for d in dests if d.startswith(country) and self.has_lane(origin, d, service, container_types)]

    def has_origin_charges(self, origin: str, service: Service) -> bool:
        svc = "LCL" if service is Service.LCL else "FCL"
        return bool(((self.origin["origin_port"] == origin) & (self.origin["service"] == svc)).any())

    def has_eu_delivery(self, port: str, country: str, zone: str, service: Service) -> bool:
        svc = "LCL" if service is Service.LCL else "FCL"
        d = self.eu_delivery
        return bool(((d["port"] == port) & (d["country"] == country) & (d["zone"] == zone)
                     & (d["service"] == svc)).any())


@lru_cache(maxsize=1)
def load_rate_book() -> RateBook:
    return RateBook()


# ---------- price lookups (Phase 4) ----------

class RateNotFound(LookupError):
    """A rate the quote needs is missing from the sheets (triage should have caught this)."""


def _one(df: pd.DataFrame, what: str) -> pd.Series:
    if df.empty:
        raise RateNotFound(f"No rate for {what}")
    return df.iloc[0]


def _dest_sheet(rb: RateBook, country: str | None) -> tuple[pd.DataFrame, str]:
    """UK sheet uses column 'uk_port', EU sheet uses 'port'."""
    return (rb.uk_destination, "uk_port") if country in (None, "GB") else (rb.eu_destination, "port")


def destination_charge(rb: RateBook, country: str | None, port: str, code: str, service: str = "FCL") -> pd.Series:
    """A destination charge row (DTHC, DO, IMPCLR...). Port-specific rows win over 'ALL'."""
    df, col = _dest_sheet(rb, country)
    rows = df[(df["code"] == code) & (df["service"].isin([service, "ALL"]))]
    specific = rows[rows[col] == port]
    return _one(specific if not specific.empty else rows[rows[col] == "ALL"], f"{code} at {port}")


def delivery_rate(rb: RateBook, country: str | None, port: str, zone: str, service: str = "FCL") -> pd.Series:
    """Delivery row with first_unit / additional_unit for this port and zone."""
    if country in (None, "GB"):
        d = rb.uk_delivery
        rows = d[(d["service"] == service) & (d["uk_port"].isin([port, "ALL"])) & (d["zone"] == zone)]
    else:
        d = rb.eu_delivery
        rows = d[(d["service"] == service) & (d["port"] == port) & (d["country"] == country) & (d["zone"] == zone)]
    return _one(rows, f"delivery {port} -> {country or 'GB'} zone {zone}")
