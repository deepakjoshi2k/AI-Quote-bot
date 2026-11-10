"""What Lincoln Cargo quotes for an import: destination services, charged to whoever emails.

Agreed rules (Nov 2026):
- The quote always goes to, and is charged to, the sender, whatever the Incoterm
  (the Incoterm is between buyer and seller).
- Lincoln Cargo does not quote ocean freight or origin charges for imports.
- Included: destination terminal handling + release fee, and delivery to the door if asked.
- Import customs clearance: only if the email asks for it. "Clearance only" means just that.
- Duties and import VAT are never included (at cost).
"""
from __future__ import annotations

from src.models import ChargeSections, Enquiry, Incoterm

# Incoterms where the seller delivers but import customs clearance stays with the importer.
CLEARANCE_BY_IMPORTER_AT_DOOR = {Incoterm.DAP, Incoterm.DPU}


def quote_scope(e: Enquiry) -> ChargeSections:
    """Sections Lincoln Cargo puts in the quote for this enquiry."""
    if e.clearance_only:
        return ChargeSections(origin_pickup=False, origin_export_clearance=False, origin_handling=False,
                              ocean_freight=False, destination_handling=False,
                              import_clearance=True, delivery=False)
    return ChargeSections(
        origin_pickup=False, origin_export_clearance=False, origin_handling=False, ocean_freight=False,
        destination_handling=True,
        import_clearance=e.customs_clearance_requested is True,
        delivery=e.delivery_required is True,
    )


def describe_scope(s: ChargeSections) -> list[str]:
    """Plain-English list of what the quote will contain."""
    items = []
    if s.destination_handling:
        items.append("Destination terminal handling + release fee")
    if s.import_clearance:
        items.append("Import customs clearance")
    if s.delivery:
        items.append("Delivery to the final address")
    return items


def clearance_note(e: Enquiry) -> str | None:
    """Staff note for DAP/DPU when clearance isn't requested (automate the importer email later)."""
    if e.incoterm in CLEARANCE_BY_IMPORTER_AT_DOOR and not e.customs_clearance_requested:
        return (f"{e.incoterm.value}: import customs clearance is the importer's responsibility and is not "
                "in this quote. Email the importer to ask whether they want us to handle clearance.")
    return None
