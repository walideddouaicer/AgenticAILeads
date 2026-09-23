"""Prospect criteria, messages, integration state and operational history."""

import streamlit as st

import common as C
import core
import runner
from contacts import load_env_key
from storage import DataFileError, read_json


C.page_header("⚙️ Settings", "settings")
st.caption("Save each form before leaving this screen; unsaved changes are discarded.")

st.subheader("Prospect criteria")
icp = core.load_icp()
with st.form("prospect_criteria"):
    c1, c2 = st.columns(2)
    with c1:
        min_size = int(st.number_input("Minimum employees", 1, 500,
                                       value=int(icp["size_min"])))
        titles = st.text_area("Decision-maker titles",
                              ", ".join(icp["titles"]))
        countries = st.text_area("Countries", ", ".join(icp["countries"]))
    with c2:
        max_size = int(st.number_input("Maximum employees", 1, 500,
                                       value=int(icp["size_max"])))
        keywords = st.text_area("Agency keywords",
                                ", ".join(icp["keywords"]))
    if st.form_submit_button("Save criteria"):
        if min_size > max_size:
            st.error("Minimum employees must be no higher than maximum.")
        else:
            core.save_icp({"titles": core.parse_csv_line(titles),
                        "countries": core.parse_csv_line(countries),
                        "keywords": core.parse_csv_line(keywords),
                        "size_min": min_size, "size_max": max_size})
            st.success("Criteria saved for future searches. Existing "
                       "prospects stay visible and are flagged if off target.")

st.divider()
st.subheader("Prospect messages")
cfg = core.load_prospects_config()
st.caption("Available fields: {first_name}, {agency}, {hook}, "
           "{person_title}, {location}, {sample_count}, {contact_count}, "
           "{sample_summary}. Sample fields require approved current rows.")
with st.form("prospect_templates"):
    initial = st.text_area("First message", cfg["dm_template"], height=125)
    followup1 = st.text_area("First follow-up", cfg["followup_template"],
                              height=105)
    followup2 = st.text_area("Final follow-up", cfg["followup2_template"],
                              height=105)
    if st.form_submit_button("Save templates"):
        core.save_prospects_config({"dm_template": initial,
                                 "followup_template": followup1,
                                 "followup2_template": followup2})
        st.success("Templates saved. Review each final draft before sending.")

st.divider()
st.subheader("Data and integrations")
st.caption(f"Data directory: {core.DATA_DIR}")
st.caption(f"Local environment file: {core.ENV_FILE}")
apollo_available = bool(load_env_key("APOLLO_API_KEY", core.ENV_FILE))
st.write("Apollo prospect lookup: " +
         ("configured; access unverified" if apollo_available
          else "not configured; CSV and manual entry remain available"))
st.caption("No provider lookup runs when this page opens.")

with st.expander("Backups and restore"):
    backups = sorted((core.DATA_DIR / "backups").glob("*.json"), reverse=True)
    if not backups:
        st.info("No history backups yet. A collection run creates them "
                "when history files exist.")
    else:
        selected = st.selectbox("History backup", backups,
                                format_func=lambda path: path.name)
        st.caption("Restoring replaces the matching history file. The current "
                   "file is copied to a pre-restore backup first.")
        confirmation = st.text_input("Type RESTORE to confirm",
                                     key="restore_confirmation")
        if st.button("Restore selected history", disabled=confirmation != "RESTORE"):
            if selected.name.startswith("job_history_"):
                destination = core.DATA_DIR / "job_history.json"
            elif selected.name.startswith("mandate_history_"):
                destination = core.DATA_DIR / "mandate_history.json"
            else:
                st.error("This backup name does not identify a history file.")
                destination = None
            if destination:
                try:
                    runner.restore_history_backup(selected, destination)
                except (DataFileError, OSError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.success(f"Restored {destination.name}. Reload Signals "
                               "to inspect the recovered history.")

with st.expander("Recent runs"):
    runs = read_json(runner.RUN_STATUS, default={}, expected_type=dict)
    entries = sorted((runs.get("runs") or {}).values(),
                     key=lambda item: item.get("started_at", ""), reverse=True)
    if not entries:
        st.caption("No collector runs have been recorded yet.")
    for entry in entries[:10]:
        st.write(f"{entry.get('label') or entry.get('command') or 'Run'} — "
                 f"{entry.get('status', 'unknown')} · "
                 f"{entry.get('started_at', 'unknown time')}")
    if entries:
        with st.expander("Technical run details"):
            st.json(entries[:10])

with st.expander("How to use the app"):
    st.markdown("1. **Prospects:** import or find agencies, review evidence "
                "and samples, edit the final message, then record a manual send.\n"
                "2. **Clients:** create a client profile and work their "
                "matched companies and outcomes.\n"
                "3. **Signals:** refresh and inspect employer and agency boards.\n"
                "4. **Settings:** adjust criteria and templates, then review "
                "run and backup status.")
