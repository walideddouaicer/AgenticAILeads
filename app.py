#!/usr/bin/env python3
"""Agentic AI Leads: four-screen Streamlit application."""

import sys

import streamlit as st
from streamlit import runtime

import auth
import common as C
import core
from storage import DataFileError

RECOVERY_STEPS = (
    "Nothing was overwritten. Stop the app, then follow “Back up and "
    "recover” in the README: restore your latest operational backup into a "
    "new folder, check it, and start the app with `AAL_DATA_DIR` set to that "
    "folder. Keep the damaged file for inspection.")


def main():
    st.set_page_config(page_title="Agentic AI Leads", page_icon="⚡",
                       layout="wide")
    core.CONFIG_DIR.mkdir(exist_ok=True)
    C.inject_css()
    C.flush_toasts()
    auth.require_login()

    pages = [
        st.Page("views/prospects.py", title="Prospects", icon="🧲", default=True),
        st.Page("views/clients.py", title="Clients", icon="👤"),
        st.Page("views/signals.py", title="Signals", icon="📡"),
        st.Page("views/settings.py", title="Settings", icon="⚙️"),
    ]
    nav = st.navigation(pages)
    try:
        C.render_sidebar_stats()
    except DataFileError:
        st.sidebar.warning("Counts unavailable: a data file is damaged.")
    auth.logout_button()
    try:
        nav.run()
    except DataFileError as error:
        # A damaged business file must stop work with a recovery step,
        # never a traceback or a silently empty screen.
        st.error(str(error))
        st.info(RECOVERY_STEPS)


if runtime.exists():
    main()
elif __name__ == "__main__":
    print("This is a Streamlit app — start it with: streamlit run app.py")
    sys.exit(0)
