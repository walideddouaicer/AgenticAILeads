"""Client profile, verified matches, outreach and actual meeting report."""

from datetime import date

import streamlit as st

import client_delivery as D
import common as C
import core
from evidence import load_refreshes, verified_at
from filter_engine import DEFAULTS
from storage import ConflictError, DataFileError, revision

C.page_header("👤 Clients", "clients")
leads, source = core.load_leads()
status, raw_revisions = core.load_status(with_revisions=True)
ids = D.client_ids(leads)
if st.session_state.get("client_pending_selection"):
    st.session_state["client_choice"] = st.session_state.pop(
        "client_pending_selection")
client = st.selectbox("Client", ids + ["Create client"],
                      index=0 if ids else len(ids), key="client_choice")
view = ("Profile & matches" if client == "Create client" else
        st.segmented_control(
            "Client work", ["Profile & matches", "Outreach", "Outcomes & report"],
            default="Profile & matches", key="client_view"))
client_leads = [row for row in leads if row.get("client") == client]
config_path = core.CONFIG_DIR / f"{client}.json"
raw_config = ({} if client == "Create client" else
              core.read_json(config_path, default={}, expected_type=dict))
config = ({} if client == "Create client" else core.load_profile(client))

if view == "Profile & matches":
    st.subheader("Client profile")
    st.caption("Save this form before changing screens. A client ID identifies "
               "their leads and cannot be changed after creation.")
    if client == "Create client":
        st.info("Create a real client profile to match employers. The "
                "app's `default` filter is reserved for its own prospecting.")
        config = {**DEFAULTS, "client_id": "", "meeting_rate":
                  core.DEFAULT_MEETING_RATE, "channel": "linkedin",
                  "dm_template": core.DEFAULT_TEMPLATE,
                  "followup_template": core.DEFAULT_FOLLOWUP_TEMPLATE,
                  "followup2_template": core.DEFAULT_FOLLOWUP2_TEMPLATE}
    with st.form("client_profile"):
        c1, c2 = st.columns(2)
        with c1:
            client_id = st.text_input("Client ID", config.get("client_id", ""),
                                      disabled=client != "Create client")
            label = st.text_input("Client name", config.get("label", ""))
            niches = st.text_area("Target roles / niche keywords",
                                   ", ".join(config.get("inclusion_keywords") or []),
                                   help="At least one term must match the job title.")
            locations = st.text_area("Allowed locations",
                                      ", ".join(config.get("allowed_locations") or []),
                                      help="Leave empty only for an unrestricted market.")
        with c2:
            exclusions = st.text_area("Exclude role keywords",
                                      ", ".join(config.get("exclusion_keywords") or []))
            rate = st.number_input("Agreed rate per attended, chargeable meeting ($)",
                                   min_value=0,
                                   value=int(config.get("meeting_rate") or 0))
            channel = st.selectbox("Outreach channel", core.CHANNELS,
                                   index=core.CHANNELS.index(
                                       config.get("channel", "linkedin"))
                                   if config.get("channel", "linkedin") in core.CHANNELS
                                   else 0)
            active = st.toggle("Active for future board sweeps",
                               value=bool(config.get("active", True)))
        with st.expander("Message templates"):
            dm_template = st.text_area("First message",
                                       config.get("dm_template", core.DEFAULT_TEMPLATE))
            followup_template = st.text_area(
                "Follow-up", config.get("followup_template",
                                        core.DEFAULT_FOLLOWUP_TEMPLATE))
            followup2_template = st.text_area(
                "Final follow-up", config.get("followup2_template",
                                              core.DEFAULT_FOLLOWUP2_TEMPLATE))
        save = st.form_submit_button("Save client profile", type="primary")
    if save:
        edited = {**config, "client_id": client_id.strip(), "label": label.strip(),
                  "inclusion_keywords": core.parse_csv_line(niches),
                  "allowed_locations": core.parse_csv_line(locations),
                  "exclusion_keywords": core.parse_csv_line(exclusions),
                  "meeting_rate": int(rate), "channel": channel,
                  "active": active, "dm_template": dm_template,
                  "followup_template": followup_template,
                  "followup2_template": followup2_template}
        if not edited["inclusion_keywords"]:
            st.error("Add at least one niche keyword so the client does not "
                     "match every job.")
        else:
            try:
                D.save_profile(edited, expected=revision(raw_config or None))
            except (ValueError, ConflictError, DataFileError) as error:
                st.error(str(error))
            else:
                st.session_state["client_pending_selection"] = client_id.strip()
                C.push_toast(f"Client profile saved: {client_id.strip()}")
                st.rerun()

    if client != "Create client" and raw_config:
        st.divider()
        st.subheader("Preview current employer matches")
        st.caption("Only jobs from a recent successful board check can be "
                   "selected. Review each source and choose the jobs to add "
                   "for this client. The first board sweep remains a baseline.")
        matches = D.preview_matches(
            config, core.all_tracked_jobs(),
            load_refreshes(core.BOARD_REFRESH_FILE))
        if not matches:
            st.info("No current jobs match this profile. Adjust the niche or "
                    "market, or refresh employer boards in Signals.")
        else:
            st.dataframe([{"company": row.get("company", ""),
                           "role": row.get("title", ""),
                           "location": row.get("location", ""),
                           "source_url": row.get("url", ""),
                           "board": row.get("board_key", ""),
                           "verified_at": row.get("verified_at") or
                           "Refresh needed"} for row in matches],
                         hide_index=True, width="stretch",
                         column_config={"source_url":
                                        st.column_config.LinkColumn("Source job")})
            available = {row["selection_id"]: row for row in matches
                         if row["selectable"]}
            selected = st.multiselect(
                "Select verified jobs to backfill", list(available),
                format_func=lambda key: (f"{available[key].get('company')} — "
                                         f"{available[key].get('title')}"),
                key=f"client_backfill_{client}")
            if st.button("Add selected jobs as client leads", disabled=not selected,
                         type="primary"):
                try:
                    added, duplicates = D.backfill_selected(client, selected)
                except (ValueError, DataFileError) as error:
                    st.error(str(error))
                else:
                    C.push_toast(f"Added {added} client lead(s); "
                                 f"{duplicates} already present.")
                    st.rerun()
            if not available:
                st.warning("Matching jobs need a successful recent refresh "
                           "before they can be backfilled.")

