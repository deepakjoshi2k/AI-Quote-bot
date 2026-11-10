"""Customer-facing quote as plain text (a draft for staff to check before sending; nothing is sent)."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from src.config import ROOT
from src.models import Enquiry, Quote, Triage

QUOTES_DIR = ROOT / "quotes"
WIDTH = 78


def _money(x: float) -> str:
    return f"{x:,.2f}"


def _port(e: Enquiry, q: Quote) -> str:
    from src.quote import PORT_NAMES
    if e.destination_port:
        return f"{PORT_NAMES.get(e.destination_port, e.destination_port)} ({e.destination_port})"
    if e.clearance_only:
        return "Not needed (customs clearance only)"
    return f"Not stated (same charges at any port in {e.destination_country})"


def render_quote_text(e: Enquiry, q: Quote, t: Triage | None = None, today: date | None = None) -> str:
    today = today or date.today()
    cur = q.currency
    rule = "-" * WIDTH
    containers = ", ".join(f"{c.quantity} x {c.container_type}" for c in e.containers) or "Not stated"
    delivery = ", ".join(filter(None, [e.delivery_place, e.delivery_postcode])) or "Not stated"

    out = [
        "=" * WIDTH,
        "LINCOLN CARGO – IMPORT QUOTATION" + ("   [MOCK RATES – PROTOTYPE]" if q.is_mock else ""),
        "Draft for staff review – not sent",
        "=" * WIDTH,
        f"Date:          {today:%d %b %Y}",
        f"Valid until:   {today + timedelta(days=q.validity_days):%d %b %Y} ({q.validity_days} days)",
        f"Prepared for:  {e.customer_name or 'Not stated'}" + (f" <{e.contact_email}>" if e.contact_email else ""),
        "",
        "SHIPMENT",
        f"  Equipment:     {containers} ({q.service.value.upper()})",
        f"  Commodity:     {e.commodity or 'Not stated'}",
        f"  Gross weight:  {f'{e.total_gross_weight_kg:,.0f} kg' if e.total_gross_weight_kg else 'Not stated'}",
        f"  Incoterm:      {e.incoterm.value if e.incoterm.value != 'UNKNOWN' else 'Not stated'}",
        f"  Arrival port:  {_port(e, q)}",
        f"  Delivery to:   {delivery}",
        "",
        f"CHARGES ({cur})",
        rule,
    ]
    desc_w = WIDTH - 2 - 22 - 14
    section = None
    for l in q.lines:
        if l.section != section:
            section = l.section
            out.append(f"{section}")
        qty = f"{l.quantity:g} x {_money(l.unit_rate)}"
        out.append(f"  {l.description[:desc_w]:<{desc_w}}{qty:>22}{_money(l.amount):>14}")
        out.append(f"    ({l.basis})")
    out.append(rule)
    if q.margin:
        out.append(f"{'Subtotal':<{WIDTH - 14}}{_money(q.subtotal):>14}")
        out.append(f"{'Margin':<{WIDTH - 14}}{_money(q.margin):>14}")
    out.append(f"{'TOTAL ' + cur:<{WIDTH - 14}}{_money(q.total):>14}")
    out.append(rule)

    if q.assumptions:
        out += ["", "BASED ON"] + [f"  - {a}" for a in q.assumptions if "MOCK" not in a]
    out += ["", "NOT INCLUDED"] + [f"  - {x}" for x in q.exclusions]
    if q.terms:
        out += ["", "TERMS AND CONDITIONS"] + [f"  {i}. {x}" for i, x in enumerate(q.terms, 1)]
    if t and t.missing_fields:
        out += ["", "PLEASE ALSO CONFIRM"] + [f"  - {m[0].upper() + m[1:]}" for m in t.missing_fields]
    out += ["", rule, f"Rates: {q.source}", rule]
    return "\n".join(out) + "\n"


def save_quote_text(text: str, folder: Path = QUOTES_DIR, stamp: str | None = None) -> Path:
    """Save to quotes/ (git-ignored). Returns the file path."""
    from datetime import datetime
    folder.mkdir(exist_ok=True)
    path = folder / f"quote-{stamp or datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    path.write_text(text, encoding="utf-8")
    return path
