"""Triage for the import quote agent: destination services, charged to whoever emails (src/scope.py)."""
import pytest

from src.extract import extract_enquiry, normalise
from src.models import ContainerLine, Enquiry, Incoterm, RawEnquiry, Service, TriageFlag
from src.scope import describe_scope, quote_scope
from src.triage import triage
from src.zones import zone_for_address
from tests.conftest import SAMPLE_IDS, sample_text


# ---------- all sample enquiries, end to end with the fake LLM ----------

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
    if "zone" in exp:
        assert zone_for_address(e.destination_country, e.delivery_postcode) == exp["zone"]
    for phrase in exp.get("missing_contains", []):
        assert any(phrase.lower() in m.lower() for m in t.missing_fields), f"missing '{phrase}' in {t.missing_fields}"


# ---------- quote scope: what goes in the quote ----------

def _fcl(**overrides) -> Enquiry:
    """FCL import to Felixstowe, delivered in Slough: quotable."""
    base = dict(direction="import", mode="sea", service=Service.FCL, destination_country="GB",
                destination_port="GBFXT", containers=[ContainerLine(container_type="40HC", quantity=1)],
                commodity="furniture", incoterm=Incoterm.FOB, delivery_required=True, delivery_postcode="SL1 4QP")
    base.update(overrides)
    return Enquiry(**base)


def test_baseline_is_quote():
    t = triage(_fcl())
    assert t.status == "quote" and t.missing_fields == []


def test_scope_default_is_terminal_handling_and_delivery():
    assert describe_scope(quote_scope(_fcl())) == ["Destination terminal handling + release fee",
                                                   "Delivery to the final address"]


def test_scope_never_includes_freight_or_origin():
    s = quote_scope(_fcl(incoterm=Incoterm.EXW))
    assert not (s.ocean_freight or s.origin_pickup or s.origin_handling or s.origin_export_clearance)


def test_clearance_only_when_asked():
    assert not quote_scope(_fcl()).import_clearance
    assert quote_scope(_fcl(customs_clearance_requested=True)).import_clearance


def test_clearance_only_enquiry():
    e = _fcl(clearance_only=True, customs_clearance_requested=True, delivery_required=False, destination_port=None)
    assert describe_scope(quote_scope(e)) == ["Import customs clearance"]
    assert triage(e).status == "quote"          # no port or delivery needed for a customs-only quote


def test_no_delivery_when_not_asked():
    assert not quote_scope(_fcl(delivery_required=False)).delivery


# ---------- same quote whatever the Incoterm (sender always pays) ----------

