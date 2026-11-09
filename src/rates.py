"""Loads the rate sheets and answers lookup questions.

Phase 3 only needs "is this lane / port in the sheet?". Phase 4 adds the price lookups.
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
        read = lambda name: pd.read_csv(data_dir / name, dtype={"zone": str})  # noqa: E731
        self.sea = read("rates_sea_import.csv")
        self.origin = read("origin_charges.csv")
        self.destination = read("uk_destination_charges.csv")
        self.delivery = read("uk_delivery_rates.csv")
        aliases = pd.read_csv(data_dir / "port_aliases.csv", dtype=str)
        self.port_aliases = {a.strip().lower(): c.strip().upper() for a, c in zip(aliases["alias"], aliases["locode"])}

    # ---------- ports ----------
    def port_code(self, name: str | None) -> str | None:
        """'Ningbo' -> 'CNNGB'. Accepts a LOCODE directly. Unknown -> None."""
        if not name:
            return None
        key = name.strip().lower().removesuffix(" port").removeprefix("port of ").strip()
        if key.upper() in set(self.port_aliases.values()):
            return key.upper()
        return self.port_aliases.get(key)

    def uk_ports(self) -> set[str]:
        """UK ports we can price (they appear in the destination or delivery sheets)."""
        ports = set(self.destination["uk_port"]) | set(self.delivery["uk_port"])
        return {p for p in ports if p != "ALL"}

    def origin_ports(self) -> set[str]:
        return set(self.sea["origin_port"])

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

    def destinations_for(self, origin: str, service: Service, container_types: list[str] | None = None) -> list[str]:
        """UK ports that have a rate from this origin for this service."""
        dests = sorted(set(self.sea.loc[self.sea["origin_port"] == origin, "destination_port"]))
        return [d for d in dests if self.has_lane(origin, d, service, container_types)]

    def has_origin_charges(self, origin: str, service: Service) -> bool:
        svc = "LCL" if service is Service.LCL else "FCL"
        return bool(((self.origin["origin_port"] == origin) & (self.origin["service"] == svc)).any())


@lru_cache(maxsize=1)
def load_rate_book() -> RateBook:
    return RateBook()
