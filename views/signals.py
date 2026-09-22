"""Signals — the supply side: the boards we watch and the jobs on them.

Replaces the old Watchlist and Live Jobs pages. Two tabs:

  Boards          what we crawl — add one by URL, or paste a list of
                  company names and let discovery find their boards
  Jobs & history  what those boards currently hold, with what we've
                  learned by crawling them repeatedly

History is NOT a separate tab. days_open and repost_count are columns on
the job table, so a "History" tab was just the summary of the column next
to it. The summary now sits above the table it describes.

job_history.json is the one file here that cannot be regenerated, so its
depth is shown prominently — if the sweep stops, this is where you notice.
"""

import csv
import io
import json
from datetime import date

import streamlit as st

import common as C
from storage import atomic_write_text
import sweep_core

C.page_header("📡 Signals", "signals")

# Streams engine output and offers the stale-lock escape hatch.
C.render_engine_panel()

# The two engines that produce what this page shows. They live here rather
# than on Home because this is where you look at their output — you run the
# sweep to get jobs, and enrichment to get the contacts those jobs need.
with st.expander("⚙️ Run engines", expanded=False):
    r1, r2 = st.columns(2)
    with r1:
        C.engine_button("sweep", key="sig_run_sweep",
                        help_text="Fetches every tracked board, records "
                                  "history, appends new matches to "
                                  "signals.csv. Safe to run any time — "
                                  "recording is idempotent per day.")
        st.caption("Crawl boards → jobs + history")
    with r2:
        C.engine_button("enrich", key="sig_run_enrich",
                        extra_args=["--from-minilists", "--limit", 25],
                        help_text="Finds decision-maker contacts for the "
                                  "companies that actually appear in "
                                  "prospect mini-lists (not the whole of "
                                  "signals.csv). No-ops without an API key.")
        st.caption("Mini-list companies → contacts")
    st.caption("The nightly chain (`run_sweep.bat`) already runs both — "
               "these are for when you don't want to wait.")

wl = C.load_watchlist()
counts = C.tracked_job_counts()
total_boards = sum(len(v) for v in wl.values())
tab_boards, tab_jobs = st.tabs(
    [f"Boards ({total_boards})", "Jobs & history"])

