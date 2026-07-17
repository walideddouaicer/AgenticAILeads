"""Help — the full operating guide, generated from README.md, PLAYBOOK.md
and the code itself. The "?" popover on every page jumps here."""

import streamlit as st

import common as C

C.page_header("📖 Help", "help")

# the "?" popover on another page can jump straight to one section
_jump = st.session_state.pop("help_jump", None)


def section(key, title):
    return st.expander(title, expanded=(_jump == key))


# ------------------------------------------------------------------ (a)
with section("how", "⚙️ How the machine works"):
    st.markdown("""
**Agentic AI Leads** is pay-per-meeting lead generation for tech
recruitment/staffing agencies. The machine turns public hiring activity
into booked sales meetings:

1. **Watchlist** (`watchlist.json`) — company job boards on 4 ATS
   platforms: Greenhouse, Lever, Ashby, Workable.
2. **Nightly sweep** (`signal_engine.py`) — fetches every board's public
   API and **diffs** against the last run (`signals_state.json`). The
   very first run (or a newly added board) records a **baseline**
   silently — differences only become signals from the second run on.
3. **Signal** — each newly opened role, with a ready-made **hook**
   sentence ("saw you just opened a …"). Appended to `signals.csv`.
4. **Per-client filter** (`filter_engine.py` + `configs/*.json`) — each
   signal passes through every active client profile: exclusion
   keywords, inclusion keywords, allowed locations, then scoring
   (10 pts per matched inclusion keyword + keyword weight bonuses).
5. **Enrichment** (`enrichment_engine.py`) — Apollo.io lookup of
   recruiting/TA decision-makers at the signal company (name, title,
   LinkedIn; emails often locked on the free tier — LinkedIn outreach
   doesn't need them). Cached per company in `enriched_contacts.json`.
6. **Outreach Queue** — follow-ups due first, then top-ranked new sends
   (named contact > lead score > freshness), capped per day. You copy
   the pre-drafted DM, send it on LinkedIn, click **Sent**.
7. **Meeting** — replies are handled by a human (within 2 hours during
   US/EU business hours). A showed meeting = stage **won** = billable.
""")
    st.graphviz_chart("""
digraph pipeline {
  rankdir=LR;
  node [shape=box, style="rounded,filled", fillcolor="#F2F2EE",
        fontname="Space Grotesk"];
  watchlist   [label="Watchlist\\n(boards)"];
  sweep       [label="Nightly sweep\\nsignal_engine.py"];
  diff        [label="Diff vs\\nsignals_state.json"];
  signal      [label="Signal + hook\\nsignals.csv", fillcolor="#D8FF3E"];
  filter      [label="Per-client filter\\nconfigs/*.json"];
  enrich      [label="Enrichment\\nApollo contacts"];
  queue       [label="Outreach Queue\\nDM → follow-ups", fillcolor="#D8FF3E"];
  meeting     [label="Meeting\\n(won = billable)", fillcolor="#3CB371",
               fontcolor="white"];
  discovery   [label="Discovery\\ndiscovery_engine.py", fillcolor="#FCFCFB"];
  discovery -> watchlist [label="new boards"];
  watchlist -> sweep -> diff -> signal -> filter -> enrich -> queue -> meeting;
}
""")
    st.markdown("**Stage colors used everywhere in the app:**")
    st.markdown(" ".join(C.stage_badge(s) for s in
                         ["new", "contacted", "replied", "won", "dismissed"]),
                unsafe_allow_html=True)
    st.caption("new → contacted (DM sent) → replied → won (meeting showed); "
               "dismissed = out of the pipeline.")

# ------------------------------------------------------------------ (b)
with section("home", "🏠 Page guide — Home"):
    st.markdown(f"""
- **New signals today / Queue size / Follow-ups due / Replies to handle**
  — today's four working numbers. Queue size counts leads still in the
  `new` stage; follow-ups come due {C.FOLLOWUP_DAYS} days after the last
  touch (max {C.MAX_FOLLOWUPS} per lead).
- **Run engines** — the three engines as buttons; output streams live
  into the panel. Only one engine can run at a time (a lockfile guards
  even across windows; a *Clear stale engine lock* button appears if a
  run crashed).
  - *Nightly sweep* — `python signal_engine.py`; first ever run records
    the baseline instead of alerting.
  - *Enrichment* — `python enrichment_engine.py`; needs
    `APOLLO_API_KEY` in `.env`.
  - *Discovery* — `python discovery_engine.py --hn N`; the number input
    sets how many monthly HN "Who is hiring" threads to harvest.
- **Pipeline funnel** — all leads → contacted+ → replied+ → won
  (cumulative: each step includes the later ones). Reply-rate target is
  ≥8%; hook-based messages benchmark at 15–25%.
- **Billable** — per client: won meetings × that client's meeting rate.
- **Setup checklist** — the four first-run requirements; every unmet
  item states the exact command or page that fixes it.
""")

