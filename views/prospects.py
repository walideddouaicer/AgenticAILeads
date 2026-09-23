"""Prospects — OUR pipeline: recruitment agencies we want as clients.

Mirrors the Outreach Queue mechanics, but for our own outreach:
prospects.csv (prospector_engine) joined with prospect_signals.csv
(mandate sweep) for hooks, status in prospects_status.json (separate
from the lead pipeline), and a one-click mini-list per prospect as the
door-opener.
"""

from datetime import date

import streamlit as st

import common as C
import core
import outreach
import prospect_import
from minilist_engine import (render_csv, render_table, reviewable_rows,
                             sample_row_id)
from storage import ConflictError, DataFileError

C.page_header("🧲 Prospects", "prospects")

prospects = core.load_prospects()
signals = core.load_prospect_signals()
status, raw_revisions = core.load_status(core.PROSPECTS_STATUS,
                                      with_revisions=True)
pcfg = core.load_prospects_config()

# Keep acquisition tools secondary to today's work.
icp = core.load_icp()
with st.expander("Add or find agencies", expanded=not prospects):
    st.caption("Import a CSV or add one person manually. Apollo lookup is optional.")
    st.caption("Save a manual entry before leaving this screen.")
    upload_epoch = st.session_state.get("prospect_upload_epoch", 0)
    uploaded = st.file_uploader("Import agencies CSV", type=["csv"],
                                key=f"prospect_upload_{upload_epoch}")
    if uploaded is not None:
        try:
            accepted, rejected = prospect_import.preview_csv(
                uploaded.getvalue(), prospects)
        except ValueError as error:
            st.error(str(error))
        else:
            st.caption(f"{len(accepted)} ready to import; {len(rejected)} rejected.")
            if rejected:
                st.dataframe(rejected, hide_index=True, width="stretch")
            if st.button("Import accepted rows", key="prospect_import_save",
                         disabled=not accepted):
                try:
                    added = prospect_import.import_rows(accepted)
                except DataFileError as error:
                    st.error(str(error))
                else:
                    st.session_state["prospect_upload_epoch"] = upload_epoch + 1
                    C.push_toast(f"{added} agency contact(s) imported.")
                    st.rerun()

    with st.form("add_prospect"):
        st.markdown("**Add one person**")
        c1, c2 = st.columns(2)
        with c1:
            agency = st.text_input("Agency name", key="add_agency")
            person_name = st.text_input("Person name", key="add_person")
            linkedin = st.text_input("LinkedIn profile URL", key="add_linkedin")
        with c2:
            person_title = st.text_input("Person title", key="add_title")
            email = st.text_input("Email address", key="add_email")
            location = st.text_input("Agency market", key="add_market")
        niche_keywords = st.text_input("Niche keywords (comma separated)",
                                       key="add_niche")
        if st.form_submit_button("Add person", key="prospect_add_person"):
            row = {"agency": agency, "person_name": person_name,
                   "person_title": person_title, "linkedin": linkedin,
                   "email": email, "location": location,
                   "niche_keywords": niche_keywords, "source": "manual entry"}
            accepted, rejected = prospect_import.preview_rows([row], prospects)
            if rejected:
                st.error(rejected[0]["reason"])
            else:
                try:
                    added = prospect_import.import_rows(accepted)
                except DataFileError as error:
                    st.error(str(error))
                else:
                    C.push_toast("Person added." if added else
                                 "This person is already in the list.")
                    st.rerun()

    with st.expander("Optional Apollo search"):
        st.caption("Requires configured Apollo access. Verify the provider's "
                   "filters and cost before running a search.")
        pr_limit = int(st.number_input("Provider call limit", 1, 100, 20,
                                       key="pr_limit"))
        C.engine_button("prospector", key="pr_run_prospector",
                        extra_args=["--limit", pr_limit])
    C.render_engine_panel()

if not prospects:
    st.info("No agencies yet. Add a person above to begin reviewing fit, "
            "evidence and a message.")
    st.stop()

