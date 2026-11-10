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
])
def test_sample_totals(sid, subtotal, total, fake_llm):
    r = run_enquiry(sample_text(sid), llm=fake_llm, log=False)
    assert r.triage.status == "quote"
    assert r.quote.subtotal == pytest.approx(subtotal)
    assert r.quote.total == pytest.approx(total)
    assert r.quote.is_mock


@pytest.mark.parametrize("sid", ["I02", "I04", "I05", "I08", "I09", "I10", "I11", "I12"])
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


def test_no_delivery_when_not_asked():
    q = price(_fcl(delivery_required=False))
    assert {l.code for l in q.lines} == {"DTHC", "DO"}
    assert any("Delivery from the port" in x for x in q.exclusions)


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


# ---------- EU ----------

def test_eu_hamburg():
    e = _fcl(destination_country="DE", destination_port="DEHAM", delivery_postcode="20095")
    q = price(e)
    # DEHAM DTHC 240 + EU DO 60 + DE zone 1 haulage 540 = 840; +15% = 966
    assert q.subtotal == pytest.approx(840) and q.total == pytest.approx(966)


def test_france_email_once_port_is_known(fake_llm):
    # The France DAP email (I12) needs a port; with "Le Havre" added it prices.
    text = sample_text("I12")
    r = run_enquiry(text, llm=fake_llm, log=False)
    assert r.quote is None
    e = r.enquiry.model_copy(update={"destination_port": "FRLEH"})
    q = price(e)
    # FRLEH DTHC 235 + EU DO 60 + FR zone 2 (51300) haulage 1180 = 1475; +15% = 1696.25
    assert q.subtotal == pytest.approx(1475) and q.total == pytest.approx(1696.25)
    assert "IMPCLR" not in {l.code for l in q.lines}      # DAP, clearance not asked


def test_eu_clearance_uses_eu_fee():
    e = _fcl(destination_country="DE", destination_port="DEHAM", delivery_postcode="20095",
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


def test_france_email_via_fos(fake_llm):
    e = run_enquiry(sample_text("I12"), llm=fake_llm, log=False).enquiry.model_copy(update={"destination_port": "FRFOS"})
    q = price(e)
    # FRFOS DTHC 225 + EU DO 60 + FR zone 2 (51300) haulage from Fos 1260 = 1545; +15% = 1776.75
    assert q.subtotal == pytest.approx(1545) and q.total == pytest.approx(1776.75)
    assert "Fos-sur-Mer" in q.lane