with section("queue", "🚀 Page guide — Outreach Queue"):
    st.markdown(f"""
Your daily working view. **Do follow-ups first, then new sends.**

- **Client** — work one client's queue or all.
- **Daily cap (new sends)** — max new leads queued today (default
  {C.DAILY_QUEUE_CAP}; LinkedIn-safe is ~15/day, 100–150 connection
  requests/week).
- **🎯 Focus mode** — one card at a time with a *send X of Y* progress
  bar; any action advances to the next card. **Skip →** cycles a card to
  the back without recording anything.
- **↩ Undo** — appears after any stage change; restores the lead's
  previous state.
- Each card shows the contact (or *⚠ no contact*), the job posting
  link, and the pre-rendered message:
  - new leads get the client's **DM template**;
  - follow-ups get **follow-up #1** or **#2 (breakup)** — after the
    breakup the lead leaves the queue for good.
- **✅ Sent — mark contacted** — click *after* you sent the DM; starts
  the follow-up timer. **🚫 Dismiss** — bad fit, out it goes.
  **🔁 Followed up** — logs the touch, resets the timer.
  **💬 They replied** — moves the conversation to the Inbox.
- **Double-DM guard** — if the same LinkedIn contact was already
  contacted for another client, the card shows a warning. Don't send.
- New sends are ranked: named contact first, then lead score, then
  freshness. Hooks older than {C.FRESH_DAYS} days automatically degrade
  from "just opened" to "currently hiring" wording.
""")

with section("inbox", "📥 Page guide — Daily Inbox"):
    st.markdown(f"""
Every signal ever captured, newest first, {25} per page.

- **Client / Show** — filter by client and stage ("follow-up due" is a
  virtual stage: contacted, timer expired).
- The colored badge on each row is the lead's stage; a second dark
  badge marks follow-ups due. 📝 in the title = the lead has notes.
- Inside a row: signal facts (date, location, hook), contact + alt
  contact when enriched, **Notes** (saved with the lead, searchable on
  the client page), the editable **Draft DM** with a copy box, and a
  one-click **Compose email** link when a real address exists.
- **Mark contacted / Dismiss** on new leads; **Stage** dropdown +
  **Update stage** afterwards; **Followed up (reset timer)** while
  contacted.
""")

with section("prospects", "🧲 Page guide — Prospects"):
    st.markdown(f"""
OUR pipeline — recruitment agencies we want as clients. Completely
separate from the lead pipeline: its own data files
(`prospects.csv`, `prospect_signals.csv`, `prospects_status.json`,
`agency_watchlist.json`), its own stages, no overlap with client
configs.

**The three engines** (buttons at the top of the page):
- **Prospector** — Apollo search for founders/MDs/owners at small
  (2–20 person) recruitment agencies in US/UK/FR. Appends to
  `prospects.csv`; never overwrites your edits; dedupes across runs.
  ⚠️ Run `python prospector_engine.py --probe` in a terminal ONCE
  before the first real run — it prints the exact payload and raw
  response so you can verify which filters your Apollo plan accepts.
- **Agency board discovery** — probes your prospects' agency names
  across Greenhouse/Lever/Ashby/Workable and saves boards classified
  as agencies (name markers like *staffing/recruit/talent*, or an
  "agency book" posting pattern) to `agency_watchlist.json`.
- **Mandate sweep** — diffs agency boards nightly-style; each new
  posting = "this agency just took a mandate" = the freshest possible
  hook ("saw you're working a {{title}} mandate…").

**Stages:** new → contacted → replied → **call booked** → **client
won** (+ dismissed), with the same day-{C.FOLLOWUP_DAYS} /
day-{C.FOLLOWUP_DAYS * 2} breakup follow-up timers as leads.
Follow-ups due always come first.

**Each card:** person + LinkedIn, agency, the best available hook
(fresh mandate > niche fallback > warning — no hook = don't send), a
DM drafted from the prospect templates (editable in the ✍️ expander),
and:

- **Niche keywords** — auto-derived from the agency's mandate titles;
  edit + Save to override (persisted per prospect).
- **🎁 One-click mini-list** — builds "10 companies hiring exactly
  what you place" from YOUR tracked jobs using those keywords, with
  CSV + branded HTML downloads right on the card. This is the
  door-opener: attach it, then "want 50 more per week?".
""")