# ---------------- per-prospect derived data ----------------

by_key = {}
for p in prospects:
    k = core.prospect_key(p)
    by_key[k] = p   # duplicate rows (same person) collapse to the last one

mandates = {k: core.mandates_for_agency(p.get("agency", ""), signals)
            for k, p in by_key.items()}


contexts = {}


def context_for(k, p):
    if k not in contexts:
        contexts[k] = outreach.prospect_context(p, status.get(k) or {},
                                                signals, pcfg)
    return contexts[k]


def niche_for(k, p):
    context = context_for(k, p)
    return context["niche"], context["niche_source"]


def hook_for(k, p):
    context = context_for(k, p)
    return context["hook"], context["hook_source"]


# ---------------- metrics ----------------

queue = outreach.compute_queue(prospects, status, signals, pcfg, icp)
for item in queue["followups"] + queue["new"]:
    contexts[item["key"]] = item["context"]

today_iso = outreach.local_now().date().isoformat()
stage_of = {k: core.effective_status(status.get(k)) for k in by_key}
n_due = len(queue["followups"]) + len(queue["blocked_followups"])
touched = sum(1 for e in status.values()
              if e.get("on") == today_iso
              and e.get("status") in ("contacted", "replied", "call_booked",
                                      "client_won"))

# Only the three numbers that change what you do next. The rest
# (replied / call booked / client won / dismissed) were five zeroes taking
# up the top of the page — they belong in a report, not a work screen.
# Stages themselves are unchanged; this is display only.
m = st.columns(4)
m[0].metric("new", sum(1 for s in stage_of.values() if s == "new"),
            help="Never contacted.")
m[1].metric("contacted", sum(1 for s in stage_of.values() if s == "contacted"),
            help="Message sent, no answer yet.")
m[2].metric("replied", sum(1 for s in stage_of.values() if s == "replied"),
            help="They answered. Handle these first — an unanswered reply "
                 "is the most expensive thing on this page.")
m[3].metric("follow-ups due", n_due,
            help=f"Contacted ≥{core.FOLLOWUP_DAYS} days ago, under "
                 f"{core.MAX_FOLLOWUPS} follow-ups. A follow-up never costs a "
                 "LinkedIn connection request — those are spent only on "
                 "first contact, so following up is free capacity.")
booked = sum(1 for s in stage_of.values()
             if s in ("call_booked", "client_won"))
if booked or any(s == "dismissed" for s in stage_of.values()):
    st.caption(f"{booked} booked/won · "
               f"{sum(1 for s in stage_of.values() if s == 'dismissed')} "
               "dismissed")
st.caption(f"{len(by_key)} prospect(s) · {touched} touched today · "
           f"{len(signals)} mandate signal(s) on file")

# ---------------- undo ----------------

u = C.undo_available()
if u and st.button(f"↩ Undo: {u['label']}", key="p_undo",
                   help="Restores the previous stage/notes of the last "
                        "prospect (or lead) you changed."):
    label = C.undo_last()
    C.push_toast(f"Undone: {label}", "↩")
    st.rerun()


# ---------------- cards ----------------

def persist_draft(k, kind, widget_key, sample):
    try:
        outreach.save_draft(status, k, kind, st.session_state[widget_key],
                            expected=raw_revisions.get(k, core.revision(None)),
                            sample=sample)
    except ConflictError:
        st.session_state[f"draft_conflict_{k}"] = True
    else:
        st.session_state.pop(f"draft_conflict_{k}", None)


