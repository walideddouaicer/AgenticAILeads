"""Prospects — OUR pipeline: recruitment agencies we want as clients.

Mirrors the Outreach Queue mechanics, but for our own outreach:
prospects.csv (prospector_engine) joined with prospect_signals.csv
(mandate sweep) for hooks, status in prospects_status.json (separate
from the lead pipeline), and a one-click mini-list per prospect as the
door-opener.
"""

from datetime import date, datetime

import streamlit as st

import common as C
import outreach
from evidence import load_refreshes, verified_at
from minilist_engine import (apply_sample_review, build_minilist, render_csv,
                             render_table, reviewable_rows, sample_row_id)
from storage import ConflictError, read_json

C.page_header("🧲 Prospects", "prospects")

prospects = C.load_prospects()
signals = C.load_prospect_signals()
status, raw_revisions = C.load_status(C.PROSPECTS_STATUS,
                                      with_revisions=True)
pcfg = C.load_prospects_config()

# ---------------- engines (always available, incl. empty state) ----------------

C.render_engine_panel()

# --- who counts as a prospect (icp.json) ---
icp = C.load_icp()
with st.expander(f"🎯 ICP — who the prospector looks for "
                 f"({icp['size_min']}–{icp['size_max']} people)"):
    st.caption(
        "This defines who ENTERS the list. It is not used to rank "
        "prospects later — if the prospector only collects "
        f"{icp['size_min']}–{icp['size_max']} person agencies, "
        "re-checking size when ranking filters nothing.")
    i1, i2 = st.columns(2)
    with i1:
        smin = int(st.number_input(
            "Min employees", 1, 500, icp["size_min"], key="icp_min",
            help="Below ~10 an agency usually can't pay $1,500/month. "
                 "The original 2–20 band was the wrong call."))
        titles = st.text_area(
            "Decision-maker titles", ", ".join(icp["titles"]),
            key="icp_titles", height=70)
    with i2:
        smax = int(st.number_input(
            "Max employees", 1, 500, icp["size_max"], key="icp_max",
            help="Above ~50 they likely have their own BD team."))
        countries = st.text_area(
            "Countries", ", ".join(icp["countries"]),
            key="icp_countries", height=70)
    kw = st.text_area("Company keywords", ", ".join(icp["keywords"]),
                      key="icp_kw", height=70,
                      help="Matched against the agency's Apollo tags.")
    if st.button("Save ICP", key="icp_save"):
        C.save_icp({
            "titles": C.parse_csv_line(titles),
            "keywords": C.parse_csv_line(kw),
            "size_min": smin, "size_max": smax,
            "countries": C.parse_csv_line(countries),
        })
        C.push_toast("ICP saved — next prospector run uses it", "🎯")
        st.rerun()
    off = sum(1 for p in prospects if not C.matches_icp(p, icp))
    if off:
        st.caption(f"⚠️ {off} of {len(prospects)} existing prospect(s) "
                   "were collected under a different ICP and no longer "
                   "match this one. They stay in the list; the daily "
                   "queue flags them.")

with st.expander("⚙️ Run prospecting engines",
                 expanded=not prospects):
    e1, e2, e3 = st.columns(3)
    with e1:
        pr_limit = int(st.number_input(
            "Prospector API-call cap", 1, 100, 20, key="pr_limit",
            help="Hard cap on Apollo API calls per prospector run "
                 "(free tier: 600/day — stay far under)."))
        C.engine_button("prospector", key="pr_run_prospector",
                        extra_args=["--limit", pr_limit],
                        help_text="Searches Apollo for founders/MDs/owners "
                                  "at small recruitment agencies and appends "
                                  "new ones to prospects.csv. Run "
                                  "`python prospector_engine.py --probe` in "
                                  "a terminal FIRST to verify the filters "
                                  "your Apollo plan accepts.")
        st.caption("Find agency decision-makers (Apollo)")
    with e2:
        # One button, two steps: find the boards, then sweep them. They were
        # never useful apart — a board you haven't swept yields no hooks,
        # and a sweep with no boards does nothing.
        C.engine_button("find_mandates", key="pr_run_mandates",
                        extra_args=["--from-prospects", "--and-sweep"],
                        help_text="Probes your prospects' agency names "
                                  "across all 4 ATS platforms, adds the ones "
                                  "that look like agency boards, then sweeps "
                                  "them for advertised roles. Confirm the "
                                  "agency-board mapping below before using "
                                  "postings in a message.")
        st.caption("Find boards → inspect → confirm mapping")
    with e3:
        n_boards = 0
        if C.AGENCY_WATCHLIST_FILE.exists():
            try:
                import json as _json
                n_boards = sum(len(v) for v in _json.loads(
                    C.AGENCY_WATCHLIST_FILE.read_text(encoding="utf-8")
                ).values())
            except (ValueError, AttributeError):
                n_boards = 0
        st.metric("Agency boards found", n_boards,
                  help="Most small agencies use Bullhorn/JobAdder/Vincere, "
                       "not the 4 platforms we can read — expect a low hit "
                       "rate here.")
        st.caption(f"{len(signals)} mandate signal(s) on file")

