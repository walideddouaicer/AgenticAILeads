"""Watchlist — the job boards swept nightly."""

import html as _html
import re

import streamlit as st

import common as C

C.page_header("📡 Watchlist", "watchlist")

wl = C.load_watchlist()
counts = C.tracked_job_counts()
total = sum(len(v) for v in wl.values())

st.markdown(f"### {total} companies watched")
st.caption("Every company here gets swept nightly. Paste any job-board URL "
           "below — platform and token are detected automatically. "
           "Newly added companies are baselined silently on their first "
           "sweep (no false 'new job' flood).")

if total == 0:
    st.info("**The watchlist is empty — nothing gets swept.** Three ways "
            "to fill it:\n"
            "1. Paste a job-board URL in the box below\n"
            "2. Bulk-import: paste any text containing board links\n"
            "3. Run discovery below (harvests HN 'Who is hiring' threads)")

# --- discovery engine, right where the boards are managed ---
C.render_engine_panel()
with st.expander("🌱 Grow the watchlist automatically (discovery engine)"):
    dc1, dc2 = st.columns([1, 2])
    with dc1:
        hn_n = int(st.number_input(
            "HN threads (--hn N)", 1, 12, 3, key="wl_hn_n",
            help="How many monthly Hacker News 'Who is hiring' threads to "
                 "harvest. Each thread has hundreds of companies; dead "
                 "candidates are cached 90 days so re-runs stay fast."))
    with dc2:
        st.caption("Runs `python discovery_engine.py --hn N`: extracts every "
                   "Greenhouse/Lever/Ashby/Workable board URL from the "
                   "threads, verifies each is live, and adds it here. "
                   "New boards are baselined silently on the next sweep.")
    C.engine_button("discover", key="wl_run_discover",
                    extra_args=["--hn", hn_n])

url_in = st.text_input(
    "Paste a job-board URL",
    placeholder="https://boards.greenhouse.io/acme  ·  https://jobs.lever.co/acme  ·  https://jobs.ashbyhq.com/acme  ·  https://apply.workable.com/acme",
    key="wl_url",
    help="Any Greenhouse / Lever / Ashby / Workable URL — careers pages, "
         "API endpoints and embed URLs all work.")
if url_in.strip():
    platform, token = C.parse_board_url(url_in)
    if platform:
        already = token in wl[platform]
        if already:
            st.info(f"**{token}** ({platform}) is already on the watchlist.")
        else:
            st.success(f"Detected: **{token}** on **{platform}**")
            if st.button(f"➕ Add {token} to watchlist", type="primary",
                         key="wl_add",
                         help="Adds the board; it will be baselined on the "
                              "next sweep."):
                C.update_watchlist(add=[(platform, token)])
                C.push_toast(f"Added {token} ({platform})", "📡")
                st.rerun()
    else:
        st.error("Couldn't detect a Greenhouse / Lever / Ashby / Workable "
                 "board in that URL. Add manually below.")

with st.expander("Bulk import — paste anything containing board URLs "
                 "(page source, HN thread, newsletter)"):
    blob = st.text_area("Paste text here", height=140, key="wl_bulk",
                        placeholder="Any text with greenhouse/lever/ashby/"
                                    "workable links — all are detected.",
                        help="The whole blob is scanned for board URLs; "
                             "duplicates are ignored.")
    if blob.strip():
        bulk_found = set()
        for _platform, _pat in C.ATS_URL_PATTERNS:
            for m in re.finditer(_pat, _html.unescape(blob), re.IGNORECASE):
                t = m.group(1).lower()
                if t not in ("api", "v1", "embed", "widget"):
                    bulk_found.add((_platform, t))
        bulk_new = sorted(x for x in bulk_found if x[1] not in wl[x[0]])
        st.caption(f"**{len(bulk_found)}** board(s) detected — "
                   f"**{len(bulk_new)}** not yet watched")
        if bulk_new:
            st.write(", ".join(f"`{t}` ({p})" for p, t in bulk_new))
            if st.button(f"➕ Add all {len(bulk_new)}", type="primary",
                         key="wl_bulk_add",
                         help="Adds every detected board that isn't already "
                              "watched."):
                C.update_watchlist(add=bulk_new)
                C.push_toast(f"Added {len(bulk_new)} board(s)", "📡")
                st.rerun()

with st.expander("Add manually (platform + token)"):
    mc1, mc2, mc3 = st.columns([1, 2, 1])
    with mc1:
        m_platform = st.selectbox("Platform", C.PLATFORMS, key="wl_mplat",
                                  help="The ATS the company uses.")
    with mc2:
        m_token = st.text_input("Board token (e.g. 'acme')", key="wl_mtok",
                                help="The company slug from the board URL, "
                                     "e.g. 'acme' in boards.greenhouse.io/acme.")
    with mc3:
        st.write("")
        st.write("")
        if st.button("Add", key="wl_madd",
                     help="Adds platform:token to the watchlist.") \
                and m_token.strip():
            C.update_watchlist(
                add=[(m_platform, m_token.strip().lower())])
            C.push_toast(f"Added {m_token.strip().lower()} ({m_platform})", "📡")
            st.rerun()

st.divider()

# --- search + bulk remove + per-platform expanders ---
sc1, sc2 = st.columns([1, 2])
with sc1:
    wl_query = st.text_input("🔍 Search boards", key="wl_search",
                             help="Filters the board lists below by token "
                                  "substring, across all platforms.")
q = wl_query.strip().lower()

all_board_ids = [f"{p}:{t}" for p in C.PLATFORMS for t in wl[p]]
with sc2:
    rm_sel = st.multiselect("Bulk remove", all_board_ids, key="wl_bulk_rm",
                            help="Pick any number of boards, then click "
                                 "Remove selected. Their tracked jobs stay "
                                 "in signals_state.json until the next sweep.")
    if rm_sel and st.button(f"🗑 Remove {len(rm_sel)} selected board(s)",
                            key="wl_bulk_rm_btn",
                            help="Removes them from the nightly sweep. "
                                 "Re-adding later re-baselines silently."):
        C.update_watchlist(
            remove=[(bid.partition(":")[0], bid.partition(":")[2])
                    for bid in rm_sel])
        C.push_toast(f"Removed {len(rm_sel)} board(s)", "🗑")
        st.rerun()

for platform in C.PLATFORMS:
    tokens = [t for t in wl[platform] if q in t] if q else wl[platform]
    label = f"{platform.capitalize()} — {len(tokens)}"
    if q:
        label += f" match(es) of {len(wl[platform])}"
    else:
        label += " board(s)"
    with st.expander(label, expanded=bool(q and tokens)):
        if not wl[platform]:
            st.caption("none yet — paste a URL above or run discovery")
            continue
        if not tokens:
            st.caption("no boards match the search")
            continue
        for i, token in enumerate(tokens):
            k = f"{platform}:{token}"
            n = counts.get(k)
            rc1, rc2, rc3 = st.columns([3, 2, 1])
            rc1.text(token)
            rc2.caption(f"{n} live jobs tracked" if n is not None
                        else "not swept yet — appears after next run")
            if rc3.button("remove", key=f"wl_rm_{platform}_{i}",
                          help=f"Stop sweeping {token} ({platform})."):
                C.update_watchlist(remove=[(platform, token)])
                C.push_toast(f"Removed {token} ({platform})", "🗑")
                st.rerun()