def prospect_card(k, p, kind, i, *, allow_send=True):
    """kind: 'fu' | 'new' | 'play'."""
    entry = status.get(k)
    eff = core.effective_status(entry)
    due = core.followup_due(entry, now=outreach.local_now())
    agency = p.get("agency", "?")
    person = p.get("person_name", "?")
    label = f"{person} @ {agency}"
    context = context_for(k, p)
    hook, hook_src = context["hook"], context["hook_source"]
    kws, kw_src = context["niche"], context["niche_source"]
    ml_rows = context["sample_rows"]
    stored_review = (entry or {}).get("sample_review")
    sample_review = stored_review if isinstance(stored_review, dict) else {}
    reviewed = context["sample"]

    icon = {"fu": "🔁 ", "new": "🆕 ", "play": "💬 "}[kind]
    head = (f"{icon}{person} — {p.get('person_title', '?')}  [{agency}]"
            + ("  · 🎣 board posting" if hook_src == "board posting" else ""))
    with st.expander(head, expanded=(i == 0)):
        st.markdown(C.stage_badge(eff, due), unsafe_allow_html=True)
        meta = " · ".join(x for x in [
            p.get("location", ""),
            f"{p.get('size', '')} employees" if p.get("size") else "",
            f"found {p.get('date_found', '')}"] if x)
        if meta:
            st.caption(meta)
        if kind == "new" and not core.matches_icp(p, icp):
            st.warning("This prospect no longer matches the current criteria. "
                       "Review the fit before sending.")
        lk = (p.get("linkedin") or "").strip()
        if lk:
            st.markdown(f"[open LinkedIn ↗]({lk})")
        email = p.get("email", "")
        if "@" in email and "locked" not in email.lower():
            st.caption(f"✉️ {email}")

        # mandates & hook
        ms = mandates[k]
        if ms:
            st.caption(f"Confirmed board posting ({ms[0].get('date', '')}): "
                       f"**{ms[0].get('mandate_title', '')}** "
                       f"{ms[0].get('location', '')} · "
                       f"{len(ms)} mandate signal(s) on file")
        if not hook:
            st.warning("No supported opener yet. Check the agency's board "
                       "in Signals or add a verified niche below.")

        # message draft
        if kind == "fu":
            n_fu = int((entry or {}).get("fu", 0) or 0)
            tmpl = (pcfg["followup2_template"] if n_fu >= 1
                    else pcfg["followup_template"])
            st.caption(f"Follow-up #{n_fu + 1}"
                       + (" (breakup)" if n_fu >= 1 else "")
                       + f" — last touch {(entry or {}).get('on', '?')}")
        else:
            tmpl = pcfg["dm_template"]
        draft = context["draft"]
        template_invalid = draft.startswith("Template needs correction:")
        draft_conflict = bool(st.session_state.get(f"draft_conflict_{k}"))
        sample_claim = any(token in tmpl for token in
                           ("{sample_count", "{contact_count",
                            "{sample_summary"))
        draft_widget_key = f"draft_{context['draft_kind']}_{k}"
        if sample_claim:
            draft_widget_key += f"_{core.revision(reviewed)[:8]}"
        included_sample = False
        if kind != "play":
            if template_invalid:
                st.error(draft)
            else:
                if context["draft_sample_changed"]:
                    st.info("The reviewed sample changed. Check the updated "
                            "message before sending.")
                draft = st.text_area("Final message", value=draft,
                                     key=draft_widget_key,
                                     height=150, on_change=persist_draft,
                                     args=(k, context["draft_kind"],
                                           draft_widget_key, reviewed))
                if draft_conflict:
                    st.error("This prospect changed in another session. Reload and review your draft.")
                if st.button("Save draft", key=f"save_draft_{k}"):
                    try:
                        outreach.save_draft(
                            status, k, context["draft_kind"], draft,
                            expected=raw_revisions.get(k, core.revision(None)),
                            sample=reviewed)
                    except ConflictError:
                        st.error("This prospect changed in another session. Reload and edit again.")
                    else:
                        st.session_state.pop(f"draft_conflict_{k}", None)
                        st.success("Draft saved.")
                        st.rerun()
                if (p.get("linkedin") or "").strip() and "@" in (p.get("email") or ""):
                    channel = st.selectbox("Send channel", ["linkedin", "email"],
                                           key=f"send_channel_{k}")
                else:
                    channel = context["channel"]
                if reviewed:
                    included_sample = st.checkbox(
                        "I included the reviewed sample with this message",
                        value=False, key=f"included_sample_{k}")
                    if sample_claim and not included_sample:
                        st.caption("Confirm the reviewed sample was included "
                                   "before recording this message.")

        # niche keywords + one-click mini-list (the door-opener)
        nc1, nc2 = st.columns([3, 1], vertical_alignment="bottom")
        with nc1:
            niche_in = st.text_input(
                "Niche keywords (comma-separated)", ", ".join(kws),
                key=f"nk_{k}",
                help="What THIS agency places — drives their mini-list. "
                     f"Currently: {kw_src}. Edit + Save to override "
                     "(persisted per prospect).")
        with nc2:
            if st.button("Save", key=f"nk_save_{k}",
                         help="Persists the override in "
                              "prospects_status.json."):
                core.update_status_fields(status, k, {"niche": niche_in},
                                       core.PROSPECTS_STATUS)
                C.push_toast(f"Niche saved for {agency}", "🏷")
                st.rerun()

        if kws:
            if ml_rows:
                n_contact = sum(1 for r in ml_rows if r["contact_name"])
                current = sum(1 for r in ml_rows if r.get("verified_at"))
                st.caption(f"🎁 Sample: **{len(ml_rows)} board/company rows**, "
                           f"{current} checked within 48 hours, "
                           f"{n_contact} with named contacts; "
                           f"{len(reviewed)} approved for export.")
                eligible = reviewable_rows(ml_rows)
                with st.popover("Review and correct sample"):
                    st.caption("Open each source before approval. Stale or "
                               "unconfirmed boards remain visible above but "
                               "cannot enter the export. Save corrections "
                               "before changing screens or records.")
                    st.dataframe(
                        ml_rows, width="stretch", hide_index=True,
                        column_config={"job_url": st.column_config.LinkColumn(
                            "Open source job")})
                    if eligible:
                        by_id = {sample_row_id(row): row for row in eligible}
                        options = list(by_id)
                        chosen = st.multiselect(
                            "Approve rows", options,
                            default=[row_id for row_id in
                                     sample_review.get("approved", [])
                                     if row_id in by_id],
                            format_func=lambda row_id: (
                                f"{by_id[row_id]['company']} — "
                                f"{by_id[row_id]['hiring_now']} "
                                f"[{by_id[row_id]['source_board']}]"),
                            key=f"sample_approved_{k}")
                        selected_id = st.selectbox(
                            "Correct one row's output", options,
                            format_func=lambda row_id: (
                                f"{by_id[row_id]['company']} — "
                                f"{by_id[row_id]['hiring_now']}"),
                            key=f"sample_correct_{k}")
                        source = by_id[selected_id]
                        prior = sample_review.get("corrections", {}).get(
                            selected_id, {})
                        corrected = {}
                        for field, title in (("company", "Company label"),
                                             ("hiring_now", "Role title"),
                                             ("location", "Location"),
                                             ("contact_name", "Contact name")):
                            corrected[field] = st.text_input(
                                title, prior.get(field, source.get(field, "")),
                                key=f"sample_{field}_{k}_{selected_id}")
                        if st.button("Save reviewed sample", key=f"sample_save_{k}"):
                            changes = dict(sample_review.get("corrections") or {})
                            delta = {field: value.strip()
                                     for field, value in corrected.items()
                                     if value.strip() != str(source.get(field, "")).strip()}
                            if delta:
                                changes[selected_id] = delta
                            else:
                                changes.pop(selected_id, None)
                            value = {"approved": chosen, "corrections": changes}
                            try:
                                core.update_status_fields(
                                    status, k, {"sample_review": value},
                                    core.PROSPECTS_STATUS,
                                    expected=raw_revisions.get(k, core.revision(None)))
                            except ConflictError:
                                st.error("This prospect changed in another "
                                         "session. Reload and review again.")
                            else:
                                st.success("Reviewed sample saved.")
                                st.rerun()
                    else:
                        st.warning("Refresh a matching board and confirm its "
                                   "company identity in Signals before "
                                   "approving rows.")
                slug = "".join(c if c.isalnum() else "-"
                               for c in agency.lower()).strip("-") or "prospect"
                fname = f"minilist_{slug}_{date.today().isoformat()}"
                if reviewed:
                    st.code(render_table(reviewed), language=None)
                else:
                    st.info("No approved sample rows yet. Review the source "
                            "and save an approval above.")
                st.download_button(
                    "⬇ Reviewed sample CSV", render_csv(reviewed),
                    file_name=f"{fname}.csv", mime="text/csv",
                    key=f"mlc_{k}", width="stretch", disabled=not reviewed,
                    help="Exports only approved rows and keeps source proof.")
            else:
                st.caption("No tracked jobs match these niche keywords — "
                           "widen them or add employer boards in Signals.")

        # notes (in-play prospects are live conversations)
        if kind == "play":
            notes = st.text_area("Notes", (entry or {}).get("notes", ""),
                                 height=70, key=f"pn_{k}",
                                 placeholder="reply content, call time, "
                                             "objections…",
                                 help="Saved with the prospect.")
        else:
            notes = None

        # actions
        b1, b2 = st.columns(2)
        if kind == "fu":
            with b1:
                if st.button("🔁 Followed up", key=f"pfu_{k}",
                             type="primary", width="stretch",
                             disabled=(not allow_send or not context["channel"] or
                                       not hook or template_invalid or draft_conflict or
                                       (sample_claim and not included_sample)),
                             help="Records the final message and resets the "
                                  f"{core.FOLLOWUP_DAYS}-day timer."):
                    try:
                        C.record_prospect_send(status, k, message=draft,
                                               sample=reviewed if included_sample else [],
                                               channel=channel,
                                               lead_label=label,
                                               expected=raw_revisions.get(k, core.revision(None)))
                    except (ConflictError, ValueError) as error:
                        st.error(str(error))
                    else:
                        st.rerun()
            with b2:
                if st.button("💬 They replied", key=f"prp_{k}",
                             width="stretch",
                             help="Moves the prospect to 'replied' for "
                                  "conversation handling."):
                    C.set_stage(status, k, "replied", lead_label=label,
                                path=core.PROSPECTS_STATUS)
                    st.rerun()
        elif kind == "new":
            with b1:
                if st.button("✅ Sent — mark contacted", key=f"pct_{k}",
                             type="primary", width="stretch",
                             disabled=(not allow_send or not context["channel"] or
                                       not hook or template_invalid or draft_conflict or
                                       (sample_claim and not included_sample)),
                             help="Click after sending. Stores this exact "
                                  "message and approved sample."):
                    try:
                        C.record_prospect_send(status, k, message=draft,
                                               sample=reviewed if included_sample else [],
                                               channel=channel,
                                               lead_label=label,
                                               expected=raw_revisions.get(k, core.revision(None)))
                    except (ConflictError, ValueError) as error:
                        st.error(str(error))
                    else:
                        st.rerun()
            with b2:
                if st.button("🚫 Dismiss", key=f"pdx_{k}", width="stretch",
                             help="Bad fit / wrong person — out of the "
                                  "queue. Undo available after."):
                    C.set_stage(status, k, "dismissed", lead_label=label,
                                path=core.PROSPECTS_STATUS)
                    st.rerun()
        else:  # play: replied -> call_booked -> client_won
            if eff == "client_won":
                st.success("Client won. Next: open Clients, choose Create "
                           "client and start from this agency, then review "
                           "and save its profile.")
            with b1:
                if eff == "replied" and st.button(
                        "📅 Call booked", key=f"pcb_{k}", type="primary",
                        width="stretch",
                        help="The discovery call is on the calendar."):
                    C.set_stage(status, k, "call_booked", notes,
                                lead_label=label, path=core.PROSPECTS_STATUS)
                    st.rerun()
                if eff == "call_booked" and st.button(
                        "🎉 Client won", key=f"pcw_{k}", type="primary",
                        width="stretch",
                        help="They're a client — set up their profile "
                             "from Clients."):
                    C.set_stage(status, k, "client_won", notes,
                                lead_label=label, path=core.PROSPECTS_STATUS)
                    st.rerun()
            with b2:
                if st.button("🚫 Dismiss", key=f"pdx2_{k}", width="stretch",
                             help="Conversation went nowhere — close it "
                                  "out."):
                    C.set_stage(status, k, "dismissed", notes,
                                lead_label=label, path=core.PROSPECTS_STATUS)
                    st.rerun()