with section("clients", "👤 Page guide — Client Pages"):
    st.markdown("""
- Pick a client to see their header (label, active/paused, meeting
  rate, keywords) and every lead assigned to them.
- **Stage / Search / Period** filter the table; search covers title,
  company, location *and your notes*.
- Metrics: Leads, Contacted+ (any touch), Replied, Won, and
  **Billable** = won × meeting rate.
- **⬇ Export this view as CSV** — the exact filtered table, ready to
  send as a client report.
""")

with section("minilist", "🎁 Page guide — Mini-List"):
    st.markdown("""
The $0 door-opener: an unsolicited gift list of companies hiring in a
*prospect agency's* niche, built from boards you already track.

- **Prospect agency name** — goes in the one-pager headline.
- **Niche keywords / market** — *their* placement niche, not yours.
- **List size** — 10 is the sweet spot.
- **Footer pitch line** — the CTA printed on the one-pager.
- Downloads: **CSV** (raw rows) and **branded one-pager (HTML)** — open
  it and Ctrl+P → PDF for a clean attachment.
- Companies with a named contact and multiple matching roles rank
  first. Send with: "Want 50 more of these per week?"
""")

with section("watchlist", "📡 Page guide — Watchlist"):
    st.markdown("""
The boards swept nightly. More boards = more signals.

- **Paste a job-board URL** — platform + token are auto-detected from
  any Greenhouse/Lever/Ashby/Workable URL, then one click adds it.
- **Bulk import** — paste *anything* (page source, HN thread,
  newsletter); every board URL in it is detected and added in one go.
- **Add manually** — pick the platform, type the token.
- **🌱 Discovery engine** — harvests HN "Who is hiring" threads
  (`--hn N`), verifies each board is live, adds it. Dead candidates are
  cached 90 days so weekly re-runs stay fast.
- **🔍 Search boards** — filters the lists below across all platforms.
- **Bulk remove** — multiselect any boards, one click removes them.
- Each board row shows its live-job count, or *"not swept yet"* until
  the next run. New boards are baselined silently — no false
  "new job" flood.
""")

with section("livejobs", "🗂 Page guide — Live Jobs"):
    st.markdown("""
The raw pool: every live job currently tracked in
`signals_state.json` — what the mini-list, the config dry-run and
backfill all draw from.

- **🔍 Search** — substring across company, title, location.
- **Platform** — multiselect; empty = all four.
- **Location contains** — e.g. `remote` or `united states`.
- **⬇ Export as CSV** — exactly the filtered rows.
""")

with section("configs", "⚙️ Page guide — Client Configs"):
    st.markdown("""
One JSON file per client in `configs/`; the sweep routes every new job
through every *active* profile.

- **Client ID** — the filename and the `client` tag on signals.
- **Gates** — exclusion keywords (title must contain none), inclusion
  keywords (title must contain ≥1; each match = 10 points), allowed
  locations (substring match both directions; `remote` also matches
  remote-flagged titles).
- **Outreach & billing** — DM, email, follow-up #1 and breakup
  templates (placeholders: `{first_name} {hook} {company} {title}
  {location} {client}`), and the per-meeting rate.
- **💾 Save profile** — writes the JSON; takes effect next sweep.
- **🗑 Delete profile** — type the exact client ID to enable the
  delete button (leads/history stay in the data files).
- **Test before you trust** — live single-job test with DM preview,
  plus a **dry run** over every tracked job showing exactly what the
  config would catch.
- **📥 Backfill** — appends today's live matches to `signals.csv` for
  a new client so they don't wait for future diffs; duplicates are
  skipped, hooks use "currently hiring" wording.
""")

