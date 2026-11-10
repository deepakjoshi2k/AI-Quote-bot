"""Staff look-up step: Maersk inland price look-up instructions + paste-back re-pricing."""
from datetime import date

import pytest

from src.haulage import HaulageCheck, lookup_instructions, needs_lookup
from src.models import ContainerLine, Enquiry, Incoterm, Service
from src.pipeline import run_enquiry
from src.quote import price
from tests.conftest import sample_text


def _fr(**kw) -> Enquiry:
    base = dict(direction="import", mode="sea", service=Service.FCL, destination_country="FR",
                containers=[ContainerLine(container_type="20GP", quantity=1)], commodity="parts",
                incoterm=Incoterm.DAP, delivery_required=True, delivery_postcode="51300",
                delivery_place="Vitry-le-François, France", total_gross_weight_kg=21000)
    base.update(kw)
    return Enquiry(**base)


# ---------- instructions ----------

def test_lookup_box_has_every_field():
    text = lookup_instructions(_fr(), today=date(2026, 11, 10))
    for must in ["Inland price lookup", "Import", "France (FR)", "Vitry-le-François, France, 51300",
                 "UNTICKED", "1 x 20DRY (email: 20GP)", "21,000 kg gross per container", "10 Nov 2026",
                 "service contract", "Truck", "IHI", "IFS"]:
        assert must.lower() in text.lower(), must


def test_lookup_box_flags_missing_port_and_weight():
    text = lookup_instructions(_fr(total_gross_weight_kg=None))
    assert "Gateway port" in text and "NOT IN EMAIL" in text and "FRLEH" in text and "FRFOS" in text
    assert "weight band; ask the sender" in text


def test_lookup_uses_port_when_given():
    assert "Fos-sur-Mer (FRFOS)" in lookup_instructions(_fr(destination_port="FRFOS"))


@pytest.mark.parametrize("ctype, maersk", [("20GP", "20DRY"), ("40GP", "40DRY"), ("40HC", "40HDRY")])
def test_container_codes(ctype, maersk):
    assert maersk in lookup_instructions(_fr(containers=[ContainerLine(container_type=ctype, quantity=2)]))


def test_needs_lookup_only_with_delivery_in_maersk_country():
    assert needs_lookup(_fr())
    assert not needs_lookup(_fr(delivery_required=False))
    assert not needs_lookup(_fr(destination_country="GB", delivery_postcode="SL1 4QP"))


# ---------- re-pricing with checked figures ----------

def test_checked_figures_replace_estimate():
    q = price(_fr(), haulage_check=HaulageCheck(650.0, 46.80, checked_on=date(2026, 11, 10)))
    # tariff 455 + haulage 650 + fuel 46.80 = 1151.80
    assert q.total == pytest.approx(1151.80)
    assert not q.haulage_estimate
    assert any("10 Nov 2026" in n for n in q.internal_notes)


def test_estimate_without_check():
    q = price(_fr())
    assert q.haulage_estimate and q.total == pytest.approx(1308.20)


def test_extras_per_container():
    e = _fr(containers=[ContainerLine(container_type="40HC", quantity=2)], total_gross_weight_kg=40000)
    q = price(e, haulage_check=HaulageCheck(700.0, 35.0, extras=[("Toll", 35.0)]))
    # tariff: THC 580 + Doc 50 + Sec 50 + Lo/Lo 180 = 860; haulage 1400 + fuel 70 + toll 70 = 1540
    assert q.total == pytest.approx(860 + 1540)


def test_heavy_from_check_or_file():
    heavy = _fr(total_gross_weight_kg=25000)
    default = price(heavy, haulage_check=HaulageCheck(650, 46.8))
    assert [l.amount for l in default.lines if l.code == "HEAVY"] == [237.0]          # our 200 + 18.5% fuel
    assert "HEAVY" not in {l.code for l in price(heavy, haulage_check=HaulageCheck(650, 46.8, heavy_surcharge=0)).lines}
    q = price(heavy, haulage_check=HaulageCheck(650, 46.8, heavy_surcharge=150))
    assert [l.amount for l in q.lines if l.code == "HEAVY"] == [150.0]


