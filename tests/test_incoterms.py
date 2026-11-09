import pandas as pd
import pytest

from src.config import DATA_DIR
from src.incoterms import charge_sections, delivery_ignored, incoterm_assumptions
from src.models import ChargeSections, Incoterm

T, F = True, False
FIELDS = [
    "origin_pickup", "origin_export_clearance", "origin_handling", "ocean_freight",
    "uk_destination", "uk_import_clearance", "uk_delivery",
]

# Expected sections WITH delivery requested (03_freight_domain_reference.md, section 4).
EXPECTED_WITH_DELIVERY = {
    "EXW": (T, T, T, T, T, T, T),
    "FCA": (T, F, T, T, T, T, T),
    "FOB": (F, F, F, T, T, T, T),
    "CFR": (F, F, F, F, T, T, T),
    "CIF": (F, F, F, F, T, T, T),
    "DAP": (F, F, F, F, F, T, F),
    "DDP": (F, F, F, F, F, F, F),
}


def _as_tuple(s: ChargeSections) -> tuple:
    return tuple(getattr(s, f) for f in FIELDS)


@pytest.mark.parametrize("term, expected", EXPECTED_WITH_DELIVERY.items())
def test_sections_with_delivery(term, expected):
    assert _as_tuple(charge_sections(Incoterm(term), delivery_required=True)) == expected


@pytest.mark.parametrize("term, expected", EXPECTED_WITH_DELIVERY.items())
@pytest.mark.parametrize("delivery", [False, None])
def test_sections_without_delivery(term, expected, delivery):
    """Everything is the same except UK delivery is never charged."""
    result = charge_sections(Incoterm(term), delivery_required=delivery)
    assert _as_tuple(result) == expected[:-1] + (False,)


def test_all_seven_incoterms_covered():
    assert set(EXPECTED_WITH_DELIVERY) == {t.value for t in Incoterm if t is not Incoterm.UNKNOWN}


def test_unknown_incoterm_raises():
    with pytest.raises(ValueError):
        charge_sections(Incoterm.UNKNOWN, True)


def test_accepts_lowercase_string():
    assert charge_sections("fob", True) == charge_sections(Incoterm.FOB, True)


def test_invalid_string_raises():
    with pytest.raises(ValueError):
        charge_sections("XYZ", True)


def test_ddp_has_nothing_to_quote():
    assert not charge_sections(Incoterm.DDP, True).any()
    assert charge_sections(Incoterm.DAP, False).any()


# ---------- sample enquiries ----------

def test_sample_I01_fob_no_origin():
    s = charge_sections(Incoterm.FOB, True)
    assert s.ocean_freight and s.uk_delivery and not s.origin_pickup


def test_sample_I03_cif_no_freight():
    s = charge_sections(Incoterm.CIF, True)
    assert not s.ocean_freight and s.uk_destination


def test_sample_I07_dap_clearance_only():
    s = charge_sections(Incoterm.DAP, True)
    assert s.uk_import_clearance
    assert not (s.ocean_freight or s.uk_destination or s.uk_delivery)
    assert delivery_ignored(Incoterm.DAP, True)


# ---------- assumptions ----------

@pytest.mark.parametrize("term", ["FCA", "CFR", "CIF", "DAP"])
def test_assumptions_present(term):
    assert incoterm_assumptions(term)


@pytest.mark.parametrize("term", ["EXW", "FOB"])
def test_no_assumptions(term):
    assert incoterm_assumptions(term) == []


# ---------- keep the rate sheet consistent with the rules ----------

ORIGIN_CODE_TO_SECTION = {
    "PICKUP": "origin_pickup",
    "EXPCLR": "origin_export_clearance",
    "OCFS": "origin_handling",
    "OTHC": "origin_handling",
    "ODOC": "origin_handling",
}


def test_origin_charges_csv_matches_incoterm_rules():
    df = pd.read_csv(DATA_DIR / "origin_charges.csv")
    for _, row in df.iterrows():
        section = ORIGIN_CODE_TO_SECTION[row["code"]]
        csv_terms = set(row["buyer_pays_when_incoterm"].split(";"))
        rule_terms = {
            t.value for t in Incoterm
            if t is not Incoterm.UNKNOWN and getattr(charge_sections(t, False), section)
        }
        assert csv_terms == rule_terms, f"{row['origin_port']} {row['code']}"
