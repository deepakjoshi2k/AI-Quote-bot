"""Step 2: decide what happens to an import enquiry. Pure Python rules, no LLM.

    manual_review : the agent must not price it (export, air/road, DG, LCL, special cargo, ...)
    need_info     : something needed for the quote is missing -> questions for the sender
    quote         : everything needed to price the destination services is present

Scope (src/scope.py): imports only; Lincoln Cargo quotes destination services to whoever emails.
Manual-review reasons are checked first: no point asking for details we can't auto-quote anyway.
"""
from __future__ import annotations

import re

from src.config import load_config
from src.models import ChargeSections, Direction, Enquiry, Incoterm, Mode, Service, Triage, TriageFlag
from src.rates import RateBook, load_rate_book
from src.scope import clearance_note, quote_scope
from src.tariff import tariff_for
from src.zones import MANUAL, zone_for_address


def _matches(patterns: list[str], text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text, flags=re.IGNORECASE)]


def _manual_checks(e: Enquiry, scope: ChargeSections, rb: RateBook, cfg: dict
                   ) -> tuple[list[str], list[TriageFlag], list[str]]:
    """Returns (blocking reasons, flags, notes for staff). Notes never block: DAP clearance, EU import,
    DDP, port health, overweight container."""
    reasons: list[str] = []
    flags: list[TriageFlag] = []
    info: list[str] = []
    tcfg = cfg["triage"]

    def block(flag: TriageFlag, reason: str) -> None:
        if flag not in flags:
            flags.append(flag)
        reasons.append(reason)

    # 1. Routing: this agent quotes UK/EU sea imports only.
    if e.direction is Direction.EXPORT:
        block(TriageFlag.EXPORT, "Export enquiry: this agent handles imports only. Route to the export team.")
    elif e.direction is Direction.CROSS_TRADE:
        block(TriageFlag.CROSS_TRADE, f"Cross-trade ({e.origin_country} → {e.destination_country}): "
                                      "neither end is in the UK/EU. Route to the overseas/partner desk.")
    elif (e.direction is Direction.IMPORT and e.destination_country
          and e.destination_country not in rb.priced_countries()):
        block(TriageFlag.DESTINATION_NOT_PRICED,
              f"Import into {e.destination_country}: no destination rates for this country. Route to the European desk.")
    if e.mode in (Mode.AIR, Mode.ROAD):
        block(TriageFlag.NOT_SEA, f"{e.mode.value.title()} freight enquiry: this agent covers sea freight only. "
                                  f"Route to the {e.mode.value} team.")
    routed_elsewhere = bool(reasons)

    # 2. Dangerous goods matter to every team. Hazard not stated = treated as non-hazardous,
    #    but the goods description is still checked for DG words.
    cargo_text = " ".join(filter(None, [e.commodity, *e.special_requirements]))
    if e.hazardous or e.un_number or _matches(tcfg["dg_patterns"], cargo_text):
        detail = e.un_number or (e.commodity or "hazardous cargo")
        block(TriageFlag.DANGEROUS_GOODS,
              f"Dangerous goods ({detail}): needs DG-qualified staff and a Safety Data Sheet (SDS).")
    if routed_elsewhere:
        return reasons, flags, info

    # 3. LCL is quoted by hand for now (cartons / pallets / W/M maths).
    if e.service is Service.LCL:
        block(TriageFlag.LCL_MANUAL, "LCL enquiry: LCL is quoted manually for now.")

    for item in e.special_requirements:
        if _matches(tcfg["special_patterns"], item):
            block(TriageFlag.SPECIAL_CARGO, f"Special handling: {item}.")
    if e.commodity and _matches(tcfg["special_patterns"], e.commodity):
        block(TriageFlag.SPECIAL_CARGO, f"Special cargo: {e.commodity}.")

    if scope.delivery and e.delivery_postcode and zone_for_address(e.destination_country, e.delivery_postcode) == MANUAL:
        where = ("Northern Ireland / Highlands & Islands / IoM / Channel Islands"
                 if e.destination_country in (None, "GB") else "an island / outermost region")
        block(TriageFlag.MANUAL_DELIVERY_AREA, f"Delivery to {e.delivery_postcode} ({where}) needs a manual quote.")

    # 4. Destination port and delivery must be in our rate sheets (tariff countries: any port in the country).
    tariff = tariff_for(e.destination_country)
    if tariff:
        if scope.destination_handling and e.destination_port and not e.destination_port.startswith(e.destination_country):
            block(TriageFlag.DESTINATION_PORT_NOT_IN_SHEET,
                  f"Arrival port {e.destination_port} is not in {e.destination_country}: quote manually.")
    elif scope.destination_handling and e.destination_port and e.destination_port not in rb.destination_ports():
        block(TriageFlag.DESTINATION_PORT_NOT_IN_SHEET,
              f"Arrival port {e.destination_port} is not in our rate sheet: quote manually or add its rates.")
    if (not tariff and scope.delivery and e.destination_country not in (None, "GB") and e.destination_port
            and e.service is Service.FCL and e.delivery_postcode):
        zone = zone_for_address(e.destination_country, e.delivery_postcode)
        if zone and zone != MANUAL and not rb.has_eu_delivery(e.destination_port, e.destination_country, zone, e.service):
            block(TriageFlag.LANE_NOT_IN_SHEET,
                  f"No delivery rate from {e.destination_port} to {e.destination_country} zone {zone}: quote manually.")

    # 5. Warnings and notes for staff (never block).
    heavy = cfg["fcl"]["heavy_20gp_kg"]
    n20 = sum(c.quantity for c in e.containers if c.container_type == "20GP")
    n_all = sum(c.quantity for c in e.containers)
    if n20 and e.total_gross_weight_kg and e.total_gross_weight_kg / n_all > heavy:
        flags.append(TriageFlag.HEAVY_CONTAINER)
        info.append(f"20GP over {heavy:,} kg (~{e.total_gross_weight_kg / n_all:,.0f} kg per container): "
                    "above typical max payload, check with the carrier and haulier.")
    if e.commodity and _matches(tcfg["port_health_patterns"], e.commodity):
        flags.append(TriageFlag.PORT_HEALTH)
        info.append(f"Food/plant product ({e.commodity}): port health checks possible, at cost.")
    note = clearance_note(e)
    if note:
        info.append(note)
    if e.incoterm is Incoterm.DDP:
        info.append("DDP: the seller pays import duty and VAT. Duty/VAT are not in this quote; agree how they are paid."
                    + (" We need a signed indemnity from shipper, consignee and sender's company (in the quote terms)."
                       if tariff else ""))
    if tariff:
        info.append(f"Priced from: {tariff['name']} ({tariff['currency']}, same charges at every port).")
        if "Road haulage" in " ".join(tariff.get("terms", [])):
            info.append("Road haulage is a fixed rate for now: check it for this delivery distance.")
    elif e.destination_country not in (None, "GB"):
        info.append(f"EU import ({e.destination_country}): customs via an EU customs partner; "
                    "duties and import VAT at cost. Confirm process with Lincoln Cargo.")
    return reasons, flags, info


