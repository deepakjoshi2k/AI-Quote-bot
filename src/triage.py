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
    ChargeSections, Direction, Enquiry, Incoterm, Mode, Service, Triage, TriageFlag,
)
from src.rates import RateBook, load_rate_book
from src.zones import MANUAL, normalise_postcode, zone_for_postcode


def _matches(patterns: list[str], text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text, flags=re.IGNORECASE)]


def _needs_origin_port(s: ChargeSections) -> bool:
    return s.ocean_freight or s.origin_pickup or s.origin_handling or s.origin_export_clearance


def _needs_uk_port(s: ChargeSections) -> bool:
    return s.ocean_freight or s.uk_destination or s.uk_delivery


def _needs_cargo_size(s: ChargeSections) -> bool:
    # Customs-only (DAP) quotes are per entry, so cargo size isn't needed to price them.
    return _needs_origin_port(s) or s.uk_destination or s.uk_delivery


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

    if e.direction is Direction.EXPORT:
        block(TriageFlag.EXPORT, "Export enquiry: this agent handles UK imports only. Route to the export team.")
    if e.mode in (Mode.AIR, Mode.ROAD):
        block(TriageFlag.NOT_SEA, f"{e.mode.value.title()} freight enquiry: this agent covers sea imports only. "
                                  f"Route to the {e.mode.value} team.")

    cargo_text = " ".join(filter(None, [e.commodity, *e.special_requirements]))
    dg_hits = _matches(tcfg["dg_patterns"], cargo_text)
    if e.hazardous or e.un_number or dg_hits:
        detail = e.un_number or (e.commodity or "hazardous cargo")
        block(TriageFlag.DANGEROUS_GOODS,
              f"Dangerous goods ({detail}): needs DG-qualified staff, IMDG booking and a Safety Data Sheet (SDS).")

    for item in e.special_requirements:
        if _matches(tcfg["special_patterns"], item):
            block(TriageFlag.SPECIAL_CARGO, f"Special handling: {item}.")
    if e.commodity and _matches(tcfg["special_patterns"], e.commodity):
        block(TriageFlag.SPECIAL_CARGO, f"Special cargo: {e.commodity}.")

    if e.incoterm is Incoterm.DDP:
        block(TriageFlag.DDP, "DDP: the supplier pays all costs including UK duty, so there is nothing to quote "
                              "to the importer. Check whether the supplier wants a quote instead.")

    if e.delivery_postcode and e.delivery_required is not False:
        pc = normalise_postcode(e.delivery_postcode)
        if pc and zone_for_postcode(pc) == MANUAL:
            block(TriageFlag.MANUAL_DELIVERY_AREA,
                  f"Delivery to {pc} (Northern Ireland / Highlands & Islands / IoM / Channel Islands) needs a manual quote.")

    heavy = cfg["fcl"]["heavy_20gp_kg"]
    n20 = sum(c.quantity for c in e.containers if c.container_type == "20GP")
    n_all = sum(c.quantity for c in e.containers)
    if n20 and e.total_gross_weight_kg and e.total_gross_weight_kg / n_all > heavy:
        block(TriageFlag.HEAVY_CONTAINER,
              f"Heavy 20GP (~{e.total_gross_weight_kg / n_all:,.0f} kg per container): check UK haulage weight limits.")

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
            elif (e.origin_port and e.uk_port and e.service is not Service.UNKNOWN
                  and (e.service is Service.LCL or types)
                  and sections.ocean_freight
                  and not rb.has_lane(e.origin_port, e.uk_port, e.service, types)):
                what = "LCL" if e.service is Service.LCL else "/".join(types)
                block(TriageFlag.LANE_NOT_IN_SHEET,
                      f"No {what} rate for {e.origin_port} → {e.uk_port} in the rate sheet: request a partner rate.")
            if (e.origin_port in rb.origin_ports() and e.service is not Service.UNKNOWN
                    and (sections.origin_pickup or sections.origin_handling)
                    and not rb.has_origin_charges(e.origin_port, e.service)):
                block(TriageFlag.LANE_NOT_IN_SHEET,
                      f"No origin charges for {e.origin_port} ({e.service.value.upper()}): request partner origin rates.")
        if _needs_uk_port(sections) and e.uk_port and e.uk_port not in rb.uk_ports():
            block(TriageFlag.UK_PORT_NOT_IN_SHEET,
                  f"UK port {e.uk_port} is not in our rate sheet (we price {', '.join(sorted(rb.uk_ports()))}).")

    if e.commodity and _matches(tcfg["port_health_patterns"], e.commodity):
        flags.append(TriageFlag.PORT_HEALTH)
        info.append(f"Food/plant product ({e.commodity}): port health checks possible, at cost.")

    return reasons, flags, info


def _missing_fields(e: Enquiry, sections: ChargeSections | None, cfg: dict) -> tuple[list[str], list[str]]:
    """Returns (required missing, optional missing)."""
    req: list[str] = []
    opt: list[str] = []

    if e.direction is Direction.UNKNOWN:
        req.append("direction (import into the UK or export)")
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
    if sections is not None and _needs_uk_port(sections) and e.uk_port is None:
        req.append("UK port of arrival")

    delivery_possible = sections is None or e.incoterm not in (Incoterm.DAP, Incoterm.DDP)
    if delivery_possible:
        if e.delivery_required is True and not e.delivery_postcode:
            req.append("UK delivery postcode")
        elif e.delivery_required is None:
            opt.append("UK delivery needed? If so, the delivery postcode")

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