if view == "Outreach":
    st.subheader("Client outreach")
    if not client_leads:
        st.info("No leads for this client yet. Preview verified matches in "
                "Profile & matches and add the relevant jobs.")
    else:
        q = st.text_input("Find a company or role", key="client_lead_search")
        visible = [row for row in client_leads if q.casefold() in (
            row.get("company", "") + " " + row.get("title", "")).casefold()]
        st.caption(f"{len(visible)} of {len(client_leads)} client leads · {source}")
        if not visible:
            st.info("No leads match this search.")
        else:
            options = {core.lead_key(row): row for row in visible}
            key = st.selectbox("Selected lead", list(options),
                               format_func=lambda value: (
                                   f"{options[value].get('company')} — "
                                   f"{options[value].get('title')}"),
                               key="client_selected_lead")
            row = options[key]
            entry = status.get(key) or {}
            stage = core.effective_status(entry)
            st.markdown(C.stage_badge(stage, core.followup_due(entry)),
                        unsafe_allow_html=True)
            st.write(f"**{row.get('company', '')}** — {row.get('title', '')} · "
                     f"{row.get('location', '')}")
            board = f"{row.get('platform', '')}:{row.get('company', '')}"
            checked = verified_at(board, load_refreshes(core.BOARD_REFRESH_FILE))
            st.caption(f"Source board: {board} · last verified: "
                       f"{checked or 'not current'}")
            if row.get("url"):
                st.markdown(f"[Open source job ↗]({row['url']})")
            st.caption("Opener: " + core.display_hook(row))
            C.render_contact_editor(row, f"client_{key}")
            contact_name = row.get("contact1_name", "")
            linkedin = (row.get("contact1_linkedin") or "").strip()
            email = (row.get("contact1_email") or "").strip()
            if contact_name:
                st.write(f"Contact: **{contact_name}** — "
                         f"{row.get('contact1_title', '')}")
            else:
                st.warning("Add a named decision-maker using the contact "
                           "editor before recording outreach.")
            if linkedin:
                other_clients = D.contacted_elsewhere(
                    leads, status, linkedin, client)
                if other_clients:
                    st.warning("This contact was already approached for "
                               "client(s): " + ", ".join(other_clients)
                               + ". Check before sending again.")
            ready = stage == "new" or (stage == "contacted" and
                                       core.followup_due(entry))
            if ready:
                kind = "initial" if stage == "new" else "followup"
                fu = int(entry.get("fu", 0) or 0)
                template = (config.get("dm_template", core.DEFAULT_TEMPLATE)
                            if kind == "initial" else
                            config.get("followup2_template", core.DEFAULT_FOLLOWUP2_TEMPLATE)
                            if fu >= 1 else
                            config.get("followup_template", core.DEFAULT_FOLLOWUP_TEMPLATE))
                draft = (entry.get("drafts") or {}).get(kind) or core.render_dm(
                    template, row)
                message = st.text_area("Final message", draft, height=140,
                                       key=f"client_message_{key}_{kind}")
                if st.button("Save draft", key=f"client_save_draft_{key}"):
                    drafts = dict(entry.get("drafts") or {})
                    drafts[kind] = message
                    try:
                        core.update_status_fields(status, key, {"drafts": drafts},
                                               expected=raw_revisions.get(
                                                   key, revision(None)))
                    except ConflictError as error:
                        st.error(str(error))
                    else:
                        C.push_toast("Client draft saved")
                        st.rerun()
                channels = [ch for ch in ("linkedin", "email")
                            if config.get("channel", "linkedin") in (ch, "both")]
                send_channel = st.selectbox("Manual send channel", channels,
                                            key=f"client_channel_{key}")
                address_ok = bool(linkedin) if send_channel == "linkedin" else (
                    "@" in email and "locked" not in email.casefold())
                if not address_ok:
                    st.warning("Add a usable " + ("LinkedIn URL" if send_channel ==
                               "linkedin" else "email address") + " for this contact.")
                st.code(message, language=None)
                if st.button("Record manual send", type="primary",
                             disabled=not (contact_name and address_ok and
                                           message.strip()),
                             help="Click only after sending the final message "
                                  "outside this app.",
                             key=f"client_record_send_{key}"):
                    try:
                        D.record_send(status, key, message=message,
                                      channel=send_channel, contact=contact_name,
                                      expected=raw_revisions.get(
                                          key, revision(None)),
                                      operator=st.session_state.get("_user") or
                                      "local operator")
                    except (ValueError, ConflictError) as error:
                        st.error(str(error))
                    else:
                        C.push_toast("Manual send recorded with its final message")
                        st.rerun()
            elif stage == "contacted":
                st.info("Follow-up is not due yet. The last send and its "
                        "message are recorded below.")
            notes = st.text_area("Lead notes", entry.get("notes", ""),
                                 key=f"client_notes_{key}")
            if st.button("Save notes", key=f"client_notes_save_{key}"):
                core.update_status_fields(status, key, {"notes": notes})
                C.push_toast("Lead notes saved")
                st.rerun()
            if stage != "dismissed":
                choices = [item for item in ("contacted", "replied", "won",
                                             "dismissed") if item != stage]
                outcome = st.selectbox("Update outcome", choices,
                                       key=f"client_outcome_{key}")
                if st.button("Save outcome", key=f"client_outcome_save_{key}"):
                    C.set_stage(status, key, outcome, notes,
                                lead_label=row.get("company", ""))
                    st.rerun()
            actions = entry.get("activity") or []
            if actions:
                with st.expander("Recorded manual sends"):
                    for action in reversed(actions):
                        st.caption(f"{action.get('at', '')} · "
                                   f"{action.get('contact', '')} · "
                                   f"{action.get('channel', '')}")
                        st.code(action.get("message", ""), language=None)

