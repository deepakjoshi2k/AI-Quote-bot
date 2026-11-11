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


# ---------- staff look-up step (Maersk inland price look-up on maersk.com) ----------

from dataclasses import dataclass, field  # noqa: E402

MAERSK_CONTAINER = {"20GP": "20DRY", "40GP": "40DRY", "40HC": "40HDRY"}
COUNTRY_NAMES = {"FR": "France", "DE": "Germany"}


@dataclass
class HaulageCheck:
    """What staff read off Maersk's inland price look-up for this shipment (per container, in EUR, COST)."""
    haulage_per_container: float
    fuel_per_container: float                                        # fuel surcharge AMOUNT, not %
    extras: list[tuple[str, float]] = field(default_factory=list)   # other inland charges per container
    heavy_surcharge: float | None = None   # None = our tariff's 200 if heavy; 0 = none (our fuel % is added on top)
    checked_on: date = field(default_factory=date.today)
    source: str = "Maersk inland price look-up"


def needs_lookup(e) -> bool:
    """Delivery into a country priced with Maersk carrier haulage."""
    from src.scope import quote_scope
    return quote_scope(e).delivery and carrier_haulage(e.destination_country) is not None


def lookup_instructions(e, today: date | None = None, port_note: str | None = None) -> str:
    """Exactly what to type into Maersk's inland price look-up, filled from the enquiry.
    port_note: set when staff picked the port (not in the email), e.g. "nearest by road, 412 km"."""
    from src.quote import PORT_NAMES
    today = today or date.today()
    n = sum(c.quantity for c in e.containers)
    na = "NOT IN EMAIL"
    country = COUNTRY_NAMES.get(e.destination_country, e.destination_country)
    place = ", ".join(filter(None, [e.delivery_place, e.delivery_postcode])) or na
    if e.destination_port:
        port = f"{PORT_NAMES.get(e.destination_port, e.destination_port)} ({e.destination_port})"
        if port_note:
            port += f" – NOT IN EMAIL: {port_note}"
        if e.destination_port[:2] != e.destination_country:
            port += " [port in another country: check Maersk lists it for this inland location]"
    else:
        port = (f"{na} – choose the port Lincoln Cargo will use (e.g. "
                + (", ".join(f"{PORT_NAMES[p]} {p}" for p in ['FRLEH', 'FRFOS']) if e.destination_country == "FR"
                   else ", ".join(f"{PORT_NAMES[p]} {p}" for p in ['DEHAM', 'DEBRV']))
                + ") and ask the sender to confirm")
    containers = ", ".join(f"{c.quantity} x {MAERSK_CONTAINER.get(c.container_type, c.container_type)}"
                           f" (email: {c.container_type})" for c in e.containers) or na
    if e.total_gross_weight_kg and n:
        weight = f"{e.total_gross_weight_kg / n:,.0f} kg gross per container ({e.total_gross_weight_kg:,.0f} kg total)"
    else:
        weight = f"{na} – Maersk prices by weight band; ask the sender"
    h = carrier_haulage(e.destination_country)
    rows = [
        ("1. Log in", "maersk.com → Prices → Inland price lookup (INLAND TRANSPORT PRICE)"),
        ("2. Direction", "Import"),
        ("3. Country", f"{country} ({e.destination_country})"),
        ("4. Inland location", place),
        ("5. Container yard?", "Leave UNTICKED (door delivery to consignee)"),
        ("6. Gateway port", port),
        ("7. Container type", containers),
        ("8. Cargo weight", weight),
        ("9. Haulage date", f"Expected delivery date if known; otherwise today ({today:%d %b %Y}) and note it"),
        ("10. Service contract", "Lincoln Cargo's Maersk contract number, if they have one (else leave blank = tariff)"),
        ("11. Transport mode", "Truck / direct trucking (unless the customer wants rail or barge)"),
    ]
    w = max(len(k) for k, _ in rows)
    from src.report import section
    out = [section("MAERSK INLAND PRICE LOOK-UP (staff)")]
    out += [f"  {k:<{w}}  {v}" for k, v in rows]
    out += ["",
            "  From the result page, note PER CONTAINER (EUR):",
            "   a) Inland haulage import (IHI) – the haulage price",
            "   b) Import fuel surcharge (IFS) – the AMOUNT in EUR per container (not the %)",
            "   c) Any other mandatory inland charges listed (name + amount)",
            f"   d) If over {h['heavy_over_kg'] / 1000:g} t: the heavy/weight surcharge (or 0 if the haulage price already covers the weight band)" if h else "",
            "",
            "  Then enter them below (or re-run with --haulage / --fuel / --extra / --heavy)."]
    return "\n".join(x for x in out if x is not None)
