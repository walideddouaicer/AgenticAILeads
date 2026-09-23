"""Daily Inbox — every signal, its stage, notes and DM drafts."""

import streamlit as st

import common as C

C.page_header("📥 Daily Inbox", "inbox")

leads, src_name = C.load_leads()
status = C.load_status()

if not leads:
    st.info("**No signals yet.** Signals appear after the signal engine has "
            "run twice (first run = baseline).")
    st.markdown("Add and refresh employer boards in **Signals**, then "
                "review the matches in **Clients → Matched companies**.")
    st.stop()

c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    f_client = st.selectbox("Client", ["all"] + C.all_clients(leads),
                            key="inbox_client",
                            help="Only show one client's signals.")
with c2:
    f_status = st.selectbox(
        "Show",
        ["new", "follow-up due", "contacted", "replied", "won",
         "dismissed", "all"],
        key="inbox_show_v2",
        help="Filter by stage. 'follow-up due' = contacted leads whose "
             f"last touch is ≥{C.FOLLOWUP_DAYS} days old.")
with c3:
    n_due = sum(1 for r in leads if C.followup_due(status.get(C.lead_key(r))))
    n_new = sum(1 for r in leads
                if C.effective_status(status.get(C.lead_key(r))) == "new")
    st.caption(f"Source: {src_name} — {len(leads)} signals — "
               f"**{n_new} new · {n_due} follow-up(s) due**")

# pass 1: collect every row matching the filters (newest first)
visible = []
for row in reversed(leads):
    key = C.lead_key(row)
    entry = status.get(key)
    eff = C.effective_status(entry)
    due = C.followup_due(entry)

    if f_client != "all" and row.get("client") != f_client:
        continue
    keep = (
        (f_status == "all") or
        (f_status == "new" and eff == "new") or
        (f_status == "follow-up due" and due) or
        (f_status == eff)
    )
    if not keep:
        continue
    visible.append((row, key, entry, eff, due))

if not visible:
    st.success("Nothing for this filter. Go check replies instead.")
    st.stop()

# pass 2: paginate, 25 per page
PER_PAGE = 25
n_pages = (len(visible) + PER_PAGE - 1) // PER_PAGE
if st.session_state.get("inbox_page", 1) > n_pages:
    st.session_state["inbox_page"] = n_pages   # filters shrank the list
if n_pages > 1:
    p1, p2 = st.columns([1, 4], vertical_alignment="center")
    with p1:
        page = int(st.number_input("Page", 1, n_pages, key="inbox_page",
                                   help=f"{PER_PAGE} signals per page, "
                                        "newest first."))
else:
    page = 1
start = (page - 1) * PER_PAGE
chunk = visible[start:start + PER_PAGE]
if n_pages > 1:
    with p2:
        st.caption(f"Showing {start + 1}–{start + len(chunk)} "
                   f"of {len(visible)}")

