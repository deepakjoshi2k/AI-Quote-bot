"""run_enquiry(text): email -> extracted enquiry -> triage -> priced quote (if quotable). Logs each run."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.config import ROOT
from src.extract import extract_enquiry
from src.llm import LLMClient
from src.models import Enquiry, Quote, Triage
from src.quote import price
from src.triage import triage

LOG_DIR = ROOT / "logs"


@dataclass
class Result:
    enquiry: Enquiry
    triage: Triage
    quote: Quote | None


def run_enquiry(text: str, llm: LLMClient | None = None, log: bool = True) -> Result:
    enquiry = extract_enquiry(text, llm=llm)
    t = triage(enquiry)
    quote = price(enquiry) if t.status == "quote" else None
    result = Result(enquiry, t, quote)
    if log:
        _log(text, result)
    return result


def _log(text: str, r: Result) -> None:
    """One JSON file per run in logs/ (git-ignored). Mock/anonymised emails only."""
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    payload = {
        "email": text,
        "enquiry": r.enquiry.model_dump(mode="json"),
        "triage": r.triage.model_dump(mode="json"),
        "quote": r.quote.model_dump(mode="json") if r.quote else None,
    }
    (LOG_DIR / f"run-{stamp}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
