import pytest
from pydantic import ValidationError

from src.models import ContainerLine, Enquiry, Incoterm, Triage


def test_enquiry_defaults():
    e = Enquiry()
    assert e.incoterm is Incoterm.UNKNOWN
    assert e.containers == [] and e.cartons == []


def test_enquiry_parses_llm_style_json():
    e = Enquiry.model_validate({
        "direction": "import", "mode": "sea", "service": "fcl",
        "origin_port": "CNNGB", "uk_port": "GBFXT",
        "containers": [{"container_type": "40HC", "quantity": 1}],
        "incoterm": "FOB", "delivery_required": True,
        "delivery_postcode": "SL1 4QP", "ready_date": "2026-11-20",
    })
    assert e.containers[0].container_type == "40HC"
    assert e.ready_date.isoformat() == "2026-11-20"


def test_rejects_unknown_container_type():
    with pytest.raises(ValidationError):
        ContainerLine(container_type="45HC", quantity=1)


def test_rejects_zero_containers():
    with pytest.raises(ValidationError):
        ContainerLine(container_type="20GP", quantity=0)


def test_triage_status_restricted():
    with pytest.raises(ValidationError):
        Triage(status="maybe")