with st.expander("Confirm which agency owns a board"):
    st.caption("Discovery suggests boards from names and posting patterns. "
               "Select a prospect agency and confirm its actual board "
               "before its postings can support a message.")
    agency_names = sorted({p.get("agency", "").strip() for p in prospects
                           if p.get("agency", "").strip()})
    watch = read_json(C.AGENCY_WATCHLIST_FILE, default={}, expected_type=dict)
    board_options = sorted(f"{platform}:{token}"
                           for platform, tokens in watch.items()
                           for token in tokens)
    mappings = read_json(C.AGENCY_BOARD_MAPPINGS, default={},
                         expected_type=dict)
    checks = load_refreshes(C.AGENCY_REFRESH_FILE)
    suggestions = read_json(C.DATA_DIR / "agency_board_suggestions.json",
                            default={}, expected_type=dict)
    if agency_names and board_options:
        selected_agency = st.selectbox("Agency", agency_names,
                                       key="confirm_agency_name")
        agency_id = selected_agency.casefold()
        st.caption("Suggested: " + (", ".join(suggestions.get(agency_id, []))
                                    or "none; inspect the board before confirming"))
        selected_boards = st.multiselect(
            "Confirmed boards", board_options,
            default=mappings.get(agency_id, []), key=f"agency_boards_{agency_id}")
        for board in selected_boards:
            check = checks.get(board, {})
            st.caption(f"{board}: {check.get('status', 'never checked')}; "
                       f"last successful check "
                       f"{check.get('last_success') or 'unknown'}; "
                       + ("current evidence" if verified_at(board, checks)
                          else "refresh required"))
        if st.button("Save confirmed boards", key="save_agency_boards"):
            C.set_agency_boards(selected_agency, selected_boards)
            st.success("Confirmed board mapping saved.")
            st.rerun()
    else:
        st.info("Add a prospect and discover or add an agency board first.")

if not prospects:
    st.info("**No prospects yet.** This page fills from `prospects.csv`.")
    st.markdown(
        "1. In a terminal, run `python prospector_engine.py --probe` once "
        "and sanity-check the response (right filters, right countries)\n"
        "2. Then click **Run Prospector** above (or run "
        "`python prospector_engine.py`)\n"
        "3. Optionally discover agency boards, inspect them and confirm "
        "their mapping before using a posting as a hook\n\n"
        "You can also add rows to `prospects.csv` by hand — columns: "
        "`date_found, agency, person_name, person_title, linkedin, email, "
        "location, size, source, niche_keywords`.")
    st.stop()

# ---------------- per-prospect derived data ----------------

by_key = {}
for p in prospects:
    k = C.prospect_key(p)
    by_key[k] = p   # duplicate rows (same person) collapse to the last one

