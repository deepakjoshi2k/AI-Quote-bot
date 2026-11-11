"""Human-readable staff screen for an import enquiry: details, decision, quote and notes for staff.

"Not available" = needed but not in the email (asked of the sender if it matters).
"Not relevant"  = not used for Lincoln Cargo's import quote (destination services, charged to the sender).

Layout (same style for every section, see section()):
    SHIPMENT DETAILS  ->  DECISION: QUOTE / EMAIL SENDER / MANUAL REVIEW  ->  (ports, Maersk look-up, quote)
    (try_email clears the working steps - ports list, Maersk look-up - once the quote is made)
    ->  NOTES FOR STAFF: Incoterm · Sender asked · Agent actions (done / to do) · To check
"""
from __future__ import annotations

import textwrap

from src.models import Enquiry, Incoterm, Quote, Service, Triage
from src.scope import clearance_note

NA = "Not available"
NR = "Not relevant"
WIDTH = 78

DECISIONS = {"quote": "QUOTE", "need_info": "EMAIL SENDER", "manual_review": "MANUAL REVIEW"}

# One line per Incoterm (Incoterms 2020), from the importer's side. Confirm wording with Lincoln Cargo.
INCOTERM_MEANING = {
    Incoterm.EXW: "the buyer arranges everything from the seller's premises, including export clearance.",
    Incoterm.FCA: "the seller hands the goods, export-cleared, to the buyer's carrier; the buyer pays the main carriage.",
    Incoterm.FAS: "the seller puts the goods alongside the ship; the buyer pays loading, sea freight and onwards.",
    Incoterm.FOB: "the seller loads the goods on board at origin; the buyer pays sea freight and everything after.",
    Incoterm.CFR: "the seller pays sea freight to the destination port; the buyer pays from there onwards.",
    Incoterm.CIF: "as CFR, plus the seller insures the goods to the destination port.",
    Incoterm.CPT: "the seller pays carriage to the named place; risk passes at the first carrier.",
    Incoterm.CIP: "as CPT, plus the seller insures the goods (higher cover).",
    Incoterm.DAP: "the seller delivers to the named place (not unloaded); the importer clears customs and pays duty/VAT.",
    Incoterm.DPU: "the seller delivers and unloads at the named place; the importer clears customs and pays duty/VAT.",
    Incoterm.DDP: "the seller pays everything, including import duty and VAT.",
}


# ---------- layout helpers ----------

def section(title: str) -> str:
    """'── TITLE ─────────' full width."""
    head = f"── {title} "
    return head + "─" * max(3, WIDTH - len(head))


def banner(text: str) -> str:
    inner = WIDTH - 2
    return "\n".join(["╔" + "═" * inner + "╗", "║" + f"  {text}".ljust(inner) + "║", "╚" + "═" * inner + "╝"])


def rows(pairs: list[tuple[str, str]], indent: int = 2) -> list[str]:
    """Aligned 'label   value' rows; a list value continues on the next lines under the value column."""
    w = max(len(k) for k, _ in pairs)
    room = max(30, WIDTH - indent - w - 2)
    out = []
    for k, v in pairs:
        values = v if isinstance(v, list) else [v]
        first = True
        for item in values:
            # a "[x] " / "[ ] " / "! " marker keeps its wrapped lines indented under the text
            hang = " " * (4 if item[:4] in ("[x] ", "[ ] ") else 2 if item.startswith("! ") else 0)
            for j, piece in enumerate(textwrap.wrap(item, room, subsequent_indent=hang) or [""]):
                out.append(" " * indent + f"{k if first else '':<{w}}  {piece}")
                first = False
    return out


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


# ---------- 1. shipment details ----------

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


def format_details(e: Enquiry) -> str:
    pairs = enquiry_rows(e)
    if e.extraction_notes:
        pairs.append(("Other details in email", list(e.extraction_notes)))
    return "\n".join([section("SHIPMENT DETAILS")] + rows(pairs))


# ---------- 2. decision ----------

def format_decision(e: Enquiry, t: Triage) -> str:
    return banner(f"DECISION: {DECISIONS[t.status]}")


def format_result(e: Enquiry, t: Triage, q: Quote | None = None) -> str:
    """Details + decision (+ the quote). Notes for staff come last, from format_staff_notes()."""
    parts = [format_details(e), "", format_decision(e, t)]
    if q is not None:
        parts += ["", format_quote(q)]
    return "\n".join(parts)


# ---------- 3. quote ----------

