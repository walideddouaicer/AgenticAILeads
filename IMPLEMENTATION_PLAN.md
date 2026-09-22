# AgenticAILeads: minimum complete, dependable version

Implementation plan — 21 September 2026

Repository: `/home/walideddouicer/Work/AgenticAILeads`
Reviewed commit: `0a7db20adabe6bdd435cc013dc16bdb2b1aa409b`

## 1. Outcome and scope

Make the existing app understandable and reliable enough for two founders to acquire recruitment-agency clients and deliver the first client's work. Keep the existing Python/Streamlit stack and useful collection code. Reduce overlapping workflows, uncertain rules, misleading claims, and operational friction.

The founders have used the app only for testing. No clients have been acquired. Treat claims in old documents about outreach, response rates, affordability, or commercial performance as unverified historical notes unless the founders supply evidence.

This is a finishing and consolidation project, not a redesign of the business or a new platform. Completion means the two workflows below work with representative data, errors are recoverable, and a founder can operate them without reading implementation files.

**Acquisition:** add/find agency → inspect fit and evidence → prepare and correct sample → review message → send manually → record the actual message/sample → handle reply/follow-up → mark client won.

**Delivery:** create client profile → select matching hiring companies → inspect evidence/contacts → prepare outreach → send manually → track replies and attended meetings → export accurate client report.

The plan does not authorize external outreach, buying subscriptions, publishing the app, or scheduling recurring runs. Those are separate operational actions. Implementation, local checks, and fixtures need not send anything.

### Fixed implementation decisions

- Preserve Python, Streamlit, public ATS collection, CSV/JSON data, and existing identifiers. No React/FastAPI rewrite, database migration, agent framework, or LLM integration.
- One running app instance and one shared data directory. Two browser sessions may use it safely. Separate independently synchronized copies are unsupported.
- Manual sending is the supported release workflow for both LinkedIn and email. Keep generic exports; remove active email-vendor sending/reply-sync integrations from this release. Preserve them in Git history, not a second maintained implementation.
- Keep Apollo prospect/contact lookup as optional assistance, with CSV import and manual correction sufficient to complete the workflow without it.
- Preserve both business workflows. Client functionality is secondary, not deleted or left permanently unfinished.
- No automatic billing, invoices, subscription system, customer accounts, lead ownership marketplace, or automated conversation handling.
- Retain current follow-up defaults (three days, two follow-ups), but make them consistently applied operational defaults. Do not describe any chosen send count as a platform-guaranteed safe limit.
- Keep the current visual identity where it does not obstruct readability. No theme project, animation work, or new dashboard.

## 2. Evidence from the reviewed code

These are static-review findings to reproduce during the baseline phase, not claims that live integrations were tested.

| Finding | Current location | Consequence |
|---|---|---|
| Builder applies strict eligibility/tier rules; page mostly borrows ordering and falls back to a separate sort | `build_today.py`, `views/prospects.py` | Different definitions of what is ready today |
| Displayed daily cap does not subtract today's completed initial sends | `views/prospects.py` | Sending can continuously reveal new prospects despite cap wording |
| Shared runner checks for a lock, starts a process, then writes the lock | `common.py` | Two sessions can both start a writer; stale-lock clearing is based on session visibility |
| Whole status/cache files are rewritten from caller-owned snapshots | `core.py`, `common.py` | Concurrent actions and undo can overwrite another operator's work |
| Several malformed JSON reads return an empty object | `core.py`, `minilist_engine.py`, sweep/discovery loaders | Corruption can masquerade as missing data and later be overwritten |
| Samples use latest stored board snapshots; old signal text can become “currently hiring” without checking current state | `minilist_engine.py`, `core.py` | Stale information can be presented as current |
| Company tokens and fuzzy agency-name matching connect records | `core.py`, contact/sample code | Similar names and cross-platform tokens can attach the wrong evidence/contact |
| Geographic matching uses broad substrings and includes remote in market expansion | `filter_engine.py`, `core.py` | An apparently relevant sample can include the wrong market |
| Fixed template promises are independent of sample size/contact coverage | `core.py`, `minilist_engine.py` | Message may promise ten contacts or delivery volume the output does not support |
| Marking contacted records stage/date, not the final message and sample | `core.py`, `views/prospects.py` | Cannot reliably reconstruct what the recipient received |
| Old page instructions and docs disagree with current navigation | views, `README.md`, `REVAMP.md`, `CLAUDE.md` | Operators and coding agents receive conflicting directions |
| Setup depends on unpinned packages; scheduling scripts contain old Windows paths | requirements and `.bat` files | Fresh Linux installation is not reproducible or operationally complete |
| Contact-provider key paths differ between components; email adapters contain verification TODOs | contact/enrichment/core/delivery modules | Configuration and integration success cannot be inferred from presence of a key |

