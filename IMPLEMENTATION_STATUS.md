# Implementation status

Updated: 23 September 2026. Scope: Phases 0–7 complete offline; Phase 8 rehearsal performed, founder acceptance pending.

## Checkpoints

- Pre-implementation commit: `0a7db20adabe6bdd435cc013dc16bdb2b1aa409b`.
- Branch: `codex/minimum-complete`.
- `2741e4e` — `Establish isolated baseline and project guidance gbt 1.0`.
- `7160da3` — `Add atomic storage and ownership-safe locks gbt 2.0`.
- `648d649` — `Make app state updates conflict-safe gbt 3.0`.
- `6289e47` — `Unify engine execution and process locking gbt 4.0`.
- `5e213af` — `Make sweep output batches crash recoverable gbt 5.0`.
- `a6760f7` — `Complete safe engine persistence and runtime paths gbt 6.0`.
- `5437e4b` — `Unify niche and location matching rules gbt 7.0`.
- `08532f9` — `Track verified board evidence and confirmed sample identities gbt 8.0`.
- Phase 2 sample review checkpoint: `Review and approve sample rows before export gbt 9.0`.
  Hash: `b34a886`.
- Phase 3 queue and send-history checkpoint: `Unify outreach queue and record manual sends gbt 10.0`.
  Hash: `4b0b74f`.
- Phase 4 interface checkpoint: `Consolidate acquisition screens and CSV import gbt 11.0`.
  Hash: `9dc1752`.
- Phase 5 client-delivery checkpoint: `Complete first-client delivery and meeting records gbt 12.0`.
  Hash: `e832abf`.
- Phase 6 cleanup checkpoint: `Remove superseded paths and align operator guide gbt 13.0`.
  Hash: `ec6e950`.
- Phase 7 operation/recovery checkpoint: `Add portable startup refresh and recovery gbt 14.0`.
  Hash: `7209ee3`.
- `a7d9adf` — `Verify Ubuntu Python 3.13.15 operation gbt 15.0`.
- Phase 8 rehearsal fixes: `2ad735e` — `Fix rehearsal message defects before manual sends gbt 16.0`;
  `f9dd1e7` — `Close won-agency to client handoff gaps gbt 17.0`;
  `d8ac3ab` — `Show recovery steps for damaged data files gbt 18.0`.
  This Phase 8 record is `Record Phase 8 rehearsal and open release gates gbt 19.0`.

| Phase | Status |
|---|---|
| 0 — baseline, environment, instructions | Complete at the first numbered checkpoint |
| 1 — persistence and process execution | Complete through `gbt 6.0` |
| 2 — evidence and sample correctness | Implemented offline through `gbt 9.0`; live compatibility still unverified |
| 3 — unified queue and durable actions | Implemented offline through `gbt 10.0` |
| 4 — interface consolidation | Implemented offline at `gbt 11.0` |
| 5 — minimum client delivery | Implemented offline at `gbt 12.0` |
| 6 — dead paths and documentation | Implemented offline at `gbt 13.0` |
| 7 — Linux operation and recovery | Implemented and exercised on Ubuntu Python 3.12.3 and 3.13.15; Docker run still unverified |
| 8 — release rehearsal | Rehearsals A/B, browser, Linux 3.13.15 and live read-only board checks done; **not complete** until the founder walkthrough is observed and recorded |

## Starting inventory

The original checkout was clean. The user confirms that only code tests had been
performed and no clients had been acquired. The tracked watchlists contain 183
employer-board entries and 8 agency-board entries; these counts do not establish
that those sources are currently live.

No operational `prospects.csv`, `signals_state.json`, `job_history.json`,
`mandate_history.json`, `enriched_contacts.json`, `prospects_status.json` or
`icp.json` was present. API credentials and operational history were not restored
or fabricated. No user data migration occurred.

## Reproducible local environment