mandates = {k: C.mandates_for_agency(p.get("agency", ""), signals)
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
stage_of = {k: C.effective_status(status.get(k)) for k in by_key}
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
            help=f"Contacted ≥{C.FOLLOWUP_DAYS} days ago, under "
                 f"{C.MAX_FOLLOWUPS} follow-ups. A follow-up never costs a "
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

# ---------------- templates editor ----------------

with st.expander("✍️ Message templates (prospects_config.json)"):
    st.caption("Placeholders: {first_name} {agency} {hook} {person_title} "
               "{location}. The offer is the PLAYBOOK one: pay per showed "
               "meeting, first free.")
    t1 = st.text_area("First touch", pcfg["dm_template"], height=110,
                      key="pt_dm", help="Used for prospects in 'new'.")
    t2 = st.text_area(f"Follow-up #1 (due {C.FOLLOWUP_DAYS} days after "
                      "contact)", pcfg["followup_template"], height=100,
                      key="pt_fu",
                      help="Most replies come from follow-ups.")
    t3 = st.text_area("Follow-up #2 — breakup (last touch)",
                      pcfg["followup2_template"], height=90, key="pt_fu2",
                      help="After this one the prospect leaves the queue.")
    if st.button("💾 Save templates", key="pt_save", type="primary",
                 help="Writes prospects_config.json — takes effect "
                      "immediately."):
        C.save_prospects_config({"dm_template": t1, "followup_template": t2,
                                 "followup2_template": t3})
        C.push_toast("Prospect templates saved", "💾")
        st.rerun()

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
                            expected=raw_revisions.get(k, C.revision(None)),
                            sample=sample)
    except ConflictError:
        st.session_state[f"draft_conflict_{k}"] = True
    else:
        st.session_state.pop(f"draft_conflict_{k}", None)


def prospect_card(k, p, kind, i):
    """kind: 'fu' | 'new' | 'play'."""
    entry = status.get(k)
    eff = C.effective_status(entry)
    due = C.followup_due(entry, now=outreach.local_now())
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
    with st.expander(head, expanded=(i == 0 and kind != "play")):
        st.markdown(C.stage_badge(eff, due), unsafe_allow_html=True)
        meta = " · ".join(x for x in [
            p.get("location", ""),
            f"{p.get('size', '')} employees" if p.get("size") else "",
            f"found {p.get('date_found', '')}"] if x)
        if meta:
            st.caption(meta)
        if kind == "new" and not C.matches_icp(p, icp):
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
            st.warning("No hook yet — run **Agency board discovery** + "
                       "**Mandate sweep** above, or set niche keywords "
                       "below. PLAYBOOK rule: no hook = don't send.")

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
            draft_widget_key += f"_{C.revision(reviewed)[:8]}"
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
                            expected=raw_revisions.get(k, C.revision(None)),
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
                C.update_status_fields(status, k, {"niche": niche_in},
                                       C.PROSPECTS_STATUS)
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
                               "cannot enter the export.")
                    st.dataframe(ml_rows, width="stretch", hide_index=True)
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
                                C.update_status_fields(
                                    status, k, {"sample_review": value},
                                    C.PROSPECTS_STATUS,
                                    expected=raw_revisions.get(k, C.revision(None)))
                            except ConflictError:
                                st.error("This prospect changed in another "
                                         "session. Reload and review again.")
                            else:
                                st.success("Reviewed sample saved.")
                                st.rerun()
                    else:
                        st.warning("Refresh a matching board and confirm its "
                                   "company identity before approving rows.")
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
                           "widen them or grow the client watchlist.")

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
                             disabled=(template_invalid or draft_conflict or
                                       (sample_claim and not included_sample)),
                             help="Records the final message and resets the "
                                  f"{C.FOLLOWUP_DAYS}-day timer."):
                    try:
                        C.record_prospect_send(status, k, message=draft,
                                               sample=reviewed if included_sample else [],
                                               channel=channel,
                                               lead_label=label,
                                               expected=raw_revisions.get(k, C.revision(None)))
                    except (ConflictError, ValueError) as error:
                        st.error(str(error))
                    else:
                        st.rerun()
            with b2:
                if st.button("💬 They replied", key=f"prp_{k}",
                             width="stretch",
                             help="Moves the prospect to 'replied' — "
                                  "answer within 2 hours."):
                    C.set_stage(status, k, "replied", lead_label=label,
                                path=C.PROSPECTS_STATUS)
                    st.rerun()
        elif kind == "new":
            with b1:
                if st.button("✅ Sent — mark contacted", key=f"pct_{k}",
                             type="primary", width="stretch",
                             disabled=(template_invalid or draft_conflict or
                                       (sample_claim and not included_sample)),
                             help="Click after sending. Stores this exact "
                                  "message and approved sample."):
                    try:
                        C.record_prospect_send(status, k, message=draft,
                                               sample=reviewed if included_sample else [],
                                               channel=channel,
                                               lead_label=label,
                                               expected=raw_revisions.get(k, C.revision(None)))
                    except (ConflictError, ValueError) as error:
                        st.error(str(error))
                    else:
                        st.rerun()
            with b2:
                if st.button("🚫 Dismiss", key=f"pdx_{k}", width="stretch",
                             help="Bad fit / wrong person — out of the "
                                  "queue. Undo available after."):
                    C.set_stage(status, k, "dismissed", lead_label=label,
                                path=C.PROSPECTS_STATUS)
                    st.rerun()
        else:  # play: replied -> call_booked -> client_won
            with b1:
                if eff == "replied" and st.button(
                        "📅 Call booked", key=f"pcb_{k}", type="primary",
                        width="stretch",
                        help="The discovery call is on the calendar."):
                    C.set_stage(status, k, "call_booked", notes,
                                lead_label=label, path=C.PROSPECTS_STATUS)
                    st.rerun()
                if eff == "call_booked" and st.button(
                        "🎉 Client won", key=f"pcw_{k}", type="primary",
                        width="stretch",
                        help="They're a client — set up their profile in "
                             "System → Client Configs!"):
                    C.set_stage(status, k, "client_won", notes,
                                lead_label=label, path=C.PROSPECTS_STATUS)
                    st.rerun()
            with b2:
                if st.button("🚫 Dismiss", key=f"pdx2_{k}", width="stretch",
                             help="Conversation went nowhere — close it "
                                  "out."):
                    C.set_stage(status, k, "dismissed", notes,
                                lead_label=label, path=C.PROSPECTS_STATUS)
                    st.rerun()


