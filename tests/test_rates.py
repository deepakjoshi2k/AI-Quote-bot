from src.models import Service
from src.rates import load_rate_book, service_key


def test_service_key():
    assert service_key(Service.LCL) == "LCL"
    assert service_key(Service.FCL, "40HC") == "FCL-40HC"


def test_port_code_aliases():
    rb = load_rate_book()
    assert rb.port_code("Ningbo") == "CNNGB"
    assert rb.port_code("ho chi minh city") == "VNSGN"
    assert rb.port_code("Port of Felixstowe") == "GBFXT"
    assert rb.port_code("CNSHA") == "CNSHA"
    assert rb.port_code("Santos") == "BRSSZ"
    assert rb.port_code("Atlantis") is None
    assert rb.port_code(None) is None


def test_uk_ports():
    assert load_rate_book().uk_ports() == {"GBFXT", "GBSOU", "GBLGP"}


def test_has_lane():
    rb = load_rate_book()
    assert rb.has_lane("CNNGB", "GBFXT", Service.FCL, ["40HC"])
    assert rb.has_lane("CNSHA", "GBFXT", Service.LCL)
    assert not rb.has_lane("CNSHA", "GBFXT", Service.FCL, ["40GP"])        # no 40GP from Shanghai
    assert not rb.has_lane("CNNGB", "GBFXT", Service.FCL, ["40HC", "45HC"])
    assert not rb.has_lane("BRSSZ", "GBFXT", Service.FCL, ["20GP"])
    assert not rb.has_lane("CNNGB", "GBFXT", Service.FCL, [])


def test_destinations_for():
    rb = load_rate_book()
    assert rb.destinations_for("CNSHA", Service.LCL) == ["GBFXT"]
    assert rb.destinations_for("CNSHA", Service.FCL, ["40HC"]) == ["GBFXT", "GBSOU"]
