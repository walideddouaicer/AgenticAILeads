"""Home — a compact status board. Numbers, health, and one next action.

Deliberately small. The old Home was a Loop A dashboard (lead queue size,
replies to handle, billable meetings) for a pipeline that has no clients
in it, plus every engine button in the app. Both moved:

  - engines that produce jobs and contacts  -> Signals
  - lead metrics + billable summary         -> only shown when the client
                                               pages are on

What's left is what you'd actually want on opening the app: how the
prospect pipeline stands, whether the plumbing is healthy, and a link to
the work.
"""

import os
from datetime import date

import streamlit as st

import common as C

C.page_header("🏠 Home", "home")

SHOW_CLIENT_PAGES = os.environ.get("AAL_SHOW_CLIENT_PAGES") == "1"
today = date.today().isoformat()

# ---------------- prospect pipeline (the work that earns) ----------------

prospects = C.load_prospects()
pstatus = C.load_status(C.PROSPECTS_STATUS)
pstage = {C.prospect_key(p): C.effective_status(pstatus.get(C.prospect_key(p)))
          for p in prospects}
p_due = sum(1 for p in prospects
            if C.followup_due(pstatus.get(C.prospect_key(p))))
p_sent_today = sum(1 for e in pstatus.values()
                   if e.get("on") == today and e.get("status") != "new")

m = st.columns(4)
m[0].metric("Prospects", len(prospects),
            help="Rows in prospects.csv.")
m[1].metric("Not yet contacted",
            sum(1 for s in pstage.values() if s == "new"))
m[2].metric("Follow-ups due", p_due,
            help=f"Contacted ≥{C.FOLLOWUP_DAYS} days ago, under "
                 f"{C.MAX_FOLLOWUPS} follow-ups.")
m[3].metric("Actioned today", p_sent_today)

# ---------------- the one next action ----------------

today_file = C.DATA_DIR / "today.json"
if not prospects:
    st.warning("**No prospects yet.** Import a list or run the prospector "
               "from the Prospects page.")
elif not today_file.exists():
    st.info("**No queue built.** Run `python build_today.py --n 20`, then "
            "open Today.")
else:
    st.page_link("views/prospects.py",
                 label="Go to Prospects → Today — send the queue", icon="📮")

# ---------------- health (short, only complains when broken) -------------

problems = []
if not (C.DATA_DIR / "job_history.json").exists():
    problems.append("`job_history.json` missing — the sweep has never "
                    "completed. History cannot be backfilled.")
fail_log = C.DATA_DIR / "sweep_failures.log"
if fail_log.exists() and fail_log.stat().st_size:
    problems.append("`sweep_failures.log` has entries — the nightly sweep "
                    "is failing. Check `sweep.log`.")
if not C.CONTACTS_CACHE.exists():
    problems.append("`enriched_contacts.json` missing — every mini-list "
                    "says '0 with named contacts'. Run enrichment from "
                    "Signals once an API key is set.")
if not sum(len(v) for v in C.load_watchlist().values()):
    problems.append("Watchlist is empty — nothing to sweep. Add boards in "
                    "Signals.")

if problems:
    with st.expander(f"⚠️ {len(problems)} thing(s) need attention",
                     expanded=True):
        for p in problems:
            st.markdown(f"- {p}")
else:
    st.caption("✅ Plumbing healthy: history recording, watchlist populated, "
               "contacts cached.")

# ---------------- Loop A summary (only with clients) ----------------

if SHOW_CLIENT_PAGES:
    st.divider()
    st.subheader("Clients")
    leads, src_name = C.load_leads()
    lstatus = C.load_status()
    lstage = {C.lead_key(r): C.effective_status(lstatus.get(C.lead_key(r)))
              for r in leads}
    won = sum(1 for s in lstage.values() if s == "won")
    c = st.columns(4)
    c[0].metric("Leads", len(leads), help=f"from {src_name}")
    c[1].metric("Queue", sum(1 for s in lstage.values() if s == "new"))
    c[2].metric("Replies", sum(1 for s in lstage.values() if s == "replied"))
    c[3].metric("Won meetings", won)
    rate = C.DEFAULT_MEETING_RATE
    st.caption(f"Billable: {won} × {rate} = **{won * rate}** "
               "(default rate; per-client rates live in their config).")
