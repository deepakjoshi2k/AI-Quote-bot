"""Human-readable view of an import enquiry + triage.

"Not available" = needed but not in the email (asked of the sender if it matters).
"Not relevant"  = not used for Lincoln Cargo's import quote (destination services, charged to the sender).
"""
from __future__ import annotations

from src.models import Enquiry, Quote, Service, Triage
from src.scope import describe_scope, quote_scope

NA = "Not available"
NR = "Not relevant"


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
    fcl = e.service is Service.FCL
    containers = ", ".join(f"{c.quantity} x {c.container_type}" for c in e.containers) or None
    cartons = f"{sum(c.quantity for c in e.cartons)} cartons" if e.cartons else None
    if e.hazardous is None and not e.un_number:
        hazardous = "No (not stated)"
    else:
        hazardous = _v(e.hazardous) + (f" ({e.un_number})" if e.un_number else "")
    clearance = ("yes (customs clearance only)" if e.clearance_only
                 else _v(e.customs_clearance_requested) if e.customs_clearance_requested is not None
                 else "not asked")
    return [
        ("Sender", _v(e.customer_name)),
        ("Sender's role", NR),
        ("Direction (from countries)", _v(e.direction)),
        ("Mode", _v(e.mode)),
        ("Service", _v(e.service)),
        ("Origin country", NR),
        ("Supplier / origin place", NR),
        ("Port of loading", NR),
        ("Destination country", _v(e.destination_country)),
        ("Port of arrival", _v(e.destination_port)),
        ("Containers", _v(containers)),
        ("Cartons", NR if fcl else _v(cartons)),
        ("Volume (CBM)", NR + " (FCL is priced per container)" if fcl else _v(e.total_volume_cbm)),
        ("Gross weight (kg)", _v(e.total_gross_weight_kg)),
        ("Pallets", NR if fcl else _v(e.pallets)),
        ("Commodity", _v(e.commodity)),
        ("Hazardous", hazardous),
        ("Incoterm", _v(e.incoterm)),
        ("Delivery by us", _v(e.delivery_required)),
        ("Delivery address", _v(", ".join(filter(None, [e.delivery_place, e.delivery_postcode])))),
        ("Customs clearance by us", clearance),
        ("Cargo ready date", NR),
    ]


def format_quote(q: Quote) -> str:
    cur = q.currency
    w = max(len(l.description) for l in q.lines)
    lines = [f"=== QUOTE ({'MOCK RATES – PROTOTYPE' if q.is_mock else 'DRAFT'}) ===",
             f"  {q.lane}  ·  {q.service.value.upper()}  ·  Incoterm: {_v(q.incoterm)}", ""]
    section = None
    for l in q.lines:
        if l.section != section:
            section = l.section
            lines.append(f"  {section}")
        qty = f"{l.quantity:g} x {l.unit_rate:,.2f}"
        lines.append(f"    {l.description:<{w}}  {qty:>16}  {cur} {l.amount:>10,.2f}")
    pad = w + 4 + 2 + 16 + 2
    lines += ["",
              f"  {'Subtotal':<{pad}}{cur} {q.subtotal:>10,.2f}",
              f"  {f'Margin ({q.margin / q.subtotal * 100:g}%)':<{pad}}{cur} {q.margin:>10,.2f}",
              f"  {'TOTAL':<{pad}}{cur} {q.total:>10,.2f}",
              "", f"  Valid for {q.validity_days} days.", "  Assumptions:"]
    lines += [f"    - {a}" for a in q.assumptions]
    lines += ["  Not included:"] + [f"    - {x}" for x in q.exclusions]
    return "\n".join(lines)


def format_result(e: Enquiry, t: Triage, q: Quote | None = None) -> str:
    rows = enquiry_rows(e)
    width = max(len(k) for k, _ in rows)
    lines = ["=== SHIPMENT DETAILS ==="]
    lines += [f"  {k:<{width}}  {v}" for k, v in rows]
    lines += ["", f"=== DECISION: {t.status.upper()} ==="]
    if t.status == "manual_review":
        lines += [f"  Why: {r}" for r in t.reasons]
    elif t.status == "need_info":
        lines += ["  Waiting for the sender's answers below."]
    else:
        lines += ["  Ready to quote."]
    if t.status != "manual_review":
        lines += ["", "=== QUOTE WILL INCLUDE ==="]
        lines += [f"  - {item}" for item in describe_scope(quote_scope(e))]
        lines += ["  (Duties and import VAT: not included, at cost)"]
    if t.missing_fields:
        title = "ASK THE SENDER" if t.status == "need_info" else "ALSO ASK (does not block the quote)"
        lines += ["", f"=== {title} ==="]
        lines += [f"  {i}. {m[0].upper() + m[1:]}" for i, m in enumerate(t.missing_fields, 1)]
    staff_notes = t.notes + e.extraction_notes
    if staff_notes:
        lines += ["", "=== NOTES FOR STAFF ==="] + [f"  - {n}" for n in staff_notes]
    if q is not None:
        lines += ["", format_quote(q)]
    return "\n".join(lines)
