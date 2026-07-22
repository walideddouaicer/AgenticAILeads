# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Lead-generation pipeline + local CRM for Agentic AI Leads (pay-per-meeting
lead gen for tech recruitment agencies). Python + Streamlit, no test
framework, no linter config. `PLAYBOOK.md` holds the business context; the
CRM's Help page (`views/help.py`) documents every screen.

## Commands

```powershell
streamlit run app.py                 # the CRM (engines can also be run from its Home page)
python signal_engine.py              # nightly sweep (--baseline on very first run)
python discovery_engine.py --hn 3    # grow watchlist from HN "Who is hiring" (--names file.txt also works)
python enrichment_engine.py          # Apollo contact lookup (--probe COMPANY to verify API parsing first)
python minilist_engine.py --prospect "Acme" --keywords "java, .net"   # prospect gift list

# own-client prospecting (second pipeline)
python prospector_engine.py --probe          # ALWAYS before the first real run
python prospector_engine.py                  # Apollo -> prospects.csv
python agency_discovery.py --from-prospects  # classify agency boards -> agency_watchlist.json
python prospect_signal_engine.py             # mandate sweep -> prospect_signals.csv

# delivery layer (email tools; all optional, no-op without keys)
python delivery.py --probe                   # print requests each adapter WOULD send
python delivery.py --sync-replies            # poll replies -> set stage 'replied'
```

Verification (no pytest here — this is the house pattern):

```powershell
python -m py_compile app.py common.py views/*.py   # must pass
python app.py                                      # bare mode must exit 0
python tests/test_units.py                         # classifier, niche derivation, Apollo parsing
python tests/test_delivery.py                      # delivery adapters, mocked HTTP
python tests/test_pages.py                         # all pages via AppTest, empty + populated data
```

For app testing use `streamlit.testing.v1.AppTest` with the `AAL_DATA_DIR`
env var (read by `common.py` at import) pointed at a throwaway fixture dir —
never test against the real data files. Pattern:

```python
os.environ["AAL_DATA_DIR"] = fixture_dir      # BEFORE importing common/app
at = AppTest.from_file("app.py"); at.switch_page("views/queue.py"); at.run()
assert not at.exception
```

Test every page against BOTH an empty data dir (empty states must say what
to do next) and a populated one.

## Architecture

Two layers connected only through data files in the repo root:

1. **Engines** — standalone CLI scripts (`signal_engine.py`,
   `discovery_engine.py`, `enrichment_engine.py`, `minilist_engine.py`).
   Each reads/writes specific files and knows nothing about the app.
2. **CRM** — `app.py` is a thin `st.navigation` entrypoint; one file per
   page in `views/`; ALL shared code (paths, constants, loaders, stage
   logic, badges, toast/undo plumbing, the subprocess engine runner,
   sidebar stats, page-header helper) lives in `common.py`. Pages import it
   as `import common as C`.

`filter_engine.evaluate_job()` is the single matching implementation used
by the sweep, the mini-list, and the config page's live tester/dry-run —
change it in one place only.

`delivery.py` is the channel-ready email layer: an adapter per tool
(Manual/Instantly/Smartlead/ApolloSequences) with one shared
`map_lead()` (email/first_name/company + custom `{hook}`/`{title}`). It is
deliberately **Streamlit-free and common-free** (pure, CLI + unit
testable); `common.py` imports it, never the reverse. Adapters read keys
from `.env` and no-op when absent — nothing sends without a key AND a
button press. `common.apply_replies()` maps replies to leads by email and
advances only `new`/`contacted` → `replied`. Per-client `channel` and
`campaign_id` are optional config fields defaulted in `common.load_profile`
(filter_engine untouched — it ignores unknown keys).

### Data files — formats are FROZEN

Other local scripts depend on these exact shapes; do not change them:

| File | Shape / owner |
|---|---|
| `signals.csv` | append-only; columns `date,client,platform,company,title,location,score,url,hook` (signal engine + backfill write) |
| `signals_state.json` | `{"platform:token": {job_id: {title,location,url}}}` (sweep state = the "live jobs" pool) |
| `inbox_status.json` | `{lead_key: {status,on,notes[,fu]}}` (CRM writes; `load_status()` migrates legacy shapes) |
| `watchlist.json` | `{platform: [tokens]}` (CRM + discovery write) |
| `enriched_contacts.json` | `{company: [contacts]}` cache — companies never re-queried |
| `configs/*.json` | one client profile per file; schema in `filter_engine.py` docstring |
| `discovery_state.json` | dead-candidate cache, 90-day expiry |
| `prospects.csv` | own-client prospects (prospector output + USER EDITS — engines only append, never rewrite) |
| `prospects_seen.json` | prospector dedupe cache, keyed on LinkedIn URL (fallback agency+name) |
| `agency_watchlist.json` | agency boards — NEVER merged into `watchlist.json`, never routed through client configs |
| `prospect_signals_state.json` / `prospect_signals.csv` | mandate-sweep state + signals (`date,agency,mandate_title,location,url,hook`) |
| `prospects_status.json` | prospect stages/notes/niche overrides (separate key space from `inbox_status.json`) |
| `prospects_config.json` | prospect DM/follow-up/breakup templates (editable in-app) |

