#!/usr/bin/env python3
"""
Agentic AI Leads — Local CRM (multipage Streamlit app).

Entry point only: page navigation, theme/CSS, and the always-on sidebar
stats. All page code lives in views/, all shared helpers in common.py.

Pages (default — 6 entries):
  Home            — today's metrics, funnel, billable summary, setup
                    checklist, engine run buttons
  Work
    Prospects      — OUR pipeline: agency decision-makers (prospector),
                     mandate hooks, per-prospect mini-list door-opener
    Mini-List      — free gift list for prospect agencies (CSV + HTML)
  System
    Watchlist      — boards swept nightly: search, bulk add/remove
    Live Jobs      — every tracked job, searchable, CSV export
    Help           — the operating guide

Hidden behind AAL_SHOW_CLIENT_PAGES=1 (Loop A — serving paying clients):
  Clients
    Outreach Queue — follow-ups due + top-N ranked new sends, focus mode,
                     one-click copy / mark contacted, double-DM guard
    Daily Inbox    — all signals -> DMs -> stages (contacted/replied/won),
                     notes, dismiss, follow-up timers, 25/page
    Client Pages   — per-client leads, filters, metrics, CSV export
    Client Configs — profiles, live tester, dry-run, backfill

These four are built and tested; they're hidden because they serve a
customer we don't have yet, not because they're broken. The views and
their tests stay — only the sidebar entries are gated.

Run:  streamlit run app.py
"""

import os
import sys

import streamlit as st
from streamlit import runtime

import auth
import common as C

# Loop A — finding and delivering leads for PAYING clients — is built and
# works, but it serves a customer we don't have yet. Four of the ten pages
# were for it, which made the one page that actually earns money (Prospects)
# one entry in a crowded sidebar. They're hidden, not deleted: set
# AAL_SHOW_CLIENT_PAGES=1 the week client #1 signs.
SHOW_CLIENT_PAGES = os.environ.get("AAL_SHOW_CLIENT_PAGES") == "1"


def main():
    st.set_page_config(page_title="Agentic AI Leads — CRM", page_icon="⚡",
                       layout="wide")
    C.CONFIG_DIR.mkdir(exist_ok=True)
    C.inject_css()
    C.flush_toasts()
    auth.require_login()  # halts here with a login form until authenticated

    # Prospects is the default landing page on purpose: opening the app
    # should present the next thing to do, not a choice of places to look.
    # Its first tab IS the daily queue — Today used to be a separate page,
    # but it was the same data at a different zoom level.
    pages = {
        "": [
            st.Page("views/prospects.py", title="Prospects",
                    icon="🧲", default=True),
            st.Page("views/signals.py", title="Signals", icon="📡"),
            st.Page("views/home.py", title="Home", icon="🏠"),
        ],
    }
    if SHOW_CLIENT_PAGES:
        # Loop A — serving paying clients. Hidden until there is one.
        pages["Clients"] = [
            st.Page("views/queue.py", title="Outreach Queue", icon="🚀"),
            st.Page("views/inbox.py", title="Daily Inbox", icon="📥"),
            st.Page("views/clients.py", title="Client Pages", icon="👤"),
            st.Page("views/configs.py", title="Client Configs", icon="⚙️"),
        ]
    pages["System"] = [
        st.Page("views/minilist.py", title="Mini-List (ad-hoc)", icon="🎁"),
        st.Page("views/help.py", title="Help", icon="📖"),
    ]
    nav = st.navigation(pages)
    C.render_sidebar_stats()
    auth.logout_button()
    nav.run()


if runtime.exists():
    main()
elif __name__ == "__main__":
    # bare `python app.py` — not an error, just the wrong launcher
    print("This is a Streamlit app — start it with:  streamlit run app.py")
    sys.exit(0)