# ------------------------------------------------------------------ (c)
with section("glossary", "📚 Glossary"):
    st.markdown(f"""
| Term | Meaning |
|---|---|
| **signal** | One newly opened role at a watched company — the reason to reach out *now*. A row in `signals.csv`. |
| **hook** | The specific, verifiable opener a DM starts with ("saw you just opened a …"). No hook = don't send. Hooks older than {C.FRESH_DAYS} days degrade to "currently hiring" wording automatically. |
| **board** | A company's public job listing on an ATS, e.g. `boards.greenhouse.io/acme`. |
| **token** | The company's slug in the board URL (`acme`). Watchlist entries are `platform:token`. |
| **ATS** | Applicant Tracking System. The four swept here: Greenhouse, Lever, Ashby, Workable — all with public JSON APIs. |
| **enrichment** | Looking up the decision-maker humans (recruiting/TA leads, founders) for a signal company via Apollo.io. |
| **baseline** | The first recorded snapshot of a board's jobs. Diffs against it produce signals; the baseline run itself never alerts. |
| **backfill** | Seeding a new client with today's *live* matches instead of waiting for future diffs (Client Configs → dry run → Backfill). |
| **mini-list** | The free "10 companies hiring in your niche" gift list for a prospect agency — the door-opener. |
| **breakup follow-up** | Follow-up #{C.MAX_FOLLOWUPS} — the last touch ("last note from me…"). After it, the lead stops appearing in the queue. |
| **prospect** | A recruitment agency (and its decision-maker) that WE want as a client. Lives in `prospects.csv`, worked on the Prospects page. |
| **mandate** | A role a recruitment agency is filling for ITS client — visible as a posting on the agency's own ATS board. A new mandate = a prospect signal = our hook. |
""")

# ------------------------------------------------------------------ (d)
with section("routine", "☀️ Daily routine"):
    st.markdown("Outreach hours are sacred — build the machine in the gaps. "
                "Reply to every prospect reply within 2 hours (US/EU "
                "business hours).")
    st.checkbox("Morning: run the nightly sweep (Home → Run engines) if the "
                "scheduler didn't", key="dr_1",
                help="This checklist is a memory aid — it resets every "
                     "session.")
    st.checkbox("Run enrichment so new companies have contacts", key="dr_2",
                help="Home → Run engines → Enrichment. Cached companies "
                     "are skipped, so it's cheap to run daily.")
    st.checkbox(f"Work every follow-up due (day-{C.FOLLOWUP_DAYS} and "
                "breakup) — most replies come from follow-ups", key="dr_3",
                help="Outreach Queue lists them first, pre-drafted.")
    st.checkbox("Then new sends, best-ranked first, up to the daily cap",
                key="dr_4",
                help="Focus mode on the Outreach Queue walks you through "
                     "them one by one.")
    st.checkbox("Log every touch (Sent / Followed up / They replied) — the "
                "timers depend on it", key="dr_5",
                help="Unlogged sends mean no follow-up reminders and a "
                     "broken double-DM guard.")
    st.checkbox("Check 'Replies to handle' is zero before stopping",
                key="dr_6",
                help="The Home page metric — every reply answered within "
                     "2 hours.")
    st.markdown("**Weekly:**")
    st.checkbox("Run discovery (--hn 3) to grow the watchlist", key="dr_w1",
                help="Watchlist page or Home → Run engines → Discovery.")
    st.checkbox("Send one mini-list to a prospect agency", key="dr_w2",
                help="Clients → Mini-List — the strongest $0 sales asset.")

# ------------------------------------------------------------------ (e)
with section("troubleshooting", "🛠 Troubleshooting"):
    st.markdown("""
**No signals appear after a sweep**
The first run only records the baseline — signals are *differences*
between runs. Run the sweep again tomorrow (or after boards actually
post new roles). Also check the watchlist isn't empty and at least one
client config is *active*.

**Apollo returns 403 on enrichment**
Your key is almost certainly a *scoped* key — the people-search
endpoint needs a **master API key**. Apollo → Settings → Integrations →
API → enable *master API key* (or create a new key with it enabled),
update `.env`, retry. If the message says the endpoint isn't on your
plan, fall back to manual Apollo UI searches until revenue funds the
upgrade. Verify with `python enrichment_engine.py --probe gitlab`.

**A board shows "board not found (check token)" / 404**
The token is wrong or the company moved ATS. Open the board URL in a
browser; if it's dead, remove it from the Watchlist. If the company
still hires, find their new board (Google
`site:boards.greenhouse.io <company>` etc.) and add that.

**A lead I expect isn't in the queue**
In order of likelihood: it was **dismissed** (Inbox → Show: dismissed);
the **breakup follow-up was already sent** (after follow-up #2 the lead
leaves the queue by design); it's **below the daily cap cutoff** (raise
the cap or check tomorrow); or it **didn't pass the client's filter**
(Client Configs → dry run shows exactly what the config catches).

**"Engine lock present" but nothing is running**
A previous run crashed or another window is mid-run. If you're sure
nothing is running, click *Clear stale engine lock* in the engine
panel.
""")
