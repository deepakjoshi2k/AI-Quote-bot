"""Step 1: email text -> Enquiry.

Two halves, kept separate on purpose:
  1. extract_raw()  - the LLM copies what the email says into RawEnquiry (no maths).
  2. normalise()    - Python converts units, maps ports to codes, computes CBM, validates.
  3. apply_defaults() is a no-op: nothing is assumed (see its docstring).
Only step 1 touches the LLM, so steps 2-3 are fully unit-tested.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from src.config import load_config
from src.llm import LLMClient, get_llm
from src.measures import cbm_from_cartons, to_cm, to_kg, weight_from_cartons
from src.models import (
    CartonLine, ContainerLine, CustomerRole, Direction, Enquiry, Incoterm, Mode, RawEnquiry, Service,
)
from src.rates import RateBook, load_rate_book
from src.zones import normalise_postcode

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract.md"
SUPPORTED_INCOTERMS = {"EXW", "FCA", "FOB", "CFR", "CIF", "DAP", "DDP", "CPT", "CIP", "DPU", "FAS"}
KNOWN_INCOTERMS = SUPPORTED_INCOTERMS


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

_SPECIAL_CONTAINER = r"reefer|\brf\b|\brh\b|open ?top|\bot\b|flat ?rack|\bfr\b|tank"


def is_special_container(raw: str) -> bool:
    return bool(re.search(_SPECIAL_CONTAINER, raw.lower().replace("-", " ")))


def normalise_container_type(raw: str) -> str | None:
    """'40ft high cube' -> '40HC', "20'" -> '20GP'. Specials (reefer, open top...) or 45ft -> None."""
    s = raw.lower().replace("-", " ")
    if re.search(_SPECIAL_CONTAINER + r"|45", s):
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
    # Destination not written: don't assume one. Only a written UK/EU origin + "export" is clear enough.
    if llm_direction == "export" and origin in home:
        return Direction.EXPORT, None
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
    container_size_unknown = False
    for c in raw.containers:
        ctype = normalise_container_type(c.container_type)
        if ctype is None and not re.search(r"\d", c.container_type) and not is_special_container(c.container_type):
            # "a container", "FCL", "full container": size not given -> ask, don't reject
            container_size_unknown = True
            notes.append(f"Container size not stated ({c.container_type!r})")
        elif ctype is None:
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
    destination_port = rb.port_code(raw.destination_port_name)

    # Service
    if containers or container_size_unknown:
        service = Service.FCL
    elif raw.service in ("lcl", "fcl"):
        service = Service(raw.service)
    else:
        service = Service.UNKNOWN

    # Countries -> direction (Python decides, the LLM only suggests)
    origin_country = _country(raw.origin_country) or (origin_port[:2] if origin_port else None)
    destination_country = _country(raw.destination_country)
    uk_postcode = normalise_postcode(raw.delivery_postcode)
    if destination_country is None and raw.direction != "export" and destination_port:
        destination_country = destination_port[:2]
        notes.append(f"Destination country taken as {destination_country} from the arrival port")
    elif destination_country is None and uk_postcode and " " in uk_postcode:
        destination_country = "GB"
        notes.append("Destination taken as GB from the UK postcode")
    direction, dir_note = decide_direction(origin_country, destination_country, raw.direction,
                                           set(cfg["home_countries"]))
    if dir_note:
        notes.append(dir_note)
    if raw.direction not in ("unknown", direction.value) and direction is not Direction.UNKNOWN:
        notes.append(f"Model said '{raw.direction}', countries say '{direction.value}': using '{direction.value}'")

    # Arrival port: must be in the destination country; unknown names only matter for sea imports
    if destination_port and destination_country and not destination_port.startswith(destination_country):
        notes.append(f"Arrival port {destination_port} is outside the delivery country {destination_country}")
    if (raw.destination_port_name and not destination_port and raw.mode == "sea"
            and direction is Direction.IMPORT and not rb.port_code(raw.destination_port_name)):
        special.append(f"unsupported arrival port: {raw.destination_port_name}")

    # Postcode: UK format check for the UK, cleaned prefix for the EU, as written elsewhere
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
        destination_port=destination_port,
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
        delivery_required=False if raw.clearance_only else raw.delivery_required,
        customs_clearance_requested=True if raw.clearance_only else raw.customs_clearance_requested,
        clearance_only=raw.clearance_only,
        port_advice_requested=raw.port_advice_requested,
        delivery_postcode=postcode,
        delivery_place=raw.delivery_place,
        ready_date=raw.ready_date,
        extraction_notes=notes,
    )


# ---------- 3. Defaults (none for now) ----------

def apply_defaults(enquiry: Enquiry, rate_book: RateBook | None = None) -> Enquiry:
    """No defaults: nothing is filled in that the email doesn't say.

    Placeholder for Lincoln Cargo's routing table (e.g. "France -> Le Havre"): when we have it,
    an estimated port can be added here, clearly labelled, while still asking the sender.
    """
    return enquiry


def extract_enquiry(text: str, llm: LLMClient | None = None, rate_book: RateBook | None = None) -> Enquiry:
    """Full step 1: LLM extraction -> Python normalisation -> defaults."""
    raw = extract_raw(text, llm)
    return apply_defaults(normalise(raw, rate_book), rate_book)
