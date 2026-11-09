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

## Status
- [x] Phase 1 – schemas (`src/models.py`), measures (`src/measures.py`), config, rate sheets in `data/`
- [x] Phase 2 – Incoterm logic (`src/incoterms.py`)
- [ ] Phase 3 – extraction (`llm.py`, `extract.py`, `triage.py`)
- [ ] Phase 4 – pricing (`zones.py`, `rates.py`, `quote.py`)
