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
import core
from evidence import load_refreshes, verified_at
from storage import atomic_write_text, read_json, update_json
import sweep_core

C.page_header("📡 Signals", "signals")

st.subheader("Refresh tracked boards")
r1, r2 = st.columns(2)
with r1:
    C.engine_button("sweep", key="sig_run_sweep",
                    label="Refresh employer boards", type="primary",
                    help_text="Checks tracked employer boards and records "
                              "their current roles and refresh results.")
with r2:
    C.engine_button("agency_sweep", key="sig_run_agency_sweep",
                    label="Refresh agency boards",
                    help_text="Checks tracked recruitment-agency boards "
                              "and records advertised roles.")
with st.expander("Refresh progress and technical details"):
    C.render_engine_panel()
with st.expander("Optional contact lookup"):
    st.caption("Requires a configured provider and may use credits. "
               "Only recently checked, confirmed sample companies are looked up.")
    C.engine_button("enrich", key="sig_run_enrich",
                    extra_args=["--from-minilists", "--limit", 25],
                    label="Look up sample contacts")

wl = core.load_watchlist()
counts = core.tracked_job_counts()
refreshes = load_refreshes(core.BOARD_REFRESH_FILE)
total_boards = sum(len(v) for v in wl.values())
agency_watch = read_json(core.AGENCY_WATCHLIST_FILE, default={},
                         expected_type=dict)
agency_total = sum(len(tokens) for tokens in agency_watch.values())
tab_boards, tab_agencies, tab_jobs = st.tabs(
    [f"Employer boards ({total_boards})",
     f"Agency boards ({agency_total})", "Jobs & history"])

# ------------------------------------------------------------- boards
with tab_boards:
    if not total_boards:
        st.info("No employer boards are tracked. Add a board URL below, "
                "then refresh it to establish a baseline.")
    else:
        cols = st.columns(len(core.PLATFORMS))
        for col, p in zip(cols, core.PLATFORMS):
            live = sum(counts.get(f"{p}:{t}", 0) for t in wl.get(p, []))
            col.metric(p.title(), len(wl.get(p, [])), f"{live} stored roles")

    st.markdown("**Add a board**")
    url = st.text_input(
        "Paste any job-board URL", key="sig_add_url",
        placeholder="https://boards.greenhouse.io/gitlab",
        help="Greenhouse, Lever, Ashby or Workable. The platform and token "
             "are parsed out of the URL — you don't need to know them.")
    if st.button("Add to watchlist", key="sig_add_btn",
                 disabled=not url.strip()):
        platform, token = core.parse_board_url(url)
        if not platform:
            st.error("Couldn't parse that URL. It must be a Greenhouse, "
                     "Lever, Ashby or Workable board.")
        elif token in wl.get(platform, []):
            st.warning(f"`{platform}:{token}` is already tracked.")
        else:
            core.update_watchlist(add=[(platform, token)])
            C.push_toast(f"Added {platform}:{token}", "📡")
            st.rerun()

    st.caption("New boards are baselined silently on the next sweep — they "
               "won't flood the queue with fake 'new' signals.")

    # ---- bulk add: paste names, the engine finds the boards ----
    st.markdown("**Add many at once**")
    st.caption(
        "Paste company names, one per line. Discovery checks the supported "
        "job-board platforms and adds boards it can verify.")
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
        names_file = core.DATA_DIR / "_bulk_names.txt"
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
                 "stored_roles": counts.get(f"{p}:{t}", 0),
                 "check_status": refreshes.get(f"{p}:{t}", {}).get(
                     "status", "never checked"),
                 "last_success": refreshes.get(f"{p}:{t}", {}).get(
                     "last_success", "") or "",
                 "current_evidence": bool(verified_at(f"{p}:{t}", refreshes))}
                for p in core.PLATFORMS for t in wl.get(p, [])]
        if q.strip():
            ql = q.strip().lower()
            rows = [r for r in rows
                    if ql in r["token"] or ql in r["platform"]]
        rows.sort(key=lambda r: -r["stored_roles"])
        st.dataframe(rows, width="stretch", hide_index=True)
        failed = sum(1 for row in rows if row["check_status"] == "failed")
        if failed:
            st.warning(f"{failed} board check(s) failed most recently. "
                       "Stored roles remain visible but require refresh "
                       "before current-hiring claims.")

        with st.expander("Confirm employer board identity"):
            st.caption("Confirm the company behind a tracked board. A "
                       "contact-cache key is optional; it connects existing "
                       "contacts only after you verify the match.")
            boards = sorted(f"{platform}:{token}"
                            for platform, tokens in wl.items()
                            for token in tokens)
            selected = st.selectbox("Employer board", boards,
                                    key="company_board_select")
            mapping = read_json(core.COMPANY_BOARD_MAPPINGS, default={},
                                expected_type=dict).get(selected, {})
            company_name = st.text_input(
                "Confirmed company name", mapping.get("company", ""),
                key=f"company_name_{selected}",
                help="Leave empty and save to remove an incorrect mapping.")
            contact_key = st.text_input(
                "Existing contact-cache key (optional)",
                mapping.get("contacts_key", ""),
                key=f"contact_key_{selected}",
                help="Use only after checking the cached people belong to "
                     "this company. Manual board-specific contacts need no key.")
            domain = st.text_input(
                "Confirmed company domain (optional)",
                mapping.get("domain", ""), key=f"domain_{selected}",
                help="Boards with the same confirmed domain may be grouped "
                     "as one company in samples.")
            if st.button("Save company identity", key="company_board_save"):
                try:
                    core.set_company_board(selected, company_name,
                                        contact_key, domain)
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.success("Company mapping saved.")
                    st.rerun()

        drop = st.multiselect(
            "Remove boards", [f"{r['platform']}:{r['token']}" for r in rows],
            key="sig_drop",
            help="Removing a board stops future sweeps. Its recorded history "
                 "is kept — deleting boards never deletes history.")
        if drop and st.button("Remove selected", key="sig_drop_btn"):
            core.update_watchlist(
                remove=[(item.partition(":")[0], item.partition(":")[2])
                        for item in drop])
            C.push_toast(f"Removed {len(drop)} board(s)", "🗑")
            st.rerun()

