"""Step 4: Incoterm -> which quote sections the UK importer pays for.

Source of truth: 03_freight_domain_reference.md, section 4. Confirm with Lincoln Cargo before production.
"""
from __future__ import annotations

from src.models import ChargeSections, Incoterm

# Columns: origin_pickup, origin_export_clearance, origin_handling, ocean_freight,
#          uk_destination, uk_import_clearance, uk_delivery_allowed
# uk_delivery_allowed = the importer *can* buy delivery from us; it is only charged if requested.
_TABLE: dict[Incoterm, tuple[bool, bool, bool, bool, bool, bool, bool]] = {
    Incoterm.EXW: (True,  True,  True,  True,  True,  True,  True),
    Incoterm.FCA: (True,  False, True,  True,  True,  True,  True),
    Incoterm.FOB: (False, False, False, True,  True,  True,  True),
    Incoterm.CFR: (False, False, False, False, True,  True,  True),
    Incoterm.CIF: (False, False, False, False, True,  True,  True),
    Incoterm.DAP: (False, False, False, False, False, True,  False),
    Incoterm.DDP: (False, False, False, False, False, False, False),
}

_ASSUMPTIONS: dict[Incoterm, list[str]] = {
    Incoterm.FCA: [
        "FCA assumed at the supplier's premises (collection and origin handling included, "
        "export clearance by the supplier). If the named place is the origin port, the quote changes."
    ],
    Incoterm.CFR: ["Ocean freight paid by the supplier (CFR). UK terminal handling assumed for the importer's account; confirm with the carrier."],
    Incoterm.CIF: ["Ocean freight and insurance paid by the supplier (CIF). UK terminal handling assumed for the importer's account; confirm with the carrier."],
    Incoterm.DAP: ["DAP: the supplier delivers to the importer's premises; only UK import customs clearance is quoted."],
}


def charge_sections(incoterm: Incoterm | str, delivery_required: bool | None) -> ChargeSections:
    """Return the sections the importer pays.

    - UNKNOWN Incoterm raises ValueError: never guess, triage must ask the customer.
    - UK delivery is charged only if delivery_required is True (None = not asked -> not charged).
    - DDP returns all False; triage should flag it as "nothing to quote".
    """
    term = Incoterm(incoterm.upper()) if isinstance(incoterm, str) else incoterm
    if term is Incoterm.UNKNOWN:
        raise ValueError("Incoterm is unknown: ask the customer before pricing.")

    pickup, exp_clr, handling, freight, dest, imp_clr, delivery_allowed = _TABLE[term]
    return ChargeSections(
        origin_pickup=pickup,
        origin_export_clearance=exp_clr,
        origin_handling=handling,
        ocean_freight=freight,
        uk_destination=dest,
        uk_import_clearance=imp_clr,
        uk_delivery=delivery_allowed and delivery_required is True,
    )


def incoterm_assumptions(incoterm: Incoterm | str) -> list[str]:
    """Assumption lines to print on the quote for this Incoterm."""
    term = Incoterm(incoterm.upper()) if isinstance(incoterm, str) else incoterm
    return list(_ASSUMPTIONS.get(term, []))


def delivery_ignored(incoterm: Incoterm | str, delivery_required: bool | None) -> bool:
    """True when the customer asked for delivery but the Incoterm makes it the supplier's job (DAP/DDP).
    Useful for a note in the quote, e.g. sample I07."""
    term = Incoterm(incoterm.upper()) if isinstance(incoterm, str) else incoterm
    return delivery_required is True and term in (Incoterm.DAP, Incoterm.DDP)