@pytest.mark.parametrize("term", ["EXW", "FCA", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS"])
def test_every_incoterm_is_quotable(term):
    assert triage(_fcl(incoterm=Incoterm(term))).status == "quote"


@pytest.mark.parametrize("term", ["DAP", "DPU"])
def test_dap_dpu_clearance_note(term):
    t = triage(_fcl(incoterm=Incoterm(term)))
    assert any("Email the importer" in r for r in t.reasons)


def test_dap_note_not_added_when_clearance_requested():
    t = triage(_fcl(incoterm=Incoterm.DAP, customs_clearance_requested=True))
    assert not any("Email the importer" in r for r in t.reasons)


def test_ddp_quoted_with_duty_note():
    t = triage(_fcl(incoterm=Incoterm.DDP))
    assert t.status == "quote" and any("DDP" in r for r in t.reasons)


def test_missing_incoterm_does_not_block():
    t = triage(_fcl(incoterm=Incoterm.UNKNOWN))
    assert t.status == "quote" and any("Incoterm" in m for m in t.missing_fields)


def test_role_is_ignored():
    for role in ["buyer", "seller", "agent", "unknown"]:
        assert triage(_fcl(customer_role=role, incoterm=Incoterm.DAP)).status == "quote"


# ---------- not relevant / not asked ----------

def test_no_questions_about_origin_ready_date_or_hazard():
    t = triage(_fcl(commodity=None))
    joined = " ".join(t.missing_fields).lower()
    assert "origin" not in joined and "ready" not in joined and "hazard" not in joined


def test_hazard_not_stated_is_non_hazardous():
    assert triage(_fcl(hazardous=None)).status == "quote"


# ---------- manual review ----------

def test_lcl_is_manual():
    t = triage(_fcl(service=Service.LCL, containers=[], total_volume_cbm=3))
    assert t.status == "manual_review" and TriageFlag.LCL_MANUAL in t.flags


def test_dg_keyword_without_flag():
    assert TriageFlag.DANGEROUS_GOODS in triage(_fcl(commodity="aerosol paint cans")).flags


def test_car_parts_are_not_vehicles():
    assert triage(_fcl(commodity="car parts")).status == "quote"


def test_reefer_is_special():
    assert TriageFlag.SPECIAL_CARGO in triage(_fcl(special_requirements=["reefer"])).flags


def test_northern_ireland_delivery_is_manual():
    t = triage(_fcl(delivery_postcode="BT1 1AA"))
    assert t.status == "manual_review" and TriageFlag.MANUAL_DELIVERY_AREA in t.flags


def test_ni_postcode_ignored_when_no_delivery():
    assert triage(_fcl(delivery_postcode="BT1 1AA", delivery_required=False)).status == "quote"


def test_port_not_in_sheet():
    assert TriageFlag.DESTINATION_PORT_NOT_IN_SHEET in triage(_fcl(destination_port="GBLIV")).flags


def test_export_and_air_routed():
    assert triage(_fcl(direction="export", origin_country="GB", destination_country="AE")).flags == [TriageFlag.EXPORT]
    assert triage(_fcl(mode="air")).flags == [TriageFlag.NOT_SEA]


def test_air_dg_still_flagged():
    assert set(triage(_fcl(mode="air", commodity="lithium batteries")).flags) == {TriageFlag.NOT_SEA,
                                                                                TriageFlag.DANGEROUS_GOODS}


def test_cross_trade_routed():
    assert triage(_fcl(direction="cross_trade", origin_country="CN", destination_country="US")).flags == [
        TriageFlag.CROSS_TRADE]


def test_home_country_without_rates_routed(monkeypatch):
    from src.config import load_config
    cfg = dict(load_config()); cfg["home_countries"] = cfg["home_countries"] + ["NO"]
    monkeypatch.setattr("src.triage.load_config", lambda: cfg)
    t = triage(_fcl(destination_country="NO", delivery_postcode="0150"))
    assert t.flags == [TriageFlag.DESTINATION_NOT_PRICED]


# ---------- warnings never block ----------

def test_heavy_container_does_not_block_quote():
    e = _fcl(total_gross_weight_kg=24000, containers=[ContainerLine(container_type="20GP", quantity=1)])
    t = triage(e)
    assert t.status == "quote" and TriageFlag.HEAVY_CONTAINER in t.flags


def test_port_health_note(fake_llm):
    t = triage(extract_enquiry(sample_text("I06"), llm=fake_llm))
    assert t.status == "quote" and any("port health" in r for r in t.reasons)


# ---------- need_info ----------

def test_port_of_arrival_asked():
    t = triage(_fcl(destination_port=None))
    assert t.status == "need_info" and any("port of arrival" in m for m in t.missing_fields)


def test_container_size_asked():
    t = triage(_fcl(containers=[]))
    assert t.status == "need_info" and any("container size" in m for m in t.missing_fields)


def test_delivery_requested_without_postcode():
    t = triage(_fcl(delivery_postcode=None))
    assert t.status == "need_info" and any("postcode" in m for m in t.missing_fields)


def test_delivery_not_mentioned_is_side_question():
    t = triage(_fcl(delivery_required=None, delivery_postcode=None))
    assert t.status == "quote" and any("delivery" in m for m in t.missing_fields)


# ---------- EU ----------

def _eu(**overrides) -> Enquiry:
    base = dict(destination_country="DE", destination_port="DEHAM", delivery_postcode="20095")
    base.update(overrides)
    return _fcl(**base)


def test_eu_import_quotable_with_note():
    t = triage(_eu())
    assert t.status == "quote" and any("EU import" in r for r in t.reasons)


def test_eu_island_is_manual():
    t = triage(_eu(destination_country="FR", destination_port="FRLEH", delivery_postcode="20000"))
    assert TriageFlag.MANUAL_DELIVERY_AREA in t.flags


def test_eu_port_without_delivery_rate_to_country():
    t = triage(_eu(destination_port="NLRTM"))
    assert t.status == "manual_review" and TriageFlag.LANE_NOT_IN_SHEET in t.flags


def test_eu_port_not_priced():
    assert TriageFlag.DESTINATION_PORT_NOT_IN_SHEET in triage(_eu(destination_port="DEBRV")).flags


def test_eu_port_name_sets_country():
    e = normalise(RawEnquiry(direction="import", mode="sea", destination_port_name="Rotterdam"))
    assert e.destination_country == "NL" and e.destination_port == "NLRTM"


# ---------- the France DAP email (I12) ----------

def test_i12_france_dap(fake_llm):
    e = extract_enquiry(sample_text("I12"), llm=fake_llm)
    t = triage(e)
    assert e.destination_port is None and e.origin_country is None   # nothing assumed
    assert t.status == "need_info"
    assert t.missing_fields == ["port of arrival (discharge port)"]
    assert any("Email the importer" in r for r in t.reasons)
    assert TriageFlag.HEAVY_CONTAINER in t.flags


# ---------- clearance flags from the email ----------

def test_clearance_only_from_email(fake_llm):
    e = extract_enquiry(sample_text("I07"), llm=fake_llm)
    assert e.clearance_only and e.customs_clearance_requested and e.delivery_required is False
    assert describe_scope(quote_scope(e)) == ["Import customs clearance"]


# ---------- readable report ----------

def test_report_relevance(fake_llm):
    from src.report import format_result
    e = extract_enquiry(sample_text("I12"), llm=fake_llm)
    out = format_result(e, triage(e))
    assert "Not relevant" in out and "No (not stated)" in out and "FRLEH" not in out
    assert "QUOTE WILL INCLUDE" in out
