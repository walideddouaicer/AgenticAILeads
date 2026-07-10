# Agentic AI Leads

Lead-generation pipeline that watches companies' public job boards for hiring
signals, scores and filters them per client, enriches promising companies with
decision-maker contacts, and manages outreach in a local CRM.

## How it works

1. **`signal_engine.py`** — polls public job boards on 4 ATS platforms
   (Greenhouse, Lever, Ashby, Workable) and reports roles opened since the
   last run. Each new role is an outreach signal, appended to `signals.csv`.
2. **`filter_engine.py`** — scores signals against per-client keyword/location
   profiles stored in `configs/*.json`.
3. **`enrichment_engine.py`** — looks up recruiting/TA decision-makers at the
   signal companies via the Apollo.io API.
4. **`app.py`** — Streamlit CRM: daily inbox (contacted / replied / won stages,
   notes, follow-up timers), per-client lead pages, and a live config tester.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then paste your Apollo API key into .env
```

## Usage

```bash
python signal_engine.py            # fetch new job signals (use --baseline on first run)
python enrichment_engine.py        # enrich companies with contacts via Apollo
streamlit run app.py               # open the CRM dashboard
```

Runtime data (`signals.csv`, `signals_state.json`, `inbox_status.json`, the
pipeline tracker spreadsheet) is intentionally not committed — see
`.gitignore`. See `PLAYBOOK.md` for the operating playbook.
