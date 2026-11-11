"""Pricing tests. Expected totals are worked out by hand from the MOCK sheets (see comments)."""
import pytest

from src.models import ContainerLine, Enquiry, Incoterm, Service
from src.pipeline import run_enquiry
from src.quote import PricingError, price
from tests.conftest import sample_text


def _fcl(**overrides) -> Enquiry:
    base = dict(direction="import", mode="sea", service=Service.FCL, destination_country="GB",
                destination_port="GBFXT", containers=[ContainerLine(container_type="40HC", quantity=1)],
                commodity="furniture", incoterm=Incoterm.FOB, delivery_required=True, delivery_postcode="SL1 4QP")
    base.update(overrides)
    return Enquiry(**base)


# ---------- the sample enquiries that are quotable ----------

@pytest.mark.parametrize("sid, subtotal, total", [
    ("I01", 1025.00, 1178.75),   # FXT DTHC 245 + DO 65 + customs 65 + haulage zone 1 650
    ("I03", 1000.00, 1150.00),   # LGP DTHC 250 + DO 65 + customs 65 + haulage zone 2 620
    ("I06", 1290.00, 1483.50),   # FXT DTHC 245 + DO 65 + haulage zone 3 980 (no customs asked)
    ("I07", 65.00, 74.75),       # customs clearance only
    ("I12", 1495.90, 1495.90),   # France DAP, DE/FR tariff (EUR, no margin), see test_france_email_tariff
])
def test_sample_totals(sid, subtotal, total, fake_llm):
    r = run_enquiry(sample_text(sid), llm=fake_llm, log=False)
    assert r.triage.status == "quote"
    assert r.quote.subtotal == pytest.approx(subtotal)
    assert r.quote.total == pytest.approx(total)
    assert r.quote.is_mock == (sid != "I12")      # I12 is priced from the real DE/FR tariff


@pytest.mark.parametrize("sid", ["I02", "I04", "I05", "I08", "I09", "I10", "I11"])
def test_non_quotable_samples_have_no_quote(sid, fake_llm):
    assert run_enquiry(sample_text(sid), llm=fake_llm, log=False).quote is None


# ---------- lines and arithmetic ----------

def test_margin_is_15_percent_of_subtotal():
    q = price(_fcl())
    assert q.margin == pytest.approx(q.subtotal * 0.15)
    assert q.total == pytest.approx(q.subtotal + q.margin)


def test_two_containers_two_types():
    e = _fcl(containers=[ContainerLine(container_type="40HC", quantity=2),
                         ContainerLine(container_type="20GP", quantity=1)])
    q = price(e)
    # DTHC 245 x 3 + DO 65 + haulage zone 1: first 650 + 2 additional x 650
    assert q.subtotal == pytest.approx(245 * 3 + 65 + 650 * 3)
    assert [l.quantity for l in q.lines if l.code == "DTHC"] == [2, 1]


def test_no_delivery_only_when_declined():
    q = price(_fcl(delivery_required=False))
    assert {l.code for l in q.lines} == {"DTHC", "DO"}
    assert any("Delivery from the port" in x for x in q.exclusions)
    assert "HAUL" in {l.code for l in price(_fcl(delivery_required=None)).lines}   # not mentioned -> we deliver


def test_clearance_line_only_when_asked():
    assert "IMPCLR" not in {l.code for l in price(_fcl()).lines}
    assert "IMPCLR" in {l.code for l in price(_fcl(customs_clearance_requested=True)).lines}


def test_never_freight_or_origin():
    q = price(_fcl(incoterm=Incoterm.EXW))
    assert {l.section for l in q.lines} <= {"Destination", "Customs", "Delivery"}


def test_duties_always_excluded():
    assert any("duty and import VAT" in x for x in price(_fcl()).exclusions)


def test_amounts_rounded_to_pence():
    for l in price(_fcl()).lines:
        assert round(l.amount, 2) == l.amount


