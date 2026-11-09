import pytest

from src.extract import apply_defaults, extract_enquiry, normalise
from src.models import ContainerLine, Enquiry, Incoterm, RawEnquiry, Service, TriageFlag
from src.triage import triage
from src.zones import zone_for_address
from tests.conftest import SAMPLE_IDS, sample_text


# ---------- all 11 sample enquiries, end to end with the fake LLM ----------

@pytest.mark.parametrize("sid", SAMPLE_IDS)
def test_sample_enquiry(sid, fake_llm, expected):
    exp = expected[sid]
    e = extract_enquiry(sample_text(sid), llm=fake_llm)
    t = triage(e)

    assert t.status == exp["status"], t.reasons + t.missing_fields
    assert sorted(f.value for f in t.flags) == sorted(exp["flags"])

    for field in ["direction", "mode", "service", "origin_port", "destination_port", "incoterm", "un_number",
                  "delivery_required", "delivery_postcode", "pallets", "hazardous", "origin_country",
                  "destination_country"]:
        if field in exp:
            value = getattr(e, field)
            assert (value.value if hasattr(value, "value") else value) == exp[field], field
    for field in ["total_volume_cbm", "total_gross_weight_kg"]:
        if field in exp:
            assert getattr(e, field) == pytest.approx(exp[field]), field
    if "containers" in exp:
        assert [c.model_dump() for c in e.containers] == exp["containers"]
    if "ready_date" in exp:
        assert e.ready_date.isoformat() == exp["ready_date"]
    if "zone" in exp:
        assert zone_for_address(e.destination_country, e.delivery_postcode) == exp["zone"]
    for phrase in exp.get("missing_contains", []):
        assert any(phrase.lower() in m.lower() for m in t.missing_fields), f"missing '{phrase}' in {t.missing_fields}"


def test_quote_has_no_missing_required(fake_llm):
    t = triage(extract_enquiry(sample_text("I01"), llm=fake_llm))
    assert t.status == "quote" and t.missing_fields == []


def test_dg_reason_mentions_sds(fake_llm):
    t = triage(extract_enquiry(sample_text("I05"), llm=fake_llm))
    assert any("SDS" in r for r in t.reasons)


def test_lane_reason_names_lane(fake_llm):
    t = triage(extract_enquiry(sample_text("I06"), llm=fake_llm))
    assert any("BRSSZ" in r for r in t.reasons)
    assert any("port health" in r for r in t.reasons)


# ---------- focused rules ----------

def _quotable(**overrides) -> Enquiry:
    """An LCL FOB Shanghai -> Felixstowe enquiry that triages to 'quote'."""
    base = dict(direction="import", mode="sea", service=Service.LCL, origin_port="CNSHA", destination_port="GBFXT",
                total_volume_cbm=2.0, total_gross_weight_kg=500, commodity="toys", hazardous=False,
                incoterm=Incoterm.FOB, delivery_required=True, delivery_postcode="SL1 4QP",
                ready_date="2026-12-01")
    base.update(overrides)
    return Enquiry(**base)


def test_baseline_is_quote():
    assert triage(_quotable()).status == "quote"


def test_northern_ireland_delivery_is_manual():
    t = triage(_quotable(delivery_postcode="BT1 1AA"))
    assert t.status == "manual_review" and TriageFlag.MANUAL_DELIVERY_AREA in t.flags


def test_ni_postcode_ignored_when_no_delivery():
    assert triage(_quotable(delivery_postcode="BT1 1AA", delivery_required=False)).status == "quote"


def test_ddp_is_manual():
    assert TriageFlag.DDP in triage(_quotable(incoterm=Incoterm.DDP)).flags


def test_dg_keyword_without_flag():
    t = triage(_quotable(commodity="aerosol paint cans", hazardous=None))
    assert TriageFlag.DANGEROUS_GOODS in t.flags


def test_car_parts_are_not_vehicles():
    assert triage(_quotable(commodity="car parts")).status == "quote"


def test_reefer_is_special():
    t = triage(_quotable(special_requirements=["reefer"]))
    assert TriageFlag.SPECIAL_CARGO in t.flags


def test_heavy_20gp_flagged():
    e = _quotable(service=Service.FCL, origin_port="CNNGB", total_gross_weight_kg=24000,
                  containers=[ContainerLine(container_type="20GP", quantity=1)])
    assert TriageFlag.HEAVY_CONTAINER in triage(e).flags


def test_container_type_not_in_sheet():
    e = _quotable(service=Service.FCL, containers=[ContainerLine(container_type="40GP", quantity=1)])
    t = triage(e)  # Shanghai has no 40GP rate
    assert t.status == "manual_review" and TriageFlag.LANE_NOT_IN_SHEET in t.flags


def test_destination_port_not_in_sheet():
    t = triage(_quotable(destination_port="GBLIV"))
    assert TriageFlag.DESTINATION_PORT_NOT_IN_SHEET in t.flags


def test_unsupported_incoterm_goes_manual():
    e = apply_defaults(normalise(RawEnquiry(direction="import", mode="sea", service="lcl", origin_port_name="Shanghai",
                                            total_volume_cbm=2, total_weight=500, commodity="toys", incoterm="CPT")))
    assert triage(e).status == "manual_review"


def test_lcl_missing_weight():
    t = triage(_quotable(total_gross_weight_kg=None))
    assert t.status == "need_info" and any("weight" in m for m in t.missing_fields)


def test_mid_volume_asks_fcl_or_lcl():
    t = triage(_quotable(service=Service.UNKNOWN, total_volume_cbm=14))
    assert any("both could work" in m for m in t.missing_fields)


