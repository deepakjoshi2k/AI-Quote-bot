"""Step 5: price the import quote. Pure Python, no LLM (rule 1).

What goes in comes from src/scope.py (agreed with Lincoln Cargo's process):
  Destination : terminal handling (per container) + release fee (per bill of lading)
  Customs     : import customs entry, only if the sender asked for clearance
  Delivery    : container haulage from the arrival port to the delivery zone (always, unless declined)
  Countries with a tariff in data/tariffs/ (Germany, France) are priced from that tariff instead.
  + margin (config.yaml). Duties and import VAT are never included.

Call only for enquiries that triage marked "quote" (FCL, port known, rates in the sheets).
Amounts are kept exact and rounded to 2 dp only at the end.
"""
from __future__ import annotations

from src.config import load_config
from src.models import Enquiry, Quote, QuoteLine, Service
from src.rates import RateBook, delivery_rate, destination_charge, load_rate_book
from src.haulage import HaulageCheck, carrier_haulage, haulage_markup_pct
from src.scope import quote_scope
from src.tariff import tariff_for, terms_for
from src.zones import zone_for_address

PORT_NAMES = {
    "GBFXT": "Felixstowe", "GBSOU": "Southampton", "GBLGP": "London Gateway", "NLRTM": "Rotterdam",
    "BEANR": "Antwerp", "DEHAM": "Hamburg", "FRLEH": "Le Havre", "ESVLC": "Valencia", "ITGOA": "Genoa",
    "GRPIR": "Piraeus", "PLGDN": "Gdansk", "SEGOT": "Gothenburg", "SIKOP": "Koper", "FRFOS": "Fos-sur-Mer",
    "DEBRV": "Bremerhaven", "FRMRS": "Marseille",
}

STANDARD_EXCLUSIONS = [
    "Import duty and import VAT (at cost, payable to the customs authority)",
    "Customs examinations, inspections and port health checks (at cost)",
    "Demurrage, detention and port storage beyond free time",
    "Waiting time at delivery beyond the free allowance",
    "Ocean freight and origin charges",
]


class PricingError(ValueError):
    pass


def _line(section: str, code: str, description: str, basis: str, qty: float, rate: float) -> QuoteLine:
    return QuoteLine(section=section, code=code, description=description, basis=basis,
                     quantity=qty, unit_rate=float(rate), amount=qty * float(rate))


_BASIS = {"container": "per container", "bl": "per bill of lading", "hbl": "per house bill of lading"}


