"""Nearest arrival port by road (src/distance.py) and its use in triage, pricing and the CLI.
OpenRouteService is never called: a fake route service stands in for it."""
import io
import json
import urllib.request

import pytest

import src.distance as d
from src.distance import (DistanceError, Located, OrsClient, PortOption, format_options, haversine_km,
                          load_ports, nearest_ports)
from src.haulage import HaulageCheck, lookup_instructions
from src.models import ContainerLine, Enquiry, Incoterm, Service, TriageFlag
from src.quote import price
from src.triage import triage

VITRY = Located(lon=4.585, lat=48.725, label="Vitry-le-François, Marne, France", confidence=1.0)


class FakeRoutes:
    """Road km = straight line x 1.3, with overrides per LOCODE (None = no road route)."""

    def __init__(self, here=VITRY, override=None):
        self.here, self.override, self.targets = here, override or {}, None

    def geocode(self, country, postcode, place):
        self.query = (country, postcode, place)
        return self.here

    def road_matrix(self, origin, targets):
        self.targets = targets
        ports = {(p.lon, p.lat): p for p in load_ports()}
        out = []
        for t in targets:
            p = ports[t]
            km = self.override.get(p.locode, haversine_km(origin[1], origin[0], p.lat, p.lon) * 1.3)
            out.append((km, None if km is None else km / 70 * 3600))
        return out


def _fr(**kw) -> Enquiry:
    base = dict(direction="import", mode="sea", service=Service.FCL, destination_country="FR",
                containers=[ContainerLine(container_type="20GP", quantity=1)], commodity="parts",
                incoterm=Incoterm.DAP, delivery_required=True, delivery_postcode="51300",
                delivery_place="Vitry-le-François, France", total_gross_weight_kg=21000)
    base.update(kw)
    return Enquiry(**base)


# ---------- port table ----------

def test_port_table_covers_uk_and_coastal_eu():
    ports = load_ports()
    assert len({p.locode for p in ports}) == len(ports)
    countries = {p.country for p in ports}
    coastal = {"GB", "NL", "BE", "DE", "FR", "ES", "IT", "PT", "GR", "PL", "IE", "DK", "SE", "FI", "EE", "LV",
               "LT", "HR", "SI", "RO", "BG", "CY", "MT"}
    assert coastal <= countries
    assert all(p.locode.startswith(p.country) for p in ports)
    assert not {"AT", "CZ", "HU", "SK", "LU"} & countries      # landlocked: served by foreign ports


def test_new_ports_are_recognised_by_name():
    from src.rates import load_rate_book
    rb = load_rate_book()
    assert rb.port_code("Dunkerque") == "FRDKK"
    assert rb.port_code("Port of Gdynia") == "PLGDY"


def test_haversine_london_paris():
    assert 330 < haversine_km(51.507, -0.128, 48.857, 2.352) < 360


# ---------- nearest ports ----------

def test_nearest_ports_ranked_by_road_not_straight_line():
    # Make the straight-line nearest port the furthest by road.
    straight = sorted(load_ports(), key=lambda p: haversine_km(VITRY.lat, VITRY.lon, p.lat, p.lon))
    first = straight[0].locode
    here, opts = nearest_ports("FR", "51300", "Vitry", service=FakeRoutes(override={first: 2000.0}))
    assert here.label.startswith("Vitry")
    assert len(opts) == 3
    assert [o.road_km for o in opts] == sorted(o.road_km for o in opts)
    assert first not in [o.locode for o in opts]


def test_candidates_preselected_and_unroutable_dropped():
    fake = FakeRoutes(override={"FRDKK": None})
    _, opts = nearest_ports("FR", "51300", None, service=fake, show=5, candidates=6)
    assert len(fake.targets) == 6
    assert "FRDKK" not in [o.locode for o in opts]
    assert fake.query == ("FR", "51300", None)


def test_foreign_ports_allowed():
    _, opts = nearest_ports("FR", "51300", None, service=FakeRoutes(), show=8, candidates=8)
    assert {o.port.country for o in opts} - {"FR"}               # e.g. Antwerp / Rotterdam


