"""Human-readable view of an extracted enquiry + triage. Empty fields show as "Not available"."""
from __future__ import annotations

from src.models import Enquiry, Triage

NA = "Not available"


def _v(value) -> str:
    if value is None or value == "" or value == []:
        return NA
    if hasattr(value, "value"):
        return NA if value.value in ("unknown", "UNKNOWN") else value.value
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    return str(value)


def enquiry_rows(e: Enquiry) -> list[tuple[str, str]]:
    containers = ", ".join(f"{c.quantity} x {c.container_type}" for c in e.containers) or None
    cartons = f"{sum(c.quantity for c in e.cartons)} cartons" if e.cartons else None
    return [
        ("Sender", _v(e.customer_name)),
        ("Sender's role", _v(e.customer_role)),
        ("Direction (from countries)", _v(e.direction)),
        ("Mode", _v(e.mode)),
        ("Service", _v(e.service)),
        ("Origin country", _v(e.origin_country)),
        ("Supplier / origin place", _v(e.origin_place)),
        ("Port of loading", _v(e.origin_port or e.origin_port_name)),
        ("Destination country", _v(e.destination_country)),
        ("Port of arrival", _v(e.destination_port)),
        ("Containers", _v(containers)),
        ("Cartons", _v(cartons)),
        ("Volume (CBM)", _v(e.total_volume_cbm)),
        ("Gross weight (kg)", _v(e.total_gross_weight_kg)),
        ("Pallets", _v(e.pallets)),
        ("Commodity", _v(e.commodity)),
        ("Hazardous", _v(e.hazardous) + (f" ({e.un_number})" if e.un_number else "")),
        ("Incoterm", _v(e.incoterm) + (f" {e.incoterm_place}" if e.incoterm_place else "")),
        ("Delivery by us", _v(e.delivery_required)),
        ("Delivery address", _v(", ".join(filter(None, [e.delivery_place, e.delivery_postcode])))),
        ("Cargo ready date", _v(e.ready_date)),
    ]


def format_result(e: Enquiry, t: Triage) -> str:
    width = max(len(k) for k, _ in enquiry_rows(e))
    lines = ["=== SHIPMENT DETAILS ==="]
    lines += [f"  {k:<{width}}  {v}" for k, v in enquiry_rows(e)]
    lines += ["", f"=== DECISION: {t.status.upper()} ==="]
    lines += [f"  - {r}" for r in t.reasons] or ["  (no warnings)"]
    if t.missing_fields:
        lines += ["", "=== ASK THE SENDER ==="]
        lines += [f"  {i}. {m[0].upper() + m[1:]}" for i, m in enumerate(t.missing_fields, 1)]
    if e.extraction_notes:
        lines += ["", "=== NOTES ==="] + [f"  - {n}" for n in e.extraction_notes]
    return "\n".join(lines)
