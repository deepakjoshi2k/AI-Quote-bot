import json

import pytest

from src.extract import (
    apply_defaults, build_system_prompt, decide_direction, extract_enquiry, normalise,
    normalise_container_type, normalise_incoterm,
)
from src.llm import LLMError, OpenAICompatibleLLM, LLMSettings, parse_json_text
from src.models import Direction, Incoterm, RawEnquiry, Service
from tests.conftest import sample_text


# ---------- container types ----------

@pytest.mark.parametrize("raw, expected", [
    ("40HC", "40HC"), ("40ft high cube", "40HC"), ("40' HQ", "40HC"), ("40 high-cube", "40HC"),
    ("40ft", "40GP"), ("40GP", "40GP"), ("40DV", "40GP"),
    ("20ft", "20GP"), ("20'", "20GP"), ("20GP", "20GP"), ("20 foot", "20GP"),
    ("45HC", None), ("20ft reefer", None), ("40 open top", None), ("40 flat rack", None), ("ISO tank", None),
])
def test_normalise_container_type(raw, expected):
    assert normalise_container_type(raw) == expected


# ---------- Incoterms ----------

@pytest.mark.parametrize("raw, expected", [
    ("FOB", Incoterm.FOB), ("fob", Incoterm.FOB), ("FOB Ningbo", Incoterm.FOB), ("Ex Works", Incoterm.EXW),
    ("EXW", Incoterm.EXW), ("D.A.P.", Incoterm.DAP), (None, Incoterm.UNKNOWN), ("CPT", Incoterm.UNKNOWN),
    ("Free on board Ningbo", Incoterm.FOB), ("cost, insurance and freight", Incoterm.CIF), ("C&F", Incoterm.CFR),
    ("Delivered at Place Unloaded", Incoterm.UNKNOWN), ("something else", Incoterm.UNKNOWN), ("FOBX", Incoterm.UNKNOWN),
])
def test_normalise_incoterm(raw, expected):
    assert normalise_incoterm(raw)[0] is expected


def test_unsupported_incoterm_noted():
    term, note = normalise_incoterm("CIP")
    assert term is Incoterm.UNKNOWN and "unsupported" in note


# ---------- units ----------

def test_weight_in_tonnes_converted():
    e = normalise(RawEnquiry(total_weight=14, weight_unit="t"))
    assert e.total_gross_weight_kg == pytest.approx(14000)


def test_weight_in_lb_converted():
    e = normalise(RawEnquiry(total_weight=1000, weight_unit="lb"))
    assert e.total_gross_weight_kg == pytest.approx(453.592)


def test_cartons_in_inches_to_cbm():
    raw = RawEnquiry(cartons=[{"quantity": 10, "length": 20, "width": 20, "height": 20,
                               "dimension_unit": "in", "weight_each": 10, "weight_unit": "lb"}])
    e = normalise(raw)
    assert e.cartons[0].length_cm == pytest.approx(50.8)
    assert e.total_volume_cbm == pytest.approx(10 * 50.8 ** 3 / 1_000_000)
    assert e.total_gross_weight_kg == pytest.approx(45.3592)
    assert any("computed from carton" in n for n in e.extraction_notes)


def test_cbm_mismatch_noted():
    raw = RawEnquiry(total_volume_cbm=10, cartons=[{"quantity": 1, "length": 100, "width": 100, "height": 100}])
    e = normalise(raw)
    assert e.total_volume_cbm == 10  # stated value kept
    assert any("differs" in n for n in e.extraction_notes)


# ---------- service ----------

def test_containers_mean_fcl():
    e = normalise(RawEnquiry(service="lcl", containers=[{"container_type": "20ft", "quantity": 1}]))
    assert e.service is Service.FCL


def test_small_volume_assumed_lcl():
    e = normalise(RawEnquiry(total_volume_cbm=3))
    assert e.service is Service.LCL


def test_mid_volume_left_unknown():
    assert normalise(RawEnquiry(total_volume_cbm=14)).service is Service.UNKNOWN


def test_unsupported_container_goes_to_special():
    e = normalise(RawEnquiry(containers=[{"container_type": "45HC", "quantity": 1}]))
    assert e.containers == []
    assert any("unsupported container" in s for s in e.special_requirements)


# ---------- ports, postcode, UN ----------

def test_ports_mapped():
    e = normalise(RawEnquiry(origin_port_name="Nhava Sheva", destination_port_name="London Gateway"))
    assert (e.origin_port, e.destination_port) == ("INNSA", "GBLGP")


def test_unknown_origin_port_kept_as_name():
    e = normalise(RawEnquiry(origin_port_name="Atlantis"))
    assert e.origin_port is None and e.origin_port_name == "Atlantis"


def test_unknown_arrival_port_flagged():
    e = normalise(RawEnquiry(destination_port_name="Hull docks", direction="import", mode="sea", destination_country="GB"))
    assert e.destination_port is None and any("unsupported arrival port" in s for s in e.special_requirements)


def test_postcode_normalised():
    assert normalise(RawEnquiry(delivery_postcode="sl14qp")).delivery_postcode == "SL1 4QP"