def test_missing_incoterm_asks():
    t = triage(_quotable(incoterm=Incoterm.UNKNOWN))
    assert t.status == "need_info" and any("Incoterm" in m for m in t.missing_fields)


def test_delivery_requested_without_postcode():
    t = triage(_quotable(delivery_postcode=None))
    assert t.status == "need_info" and "delivery postcode" in t.missing_fields


def test_optional_fields_dont_block_quote():
    assert triage(_quotable(ready_date=None, hazardous=None, delivery_required=False)).status == "quote"


def test_dap_customs_only_needs_no_cargo_size():
    e = _quotable(incoterm=Incoterm.DAP, total_volume_cbm=None, total_gross_weight_kg=None, destination_port=None,
                  origin_port=None, service=Service.UNKNOWN, delivery_required=False)
    assert triage(e).status == "quote"


# ---------- Phase 3.1: direction from countries, routing, customer role ----------

def test_air_enquiry_only_routes_to_air_team(fake_llm):
    t = triage(extract_enquiry(sample_text("I08"), llm=fake_llm))
    assert t.flags == [TriageFlag.NOT_SEA]   # no noise about Heathrow not being a sea port


def test_air_dg_still_flagged():
    e = _quotable(mode="air", commodity="lithium batteries")
    assert set(triage(e).flags) == {TriageFlag.NOT_SEA, TriageFlag.DANGEROUS_GOODS}


def test_i12_agent_asking_for_eu_dap_is_manual(fake_llm):
    t = triage(extract_enquiry(sample_text("I12"), llm=fake_llm))
    assert TriageFlag.CUSTOMER_NOT_BUYER in t.flags
    assert any("agent" in r for r in t.reasons)


# ---------- EU destinations ----------

def _eu(**overrides) -> Enquiry:
    """LCL FOB Shanghai -> Hamburg, delivered in Germany: should be quotable."""
    base = dict(destination_country="DE", destination_port="DEHAM", delivery_postcode="20095")
    base.update(overrides)
    return _quotable(**base)


def test_eu_import_quotable():
    t = triage(_eu())
    assert t.status == "quote", t.reasons
    assert any("EU EORI" in r for r in t.reasons)


def test_eu_default_port_filled():
    raw = RawEnquiry(direction="import", mode="sea", service="lcl", origin_port_name="Shanghai",
                     destination_country="FR", delivery_postcode="51300", total_volume_cbm=2, total_weight=400,
                     commodity="toys", incoterm="FOB", delivery_required=True, hazardous=False, customer_role="buyer")
    e = apply_defaults(normalise(raw))
    assert e.destination_port == "FRLEH"
    assert any("staff to confirm" in n for n in e.extraction_notes)
    assert triage(e).status == "quote"


def test_eu_island_is_manual():
    t = triage(_eu(destination_country="FR", destination_port="FRLEH", delivery_postcode="20000"))
    assert TriageFlag.MANUAL_DELIVERY_AREA in t.flags


def test_eu_port_without_delivery_rate_to_country():
    t = triage(_eu(destination_port="NLRTM"))   # Rotterdam -> Germany delivery not in the sheet
    assert t.status == "manual_review" and TriageFlag.LANE_NOT_IN_SHEET in t.flags


def test_eu_port_not_priced():
    t = triage(_eu(destination_port="DEBRV"))
    assert TriageFlag.DESTINATION_PORT_NOT_IN_SHEET in t.flags


def test_eu_missing_postcode_asks():
    t = triage(_eu(delivery_postcode=None))
    assert t.status == "need_info" and "delivery postcode" in t.missing_fields


def test_eu_port_name_sets_country():
    e = normalise(RawEnquiry(direction="import", mode="sea", destination_port_name="Rotterdam"))
    assert e.destination_country == "NL" and e.destination_port == "NLRTM"


def test_home_country_without_rates_routed(monkeypatch):
    from src.config import load_config
    cfg = dict(load_config()); cfg["home_countries"] = cfg["home_countries"] + ["NO"]
    monkeypatch.setattr("src.triage.load_config", lambda: cfg)
    t = triage(_quotable(destination_country="NO", delivery_postcode="0150"))
    assert t.flags == [TriageFlag.DESTINATION_NOT_PRICED]


def test_seller_asking_is_manual():
    t = triage(_quotable(customer_role="seller"))
    assert t.status == "manual_review" and TriageFlag.CUSTOMER_NOT_BUYER in t.flags


def test_unknown_role_treated_as_importer():
    assert triage(_quotable(customer_role="unknown")).status == "quote"


def test_cross_trade_routed():
    e = _quotable(direction="cross_trade", origin_country="CN", destination_country="US")
    assert triage(e).flags == [TriageFlag.CROSS_TRADE]


# ---------- seller-side cue that doesn't depend on the model ----------

@pytest.mark.parametrize("role", ["buyer", "unknown"])
def test_dap_with_delivery_request_is_seller_side(role):
    t = triage(_quotable(incoterm=Incoterm.DAP, delivery_required=True, customer_role=role))
    assert t.status == "manual_review" and TriageFlag.CUSTOMER_NOT_BUYER in t.flags
    assert any("supplier arranges delivery" in r for r in t.reasons)


def test_dap_clearance_only_still_quotes():
    # I07 pattern: DAP, importer only wants customs clearance
    assert triage(_quotable(incoterm=Incoterm.DAP, delivery_required=False)).status == "quote"


def test_i12_caught_even_when_model_says_buyer(fake_llm):
    t = triage(extract_enquiry(sample_text("I12"), llm=fake_llm))
    assert TriageFlag.CUSTOMER_NOT_BUYER in t.flags
