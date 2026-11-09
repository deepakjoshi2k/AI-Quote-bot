"""Step 2: decide what happens to an enquiry. Pure Python rules, no LLM.

    manual_review : the agent must not price it (DG, export, air, lane not in sheet, ...)
    need_info     : something required is missing -> follow-up questions
    quote         : everything needed to price is present

Order matters: manual-review reasons are checked first, because asking a customer
for missing details is pointless if we can't auto-quote the shipment anyway.
"""
from __future__ import annotations

import re

from src.config import load_config
from src.incoterms import charge_sections
from src.models import (
    ChargeSections, CustomerRole, Direction, Enquiry, Incoterm, Mode, Service, Triage, TriageFlag,
)
from src.rates import RateBook, load_rate_book
from src.zones import MANUAL, zone_for_address


def _matches(patterns: list[str], text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text, flags=re.IGNORECASE)]


def _needs_origin_port(s: ChargeSections) -> bool:
    return s.ocean_freight or s.origin_pickup or s.origin_handling or s.origin_export_clearance


def _needs_destination_port(s: ChargeSections) -> bool:
    return s.ocean_freight or s.destination_handling or s.delivery


def _needs_cargo_size(s: ChargeSections) -> bool:
    # Customs-only (DAP) quotes are per entry, so cargo size isn't needed to price them.
    return _needs_origin_port(s) or s.destination_handling or s.delivery


