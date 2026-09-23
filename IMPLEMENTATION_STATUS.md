# Implementation status

Updated: 23 September 2026. Scope: Phase 0 baseline, Phase 1 persistence,
and Phase 2 evidence/sample correctness.

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
  Its hash is reported with the commit. Next suffix after it: `gbt 10.0`.

| Phase | Status |
|---|---|
| 0 — baseline, environment, instructions | Complete at the first numbered checkpoint |
| 1 — persistence and process execution | Complete through `gbt 6.0` |
| 2 — evidence and sample correctness | Implemented offline through `gbt 9.0`; live compatibility still unverified |
| 3 — unified queue and durable actions | Not started |
| 4 — interface consolidation | Not started |
| 5 — minimum client delivery | Not started |
| 6 — dead paths and documentation | Not started |
| 7 — Linux operation and recovery | Not started |
| 8 — release rehearsal | Not started |

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
requirements. Other Python versions/platforms have not been verified.

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

## Next phase

Phase 3: one queue decision path, durable final message/sample actions and
initial-send budget. Keep both existing expected-failure regressions until
their behavior is fixed; then remove each expected-failure marker and retain
the assertion.