The checkout has curated watchlists but no operational prospect/contact/history/status files. Recover old data if available; otherwise start a clearly labeled empty installation. Never invent historical observations or commercial results from test fixtures.

## 3. Final interface

Four navigation entries, with plain labels and few persistent controls:

### Prospects — default page

- **Today:** replies to handle, follow-ups due, then eligible new contacts within the remaining initial-send budget.
- **All:** search, stage filter, select a prospect, edit it, inspect its activity.
- Selected prospect shows agency/person, source/contact links, niche/market, concise match explanation, sample, editable message, and relevant next action.
- Samples and messages are reviewed together. Export is an action on the same prospect, not another destination.
- Show missing information with a correction action. An empty list must distinguish no prospects, nothing eligible, nothing due, exhausted budget, and unavailable/stale inputs.
- Import/find controls are secondary; provider settings and logs do not dominate daily work.

### Clients — secondary page

- With no real clients: explain the workflow and provide Create client. The default prospecting filter must not appear as a paying client.
- Client selector, profile/preferences, matched companies and outreach, outcomes/report. Reuse the existing components after their business logic is extracted.
- Existing client stages/keys stay compatible. Label `won` accurately as an attended meeting where that is the existing meaning; distinguish this from an agency becoming a client.
- Turning a won agency into a client opens a prefilled profile for review; it must not silently assume target roles, market, or prices.

### Signals

- Employer boards, agency boards, tracked jobs and recent collection results in one place, with clear separation between the two board sets.
- Refresh tracked boards is the primary maintenance action. Add/import/discover boards are secondary actions.
- Show last successful check, partial failures, and whether the stored data can support current claims.

### Settings

- Prospect criteria, message templates, integration availability, data location, backups, run history, short help.
- Advanced technical details in an expander. Ordinary errors should say what failed and the corrective action.
- No separate Home dashboard, standalone Today page, Mini-List page, or large Help navigation entry.

## 4. Behaviour contracts

### A. One decision path for outreach

Create one Streamlit-free queue function used by both the CLI and pages. Inputs include current records, statuses, sample availability, explicit time/date, and policy. Output contains actionable items plus reasons for missing requirements. Pages render this result; they do not recreate ranking or silently substitute another algorithm.

- Reply/call handling first, follow-ups second, new outreach third.
- Initial-send eligibility: not already in play/terminal, chosen usable contact channel, adequate agency identity, and a supported opener. A sample-based opener needs at least one approved sample row; a different factual opener need not promise a sample.
- LinkedIn-only outreach does not require email. Email outreach requires an actual usable address, not a locked placeholder. Email syntax must never be labeled deliverability verification.
- Drop the small/bulk split and compulsory ten-company/ten-contact gate. Display actual sample size and contact coverage.
- Order eligible new work deterministically by evidence recency and usable sample quality; use stable tie-breakers. Do not pretend the ranking predicts conversion.
- Existing off-target imported prospects are flagged for review, not silently treated as matching current criteria or silently deleted.
- Compute remaining initial sends from recorded initial-send actions in the configured business timezone. Follow-ups are tracked separately and remain visible. Reaching the budget must stop new recommendations; rerunning or reopening the app must not reset it.
- Terminal states and replied/booked states never become cold-outreach candidates through a fallback path.
- `today.json`, if retained for CLI compatibility, is a derived export/cache. It never outranks current status. A stale cache cannot revive contacted people or hide current follow-ups.

