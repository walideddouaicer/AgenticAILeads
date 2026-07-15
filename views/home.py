"""Home — today's numbers, pipeline funnel, billable summary, setup
checklist and the engine run buttons."""

from datetime import date

import streamlit as st

import common as C

C.page_header("🏠 Home", "home")

leads, src_name = C.load_leads()
status = C.load_status()
today = date.today().isoformat()

# ---------------- today's metrics ----------------

new_today = sum(1 for r in leads if r.get("date") == today)
stage_of = {C.lead_key(r): C.effective_status(status.get(C.lead_key(r)))
            for r in leads}
n_new = sum(1 for r in leads if stage_of[C.lead_key(r)] == "new")
n_due = sum(1 for r in leads if C.followup_due(status.get(C.lead_key(r))))
n_replied = sum(1 for r in leads if stage_of[C.lead_key(r)] == "replied")

m1, m2, m3, m4 = st.columns(4)
m1.metric("New signals today", new_today,
          help="Rows appended to signals.csv with today's date.")
m2.metric("Queue size", n_new,
          help="Leads in the 'new' stage — what the Outreach Queue draws "
               "from (capped per day there).")
m3.metric("Follow-ups due", n_due,
          help=f"Contacted ≥{C.FOLLOWUP_DAYS} days ago, fewer than "
               f"{C.MAX_FOLLOWUPS} follow-ups sent. Do these first.")
m4.metric("Replies to handle", n_replied,
          help="Leads currently in the 'replied' stage — answer within "
               "2 hours during US/EU business hours.")

# ---------------- run engines ----------------

st.subheader("Run engines")
C.render_engine_panel()
e1, e2, e3 = st.columns(3)
with e1:
    C.engine_button("sweep", key="home_sweep",
                    help_text="Sweeps every watched board and appends new "
                              "roles to signals.csv. The very first run "
                              "records a baseline instead (no signals).")
    st.caption("Nightly job-board sweep → new signals")
with e2:
    C.engine_button("enrich", key="home_enrich",
                    help_text="Looks up recruiting/TA decision-makers for "
                              "signal companies via Apollo. Needs "
                              "APOLLO_API_KEY in .env. Companies are "
                              "cached — re-runs only query new ones.")
    st.caption("Find decision-maker contacts (Apollo)")
with e3:
    hn_n = int(st.number_input("HN threads (--hn N)", 1, 12, 3,
                               key="home_hn_n",
                               help="How many monthly 'Who is hiring' "
                                    "threads to harvest for new boards."))
    C.engine_button("discover", key="home_discover", extra_args=["--hn", hn_n],
                    help_text="Harvests job-board URLs from Hacker News "
                              "'Who is hiring' threads, verifies them and "
                              "adds them to the watchlist.")
    st.caption("Grow the watchlist from HN threads")

# ---------------- funnel ----------------

st.subheader("Pipeline funnel")
if not leads:
    st.info("**No leads yet, so no funnel.** Run the sweep above twice "
            "(first run = baseline) — or check the setup checklist below.")
else:
    won = sum(1 for s in stage_of.values() if s == "won")
    replied_plus = won + n_replied
    contacted_plus = replied_plus + sum(
        1 for s in stage_of.values() if s == "contacted")
    dismissed = sum(1 for s in stage_of.values() if s == "dismissed")
    steps = [
        ("all leads", len(leads), C.STAGE_COLORS["new"]),
        ("contacted+", contacted_plus, C.STAGE_COLORS["contacted"]),
        ("replied+", replied_plus, C.STAGE_COLORS["replied"]),
        ("won", won, C.STAGE_COLORS["won"]),
    ]
    top = max(len(leads), 1)
    rows = []
    for label, n, color in steps:
        pct = 100 * n / top
        rows.append(
            f'<div class="aal-funnel-row">'
            f'<div class="aal-funnel-label">{label}</div>'
            f'<div class="aal-funnel-track">'
            f'<div class="aal-funnel-bar" style="width:{pct:.1f}%;'
            f'background:{color}"></div></div>'
            f'<div class="aal-funnel-count">{n}</div></div>')
    st.markdown("".join(rows), unsafe_allow_html=True)
    reply_rate = (100 * replied_plus / contacted_plus) if contacted_plus else 0
    st.caption(f"{dismissed} dismissed (not shown) · reply rate "
               f"{reply_rate:.0f}% of contacted — target ≥8%, hooks push "
               "15–25%.")