due_list = [(item["key"], item["prospect"]) for item in queue["followups"]]
new_list = [(item["key"], item["prospect"]) for item in queue["new"]]
play_list = [(item["key"], item["prospect"]) for item in queue["replies"]]

# ---------------- daily work and searchable records ----------------
n_today = len(play_list) + n_due + len(new_list)
view = st.segmented_control(
    "Prospect view", ["Today", "All"], default="Today",
    key="prospect_view", label_visibility="collapsed")

# ---------------- Today: the daily driver ----------------
if view == "Today":
    st.caption(f"Today ({n_today}) · All ({len(by_key)})")
    st.caption(
        f"Replies first, then follow-ups, then new sends. "
        f"{queue['remaining_initial']} of {queue['budget']} initial sends "
        "remain today. Follow-ups do not use that budget.")
    if queue["legacy_counts_incomplete"]:
        st.caption("Older contacts have no action history, so historical "
                   "send counts may be incomplete.")

    # Live conversations sit at the TOP, not in a tab you have to remember
    # to click. One unanswered reply is worth more than twenty sends, so
    # it must be impossible to miss.
    if play_list:
        st.markdown(f"**💬 Waiting on you ({len(play_list)}) — "
                    "answer these before sending anything**")
        for i, (k, p) in enumerate(play_list):
            prospect_card(k, p, "play", i)
        st.divider()

    if due_list:
        st.markdown(f"**🔁 Follow-ups due ({len(due_list)})**")
        for i, (k, p) in enumerate(due_list):
            prospect_card(k, p, "fu", i)
    elif not queue["blocked_followups"]:
        st.caption("No follow-ups due.")
    if queue["blocked_followups"]:
        st.markdown(f"**🔁 Follow-ups needing correction "
                    f"({len(queue['blocked_followups'])})**")
        for item in queue["blocked_followups"]:
            p = item["prospect"]
            st.warning(f"{p.get('person_name', 'Unknown')} @ "
                       f"{p.get('agency', 'Unknown')}: "
                       + "; ".join(item["reasons"]))

    st.markdown(f"**🆕 New prospects — showing {len(new_list)} "
                f"of {queue['new_available']} eligible**")
    if not new_list and not prospects:
        st.caption("No prospects yet — import or find an agency.")
    elif not new_list and not queue["remaining_initial"]:
        st.caption("Today's initial-send budget is used. Follow-ups remain available.")
    elif not new_list and queue["blocked"]:
        st.caption(f"{len(queue['blocked'])} prospect(s) need a usable contact "
                   "or supported opener. Review them in All.")
    elif not new_list:
        st.caption("No new prospects are eligible today.")
    else:
        for i, (k, p) in enumerate(new_list):
            prospect_card(k, p, "new", i)

