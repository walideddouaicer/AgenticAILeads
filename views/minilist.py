"""Mini-List — the free 'companies hiring in your niche' door-opener."""

import re
from datetime import date

import streamlit as st

import common as C
from minilist_engine import (DEFAULT_PITCH, apply_sample_review,
                             build_minilist, render_csv, render_table,
                             reviewable_rows, sample_row_id)

C.page_header("🎁 Mini-List", "minilist")

st.caption("Job-board examples for a prospect agency. Check each row's "
           "source and verification time before describing an opening as current.")

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
                     help="Choose how many reviewed matches to show.")
ml_pitch = st.text_input("Footer pitch line", DEFAULT_PITCH, key="ml_pitch",
                         help="The call-to-action printed at the bottom of "
                              "the branded one-pager.")

ml_kws = C.parse_csv_line(ml_keywords)
if not ml_kws:
    st.info("Enter at least one niche keyword above to build the list.")
    st.stop()

ml_rows = build_minilist(ml_kws, C.parse_csv_line(ml_locations) or None,
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
           "named contact; {sum(1 for r in ml_rows if r.get('verified_at'))} "
           "boards checked within 48 hours). Review stale rows before sending.")
st.dataframe(
    ml_rows, width="stretch", height=340,
    column_config={
        "job_url": st.column_config.LinkColumn("job_url"),
        "contact_linkedin":
            st.column_config.LinkColumn("contact_linkedin")})

eligible = reviewable_rows(ml_rows)
by_id = {sample_row_id(row): row for row in eligible}
st.markdown("**Review before export**")
if not eligible:
    st.warning("No rows have both a recent board check and a confirmed "
               "company identity. Refresh and confirm boards first.")
    reviewed = []
else:
    ids = list(by_id)
    approved = st.multiselect(
        "Approve rows", ids,
        format_func=lambda row_id: (f"{by_id[row_id]['company']} — "
                                    f"{by_id[row_id]['hiring_now']} "
                                    f"[{by_id[row_id]['source_board']}]"),
        key="ml_approved")
    selected_id = st.selectbox(
        "Correct one row's output", ids,
        format_func=lambda row_id: (f"{by_id[row_id]['company']} — "
                                    f"{by_id[row_id]['hiring_now']}"),
        key="ml_correction_row")
    corrections = st.session_state.setdefault("ml_corrections", {})
    source = by_id[selected_id]
    prior = corrections.get(selected_id, {})
    corrected = {}
    for field, title in (("company", "Company label"),
                         ("hiring_now", "Role title"),
                         ("location", "Location"),
                         ("contact_name", "Contact name")):
        corrected[field] = st.text_input(
            title, prior.get(field, source.get(field, "")),
            key=f"ml_{field}_{selected_id}")
    if st.button("Apply correction", key="ml_correct_save"):
        delta = {field: value.strip() for field, value in corrected.items()
                 if value.strip() != str(source.get(field, "")).strip()}
        if delta:
            corrections[selected_id] = delta
        else:
            corrections.pop(selected_id, None)
        st.rerun()
    reviewed = apply_sample_review(
        ml_rows, {"approved": approved, "corrections": corrections})

ml_slug = re.sub(r"[^a-z0-9]+", "-",
                 (ml_prospect or "prospect").lower()).strip("-")
ml_fname = f"minilist_{ml_slug}_{date.today().isoformat()}"
d1, d2 = st.columns(2)
with d1:
    st.download_button("⬇ Reviewed sample CSV", render_csv(reviewed),
                       file_name=f"{ml_fname}.csv",
                       mime="text/csv", width="stretch",
                       disabled=not reviewed,
                       help="Includes only approved rows with original "
                            "source title/location retained.")
with d2:
    st.caption("CSV is for when they ask for the full list. The message "
               "itself should carry the table below.")
st.markdown("**Paste this into the message**")
if reviewed:
    st.code(render_table(reviewed), language=None)
else:
    st.info("Approve at least one current, confirmed row above.")
