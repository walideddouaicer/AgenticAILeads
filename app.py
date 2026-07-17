#!/usr/bin/env python3
"""
Agentic AI Leads — Local CRM (multipage Streamlit app).

Entry point only: page navigation, theme/CSS, and the always-on sidebar
stats. All page code lives in views/, all shared helpers in common.py.

Pages:
  Home            — today's metrics, funnel, billable summary, setup
                    checklist, engine run buttons
  Work
    Outreach Queue — follow-ups due + top-N ranked new sends, focus mode,
                     one-click copy / mark contacted, double-DM guard
    Daily Inbox    — all signals -> DMs -> stages (contacted/replied/won),
                     notes, dismiss, follow-up timers, 25/page
    Prospects      — OUR pipeline: agency decision-makers (prospector),
                     mandate hooks, per-prospect mini-list door-opener
  Clients
    Client Pages   — per-client leads, filters, metrics, CSV export
    Mini-List      — free gift list for prospect agencies (CSV + HTML)
  System
    Watchlist      — boards swept nightly: search, bulk add/remove
    Live Jobs      — every tracked job, searchable, CSV export
    Client Configs — profiles, live tester, dry-run, backfill
    Help           — the full operating guide

Run:  streamlit run app.py
"""

import sys

import streamlit as st
from streamlit import runtime

import common as C


def main():
    st.set_page_config(page_title="Agentic AI Leads — CRM", page_icon="⚡",
                       layout="wide")
    C.CONFIG_DIR.mkdir(exist_ok=True)
    C.inject_css()
    C.flush_toasts()

    pages = {
        "": [
            st.Page("views/home.py", title="Home", icon="🏠", default=True),
        ],
        "Work": [
            st.Page("views/queue.py", title="Outreach Queue", icon="🚀"),
            st.Page("views/inbox.py", title="Daily Inbox", icon="📥"),
            st.Page("views/prospects.py", title="Prospects", icon="🧲"),
        ],
        "Clients": [
            st.Page("views/clients.py", title="Client Pages", icon="👤"),
            st.Page("views/minilist.py", title="Mini-List", icon="🎁"),
        ],
        "System": [
            st.Page("views/watchlist.py", title="Watchlist", icon="📡"),
            st.Page("views/livejobs.py", title="Live Jobs", icon="🗂"),
            st.Page("views/configs.py", title="Client Configs", icon="⚙️"),
            st.Page("views/help.py", title="Help", icon="📖"),
        ],
    }
    nav = st.navigation(pages)
    C.render_sidebar_stats()
    nav.run()


if runtime.exists():
    main()
elif __name__ == "__main__":
    # bare `python app.py` — not an error, just the wrong launcher
    print("This is a Streamlit app — start it with:  streamlit run app.py")
    sys.exit(0)
