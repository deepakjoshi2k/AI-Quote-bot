"""Step 1: email text -> Enquiry.

Two halves, kept separate on purpose:
  1. extract_raw()  - the LLM copies what the email says into RawEnquiry (no maths).
  2. normalise()    - Python converts units, maps ports to codes, computes CBM, validates.
  3. apply_defaults() fills gaps we can safely fill from the rate sheet (and says so).
Only step 1 touches the LLM, so steps 2-3 are fully unit-tested.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from src.config import load_config
from src.llm import LLMClient, get_llm
from src.measures import cbm_from_cartons, suggest_service, to_cm, to_kg, weight_from_cartons
from src.models import (
    CartonLine, ContainerLine, CustomerRole, Direction, Enquiry, Incoterm, Mode, RawEnquiry, Service,
)
from src.rates import RateBook, load_rate_book
from src.zones import normalise_postcode

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract.md"
SUPPORTED_INCOTERMS = {"EXW", "FCA", "FOB", "CFR", "CIF", "DAP", "DDP"}
KNOWN_INCOTERMS = SUPPORTED_INCOTERMS | {"CPT", "CIP", "DPU", "FAS"}


# ---------- 1. LLM ----------

def build_system_prompt(today: date | None = None) -> str:
    schema = json.dumps(RawEnquiry.model_json_schema(), indent=1)
    return (PROMPT_PATH.read_text(encoding="utf-8")
            .replace("{today}", (today or date.today()).isoformat())
            .replace("{schema}", schema))


def extract_raw(text: str, llm: LLMClient | None = None) -> RawEnquiry:
    llm = llm or get_llm()
    return llm.extract(build_system_prompt(), f"EMAIL:\n\"\"\"\n{text.strip()}\n\"\"\"", RawEnquiry)


# ---------- 2. Python normalisation ----------

def normalise_container_type(raw: str) -> str | None:
    """'40ft high cube' -> '40HC', "20'" -> '20GP'. Specials (reefer, open top...) or 45ft -> None."""
    s = raw.lower().replace("-", " ")
    if re.search(r"reefer|\brf\b|\brh\b|open ?top|\bot\b|flat ?rack|\bfr\b|tank|45", s):
        return None
    compact = re.sub(r"[\s'\"’]|ft|foot|feet", "", s)
    if compact.startswith("20"):
        return "20GP"
    if compact.startswith("40"):
        if re.search(r"hc|hq|highcube|high", compact):
            return "40HC"
        return "40GP"
    return None


_INCOTERM_NAMES = {
    "EX WORKS": "EXW", "FREE CARRIER": "FCA", "FREE ON BOARD": "FOB", "FREE ALONGSIDE SHIP": "FAS",
    "COST AND FREIGHT": "CFR", "COST & FREIGHT": "CFR", "COST INSURANCE AND FREIGHT": "CIF",
    "COST, INSURANCE AND FREIGHT": "CIF", "CARRIAGE PAID TO": "CPT", "CARRIAGE AND INSURANCE PAID TO": "CIP",
    "DELIVERED AT PLACE UNLOADED": "DPU", "DELIVERED AT PLACE": "DAP", "DELIVERED DUTY PAID": "DDP",
    "C&F": "CFR", "CNF": "CFR",
}


def normalise_incoterm(raw: str | None) -> tuple[Incoterm, str | None]:
    """Returns (Incoterm, note). Unsupported terms (CPT, CIP, DPU, FAS) become UNKNOWN with a note."""
    if not raw:
        return Incoterm.UNKNOWN, None
    text = raw.upper().replace(".", "")
    code = None
    m = re.search(r"\b(" + "|".join(sorted(KNOWN_INCOTERMS)) + r")\b", text)
    if m:
        code = m.group(1)
    else:
        for name, c in _INCOTERM_NAMES.items():  # longest names listed first where they overlap
            if name in text:
                code = c
                break
    if code in SUPPORTED_INCOTERMS:
        return Incoterm(code), None
    if code in KNOWN_INCOTERMS:
        return Incoterm.UNKNOWN, f"unsupported Incoterm {code} (v1 handles EXW, FCA, FOB, CFR, CIF, DAP, DDP)"
    return Incoterm.UNKNOWN, f"Incoterm not recognised: {raw!r}"


def _country(code: str | None) -> str | None:
    code = (code or "").strip().upper()
    if code == "UK":
        code = "GB"
    return code if len(code) == 2 and code.isalpha() else None


def decide_direction(origin: str | None, destination: str | None, llm_direction: str,
                     home: set[str]) -> tuple[Direction, str | None]:
    """Python, not the LLM, decides direction from the countries. Returns (direction, note).

    destination in UK/EU                 -> import
    origin in UK/EU, destination outside -> export
    neither end in UK/EU                 -> cross_trade
    """
    if destination:
        if destination in home:
            return Direction.IMPORT, None
        if origin in home:
            return Direction.EXPORT, None
        if origin:
            return Direction.CROSS_TRADE, None
        return Direction.UNKNOWN, f"Destination {destination} is outside the UK/EU and origin is unknown"
    # Destination unknown: only accept the LLM's label when the countries don't contradict it.
    if llm_direction == "export" and origin in home:
        return Direction.EXPORT, None
    if llm_direction == "import" and origin not in home:
        return Direction.IMPORT, "Destination country not stated; assumed UK/EU import"
    return Direction.UNKNOWN, None


def _enum(enum_cls, value, default):
    try:
        return enum_cls(value)
    except ValueError:
        return default


def normalise(raw: RawEnquiry, rate_book: RateBook | None = None) -> Enquiry:
    rb = rate_book or load_rate_book()
    cfg = load_config()
    notes = list(raw.notes)
    special = list(raw.special_requirements)

    # Containers
    containers: list[ContainerLine] = []
    for c in raw.containers:
        ctype = normalise_container_type(c.container_type)
        if ctype is None:
            special.append(f"unsupported container type: {c.container_type}")
        elif c.quantity > 0:
            containers.append(ContainerLine(container_type=ctype, quantity=c.quantity))

    # Cartons -> cm / kg
    cartons = [
        CartonLine(
            quantity=c.quantity,
            length_cm=to_cm(c.length, c.dimension_unit),
            width_cm=to_cm(c.width, c.dimension_unit),
            height_cm=to_cm(c.height, c.dimension_unit),
            weight_kg_each=None if c.weight_each is None else to_kg(c.weight_each, c.weight_unit),
        )
        for c in raw.cartons
    ]

    # Volume
    volume = raw.total_volume_cbm
    if cartons:
        carton_cbm = cbm_from_cartons(cartons)
        if volume is None:
            volume = carton_cbm
            notes.append(f"CBM computed from carton dimensions: {carton_cbm:.3f}")
        elif volume and abs(carton_cbm - volume) / volume > cfg["cbm_mismatch_tolerance"]:
            notes.append(f"Stated volume {volume} CBM differs from carton dimensions ({carton_cbm:.3f} CBM): check")

    # Weight
    weight = None if raw.total_weight is None else to_kg(raw.total_weight, raw.weight_unit)
    if weight is None and cartons:
        weight = weight_from_cartons(cartons)
        if weight is not None:
            notes.append(f"Weight computed from cartons: {weight:.1f} kg")

    # Incoterm
    incoterm, inco_note = normalise_incoterm(raw.incoterm)
    if inco_note:
        notes.append(inco_note)
        if inco_note.startswith("unsupported"):
            special.append(inco_note)

    # Ports
    origin_port = rb.port_code(raw.origin_port_name)
    if raw.origin_port_name and not origin_port:
        notes.append(f"Origin port not recognised: {raw.origin_port_name!r}")
    uk_port = rb.port_code(raw.uk_port_name)
    if uk_port and not uk_port.startswith("GB"):
        notes.append(f"Arrival port {uk_port} is not in the UK")
        uk_port = None

    # Service
    if containers:
        service = Service.FCL
    elif raw.service in ("lcl", "fcl"):
        service = Service(raw.service)
    elif volume is not None:
        suggested = suggest_service(volume, cfg["service_thresholds"]["lcl_max_cbm"],
                                    cfg["service_thresholds"]["fcl_min_cbm"])
        service = Service.LCL if suggested is Service.LCL else Service.UNKNOWN
        if service is Service.LCL:
            notes.append(f"Service not stated; assumed LCL for {volume:.2f} CBM")
    else:
        service = Service.UNKNOWN

    # Countries -> direction (Python decides, the LLM only suggests)
    origin_country = _country(raw.origin_country) or (origin_port[:2] if origin_port else None)
    destination_country = _country(raw.destination_country)
    uk_postcode = normalise_postcode(raw.delivery_postcode)
    if destination_country is None and ((uk_postcode and " " in uk_postcode)
                                        or (uk_port and raw.direction != "export")):
        destination_country = "GB"
        notes.append("Destination taken as GB from the UK postcode / port")
    direction, dir_note = decide_direction(origin_country, destination_country, raw.direction,
                                           set(cfg["home_countries"]))
    if dir_note:
        notes.append(dir_note)
    if raw.direction not in ("unknown", direction.value) and direction is not Direction.UNKNOWN:
        notes.append(f"Model said '{raw.direction}', countries say '{direction.value}': using '{direction.value}'")

    # A named UK arrival port we don't recognise only matters for UK sea imports
    if (raw.uk_port_name and not uk_port and destination_country in (None, "GB")
            and raw.mode == "sea" and direction is Direction.IMPORT):
        special.append(f"unsupported UK port: {raw.uk_port_name}")

    # Postcode: validate as a UK postcode only when delivering in the UK
    if destination_country in (None, "GB"):
        postcode = uk_postcode
        if raw.delivery_postcode and not postcode:
            notes.append(f"Delivery postcode not recognised as a UK postcode: {raw.delivery_postcode!r}")
    else:
        postcode = raw.delivery_postcode.strip() if raw.delivery_postcode else None

    # DG
    un_number = None
    if raw.un_number:
        digits = re.sub(r"\D", "", raw.un_number)
        un_number = f"UN{digits}" if len(digits) == 4 else raw.un_number
    hazardous = True if un_number else raw.hazardous

    return Enquiry(
        customer_name=raw.customer_name,
        contact_email=raw.contact_email,
        customer_role=_enum(CustomerRole, raw.customer_role, CustomerRole.UNKNOWN),
        direction=direction,
        mode=_enum(Mode, raw.mode, Mode.UNKNOWN),
        service=service,
        origin_country=origin_country,
        origin_place=raw.origin_place,
        destination_country=destination_country,
        origin_port=origin_port,
        origin_port_name=raw.origin_port_name,
        uk_port=uk_port,
        containers=containers,
        cartons=cartons,
        total_volume_cbm=volume,
        total_gross_weight_kg=weight,
        pallets=raw.pallets if raw.pallets and raw.pallets > 0 else None,
        commodity=raw.commodity,
        hazardous=hazardous,
        un_number=un_number,
        special_requirements=special,
        incoterm=incoterm,
        incoterm_place=raw.incoterm_place,
        delivery_required=raw.delivery_required,
        delivery_postcode=postcode,
        delivery_place=raw.delivery_place,
        ready_date=raw.ready_date,
        extraction_notes=notes,
    )


# ---------- 3. Safe defaults from the rate sheet ----------

def apply_defaults(enquiry: Enquiry, rate_book: RateBook | None = None) -> Enquiry:
    """Fill the UK port when the email doesn't name one and the rate sheet makes the choice clear."""
    rb = rate_book or load_rate_book()
    cfg = load_config()
    e = enquiry.model_copy(deep=True)
    if (e.uk_port is None and e.origin_port and e.service is not Service.UNKNOWN
            and e.direction is Direction.IMPORT and e.mode is Mode.SEA):
        types = [c.container_type for c in e.containers]
        options = rb.destinations_for(e.origin_port, e.service, types)
        choice = None
        if cfg["default_uk_port"] in options:
            choice = cfg["default_uk_port"]
        elif len(options) == 1:
            choice = options[0]
        if choice:
            e.uk_port = choice
            e.extraction_notes.append(f"UK port not stated; assumed {choice}")
    return e


def extract_enquiry(text: str, llm: LLMClient | None = None, rate_book: RateBook | None = None) -> Enquiry:
    """Full step 1: LLM extraction -> Python normalisation -> defaults."""
    raw = extract_raw(text, llm)
    return apply_defaults(normalise(raw, rate_book), rate_book)
