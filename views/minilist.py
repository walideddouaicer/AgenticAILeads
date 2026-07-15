"""Mini-List — the free 'companies hiring in your niche' door-opener."""

import re
from datetime import date

import streamlit as st

import common as C
from minilist_engine import build_minilist, render_csv, render_html, DEFAULT_PITCH

C.page_header("🎁 Mini-List", "minilist")

st.caption("Unsolicited gift for a prospect agency: companies hiring in "
           "THEIR niche right now, from your tracked boards. Send it with "
           "“Want 50 more per week?” — strongest $0 sales asset you have.")

if not C.STATE_FILE.exists():
    st.info("**No tracked jobs yet.** The mini-list is built from "
            "`signals_state.json`, which the signal engine creates.")
    st.markdown("Run the sweep from the **Home** page (or "
                "`python signal_engine.py`), then come back.")
    st.stop()

g1, g2 = st.columns(2)
with g1:
    ml_prospect = st.text_input("Prospect agency name",
                                placeholder="Acme Staffing",
                                key="ml_prospect",
                                help="Used in the one-pager headline and the "
                                     "output file name.")
    ml_keywords = st.text_input(
        "Their niche keywords (comma-separated)",
        placeholder="java, .net, backend", key="ml_kw",
        help="Job titles must contain at least one of these. This is THEIR "
             "niche, not yours — check their website/LinkedIn.")
with g2:
    ml_locations = st.text_input(
        "Their market (locations, comma-separated — empty = anywhere)",
        placeholder="united states, remote", key="ml_loc",
        help="Substring match on job location; 'remote' also matches "
             "remote-flagged titles.")
    ml_n = st.slider("List size", 5, 25, 10, key="ml_n",
                     help="10 is the sweet spot — enough to prove the "
                          "machine, small enough to leave them wanting more.")
ml_pitch = st.text_input("Footer pitch line", DEFAULT_PITCH, key="ml_pitch",
                         help="The call-to-action printed at the bottom of "
                              "the branded one-pager.")

ml_kws = C.parse_csv_line(ml_keywords)
if not ml_kws:
    st.info("Enter at least one niche keyword above to build the list.")
    st.stop()

ml_rows = build_minilist(ml_kws, C.parse_csv_line(ml_locations),
                         n=int(ml_n),
                         state_file=C.STATE_FILE,
                         contacts_file=C.CONTACTS_CACHE)
if not ml_rows:
    st.warning("**No tracked jobs match.** Widen the keywords, drop the "
               "location filter, or grow the watchlist "
               "(System → Watchlist).")
    st.stop()

n_contact = sum(1 for r in ml_rows if r["contact_name"])
st.caption(f"**{len(ml_rows)} companies** ({n_contact} with a "
           "named contact). Companies with contacts and multiple "
           "matching roles rank first.")
st.dataframe(
    ml_rows, width="stretch", height=340,
    column_config={
        "job_url": st.column_config.LinkColumn("job_url"),
        "contact_linkedin":
            st.column_config.LinkColumn("contact_linkedin")})

ml_slug = re.sub(r"[^a-z0-9]+", "-",
                 (ml_prospect or "prospect").lower()).strip("-")
ml_fname = f"minilist_{ml_slug}_{date.today().isoformat()}"
d1, d2 = st.columns(2)
with d1:
    st.download_button("⬇ CSV (raw data)", render_csv(ml_rows),
                       file_name=f"{ml_fname}.csv",
                       mime="text/csv", width="stretch",
                       help="The list as plain data — paste rows into a DM "
                            "or spreadsheet.")
with d2:
    st.download_button(
        "⬇ Branded one-pager (HTML)",
        render_html(ml_prospect or "you", ml_rows, ml_pitch),
        file_name=f"{ml_fname}.html", mime="text/html",
        width="stretch",
        help="Open it in a browser and Ctrl+P → PDF for a clean attachment.")
st.caption("Open the HTML and Ctrl+P → PDF for a clean "
           "attachment, or paste the top rows straight into a DM.")