# ---------- EU: Germany & France use Lincoln Cargo's tariff (data/tariffs/eu_de_fr.yaml) ----------

def test_france_email_tariff(fake_llm):
    """The real France DAP email: 1 x 20GP, 21 t, delivered to 51300, no customs asked."""
    r = run_enquiry(sample_text("I12"), llm=fake_llm, log=False)
    assert r.triage.status == "quote"            # same charges at every French port: port not needed
    q = r.quote
    assert q.currency == "EUR" and q.margin == 0 and not q.is_mock
    # THC 290 + Doc 50 + Sec 25 + Lo/Lo 90 = 455
    # Road haulage (ESTIMATE) = Maersk 720 + Maersk fuel 18.5% (133.20) = 853.20; our fuel 22% = 187.70
    assert [(l.code, l.amount) for l in q.lines] == [
        ("THC", 290.0), ("DOC", 50.0), ("SEC", 25.0), ("LOLO", 90.0), ("HAUL", 853.2), ("HAUL-FUEL", 187.7)]
    assert q.total == pytest.approx(1495.90)
    assert any("non-hazardous" in t for t in q.terms) and any("French import VAT" in t for t in q.terms)


def test_same_charges_any_port_in_country(fake_llm):
    e = run_enquiry(sample_text("I12"), llm=fake_llm, log=False).enquiry
    totals = {price(e.model_copy(update={"destination_port": p})).total for p in [None, "FRLEH", "FRFOS"]}
    assert totals == {1495.90}


def test_germany_two_heavy_containers_with_customs():
    e = _fcl(destination_country="DE", destination_port="DEHAM", delivery_postcode="20095",
             containers=[ContainerLine(container_type="40HC", quantity=2)], total_gross_weight_kg=50000,
             customs_clearance_requested=True)
    q = price(e)
    # per container 25 t > 23 t: THC 2x290 + Doc 50 + Sec 2x25 + Lo/Lo 2x90 + Customs 150 = 1010
    #   + road haulage 2 x 853.20 (estimate) + fuel 22% (375.408) + heavy 2x200 + fuel 22% (88) = 3579.81
    assert q.total == pytest.approx(3579.81)
    assert any("Import VAT 19% (Germany)" in t for t in q.terms)


def test_23_tonnes_exactly_is_not_heavy():
    e = _fcl(destination_country="DE", destination_port="DEHAM", delivery_postcode="20095",
             containers=[ContainerLine(container_type="20GP", quantity=1)], total_gross_weight_kg=23000)
    assert "HEAVY" not in {l.code for l in price(e).lines}


def test_tariff_weight_not_stated_is_an_assumption():
    q = price(_fcl(destination_country="DE", destination_port="DEHAM", delivery_postcode="20095"))
    assert any("Weight not stated" in a for a in q.assumptions)


def test_tariff_ddp_adds_indemnity_term():
    q = price(_fcl(destination_country="FR", destination_port="FRLEH", delivery_postcode="51300",
                   incoterm=Incoterm.DDP))
    assert any("indemnity" in t for t in q.terms)


def test_tariff_clearance_only():
    e = _fcl(destination_country="DE", destination_port=None, clearance_only=True,
             customs_clearance_requested=True, delivery_required=False)
    assert [(l.code, l.amount) for l in price(e).lines] == [("CUSTOMS", 150.0)]


# ---------- other EU countries still use the MOCK sheets ----------

def test_netherlands_mock():
    e = _fcl(destination_country="NL", destination_port="NLRTM", delivery_postcode="3011 AB")
    q = price(e)
    # NLRTM DTHC 230 + EU DO 60 + NL zone 1 haulage 430 = 720; +15% = 828
    assert q.subtotal == pytest.approx(720) and q.total == pytest.approx(828) and q.is_mock