# ------------------------------------------------------------- boards
with tab_boards:
    if not total_boards:
        st.info("**No boards tracked yet.** Add one below, or grow the list "
                "automatically with `python discovery_engine.py --hn 3`.")
    else:
        cols = st.columns(len(C.PLATFORMS))
        for col, p in zip(cols, C.PLATFORMS):
            live = sum(counts.get(f"{p}:{t}", 0) for t in wl.get(p, []))
            col.metric(p.title(), len(wl.get(p, [])), f"{live} live jobs")

    st.markdown("**Add a board**")
    url = st.text_input(
        "Paste any job-board URL", key="sig_add_url",
        placeholder="https://boards.greenhouse.io/gitlab",
        help="Greenhouse, Lever, Ashby or Workable. The platform and token "
             "are parsed out of the URL — you don't need to know them.")
    if st.button("Add to watchlist", key="sig_add_btn",
                 disabled=not url.strip()):
        platform, token = C.parse_board_url(url)
        if not platform:
            st.error("Couldn't parse that URL. It must be a Greenhouse, "
                     "Lever, Ashby or Workable board.")
        elif token in wl.get(platform, []):
            st.warning(f"`{platform}:{token}` is already tracked.")
        else:
            C.update_watchlist(add=[(platform, token)])
            C.push_toast(f"Added {platform}:{token}", "📡")
            st.rerun()

    st.caption("New boards are baselined silently on the next sweep — they "
               "won't flood the queue with fake 'new' signals.")

    # ---- bulk add: paste names, the engine finds the boards ----
    st.markdown("**Add many at once**")
    st.caption(
        "Paste company names — one per line. You don't need to know which "
        "ATS they use: the engine tries every slug variant against all four "
        "platforms and keeps only the ones with a live board. Ask an LLM "
        "for '80 mid-market fintech companies in the UK' and paste the "
        "answer straight in.")
    names = st.text_area(
        "Company names", key="sig_bulk_names", height=140,
        placeholder="Monzo\nStarling Bank\nGoCardless\nWise",
        help="Names, not URLs. 'Acme Corp' is tried as acme, acmecorp, "
             "acme-corp across Greenhouse, Lever, Ashby and Workable. "
             "Dead candidates are cached for 90 days so re-runs are fast.")
    parsed = [n.strip() for n in (names or "").splitlines()
              if n.strip() and not n.strip().startswith("#")]

    bc1, bc2 = st.columns([1, 2])
    with bc1:
        cap = int(st.number_input(
            "Probe cap", 50, 2000, 400, step=50, key="sig_bulk_cap",
            help="Max board fetches this run. Each name costs up to 8 "
                 "probes (4 platforms x 2 slug variants), so 80 names "
                 "needs roughly 640."))
    with bc2:
        st.caption(
            f"**{len(parsed)} name(s) pasted** — roughly {len(parsed) * 5} "
            "probes. Nothing is added until a live board is confirmed."
            if parsed else
            "Paste some names above and this button turns on.")
        # Always rendered, just disabled — a button that appears out of
        # nowhere once you type is a button people don't find.
        names_file = C.DATA_DIR / "_bulk_names.txt"
        busy = C.engine_running() or C.engine_busy()
        if st.button("🌱 Find their boards & add", key="sig_bulk_run",
                     type="primary", width="stretch",
                     disabled=busy or not parsed,
                     help="Runs discovery_engine.py --names on the list "
                          "above and streams the output at the top of this "
                          "page. Only one engine runs at a time."
                     if not busy else "An engine is already running."):
            atomic_write_text(names_file, "\n".join(parsed))
            if C.start_engine("discover",
                              ["--names", str(names_file), "--limit", cap]):
                st.rerun()

    if total_boards:
        q = st.text_input("Filter boards", key="sig_filter",
                          placeholder="token or platform")
        rows = [{"platform": p, "token": t,
                 "live_jobs": counts.get(f"{p}:{t}", 0)}
                for p in C.PLATFORMS for t in wl.get(p, [])]
        if q.strip():
            ql = q.strip().lower()
            rows = [r for r in rows
                    if ql in r["token"] or ql in r["platform"]]
        rows.sort(key=lambda r: -r["live_jobs"])
        st.dataframe(rows, width="stretch", hide_index=True)

        drop = st.multiselect(
            "Remove boards", [f"{r['platform']}:{r['token']}" for r in rows],
            key="sig_drop",
            help="Removing a board stops future sweeps. Its recorded history "
                 "is kept — deleting boards never deletes history.")
        if drop and st.button("Remove selected", key="sig_drop_btn"):
            C.update_watchlist(
                remove=[(item.partition(":")[0], item.partition(":")[2])
                        for item in drop])
            C.push_toast(f"Removed {len(drop)} board(s)", "🗑")
            st.rerun()

