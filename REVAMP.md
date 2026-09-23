# Historical planning notes — superseded by IMPLEMENTATION_PLAN.md and README.md.
# Do not use these old page and integration instructions to operate the current app.

# REVAMP — from CRM to daily driver

**Status:** plan, not yet executed. Written 2026-08-09.

## Why the app feels cluttered

It isn't badly built. It's built for a company that has clients. You don't
have clients yet, so most of it is furniture.

Ten pages. Seven of them serve **Loop A** — finding leads for paying
clients and delivering them. Zero paying clients exist. Meanwhile the one
page that could get you a client, Prospects, is one entry in a ten-item
sidebar, sitting under a heading called "Work" next to two pages you can't
use yet.

The noise isn't cosmetic. It has a cost:

- `configs/` holds `clientid1.json`, `default.json`, `id2.json` — test
  junk. The nightly sweep matches every job against all three, so
  `signals.csv` fills with results for clients that don't exist.
- `views/help.py` is 412 lines documenting ten pages. Every change owes a
  Help edit.
- Opening the app presents a choice of ten places to go, when there is
  exactly one thing to do today.

## The principle

> **The app has one job right now: help us send 10–20 good outreach
> messages a day. Everything else is hidden until it pays rent.**

Four rules that follow from it:

1. **One screen per job-to-be-done.** Not per data table.
2. **Nothing on screen that doesn't lead to an action today.**
3. **Hide, don't delete.** Loop A works and will be needed the week we
   land client #1. It goes behind a flag, not in the bin.
4. **The UI triggers automation, never contains it.** Every engine stays
   a CLI script. Pages only start them and read their output.

Rule 4 is the one that makes automation easy later: if the daily queue is
a *file* that a script produces, then a nightly job can produce it and the
UI never changes.

## The cut

| Page | Now | After | Why |
|---|---|---|---|
| `home.py` | Engine runner + stats | **merge → Settings** | Starting engines isn't a daily job |
| `queue.py` | Lead outreach (Loop A) | **hidden** | For clients we don't have |
| `inbox.py` | All leads, paginated (Loop A) | **hidden** | Same |
| `clients.py` | Client delivery (Loop A) | **hidden** | Same |
| `configs.py` | Client filter profiles | **hidden** | Same |
| `prospects.py` | Our outreach (Loop B) | **→ split: Today + Prospects** | The only page that earns money now |
| `minilist.py` | Gift generator | **fold into Today** | Never used standalone; it's a step in outreach |
| `watchlist.py` | Board management | **merge → Signals** | Infrastructure, weekly not daily |
| `livejobs.py` | Browse tracked jobs | **merge → Signals** | Same data, different lens |
| `help.py` | Docs | **trim to what remains** | Shrinks with the surface |

**Result: 4 visible pages.** Today · Prospects · Signals · Settings.
Six Loop A pages stay in `views/`, restored by one env var when a client
signs.

## The new shape

### 1. Today — the daily driver

Replaces "browse a list and figure out what to do" with "here is the next
one." One prospect at a time, full screen:

- who they are, and the **hook** (their live mandate, from the mandate
  sweep)
- the drafted message, editable, in the template that's being measured
- the mini-list gift, already generated, one click to download
- three buttons: **Sent** · **Skip** · **Not a fit** (with a reason)

Then the next one. A counter: `7 / 10 sent today`. When it hits the
target, the page says so and stops handing out work.

**Critical design decision:** Today reads a single ranked file,
`data/today.json`, produced by a script. It does not compute the ranking
itself. That means the day the ranking gets smart — scored by
`days_open`, `repost_count`, reply-rate feedback — nothing in the UI
changes. The intelligence moves to a nightly job and the page stays dumb.
This is the whole "easy to add automation" requirement, and it costs
nothing to build this way from the start.

### 2. Prospects — the CRM view

What `prospects.py` is today, minus the mini-list block that moves to
Today: the list, search, stage filters, follow-ups due, notes. The place
you go to *manage*, not to *work*. Keeps stages, the double-DM guard, and
the follow-up timers exactly as they are.