### B. Reliable state and operator actions

Keep legacy keys and file shapes readable. Extend status entries additively with a small activity list: action ID, timestamp, operator, action type, channel, final message, and sample snapshot or immutable sample reference. Store status and its new activity in the same atomic update. Do not create a separate event-sourcing platform.

- Under a short data-write lock, reload latest state, validate expected record revision/stage, apply one record-level change, and atomically replace the file.
- Action IDs make duplicate button submissions harmless. Reject stale conflicting edits with a reload message.
- Undo reverses only the relevant eligible action if its revision still matches. It cannot restore an old whole-file snapshot over another operator's work.
- Message edits survive rerenders/navigation and are included when marking sent. Exporting/copying never counts as sending.
- Existing historical stages remain readable. Never fabricate past message text, send timestamps, or activity entries. Explain when legacy counts are incomplete.
- When adding a LinkedIn URL or correcting a name changes the legacy-derived prospect key, migrate that record's state explicitly and atomically; do not orphan history or generate a second cold prospect. Preserve existing lead identifiers unchanged.
- All persistence uses UTF-8, schema checks, atomic replacement, and explicit errors for damaged business data. Missing optional files are an empty state; corrupt files are not.
- Serialize CSV appends, deduplicate before append, and preserve user-edited prospect values. Refreshes cannot replace manual corrections.

### C. Evidence, samples, and copy

- Preserve job URLs, board identity, source identity and last successful observation. Track board refresh outcomes separately from historic signal timestamps.
- Suggested release freshness default: a source verified within 48 hours can support present-tense hiring copy. This is an operational policy, not a validated market threshold. Older/unknown sources remain inspectable but require refresh before being presented as current.
- Failed fetches do not close jobs, erase good snapshots, or make stale rows fresh. A successful empty response can close jobs; malformed responses cannot.
- A new agency posting is evidence of an advertised role, not proof of a newly signed client mandate. Use literal descriptions of observed facts.
- Join agencies to boards using a confirmed mapping. Fuzzy discovery can suggest a match but cannot silently substantiate outreach copy.
- Use platform+board identity internally. Group across boards only with verified company/domain mapping; use that mapping for contacts. Unknown identities stay separate rather than merge on a similar token/name.
- Reuse one matcher for client leads and samples. Preserve technology phrases such as `.NET`, JavaScript, C++, and data engineering; avoid naive matches such as Java inside JavaScript unless explicitly requested.
- Distinguish remote work from geographic eligibility. Unknown/ambiguous locations need review; they do not automatically match every market.
- Remove the default 60-role company-size exclusion. Any explicit client size preference needs actual size evidence or a visible unknown value. Do not turn job counts into asserted employee counts.
- Sample rows show actual matching roles, location, sources, contact availability, and verification date. Allow exclude/correct before export using the existing review workflow.
- Generate template facts from the reviewed sample. No fixed ten-contact claim if only four contacts exist; remove unsupported “50 per week” or equivalent volume promises. Keep commercial offer text editable without inventing evidence.
- Missing template fields produce a visible correction error, not a raw `{placeholder}` message. Escape untrusted text in HTML and generate CSV with the CSV library; protect spreadsheet-facing exports from formula execution while preserving source data.

### D. Collection and integrations