for offset, (row, key, entry, eff, due) in enumerate(chunk):
    wkey = f"{key}_{start + offset}"  # unique even for duplicate rows

    notes_preview = (entry or {}).get("notes", "")
    head = (f"{row.get('company','?')} — {row.get('title','?')}  "
            f"[{row.get('client')}]"
            + ("  📝" if notes_preview else ""))

    bcol, xcol = st.columns([0.14, 0.86], vertical_alignment="center")
    with bcol:
        st.markdown(C.stage_badge(eff, due), unsafe_allow_html=True)
    with xcol:
        expander = st.expander(head, expanded=False)

    with expander:
        a, b = st.columns([1, 1])
        with a:
            st.text(f"Date:      {row.get('date','')}")
            st.text(f"Location:  {row.get('location','')}")
            st.text(f"Hook:      {C.display_hook(row)}")
            if entry and eff != "new":
                st.text(f"Stage:     {eff}"
                        + (f" (since {entry.get('on','')})" if entry.get('on') else "")
                        + ("  — FOLLOW-UP DUE" if due else ""))
            if row.get("url"):
                st.markdown(f"[job posting]({row['url']})")
            if row.get("contact1_name"):
                st.markdown(
                    f"**Contact:** {row['contact1_name']} — {row.get('contact1_title','')}  \n"
                    f"[LinkedIn]({row.get('contact1_linkedin','')}) · "
                    f"{row.get('contact1_email','')}")
                if row.get("contact2_name"):
                    st.markdown(
                        f"**Alt:** {row['contact2_name']} — {row.get('contact2_title','')} "
                        f"([LinkedIn]({row.get('contact2_linkedin','')}))")
            else:
                st.caption("No contact yet — find them on LinkedIn and add "
                           "them below (Apollo API enrichment is gated on "
                           "the free plan).")
            C.render_contact_editor(row, wkey)

            notes = st.text_area("Notes", (entry or {}).get("notes", ""),
                                 height=80, key=f"nt_{wkey}",
                                 placeholder="reply content, objections, next step…",
                                 help="Saved with the lead — searchable from "
                                      "the client page.")
            if st.button("Save notes", key=f"svnt_{wkey}",
                         help="Stores the notes without changing the stage."):
                C.update_status_fields(status, key, {"notes": notes})
                C.push_toast("Notes saved", "📝")
                st.rerun()

        with b:
            client = row.get("client", "default")
            cfg = C.load_profile(client) \
                if (C.CONFIG_DIR / f"{client}.json").exists() \
                else {"dm_template": C.DEFAULT_TEMPLATE}
            dm = C.render_dm(cfg.get("dm_template", C.DEFAULT_TEMPLATE), row)
            edited = st.text_area("Draft DM (edit, then copy)", dm,
                                  height=130, key=f"dm_{wkey}",
                                  help="Rendered from this client's DM "
                                       "template. Edit freely — the copy "
                                       "box below follows.")
            st.code(edited, language=None)

            # one-click email compose (only when a real address exists)
            email = row.get("contact1_email", "")
            if "@" in email and "locked" not in email.lower():
                subj = C.render_dm(cfg.get("email_subject",
                                           C.DEFAULT_EMAIL_SUBJECT), row)
                body = C.render_dm(cfg.get("email_template",
                                           C.DEFAULT_EMAIL_TEMPLATE), row)
                st.markdown(
                    f"[✉️ Compose email to {email}]"
                    f"({C.mailto_link(email, subj, body)})")

            label = f"{row.get('company', '?')} — {row.get('title', '?')}"
            if eff == "new":
                q1, q2 = st.columns(2)
                with q1:
                    if st.button("Mark contacted", key=f"c_{wkey}",
                                 type="primary", width="stretch",
                                 help="Click after sending the DM — starts "
                                      "the follow-up timer."):
                        C.set_stage(status, key, "contacted", notes,
                                    lead_label=label)
                        st.rerun()
                with q2:
                    if st.button("Dismiss", key=f"dx_{wkey}",
                                 width="stretch",
                                 help="Removes the lead from the queue."):
                        C.set_stage(status, key, "dismissed", notes,
                                    lead_label=label)
                        st.rerun()
            else:
                stage_options = C.STAGES + ["dismissed"]
                cur = eff if eff in stage_options else "contacted"
                new_stage = st.selectbox(
                    "Stage", stage_options,
                    index=stage_options.index(cur), key=f"st_{wkey}",
                    help="contacted → replied → won; dismissed drops the "
                         "lead from the queue.")
                u1, u2 = st.columns(2)
                with u1:
                    if st.button("Update stage", key=f"up_{wkey}",
                                 type="primary", width="stretch",
                                 help="Applies the stage picked above and "
                                      "saves the notes."):
                        C.set_stage(status, key, new_stage, notes,
                                    lead_label=label)
                        st.rerun()
                with u2:
                    if eff == "contacted" and st.button(
                            "Followed up (reset timer)", key=f"fu_{wkey}",
                            width="stretch",
                            help="Logs a follow-up touch and restarts the "
                                 f"{C.FOLLOWUP_DAYS}-day timer."):
                        C.mark_followed_up(status, key, label)
                        st.rerun()
