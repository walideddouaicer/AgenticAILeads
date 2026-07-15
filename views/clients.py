"""Client Pages — per-client leads, metrics, billable total, CSV report."""

from datetime import date, timedelta

import streamlit as st

import common as C

C.page_header("👤 Client Pages", "clients")

leads, src_name = C.load_leads()
status = C.load_status()
clients = C.all_clients(leads)

if not clients:
    st.info("**No clients yet.** Create the first profile in "
            "**System → Client Configs** (a `default` own-prospecting "
            "profile is a good start), then leads matched to it will "
            "appear here after the next sweep.")
    st.stop()

sel = st.selectbox("Open client page", clients, key="cl_select",
                   help="Every client with a config file or with leads "
                        "assigned in signals.csv.")
client_leads = [r for r in leads if r.get("client") == sel]

# --- client header ---
has_cfg = (C.CONFIG_DIR / f"{sel}.json").exists()
cfg = C.load_profile(sel) if has_cfg else {}
st.markdown(f"## {cfg.get('label') or sel}")
info_bits = [f"`{sel}`"]
if has_cfg:
    info_bits.append("🟢 active" if cfg.get("active") else "⚪ paused")
    info_bits.append(f"${cfg.get('meeting_rate', C.DEFAULT_MEETING_RATE)}/meeting")
    if cfg.get("inclusion_keywords"):
        info_bits.append("keywords: " + ", ".join(cfg["inclusion_keywords"][:6])
                         + ("…" if len(cfg["inclusion_keywords"]) > 6 else ""))
else:
    info_bits.append("(no config file — data only)")
st.caption(" · ".join(info_bits))

if not client_leads:
    st.info("**No leads assigned to this client yet.** They appear here "
            "after the nightly sweep matches new jobs to this client's "
            "config — or use **Backfill** in System → Client Configs to "
            "seed today's live matches immediately.")
    st.stop()

# --- client-page filters ---
f1, f2, f3 = st.columns([1, 2, 1])
with f1:
    cf_stage = st.selectbox(
        "Stage", ["all", "new", "follow-up due", "contacted",
                  "replied", "won", "dismissed"], key="cl_stage_v2",
        help="Filter the table by outreach stage.")
with f2:
    cf_search = st.text_input("Search (title/company/location/notes)",
                              key="cl_search",
                              help="Case-insensitive substring match across "
                                   "title, company, location and your notes.")
with f3:
    cf_days = st.selectbox("Period", ["all time", "7 days", "30 days", "90 days"],
                           key="cl_days",
                           help="Only leads whose signal date falls in this "
                                "window.")

cutoff = None
if cf_days != "all time":
    cutoff = (date.today() - timedelta(days=int(cf_days.split()[0]))).isoformat()

table = []
for r in client_leads:
    if cutoff and (r.get("date", "") or "") < cutoff:
        continue
    entry = status.get(C.lead_key(r))
    eff = C.effective_status(entry)
    due = C.followup_due(entry)
    if cf_stage == "follow-up due" and not due:
        continue
    if cf_stage not in ("all", "follow-up due") and eff != cf_stage:
        continue
    notes = (entry or {}).get("notes", "")
    if cf_search:
        blob = " ".join([r.get("title", ""), r.get("company", ""),
                         r.get("location", ""), notes]).lower()
        if cf_search.lower() not in blob:
            continue
    table.append({
        "date": r.get("date", ""),
        "company": r.get("company", ""),
        "title": r.get("title", ""),
        "location": r.get("location", ""),
        "stage": eff + (" · follow-up due" if due else ""),
        "since": (entry or {}).get("on", ""),
        "notes": notes,
        "contact": r.get("contact1_name", ""),
        "url": r.get("url", ""),
    })


def n_stage(*stages):
    return sum(1 for t in table if t["stage"].split()[0] in stages)


rate = int(cfg.get("meeting_rate", C.DEFAULT_MEETING_RATE)) if has_cfg \
    else C.DEFAULT_MEETING_RATE
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Leads", len(table))
m2.metric("Contacted+", n_stage("contacted", "replied", "won"),
          help="Leads that got at least one touch (contacted, replied or won).")
m3.metric("Replied", n_stage("replied"))
m4.metric("Won", n_stage("won"), help="Meetings that showed.")
m5.metric("Billable", f"${n_stage('won') * rate}",
          help=f"won meetings × ${rate}/meeting for this client.")

st.caption("Click any column header to sort.")
st.dataframe(table, width="stretch", height=420,
             column_config={"url": st.column_config.LinkColumn("url")})

if table:
    out = "\n".join([",".join(table[0].keys())] + [
        ",".join(f'"{str(v).replace(chr(34), chr(39))}"' for v in t.values())
        for t in table])
    fname = f"report_{sel}_{date.today().isoformat()}.csv"
    st.download_button("⬇ Export this view as CSV (client report)", out,
                       file_name=fname, mime="text/csv",
                       help="Exactly the rows shown above, with your current "
                            "filters applied — ready to send to the client.")
