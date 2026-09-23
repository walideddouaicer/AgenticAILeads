"""Client workspace. Existing delivery tools remain available during Phase 4."""

import runpy

import streamlit as st

import common as C


profiles = [name for name in C.list_profiles() if name != "default"]
leads, _ = C.load_leads()
client_ids = sorted(set(profiles) | {row.get("client", "") for row in leads
                                    if row.get("client") not in ("", "default")})

if not client_ids and st.session_state.get("client_section") != "Profile":
    C.page_header("👤 Clients", "clients")
    st.info("No paying clients are set up yet. Create a client profile to "
            "choose their target roles, market, outreach channel and terms.")
    if st.button("Create client", type="primary", key="create_client"):
        st.session_state["client_section"] = "Profile"
        st.session_state["cfg_choice"] = "➕ New client"
        st.rerun()
    st.stop()

sections = {
    "Profile": "configs.py",
    "Matched companies": "queue.py",
    "Outreach": "inbox.py",
    "Outcomes & report": "client_report.py",
}
if "client_section" not in st.session_state:
    st.session_state["client_section"] = "Matched companies"
section = st.radio("Client workflow", list(sections), horizontal=True,
                   key="client_section")
if not client_ids:
    st.caption("Create a real client profile; the default prospecting filter "
               "is not a paying client.")
runpy.run_path(str(C.HERE / "views" / sections[section]), run_name="__main__")
