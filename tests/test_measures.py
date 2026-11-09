import pytest

from src.measures import (
    cbm_from_cartons, estimate_pallets, inches_to_cm, kg_to_tonnes, lb_to_kg,
    suggest_service, weight_from_cartons, wm,
)
from src.models import CartonLine, Service


# ---------- conversions ----------

def test_inches_to_cm():
    assert inches_to_cm(10) == pytest.approx(25.4)


def test_lb_to_kg():
    assert lb_to_kg(100) == pytest.approx(45.3592)


def test_kg_to_tonnes():
    assert kg_to_tonnes(1450) == pytest.approx(1.45)


@pytest.mark.parametrize("fn", [inches_to_cm, lb_to_kg, kg_to_tonnes])
def test_conversions_reject_negative(fn):
    with pytest.raises(ValueError):
        fn(-1)


# ---------- cartons ----------

def test_cbm_from_cartons_sample_I11():
    cartons = [CartonLine(quantity=40, length_cm=60, width_cm=40, height_cm=50, weight_kg_each=18)]
    assert cbm_from_cartons(cartons) == pytest.approx(4.8)
    assert weight_from_cartons(cartons) == pytest.approx(720)


def test_cbm_from_multiple_carton_lines():
    cartons = [
        CartonLine(quantity=10, length_cm=100, width_cm=100, height_cm=100),  # 10 CBM
        CartonLine(quantity=2, length_cm=50, width_cm=50, height_cm=40),      # 0.2 CBM
    ]
    assert cbm_from_cartons(cartons) == pytest.approx(10.2)


def test_cbm_from_no_cartons_is_zero():
    assert cbm_from_cartons([]) == 0


def test_weight_from_cartons_none_if_any_weight_missing():
    cartons = [
        CartonLine(quantity=1, length_cm=10, width_cm=10, height_cm=10, weight_kg_each=5),
        CartonLine(quantity=1, length_cm=10, width_cm=10, height_cm=10),
    ]
    assert weight_from_cartons(cartons) is None


def test_carton_rejects_zero_dimension():
    with pytest.raises(ValueError):
        CartonLine(quantity=1, length_cm=0, width_cm=10, height_cm=10)


# ---------- W/M ----------

@pytest.mark.parametrize(
    "weight_kg, cbm, expected",
    [
        (1450, 4.8, 4.8),    # I02: volume wins
        (720, 4.8, 4.8),     # I11
        (210, 0.6, 1.0),     # I10: below minimum -> 1 W/M
        (3000, 2.0, 3.0),    # weight wins
        (1000, 1.0, 1.0),    # tie
        (0, 0, 1.0),         # empty -> minimum
    ],
)
def test_wm(weight_kg, cbm, expected):
    assert wm(weight_kg, cbm) == pytest.approx(expected)


def test_wm_custom_minimum():
    assert wm(100, 0.5, minimum=2.0) == 2.0


def test_wm_rejects_negative():
    with pytest.raises(ValueError):
        wm(-1, 1)


# ---------- pallets ----------

@pytest.mark.parametrize(
    "cbm, expected",
    [(0.6, 1), (1.6, 1), (1.61, 2), (3.2, 2), (4.8, 3), (0, 1)],
)
def test_estimate_pallets(cbm, expected):
    assert estimate_pallets(cbm) == expected


def test_estimate_pallets_bad_divisor():
    with pytest.raises(ValueError):
        estimate_pallets(1, cbm_per_pallet=0)


# ---------- FCL / LCL suggestion ----------

@pytest.mark.parametrize(
    "cbm, expected",
    [(4.8, Service.LCL), (12.99, Service.LCL), (13, Service.UNKNOWN),
     (14, Service.UNKNOWN), (15, Service.UNKNOWN), (15.01, Service.FCL), (60, Service.FCL)],
)
def test_suggest_service(cbm, expected):
    assert suggest_service(cbm) == expected