def test_bad_postcode_dropped_with_note():
    e = normalise(RawEnquiry(delivery_postcode="Slough"))
    assert e.delivery_postcode is None and e.extraction_notes


def test_un_number_sets_hazardous():
    e = normalise(RawEnquiry(un_number="UN 3480", hazardous=None))
    assert e.un_number == "UN3480" and e.hazardous is True


# ---------- defaults ----------

def test_default_uk_port_filled_and_noted():
    e = apply_defaults(normalise(RawEnquiry(direction="import", mode="sea", service="lcl", origin_port_name="Shanghai")))
    assert e.destination_port == "GBFXT"
    assert any("UK port not stated" in n for n in e.extraction_notes)


def test_default_not_applied_when_no_lane():
    e = apply_defaults(normalise(RawEnquiry(direction="import", mode="sea", service="lcl", origin_port_name="Santos")))
    assert e.destination_port is None


# ---------- prompt + LLM plumbing ----------

def test_system_prompt_has_schema_and_no_placeholders():
    p = build_system_prompt()
    assert "{schema}" not in p and "{today}" not in p
    assert '"origin_port_name"' in p


def test_extract_enquiry_with_fake_llm(fake_llm):
    e = extract_enquiry(sample_text("I11"), llm=fake_llm)
    assert e.total_volume_cbm == pytest.approx(4.8)
    assert e.total_gross_weight_kg == pytest.approx(720)
    assert len(fake_llm.calls) == 1


@pytest.mark.parametrize("text", [
    '{"a": 1}', '```json\n{"a": 1}\n```', 'Here you go: {"a": 1} hope that helps',
])
def test_parse_json_text(text):
    assert parse_json_text(text) == {"a": 1}


class _ScriptedLLM(OpenAICompatibleLLM):
    """Real extract() logic, but _chat returns scripted replies instead of calling an API."""

    def __init__(self, replies):
        self.settings = LLMSettings("test", "m", "http://x", "k")
        self.replies = list(replies)
        self.sent = []

    def _chat(self, messages, temperature, json_mode):
        self.sent.append(messages)
        return self.replies.pop(0)


def test_extract_retries_once_then_succeeds():
    llm = _ScriptedLLM(['{"direction": "sideways"}', json.dumps({"direction": "import"})])
    raw = llm.extract("sys", "prompt", RawEnquiry)
    assert raw.direction == "import"
    assert "invalid" in llm.sent[1][-1]["content"]


def test_extract_gives_up_after_two_failures():
    llm = _ScriptedLLM(["not json", "still not json"])
    with pytest.raises(LLMError):
        llm.extract("sys", "prompt", RawEnquiry)


def test_settings_require_key(monkeypatch):
    monkeypatch.setattr("src.llm.load_dotenv", lambda: None)
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(LLMError):
        LLMSettings.from_env()


def test_settings_ollama_needs_no_key(monkeypatch):
    monkeypatch.setattr("src.llm.load_dotenv", lambda: None)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    s = LLMSettings.from_env()
    assert s.base_url.startswith("http://localhost:11434") and s.model == "llama3.1"


# ---------- direction decided by Python from the countries ----------

HOME = {"GB", "FR", "NL", "DE"}


@pytest.mark.parametrize("origin, dest, llm, expected", [
    ("CN", "GB", "import", Direction.IMPORT),
    (None, "FR", "export", Direction.IMPORT),        # the France DAP email: destination in EU = import
    ("NL", "FR", "unknown", Direction.IMPORT),       # intra-EU
    ("GB", "AE", "import", Direction.EXPORT),        # model wrong, countries win
    ("CN", "US", "import", Direction.CROSS_TRADE),
    (None, "US", "import", Direction.UNKNOWN),
    ("GB", None, "export", Direction.EXPORT),
    ("VN", None, "import", Direction.IMPORT),
    ("GB", None, "import", Direction.UNKNOWN),       # contradicts: ask
    (None, None, "unknown", Direction.UNKNOWN),
])
def test_decide_direction(origin, dest, llm, expected):
    assert decide_direction(origin, dest, llm, HOME)[0] is expected


def test_uk_postcode_implies_gb():
    e = normalise(RawEnquiry(direction="import", delivery_postcode="SL1 4QP"))
    assert e.destination_country == "GB"


def test_foreign_postcode_kept_as_written():
    e = normalise(RawEnquiry(destination_country="FR", delivery_postcode="51300"))
    assert e.delivery_postcode == "51300"
    assert not any("not recognised" in n for n in e.extraction_notes)


def test_model_cannot_invent_uk_origin_for_eu_import():
    # The real failure: model said export + origin GB for a France delivery with no origin stated.
    e = normalise(RawEnquiry(direction="export", destination_country="FR"))
    assert e.direction is Direction.IMPORT


def test_origin_country_from_port():
    assert normalise(RawEnquiry(origin_port_name="Ningbo")).origin_country == "CN"


def test_uk_alias_normalised():
    assert normalise(RawEnquiry(destination_country="UK")).destination_country == "GB"


def test_export_port_not_taken_as_destination():
    e = normalise(RawEnquiry(direction="export", origin_country="GB", destination_port_name="Felixstowe"))
    assert e.destination_country is None and e.direction is Direction.EXPORT