due_list = [(item["key"], item["prospect"]) for item in queue["followups"]]
new_list = [(item["key"], item["prospect"]) for item in queue["new"]]
play_list = [(item["key"], item["prospect"]) for item in queue["replies"]]

# ---------------- the three zoom levels ----------------
# Today, In play and All are the same data at three distances, so they are
# tabs on one page rather than three pages. Today is first because it is
# the only one you open every day.
n_today = len(play_list) + n_due + len(new_list)
tab_today, tab_all = st.tabs(
    [f"📮 Today ({n_today})", f"📋 All ({len(by_key)})"])

# ---------------- Today: the daily driver ----------------
with tab_today:
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
with tab_all:
    q = st.text_input("Search", key="p_all_q",
                      placeholder="agency, person, location")
    only = st.multiselect("Stage", ["new", "contacted", "replied",
                                    "call_booked", "client_won", "dismissed"],
                          key="p_all_stage")
    rows = []
    for k, p in by_key.items():
        entry = status.get(k) or {}
        if only and stage_of[k] not in only:
            continue
        blob = " ".join([p.get("agency", ""), p.get("person_name", ""),
                         p.get("location", "")]).lower()
        if q.strip() and q.strip().lower() not in blob:
            continue
        rows.append({
            "agency": p.get("agency", ""),
            "person": p.get("person_name", ""),
            "title": p.get("person_title", ""),
            "stage": stage_of[k].replace("_", " ")
                     + (" · follow-up due" if C.followup_due(
                         entry, now=outreach.local_now()) else ""),
            "since": entry.get("on", ""),
            "mandates": len(mandates[k]),
            "location": p.get("location", ""),
            "linkedin": p.get("linkedin", ""),
            "notes": entry.get("notes", ""),
            "next_step": "; ".join(
                queue["blocked"].get(k, [])
                + (["Review against current criteria"]
                   if not C.matches_icp(p, icp) else [])),
        })
    st.caption(f"{len(rows)} of {len(by_key)} prospect(s)")
    st.dataframe(rows, width="stretch", height=420, hide_index=True,
                 column_config={
                     "linkedin": st.column_config.LinkColumn("linkedin")})
    selected_key = st.selectbox(
        "Inspect prospect history", list(by_key),
        format_func=lambda key: (f"{by_key[key].get('person_name', '')} @ "
                                 f"{by_key[key].get('agency', '')}"),
        key="prospect_history_key")
    with st.expander("Correct prospect identity or contact"):
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
                C.correct_prospect_identity(
                    selected_key,
                    {"person_name": corrected_name,
                     "linkedin": corrected_linkedin,
                     "email": corrected_email},
                    expected=C.prospect_row_revision(selected))
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