# --------------------------------------------- agency boards
with tab_agencies:
    checks = load_refreshes(core.AGENCY_REFRESH_FILE)
    board_keys = sorted(f"{platform}:{token}"
                        for platform, tokens in agency_watch.items()
                        for token in tokens)
    if not board_keys:
        st.info("No agency boards are tracked. Add one below or discover "
                "boards from the prospect agencies.")
    else:
        st.dataframe([{
            "board": board,
            "latest_check": checks.get(board, {}).get("status", "never checked"),
            "last_success": checks.get(board, {}).get("last_success") or "unknown",
            "can_support_current_copy": bool(verified_at(board, checks)),
        } for board in board_keys], hide_index=True, width="stretch")
        failed = [board for board in board_keys
                  if checks.get(board, {}).get("status") == "failed"]
        if failed:
            st.warning(f"{len(failed)} agency board check(s) failed. "
                       "Earlier postings remain visible but need a successful "
                       "refresh before current-hiring copy.")

    with st.expander("Add or discover agency boards"):
        url = st.text_input("Agency board URL", key="agency_board_url")
        if st.button("Track agency board", key="agency_board_add"):
            platform, token = core.parse_board_url(url)
            if not platform:
                st.error("Use a Greenhouse, Lever, Ashby or Workable board URL.")
            else:
                def add_board(current):
                    tokens = current.setdefault(platform, [])
                    if token not in tokens:
                        tokens.append(token)
                        tokens.sort()
                update_json(core.AGENCY_WATCHLIST_FILE, add_board,
                            default={}, expected_type=dict)
                st.success("Board tracked. Refresh it and confirm which "
                           "agency owns it before using its postings.")
                st.rerun()
        C.engine_button("find_mandates", key="sig_discover_agency",
                        extra_args=["--from-prospects", "--and-sweep"],
                        label="Discover boards from prospects",
                        help_text="Searches public boards for the agencies "
                                  "in Prospects. Review matches before use.")

    with st.expander("Confirm which agency owns a board"):
        prospects = core.load_prospects()
        agency_names = sorted({p.get("agency", "").strip() for p in prospects
                               if p.get("agency", "").strip()})
        mappings = read_json(core.AGENCY_BOARD_MAPPINGS, default={},
                             expected_type=dict)
        suggestions = read_json(core.DATA_DIR / "agency_board_suggestions.json",
                                default={}, expected_type=dict)
        if agency_names and board_keys:
            selected_agency = st.selectbox("Prospect agency", agency_names,
                                           key="confirm_agency_name")
            agency_id = selected_agency.casefold()
            st.caption("Suggested boards: " +
                       (", ".join(suggestions.get(agency_id, [])) or "none"))
            selected_boards = st.multiselect(
                "Confirmed boards", board_keys,
                default=mappings.get(agency_id, []),
                key=f"agency_boards_{agency_id}")
            for board in selected_boards:
                check = checks.get(board, {})
                st.caption(f"{board}: latest check "
                           f"{check.get('status', 'never checked')}; "
                           f"last success {check.get('last_success') or 'unknown'}")
            if st.button("Save confirmed boards", key="save_agency_boards"):
                core.set_agency_boards(selected_agency, selected_boards)
                st.success("Agency board identity saved.")
                st.rerun()
        else:
            st.info("Add a prospect and a tracked agency board first.")

# --------------------------------------------- jobs (with their history)
# Live jobs and history were two tabs showing the same thing from two
# angles: the table already carries days_open and repost_count joined from
# job_history.json, so a separate "History" tab was just the summary of the
# column next to it. One tab: the numbers on top, the rows underneath.
with tab_jobs:
    # No st.stop() anywhere in this block. st.stop() halts the whole
    # script, not the tab — inside a tab it would kill any tab rendered
    # after it. Empty states branch instead.
    jobs = core.all_tracked_jobs()
    hist_path = core.DATA_DIR / "job_history.json"
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
            "board": j.get("board_key", ""),
            "verified_at": (j.get("observed_at", "")
                            if j.get("observed_at") == verified_at(
                                j.get("board_key", ""), refreshes) else ""),
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

    st.caption(f"{len(rows):,} stored roles across {len(counts)} board(s); "
               f"{sum(1 for r in rows if r['verified_at'])} have a recent "
               "successful board check. `days_open` and "
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