- One portable runner for an individual engine and for the refresh chain. CLI and UI use it, and it takes the same process-safe engine lock before starting work.
- Run-long engine lock prevents two collectors. Short data locks protect shared read/modify/write operations without holding the UI hostage during network requests.
- Release only a lock owned by the completing run. Determine activity from process/lock ownership, not whether a browser session has a job object. Crashed-run recovery must not kill or unlock an active run.
- Use bounded network timeouts, limited retries for transient/rate-limit failures, and meaningful partial-failure results. Stop on credentials/permission problems with a useful diagnosis. Never retry an uncertain chargeable action blindly.
- State/history/signal writes need a recoverable commit sequence: save the fetched batch as a pending checkpoint before changing snapshots; apply its recorded-date observations and deduplicated signals idempotently; clear the checkpoint only after all outputs succeed. Test restart after each write boundary. Atomic individual files alone do not solve a crash between files.
- Record per-source successes/failures and run timestamps. “Healthy” reflects actual results and freshness, not just whether files exist. Missing optional integration is “not configured,” not success or a broken app.
- Centralize data paths and credentials: explicit environment settings take precedence; a documented local `.env` location supplies fallback. Keep credentials and raw private probe responses out of logs/Git.
- Enrichment targets the reviewed sample/client selection. Missing or failed lookups must not become permanent negative cache entries. Provide bounded refresh for old/no-result records, preserving manual contact corrections.
- Verify provider access and response parsing during an explicitly initiated live lookup. Offline tests cannot establish that an account has access or that contacts are accurate.

## 5. Implementation sequence

Each phase is a reviewable change. Complete its meaningful checks and update the implementation checklist before moving on. No branch push, production deployment, or real outreach is implied.

### Phase 0 — Baseline and executable scope

**Work:** inspect the current checkout and any new user changes; create a working branch; read repository instructions; record baseline failures. Establish a project-local environment, supported Python version, and reproducible dependency versions after compatibility testing. Inspect tests before running them and keep every generated file in a temporary data directory.

Create concise `AGENTS.md` guidance and align `CLAUDE.md` with this plan. Keep identity/history preservation rules; supersede obsolete frozen-behaviour claims that conflict with the user-requested fixes. Record the four-screen target and no-new-features boundary. Keep stale plans out of active agent instructions.

**Checks:** existing unit/delivery/page suite results recorded; empty and populated app launch attempted; known queue/cap inconsistencies reproduced in small regression cases; no external sends or paid calls; baseline data inventory captured without secrets.

**Done:** reproducible baseline, explicit known failures, no unexplained mutations. Existing failing tests are triaged, not simply deleted.

### Phase 1 — Safe persistence and process execution

**Files:** `core.py`, `common.py`, `sweep_core.py`, all engine writers; introduce small shared `storage.py`, `runtime.py`, and `runner.py` only where they remove duplication.

Implement validated reads, atomic writes, record-level updates, process locking, consistent paths/config loading, snapshots/backups and restore procedure. Replace engine launch race and unsafe stale-lock clearing. Add run results and recoverable collection checkpoints. Keep old data readable.

**Checks:** simultaneous edits to different records survive; conflicting same-record changes are detected; two engine launches start only one collector; failed launch releases its own lock; malformed JSON remains preserved; interrupted writes retain old valid data; interrupted multi-file commits replay without lost/duplicate signals or changed observation dates; `AAL_DATA_DIR` is honored by every writer.

**Done:** errors cannot silently reset business data, and the two founders cannot overwrite each other through routine actions.

### Phase 2 — Evidence and sample correctness

**Files:** `filter_engine.py`, `signal_engine.py`, `prospect_signal_engine.py`, `agency_discovery.py`, `contacts.py`, `enrichment_engine.py`, `minilist_engine.py`, relevant core helpers.

Implement freshness and source validation, confirmed agency/company mappings, consistent niche/location matching, accurate contact association, deliberate enrichment refresh and corrected exports. Remove unvalidated size gates and unsupported generated claims.