Linux x86_64, Python 3.13.15, isolated `.venv`. Direct packages:
Streamlit 1.64.0, requests 2.34.2, truststore 0.10.4. `requirements.txt` records the
exact installed direct/transitive versions; `pip check` reported no broken
requirements. Later Phase 7 checks also passed on Ubuntu Python 3.12.3 and
Windows Python 3.12.14.

## Original baseline results

The untouched starting commit was copied to a scratch directory and checked with
outbound socket connections disabled. The installed package versions are the
fresh resolution of the original unpinned requirements, not a claim to reproduce
the old machine's package versions.

| Original check | Result | Diagnosis |
|---|---|---|
| Unit assertions | 29 pass, 1 fails | Re-export test expects internal modules `hashlib` and `re`, which `common.py` intentionally excludes |
| Mocked delivery assertions | 11 pass | Only fixture/mocked behavior; not live vendor validation |
| Page suite | Stops before rendering | Relative `AppTest.from_file("app.py")` resolves to `tests/app.py` with installed Streamlit |
| Bare `python app.py` | Pass | Correct launcher hint and exit code 0 |

The page harness also needed to run the entrypoint before switching pages, enable
client pages when testing them, and test legacy unregistered pages directly.
Those are harness repairs, not removal of product checks. Navigation gating now
uses the actual app/page switch instead of merely checking a module boolean.

## Phase 0 checks

- Isolated runner: offline-guard self-check, syntax and bare launcher pass.
- Unit assertions: **30 pass**.
- Mocked delivery assertions: **11 pass**.
- Page/render/interaction assertions: **50 pass**, covering empty and populated
  fixture data, both navigation modes, contact edits, separate prospect/lead
  tracking, status persistence and undo.
- Workflow-gap regressions: **2 expected failures**, explicitly unresolved below.
- Local Streamlit server startup/health succeeded with empty and populated
  fixture directories. This is not visual browser acceptance; page behavior was
  exercised through AppTest. The servers were stopped after the checks.
- No live provider requests, outreach, deployment or scheduled jobs were used.

Run `.venv/bin/python scripts/check.py --logs-dir /tmp/aal-check-logs` to reproduce
the current suite. The runner copies source to a temporary directory and excludes
working runtime data, root credentials and private API probes. Its Python child
processes cannot make network connections. Fixture generation therefore cannot
overwrite data in the working checkout. Direct legacy test commands do not offer
this isolation; use the runner.

## Reproduced product blockers

1. **Queue eligibility mismatch (Phase 3):** the current fixture builder returns
   no eligible priority items, while the page still offers two new prospects.
   The page uses its own fallback rather than the builder's eligibility result.
2. **Initial-send budget does not decrease (Phase 3):** with 17 fresh prospects and
   a budget of 15, marking one contacted leaves 15 new-send cards instead of 14.

Both tests check the desired behavior and are marked `expectedFailure`. Their
fixture/runtime preconditions run outside the expected-failure body, so a broken
setup cannot masquerade as the known defect. When Phase 3 fixes the behavior,
remove each marker and retain the regression; an unexpected success currently
fails the known-gap suite to require that update.

Additional reliability/data-quality issues in the implementation plan remain
static-review findings until their phases reproduce and fix them. The baseline
ResourceWarnings from unclosed CSV readers were addressed in Phase 1. Passing the
repaired baseline suite is not release readiness.

## Phase 1 result

- JSON writes use atomic replacement and schema-checked reads. Damaged business
  data is preserved with an explicit error instead of being reset to empty.
- Status, contact and watchlist changes reload the latest file under a short lock.
  Different-record edits merge; stale same-record changes and stale undo conflict.
- App buttons and scheduled chains use one atomic owner-token engine lock. A failed
  spawn releases its own lock, live owners cannot be cleared by another session,
  and dead/legacy locks recover automatically.
- Signal and mandate sweeps checkpoint state, history and signals before applying
  them. Restart tests interrupt after each write boundary and recover without a
  duplicate signal or a changed observation date.
- Discovery, prospect import, enrichment, mini-list and daily-queue writers now
  use the shared safe storage operations. CSV appends are locked and idempotent;
  manual prospect rows and non-empty contact corrections are preserved.
