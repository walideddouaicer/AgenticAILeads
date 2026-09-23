# AgenticAILeads implementation instructions

## Scope and source of truth

Read `IMPLEMENTATION_PLAN.md` and `IMPLEMENTATION_STATUS.md` before changing code.
The user wants the smallest complete, reliable version of the existing app.
Preserve agency acquisition AND first-client delivery. No clients have been
acquired; operational/business claims in older documents are not proof.

Keep Python/Streamlit and compatible CSV/JSON data. Do not add LLMs, agent
frameworks, dashboards, vendor integrations, or a frontend/database rewrite.
Final navigation: Prospects, Clients, Signals, Settings. Follow the plan's
dependency order; do not delete a required workflow before its replacement works.

## Required commits

Every implementation commit subject ends with lowercase `gbt N.0`: `gbt 1.0`,
`gbt 2.0`, `gbt 3.0`, etc. Increment once per commit, including tests/docs/fixes,
not per phase. Inspect implementation history before each commit; continue the
sequence across tasks and sessions. Preserve older commits unchanged. No squash
or rewriting published history unless explicitly requested. Report commit hash,
subject, phase, actual checks and remaining limitations at each checkpoint.

Baseline parent: `0a7db20adabe6bdd435cc013dc16bdb2b1aa409b`.
Implementation branch: `codex/minimum-complete`.

## Verification

Supported/tested baseline: Linux x86_64, Python 3.13.15. Use the project `.venv`:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/check.py
```

`scripts/check.py` copies source into a temporary directory, clears provider
credentials, blocks network connections in Python child processes, and runs
syntax, unit, page/interaction and workflow regression checks. It does not
copy working runtime data or private probe files. Optional `--logs-dir PATH`
retains detailed results. Do not run fixture-generating tests on working data.

The Phase 3 queue and budget regressions in `tests/test_workflow_gaps.py`
must remain passing. Do not skip assertions just to obtain green tests.
No tests may send outreach or spend provider credits. Keep checks focused on
behavioural risk; preserve the assertion/AppTest style already in the repository.

## Data and architecture

- Preserve `lead_key`, current prospect identities and historic statuses; identity
  corrections need explicit migration. Keep prospects and client leads separate.
- Never reset damaged business/history files to empty state. Back up before
  migration. `job_history.json` and `mandate_history.json` cannot be reconstructed.
- Domain/queue/storage/runner logic must remain independent of Streamlit.
  Pages render and invoke shared operations; no competing ranking/state logic.
- Honour `AAL_DATA_DIR` everywhere. Never expose credentials, private probes or
  live data in Git, logs, generated examples or reports.
- Preserve first-sweep baseline semantics. Failed source fetches cannot close
  jobs, erase snapshots or make stale evidence current.
- Preserve unrelated user changes. External outreach, paid provider calls,
  deployment, pushing and recurring scheduling need their own user instruction.

Older `REVAMP.md`/`PLAYBOOK.md` describe history and ideas, not current acceptance
criteria. The user-approved implementation plan supersedes obsolete frozen-rule
instructions. Make ordinary in-scope implementation choices without repeatedly
asking the user. Stop at a phase checkpoint when the user requested that phase.
The README is the current operator guide. The only supported pages are
Prospects, Clients, Signals and Settings. Legacy page, email-sequence send and
reply-sync paths are removed; preserve the manual workflow and data readers.
For new installs, `start.py` and `refresh.py` choose project-local `data/`
unless `AAL_DATA_DIR` is set. Keep the legacy root-file default readable for
existing direct script users. `bootstrap.py` seeds defaults only into an empty
directory; `recovery.py` restores into a new directory and never activates it.
Do not add scheduled jobs or deploy the optional container without instruction.