**Checks:** Java/JavaScript and .NET examples; remote location ambiguity; similarly named agencies; same board token on two platforms; stale/failed/empty board fetches; duplicate company roles; zero/some/full contact coverage; manual correction survives refresh; first baseline produces samples from current jobs without inventing new-opening signals.

**Done:** every exported row and factual opener is traceable to the correct source with honest uncertainty.

### Phase 3 — One queue and durable outreach actions

**Files:** `build_today.py`, `core.py`, prospective `outreach.py`, affected UI handlers and tests.

Extract the shared queue function and prospect context builder. Remove alternate ranking/tier paths. Add final-message/sample snapshots, explicit action timestamps, remaining initial-send budget, conflict-safe undo, persistent drafts, and stable history on prospect identity corrections.

**Checks:** LinkedIn-only prospect eligible without email; no hook/sample claim produces a correction state; empty/stale cache never changes decisions; initial send consumes budget exactly once; follow-up does not consume initial budget; reply/won/dismissed stop follow-ups; local-midnight boundary works; note edits do not move send timestamps; stale undo cannot revert another user's work.

**Done:** CLI and UI produce the same decisions for the same inputs; a completed send can be reconstructed exactly.

### Phase 4 — Consolidate the interface

**Files:** `app.py`, `views/prospects.py`, `views/signals.py`, `views/clients.py`, new `views/settings.py`, relevant `common.py` UI helpers.

Implement the four-entry navigation and shared selected-record layout. Move samples into prospect review, templates/settings out of daily work, source maintenance into Signals. Give every blocker a concrete correction action. Hide raw logs behind details. Replace ambiguous success states and removed-tab references. Preserve unsaved edits intentionally or warn before discarding them.

Add the minimum CSV upload/manual edit surface needed to use the existing import workflow without terminal edits. Reuse existing parsing; do not build a generic import designer. Show row-level rejection reasons and deduplicate accepted entries.

**Checks:** actual browser walkthrough plus AppTest with empty, populated, incomplete, stale, error and exhausted-budget fixtures; draft survives rerender; operator can import/correct/review/export/record a send without CLI commands; readable on an ordinary laptop display; source links and downloaded sample agree.

**Done:** the acquisition workflow is complete on the new screens. Do not delete old screens until equivalent required behavior is exercised.

### Phase 5 — Finish minimum client delivery

**Files:** existing client/config/queue/inbox logic consolidated behind `views/clients.py`, shared outreach/sample helpers.

Provide profile creation/editing, preview matches from current live jobs, explicit backfill/selection, contact/sample review, manual outreach tracking, outcome recording, and CSV reporting. Backfill keeps baseline semantics and does not duplicate existing leads. Keep client/prospect data and identity spaces separate.

Define a lightweight meeting outcome record with meeting identity/date and whether it is free/chargeable. Deduplicate across multiple vacancy rows for the same meeting. Use each client's agreed rate; label financial totals as recorded estimates, not invoices or confirmed payments. Do not count the promised free first meeting as chargeable by default.

**Checks:** no phantom default client; two agencies with different niches receive different matches; two vacancies/contact rows do not become two meetings; first free meeting excluded from chargeable estimate; per-client price applied; report matches selected rows and actual outcomes; client actions do not alter acquisition status; duplicate-contact warning remains useful across clients.

**Done:** a first client can be served and accurately reported on without restoring hidden old pages or editing JSON manually.

### Phase 6 — Remove dead paths and align documentation

Remove superseded Today/Home/watchlist/live-jobs/mini-list/help pages and separate client queue/inbox/config pages after consolidation checks pass. Delete unreachable code rather than maintaining two UIs. Keep CLI entry points that remain useful.

Remove live Instantly/Smartlead/Apollo sequence sending and reply-sync code from the supported path; keep only required manual/export helpers. Remove old bulk-tier outputs from consumers, with backward-compatible reads where necessary. Delete the redundant enriched-signals join only after consumers use one contacts join and old files remain safely ignorable.