# --------------------------------------------- jobs (with their history)
# Live jobs and history were two tabs showing the same thing from two
# angles: the table already carries days_open and repost_count joined from
# job_history.json, so a separate "History" tab was just the summary of the
# column next to it. One tab: the numbers on top, the rows underneath.
with tab_jobs:
    # No st.stop() anywhere in this block. st.stop() halts the whole
    # script, not the tab — inside a tab it would kill any tab rendered
    # after it. Empty states branch instead.
    jobs = C.all_tracked_jobs()
    hist_path = C.DATA_DIR / "job_history.json"
    hist, hist_broken = {}, False
    if hist_path.exists():
        try:
            hist = json.loads(hist_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            hist_broken = True
            st.error("`job_history.json` is corrupt. Do NOT delete it — "
                     "restore the newest file from `backups/`. Jobs below "
                     "still render; days_open and reposts will be blank.")

    if hist:
        s = sweep_core.summarize(hist)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Days of history", s["days_of_history"],
                  help="Since the first completed sweep. Cannot be "
                       "backfilled — a day the sweep doesn't run is gone.")
        m2.metric("Jobs seen", f"{s['jobs_tracked']:,}")
        m3.metric("Closed", f"{s['closed']:,}",
                  help="Vanished from the board. Kept, because they're what "
                       "close_velocity is measured from.")
        m4.metric("Reposts", s["total_reposts"],
                  help="Pulled and relisted — the strongest single signal "
                       "that in-house recruiting is failing.")
        if s["days_of_history"] < 14:
            st.caption("⚠️ Under 14 days of history — `days_open` isn't "
                       "meaningful yet. Keep the sweep running daily.")
    elif not hist_broken:
        st.warning("**No history recorded yet.** Run the sweep above. "
                   "`job_history.json` is the one file here that can't be "
                   "rebuilt.")

    if not jobs:
        st.info("**No tracked jobs.** Add boards in the Boards tab, then "
                "run the sweep.")

    rows = []
    for j in jobs:
        e = (hist.get(j.get("board_key", "")) or {}).get(j.get("job_id", ""))
        f = sweep_core.job_features(e) if e else {}
        rows.append({
            "company": j.get("company", ""),
            "title": j.get("title", ""),
            "location": j.get("location", ""),
            "workplace": j.get("workplace", ""),
            "days_open": f.get("days_open", ""),
            "reposts": f.get("repost_count", ""),
            "platform": j.get("platform", ""),
            "url": j.get("url", ""),
        })

    # Older rows were stored before the fetchers learned to separate place
    # from arrangement. Show that honestly rather than pretending "Hybrid"
    # is a city — one sweep replaces them.
    stale_loc = sum(1 for r in rows if r["location"] and not r["workplace"]
                    and sweep_core.split_workplace(r["location"])[0] == "")
    if stale_loc:
        st.warning(
            f"**{stale_loc:,} job(s) below show 'Hybrid' / 'In-Office' in "
            "the location column and an empty `workplace`.** Those rows "
            "were captured before the fetchers learned to tell a place from "
            "a working arrangement — the data on disk is stale, the table "
            "is fine. **Run the sweep** (Run engines, above) and they'll "
            "show a real city.")

    st.caption(f"{len(rows):,} live jobs across {len(counts)} board(s) — the "
               "pool every mini-list is built from. `days_open` and "
               "`reposts` come from the history file.")

    f1, f2, f3 = st.columns([2, 1, 1])
    with f1:
        q = st.text_input("Search titles, companies, locations",
                          key="sig_job_q", label_visibility="collapsed",
                          placeholder="Search titles, companies, locations")
    with f2:
        min_days = int(st.number_input(
            "Open ≥ days", 0, 365, 0, key="sig_min_days",
            help="14+ is where in-house recruiting is visibly struggling — "
                 "the Warm Req idea. Needs history depth to be useful."))
    with f3:
        oldest = st.toggle("Longest open first", key="sig_job_sort")

    shown = rows
    if q.strip():
        ql = q.strip().lower()
        shown = [r for r in shown
                 if ql in r["title"].lower()
                 or ql in r["company"].lower()
                 or ql in r["location"].lower()]
    if min_days:
        shown = [r for r in shown
                 if isinstance(r["days_open"], int)
                 and r["days_open"] >= min_days]
    if oldest:
        shown = sorted(shown, key=lambda r: -(r["days_open"] or 0))

    if len(shown) != len(rows):
        st.caption(f"{len(shown):,} match")

    st.dataframe(
        shown[:500], width="stretch", hide_index=True,
        column_config={"url": st.column_config.LinkColumn(
            "url", display_text="open", width="small")})
    if len(shown) > 500:
        st.caption("Showing the first 500 — narrow the search or export.")

    buf = io.StringIO()
    if shown:
        w = csv.DictWriter(buf, fieldnames=list(shown[0].keys()),
                           lineterminator="\n")
        w.writeheader()
        w.writerows(shown)
    st.download_button("⬇ Export CSV", buf.getvalue(),
                       file_name=f"jobs_{date.today().isoformat()}.csv",
                       mime="text/csv", disabled=not shown)
