"""Live Jobs — every job currently tracked in signals_state.json."""

import csv
import io
from datetime import date

import streamlit as st

import common as C

C.page_header("🗂 Live Jobs", "livejobs")

jobs = C.all_tracked_jobs()

if not jobs:
    st.info("**No tracked jobs yet.** This page shows the contents of "
            "`signals_state.json`, which the signal engine writes.")
    st.markdown("1. Add boards in **System → Watchlist** (if empty)\n"
                "2. Run the sweep below (or `python signal_engine.py`)\n"
                "3. Every live job on every watched board appears here")
    C.render_engine_panel()
    C.engine_button("sweep", key="lj_run_sweep")
    st.stop()

st.caption(f"**{len(jobs)} live jobs** across "
           f"{len(C.tracked_job_counts())} boards — this is the raw pool "
           "the mini-list, dry-run tester and backfill all draw from.")

f1, f2, f3 = st.columns([2, 1, 1])
with f1:
    q = st.text_input("🔍 Search", key="lj_search",
                      help="Case-insensitive substring match across "
                           "company, title and location.")
with f2:
    plats = st.multiselect("Platform", C.PLATFORMS, key="lj_platform",
                           help="Empty = all four ATS platforms.")
with f3:
    loc = st.text_input("Location contains", key="lj_loc",
                        help="Substring match on the job's location field, "
                             "e.g. 'remote' or 'united states'.")

ql, locl = q.strip().lower(), loc.strip().lower()
rows = []
for j in jobs:
    if plats and j.get("platform") not in plats:
        continue
    if locl and locl not in (j.get("location") or "").lower():
        continue
    if ql:
        blob = " ".join([j.get("company", ""), j.get("title", ""),
                         j.get("location", "")]).lower()
        if ql not in blob:
            continue
    rows.append({"company": j.get("company", ""),
                 "platform": j.get("platform", ""),
                 "title": j.get("title", ""),
                 "location": j.get("location", ""),
                 "link": j.get("url", "")})

rows.sort(key=lambda r: (r["company"], r["title"]))
st.caption(f"{len(rows)} of {len(jobs)} jobs match")

if not rows:
    st.warning("No jobs match these filters — clear the search box or "
               "widen the platform/location filters above.")
    st.stop()

st.dataframe(rows, width="stretch", height=480,
             column_config={"link": st.column_config.LinkColumn("link")})

buf = io.StringIO()
w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
w.writeheader()
w.writerows(rows)
st.download_button(
    f"⬇ Export {len(rows)} job(s) as CSV",
    buf.getvalue(),
    file_name=f"live_jobs_{date.today().isoformat()}.csv",
    mime="text/csv",
    help="Exports exactly the filtered rows shown above.")