def test_netherlands_clearance_uses_mock_eu_fee():
    e = _fcl(destination_country="NL", destination_port="NLRTM", delivery_postcode="3011 AB",
             customs_clearance_requested=True)
    assert [l.unit_rate for l in price(e).lines if l.code == "IMPCLR"] == [85.0]


# ---------- guards ----------

def test_lcl_not_priced():
    with pytest.raises(PricingError):
        price(_fcl(service=Service.LCL, containers=[]))


def test_port_needed():
    with pytest.raises(PricingError):
        price(_fcl(destination_port=None))


def test_run_writes_log(fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr("src.pipeline.LOG_DIR", tmp_path)
    run_enquiry(sample_text("I01"), llm=fake_llm)
    assert len(list(tmp_path.glob("run-*.json"))) == 1


# ---------- quote text file ----------

def test_quote_text_file(fake_llm, tmp_path):
    from datetime import date
    from src.quote_text import render_quote_text, save_quote_text
    r = run_enquiry(sample_text("I12"), llm=fake_llm, log=False)
    text = render_quote_text(r.enquiry, r.quote, r.triage, today=date(2026, 11, 10))
    assert "IMPORT QUOTATION" in text and "MOCK" not in text
    assert "TOTAL EUR" in text and "1,495.90" in text
    assert "Valid until:   24 Nov 2026" in text
    assert "TERMS AND CONDITIONS" in text and "PLEASE ALSO CONFIRM" in text
    path = save_quote_text(text, folder=tmp_path, stamp="test")
    assert path.read_text(encoding="utf-8") == text


def test_mock_quote_text_is_labelled(fake_llm):
    from src.quote_text import render_quote_text
    r = run_enquiry(sample_text("I01"), llm=fake_llm, log=False)
    assert "MOCK RATES" in render_quote_text(r.enquiry, r.quote)


# ---------- Maersk carrier haulage (data/haulage/maersk.yaml) ----------

def _fr(**kw):
    base = dict(destination_country="FR", destination_port=None, delivery_postcode="51300",
                containers=[ContainerLine(container_type="20GP", quantity=1)], total_gross_weight_kg=21000)
    base.update(kw)
    return _fcl(**base)


def test_one_haulage_line_plus_22pct_fuel():
    q = price(_fr())
    haul = [l for l in q.lines if l.section == "Delivery"]
    assert [(l.code, l.description) for l in haul] == [("HAUL", "Road haulage"), ("HAUL-FUEL", "Fuel surcharge 22%")]
    assert haul[1].amount == pytest.approx(round(haul[0].amount * 0.22, 2))
    assert q.haulage_estimate and any("ESTIMATE" in n and "Maersk" in n for n in q.internal_notes)


def test_new_maersk_fuel_rate_changes_price(monkeypatch):
    from src.haulage import carrier_haulage as real
    monkeypatch.setattr("src.quote.carrier_haulage", lambda c: {**real(c), "fuel_pct": 10.0})
    q = price(_fr())   # estimate path: Maersk fuel amount = file % x haulage
    # 455 tariff + road haulage (720 + 10% = 792) + our fuel 22% (174.24) = 1421.24
    assert q.total == pytest.approx(1421.24)


def test_stale_maersk_fuel_warns_staff(monkeypatch):
    from datetime import date
    from src.haulage import carrier_haulage as real
    from src.triage import triage
    monkeypatch.setattr("src.triage.carrier_haulage", lambda c: {**real(c), "fuel_updated": date(2026, 1, 1)})
    t = triage(_fr(destination_port="FRLEH"))
    assert t.status == "quote" and any("last updated" in n for n in t.notes)


def test_fresh_fuel_no_warning():
    from datetime import date
    from src.haulage import carrier_haulage, fuel_is_stale
    assert not fuel_is_stale(carrier_haulage("FR"), today=date(2026, 11, 20))
    assert fuel_is_stale(carrier_haulage("FR"), today=date(2026, 12, 1))
