#!/usr/bin/env python3
"""Agentic AI Leads: four-screen Streamlit application."""

import sys

import streamlit as st
from streamlit import runtime

import auth
import common as C
import core


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
    C.render_sidebar_stats()
    auth.logout_button()
    nav.run()


if runtime.exists():
    main()
elif __name__ == "__main__":
    print("This is a Streamlit app — start it with: streamlit run app.py")
    sys.exit(0)