- `runtime.py` is the single source for `AAL_DATA_DIR` and `AAL_ENV_FILE`.
  Private probe output follows the data directory rather than the code checkout.
- History backups are validated JSON. The restore command preserves the current
  history as a pre-restore backup before atomically applying the selected copy.

Phase 1 verification: offline guard, syntax, bare launcher, units, storage,
runner, sweep recovery, engine storage, mocked delivery and page tests all pass.
The two Phase 3 regressions remain expected failures: queue eligibility differs
between builder and page, and today's initial-send budget does not decrease.
No live network/provider request, message, deployment or scheduler change ran.

## Phase 2 result

- `gbt 7.0` unified niche and geography matching. Java/JavaScript, `.NET`, C++,
  technical phrases, remote versus market, ambiguous locations and the old
  open-role-count size proxy are covered by behavioral checks.
- Both sweep engines validate complete ATS job lists before replacing snapshots.
  A malformed response, 404 or request failure leaves the prior state/history
  intact; a valid empty response closes prior jobs. Each attempted board check
  writes its result and UTC timestamp after the recoverable sweep batch commits.
  Jobs carry their successful observation timestamp. Current claims require
  matching job and board timestamps, a successful latest check and age at most
  48 hours. This is an operational default, not a validated market threshold.
- Agency discovery saves suggestions separately. A confirmed agency-board
  mapping and current posting are required for an agency posting hook. New
  prospect signal rows store `platform:token` in the existing `agency` column;
  legacy token-only rows remain readable but cannot be linked ambiguously.
  Advertised roles are described literally rather than as newly won mandates.
- Employer samples keep platform/board identity and only group boards after
  their company domain is explicitly confirmed. Legacy company-keyed contacts
  attach only through a confirmed mapping; manual contacts entered on a lead
  stay with its exact board. Provider lookups target confirmed, recently checked
  sample companies, retry empty results after a bounded interval, and preserve
  manual contact corrections on refresh.
- Sample rows expose source board, posting URL, verification time, identity
  status and actual named-contact coverage. Unsupported fixed volume/contact
  promises were removed from generated copy and active help text. CSV output
  escapes spreadsheet formulas; HTML output escapes text and rejects non-HTTP
  links. Prospect and standalone sample screens require the operator to approve
  eligible rows before export; prospect approvals and output corrections persist
  in `prospects_status.json`. The original role/location remain alongside any
  corrected output. Approved sample counts can populate message templates;
  missing sample fields show a correction state instead of a raw placeholder.

Additive data: `board_refresh.json`, `agency_board_refresh.json`,
`agency_board_suggestions.json`, `agency_board_mappings.json`,
`company_board_mappings.json`, `contact_lookup_status.json`, `observed_at` on
stored jobs, and `sample_review` on prospect status entries. Existing data was
not rewritten in the working checkout. No mappings or current observations were
invented for previously tracked boards; those require an operator check and
confirmation.

Verification: the isolated runner passed offline guard, syntax, bare app,
units, storage, runner, sweep recovery, engine storage, matching, evidence,
sample-review page interaction, mocked delivery and all existing page checks.
The two Phase 3 workflow gaps remain expected failures with the same observed
builder/page and budget disagreements. These runs used Windows with Python
3.12.14 and pinned package versions because the requested Linux path and
Python 3.13.15 environment are not available in this checkout. The runner
blocks external connections while allowing local sockets needed by Streamlit
on Windows. No live ATS/provider request, outreach, deployment, push or
scheduler action was made. Live ATS shape and provider access still require
separate read-only/operator-initiated checks later in the plan.

## Phase 3 result

- `outreach.py` now supplies one Streamlit-free queue and prospect context to
  both the page and `build_today.py`. The `today.json` file is a derived export;
  missing or old copies do not change page eligibility. The old small/bulk
  split, compulsory ten-company/ten-contact thresholds and fallback page sort
  are removed from active acquisition decisions. LinkedIn-only prospects can
  qualify without email; missing contact, identity, opener or valid message
  produces an explicit correction reason. Current ICP mismatches stay visible
  for review.
