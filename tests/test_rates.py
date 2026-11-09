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


def test_destination_ports():
    rb = load_rate_book()
    assert rb.uk_destination_ports() == {"GBFXT", "GBSOU", "GBLGP"}
    assert {"NLRTM", "DEHAM", "FRLEH", "BEANR"} <= rb.eu_destination_ports()
    assert rb.destination_ports() == rb.uk_destination_ports() | rb.eu_destination_ports()


def test_priced_countries_cover_uk_and_eu27():
    assert len(load_rate_book().priced_countries()) == 28


def test_default_port_for_country():
    rb = load_rate_book()
    assert rb.default_port_for_country("FR")[0] == "FRLEH"
    assert rb.default_port_for_country("AT")[0] == "DEHAM"
    assert rb.default_port_for_country("GB") == (None, "")


def test_every_eu_default_port_has_ocean_rates_and_delivery():
    rb = load_rate_book()
    for _, row in rb.eu_country_ports.iterrows():
        port = row["default_port"]
        assert port in rb.eu_destination_ports(), port
        assert rb.has_lane("CNSHA", port, Service.LCL) and rb.has_lane("CNNGB", port, Service.FCL, ["40HC"])
        assert rb.has_eu_delivery(port, row["country"], "1", Service.FCL), row["country"]


def test_port_alias_eu():
    rb = load_rate_book()
    assert rb.port_code("Rotterdam") == "NLRTM" and rb.port_code("Göteborg") == "SEGOT"
    assert rb.port_code("Bremerhaven") == "DEBRV"   # known, but not priced


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
    assert "DEHAM" in rb.destinations_for("CNSHA", Service.LCL, country="DE")
