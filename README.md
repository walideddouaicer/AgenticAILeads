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
4. **`app.py`** — Streamlit CRM: outreach queue (follow-ups due + top-N ranked
   new sends, one-click copy/mark-contacted, double-DM guard), daily inbox
   (contacted / replied / won stages, notes, follow-up timers), per-client
   lead pages, mini-list generator tab, and a live config tester.
5. **`discovery_engine.py`** — continuous watchlist growth: probes company
   names (from any list you collect) across all 4 ATS platforms, harvests
   board URLs from Hacker News "Who is hiring" threads, and extracts tokens
   from any saved page. Dead candidates are cached 90 days so weekly re-runs
   stay fast. The Watchlist tab also has a bulk-import box (paste any text,
   all board URLs are detected).
6. **`minilist_engine.py`** — free mini-list generator: matches a prospect
   agency's niche keywords against tracked jobs and produces a branded
   CSV + HTML gift list (the "here's 10 companies hiring in your niche"
   door-opener). Also available as the CRM's Mini-List tab.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then paste your Apollo API key into .env
```

## Usage

```bash
python signal_engine.py            # fetch new job signals (use --baseline on first run)
python discovery_engine.py --hn 3  # grow watchlist from HN "Who is hiring" threads
python discovery_engine.py --names companies.txt   # ...or from a company-name list
python enrichment_engine.py        # enrich companies with contacts via Apollo
streamlit run app.py               # open the CRM dashboard
python minilist_engine.py --prospect "Acme Staffing" --keywords "java, .net" \
    --locations "united states"    # build a free mini-list for a prospect
```

Runtime data (`signals.csv`, `signals_state.json`, `inbox_status.json`, the
pipeline tracker spreadsheet) is intentionally not committed — see
`.gitignore`. See `PLAYBOOK.md` for the operating playbook.