- Replies appear first, then due follow-ups, then eligible first sends.
  First sends consume the configured daily budget once, under the same file
  lock used for status changes. Follow-ups do not consume it. The business
  day uses `AAL_BUSINESS_TIMEZONE` (default UTC); the count survives app restarts.
  Older stage-only entries remain readable, but historical counts may be
  incomplete and are labeled that way.
- Editing a draft saves it in the prospect status; the page also offers an
  explicit Save draft action. Recording an actual manual send stores an action
  ID, UTC timestamp, operator, channel, exact final message and the reviewed
  sample the operator confirms was included. A duplicate action ID is harmless.
  Sends and draft edits reject stale record revisions. Undo restores only the
  last eligible record when its revision still matches. All prospects expose
  recorded send history; legacy status entries never invent old messages.
  Sample-count drafts regenerate when the reviewed sample changes.
- Prospect name/LinkedIn corrections are written under the CSV lock. A new
  derived key is aliased to the original status key before the CSV changes, so
  existing send history and stage remain attached. An interrupted correction
  is replayed from a pending checkpoint before prospects are read again.
- Both Phase 3 expected-failure markers were removed after their assertions
  passed: builder/page now recommend the same new prospect, and after one of
  15 first sends the page shows 14 remaining cards. Additional offline checks
  cover LinkedIn-only eligibility, missing sample claims, exact send snapshots,
  duplicate actions, follow-up counting, local midnight, stale edits, legacy
  status revisions and identity correction. The full isolated suite passed on
  Windows Python 3.12.14. No live source/provider request, outreach, push,
  deployment or scheduler action was made.

Additive data: `activity`, `drafts` and `draft_sample_fingerprints` on prospect
status entries, and
`prospect_key_aliases.json` and `prospect_identity_pending.json` for identity
corrections. Existing operational
data was not migrated in this checkout.

## Phase 4 result

- The sidebar has four screens: Prospects, Clients, Signals and Settings.
  Prospects opens on the daily acquisition queue. Its All view uses the same
  selected-record review controls and shows identity correction and recorded
  send history. Replies, due follow-ups and eligible first sends stay in
  priority order. Message drafts persist across rerenders and screen switches.
- Prospect criteria and message templates moved to Settings. Signals now owns
  employer and agency board refresh, source lists and confirmed agency-board
  mapping. Client tools remain available within Clients while Phase 5 joins
  their workflow more tightly. Old client screens and code remain until their
  required behavior is replaced; they are no longer sidebar entries.
- Prospects can import a UTF-8 CSV or add one person manually. Preview shows
  accepted counts and row-numbered rejection reasons. Accepted rows append
  without replacing existing corrections; repeated imports are idempotent.
  The upload clears after saving so newly imported rows are not presented as
  fresh rejections. Sample review presents a source-job link, board identity
  and verification time; approved CSV rows retain the same source fields.
- Source and import failures present correction steps. Technical collector
  logs are behind details. Active client copy points to the new navigation.
  Settings and sample-edit surfaces warn that unsaved edits will be discarded.

Verification: the isolated `scripts/check.py` suite passed on Windows Python
3.12.14 with no external requests. It includes empty and populated page checks,
manual-entry and import rejection checks, sample approval/correction and stale
source checks, and the Phase 3 queue and budget regressions. A local browser
walkthrough with disposable data confirmed four-screen navigation, empty client
setup, manual prospect entry, draft persistence across views, and CSV preview
with one accepted and two rejected rows followed by successful import. The
browser view was checked at approximately 900×640 px; the sidebar budget label
was shortened after it appeared clipped. No real board refresh, provider lookup,
outreach, push, deployment or schedule occurred. Live provider behavior and
founder acceptance remain unverified.

## Phase 5 result

- Clients now has one selected-client workflow for profile setup, current
  employer matches, outreach, and outcomes/reporting. A profile is created or
  edited in the app with niche, market, exclusion terms, channel, templates and
  agreed meeting rate. The own-prospecting `default` profile is never shown as
  a paying client. Legacy data-only clients remain visible so their history is
  not hidden; without a saved agreed rate, their estimate says "Rate needed."
