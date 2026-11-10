"""Run the agent on one email: details, decision, questions and (if quotable) the priced quote.
MOCK / anonymised emails only.

    python -m src.try_email data/sample_enquiries/I12.txt     # from a file
    python -m src.try_email                                   # paste, then Enter, Ctrl+Z, Enter (Windows)

If the email is quotable, the customer-facing quote is also saved to quotes/quote-<date-time>.txt.
"""
from __future__ import annotations

import sys
from pathlib import Path

from src.pipeline import run_enquiry
from src.quote_text import render_quote_text, save_quote_text
from src.report import format_result


def main(argv: list[str]) -> None:
    if argv:
        text = Path(argv[0]).read_text(encoding="utf-8")
    else:
        print("Paste the email, then press Enter, Ctrl+Z, Enter (Windows) or Ctrl+D (Mac/Linux):\n")
        text = sys.stdin.read()
    r = run_enquiry(text)
    print()
    print(format_result(r.enquiry, r.triage, r.quote))
    if r.quote:
        path = save_quote_text(render_quote_text(r.enquiry, r.quote, r.triage))
        print(f"\nQuote saved as a text file: {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