Reduce `common.py` to actual UI helpers and use explicit domain imports in changed code. Avoid a mass import/style rewrite. Keep modules only when they have a clear independent responsibility; no speculative provider/plugin architecture expansion.

Rewrite README as install → configure → acquire → deliver → backup/recover. Keep one concise operator guide. Mark/archive old planning documents and align AI instructions. Leave the marketing landing pages out of this refactor; do not publish them.

**Checks:** no broken navigation/imports/help targets; no references instructing ordinary operators to open deleted screens; supported CLI commands still work; tests target final supported behavior, with removed vendor-adapter tests removed only alongside those adapters.

**Done:** one supported workflow per task and one current description of it.

### Phase 7 — Linux setup and recovery

Provide a documented project-local install/start command and portable Python refresh entry point. Replace the old hardcoded Windows scheduler dependency; Windows wrappers may delegate to the portable runner if retained. Do not create cron/systemd jobs automatically.

Keep auth for shared access. Bind local development to loopback by default. Review container flags that disable CORS/XSRF protection and restore secure defaults with a documented tested configuration. Preserve the existing container as an optional deployment method; do not deploy during cleanup.

Backups include operational state, profiles, mappings, contact corrections and both histories, not just code. Exclude credentials from routine shareable archives. Restore into a separate directory first, validate it, and document deliberate activation. Seed first-run defaults only into an empty installation, including separate agency/employer watchlists; never overwrite existing data.

**Checks:** clean Linux install/start; successful refresh against fixtures; second app session sees persisted actions; missing credentials give setup guidance; backup/restore round trip; persistent-volume restart preserves state; no real data/credentials staged in Git.

**Done:** the app can be installed, operated, restarted and recovered on the new machine without old-machine paths.

### Phase 8 — Release rehearsal

Run the full final suite once after integration. Use network-mocked fixtures for automated tests. Do not keep broadening tests after they pass unless a new change or failure warrants it.

Rehearsal A: clean directory → import agencies → load controlled board responses → inspect one agency and sample → edit message → simulate manual-send recording → restart → advance test clock → follow-up → record reply → client won.

Rehearsal B: create that client's profile → review relevant hiring companies → record manual outreach and one free/one chargeable attended meeting → export report → restore backup and verify the same history/report.

Live read-only smoke checks against a few public boards verify response compatibility and source quality. A configured provider lookup is separate, bounded and operator-initiated; absent credentials must not prevent the manual release workflow. No real prospect is contacted as part of testing.

Have the friend perform one acceptance walkthrough without developer narration. Observe where they must ask what to click, what a label means, or whether an output is trustworthy; fix those concrete obstacles. Automated/browser checks can precede this, but do not claim user acceptance occurred until it does.

**Done:** all release gates below are met, with any untested live integration explicitly listed.

## 6. Release gates

- [ ] Both acquisition and first-client delivery work from the supported screens.
- [ ] No code/file edits or remembered script order required for ordinary work.
- [ ] Queue, eligibility, budget, stages and follow-up logic have a single tested implementation.
- [ ] Final drafts, actual send records and sample evidence remain inspectable after restart.
- [ ] No fabricated current-hiring, contact-verification, sample-size, revenue or performance claims.
- [ ] Empty, incomplete, stale, failed and corrupted inputs have distinct actionable states.
- [ ] Concurrent use, double clicks, interrupted writes and restore have meaningful regression coverage.
- [ ] All data paths respect the configured directory; operational secrets/data are absent from Git changes.
- [ ] Linux installation is reproducible; scheduled operation is optional, explicit, and uses the same runner.
- [ ] No external outreach occurs through tests or background initialization.
- [ ] Superseded screens/instructions and unsupported live email integrations are gone from the active app.
- [ ] Browser rehearsal passed and the founders' walkthrough is recorded separately from automated tests.

