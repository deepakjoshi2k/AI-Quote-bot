"""Run the agent on one email and print what it found. MOCK / anonymised emails only.

    python -m src.try_email data/sample_enquiries/I12.txt     # from a file
    python -m src.try_email                                   # paste, then Enter, Ctrl+Z, Enter (Windows)
"""
from __future__ import annotations

import sys
from pathlib import Path

from src.extract import extract_enquiry
from src.report import format_result
from src.triage import triage


def main(argv: list[str]) -> None:
    if argv:
        text = Path(argv[0]).read_text(encoding="utf-8")
    else:
        print("Paste the email, then press Enter, Ctrl+Z, Enter (Windows) or Ctrl+D (Mac/Linux):\n")
        text = sys.stdin.read()
    enquiry = extract_enquiry(text)
    print()
    print(format_result(enquiry, triage(enquiry)))


if __name__ == "__main__":
    main(sys.argv[1:])
