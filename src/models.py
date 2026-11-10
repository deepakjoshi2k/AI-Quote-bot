"""Pydantic schemas shared across the pipeline (see 04_architecture_and_stack.md, section 3).

Units: kg, cm, CBM, GBP. Nothing in here does maths beyond simple validation.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Service(str, Enum):
    FCL = "fcl"
    LCL = "lcl"
    UNKNOWN = "unknown"


class Direction(str, Enum):
    IMPORT = "import"            # destination in the UK or the EU
    EXPORT = "export"            # origin in the UK/EU, destination outside it
    CROSS_TRADE = "cross_trade"  # neither end in the UK/EU
    UNKNOWN = "unknown"


class CustomerRole(str, Enum):
    BUYER = "buyer"      # the importer / consignee (what the Incoterm table assumes)
    SELLER = "seller"    # the supplier / shipper
    AGENT = "agent"      # another forwarder asking on behalf of a client
    UNKNOWN = "unknown"


class Mode(str, Enum):
    SEA = "sea"
    AIR = "air"
    ROAD = "road"
    UNKNOWN = "unknown"


class Incoterm(str, Enum):
    EXW = "EXW"
    FCA = "FCA"
    FOB = "FOB"
    CFR = "CFR"
    CIF = "CIF"
    DAP = "DAP"
    DDP = "DDP"
    CPT = "CPT"
    CIP = "CIP"
    DPU = "DPU"
    FAS = "FAS"
    UNKNOWN = "UNKNOWN"


ContainerType = Literal["20GP", "40GP", "40HC"]
TriageStatus = Literal["quote", "need_info", "manual_review"]


class ContainerLine(BaseModel):
    container_type: ContainerType
    quantity: int = Field(gt=0)


class CartonLine(BaseModel):
    quantity: int = Field(gt=0)
    length_cm: float = Field(gt=0)
    width_cm: float = Field(gt=0)
    height_cm: float = Field(gt=0)
    weight_kg_each: float | None = Field(default=None, ge=0)


class Enquiry(BaseModel):
    customer_name: str | None = None
    contact_email: str | None = None
    customer_role: CustomerRole = CustomerRole.UNKNOWN
    direction: Direction = Direction.UNKNOWN
    mode: Mode = Mode.UNKNOWN
    service: Service = Service.UNKNOWN
    origin_country: str | None = None      # ISO 2-letter
    origin_place: str | None = None        # supplier city/district
    destination_country: str | None = None # ISO 2-letter; GB for the UK
    origin_port: str | None = None         # UN/LOCODE e.g. CNSHA (only if recognised)
    origin_port_name: str | None = None    # port as written in the email, e.g. "Santos"
    destination_port: str | None = None             # GBFXT / GBSOU / GBLGP
    containers: list[ContainerLine] = Field(default_factory=list)   # FCL
    cartons: list[CartonLine] = Field(default_factory=list)         # LCL detail
    total_volume_cbm: float | None = Field(default=None, ge=0)
    total_gross_weight_kg: float | None = Field(default=None, ge=0)
    pallets: int | None = Field(default=None, gt=0)
    commodity: str | None = None
    hazardous: bool | None = None
    un_number: str | None = None
    special_requirements: list[str] = Field(default_factory=list)  # e.g. "reefer", "tail-lift"
    incoterm: Incoterm = Incoterm.UNKNOWN
    incoterm_place: str | None = None      # e.g. "Ningbo", "supplier premises"
    delivery_required: bool | None = None
    customs_clearance_requested: bool | None = None  # sender asks us to clear import customs
    clearance_only: bool | None = None               # sender wants ONLY customs clearance
    delivery_postcode: str | None = None   # normalised if UK, as written otherwise
    delivery_place: str | None = None      # town / address as written
    ready_date: date | None = None
    extraction_notes: list[str] = Field(default_factory=list)


class TriageFlag(str, Enum):
    """Machine-readable reasons, used by the pipeline to route the enquiry."""
    EXPORT = "export"
    CROSS_TRADE = "cross_trade"
    DESTINATION_NOT_PRICED = "destination_not_priced"   # e.g. EU delivery: no rates in v1
    CUSTOMER_NOT_BUYER = "customer_not_buyer"
    NOT_SEA = "not_sea"
    DANGEROUS_GOODS = "dangerous_goods"
    SPECIAL_CARGO = "special_cargo"
    DDP = "ddp"
    LANE_NOT_IN_SHEET = "lane_not_in_sheet"
    DESTINATION_PORT_NOT_IN_SHEET = "destination_port_not_in_sheet"
    MANUAL_DELIVERY_AREA = "manual_delivery_area"
    HEAVY_CONTAINER = "heavy_container"     # warning only
    LCL_MANUAL = "lcl_manual"               # LCL is quoted by hand for now
    PORT_HEALTH = "port_health"          # informational only, does not block a quote


class Triage(BaseModel):
    status: TriageStatus
    reasons: list[str] = Field(default_factory=list)        # why it's manual_review (blocking only)
    notes: list[str] = Field(default_factory=list)          # notes for staff: never block
    missing_fields: list[str] = Field(default_factory=list)
    flags: list[TriageFlag] = Field(default_factory=list)


class ChargeSections(BaseModel):
    """Which quote sections the UK importer pays for. Produced by incoterms.charge_sections()."""
    origin_pickup: bool
    origin_export_clearance: bool
    origin_handling: bool
    ocean_freight: bool
    destination_handling: bool
    import_clearance: bool
    delivery: bool

    def any(self) -> bool:
        """True if there is anything at all to quote."""
        return any(self.model_dump().values())


class QuoteLine(BaseModel):
    section: str        # "Destination" | "Customs" | "Delivery"
    code: str
    description: str
    basis: str
    quantity: float
    unit_rate: float
    amount: float


class Quote(BaseModel):
    lane: str                              # e.g. "Felixstowe (GBFXT) → SL1 4QP"
    service: Service
    incoterm: Incoterm
    currency: str = "GBP"
    lines: list[QuoteLine]
    subtotal: float
    margin: float
    total: float
    transit_time: str | None = None
    validity_days: int
    assumptions: list[str] = Field(default_factory=list)
    exclusions: list[str]                  # always includes duties & import VAT
    terms: list[str] = Field(default_factory=list)   # tariff terms & conditions printed on the quote
    source: str = ""                       # which rate data priced it
    is_mock: bool = True


# ---------- what the LLM returns (raw, before Python normalisation) ----------
# The LLM only copies what the email says, with units as written. Python converts
# units, maps ports to codes, computes CBM, etc. (rule 1: no maths in the LLM).

class RawContainerLine(BaseModel):
    container_type: str                    # as written: "40ft high cube", "20'", "40HC"
    quantity: int = 1


class RawCartonLine(BaseModel):
    quantity: int
    length: float
    width: float
    height: float
    dimension_unit: Literal["cm", "mm", "m", "in"] = "cm"
    weight_each: float | None = None
    weight_unit: Literal["kg", "lb", "t"] = "kg"


class RawEnquiry(BaseModel):
    customer_name: str | None = None
    contact_email: str | None = None
    customer_role: Literal["buyer", "seller", "agent", "unknown"] = "unknown"
    direction: Literal["import", "export", "cross_trade", "unknown"] = "unknown"
    mode: Literal["sea", "air", "road", "unknown"] = "unknown"
    service: Literal["fcl", "lcl", "unknown"] = "unknown"
    origin_country: str | None = None      # ISO 3166 alpha-2, e.g. "CN"
    origin_place: str | None = None
    destination_country: str | None = None # ISO 3166 alpha-2, "GB" for the UK
    origin_port_name: str | None = None
    destination_port_name: str | None = None
    containers: list[RawContainerLine] = Field(default_factory=list)
    cartons: list[RawCartonLine] = Field(default_factory=list)
    total_volume_cbm: float | None = None  # only if stated in CBM / m3
    total_weight: float | None = None
    weight_unit: Literal["kg", "lb", "t"] = "kg"
    pallets: int | None = None
    commodity: str | None = None
    hazardous: bool | None = None
    un_number: str | None = None
    special_requirements: list[str] = Field(default_factory=list)
    incoterm: str | None = None            # as written, e.g. "FOB"
    incoterm_place: str | None = None
    delivery_required: bool | None = None
    customs_clearance_requested: bool | None = None
    clearance_only: bool | None = None
    delivery_postcode: str | None = None
    delivery_place: str | None = None
    ready_date: date | None = None
    notes: list[str] = Field(default_factory=list)
