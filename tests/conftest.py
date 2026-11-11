"""Shared test helpers: a fake LLM so tests never call a real API."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "sample_enquiries"
SAMPLE_IDS = [f"I{i:02d}" for i in range(1, 14)]


class FakeLLM:
    """Returns canned JSON: what a well-behaved model should extract for each sample email."""

    def __init__(self):
        self.raw = json.loads((ROOT / "tests" / "fixtures" / "llm_raw.json").read_text(encoding="utf-8"))
        self.texts = {sid: (SAMPLES / f"{sid}.txt").read_text(encoding="utf-8").strip() for sid in SAMPLE_IDS}
        self.calls = []

    def extract(self, system, prompt, schema):
        self.calls.append((system, prompt))
        for sid, text in self.texts.items():
            if text in prompt:
                return schema.model_validate(self.raw[sid])
        raise AssertionError("FakeLLM got an email it doesn't know")

    def complete(self, system, prompt, temperature=0.3):
        return "fake text"


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture(scope="session")
def expected():
    return json.loads((SAMPLES / "expected.json").read_text(encoding="utf-8"))


def sample_text(sid: str) -> str:
    return (SAMPLES / f"{sid}.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def no_road_service(monkeypatch):
    """Tests never call OpenRouteService: the default service fails like a missing key.
    Tests that need distances pass (or patch in) their own fake service."""
    import src.distance as d

    def _no_ors():
        raise d.DistanceError("ORS disabled in tests")

    monkeypatch.setattr(d, "default_service", _no_ors)


@pytest.fixture(autouse=True)
def no_clear_screen(monkeypatch):
    """try_email clears the terminal when a quote is done; never in tests."""
    import src.try_email as te
    monkeypatch.setattr(te, "clear_screen", lambda: print("<<CLEAR SCREEN>>"))