def _price_tariff(e: Enquiry, tariff: dict, cfg: dict, check: HaulageCheck | None = None) -> Quote:
    """Price from a country tariff (same charges at every port in the country)."""
    from src.models import Incoterm
    scope = quote_scope(e)
    if e.service is not Service.FCL and not e.clearance_only:
        raise PricingError("Only FCL is priced automatically (LCL is manual for now).")
    n = sum(c.quantity for c in e.containers)
    if not e.clearance_only and n == 0:
        raise PricingError("Container size and number are needed.")
    per_container_kg = (e.total_gross_weight_kg / n) if (e.total_gross_weight_kg and n) else None

    # Lincoln Cargo's own destination charges (tariff)
    wanted = {"always": not e.clearance_only, "clearance": scope.import_clearance}
    section_of = {"always": "Destination", "clearance": "Customs"}
    lines: list[QuoteLine] = []
    for ch in tariff["charges"]:
        if not wanted.get(ch["applies"], False):
            continue
        qty = n if ch["basis"] == "container" else 1
        lines.append(_line(section_of[ch["applies"]], ch["code"], ch["description"], _BASIS[ch["basis"]],
                           qty, ch["amount"]))

    # Carrier haulage: always Maersk COST (staff look-up, else the flat estimate in data/haulage/maersk.yaml)
    # + hidden markup (HAULAGE_MARKUP_PCT in .env) on haulage + fuel. The customer sees only selling prices,
    # with neutral labels: no carrier name, no fuel %, no markup.
    heavy = False
    h = None
    estimate = False
    internal: list[str] = []
    if scope.delivery:
        h = carrier_haulage(e.destination_country)
        if h is None:
            raise PricingError(f"No Maersk haulage rate for {e.destination_country}.")
        carrier = h["carrier"]
        if check:
            base, fuel_amt = check.haulage_per_container, check.fuel_per_container
            internal.append(f"Haulage cost from the {check.source} on {check.checked_on:%d %b %Y}.")
        else:
            base = h["haulage_per_container"]
            fuel_amt = base * h["fuel_pct"] / 100
            estimate = True
            internal.append(f"ESTIMATE: flat {carrier} haulage {base:.2f} + fuel {h['fuel_pct']:g}% "
                            f"(updated {h['fuel_updated']}). Check the {carrier} inland price look-up.")
        markup = haulage_markup_pct()
        k = 1 + markup / 100
        lines.append(_line("Delivery", "HAUL", "Road haulage", "per container", n, base * k))
        lines.append(_line("Delivery", "HAUL-FUEL", "Fuel surcharge", "per container", n, fuel_amt * k))
        cost = (base + fuel_amt) * n
        internal.append(f"{carrier} cost per container: haulage {base:.2f} + fuel {fuel_amt:.2f} = {base + fuel_amt:.2f}"
                        f" (x{n} = {cost:.2f}).")
        internal.append(f"Hidden markup {markup:g}% on haulage + fuel = {cost * markup / 100:.2f}; "
                        f"customer sees {cost * k:.2f}.")
        for name, amount in (check.extras if check else []):
            lines.append(_line("Delivery", "INLAND", name, "per container", n, amount))
        heavy = per_container_kg is not None and per_container_kg > h["heavy_over_kg"]
        if check is None or check.heavy_surcharge is None:
            heavy_amount = h["heavy_surcharge"] * (1 + h["fuel_pct"] / 100)   # our tariff: EUR 200 + fuel
        else:
            heavy_amount = check.heavy_surcharge                              # staff figure, incl. fuel
        if heavy and heavy_amount:
            lines.append(_line("Delivery", "HEAVY", f"Heavy container surcharge (over {h['heavy_over_kg'] / 1000:g} t), "
                               "incl. fuel", "per container", n, heavy_amount))

    if not lines:
        raise PricingError("Nothing to price for this enquiry.")

    cur = tariff["currency"]
    subtotal = sum(l.amount for l in lines)
    margin = subtotal * tariff.get("margin_pct", 0) / 100
    assumptions = []
    if not e.clearance_only:
        assumptions.append("Priced for 1 bill of lading.")
    if scope.import_clearance:
        assumptions.append("Priced for 1 house bill of lading (customs).")
    if h is not None and per_container_kg is None:
        assumptions.append(f"Weight not stated: heavy surcharge applies if over {h['heavy_over_kg'] / 1000:g} t per container.")
    exclusions = ["Import duty and import VAT (at cost)", "Ocean freight and origin charges"]
    if not scope.import_clearance:
        exclusions.insert(0, "Customs clearance (available on request)")
    if not scope.delivery and not e.clearance_only:
        exclusions.append("Delivery from the port (declined by sender)")

    port = e.destination_port
    country = e.destination_country
    where = ", ".join(filter(None, [e.delivery_place, e.delivery_postcode]))
    lane = (f"{PORT_NAMES.get(port, port)} ({port})" if port else f"Any port in {country}")
    if scope.delivery and where:
        lane += f" → {where}"
    return Quote(
        lane=lane, service=e.service, incoterm=e.incoterm, currency=cur,
        lines=[l.model_copy(update={"amount": round(l.amount, 2)}) for l in lines],
        subtotal=round(subtotal, 2), margin=round(margin, 2), total=round(subtotal + margin, 2),
        validity_days=cfg["validity_days"], assumptions=assumptions, exclusions=exclusions,
        terms=terms_for(tariff, country, ddp=e.incoterm is Incoterm.DDP),
        source=tariff["name"], is_mock=tariff.get("is_mock", False), haulage_estimate=estimate,
        internal_notes=internal,
    )


