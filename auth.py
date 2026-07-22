"""Simple environment-variable login gate for the CRM.

Credentials come from environment variables — set them in Coolify (or any
host), never in the repo:

    APP_USERS      "alice:secret1,bob:secret2"     (preferred, multi-user)
  or, for a single user:
    APP_USERNAME   "alice"
    APP_PASSWORD   "secret1"

Login state lives in ``st.session_state``: it survives Streamlit reruns (so
you stay logged in while clicking around) and resets when the browser tab is
closed. No passwords are written to disk. This module is Streamlit-only and
imports nothing from ``common`` so it can be reasoned about in isolation.
"""

from __future__ import annotations

import hmac
import os

import streamlit as st


def _configured_users() -> dict:
    """Parse the credential env vars into a {username: password} mapping."""
    users: dict = {}
    raw = os.environ.get("APP_USERS", "").strip()
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                name, pw = pair.split(":", 1)
                if name.strip() and pw:
                    users[name.strip()] = pw
    else:
        name = os.environ.get("APP_USERNAME", "").strip()
        pw = os.environ.get("APP_PASSWORD", "")
        if name and pw:
            users[name] = pw
    return users


def _password_ok(username: str, password: str, users: dict) -> bool:
    """Constant-time credential check."""
    expected = users.get(username)
    if expected is None:
        # Compare against a dummy so timing doesn't reveal valid usernames.
        hmac.compare_digest(password, password)
        return False
    return hmac.compare_digest(password, expected)


def require_login() -> None:
    """Render a login form and halt the app until the user authenticates.

    Call this once, early in ``main()`` (after ``set_page_config``). It is a
    no-op once the user is signed in.
    """
    # Test-only escape hatch (used by tests/test_pages.py). MUST NEVER be set
    # in production — it disables the login gate entirely.
    if os.environ.get("AAL_DISABLE_AUTH") == "1":
        return
    if st.session_state.get("_authed"):
        return

    users = _configured_users()
    if not users:
        st.error(
            "Login isn't configured. Set the APP_USERNAME and APP_PASSWORD "
            "(or APP_USERS) environment variables, then reload."
        )
        st.stop()

    st.markdown("## Agentic AI Leads — Sign in")
    with st.form("aal_login", clear_on_submit=False):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        if _password_ok(username.strip(), password, users):
            st.session_state["_authed"] = True
            st.session_state["_user"] = username.strip()
            st.rerun()
        else:
            st.error("Incorrect username or password.")

    st.stop()


def logout_button() -> None:
    """Render a sidebar 'Log out' control. Call after ``require_login``."""
    if not st.session_state.get("_authed"):
        return
    with st.sidebar:
        st.caption(f"Signed in as {st.session_state.get('_user', '')}")
        if st.button("Log out", width="stretch"):
            for key in ("_authed", "_user"):
                st.session_state.pop(key, None)
            st.rerun()
