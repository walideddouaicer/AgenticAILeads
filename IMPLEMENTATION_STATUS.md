# Implementation status

Updated: 22 September 2026. Scope: Phase 0 baseline, setup and verification.

## Checkpoints

- Pre-implementation commit: `0a7db20adabe6bdd435cc013dc16bdb2b1aa409b`.
- Branch: `codex/minimum-complete`.
- First implementation subject: `Establish isolated baseline and project guidance gbt 1.0`.
- Next implementation commit suffix after this checkpoint: `gbt 2.0` (verify Git history).
- No application workflow, matching rule, storage format or integration behavior
  was changed in Phase 0. The Docker Python minor version now matches local setup;
  the image itself has not been built or deployed.

| Phase | Status |
|---|---|
| 0 — baseline, environment, instructions | Complete at the first numbered checkpoint |
| 1 — persistence and process execution | Next |
| 2 — evidence and sample correctness | Not started |
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
static-review findings until their phases reproduce and fix them. ResourceWarnings
about unclosed CSV readers occur in the existing app and belong in Phase 1's file
access cleanup. Passing the repaired baseline suite is not release readiness.

## Next phase

Implement Phase 1: validated reads and atomic writes, safe record updates,
shared process locking, consistent data/credential paths, recoverable engine
commits, run outcomes, backups and restore tests. Preserve existing identifiers
and data compatibility. Continue the commit sequence with `gbt 2.0`.
