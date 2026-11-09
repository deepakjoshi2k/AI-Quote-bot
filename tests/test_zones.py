import pytest

from src.zones import normalise_postcode, postcode_area, zone_for_postcode


@pytest.mark.parametrize("raw, expected", [
    ("SL1 4QP", "SL1 4QP"), ("sl14qp", "SL1 4QP"), ("  b6 7jh ", "B6 7JH"),
    ("EC1A 1BB", "EC1A 1BB"), ("SL1", "SL1"), ("hello", None), ("", None), (None, None), ("12345", None),
])
def test_normalise_postcode(raw, expected):
    assert normalise_postcode(raw) == expected


@pytest.mark.parametrize("pc, area", [("SL1 4QP", "SL"), ("B6 7JH", "B"), ("EC1A 1BB", "EC"), ("W1A 0AX", "W")])
def test_postcode_area(pc, area):
    assert postcode_area(pc) == area


def test_postcode_area_invalid():
    with pytest.raises(ValueError):
        postcode_area("not a postcode")


@pytest.mark.parametrize("pc, zone", [
    ("SL1 4QP", "1"), ("SL3 0HJ", "1"), ("RG2 0QX", "2"), ("LE4 9HA", "2"), ("B6 7JH", "2"),
    ("MK1 1AA", "2"), ("BS2 0XS", "3"), ("M1 1AE", "3"), ("BT1 1AA", "MANUAL"), ("IV1 1AA", "MANUAL"),
    ("JE2 3AB", "MANUAL"), ("IM1 1AA", "MANUAL"),
])
def test_zone_for_postcode(pc, zone):
    assert zone_for_postcode(pc) == zone