def test_no_route_at_all_is_an_error():
    ports = {p.locode: None for p in load_ports()}
    with pytest.raises(DistanceError):
        nearest_ports("FR", "51300", None, service=FakeRoutes(override=ports))


def test_format_marks_ports_we_cannot_price():
    p = load_ports()
    opts = [PortOption(next(x for x in p if x.locode == c), km, 5.0) for c, km in [("BEANR", 300), ("FRLEH", 412)]]
    text = format_options(VITRY, opts, {"BEANR": False, "FRLEH": True})
    assert "1. Antwerp (BEANR, BE)  300 km" in text
    assert "manual quote" in text.splitlines()[2] and "manual" not in text.splitlines()[3]
    assert "confirm the port" in text


# ---------- ORS client (HTTP shape only, no network) ----------

def test_ors_client_requests(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        if "geocode" in req.full_url:
            body = {"features": [{"geometry": {"coordinates": [4.585, 48.725]},
                                  "properties": {"label": "Vitry-le-François, France", "confidence": 1}}]}
        else:
            body = {"distances": [[412.3, None]], "durations": [[20000.0, None]]}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    c = OrsClient(api_key="k")
    here = c.geocode("FR", "51300", "Vitry-le-François")
    assert (here.lon, here.lat) == (4.585, 48.725)
    assert "boundary.country=FR" in seen[0].full_url and "51300" in seen[0].full_url
    assert seen[0].get_header("Authorization") == "k"
    res = c.road_matrix((4.585, 48.725), [(0.17, 49.47), (1.0, 2.0)])
    assert res == [(412.3, 20000.0), (None, None)]
    assert seen[1].full_url.endswith("/v2/matrix/driving-hgv")
    sent = json.loads(seen[1].data)
    assert sent["locations"][0] == [4.585, 48.725]                # [lon, lat]
    assert sent["sources"] == [0] and sent["destinations"] == [1, 2] and sent["units"] == "km"


def test_ors_client_needs_key(monkeypatch):
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    with pytest.raises(DistanceError, match="ORS_API_KEY"):
        OrsClient()


# ---------- triage / pricing with a port chosen by staff ----------

def test_port_in_other_tariff_country_is_quoted_with_note():
    e = _fr(destination_port="DEHAM")
    t = triage(e)
    assert t.status == "quote"
    assert any("DEHAM is in DE" in n for n in t.notes)
    q = price(e, haulage_check=HaulageCheck(650.0, 46.80))
    assert q.currency == "EUR" and q.total == pytest.approx(1305.10)
    assert any("outside FR" in n for n in q.internal_notes)


def test_port_in_country_without_tariff_goes_to_manual():
    t = triage(_fr(destination_port="BEANR"))
    assert t.status == "manual_review"
    assert TriageFlag.DESTINATION_PORT_NOT_IN_SHEET in t.flags
    assert "no tariff for BE" in t.reasons[0]


def test_dunkirk_priced_from_french_tariff():
    q = price(_fr(destination_port="FRDKK"), haulage_check=HaulageCheck(650.0, 46.80))
    assert q.lane.startswith("Dunkirk (FRDKK)")
    assert q.total == pytest.approx(1305.10)


def test_mock_sheet_country_rejects_foreign_port():
    e = Enquiry(direction="import", mode="sea", service=Service.FCL, destination_country="GB",
                destination_port="NLRTM", containers=[ContainerLine(container_type="40HC", quantity=1)],
                commodity="furniture", delivery_required=True, delivery_postcode="SL3 0AX")
    t = triage(e)
    assert t.status == "manual_review" and TriageFlag.DESTINATION_PORT_NOT_IN_SHEET in t.flags


def test_lookup_box_shows_staff_port_choice():
    text = lookup_instructions(_fr(destination_port="DEHAM"), port_note="nearest by road, 512 km (staff choice)")
    line = next(l for l in text.splitlines() if "Gateway port" in l)
    assert "Hamburg (DEHAM)" in line and "NOT IN EMAIL" in line and "another country" in line


# ---------- CLI ----------

def _cli(monkeypatch, fake_llm, tmp_path, override=None):
    import src.quote_text as qt
    import src.try_email as te
    real = te.run_enquiry
    monkeypatch.setattr(te, "run_enquiry", lambda text: real(text, llm=fake_llm, log=False))
    monkeypatch.setattr(te, "save_quote_text",
                        lambda text, stamp=None: qt.save_quote_text(
                            text, folder=tmp_path, stamp="t" if stamp is None else "t-" + stamp.rsplit("-", 1)[-1]))
    monkeypatch.setattr(d, "default_service", lambda: FakeRoutes(override=override))
    return te


def test_parse_port_choice():
    from src.try_email import parse_port_choice
    p = load_ports()
    opts = [PortOption(next(x for x in p if x.locode == c), 1.0) for c in ["FRLEH", "BEANR", "DEHAM"]]
    assert parse_port_choice("1,3", opts) == ["FRLEH", "DEHAM"]
    assert parse_port_choice(" frdkk ; 1, 1", opts) == ["FRDKK", "FRLEH"]
    assert parse_port_choice("Le Havre, Hamburg", opts) == ["FRLEH", "DEHAM"]
    for bad in ["4", "", "Atlantis"]:
        with pytest.raises(ValueError):
            parse_port_choice(bad, opts)


def test_cli_autofills_nearest_priced_port(monkeypatch, fake_llm, tmp_path, capsys):
    # Antwerp nearest by road, but no BE tariff -> the nearest port we can price is filled in.
    te = _cli(monkeypatch, fake_llm, tmp_path, override={"BEANR": 100.0, "FRLEH": 200.0, "FRDKK": 300.0})
    te.main(["data/sample_enquiries/I12.txt", "--haulage", "650", "--fuel", "46.80"])
    out = capsys.readouterr().out
    assert "NEAREST PORTS BY ROAD" in out and "1. Antwerp (BEANR, BE)  100 km" in out
    assert "Auto-filled the nearest port we can price: FRLEH" in out
    assert "Le Havre (FRLEH) – NOT IN EMAIL: nearest by road, 200 km" in out
    saved = (tmp_path / "quote-t.txt").read_text(encoding="utf-8")
    assert "1,305.10" in saved and "Le Havre" in saved


def test_cli_two_ports_two_quotes(monkeypatch, fake_llm, tmp_path, capsys):
    te = _cli(monkeypatch, fake_llm, tmp_path, override={"FRLEH": 200.0, "FRDKK": 210.0, "BEANR": 220.0})
    te.main(["data/sample_enquiries/I12.txt", "--ports", "1,2,3", "--haulage", "650", "--fuel", "46.80"])
    out = capsys.readouterr().out
    assert "BEANR: no tariff for BE" in out
    assert (tmp_path / "quote-t-FRLEH.txt").exists() and (tmp_path / "quote-t-FRDKK.txt").exists()
    assert not (tmp_path / "quote-t-BEANR.txt").exists()


def test_cli_interactive_port_pick(monkeypatch, fake_llm, tmp_path, capsys):
    te = _cli(monkeypatch, fake_llm, tmp_path, override={"FRLEH": 200.0, "FRDKK": 210.0})
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["2", "650", "46.80", ""])                    # port 2, then the Maersk figures
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    te.main(["data/sample_enquiries/I12.txt"])
    saved = (tmp_path / "quote-t.txt").read_text(encoding="utf-8")
    assert "Dunkirk" in saved


