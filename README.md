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
3. `python -m eval.run_eval` scores extraction + triage on the sample enquiries (`python -m eval.run_eval I12` for one).
4. `python -m src.try_email data/sample_enquiries/I12.txt` shows one email's details ("Not available" / "Not relevant"), the decision, the questions for the sender and, when quotable, the priced quote. Each run is logged to `logs/` (git-ignored).

**Import quote scope (agreed with Lincoln Cargo's process):** imports only; the quote goes to, and is charged to, whoever emails, whatever the Incoterm. It covers destination terminal handling + release fee, delivery to the door if asked, and customs clearance only if asked. No ocean freight or origin charges. LCL, DG, special cargo, exports and air/road go to manual review. See `src/scope.py`.

**No-assumption rule:** the agent only uses what the email says. Missing details become "Not available" and a question for the sender; nothing (ports, roles, FCL/LCL) is guessed.

`pytest` never calls a real model: tests use a fake LLM with canned answers (`tests/fixtures/llm_raw.json`).

## Status
- [x] Phase 1 – schemas (`src/models.py`), measures (`src/measures.py`), config, rate sheets in `data/`
- [x] Phase 2 – Incoterm logic (`src/incoterms.py`)
- [x] Phase 3 – extraction (`llm.py`, `extract.py`, `triage.py`, `zones.py`, lane checks in `rates.py`)
- [x] Phase 3.1 – direction from countries, customer role, UK **and EU-27** destinations (mock EU sheets via `scripts/make_eu_mock_data.py`)
- [x] Phase 4 – pricing (`src/quote.py`, `src/pipeline.py`): destination services, UK + EU, FCL
- [x] Real Lincoln Cargo tariff for **Germany & France** (`data/tariffs/eu_de_fr.yaml`: EUR, no markup, same at every port, terms on the quote). Other countries still use MOCK sheets.
- [x] Quote saved as a text file in `quotes/` (git-ignored) by `python -m src.try_email` (`zones.py`, `rates.py`, `quote.py`)