### 3. Signals — the supply side

`watchlist.py` + `livejobs.py` merged. Boards tracked, jobs live, history
depth (`days_open` distribution, reposts seen), and a button to run
discovery. Weekly page, not daily.

### 4. Settings — everything else

Engine runner from `home.py`, message templates, delivery channel, auth
info, data-file health. One page, collapsed sections.

## Step by step

Each step ends with the repo's verification suite green. Do them in order
— step 2 depends on step 1, and skipping it means writing every page
twice.

### Step 0 — clean the floor (30 min)

- Set `APP_USERNAME` / `APP_PASSWORD` as user env vars.
- Delete `configs/clientid1.json` and `configs/id2.json`. Keep
  `default.json` only if it's a real profile; otherwise delete it too and
  let the sweep run with no configs.
- Delete `discover_tokens.py` (superseded, unreferenced).
- Confirm `job_history.json` exists and `sweep_failures.log` is empty.

**Why first:** the junk configs are actively polluting `signals.csv`
every night.

### Step 1 — extract `core.py` (half a day)

Move the 29 pure functions out of `common.py` into a Streamlit-free
`core.py` (see Phase 1 notes — 16 of 18 lead functions are already pure).
Split `set_stage` / `mark_followed_up` into a pure `core.apply_stage()`
plus the existing UI wrapper that adds toast and undo.

`common.py` becomes UI glue that imports `core`. `core.py` imports
neither streamlit nor common.

**Acceptance:** `python -c "import core"` works without streamlit
installed; `tests/test_pages.py` passes untouched.

**Why before the UI work:** the new pages must be thin. If they're built
against a god-module they get rewritten when it's split. Also, the
`today.json` builder in step 2 is a headless script — it *cannot* import
`common.py` today.

### Step 2 — build `today.json` + the Today page (1 day)

First the script, `build_today.py`:

- reads prospects, statuses, mandate signals, history
- drops anyone contacted, dismissed, or not yet due for follow-up
- ranks by hook quality (live mandate > recent mandate > niche only)
- writes the top N with everything the page needs: prospect, hook,
  mandate URL, niche keywords, pre-rendered message
- `--n 20`, `--dry-run`

Then `views/today.py`, which only renders that file and writes stage
changes back through `core`.

**Acceptance:** `python build_today.py --n 10` produces a file; the page
renders it; marking Sent advances the stage and the next card appears;
empty file shows "nothing due — run build_today.py".

### Step 3 — collapse the navigation (2 hours)

`app.py` shows four pages. Loop A pages restored by
`AAL_SHOW_CLIENT_PAGES=1`.

**Acceptance:** `test_pages.py` runs both with and without the flag.

### Step 4 — merge Signals and Settings (half a day)

`watchlist.py` + `livejobs.py` → `signals.py`. `home.py` + `configs.py` →
`settings.py` (configs section only visible with the flag).

### Step 5 — trim Help (1 hour)

Rewrite for four pages. It should get much shorter.

### Step 6 — verify

```powershell
python -m compileall -q app.py common.py core.py views
python app.py
python tests/test_units.py
python tests/test_delivery.py
python tests/test_pages.py
```

Plus one manual pass: log in, open Today, send one for real.

## Strip / rework checklist

Audited 2026-08-20. One number frames it: **3,703 lines are "shared
infrastructure"** vs 1,259 for Loop A and 1,726 for Loop B. Half the
codebase is plumbing, and `common.py` is 1,141 of it.

The cause isn't random over-building: we built the *product for clients*
before having clients, and built for a scale we don't have.

### Strip — delete outright (~360 lines)

- [ ] `discover_tokens.py` (171) — dead, superseded by `discovery_engine.py`,
      imported by nothing
- [ ] `InstantlyAdapter` (46) + `SmartleadAdapter` (48) in `delivery.py` —
      **only once Apollo is committed.** Keep `Manual` + `ApolloSequences`.
