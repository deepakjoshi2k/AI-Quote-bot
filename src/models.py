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
    IMPORT = "import"
    EXPORT = "export"
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
    direction: Direction = Direction.UNKNOWN
    mode: Mode = Mode.UNKNOWN
    service: Service = Service.UNKNOWN
    origin_country: str | None = None
    origin_place: str | None = None        # supplier city/district
    origin_port: str | None = None         # UN/LOCODE e.g. CNSHA
    uk_port: str | None = None             # GBFXT / GBSOU / GBLGP
    containers: list[ContainerLine] = Field(default_factory=list)   # FCL
    cartons: list[CartonLine] = Field(default_factory=list)         # LCL detail
    total_volume_cbm: float | None = Field(default=None, ge=0)
    total_gross_weight_kg: float | None = Field(default=None, ge=0)
    pallets: int | None = Field(default=None, gt=0)
    commodity: str | None = None
    hazardous: bool | None = None
    un_number: str | None = None
    incoterm: Incoterm = Incoterm.UNKNOWN
    incoterm_place: str | None = None      # e.g. "Ningbo", "supplier premises"
    delivery_required: bool | None = None
    delivery_postcode: str | None = None
    ready_date: date | None = None
    extraction_notes: list[str] = Field(default_factory=list)


class Triage(BaseModel):
    status: TriageStatus
    reasons: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class ChargeSections(BaseModel):
    """Which quote sections the UK importer pays for. Produced by incoterms.charge_sections()."""
    origin_pickup: bool
    origin_export_clearance: bool
    origin_handling: bool
    ocean_freight: bool
    uk_destination: bool
    uk_import_clearance: bool
    uk_delivery: bool

    def any(self) -> bool:
        """True if there is anything at all to quote."""
        return any(self.model_dump().values())


class QuoteLine(BaseModel):
    section: str        # "Origin" | "Ocean freight" | "UK destination" | "UK customs" | "UK delivery"
    code: str
    description: str
    basis: str
    quantity: float
    unit_rate: float
    amount: float


class Quote(BaseModel):
    lane: str                              # "CNSHA → GBFXT"
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
    is_mock: bool = True