def price(e: Enquiry, rate_book: RateBook | None = None, cfg: dict | None = None,
          haulage_check: HaulageCheck | None = None) -> Quote:
    rb = rate_book or load_rate_book()
    cfg = cfg or load_config()
    tariff = tariff_for(e.destination_country)
    if tariff:
        return _price_tariff(e, tariff, cfg, haulage_check)
    scope = quote_scope(e)
    country = e.destination_country
    port = e.destination_port
    n_containers = sum(c.quantity for c in e.containers)
    lines: list[QuoteLine] = []
    assumptions: list[str] = []

    if (scope.destination_handling or scope.delivery) and e.service is not Service.FCL:
        raise PricingError("Only FCL is priced automatically (LCL is manual for now).")
    if (scope.destination_handling or scope.delivery) and not port:
        raise PricingError("Port of arrival is needed to price terminal handling and delivery.")

    # Destination: terminal handling per container + release fee per bill of lading
    if scope.destination_handling:
        for c in e.containers:
            row = destination_charge(rb, country, port, "DTHC")
            lines.append(_line("Destination", "DTHC", f"{row['description']} – {c.container_type}",
                               "per container", c.quantity, row["rate"]))
        do = destination_charge(rb, country, port, "DO")
        lines.append(_line("Destination", "DO", do["description"], "per bill of lading", 1, do["rate"]))
        assumptions.append(f"Priced for 1 bill of lading; each extra B/L {cfg['currency']} {do['rate']:.2f}.")

    # Customs: only if asked
    if scope.import_clearance:
        clr = destination_charge(rb, country, port or "ALL", "IMPCLR")
        lines.append(_line("Customs", "IMPCLR", clr["description"], "per entry", 1, clr["rate"]))
        assumptions.append(f"Priced for 1 customs entry; each extra entry {cfg['currency']} {clr['rate']:.2f}.")

    # Delivery: per container, first + additional, by port and delivery zone
    if scope.delivery:
        if not e.delivery_postcode:
            raise PricingError("Delivery postcode is needed to price delivery.")
        zone = zone_for_address(country, e.delivery_postcode)
        if zone in (None, "MANUAL"):
            raise PricingError(f"No automatic delivery zone for {e.delivery_postcode}.")
        d = delivery_rate(rb, country, port, zone)
        where = e.delivery_postcode if not e.delivery_place else f"{e.delivery_place} {e.delivery_postcode}"
        lines.append(_line("Delivery", "HAUL", f"Container haulage {PORT_NAMES.get(port, port)} → {where} (zone {zone})",
                           "per container (first)", 1, d["first_unit"]))
        if n_containers > 1:
            lines.append(_line("Delivery", "HAUL", "Container haulage, each additional container",
                               "per container", n_containers - 1, d["additional_unit"]))

    if not lines:
        raise PricingError("Nothing to price for this enquiry.")

    subtotal = sum(l.amount for l in lines)
    margin = subtotal * cfg["margin_pct"] / 100
    exclusions = list(STANDARD_EXCLUSIONS)
    if not scope.import_clearance:
        exclusions.insert(0, "Import customs clearance (available on request)")
    if not scope.delivery and not e.clearance_only:
        exclusions.append("Delivery from the port (available on request)")
    assumptions.append("MOCK RATES – PROTOTYPE: not real Lincoln Cargo prices.")

    if port:
        lane = f"{PORT_NAMES.get(port, port)} ({port})" + (f" → {e.delivery_postcode}" if scope.delivery else "")
    else:
        lane = f"Import customs clearance ({country or 'GB'})"

    return Quote(
        lane=lane, service=e.service, incoterm=e.incoterm, currency=cfg["currency"],
        lines=[l.model_copy(update={"amount": round(l.amount, 2)}) for l in lines],
        subtotal=round(subtotal, 2), margin=round(margin, 2), total=round(subtotal + margin, 2),
        validity_days=cfg["validity_days"], assumptions=assumptions, exclusions=exclusions,
        source="MOCK rate sheets (data/*.csv)", is_mock=True,
    )