def _manual_checks(e: Enquiry, sections: ChargeSections | None, rb: RateBook, cfg: dict
                   ) -> tuple[list[str], list[TriageFlag], list[str]]:
    """Returns (blocking reasons, flags, info reasons)."""
    reasons: list[str] = []
    flags: list[TriageFlag] = []
    info: list[str] = []
    tcfg = cfg["triage"]

    def block(flag: TriageFlag, reason: str) -> None:
        if flag not in flags:
            flags.append(flag)
        reasons.append(reason)

    # 1. Routing: is this enquiry for another team? (v1 prices UK sea imports only)
    if e.direction is Direction.EXPORT:
        block(TriageFlag.EXPORT, "Export enquiry (goods leaving the UK/EU): route to the export team.")
    elif e.direction is Direction.CROSS_TRADE:
        block(TriageFlag.CROSS_TRADE, f"Cross-trade ({e.origin_country} → {e.destination_country}): "
                                      "neither end is in the UK/EU. Route to the overseas/partner desk.")
    elif (e.direction is Direction.IMPORT and e.destination_country
          and e.destination_country not in rb.priced_countries()):
        place = e.delivery_place or e.destination_country
        block(TriageFlag.DESTINATION_NOT_PRICED,
              f"Import into {e.destination_country} ({place}): no destination rates for this country. "
              "Route to the European desk for destination port, customs and delivery charges.")
    if e.mode in (Mode.AIR, Mode.ROAD):
        block(TriageFlag.NOT_SEA, f"{e.mode.value.title()} freight enquiry: this agent covers sea freight only. "
                                  f"Route to the {e.mode.value} team.")
    routed_elsewhere = bool(reasons)

    # 2. Dangerous goods matter to every team, so always check.
    cargo_text = " ".join(filter(None, [e.commodity, *e.special_requirements]))
    dg_hits = _matches(tcfg["dg_patterns"], cargo_text)
    if e.hazardous or e.un_number or dg_hits:
        detail = e.un_number or (e.commodity or "hazardous cargo")
        block(TriageFlag.DANGEROUS_GOODS,
              f"Dangerous goods ({detail}): needs DG-qualified staff, IMDG booking and a Safety Data Sheet (SDS).")

    # 3. Who is asking? Useful context for whichever team gets it.
    if e.customer_role in (CustomerRole.SELLER, CustomerRole.AGENT):
        role_note = (f"Enquirer appears to be the {e.customer_role.value}, not the importer: "
                     "the Incoterm split is the other way round for them.")
        if routed_elsewhere:
            info.append(role_note)
        else:
            block(TriageFlag.CUSTOMER_NOT_BUYER, role_note + " v1 only auto-quotes importers. Quote manually.")

    # The remaining checks are about pricing a UK sea import; skip them if it's going elsewhere.
    if routed_elsewhere:
        return reasons, flags, info

    for item in e.special_requirements:
        if _matches(tcfg["special_patterns"], item):
            block(TriageFlag.SPECIAL_CARGO, f"Special handling: {item}.")
    if e.commodity and _matches(tcfg["special_patterns"], e.commodity):
        block(TriageFlag.SPECIAL_CARGO, f"Special cargo: {e.commodity}.")

    if e.incoterm is Incoterm.DDP:
        block(TriageFlag.DDP, "DDP: the supplier pays all costs including import duty, so there is nothing to quote "
                              "to the importer. Check whether the supplier wants a quote instead.")

    if e.delivery_postcode and e.delivery_required is not False:
        if zone_for_address(e.destination_country, e.delivery_postcode) == MANUAL:
            where = ("Northern Ireland / Highlands & Islands / IoM / Channel Islands"
                     if e.destination_country in (None, "GB") else "an island / outermost region")
            block(TriageFlag.MANUAL_DELIVERY_AREA,
                  f"Delivery to {e.delivery_postcode} ({where}) needs a manual quote.")

    heavy = cfg["fcl"]["heavy_20gp_kg"]
    n20 = sum(c.quantity for c in e.containers if c.container_type == "20GP")
    n_all = sum(c.quantity for c in e.containers)
    if n20 and e.total_gross_weight_kg and e.total_gross_weight_kg / n_all > heavy:
        block(TriageFlag.HEAVY_CONTAINER,
              f"Heavy 20GP (~{e.total_gross_weight_kg / n_all:,.0f} kg per container): check road weight limits for delivery.")

    # Lane checks only make sense once we know which legs the importer pays for.
    if sections is not None and e.mode is not Mode.AIR and e.direction is not Direction.EXPORT:
        types = [c.container_type for c in e.containers]
        if _needs_origin_port(sections):
            if e.origin_port is None and e.origin_port_name:
                block(TriageFlag.LANE_NOT_IN_SHEET,
                      f"Origin port '{e.origin_port_name}' is not in our rate sheet: request a partner rate.")
            elif e.origin_port and e.origin_port not in rb.origin_ports():
                block(TriageFlag.LANE_NOT_IN_SHEET,
                      f"No rates from {e.origin_port} in the rate sheet: request a partner rate.")
            elif (e.origin_port and e.destination_port and e.service is not Service.UNKNOWN
                  and (e.service is Service.LCL or types)
                  and sections.ocean_freight
                  and not rb.has_lane(e.origin_port, e.destination_port, e.service, types)):
                what = "LCL" if e.service is Service.LCL else "/".join(types)
                block(TriageFlag.LANE_NOT_IN_SHEET,
                      f"No {what} rate for {e.origin_port} → {e.destination_port} in the rate sheet: request a partner rate.")
            if (e.origin_port in rb.origin_ports() and e.service is not Service.UNKNOWN
                    and (sections.origin_pickup or sections.origin_handling)
                    and not rb.has_origin_charges(e.origin_port, e.service)):
                block(TriageFlag.LANE_NOT_IN_SHEET,
                      f"No origin charges for {e.origin_port} ({e.service.value.upper()}): request partner origin rates.")
        if _needs_destination_port(sections) and e.destination_port and e.destination_port not in rb.destination_ports():
            block(TriageFlag.DESTINATION_PORT_NOT_IN_SHEET,
                  f"Arrival port {e.destination_port} is not in our rate sheet: request partner destination rates.")
        # EU delivery is priced from a given port to a given country/zone; check that rate exists.
        if (sections.delivery and e.destination_country not in (None, "GB") and e.destination_port
                and e.service is not Service.UNKNOWN and e.delivery_postcode):
            zone = zone_for_address(e.destination_country, e.delivery_postcode)
            if zone and zone != MANUAL and not rb.has_eu_delivery(e.destination_port, e.destination_country,
                                                                    zone, e.service):
                default, _ = rb.default_port_for_country(e.destination_country)
                block(TriageFlag.LANE_NOT_IN_SHEET,
                      f"No delivery rate from {e.destination_port} to {e.destination_country} zone {zone} "
                      f"(our default port for {e.destination_country} is {default}): quote manually.")

    if e.direction is Direction.IMPORT and e.destination_country not in (None, "GB"):
        info.append(f"EU import ({e.destination_country}): customs via an EU customs partner; the importer "
                    "needs an EU EORI number. Duties and import VAT at cost. Confirm process with Lincoln Cargo.")

    if e.commodity and _matches(tcfg["port_health_patterns"], e.commodity):
        flags.append(TriageFlag.PORT_HEALTH)
        info.append(f"Food/plant product ({e.commodity}): port health checks possible, at cost.")

    return reasons, flags, info