- Matching jobs show source URL, board and recent verification. An operator
  selects the verified jobs to backfill. The save path rechecks the saved client
  profile and current board evidence; it rejects stale selections. Backfill
  retains the first-sweep baseline and is repeat-safe across calendar dates.
  Separate client niches produce separate matches.
- A selected client lead shows its posting, evidence, contact correction and
  cross-client duplicate-contact warning. Manual sends record the final edited
  message, contact, channel, operator and UTC timestamp in the client status
  file. Reply/won/dismissed outcomes and notes remain editable. These actions
  do not touch prospect acquisition status. The default last follow-up to an
  employer no longer repeats the agency-facing free-meeting offer.
- `client_meetings.json` stores one attended-meeting record keyed by client and
  meeting identity, with date, attendee, linked lead keys, notes and free versus
  chargeable status. The first meeting defaults to free. Two vacancy rows
  linked to one meeting still yield one outcome. Only distinct recorded
  chargeable meetings use the client's agreed rate for a "Recorded estimate";
  the app labels it neither an invoice nor a confirmed payment. Future meeting
  dates and duplicate new meeting IDs are rejected. The CSV contains exactly
  the selected lead rows and their actual meeting records.

Verification: the isolated runner passed on Windows Python 3.12.14, including
new domain and AppTest checks. The UI check creates a client, selects and
backfills a verified job, edits and records a manual message, records a free
first meeting and a chargeable second meeting, and verifies the per-client
estimate and export availability. Domain checks cover two distinct niches,
cross-date duplicate backfill, stale source rejection, cross-client contact
warning, one meeting linked to two vacancies, per-client rates and report
filtering. No live provider call, actual outreach, push, deployment or
scheduling occurred. The old client page files remain for Phase 6 removal;
live source compatibility and founder acceptance are still unverified.

## Phase 6 result

- Removed superseded Today, Home, watchlist, live jobs, mini-list, help,
  queue, inbox, client configuration and old client report pages. The four
  supported pages still render under the app navigation. CLI engines and the
  derived `today.json` queue export remain available.
- Removed live Instantly, Smartlead and Apollo sequence send/reply-sync code
  and its vendor-only tests. The manual prospect/client send records and
  client report CSV remain. `common.py` now contains UI helpers; active pages
  import domain behavior from `core.py` explicitly.
- Contact enrichment now updates `enriched_contacts.json` only. The app joins
  that cache when loading `signals.csv`. If `signals.csv` is absent, the
  legacy `enriched_signals.csv` remains readable so historical data is not
  discarded; it is no longer written.
- Replaced the README with the current install, configure, acquisition,
  delivery and recovery guide. Marked old planning and deployment notes as
  historical, updated the environment example and aligned agent guidance.

Verification: isolated offline suite passed on Windows Python 3.12.14,
including final four-page tests and the Phase 3 queue/budget regressions.
Supported CLI entry points responded to `--help`. No live ATS/provider check,
external outreach, push, deployment or scheduling occurred in this phase.
Full backup/restore and Linux installation remain Phase 7 work; founder
acceptance remains Phase 8 work.

## Phase 7 result

- Added project-local `start.py` and `refresh.py`. New installations use an
  ignored `data/` directory by default; `AAL_DATA_DIR` selects an existing
  installation. Both employer and agency watchlists and the default profile
  seed only when the entire directory is empty. Existing files are untouched.
- Local startup binds to `127.0.0.1`, requires login configuration and keeps
  CORS/XSRF protection enabled. Shared binding is explicit. The optional
  container uses the same startup path, no longer disables those protections,
  and excludes runtime data from its build context. Windows wrappers use the
  project environment and no longer contain the old machine path. No scheduler
  or deployment was created.
