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
4. **`app.py`** — Streamlit CRM (multipage; see below).
5. **`discovery_engine.py`** — continuous watchlist growth: probes company
   names (from any list you collect) across all 4 ATS platforms, harvests
   board URLs from Hacker News "Who is hiring" threads, and extracts tokens
   from any saved page. Dead candidates are cached 90 days so weekly re-runs
   stay fast.
6. **`minilist_engine.py`** — free mini-list generator: matches a prospect
   agency's niche keywords against tracked jobs and produces a branded
   CSV + HTML gift list (the "here's 10 companies hiring in your niche"
   door-opener). Also available as the CRM's Mini-List page.

### Own-client prospecting (finding OUR clients)

A second, fully separate pipeline that finds recruitment agencies to sell to:

7. **`prospector_engine.py`** — searches Apollo for decision-makers
   (founders/MDs/owners) at small recruitment/staffing agencies (2–20
   employees, US/UK/FR by default) and appends them to `prospects.csv`.
   **Run `--probe` first** — it dumps one raw response so you can verify
   which filter names your Apollo plan accepts.
8. **`agency_discovery.py`** — probes prospect agency names across the 4 ATS
   platforms and classifies live boards as agency boards (name markers or an
   "agency book" posting pattern) into `agency_watchlist.json` — never mixed
   with the client watchlist.
9. **`prospect_signal_engine.py`** — sweeps agency boards; each new posting
   is a prospect signal ("this agency just took a mandate") appended to
   `prospect_signals.csv` — the hook for our own outreach.

## The CRM (`streamlit run app.py`)

Multipage app (`st.navigation`); `app.py` is the entry point, pages live in
`views/`, shared helpers in `common.py`, brand theme in
`.streamlit/config.toml`. The sidebar always shows follow-ups due, new
leads, touched-today and boards-watched counts.

| Page | What it does |
|---|---|
| **Home** | Today's metrics, pipeline funnel, per-client billable summary, first-run checklist, and buttons that run all three engines in-app with live output (one at a time — lockfile-guarded). |
| **Work → Outreach Queue** | Follow-ups due + top-N ranked new sends, pre-drafted DMs, double-DM guard, focus mode (one card at a time with progress), undo, daily cap. |
| **Work → Daily Inbox** | Every signal with stage badges (new / contacted / replied / won / dismissed), notes, DM drafts, follow-up timers; 25 per page. |
| **Work → Prospects** | OUR pipeline: agency decision-makers from the prospector, mandate hooks from the sweep, DM drafts from editable templates, and a one-click per-prospect mini-list (CSV + branded HTML) as the door-opener. Stages: new → contacted → replied → call booked → client won. |
| **Clients → Client Pages** | Per-client leads, filters, metrics, billable total, CSV client report. |
| **Clients → Mini-List** | The free gift list (CSV + branded HTML one-pager). |
| **System → Watchlist** | Boards swept nightly: URL paste, bulk import, manual add, discovery run, search, bulk remove, live-job counts. |
| **System → Live Jobs** | Every tracked job across all boards — search, platform/location filters, CSV export. |
| **System → Client Configs** | Profiles with live tester, dry-run over tracked jobs, backfill, type-to-confirm delete. |
| **System → Help** | The full operating guide: pipeline diagram, per-page docs, glossary, daily routine, troubleshooting. Every page has a "?" popover linking to its section. |

## Setup

```bash
pip install -r requirements.txt
# create a file named .env next to app.py containing:
#   APOLLO_API_KEY=your_key      (Apollo → Settings → Integrations → API, master enabled)
```

## Usage

```bash
streamlit run app.py               # open the CRM (engines can be run from Home)
python signal_engine.py            # fetch new job signals (use --baseline on first run)
python discovery_engine.py --hn 3  # grow watchlist from HN "Who is hiring" threads
python discovery_engine.py --names companies.txt   # ...or from a company-name list
python enrichment_engine.py        # enrich companies with contacts via Apollo
python minilist_engine.py --prospect "Acme Staffing" --keywords "java, .net" \
    --locations "united states"    # build a free mini-list for a prospect

# own-client prospecting
python prospector_engine.py --probe          # ALWAYS first: verify Apollo filters
python prospector_engine.py                  # then: fill prospects.csv
python agency_discovery.py --from-prospects  # find the agencies' own boards
python prospect_signal_engine.py             # sweep their mandates (hooks)

# tests
python tests/test_units.py                   # classifier, niche derivation, parsing
python tests/test_pages.py                   # every app page, empty + populated data
```

Runtime data (`signals.csv`, `signals_state.json`, `inbox_status.json`,
`enriched_contacts.json`, `discovery_state.json`, `prospects.csv`,
`prospect_signals*.json/csv`, `prospects_status.json`, the pipeline tracker
spreadsheet) is intentionally not committed — see `.gitignore`. See
`PLAYBOOK.md` for the operating playbook, or the CRM's **Help** page for the
full guide.