def test_cli_without_ors_still_works(monkeypatch, fake_llm, tmp_path, capsys):
    te = _cli(monkeypatch, fake_llm, tmp_path)

    def broken():
        raise DistanceError("ORS_API_KEY is not set")

    monkeypatch.setattr(d, "default_service", broken)
    te.main(["data/sample_enquiries/I12.txt", "--haulage", "650", "--fuel", "46.80"])
    out = capsys.readouterr().out
    assert "Nearest ports by road not available: ORS_API_KEY" in out
    assert "1,305.10" in (tmp_path / "quote-t.txt").read_text(encoding="utf-8")


def test_cli_adds_nearest_priced_port_when_top3_cannot_be_priced(monkeypatch, fake_llm, tmp_path, capsys):
    te = _cli(monkeypatch, fake_llm, tmp_path,
              override={"BEGNE": 100.0, "BEANR": 110.0, "BEZEE": 120.0, "NLRTM": 130.0, "FRDKK": 140.0})
    te.main(["data/sample_enquiries/I12.txt", "--haulage", "650", "--fuel", "46.80"])
    out = capsys.readouterr().out
    assert "4. Rotterdam" not in out                             # only the nearest PRICED extra is added
    assert "4. Dunkirk (FRDKK, FR)  140 km" in out
    assert "Auto-filled the nearest port we can price: FRDKK" in out