Once these gates pass, stop cleanup. Do not add a new model, dashboard, scoring method or integration to make the release feel more complete.

## 7. Codex execution rules and handoff

Implement sequentially: baseline → persistence/runner → evidence/samples → queue/actions → UI → client delivery → removal/docs → Linux/recovery → rehearsal. Evidence and queue contracts must stabilize before UI cleanup. Keep each phase in a small reviewable commit or coherent series of commits.

At each phase report: concrete behavior changed; tests and manual checks actually run; remaining failures/limitations; intentional data-format additions; next phase. Do not present mocks as live validation. Do not claim done because code compiles or pages render.

Use existing assertion/AppTest patterns unless they prevent a required test. No new test framework just for preference. Add tests for behavioral risks, not every helper or visual change. Avoid fixed line-count targets; smaller code is useful only if it removes competing behavior or maintenance burden.

Preserve unrelated user edits. Read current Git state before each phase. Back up operational data before any migration. Future credentials, deployments, real sends, and recurring scheduling remain separate actions. Routine implementation choices within this plan do not need repeated approval.

### Required commit numbering

Every new commit created for this implementation must end its subject line with the exact lowercase suffix `gbt N.0`, starting at `gbt 1.0` and increasing by one for every subsequent commit: `gbt 2.0`, `gbt 3.0`, and so on.

- Example first subject: `Establish baseline and reproducible setup gbt 1.0`.
- Example next subject: `Make state updates atomic gbt 2.0`.
- Number commits, not phases. If a phase needs three commits, it consumes three successive numbers. Fix, documentation, test, and merge commits created for this implementation follow the same rule.
- Before each commit, inspect the implementation branch's Git history and continue from its highest existing `gbt N.0` suffix. Start at 1 only when this implementation has no numbered commits. Never reset the sequence when changing tasks or sessions.
- Do not rewrite or relabel commits made before this work. Record the parent of `gbt 1.0` as the pre-implementation baseline; the reviewed checkout was `0a7db20adabe6bdd435cc013dc16bdb2b1aa409b`, but recheck for intervening changes before beginning.
- Preserve these suffixes; do not squash away numbered checkpoints or amend/rebase published history without an explicit user request.
- Include each resulting commit hash, subject, and completed phase in progress reports. Add this rule to the repository's `AGENTS.md` during Phase 0 so future Codex sessions inherit it.

### Initial implementation prompt

> Work in `/home/walideddouicer/Work/AgenticAILeads`. Read `AgenticAILeads-implementation-plan.md` supplied with this task and inspect the current repository instructions and Git state. Implement the minimum complete, dependable version described by the plan, following its phases and acceptance checks. Preserve both agency acquisition and first-client delivery. Keep Python/Streamlit and compatible file-based data; do not add new product features, a framework rewrite, LLMs, or automatic outreach. Begin with the baseline and reproducible environment, then fix persistence and execution before changing screens. Use isolated fixtures for tests, preserve user data and unrelated edits, and never send messages or spend provider credits in automated tests. Keep progress and the checklist current; report exact checks and remaining limitations honestly. Continue through the implementation phases without seeking repeated approval for routine authorized changes. Stop for genuinely missing access or a user decision that changes scope, and distinguish that from work you can continue locally. Do not push, deploy, schedule recurring work, or contact prospects as part of implementation.

> Commit rule: every commit subject created for this implementation must end with `gbt N.0`, starting with `gbt 1.0`, then `gbt 2.0`, `gbt 3.0`, and continuing once per commit, not per phase. Inspect history to resume the sequence across sessions. Preserve all earlier commits unchanged, record the pre-implementation baseline, and add this convention to repository `AGENTS.md` during Phase 0. Report hashes and numbered subjects at phase checkpoints.

This plan does not itself modify the application. The baseline suite, live APIs, rendered UI and user acceptance remain to be exercised during implementation.
