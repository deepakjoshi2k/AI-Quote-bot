"""Run the agent on one email: details, decision, questions and (if quotable) the priced quote.
MOCK / anonymised emails only.

    python -m src.try_email data/sample_enquiries/I12.txt
    python -m src.try_email                                   # paste, then Enter, Ctrl+Z, Enter (Windows)

For Germany/France deliveries it prints a "Maersk inland price look-up" box FIRST: type those fields into
maersk.com, then enter the haulage and fuel you see. Only then is the quote shown and saved to
quotes/quote-<date-time>.txt. Skip (press Enter) and no quote is made (use --estimate to force one).

No port of arrival in the email? It shows the 3 nearest container ports BY ROAD (needs ORS_API_KEY in .env):
  - the sender ASKED us to suggest a port ("advise nearest POD"): the nearest port we can price is filled in;
  - the sender did NOT ask: no port is filled in. The decision is NEED_INFO (ask the sender which port the
    container is booked into); staff may still pick a port from the list to quote now (Enter = just ask).
Pick another port or several (one quote per port):
    python -m src.try_email email.txt --ports 1,2        # numbers from the list, or LOCODEs: --ports FRLEH,DEHAM

You can also pass the figures directly:
    python -m src.try_email email.txt --haulage 650 --fuel 46.80 --extra "Toll=35" --heavy 0
    (--fuel is Maersk's fuel AMOUNT per container. The customer sees one "Road haulage" line = haulage + Maersk fuel,
     then "Fuel surcharge 22%" on top of it.)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from src.config import load_config
from src.distance import DistanceError, PortOption, format_options, load_ports, nearest_ports
from src.haulage import HaulageCheck, carrier_haulage, lookup_instructions, needs_lookup
from src.models import Enquiry
from src.quote import PORT_NAMES
from src.scope import quote_scope
from src.tariff import port_country, pricing_tariff, tariff_for
from src.triage import PORT_QUESTION, triage
from src.pipeline import run_enquiry
from src.quote import price
from src.rates import load_rate_book
from src.quote_text import render_quote_text, save_quote_text
from src.report import format_quote, format_result


def _parse_extra(text: str) -> tuple[str, float]:
    name, _, amount = text.partition("=")
    if not name.strip() or not amount.strip():
        raise argparse.ArgumentTypeError(f"--extra must look like 'Name=35', got {text!r}")
    return name.strip(), float(amount)


def _ask_number(prompt: str, allow_blank: bool = True) -> float | None:
    while True:
        raw = input(prompt).strip().replace("€", "").replace(",", ".").rstrip("%")
        if raw == "" and allow_blank:
            return None
        try:
            return float(raw)
        except ValueError:
            print("  Please type a number (e.g. 650 or 7.2), or press Enter to skip.")


def ask_for_check(heavy_possible: bool) -> HaulageCheck | None:
    """Interactive paste-back after the staff look-up. Enter on the first question = skip (estimate)."""
    print()
    haulage = _ask_number("Maersk haulage per container, IHI (EUR) [Enter = skip, no quote]: ")
    if haulage is None:
        return None
    fuel = _ask_number("Maersk import fuel surcharge per container, IFS (EUR amount, not %): ", allow_blank=False)
    extras: list[tuple[str, float]] = []
    print("Other mandatory inland charges per container, as Name=amount (Enter on an empty line to finish):")
    while True:
        line = input("  > ").strip()
        if not line:
            break
        try:
            extras.append(_parse_extra(line))
        except (argparse.ArgumentTypeError, ValueError) as err:
            print(f"  {err}")
    heavy = None
    if heavy_possible:
        heavy = _ask_number("Heavy/weight surcharge per container (EUR, 0 if included) [Enter = use our 200]: ")
    return HaulageCheck(haulage_per_container=haulage, fuel_per_container=fuel, extras=extras, heavy_surcharge=heavy)


def can_price_port(e: Enquiry, locode: str) -> bool:
    """A port we can price for this enquiry.
    Tariff (DE/FR): the port's country has a tariff and we have Maersk haulage for the delivery country.
    Mock sheets (GB...): the port is in the delivery country and in the rate sheet."""
    if tariff_for(e.destination_country) or pricing_tariff(locode, e.destination_country):
        return (pricing_tariff(locode, e.destination_country) is not None
                and (not quote_scope(e).delivery or carrier_haulage(e.destination_country) is not None))
    return port_country(locode) == e.destination_country and locode in load_rate_book().destination_ports()


def staff_can_choose_port(e: Enquiry, t) -> bool:
    """need_info only because the arrival port is missing: staff may pick one of the nearest ports to quote now."""
    return (t.status == "need_info" and t.required_fields == [PORT_QUESTION]
            and bool(e.destination_country) and bool(e.delivery_postcode or e.delivery_place))


def parse_port_choice(text: str, options: list[PortOption]) -> list[str]:
    """'1,2' -> the 1st and 2nd suggested ports; 'FRLEH, DEHAM' -> those LOCODEs. Order kept, no repeats."""
    chosen: list[str] = []
    for part in (p.strip() for p in text.replace(";", ",").split(",")):
        if not part:
            continue
        if part.isdigit():
            i = int(part)
            if not 1 <= i <= len(options):
                raise ValueError(f"No port number {i} in the list (1-{len(options)}).")
            code = options[i - 1].locode
        else:
            code = part.upper() if part.upper() in {p.locode for p in load_ports()} else load_rate_book().port_code(part)
            if not code:
                raise ValueError(f"{part!r} is not a list number or a known port (e.g. 1, FRLEH or Le Havre).")
        if code not in chosen:
            chosen.append(code)
    if not chosen:
        raise ValueError("No port chosen.")
    return chosen


def suggest_ports(e: Enquiry) -> tuple[list[PortOption], str | None]:
    """Nearest ports by road for an enquiry with no port; prints the list. Returns (options, default LOCODE)."""
    cfg = load_config().get("nearest_ports", {})
    show = cfg.get("show", 3)
    try:
        # Measure every candidate, show the nearest few + the nearest one we can price if it isn't among them.
        here, measured = nearest_ports(e.destination_country, e.delivery_postcode, e.delivery_place,
                                       show=cfg.get("candidates", 10))
    except DistanceError as err:
        print(f"\nNearest ports by road not available: {err}\nChoose the port yourself (--ports FRLEH).")
        return [], None
    priced = {o.locode: can_price_port(e, o.locode) for o in measured}
    options = measured[:show]
    if not any(priced[o.locode] for o in options):
        options += [o for o in measured[show:] if priced[o.locode]][:1]
    print()
    print(format_options(here, options, priced))
    default = next((o.locode for o in options if priced[o.locode]), None)
    return options, default


def choose_ports(e: Enquiry, args, interactive: bool, auto: bool = True) -> tuple[list[str | None], dict[str, str]]:
    """Which arrival port(s) to quote. Returns (ports, notes per port).
    auto=True  (sender asked us to suggest a port): the nearest port we can price is filled in.
    auto=False (sender didn't ask): nothing is filled in; staff must choose, else no quote ([]).
    [None] = no port and none chosen (single quote, the look-up box asks staff to pick)."""
    if e.destination_port:
        if args.ports:
            print(f"\nThe email names {e.destination_port}: --ports ignored.")
        return [e.destination_port], {}
    options, default = suggest_ports(e)
    km = {o.locode: o.road_km for o in options}
    if args.ports:
        try:
            ports = parse_port_choice(args.ports, options)
        except ValueError as err:
            raise SystemExit(f"--ports: {err}")
    elif interactive and options:
        if auto:
            hint = f"Enter = {default}" if default else "type a port code"
        else:
            hint = "Enter = none: just ask the sender"
        while True:
            raw = input(f"\nPort(s) to quote: list number(s) or LOCODE(s), e.g. 1 or 1,2 [{hint}]: ").strip()
            if not raw and not auto:
                return [], {}
            if not raw and default:
                ports = [default]
                break
            try:
                ports = parse_port_choice(raw, options)
                break
            except ValueError as err:
                print(f"  {err}")
    elif not auto:
        return [], {}
    elif default:
        ports = [default]
        print(f"\nAuto-filled the nearest port we can price: {default} (use --ports to choose others).")
    else:
        return [None], {}

    keep: list[str | None] = []
    for code in ports:
        if can_price_port(e, code):
            keep.append(code)
        else:
            print(f"\n{code}: no tariff for {code[:2]} (or no haulage for {e.destination_country}): "
                  "quote this port manually.")
    notes = {c: (f"nearest by road, {km[c]:,.0f} km (staff choice)" if c in km else "staff choice") for c in keep if c}
    return keep, notes


def get_check(args, ap, e: Enquiry, interactive: bool) -> HaulageCheck | None:
    if args.haulage is not None:
        if args.fuel is None:
            ap.error("--haulage needs --fuel too")
        return HaulageCheck(args.haulage, args.fuel, args.extra, args.heavy)
    if interactive:
        n = sum(c.quantity for c in e.containers) or 1
        heavy_possible = e.total_gross_weight_kg is None or e.total_gross_weight_kg / n > 23000
        return ask_for_check(heavy_possible)
    return None


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="email text file (omit to paste)")
    ap.add_argument("--ports", help="arrival port(s) when the email names none: list numbers or LOCODEs, e.g. 1,2")
    ap.add_argument("--haulage", type=float, help="Maersk haulage per container (EUR)")
    ap.add_argument("--fuel", type=float, help="Maersk import fuel surcharge per container (EUR amount, not %%)")
    ap.add_argument("--extra", type=_parse_extra, action="append", default=[], help="other inland charge, Name=amount")
    ap.add_argument("--heavy", type=float, help="heavy surcharge per container (0 if included); 22%% fuel is added")
    ap.add_argument("--no-prompt", action="store_true", help="don't ask for Maersk figures")
    ap.add_argument("--estimate", action="store_true",
                    help="make the quote with the flat ESTIMATE haulage instead of asking for Maersk figures")
    args = ap.parse_args(argv)

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        print("Paste the email, then press Enter, Ctrl+Z, Enter (Windows) or Ctrl+D (Mac/Linux):\n")
        text = sys.stdin.read()

    r = run_enquiry(text)
    e0, t0 = r.enquiry, r.triage
    staff_pick = staff_can_choose_port(e0, t0)          # port missing, sender didn't ask us to suggest one
    lookup = (r.quote is not None or staff_pick) and needs_lookup(e0)

    # When Maersk haulage is needed, show NO price until staff have entered the Maersk figures.
    print()
    print(format_result(e0, t0, None if lookup else r.quote))
    if r.quote is None and not staff_pick:
        return
    if r.quote is not None and not lookup:
        path = save_quote_text(render_quote_text(e0, r.quote, t0))
        print(f"\nQuote saved as a text file: {path}")
        return

    can_ask = bool(args.file) and not args.no_prompt and sys.stdin.isatty()
    interactive = can_ask and not args.estimate
    if staff_pick:
        print("\nSTAFF: the email has no port of arrival and the sender did not ask us to suggest one.\n"
              "Send them the question above. To quote now, choose a port from the nearest ones below.")
    ports, port_notes = choose_ports(e0, args, can_ask, auto=not staff_pick)
    if not ports:
        print("\nNo port chosen: no quote made. Send the questions above to the sender." if staff_pick
              else "\nNo port we can price was chosen: no quote made.")
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")   # shared by all ports of this run
    for port in ports:
        chosen = port is not None and port != e0.destination_port
        e = e0.model_copy(update={"destination_port": port}) if chosen else e0
        t = triage(e) if chosen else t0
        if t.status != "quote":
            print(f"\n{port}: cannot quote automatically: " + "; ".join(t.reasons or t.missing_fields))
            continue
        if chosen:
            # Not in the email: the sender must confirm the port the container is booked into.
            confirm = (f"port of arrival: quoted via {PORT_NAMES.get(port, port)} ({port}); "
                       "please confirm the port the container is booked into")
            t = t.model_copy(update={"missing_fields": [confirm] + [m for m in t.missing_fields
                                                                    if "port of arrival" not in m]})
        if needs_lookup(e):
            print()
            print(lookup_instructions(e, port_note=port_notes.get(port)))
            check = get_check(args, ap, e, interactive)
            if check:
                quote = price(e, haulage_check=check)
            elif args.estimate:
                print("\n--estimate: quote made with the flat ESTIMATE haulage (marked for staff).")
                quote = price(e)
            else:
                print("\nNo quote made" + (f" for {port}" if port else "") + ": enter the Maersk haulage and fuel "
                      "first.\nRe-run and type them when asked, or pass --haulage <amount> --fuel <amount>.\n"
                      "(Use --estimate to make an estimate quote anyway.)")
                continue
        else:
            quote = price(e)
        print()
        print(format_quote(quote))
        text_out = render_quote_text(e, quote, t)
        # several ports -> one file per port: quote-<date-time>-<PORT>.txt
        path = save_quote_text(text_out, stamp=f"{stamp}-{port}") if len(ports) > 1 and port else save_quote_text(text_out)
        print(f"\nQuote saved as a text file: {path}")

if __name__ == "__main__":
    main(sys.argv[1:])