def _missing_fields(e: Enquiry, scope: ChargeSections) -> tuple[list[str], list[str]]:
    """Returns (required, optional) questions for the sender."""
    req: list[str] = []
    opt: list[str] = []
    tariff = tariff_for(e.destination_country)

    if e.destination_country is None and e.direction is Direction.UNKNOWN:
        req.append("delivery country (where the goods are being imported to)")
    if e.mode is Mode.UNKNOWN:
        req.append("transport mode (sea freight?)")
    if not e.commodity:
        req.append("commodity (what the goods are)")

    if not e.clearance_only:
        if not e.containers:
            req.append("container size and number (20GP / 40GP / 40HC)")
        if scope.destination_handling and e.destination_port is None:
            # Tariff countries charge the same at every port: the port doesn't change the price.
            (opt if tariff else req).append("port of arrival (discharge port)")
        if scope.delivery and not e.delivery_postcode:
            req.append("delivery address and postcode")
        if tariff and scope.delivery and e.total_gross_weight_kg is None:
            opt.append(f"gross weight per container (surcharge over {tariff['heavy_over_kg'] / 1000:g} t)")

    if e.incoterm is Incoterm.UNKNOWN:
        opt.append("Incoterm (e.g. FOB, CIF, DAP)")
    return req, opt


def triage(enquiry: Enquiry, rate_book: RateBook | None = None) -> Triage:
    rb = rate_book or load_rate_book()
    cfg = load_config()
    e = enquiry
    scope = quote_scope(e)

    blocking, flags, notes = _manual_checks(e, scope, rb, cfg)
    if blocking:
        return Triage(status="manual_review", reasons=blocking, notes=notes, flags=flags)

    required, optional = _missing_fields(e, scope)
    if required:
        return Triage(status="need_info", notes=notes, missing_fields=required + optional, flags=flags)
    # Optional questions ride along with the quote (e.g. "which Incoterm?"); they never block it.
    return Triage(status="quote", notes=notes, missing_fields=optional, flags=flags)