- `recovery.py` archives the known operational CSV/JSON state, profiles,
  mappings, corrections, meetings, both histories and sweep checkpoints.
  Credential files, probes and prior archives are excluded. Restore verifies
  checksums and JSON/CSV content into a new directory, refusing an existing
  target. Activation remains a deliberate operator action. The README and
  optional container guide now describe this procedure.

Verification: isolated offline suite passed on Windows Python 3.12.14 and in
a clean Ubuntu Python 3.12.3 virtual environment with pinned dependencies.
The Ubuntu run started the actual local server, received a healthy response,
stopped and restarted against the same data directory, and retained saved
status. Fixture refresh, two-session visibility, missing-credential guidance,
seed non-overwrite, and backup/restore integrity checks passed. `pip check`
passed on Windows and Ubuntu; container entrypoint shell syntax passed. A
separate Ubuntu Python 3.13.15 environment installed all 37 pinned packages,
passed a dependency compatibility check, and passed the full isolated offline
suite, including actual loopback server startup, health response, stop, restart
and retained status. Docker had no daemon, so an actual container run remains
unverified.
No live provider call, outreach, push, deployment or schedule occurred.

## Phase 8 result

### What was run

- **Isolated suite.** `scripts/check.py` passed all 18 checks on Ubuntu 22.04.5
  x86_64 with CPython **3.13.15** (uv-managed, fresh venv, all 37 pinned
  packages, `pip check` clean) at `a7d9adf` and again at `d8ac3ab`, including
  the Phase 3 queue and daily-budget regressions. It also passed on CPython
  3.13.13 in a separate Linux container after each fix. The earlier WSL
  Python 3.13.15 environment was not reachable from this session and was not
  reused.
- **Browser rehearsal.** A real headless Chromium drove the actual app
  (`start.py`, login, CORS/XSRF on) against a new disposable data directory.
  Board APIs were answered by controlled ATS-shaped responses (Greenhouse,
  Lever, Ashby, Workable); every other outbound connection was blocked and the
  request log shows only the four fixture boards. The clock was advanced by
  restarting the server under libfaketime. The final scripted run passed
  end to end on `d8ac3ab`. The harness (Playwright, libfaketime, fixtures) is
  test-only and not part of the app or requirements.
- **Rehearsal A (acquisition).** Clean directory seeded on first start; CSV
  import previewed 3 accepted / 1 row-numbered rejection; employer and agency
  boards added by URL on Signals; employer refresh; agency baseline, then a
  second refresh produced one literal agency-posting signal; company and
  agency-board identities confirmed. The agency showed a confirmed board
  posting hook and a one-row sample with source link, board, check time and
  "no named contact". The row was approved, the message edited (the edit
  persisted into a second browser session), and a send recorded with the
  reviewed sample; the budget showed 14 of 15. After restart at +4 days the
  follow-up was due, the budget was back to 15 of 15, and the sample showed
  "0 checked within 48 hours" until refresh. A refresh in which one board
  returned malformed data kept that board's snapshot, marked it failed with
  its last success, and warned on Signals. Follow-up, reply, call booked and
  client won were recorded; history shows both sends with exact messages.
- **Rehearsal B (delivery).** Clients offered "Start from a won agency"
  (name, stated niche and market prefilled; rate empty and required). Three
  verified London Java/data jobs were selectable; the JavaScript role, the
  Berlin Java role and the remote-only data role did not match. After a contact was added manually the draft
  greeted her by name; two manual sends were recorded with exact text. One
  free and one chargeable attended meeting gave "Attended meetings 2" and
  "Recorded estimate $100" at the $100 rate; the exported CSV has 3 lead and
  2 meeting rows whose values match `client_meetings.json` and the send log.
  Client actions left the agency's acquisition status unchanged.
- **Backup and restore.** With the app stopped, `recovery.py backup` then
  `restore` into a new directory validated checksums and refused an existing
  target. Restored files were byte-identical to the source (excluding the
  archive folder). A second server on the restored directory showed the same
  send history, and its exported report was byte-identical to the original.
