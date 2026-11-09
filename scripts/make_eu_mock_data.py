"""Generates the MOCK EU rate sheets in data/. All numbers are invented.

    python -m scripts.make_eu_mock_data

Edit the tables below and re-run, or replace the CSVs with Lincoln Cargo's real rates
(keep the same columns). Prices are GBP equivalents for now (EUR handling is a later feature).
"""
from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
MOCK = "MOCK - NOT REAL RATES"

# EU hub ports we price ocean freight into: code -> (name, ocean factor vs Felixstowe, FCL DTHC)
HUBS = {
    "NLRTM": ("Rotterdam", 0.97, 230), "BEANR": ("Antwerp", 0.98, 220), "DEHAM": ("Hamburg", 1.00, 240),
    "FRLEH": ("Le Havre", 1.00, 235), "ESVLC": ("Valencia", 0.92, 190), "ITGOA": ("Genoa", 0.93, 210),
    "GRPIR": ("Piraeus", 0.88, 180), "PLGDN": ("Gdansk", 1.06, 170), "SEGOT": ("Gothenburg", 1.08, 225),
    "SIKOP": ("Koper", 0.95, 185),
}

# Base Asia/Middle East -> North Europe ocean rates (GBP), per origin
ORIGINS = {
    "CNSHA": ("Shanghai", {"LCL": 38, "20GP": 1400, "40GP": 1980, "40HC": 2050}, "30-36"),
    "CNNGB": ("Ningbo", {"LCL": 36, "20GP": 1350, "40GP": 1950, "40HC": 2000}, "30-36"),
    "INNSA": ("Nhava Sheva", {"LCL": 42, "20GP": 1150, "40GP": 1650, "40HC": 1700}, "24-30"),
    "VNSGN": ("Ho Chi Minh City", {"LCL": 44, "20GP": 1500, "40GP": 2150, "40HC": 2250}, "28-34"),
    "AEJEA": ("Jebel Ali", {"LCL": 40, "20GP": 950, "40GP": 1400, "40HC": 1450}, "20-26"),
}

# country -> (name, default port, km zone 1, km zone 2, feeder/ferry surcharge?, note)
COUNTRIES = {
    "AT": ("Austria", "DEHAM", 900, 1000, False, ""),
    "BE": ("Belgium", "BEANR", 80, 80, False, ""),
    "BG": ("Bulgaria", "GRPIR", 750, 850, False, "Constanta or Varna may be better: confirm"),
    "HR": ("Croatia", "SIKOP", 150, 450, False, ""),
    "CY": ("Cyprus", "GRPIR", 100, 150, True, "feeder Piraeus -> Limassol: confirm"),
    "CZ": ("Czechia", "DEHAM", 650, 750, False, ""),
    "DK": ("Denmark", "DEHAM", 300, 450, False, ""),
    "EE": ("Estonia", "PLGDN", 900, 950, False, ""),
    "FI": ("Finland", "SEGOT", 250, 500, True, "ferry to Finland: confirm routing"),
    "FR": ("France", "FRLEH", 200, 550, False, ""),
    "DE": ("Germany", "DEHAM", 150, 550, False, ""),
    "GR": ("Greece", "GRPIR", 50, 400, False, "islands need a manual quote"),
    "HU": ("Hungary", "SIKOP", 500, 600, False, ""),
    "IE": ("Ireland", "NLRTM", 150, 300, True, "feeder / ro-ro to Ireland: confirm"),
    "IT": ("Italy", "ITGOA", 150, 650, False, ""),
    "LV": ("Latvia", "PLGDN", 650, 700, False, ""),
    "LT": ("Lithuania", "PLGDN", 450, 550, False, ""),
    "LU": ("Luxembourg", "BEANR", 220, 220, False, ""),
    "MT": ("Malta", "ITGOA", 100, 100, True, "feeder Genoa -> Malta: confirm"),
    "NL": ("Netherlands", "NLRTM", 80, 150, False, ""),
    "PL": ("Poland", "PLGDN", 150, 500, False, ""),
    "PT": ("Portugal", "ESVLC", 800, 950, False, "Sines/Lisbon may be better: confirm"),
    "RO": ("Romania", "GRPIR", 1000, 1150, False, "Constanta may be better: confirm"),
    "SK": ("Slovakia", "DEHAM", 1000, 1100, False, ""),
    "SI": ("Slovenia", "SIKOP", 50, 150, False, ""),
    "ES": ("Spain", "ESVLC", 150, 500, False, ""),
    "SE": ("Sweden", "SEGOT", 100, 500, False, ""),
}

