"""Run the agent on one email: details, decision, questions and (if quotable) the priced quote.
MOCK / anonymised emails only.

    python -m src.try_email data/sample_enquiries/I12.txt
    python -m src.try_email                                   # paste, then Enter, Ctrl+Z, Enter (Windows)

For Germany/France deliveries it prints a "Maersk inland price look-up" box FIRST: type those fields into
maersk.com, then enter the haulage and fuel you see. Only then is the quote shown and saved to
quotes/quote-<date-time>.txt. Skip (press Enter) and no quote is made (use --estimate to force one).

You can also pass the figures directly:
    python -m src.try_email email.txt --haulage 650 --fuel 46.80 --extra "Toll=35" --heavy 0
    (--fuel is Maersk's fuel AMOUNT per container. The customer sees one "Road haulage" line = haulage + Maersk fuel,
     then "Fuel surcharge 22%" on top of it.)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.haulage import HaulageCheck, lookup_instructions, needs_lookup
from src.pipeline import run_enquiry
from src.quote import price
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


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="email text file (omit to paste)")
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
    lookup = r.quote is not None and needs_lookup(r.enquiry)

    # When Maersk haulage is needed, show NO price until staff have entered the Maersk figures.
    print()
    print(format_result(r.enquiry, r.triage, None if lookup else r.quote))
    if not r.quote:
        return

    quote = r.quote
    if lookup:
        print()
        print(lookup_instructions(r.enquiry))
        check = None
        if args.haulage is not None:
            if args.fuel is None:
                ap.error("--haulage needs --fuel too")
            check = HaulageCheck(args.haulage, args.fuel, args.extra, args.heavy)
        elif args.file and not args.no_prompt and not args.estimate and sys.stdin.isatty():
            n = sum(c.quantity for c in r.enquiry.containers) or 1
            heavy_possible = r.enquiry.total_gross_weight_kg is None or r.enquiry.total_gross_weight_kg / n > 23000
            check = ask_for_check(heavy_possible)

        if check:
            quote = price(r.enquiry, haulage_check=check)
        elif args.estimate:
            print("\n--estimate: quote made with the flat ESTIMATE haulage (marked for staff).")
        else:
            print("\nNo quote made: enter the Maersk haulage and fuel first.\n"
                  "Re-run and type them when asked, or pass --haulage <amount> --fuel <amount>.\n"
                  "(Use --estimate to make an estimate quote anyway.)")
            return
        print()
        print(format_quote(quote))

    path = save_quote_text(render_quote_text(r.enquiry, quote, r.triage))
    print(f"\nQuote saved as a text file: {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
