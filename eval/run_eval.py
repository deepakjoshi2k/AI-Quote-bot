"""Score the REAL LLM on the 11 sample enquiries (extraction + triage).

    python -m eval.run_eval            # all samples
    python -m eval.run_eval I02 I11    # just some

Free tiers limit requests per minute, so the script pauses between emails
(EVAL_PAUSE_SECONDS in .env, default 4).

Needs a working .env. Mock emails only (rule 3). Phase 7 will add quote totals.
Success target (project brief): correct extraction on >= 10 of 11 samples.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from src.extract import extract_enquiry
from src.llm import get_llm
from src.triage import triage
from src.zones import zone_for_address

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "sample_enquiries"
SCALAR_FIELDS = ["direction", "mode", "service", "origin_port", "destination_port", "incoterm", "un_number",
                 "delivery_required", "delivery_postcode", "pallets", "hazardous", "origin_country",
                 "destination_country"]
NUMERIC_FIELDS = ["total_volume_cbm", "total_gross_weight_kg"]


def _val(v):
    return v.value if hasattr(v, "value") else v


def check(sid: str, exp: dict, llm) -> tuple[list[str], float]:
    text = (SAMPLES / f"{sid}.txt").read_text(encoding="utf-8")
    t0 = time.perf_counter()
    e = extract_enquiry(text, llm=llm)
    t = triage(e)
    secs = time.perf_counter() - t0

    errors = []
    if t.status != exp["status"]:
        errors.append(f"status {t.status} != {exp['status']}  ({'; '.join(t.reasons + t.missing_fields)})")
    got_flags = sorted(f.value for f in t.flags)
    if got_flags != sorted(exp["flags"]):
        errors.append(f"flags {got_flags} != {sorted(exp['flags'])}")
    for f in SCALAR_FIELDS:
        if f in exp and _val(getattr(e, f)) != exp[f]:
            errors.append(f"{f}: {_val(getattr(e, f))!r} != {exp[f]!r}")
    for f in NUMERIC_FIELDS:
        if f in exp and (getattr(e, f) is None or abs(getattr(e, f) - exp[f]) > 0.01):
            errors.append(f"{f}: {getattr(e, f)} != {exp[f]}")
    if "containers" in exp and [c.model_dump() for c in e.containers] != exp["containers"]:
        errors.append(f"containers: {[c.model_dump() for c in e.containers]} != {exp['containers']}")
    if "ready_date" in exp and (e.ready_date is None or e.ready_date.isoformat() != exp["ready_date"]):
        errors.append(f"ready_date: {e.ready_date} != {exp['ready_date']}")
    if "zone" in exp and zone_for_address(e.destination_country, e.delivery_postcode) != exp["zone"]:
        errors.append(f"zone for {e.delivery_postcode} != {exp['zone']}")
    for phrase in exp.get("missing_contains", []):
        if not any(phrase.lower() in m.lower() for m in t.missing_fields):
            errors.append(f"missing_fields lacks '{phrase}'")
    return errors, secs


def main(ids: list[str]) -> None:
    expected = json.loads((SAMPLES / "expected.json").read_text(encoding="utf-8"))
    ids = ids or sorted(expected)
    llm = get_llm()
    print(f"Model: {llm.settings.provider} / {llm.settings.model}\n")
    pause = float(os.getenv("EVAL_PAUSE_SECONDS", "4"))
    passed = 0
    for i, sid in enumerate(ids):
        if i:
            time.sleep(pause)
        try:
            errors, secs = check(sid, expected[sid], llm)
        except Exception as ex:  # keep going so one bad call doesn't hide the rest
            errors, secs = [f"ERROR: {ex}"], 0.0
        ok = not errors
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {sid}  ({secs:.1f}s)")
        for err in errors:
            print(f"      - {err}")
    print(f"\n{passed}/{len(ids)} samples fully correct (target: >= 90%)")


if __name__ == "__main__":
    main(sys.argv[1:])