if view == "Outcomes & report":
    st.subheader("Attended meetings and report")
    meetings = D.load_meetings()
    client_meetings = {key: value for key, value in meetings.items()
                       if value.get("client") == client}
    if client_leads:
        st.caption("Record each actual meeting once, even if it came from "
                   "several job rows. The first meeting defaults to free.")
        existing = st.selectbox("Meeting record", ["New meeting"] +
                                list(client_meetings),
                                format_func=lambda key: ("New meeting" if key ==
                                                         "New meeting" else
                                                         client_meetings[key].get(
                                                             "meeting_id", key)),
                                key="client_meeting_choice")
        current_meeting = client_meetings.get(existing) or {}
        lead_options = {core.lead_key(row): row for row in client_leads}
        with st.form("record_meeting"):
            c1, c2 = st.columns(2)
            with c1:
                meeting_id = st.text_input(
                    "Meeting ID or calendar reference",
                    current_meeting.get("meeting_id", ""),
                    disabled=existing != "New meeting")
                held_on = st.date_input(
                    "Meeting date", value=date.fromisoformat(
                        current_meeting.get("held_on") or date.today().isoformat()),
                    max_value=date.today())
                attendee = st.text_input("Person who attended",
                                         current_meeting.get("attendee", ""))
            with c2:
                linked = st.multiselect(
                    "Related client leads", list(lead_options),
                    default=[key for key in current_meeting.get("lead_keys", [])
                             if key in lead_options],
                    format_func=lambda key: (
                        f"{lead_options[key].get('company')} — "
                        f"{lead_options[key].get('title')}"))
                first_default = D.default_chargeable(client, meetings)
                chargeable = st.selectbox(
                    "Charge status", ["Free", "Chargeable"],
                    index=1 if current_meeting.get("chargeable", first_default)
                    else 0)
                meeting_notes = st.text_area("Meeting notes",
                                             current_meeting.get("notes", ""))
            record = st.form_submit_button("Save attended meeting", type="primary")
        if record:
            try:
                D.save_meeting(
                    client, meeting_id, held_on.isoformat(), attendee, linked,
                    chargeable=chargeable == "Chargeable", notes=meeting_notes,
                    leads=client_leads,
                    expected=(revision(current_meeting) if current_meeting
                              else D.UNSET))
            except (ValueError, ConflictError, DataFileError) as error:
                st.error(str(error))
            else:
                C.push_toast("Attended meeting saved")
                st.rerun()
    else:
        st.info("Add client leads before recording their meeting outcomes.")

    st.divider()
    st.subheader("Client report")
    stage_filter = st.selectbox("Lead stage", ["All", "new", "contacted",
                                              "replied", "won", "dismissed"],
                                key="client_report_stage")
    search = st.text_input("Search company or role", key="client_report_search")
    selected_leads = [row for row in client_leads
                      if (stage_filter == "All" or core.effective_status(
                          status.get(core.lead_key(row))) == stage_filter)
                      and search.casefold() in (row.get("company", "") + " " +
                                                row.get("title", "")).casefold()]
    rate = (int(raw_config["meeting_rate"])
            if raw_config.get("meeting_rate") is not None else None)
    rows = D.report_rows(client, selected_leads, status, meetings, rate)
    selected_meetings, estimate = D.meeting_summary(
        client, meetings, rate,
        selected_lead_keys={core.lead_key(row) for row in selected_leads})
    m1, m2, m3 = st.columns(3)
    m1.metric("Selected leads", len(selected_leads))
    m2.metric("Attended meetings", len(selected_meetings))
    m3.metric("Recorded estimate", f"${estimate}" if estimate is not None
              else "Rate needed")
    if rate is None:
        st.warning("Save this client's agreed meeting rate in Profile & "
                   "matches before reporting an estimate.")
    st.caption("Only attended meetings marked chargeable contribute to the "
               "recorded estimate. This is not an invoice or confirmed payment.")
    st.dataframe(rows, hide_index=True, width="stretch",
                 column_config={"source_url":
                                st.column_config.LinkColumn("Source job")})
    st.download_button("Export selected report CSV", D.report_csv(rows),
                       file_name=f"client_report_{client}_{date.today()}.csv",
                       mime="text/csv", disabled=not rows)