- **Other states in the browser.** Empty Prospects/Clients, stale sample,
  failed board and, after the fix below, a damaged `prospects_status.json`
  (error plus recovery steps; Settings still usable; file untouched).
  Screenshots at 1366×900 and Settings at 1280×720 were spot-checked for
  readability.
- **Live read-only board check (separate from mocks).** On 23 Sep 2026 at
  22:00 UTC the desktop app's browser fetched six public boards once each
  (GET only): Greenhouse `adyen` (217 jobs) and `future-talent` (0), Lever
  `apolloresearch` (14), Ashby `airops` (19), Workable `huggingface` (8) and
  `linuxrecruit` (0). The app's own parsers and snapshot validation accepted
  all six current response shapes. Every parsed job had an HTTPS URL and a
  location; Greenhouse job links now use `job-boards.greenhouse.io`. Both
  sampled agency boards were valid but empty. Nothing was written to working
  data. The app's Python HTTP path could not reach these hosts from either
  sandbox, so live fetching through `signal_engine.py` itself is unverified.

### Defects found and fixed

- `gbt 16.0`: a client draft kept "Hi there" after its contact was added and
  could be recorded that way; client templates with unknown fields returned
  raw `{placeholders}` (sends now refuse the correction text); hooks placed
  after a full stop were lowercase in follow-ups; one "Data Engineer" posting
  derived the over-broad keyword "data".
- `gbt 17.0`: client won gave no next step and Create client had no prefilled
  profile; new clients silently defaulted to a $150 rate; the first reply card
  was collapsed; outcome "won" is now labelled as a meeting attended; raw
  stage keys in undo labels, stale Signals copy and the developer Deploy menu
  were removed.
- `gbt 18.0`: a damaged business file broke every screen with a traceback.
Each fix has a regression check that fails on the previous code.

### Release gates

| Gate | Status |
|---|---|
| Both workflows work from supported screens | Passed in the controlled browser rehearsal; founder use unverified |
| No code/file edits or remembered script order for ordinary work | Passed for daily work; full backup/restore remains a documented CLI procedure with the app stopped |
| Single tested queue/eligibility/budget/stage/follow-up logic | Passed (suite and rehearsal budget counts agreed) |
| Drafts, send records, sample evidence inspectable after restart | Passed after restart and after restore |
| No fabricated hiring, contact, sample, revenue or performance claims | Passed with controlled data: stale evidence dropped the current-posting hook, failed boards stayed stale, estimate labelled; live copy quality unverified |
| Distinct actionable empty/incomplete/stale/failed/corrupted states | Passed after `gbt 18.0` |
| Concurrency, double clicks, interrupted writes, restore coverage | Passed (automated) plus rehearsed restore |
| Configured data directory; no secrets/data in Git | Passed; only disposable directories were used; commits contain code, tests and docs only |
| Reproducible Linux install; optional explicit scheduling | Passed on Ubuntu 22.04 with Python 3.13.15; no scheduler created; Docker run still unverified |
| No external outreach from tests or startup | Passed; no messages sent, no provider calls |
| Superseded screens/instructions and live email integrations gone | Passed; four screens only |
| Browser rehearsal passed; founder walkthrough recorded separately | Browser rehearsal passed; **founder walkthrough not yet performed** |

### Still unverified or open

- Founder/friend acceptance walkthrough without developer narration.
- Live fetching through the app's own HTTP client, agency board discovery
  (`agency_discovery.py`) and employer discovery against live sites.
- Apollo prospect search and contact enrichment: no credentials used and no
  paid lookup run. The manual workflow did not need them.
- Actual Docker run; Windows `start_app.bat` was not re-run in this phase.
- Observations for the walkthrough, not changed: a current agency posting
  replaces the stated niche for that prospect's sample until the posting goes
  stale; client leads and the report show the board token rather than the
  confirmed company name; an approved sample row can later list extra roles
  from the same verified board; the new-meeting form keeps the previous
  entries after saving; recording a reply before a follow-up is due is done
  from the All view.

## Next step

The founder/friend acceptance walkthrough. Record what they could not find,
understand or trust, fix concrete obstacles, then mark Phase 8 complete.