# ---------------- All: the full table ----------------
if view == "All":
    q = st.text_input("Search", key="p_all_q",
                      placeholder="agency, person, location")
    only = st.multiselect("Stage", ["new", "contacted", "replied",
                                    "call_booked", "client_won", "dismissed"],
                          key="p_all_stage")
    rows = []
    filtered_keys = []
    for k, p in by_key.items():
        entry = status.get(k) or {}
        if only and stage_of[k] not in only:
            continue
        blob = " ".join([p.get("agency", ""), p.get("person_name", ""),
                         p.get("location", "")]).lower()
        if q.strip() and q.strip().lower() not in blob:
            continue
        filtered_keys.append(k)
        rows.append({
            "agency": p.get("agency", ""),
            "person": p.get("person_name", ""),
            "title": p.get("person_title", ""),
            "stage": stage_of[k].replace("_", " ")
                     + (" · follow-up due" if core.followup_due(
                         entry, now=outreach.local_now()) else ""),
            "since": entry.get("on", ""),
            "mandates": len(mandates[k]),
            "location": p.get("location", ""),
            "linkedin": p.get("linkedin", ""),
            "notes": entry.get("notes", ""),
            "next_step": "; ".join(
                queue["blocked"].get(k, [])
                + (["Review against current criteria"]
                   if not core.matches_icp(p, icp) else [])),
        })
    st.caption(f"{len(rows)} of {len(by_key)} prospect(s)")
    st.dataframe(rows, width="stretch", height=420, hide_index=True,
                 column_config={
                     "linkedin": st.column_config.LinkColumn("linkedin")})
    if not filtered_keys:
        st.info("No prospect matches these filters. Clear Search or Stage "
                "to select a record.")
        st.stop()
    selected_key = st.selectbox(
        "Select prospect", filtered_keys,
        format_func=lambda key: (f"{by_key[key].get('person_name', '')} @ "
                                 f"{by_key[key].get('agency', '')}"),
        key="prospect_history_key")
    visible_today = {key for key, _ in play_list + due_list + new_list}
    selected_stage = stage_of[selected_key]
    if selected_stage in ("new", "contacted"):
        reasons = queue["blocked"].get(selected_key, [])
        if reasons:
            st.warning("Before sending: " + "; ".join(reasons))
        elif selected_stage == "new" and not queue["remaining_initial"]:
            st.info("Today's initial-send budget is used. You can still "
                    "review and correct this prospect.")
        elif selected_stage == "contacted":
            st.info("A follow-up is not due yet. You can review the "
                    "message and sample now.")
        prospect_card(selected_key, by_key[selected_key],
                      "fu" if selected_stage == "contacted" else "new",
                      0, allow_send=selected_key in visible_today)
    else:
        prospect_card(selected_key, by_key[selected_key], "play", 0,
                      allow_send=False)
    with st.expander("Correct prospect identity or contact"):
        st.caption("Save changes before selecting another record or screen.")
        selected = by_key[selected_key]
        corrected_name = st.text_input(
            "Person name", selected.get("person_name", ""),
            key=f"correct_name_{selected_key}")
        corrected_linkedin = st.text_input(
            "LinkedIn URL", selected.get("linkedin", ""),
            key=f"correct_linkedin_{selected_key}")
        corrected_email = st.text_input(
            "Email", selected.get("email", ""),
            key=f"correct_email_{selected_key}")
        if st.button("Save correction", key=f"correct_save_{selected_key}"):
            try:
                core.correct_prospect_identity(
                    selected_key,
                    {"person_name": corrected_name,
                     "linkedin": corrected_linkedin,
                     "email": corrected_email},
                    expected=core.prospect_row_revision(selected))
            except (ConflictError, ValueError) as error:
                st.error(str(error))
            else:
                st.success("Correction saved; prospect history stays attached.")
                st.rerun()
    actions = (status.get(selected_key) or {}).get("activity") or []
    if actions:
        for action in reversed(actions):
            with st.expander(f"{action.get('type', 'action')} · "
                             f"{action.get('at', 'unknown time')} · "
                             f"{action.get('channel', '')}"):
                st.caption(f"Recorded by {action.get('operator', 'unknown')}")
                st.code(action.get("message", ""), language=None)
                sample = action.get("sample") or []
                st.caption(f"{len(sample)} reviewed sample row(s) sent")
                if sample:
                    st.dataframe(sample, hide_index=True, width="stretch")
    else:
        st.caption("No recorded sends for this prospect. Older stage-only "
                   "entries cannot reconstruct a past message.")
