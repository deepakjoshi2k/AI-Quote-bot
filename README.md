# Lincoln Cargo – Sea Freight Import Quote Agent (prototype)

> **MOCK RATES – PROTOTYPE.** All rates in `data/` are invented. Never put real customer data into a free-tier LLM.

Reads a UK sea-freight import enquiry, applies the Incoterm, prices the quote from rate sheets and drafts an email for a human to review. Nothing is sent automatically.

## Setup
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # add your LLM key later (Phase 3)
pytest
```

## Connect a language model (Phase 3+)
1. `cp .env.example .env` and fill in one option (Gemini or Groq free tier, or Ollama locally).
2. `python -m src.llm` should print `LLM connection OK`.
3. `python -m eval.run_eval` scores extraction + triage on the 11 sample enquiries.

`pytest` never calls a real model: tests use a fake LLM with canned answers (`tests/fixtures/llm_raw.json`).

## Status
- [x] Phase 1 – schemas (`src/models.py`), measures (`src/measures.py`), config, rate sheets in `data/`
- [x] Phase 2 – Incoterm logic (`src/incoterms.py`)
- [x] Phase 3 – extraction (`llm.py`, `extract.py`, `triage.py`, `zones.py`, lane checks in `rates.py`)
- [x] Phase 3.1 – direction from countries, customer role, UK **and EU-27** destinations (mock EU sheets via `scripts/make_eu_mock_data.py`)
- [ ] Phase 4 – pricing (`zones.py`, `rates.py`, `quote.py`)
