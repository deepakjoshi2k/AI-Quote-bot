"""Step 3: unit conversion, CBM, W/M, pallet estimate, FCL/LCL suggestion.

Pure, deterministic functions. No rounding here: round to 2 dp only at the end (quote.py).
"""
from __future__ import annotations

import math
from typing import Iterable

from src.models import CartonLine, Service

CM_PER_INCH = 2.54
KG_PER_LB = 0.453592
CM3_PER_CBM = 1_000_000
KG_PER_TONNE = 1_000

# Guards against float noise, e.g. 3.2 / 1.6 = 2.0000000000000004 -> ceil would give 3.
_EPS_DP = 9


def _check_non_negative(**values: float) -> None:
    for name, v in values.items():
        if v is None or v < 0:
            raise ValueError(f"{name} must be a non-negative number, got {v!r}")


# ---------- unit conversion ----------

def inches_to_cm(inches: float) -> float:
    _check_non_negative(inches=inches)
    return inches * CM_PER_INCH


def lb_to_kg(lb: float) -> float:
    _check_non_negative(lb=lb)
    return lb * KG_PER_LB


def kg_to_tonnes(kg: float) -> float:
    _check_non_negative(kg=kg)
    return kg / KG_PER_TONNE


def cm3_to_cbm(cm3: float) -> float:
    _check_non_negative(cm3=cm3)
    return cm3 / CM3_PER_CBM


# ---------- volume & weight ----------

def cbm_from_cartons(cartons: Iterable[CartonLine]) -> float:
    """Sum of L x W x H (cm) x quantity / 1,000,000."""
    total_cm3 = sum(c.length_cm * c.width_cm * c.height_cm * c.quantity for c in cartons)
    return cm3_to_cbm(total_cm3)


def weight_from_cartons(cartons: Iterable[CartonLine]) -> float | None:
    """Total gross kg, or None if any carton line has no weight (don't guess)."""
    cartons = list(cartons)
    if not cartons or any(c.weight_kg_each is None for c in cartons):
        return None
    return sum(c.weight_kg_each * c.quantity for c in cartons)


def wm(weight_kg: float, cbm: float, minimum: float = 1.0) -> float:
    """LCL revenue tonnes: max(tonnes, CBM), never below the minimum."""
    _check_non_negative(weight_kg=weight_kg, cbm=cbm, minimum=minimum)
    return max(kg_to_tonnes(weight_kg), cbm, minimum)


def estimate_pallets(cbm: float, cbm_per_pallet: float = 1.6) -> int:
    """ceil(CBM / CBM-per-pallet), at least 1. Only used when the customer gives no pallet count."""
    _check_non_negative(cbm=cbm)
    if cbm_per_pallet <= 0:
        raise ValueError("cbm_per_pallet must be > 0")
    return max(1, math.ceil(round(cbm / cbm_per_pallet, _EPS_DP)))


def suggest_service(cbm: float, lcl_max_cbm: float = 13.0, fcl_min_cbm: float = 15.0) -> Service:
    """LCL below lcl_max, FCL above fcl_min, UNKNOWN in between (quote should say both could work)."""
    _check_non_negative(cbm=cbm)
    if cbm < lcl_max_cbm:
        return Service.LCL
    if cbm > fcl_min_cbm:
        return Service.FCL
    return Service.UNKNOWN