def test_quote_text_marks_estimate(fake_llm):
    from src.quote_text import render_quote_text
    r = run_enquiry(sample_text("I12"), llm=fake_llm, log=False)
    assert "HAULAGE IS AN ESTIMATE" in render_quote_text(r.enquiry, r.quote)
    q = price(r.enquiry, haulage_check=HaulageCheck(650, 46.8))
    assert "HAULAGE IS AN ESTIMATE" not in render_quote_text(r.enquiry, q)


# ---------- the command line ----------

def _patch_run(monkeypatch, fake_llm, tmp_path):
    import src.try_email as te
    real = te.run_enquiry
    monkeypatch.setattr(te, "run_enquiry", lambda text: real(text, llm=fake_llm, log=False))
    monkeypatch.setattr("src.quote_text.QUOTES_DIR", tmp_path)
    import src.quote_text as qt
    monkeypatch.setattr(te, "save_quote_text", lambda text: qt.save_quote_text(text, folder=tmp_path, stamp="t"))
    return te


def test_cli_flags(monkeypatch, fake_llm, tmp_path, capsys):
    te = _patch_run(monkeypatch, fake_llm, tmp_path)
    te.main(["data/sample_enquiries/I12.txt", "--haulage", "650", "--fuel", "46.80"])
    out = capsys.readouterr().out
    assert "MAERSK INLAND PRICE LOOK-UP" in out and "TOTAL EUR 1,151.80" in out
    assert "1,151.80" in (tmp_path / "quote-t.txt").read_text(encoding="utf-8")


def test_cli_interactive(monkeypatch, fake_llm, tmp_path, capsys):
    te = _patch_run(monkeypatch, fake_llm, tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["650", "46.80", "Toll=35", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    te.main(["data/sample_enquiries/I12.txt"])
    assert "TOTAL EUR 1,186.80" in capsys.readouterr().out      # 1151.80 + toll 35


def test_cli_skip_keeps_estimate(monkeypatch, fake_llm, tmp_path, capsys):
    te = _patch_run(monkeypatch, fake_llm, tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    te.main(["data/sample_enquiries/I12.txt"])
    assert "ESTIMATE" in (tmp_path / "quote-t.txt").read_text(encoding="utf-8")



# ---------- hidden markup on haulage + fuel ----------

def test_markup_on_haulage_and_fuel_only(monkeypatch):
    monkeypatch.setenv("HAULAGE_MARKUP_PCT", "10")
    q = price(_fr(), haulage_check=HaulageCheck(650.0, 46.80, extras=[("Toll", 35.0)]))
    by = {l.code: l.amount for l in q.lines}
    assert by["HAUL"] == pytest.approx(715.0) and by["HAUL-FUEL"] == pytest.approx(51.48)   # +10%
    assert by["INLAND"] == 35.0 and by["THC"] == 290.0                                     # no markup
    assert q.total == pytest.approx(455 + 715 + 51.48 + 35)
    assert q.margin == 0                                                                    # nothing shown as margin


def test_markup_never_reaches_the_customer(monkeypatch, fake_llm):
    from src.quote_text import render_quote_text
    monkeypatch.setenv("HAULAGE_MARKUP_PCT", "12.5")
    r = run_enquiry(sample_text("I12"), llm=fake_llm, log=False)
    q = price(r.enquiry, haulage_check=HaulageCheck(650.0, 46.80))
    text = render_quote_text(r.enquiry, q, r.triage)
    for secret in ["12.5", "markup", "Markup", "Maersk", "650.00", "46.80", "INTERNAL", "Hidden", "cost per container"]:
        assert secret not in text, secret
    assert "Road haulage" in text and "Fuel surcharge" in text
    assert any("Hidden markup 12.5%" in n for n in q.internal_notes)                    # staff can see it


def test_staff_report_shows_internal(monkeypatch):
    from src.report import format_quote
    monkeypatch.setenv("HAULAGE_MARKUP_PCT", "10")
    out = format_quote(price(_fr(), haulage_check=HaulageCheck(650.0, 46.80)))
    assert "INTERNAL – staff only" in out and "Hidden markup 10%" in out


def test_bad_markup_value(monkeypatch):
    monkeypatch.setenv("HAULAGE_MARKUP_PCT", "ten")
    with pytest.raises(ValueError):
        price(_fr())


def test_markup_percent_sign_allowed(monkeypatch):
    monkeypatch.setenv("HAULAGE_MARKUP_PCT", "10%")
    assert price(_fr(), haulage_check=HaulageCheck(100.0, 10.0)).total == pytest.approx(455 + 121)