# ---------- port missing and NOT asked to suggest one (I13): ask the sender, staff may pick ----------

MUNICH = Located(lon=11.53, lat=48.14, label="80339 München, Germany")


def _cli13(monkeypatch, fake_llm, tmp_path):
    te = _cli(monkeypatch, fake_llm, tmp_path)
    monkeypatch.setattr(d, "default_service", lambda: FakeRoutes(here=MUNICH))
    return te


def test_i13_no_port_no_request_asks_sender_and_makes_no_quote(monkeypatch, fake_llm, tmp_path, capsys):
    te = _cli13(monkeypatch, fake_llm, tmp_path)
    te.main(["data/sample_enquiries/I13.txt", "--haulage", "650", "--fuel", "46.80"])
    out = " ".join(capsys.readouterr().out.split())              # ignore line wrapping
    assert "DECISION: EMAIL SENDER" in out
    assert "[ ] Email the sender to ask: Port of arrival the container is booked into" in out
    assert "no port of arrival and the sender did not ask us to suggest one" in out
    assert "NEAREST PORTS BY ROAD" in out
    assert "Auto-filled" not in out and "[x]" not in out          # never filled in without being asked
    assert not list(tmp_path.glob("quote-*.txt"))


def test_i13_staff_picks_port_and_quotes(monkeypatch, fake_llm, tmp_path, capsys):
    te = _cli13(monkeypatch, fake_llm, tmp_path)
    te.main(["data/sample_enquiries/I13.txt", "--ports", "Hamburg", "--haulage", "650", "--fuel", "46.80"])
    out = capsys.readouterr().out
    assert "Hamburg (DEHAM) – NOT IN EMAIL" in out
    saved = (tmp_path / "quote-t.txt").read_text(encoding="utf-8")
    assert "quoted via Hamburg (DEHAM); please confirm the port the container is booked into" in saved
    assert saved.count("ort of arrival") == 1                      # the open question is replaced, not repeated


def test_i13_interactive_enter_means_just_ask(monkeypatch, fake_llm, tmp_path, capsys):
    te = _cli13(monkeypatch, fake_llm, tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    te.main(["data/sample_enquiries/I13.txt"])
    out = " ".join(capsys.readouterr().out.split())
    assert "[ ] Email the sender to ask: Port of arrival" in out and "[x]" not in out
    assert not list(tmp_path.glob("quote-*.txt"))


def test_i13_port_without_tariff_not_quoted(monkeypatch, fake_llm, tmp_path, capsys):
    te = _cli13(monkeypatch, fake_llm, tmp_path)
    te.main(["data/sample_enquiries/I13.txt", "--ports", "SIKOP", "--haulage", "650", "--fuel", "46.80"])
    out = capsys.readouterr().out
    assert "SIKOP: no tariff for SI" in out
    assert not list(tmp_path.glob("quote-*.txt"))


def test_staff_pick_only_when_port_is_the_only_gap(fake_llm):
    from src.extract import extract_enquiry
    from src.try_email import staff_can_choose_port
    from tests.conftest import sample_text
    e = extract_enquiry(sample_text("I13"), llm=fake_llm)
    assert staff_can_choose_port(e, triage(e))
    e2 = e.model_copy(update={"containers": []})
    assert not staff_can_choose_port(e2, triage(e2))             # containers missing too: just ask
