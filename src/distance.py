"""Nearest arrival ports by ROAD distance (OpenRouteService, lorry profile). No LLM.

Used when the email names no port of arrival: we suggest the nearest container ports to the delivery
address so staff can pick one (or several, for one quote per port). The port is never put into the
enquiry silently: staff choose, and the quote asks the sender to confirm it.

    1. Geocode the delivery address (postcode + place + country) with ORS geocoding.
    2. Pre-select candidate ports from data/ports/gateway_ports.csv by straight-line distance
       (internal only, never shown) - ports in other countries are allowed.
    3. Ask the ORS matrix service for real road distances (profile driving-hgv), keep the nearest N.

Needs ORS_API_KEY in .env (free key: https://openrouteservice.org/dev/#/signup).
Only the delivery postcode/place goes to ORS, never the customer's name or email.

    python -m src.distance FR 51300 "Vitry-le-Francois"
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import pandas as pd

from src.config import DATA_DIR, load_config

PORTS_FILE = DATA_DIR / "ports" / "gateway_ports.csv"
ORS_BASE = "https://api.openrouteservice.org"


class DistanceError(RuntimeError):
    """Could not work out road distances (no key, address not found, service error)."""


@dataclass(frozen=True)
class Port:
    locode: str
    name: str
    country: str
    lat: float
    lon: float


@dataclass(frozen=True)
class PortOption:
    port: Port
    road_km: float
    hours: float | None = None

    @property
    def locode(self) -> str:
        return self.port.locode


@dataclass(frozen=True)
class Located:
    lon: float
    lat: float
    label: str          # what the geocoder found - staff check it is the right place
    confidence: float | None = None


@lru_cache(maxsize=1)
def load_ports(path: Path = PORTS_FILE) -> tuple[Port, ...]:
    df = pd.read_csv(path)
    return tuple(Port(r.locode, r.name, r.country, float(r.lat), float(r.lon)) for r in df.itertuples())


def port_names() -> dict[str, str]:
    return {p.locode: p.name for p in load_ports()}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance. Only used to pick candidates before asking for road distances."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------- routing service ----------

class RouteService(Protocol):
    def geocode(self, country: str, postcode: str | None, place: str | None) -> Located: ...
    def road_matrix(self, origin: tuple[float, float], targets: list[tuple[float, float]]
                    ) -> list[tuple[float | None, float | None]]: ...


class OrsClient:
    """OpenRouteService over plain HTTPS (stdlib only). Coordinates are [lon, lat]."""

    def __init__(self, api_key: str | None = None, base_url: str = ORS_BASE, timeout: float = 20):
        if api_key is None:
            from dotenv import load_dotenv
            load_dotenv()
            api_key = os.getenv("ORS_API_KEY")
        if not api_key:
            raise DistanceError("ORS_API_KEY is not set in .env (free key: https://openrouteservice.org/dev/#/signup).")
        self.key, self.base, self.timeout = api_key, base_url.rstrip("/"), timeout

    def _call(self, req: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:300]
            raise DistanceError(f"OpenRouteService error {err.code}: {detail}") from err
        except urllib.error.URLError as err:
            raise DistanceError(f"Cannot reach OpenRouteService: {err.reason}") from err

    def geocode(self, country: str, postcode: str | None, place: str | None) -> Located:
        text = " ".join(filter(None, [postcode, place]))
        if not text:
            raise DistanceError("No delivery postcode or place to look up.")
        q = urllib.parse.urlencode({"text": text, "boundary.country": country, "size": 1})
        req = urllib.request.Request(f"{self.base}/geocode/search?{q}",
                                     headers={"Authorization": self.key, "Accept": "application/json"})
        feats = self._call(req).get("features") or []
        if not feats:
            raise DistanceError(f"Address not found: {text} ({country}).")
        f = feats[0]
        lon, lat = f["geometry"]["coordinates"][:2]
        props = f.get("properties", {})
        return Located(float(lon), float(lat), props.get("label", text), props.get("confidence"))

    def road_matrix(self, origin, targets):
        body = {"locations": [list(origin)] + [list(t) for t in targets], "sources": [0],
                "destinations": list(range(1, len(targets) + 1)),
                "metrics": ["distance", "duration"], "units": "km"}
        req = urllib.request.Request(f"{self.base}/v2/matrix/driving-hgv", data=json.dumps(body).encode("utf-8"),
                                     headers={"Authorization": self.key, "Content-Type": "application/json",
                                              "Accept": "application/json"}, method="POST")
        data = self._call(req)
        km = (data.get("distances") or [[None] * len(targets)])[0]
        secs = (data.get("durations") or [[None] * len(targets)])[0]
        return list(zip(km, secs))


def default_service() -> RouteService:
    return OrsClient()


# ---------- nearest ports ----------

def nearest_ports(country: str, postcode: str | None, place: str | None = None,
                  service: RouteService | None = None, show: int | None = None,
                  candidates: int | None = None) -> tuple[Located, list[PortOption]]:
    """Nearest container ports to the delivery address by ROAD distance (lorry), nearest first.
    Ports the road router can't reach (e.g. across the sea) are dropped."""
    cfg = load_config().get("nearest_ports", {})
    show = show or cfg.get("show", 3)
    candidates = max(candidates or cfg.get("candidates", 10), show)
    service = service or default_service()
    here = service.geocode(country, postcode, place)

    by_line = sorted(load_ports(), key=lambda p: haversine_km(here.lat, here.lon, p.lat, p.lon))[:candidates]
    results = service.road_matrix((here.lon, here.lat), [(p.lon, p.lat) for p in by_line])
    options = [PortOption(p, float(km), (s / 3600) if s is not None else None)
               for p, (km, s) in zip(by_line, results) if km is not None]
    if not options:
        raise DistanceError(f"No road route found from any port to {here.label}.")
    options.sort(key=lambda o: o.road_km)
    return here, options[:show]


def format_options(here: Located, options: list[PortOption], priced: dict[str, bool] | None = None) -> str:
    """Numbered list for staff, e.g. '1. Le Havre (FRLEH, FR)  412 km by road, ~5.6 h'."""
    priced = priced or {}
    out = [f"=== NEAREST PORTS BY ROAD (lorry) to: {here.label} ==="]
    for i, o in enumerate(options, 1):
        t = f", ~{o.hours:.1f} h" if o.hours is not None else ""
        flag = "" if priced.get(o.locode, True) else "   (no tariff for this port's country: manual quote)"
        out.append(f"  {i}. {o.port.name} ({o.locode}, {o.port.country})  {o.road_km:,.0f} km{t}{flag}")
    out.append("  Not in the email: the sender is asked to confirm the port.")
    return "\n".join(out)


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        print(__doc__)
        return
    country, postcode, place = argv[0].upper(), argv[1], (argv[2] if len(argv) > 2 else None)
    here, options = nearest_ports(country, postcode, place)
    print(format_options(here, options))


if __name__ == "__main__":
    main(sys.argv[1:])