- [ ] `configs/clientid1.json`, `configs/id2.json` — test junk; `id2` has
      already written 8 garbage rows into `signals.csv`
- [ ] `ai-leadgen-launch-plan.md` (63) — superseded by PLAYBOOK + REVAMP
- [ ] `enriched_signals.csv` output (~30) — a denormalised join of signals +
      contacts, read by nothing but itself and one page. Join at read time.

### Hide — works, but serves a customer we don't have (910 lines)

Behind `AAL_SHOW_CLIENT_PAGES=1`. **Do not delete** — needed the week
client #1 signs.

- [x] `views/queue.py` (276)
- [x] `views/inbox.py` (204)
- [x] `views/clients.py` (174)
- [x] `views/configs.py` (256)

### Shrink

- [ ] `views/help.py` (412) — docs for ten pages; with four it should be ~120
- [ ] `tests/test_delivery.py` (270) — shrinks when two adapters go
- [ ] Docs: 922 lines across 6 files. `DEPLOY.md` (247) covers a Coolify
      deploy we don't run — keep, stop maintaining.

### Rework — in order of payoff

- [x] **1. Split `common.py`.** DONE — core.py, 1141->679 lines. 29 functions are already pure and trapped
      behind `import streamlit`. Nothing headless can call `lead_key()` or
      `followup_due()`. Blocks every automation on the roadmap.
- [x] **2. DONE (contacts.py + --from-minilists).** `enrichment_engine` → provider adapter + `--companies`.**
      Hardcoded to Apollo, and enriches the 87 companies in `signals.csv`
      when mini-lists draw on a different 64. Both wrong.
- [x] **3. DONE.** `niche_for()` priority was backwards. CSV keywords outrank
      mandate-derived ones, so the mandate sweep's contribution to the
      mini-list is dead code for all 176 prospects. Marketing copy beating
      evidence. Correct order: override > mandates > CSV.
- [x] **4. DONE.** Today/Prospects/Signals + System. Ten pages → six entries, Today · Prospects · Signals · Settings.
- [ ] **5. `ApolloSequencesAdapter` (187).** Largest adapter,
      `TODO(verify)` in three places, never run against a real account.
      Probe on day 1 of the Apollo trial, not day 12.
- [ ] **6. Prospector criteria → UI-editable.** Only `--limit` is exposed;
      changing niche means editing a command.
- [ ] **7. Mini-list's 60-role ceiling** — a crude proxy for company size.
      Replace with real scoring once `job_history.json` has weeks of
      `days_open` behind it.
- [ ] **8. Two cadences, one stop condition.** If Apollo owns email
      follow-ups and the app owns LinkedIn, email steps must not count
      toward `MAX_FOLLOWUPS`; both stop at `replied`/`won`/`dismissed`.

### Do NOT strip

- `sweep_core.py` + `job_history.json` — the only thing here a competitor
  cannot buy. 12 days deep, 1,144 closes, 31 reposts recorded.
- `signal_engine.py` + `watchlist.json` — the supply everything feeds on.
- `minilist_engine.py` — this *is* the product.
- `tls_compat.py` — 26 lines; without it every networked engine dies on
  this machine.
- `auth.py` — `start_app.bat` binds `0.0.0.0` for Tailscale. Real gate.
- The three test files — no pytest, no linter; they're the only safety net.

**Net effect:** ~1,300 lines deleted or hidden, `common.py` halved,
sidebar from ten entries to four.

## Timebox

**Three days of work, spread over no more than two weeks.**

The revamp is justified — a cluttered daily tool does get used less. But
it is also, honestly, another reason not to send. The target isn't a
cleaner app; the target is 3 clients in 90 days, and the app has produced
zero sends so far.

So: **Step 0 and Step 2 are the only ones that change whether you send
today.** If time gets tight, do those two and leave the rest. A working
Today page on top of a cluttered sidebar beats a beautiful four-page app
you finish in September.

**The metric for this revamp is not "app feels cleaner." It's "messages
sent per day went up."** If it doesn't move that number, it failed.