def format_quote(q: Quote) -> str:
    cur = q.currency
    w = max(len(l.description) for l in q.lines)
    title = "QUOTE – MOCK RATES (PROTOTYPE)" if q.is_mock else "QUOTE (DRAFT – check before sending)"
    lines = [section(title), f"  {q.lane}", f"  {q.service.value.upper()}  ·  Incoterm: {_v(q.incoterm)}  ·  "
             f"valid {q.validity_days} days", ""]
    section_name = None
    for l in q.lines:
        if l.section != section_name:
            section_name = l.section
            lines.append(f"  {section_name}")
        qty = f"{l.quantity:g} x {l.unit_rate:,.2f}"
        lines.append(f"    {l.description:<{w}}  {qty:>16}  {cur} {l.amount:>10,.2f}")
    pad = w + 4 + 2 + 16 + 2
    rule = "  " + "─" * (pad + len(cur) + 11)
    lines.append(rule)
    if q.margin:
        lines += [f"  {'Subtotal':<{pad}}{cur} {q.subtotal:>10,.2f}",
                  f"  {f'Margin ({q.margin / q.subtotal * 100:g}%)':<{pad}}{cur} {q.margin:>10,.2f}"]
    lines += [f"  {'TOTAL':<{pad}}{cur} {q.total:>10,.2f}", rule, ""]
    info = [("Based on", list(q.assumptions) or ["-"]), ("Not included", list(q.exclusions)),
            ("Rates", q.source)]
    if q.internal_notes:
        info.append(("Internal (staff)", list(q.internal_notes)))
    lines += rows(info)
    return "\n".join(lines)


# ---------- 4. notes for staff ----------

def incoterm_line(e: Enquiry, t: Triage) -> str:
    if e.incoterm is Incoterm.UNKNOWN:
        return "Not stated" + (" (asked in the email to the sender)" if t.status != "manual_review" else "")
    text = f"{e.incoterm.value} – {INCOTERM_MEANING[e.incoterm]}"
    if e.incoterm is Incoterm.DDP:
        text += " Duty/VAT are not in our quote: agree how they are paid."
    return text


def sender_asked(e: Enquiry) -> str:
    if e.clearance_only:
        return "Customs clearance only"
    items = ["Destination charges"]
    if e.delivery_required is True:
        items.append("door delivery")
    elif e.delivery_required is False:
        items.append("no delivery (declined)")
    if e.customs_clearance_requested is True:
        items.append("customs clearance")
    elif e.customs_clearance_requested is False:
        items.append("no customs clearance (they clear it)")
    if e.port_advice_requested:
        items.append("advise the nearest port")
    return " · ".join(items)


def agent_actions(e: Enquiry, t: Triage, done: list[str] | None = None, quotes_made: int = 0,
                  ask: list[str] | None = None, todo: list[str] | None = None) -> list[str]:
    """What the agent did ([x]) and what staff still need to do ([ ]). Nothing is ever sent automatically."""
    ask = t.missing_fields if ask is None else ask
    out = [f"[x] {d}" for d in (done or [])] + [f"[ ] {x}" for x in (todo or [])]
    if t.status == "manual_review":
        out += [f"[ ] Manual: {r}" for r in t.reasons]
        return out
    questions = "; ".join(m[0].upper() + m[1:] for m in ask)
    if quotes_made:
        what = "the quote" if quotes_made == 1 else f"the {quotes_made} quotes"
        out.append(f"[ ] Check {what}, then email to the sender"
                   + (f" asking them to confirm: {questions}" if ask else ""))
    elif ask:
        out.append(f"[ ] Email the sender to ask: {questions}")
    elif t.status == "quote" and not todo:
        out.append("[ ] No quote yet: enter the Maersk haulage and fuel, then re-run")
    if clearance_note(e):
        out.append("[ ] Clearance email: ask the importer whether they want us to handle import customs clearance")
    return out


def format_staff_notes(e: Enquiry, t: Triage, done: list[str] | None = None, quotes_made: int = 0,
                       ask: list[str] | None = None, todo: list[str] | None = None) -> str:
    pairs = [("Incoterm", incoterm_line(e, t)), ("Sender asked", sender_asked(e)),
             ("Agent actions", agent_actions(e, t, done, quotes_made, ask, todo))]
    if t.notes:
        pairs.append(("To check", [f"! {n}" for n in t.notes]))
    return "\n".join([section("NOTES FOR STAFF")] + rows(pairs))
