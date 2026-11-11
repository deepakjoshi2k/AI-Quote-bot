"""Staff screen layout (src/report.py): decision labels, notes for staff, agent actions."""
import pytest

from src.extract import extract_enquiry
from src.models import ContainerLine, Enquiry, Incoterm, Service, Triage
from src.report import (DECISIONS, INCOTERM_MEANING, agent_actions, format_decision, format_staff_notes, rows,
                        sender_asked)
from src.triage import triage
from tests.conftest import sample_text


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize("status,label", [("quote", "QUOTE"), ("need_info", "EMAIL SENDER"),
                                          ("manual_review", "MANUAL REVIEW")])
def test_decision_is_one_line(status, label):
    e = Enquiry(direction="import", mode="sea", service=Service.FCL)
    out = format_decision(e, Triage(status=status))
    assert f"║  DECISION: {label}" in out
    assert "Why" not in out and "Waiting" not in out and "Ready to quote" not in out


def test_every_incoterm_has_a_meaning():
    assert set(INCOTERM_MEANING) == {i for i in Incoterm if i is not Incoterm.UNKNOWN}


def test_sender_asked():
    e = Enquiry(delivery_required=True, customs_clearance_requested=True, port_advice_requested=True)
    assert sender_asked(e) == "Destination charges · door delivery · customs clearance · advise the nearest port"
    assert sender_asked(Enquiry(clearance_only=True)) == "Customs clearance only"


def test_manual_review_actions_list_the_reasons():
    t = Triage(status="manual_review", reasons=["LCL enquiry: LCL is quoted manually for now."])
    acts = agent_actions(Enquiry(), t)
    assert acts == ["[ ] Manual: LCL enquiry: LCL is quoted manually for now."]


def test_quote_actions_done_then_email_then_clearance(fake_llm):
    e = extract_enquiry(sample_text("I12"), llm=fake_llm)
    t = triage(e)
    out = _flat(format_staff_notes(e, t, done=["Quote drafted via Le Havre (FRLEH): EUR 1,305.10"], quotes_made=1,
                                   ask=["port of arrival the container is booked into (quoted via Le Havre (FRLEH))"]))
    assert out.index("[x] Quote drafted") < out.index("[ ] Check the quote, then email to the sender asking them to "
                                                       "confirm: Port of arrival") < out.index("[ ] Clearance email")
    assert "Incoterm DAP – the seller delivers" in out
    assert "Priced from" not in out and "Road haulage =" not in out   # no clutter


def test_only_three_note_groups_plus_checks(fake_llm):
    e = extract_enquiry(sample_text("I12"), llm=fake_llm)
    labels = [line[2:16].strip() for line in format_staff_notes(e, triage(e)).splitlines()[1:]
              if line[2:16].strip()]
    assert labels == ["Incoterm", "Sender asked", "Agent actions"]


def test_rows_wrap_under_the_value_column():
    out = rows([("Agent actions", ["[ ] " + "word " * 40])])
    assert len(out) > 1 and all(len(l) <= 78 for l in out)
    assert out[1].startswith(" " * (2 + len("Agent actions") + 2 + 4))   # hangs under the text, not the box


def test_cli_notes_for_staff_come_last(monkeypatch, fake_llm, tmp_path, capsys):
    import src.quote_text as qt
    import src.try_email as te
    real = te.run_enquiry
    monkeypatch.setattr(te, "run_enquiry", lambda text: real(text, llm=fake_llm, log=False))
    monkeypatch.setattr(te, "save_quote_text", lambda text, stamp=None: qt.save_quote_text(text, folder=tmp_path,
                                                                                           stamp="t"))
    te.main(["data/sample_enquiries/I12.txt", "--ports", "Le Havre", "--haulage", "650", "--fuel", "46.80"])
    out = capsys.readouterr().out
    assert out.index("DECISION: QUOTE") < out.index("── QUOTE (") < out.index("── NOTES FOR STAFF")
    assert "[x] Quote drafted via Le Havre (FRLEH): EUR 1,305.10" in _flat(out)


# ---------- clean final screen (working steps cleared once the quote is done) ----------

def _cli(monkeypatch, fake_llm, tmp_path, answers):
    import src.distance as d
    import src.quote_text as qt
    import src.try_email as te
    from tests.test_distance import FakeRoutes
    real = te.run_enquiry
    monkeypatch.setattr(te, "run_enquiry", lambda text: real(text, llm=fake_llm, log=False))
    monkeypatch.setattr(te, "save_quote_text", lambda text, stamp=None: qt.save_quote_text(text, folder=tmp_path,
                                                                                           stamp="t"))
    monkeypatch.setattr(d, "default_service", lambda: FakeRoutes(override={"FRLEH": 200.0, "FRDKK": 210.0}))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))
    return te


def test_steps_cleared_after_quote(monkeypatch, fake_llm, tmp_path, capsys):
    te = _cli(monkeypatch, fake_llm, tmp_path, ["1", "2300", "22", ""])
    te.main(["data/sample_enquiries/I12.txt"])
    out = capsys.readouterr().out
    before, final = out.split("<<CLEAR SCREEN>>")
    assert "MAERSK INLAND PRICE LOOK-UP" in before and "NEAREST PORTS" in before     # working steps
    assert "MAERSK INLAND" not in final and "NEAREST PORTS" not in final and "QUOTE COVERS" not in final
    assert final.index("SHIPMENT DETAILS") < final.index("DECISION: QUOTE") < final.index("── QUOTE (") \
        < final.index("NOTES FOR STAFF")
    assert "EUR   3,287.84" in final
    assert "[x] Port Le Havre (FRLEH) chosen – 200 km by road" in _flat(final)


def test_steps_kept_when_quote_not_made(monkeypatch, fake_llm, tmp_path, capsys):
    te = _cli(monkeypatch, fake_llm, tmp_path, ["1", ""])          # port chosen, Maersk figures skipped
    te.main(["data/sample_enquiries/I12.txt"])
    out = capsys.readouterr().out
    assert "<<CLEAR SCREEN>>" not in out and "MAERSK INLAND PRICE LOOK-UP" in out   # still needed
    assert "[ ] Le Havre (FRLEH): no quote yet" in _flat(out)


def test_keep_steps_flag(monkeypatch, fake_llm, tmp_path, capsys):
    te = _cli(monkeypatch, fake_llm, tmp_path, ["1", "2300", "22", ""])
    te.main(["data/sample_enquiries/I12.txt", "--keep-steps"])
    out = capsys.readouterr().out
    assert "<<CLEAR SCREEN>>" not in out and "MAERSK INLAND PRICE LOOK-UP" in out