def _missing_fields(e: Enquiry, sections: ChargeSections | None, cfg: dict) -> tuple[list[str], list[str]]:
    """Returns (required missing, optional missing)."""
    req: list[str] = []
    opt: list[str] = []

    if e.direction is Direction.UNKNOWN:
        req.append("where the goods are coming from and going to (countries)")
    if e.mode is Mode.UNKNOWN:
        req.append("transport mode (sea, air or road)")
    if e.incoterm is Incoterm.UNKNOWN:
        req.append("Incoterm (e.g. EXW, FOB, CIF) and named place")
    if not e.commodity:
        req.append("commodity (what the goods are)")

    if sections is None or _needs_cargo_size(sections):
        if e.service is Service.FCL and not e.containers:
            req.append("container size and number (20GP / 40GP / 40HC)")
        elif e.service is Service.LCL:
            if e.total_volume_cbm is None:
                req.append("total volume (CBM) or carton dimensions")
            if e.total_gross_weight_kg is None:
                req.append("total gross weight (kg)")
        elif e.service is Service.UNKNOWN:
            th = cfg["service_thresholds"]
            if e.total_volume_cbm is not None and th["lcl_max_cbm"] <= e.total_volume_cbm <= th["fcl_min_cbm"]:
                req.append(f"FCL or LCL? ({e.total_volume_cbm:.1f} CBM: both could work)")
            else:
                req.append("container size and number, or total volume (CBM) and weight")

    if sections is None or _needs_origin_port(sections):
        if e.origin_port is None and not e.origin_port_name:
            req.append("origin port or supplier's city")
    if sections is not None and (sections.origin_pickup and not e.origin_place):
        opt.append("supplier's collection address")
    if sections is not None and _needs_destination_port(sections) and e.destination_port is None:
        req.append("port of arrival")

    delivery_possible = sections is None or e.incoterm not in (Incoterm.DAP, Incoterm.DDP)
    if delivery_possible:
        if e.delivery_required is True and not e.delivery_postcode:
            req.append("delivery postcode")
        elif e.delivery_required is None:
            opt.append("delivery needed? If so, the delivery postcode")

    if e.ready_date is None:
        opt.append("cargo ready date")
    if e.hazardous is None:
        opt.append("is the cargo hazardous? (if so, UN number and SDS)")
    return req, opt


def triage(enquiry: Enquiry, rate_book: RateBook | None = None) -> Triage:
    rb = rate_book or load_rate_book()
    cfg = load_config()
    e = enquiry

    sections = None
    if e.incoterm is not Incoterm.UNKNOWN:
        sections = charge_sections(e.incoterm, e.delivery_required)

    blocking, flags, info = _manual_checks(e, sections, rb, cfg)
    if blocking:
        return Triage(status="manual_review", reasons=blocking + info, flags=flags)

    required, optional = _missing_fields(e, sections, cfg)
    if required:
        return Triage(status="need_info", reasons=info, missing_fields=required + optional, flags=flags)

    return Triage(status="quote", reasons=info, flags=flags)