# ---------------- billable summary ----------------

st.subheader("Billable — won meetings × meeting rate")
won_by_client = {}
for r in leads:
    if stage_of[C.lead_key(r)] == "won":
        won_by_client[r.get("client", "default")] = \
            won_by_client.get(r.get("client", "default"), 0) + 1
clients = C.all_clients(leads)
if not clients:
    st.info("No clients yet — create one in **System → Client Configs**.")
else:
    bill_rows, total = [], 0
    for cl in clients:
        has_cfg = (C.CONFIG_DIR / f"{cl}.json").exists()
        rate = int(C.load_profile(cl).get("meeting_rate",
                                          C.DEFAULT_MEETING_RATE)) \
            if has_cfg else C.DEFAULT_MEETING_RATE
        won = won_by_client.get(cl, 0)
        total += won * rate
        bill_rows.append({"client": cl, "won meetings": won,
                          "rate": f"${rate}", "billable": f"${won * rate}"})
    b1, b2 = st.columns([3, 1], vertical_alignment="center")
    with b1:
        st.dataframe(bill_rows, width="stretch",
                     height=min(40 + 36 * len(bill_rows), 260))
    with b2:
        st.metric("Total billable", f"${total}",
                  help="Sum of won meetings × each client's rate. "
                       "First meeting per client is free — adjust manually "
                       "when invoicing.")

# ---------------- first-run checklist ----------------

st.subheader("Setup checklist")


def _env_has_apollo() -> bool:
    import os
    if C.ENV_FILE.exists():
        for line in C.ENV_FILE.read_text(encoding="utf-8",
                                         errors="replace").splitlines():
            if line.strip().startswith("APOLLO_API_KEY"):
                val = line.split("=", 1)[1].strip().strip('"').strip("'") \
                    if "=" in line else ""
                if val:
                    return True
    return bool(os.environ.get("APOLLO_API_KEY"))


wl_total = sum(len(v) for v in C.load_watchlist().values())
checks = [
    (wl_total > 0,
     f"Watchlist has boards ({wl_total} watched)",
     "Open **System → Watchlist** and paste a job-board URL, or run "
     "discovery above (`python discovery_engine.py --hn 3`)."),
    (bool(C.list_profiles()),
     "At least one client config exists",
     "Open **System → Client Configs** and save a profile (start with "
     "client ID `default` for your own prospecting)."),
    (C.STATE_FILE.exists(),
     "Baseline recorded (signals_state.json exists)",
     "Click **Run Nightly sweep** above or run "
     "`python signal_engine.py --baseline` — the first sweep records the "
     "baseline; every later sweep reports the differences as signals."),
    (_env_has_apollo(),
     ".env contains APOLLO_API_KEY (enrichment ready)",
     "Create a file named `.env` next to app.py containing "
     "`APOLLO_API_KEY=your_key` (Apollo → Settings → Integrations → API — "
     "enable *master key*). Then verify with "
     "`python enrichment_engine.py --probe gitlab`."),
]
n_ok = sum(1 for ok, *_ in checks if ok)
if n_ok == len(checks):
    st.success("All set — the machine is fully configured. ✅")
else:
    st.caption(f"{n_ok} of {len(checks)} done — each unmet item says "
               "exactly how to fix it.")
for ok, title, fix in checks:
    if ok:
        st.markdown(f"✅ ~~{title}~~")
    else:
        st.warning(f"⬜ **{title}**\n\n{fix}")