### Business rules — do not alter

- `lead_key(row)` = md5 of `date|client|company|title`, first 12 hex chars.
  Changing it orphans every entry in `inbox_status.json`.
- Stages: `new → contacted → replied → won`, plus `dismissed`;
  `LEGACY_MAP` migrates old names on load.
- Follow-ups: due `FOLLOWUP_DAYS` (3) after last touch; after
  `MAX_FOLLOWUPS` (2, the breakup) the lead leaves the queue forever.
- Double-DM guard: a LinkedIn contact URL already contacted for one client
  warns on every other client's card.
- Baseline semantics: the first sweep ever, and the first sweep of any
  newly added board, records state silently — diffs only alert afterwards.
- Hooks older than `FRESH_DAYS` (7) degrade from "just opened" to
  "currently hiring" wording (`display_hook`).

### CRM conventions

- Engine subprocesses: only via `common.start_engine()` — `.engine.lock`
  in the data dir + the session job object guarantee one engine at a time;
  a reader thread streams stdout (the thread must never touch `st.*`).
- Stage colors app-wide (badges, funnel): new `#D8FF3E`, contacted
  `#4A90D9`, replied `#F5A623`, won `#3CB371`, dismissed `#999999` —
  always paired with the stage name as text, never color alone.
- Theme is editorial-brutalist (STITCH design system): paper `#F4F2EC`,
  ink `#0A0A0A`, accent `#D14829`, Archivo/JetBrains Mono, square
  corners (`baseRadius = "none"`), 1.5px ink borders, hard offset
  shadows. Tokens live in `.streamlit/config.toml` + `common._CSS`
  (`--aal-*` variables) — change them there, not per-page.
- Every input gets a `help=` tooltip; every page starts with
  `C.page_header(title, page_key)` (the "?" popover jumps to the Help
  section via the `help_jump` session key); every empty state names the
  exact button/command that fixes it.
- Streamlit ≥1.3x APIs: `width="stretch"`, never the deprecated
  `use_container_width`. `st.set_page_config` is called once, in `app.py`.
- Stage changes go through `C.set_stage()` / `C.mark_followed_up()` — they
  handle the undo snapshot and toast; don't write `inbox_status.json`
  directly from pages.

### The two pipelines stay separate

Leads (companies hiring → our clients' meetings) and prospects
(recruitment agencies → OUR clients) never share files, watchlists,
stages, or status keys. Prospect stages are `new → contacted → replied →
call_booked → client_won` (+ dismissed) with the same follow-up timers;
status helpers in `common.py` take a `path` argument
(`C.PROSPECTS_STATUS`) — never write a prospect entry into
`inbox_status.json` or vice versa. No LinkedIn automation anywhere: the
app drafts, a human sends.

## Gotchas

- `python app.py` must keep exiting 0 (the `runtime.exists()` guard at the
  bottom of `app.py`).
- `app.py` gates the whole app behind a login form (`auth.py`,
  `require_login()`), reading credentials from env vars (`APP_USERS`, or
  `APP_USERNAME`/`APP_PASSWORD`) — set in Coolify, never in the repo. Set
  `AAL_DISABLE_AUTH=1` to bypass the gate; this is test-only (the page tests
  set it) and must NEVER be set in production.
- Container deploy: `Dockerfile` + `docker-entrypoint.sh` build the image,
  set `AAL_DATA_DIR=/data` (a Coolify persistent volume), and seed
  `watchlist.json`/`configs` onto the volume on first boot. Full runbook in
  `DEPLOY.md`.
- Data files live at repo root by default; `AAL_DATA_DIR` relocates them
  (used by tests, and by the container deploy to point at a persistent
  volume). Both the CRM (`common.py`) and the engines honor it — each
  engine resolves `DATA_DIR = Path(os.environ.get("AAL_DATA_DIR", HERE))`
  and builds its data-file paths from `DATA_DIR`. Two paths intentionally
  stay on the code dir (`HERE`): `enrichment_engine.ENV_FILE` and
  `prospector_engine.PROBE_FILE` (the committed test fixture).
- `watchlist.json` is git-tracked (it's curated state, not runtime output);
  the rest of the runtime data is gitignored.
- On this machine git pushes need `http.sslBackend schannel` (already set
  repo-locally) because a TLS-inspecting proxy/AV breaks Git's bundled CA
  store. The Python equivalent: every networked engine calls
  `tls_compat.enable_os_trust_store()` (uses the `truststore` package) —
  keep that import in any new engine that makes HTTPS calls.
- Apollo filter parameter names vary by plan: `prospector_engine.py --probe`
  must be run (by the user, with the real key) before trusting a payload
  change; `tests/test_units.py` auto-validates parsing against the saved
  probe (`prospector_probe.json`) when it exists.