# Postcode prefixes: zone 1 (near the port) and MANUAL (islands / outermost regions). Everything else zone 2.
ZONE1 = {
    "FR": "75;76;77;78;91;92;93;94;95;27;14;60;80;62;59", "DE": "19;20;21;22;23;24;25;26;27;28;29;30;31",
    "ES": "46;03;12;02;16;30", "IT": "10;12;13;15;16;17;18;19;20", "GR": "10;11;12;13;14;15;16;17;18;19",
    "PL": "80;81;82;83;84;85;86", "SE": "40;41;42;43;44;45;46;47", "HR": "51;52", "NL": "*", "BE": "*",
    "LU": "*", "SI": "*", "CY": "*", "MT": "*", "DK": "6;7", "FI": "0", "IE": "D", "AT": "*", "CZ": "*",
    "SK": "*", "HU": "*", "EE": "*", "LV": "*", "LT": "*", "BG": "*", "RO": "*", "PT": "*",
}
MANUAL = {
    "FR": ("20;97;98", "Corsica / overseas departments"), "ES": ("07;35;38;51;52", "Balearics / Canaries / Ceuta / Melilla"),
    "PT": ("9", "Azores / Madeira"), "IT": ("07;08;09", "Sardinia"),
}

FEEDER_FCL, FEEDER_LCL = 400, 60


def fcl_delivery(km: float, feeder: bool) -> float:
    return round(300 + 1.6 * km + (FEEDER_FCL if feeder else 0), -1)


def lcl_first(km: float, feeder: bool) -> float:
    return round(70 + 0.12 * km + (FEEDER_LCL if feeder else 0))


def write(name: str, header: list[str], rows: list[list]) -> None:
    with open(DATA / name, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    print(f"wrote {name}: {len(rows)} rows")


def main() -> None:
    write("eu_country_ports.csv", ["country", "name", "default_port", "note"],
          [[c, n, p, note] for c, (n, p, _, _, _, note) in COUNTRIES.items()])

    zone_rows = []
    for c in COUNTRIES:
        z1 = ZONE1[c]
        zone_rows.append([c, "1", z1, "near the default port / main area" if z1 != "*" else "whole country"])
        if z1 != "*":
            zone_rows.append([c, "2", "*", "rest of country"])
        if c in MANUAL:
            zone_rows.append([c, "MANUAL", MANUAL[c][0], MANUAL[c][1]])
    write("eu_postcode_zones.csv", ["country", "zone", "postcode_prefixes", "description"], zone_rows)

    deliv = []
    for c, (_, port, km1, km2, feeder, _) in COUNTRIES.items():
        zones = [("1", km1)] if ZONE1[c] == "*" else [("1", km1), ("2", km2)]
        for zone, km in zones:
            f = fcl_delivery(km, feeder)
            deliv.append(["FCL", port, c, zone, f"{f:.2f}", f"{f:.2f}", "per container", "GBP", MOCK])
            first = lcl_first(km, feeder)
            deliv.append(["LCL", port, c, zone, f"{first:.2f}", f"{round(first * 0.4):.2f}", "per pallet", "GBP", MOCK])
    write("eu_delivery_rates.csv",
          ["service", "port", "country", "zone", "first_unit", "additional_unit", "unit", "currency", "data_status"], deliv)

    dest = [["FCL", p, "DTHC", f"Destination terminal handling ({n})", "per container", f"{thc:.2f}", "0", "GBP", MOCK]
            for p, (n, _, thc) in HUBS.items()]
    dest += [
        ["FCL", "ALL", "DO", "Delivery order / release fee (EU)", "per bill of lading", "60.00", "0", "GBP", MOCK],
        ["LCL", "ALL", "DCFS", "Destination CFS / devanning (EU)", "per W/M", "30.00", "30.00", "GBP", MOCK],
        ["LCL", "ALL", "DO", "Delivery order / release fee (EU)", "per bill of lading", "50.00", "0", "GBP", MOCK],
        ["ALL", "ALL", "IMPCLR", "EU import customs declaration (via EU customs partner)", "per entry", "85.00", "0", "GBP", MOCK],
        ["ALL", "ALL", "DUTYVAT", "EU import duty and import VAT", "at cost (paid to the EU customs authority)", "0", "0", "GBP",
         "NOT INCLUDED - pass-through"],
    ]
    write("eu_destination_charges.csv",
          ["service", "port", "code", "description", "basis", "rate", "min_charge", "currency", "data_status"], dest)

    # Ocean rates into the EU hubs, appended to rates_sea_import.csv (UK rows are kept as they are)
    path = DATA / "rates_sea_import.csv"
    with open(path, newline="", encoding="utf-8") as f:
        existing = [r for r in csv.reader(f)]
    header, uk_rows = existing[0], [r for r in existing[1:] if r[3].startswith("GB")]
    eu_rows = []
    for o, (oname, base, transit) in ORIGINS.items():
        for p, (_, factor, _) in HUBS.items():
            for svc, rate in base.items():
                key = "LCL" if svc == "LCL" else f"FCL-{svc}"
                basis = "per W/M" if svc == "LCL" else "per container"
                value = round(rate * factor, 2) if svc == "LCL" else round(rate * factor, -1)
                eu_rows.append([key, o, oname, p, "GBP", basis, f"{value:.2f}", "1", transit, MOCK])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(uk_rows + eu_rows)
    print(f"rates_sea_import.csv: {len(uk_rows)} UK rows kept + {len(eu_rows)} EU rows")


if __name__ == "__main__":
    main()
