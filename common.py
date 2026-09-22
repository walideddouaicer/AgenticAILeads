#!/usr/bin/env python3
"""
Agentic AI Leads — Streamlit glue for the CRM.

This module is now the UI half only: theme/CSS, toasts and undo, the
manual-contact editor, the engine subprocess runner, the sidebar and page
headers. All domain logic — paths, constants, the lead and prospect
stores, the stage machine, follow-up rules, DM rendering, the watchlist —
moved to core.py, which imports no Streamlit and can therefore be used by
scheduled jobs and CLI tools.

Every public name in core is re-exported below, so pages keep calling
``C.lead_key(...)``, ``C.load_leads()``, ``C.SIGNALS_CSV`` unchanged. If
you are adding logic that does not touch ``st.*``, put it in core.py.

Stage changes still go through ``set_stage`` / ``mark_followed_up`` HERE,
not through core's ``apply_stage`` / ``apply_followup`` — the wrappers add
the undo snapshot and the toast. Pages must never write status files
directly.

AAL_DATA_DIR env var overrides the data directory (tests, container
deploy); default is the repo root.
"""

import csv
import json
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

import delivery
import runner as engine_runner
from storage import LockBusy, lock_info

from core import (AGENCY_WATCHLIST_FILE, ATS_URL_PATTERNS, CHANNELS,
    CHANNEL_COLORS, CONFIG_DIR, CONTACTS_CACHE, DAILY_QUEUE_CAP,
    DATA_DIR, DEFAULT_EMAIL_SUBJECT, DEFAULT_EMAIL_TEMPLATE,
    DEFAULT_FOLLOWUP2_TEMPLATE, DEFAULT_FOLLOWUP_TEMPLATE,
    DEFAULT_MEETING_RATE, DEFAULT_PROSPECT_DM, DEFAULT_PROSPECT_FOLLOWUP,
    DEFAULT_PROSPECT_FOLLOWUP2, DEFAULT_TEMPLATE, DISCOVERY_STATE,
    ENRICHED_CSV, ENV_FILE, FOLLOWUP_DAYS, FRESH_DAYS, HERE,
    INBOX_STATUS, LEGACY_MAP, MAX_FOLLOWUPS, PLATFORMS, PROSPECTS_CONFIG,
    PROSPECTS_CSV, PROSPECTS_STATUS, PROSPECT_SIGNALS_CSV,
    PROSPECT_STAGES, SIGNALS_CSV, STAGES, STAGE_COLORS, STATE_FILE,
    WATCHLIST_FILE, active_email_adapter, all_clients, all_tracked_jobs,
    apply_followup, apply_replies, apply_stage, delivery_adapters,
    derive_niche_keywords, display_hook, effective_status, followup_due,
    lead_key, list_profiles, load_contacts_cache, load_leads,
    load_profile, load_prospect_signals, load_prospects,
    load_prospects_config, load_status, load_watchlist, mailto_link,
    mandates_for_agency, parse_board_url, parse_csv_line, parse_weights,
    prospect_key, render_dm, render_prospect_dm, save_contacts_cache,
    save_prospects_config, save_status, save_watchlist,
    tracked_job_counts, weights_to_text)

# Safety net for the list above. That explicit import is good documentation
# — you can read it and see the public surface — but it goes stale the
# moment core gains a function, and the failure mode is an AttributeError
# deep inside a page at runtime (this happened with market_tokens).
#
# So: anything public in core that isn't already bound here gets bound
# automatically. It only FILLS GAPS — names common defines itself (set_stage,
# mark_followed_up) are defined later in this file and still win, because
# this runs before those definitions.
import types as _types  # noqa: E402

import core as _core  # noqa: E402

for _name in dir(_core):
    if _name.startswith("_") or _name in globals():
        continue
    _val = getattr(_core, _name)
    if isinstance(_val, _types.ModuleType):
        continue        # don't re-export core's own imports (re, hashlib…)
    globals()[_name] = _val
del _name, _val

# --- presentation-only constants (domain constants live in core) ---
# App-wide stage colors (also the badge palette; keep in sync with Help page)
STAGE_COLORS = {
    "new": "#D8FF3E",
    "contacted": "#4A90D9",
    "replied": "#F5A623",
    "won": "#3CB371",
    "dismissed": "#999999",
    # prospect-only stages
    "call_booked": "#7A5FD0",
    "client_won": "#3CB371",
}
# light backgrounds get dark text, the rest white
_BADGE_FG = {"new": "#111111", "replied": "#111111"}

# outreach channel (per-client) — badge palette (amber = the theme's
# highlight color; ink = both channels at once)
CHANNELS = ["linkedin", "email", "both"]
CHANNEL_COLORS = {"linkedin": "#4A90D9", "email": "#F2B705", "both": "#0A0A0A"}
_CHANNEL_FG = {"email": "#111111"}


# ============================ look & feel ============================

# Editorial-brutalist skin (STITCH design system): Archivo display type,
# JetBrains Mono eyebrows, paper/ink palette, coral-red accent, square
# corners, 1.5px ink borders, hard offset shadows, paper grain.
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800;900&family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,700;1,9..144,400&family=JetBrains+Mono:wght@400;500;700&display=swap');

:root {
    --aal-ink: #0A0A0A;
    --aal-paper: #F4F2EC;
    --aal-paper-2: #EAE7DD;
    --aal-line: rgba(10,10,10,0.12);
    --aal-line-strong: rgba(10,10,10,0.28);
    --aal-muted: rgba(10,10,10,0.56);
    --aal-accent: #D14829;
}

/* paper grain over the main canvas */
.stApp::after {
    content: '';
    position: fixed; inset: 0;
    z-index: 999990;
    pointer-events: none;
    opacity: 0.05;
    mix-blend-mode: multiply;
    background-image: url("data:image/svg+xml;utf8,<svg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.9 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
}

/* display headings: heavy, tight */
h1, h2, h3 {
    font-family: 'Archivo', 'Helvetica Neue', sans-serif !important;
    font-weight: 900 !important;
    letter-spacing: -0.03em !important;
    line-height: 1.02 !important;
}
h1 { border-bottom: 1.5px solid var(--aal-ink); padding-bottom: 10px; }
[data-testid="stSidebar"] h1 { border-bottom: none; }

/* mono eyebrows: captions + metric labels */
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.72rem !important;
    letter-spacing: 0.04em;
}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.68rem !important;
    letter-spacing: 0.14em !important;
    text-transform: uppercase;
}
[data-testid="stMetricValue"] {
    font-family: 'Archivo', sans-serif !important;
    font-weight: 900 !important;
    letter-spacing: -0.03em;
}

/* buttons: square, uppercase display type, hard-shadow hover */
.stButton button, .stDownloadButton button, [data-testid="stPopover"] > button {
    border-radius: 0 !important;
    font-family: 'Archivo', sans-serif !important;
    font-weight: 700 !important;
    font-size: 12px !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase;
    transition: transform 0.08s, box-shadow 0.15s, background 0.15s !important;
}
.stButton button[kind="secondary"], .stDownloadButton button {
    border: 1.5px solid var(--aal-ink) !important;
    color: var(--aal-ink);
    background: transparent;
}
.stButton button[kind="secondary"]:hover:enabled,
.stDownloadButton button:hover:enabled {
    background: var(--aal-ink) !important;
    color: var(--aal-paper) !important;
    box-shadow: 4px 4px 0 var(--aal-accent);
}
.stButton button[kind="primary"] {
    border: 1.5px solid var(--aal-accent) !important;
    color: #F4F2EC !important;
}
.stButton button[kind="primary"]:hover:enabled {
    box-shadow: 4px 4px 0 var(--aal-ink);
}
.stButton button:active:enabled { transform: translate(2px, 2px); box-shadow: none !important; }

/* expanders: panels with ink borders */
[data-testid="stExpander"] {
    border: 1.5px solid var(--aal-ink) !important;
    border-radius: 0 !important;
    background: var(--aal-paper);
}
[data-testid="stExpander"] summary {
    font-family: 'Archivo', sans-serif !important;
    font-weight: 700 !important;
    letter-spacing: -0.01em;
}
[data-testid="stSidebar"] [data-testid="stExpander"] { border-color: rgba(244,242,236,0.25) !important; }

/* code blocks (DM drafts, engine logs) */
[data-testid="stCode"] pre, .stCode pre {
    background: var(--aal-paper-2) !important;
    border: 1px solid var(--aal-line-strong) !important;
    border-radius: 0 !important;
}

/* sidebar nav: uppercase display labels, mono group headers */
[data-testid="stSidebarNav"] a span {
    font-family: 'Archivo', sans-serif !important;
    font-weight: 700 !important;
    font-size: 12px !important;
    letter-spacing: 0.1em !important;
    text-transform: uppercase;
}
[data-testid="stSidebarNav"] header,
[data-testid="stSidebarNavSeparator"] + div,
[data-testid="stNavSectionHeader"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.62rem !important;
    letter-spacing: 0.22em !important;
    text-transform: uppercase;
    color: rgba(244,242,236,0.45) !important;
}

/* stage / channel badges: square rubberstamp tags */
.aal-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 0;
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    margin-right: 5px;
    white-space: nowrap;
    font-family: 'JetBrains Mono', monospace;
    border: 1px solid rgba(10,10,10,0.25);
}

/* funnel: flat bars on a paper-2 track, mono labels */
.aal-funnel-row { display: flex; align-items: center; margin: 5px 0; }
.aal-funnel-label { width: 108px; font-family: 'JetBrains Mono', monospace;
                    font-size: 0.68rem; letter-spacing: 0.1em;
                    text-transform: uppercase; color: var(--aal-muted); }
.aal-funnel-track { flex: 1; background: var(--aal-paper-2);
                    border: 1px solid var(--aal-line); }
.aal-funnel-bar { height: 20px; min-width: 2px; }
.aal-funnel-count { width: 56px; text-align: right;
                    font-family: 'Archivo', sans-serif; font-size: 0.9rem;
                    font-weight: 900; color: var(--aal-ink); padding-left: 8px; }
</style>
"""


def inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)


def stage_badge(stage: str, due: bool = False) -> str:
    """Colored HTML badge for a stage; render with unsafe_allow_html=True."""
    bg = STAGE_COLORS.get(stage, "#999999")
    fg = _BADGE_FG.get(stage, "#ffffff")
    label = stage.replace("_", " ")
    out = f'<span class="aal-badge" style="background:{bg};color:{fg}">{label}</span>'
    if due:
        out += ('<span class="aal-badge" style="background:#111111;'
                'color:#D8FF3E">follow-up due</span>')
    return out


def channel_badge(channel: str) -> str:
    """Colored HTML badge for a client's outreach channel."""
    ch = (channel or "linkedin").lower()
    bg = CHANNEL_COLORS.get(ch, "#999999")
    fg = _CHANNEL_FG.get(ch, "#ffffff")
    icon = {"linkedin": "in", "email": "✉", "both": "in+✉"}.get(ch, "")
    return (f'<span class="aal-badge" style="background:{bg};color:{fg}">'
            f'{icon} {ch}</span>')


# ============================ toasts & undo ============================

def push_toast(msg: str, icon: str = "✅"):
    """Queue a toast; it survives st.rerun() (shown by flush_toasts())."""
    st.session_state.setdefault("_toasts", []).append((msg, icon))


def flush_toasts():
    """Called once per run from app.py before the page renders."""
    for msg, icon in st.session_state.pop("_toasts", []):
        st.toast(msg, icon=icon)


def _snapshot_undo(status: dict, key: str, label: str, path=None):
    prev = status.get(key)
    st.session_state["_undo"] = {
        "key": key,
        "prev": dict(prev) if isinstance(prev, dict) else prev,
        "label": label,
        "path": str(path or INBOX_STATUS),
        "expected": None,
    }


def undo_available():
    return st.session_state.get("_undo")


def undo_last():
    """Restore the status entry saved before the last stage change
    (works for both the lead and the prospect status files)."""
    u = st.session_state.pop("_undo", None)
    if not u:
        return None
    path = Path(u.get("path") or INBOX_STATUS)
    status = load_status(path)
    try:
        restore_status_record(status, u["key"], u["prev"], path,
                              expected=u["expected"])
    except ConflictError:
        st.session_state["_undo"] = u
        push_toast("Undo stopped: this record changed in another session. "
                   "Reload before trying again.", "⚠️")
        return None
    return u["label"]


def render_undo(key: str = "undo_btn"):
    """'Undo last action' button — appears only when there is one to undo."""
    u = undo_available()
    if not u:
        return
    if st.button(f"↩ Undo: {u['label']}", key=key,
                 help="Restores the previous stage/notes/follow-up state of "
                      "the last lead you changed."):
        label = undo_last()
        push_toast(f"Undone: {label}", "↩")
        st.rerun()



def render_contact_editor(row, wkey):
    """Add / edit / remove a manually-found contact for this lead's company.

    Writes enriched_contacts.json in the exact structure the enrichment
    engine uses, so load_leads() picks the contact up on every lead of the
    company automatically. Rendered as a popover because the lead cards are
    already expanders (Streamlit forbids nesting them)."""
    company = (row.get("company") or "").strip()
    if not company:
        return
    existing_name = (row.get("contact1_name") or "").strip()
    editing = bool(existing_name)
    prefill_email = row.get("contact1_email", "") if editing else ""
    if "locked" in prefill_email.lower():
        prefill_email = ""

    label = "✏️ Edit / remove contact" if editing else "➕ Add contact manually"
    with st.popover(label,
                    help="Contacts are stored per COMPANY in "
                         "enriched_contacts.json — saving here updates "
                         "every lead of this company."):
        st.caption(f"Company: **{company}** — find the decision-maker on "
                   "LinkedIn (head of talent / TA / founder) and paste "
                   "their details.")
        name = st.text_input("Name", existing_name, key=f"mc_n_{wkey}",
                             help="Full name as shown on LinkedIn.")
        title = st.text_input("Title",
                              row.get("contact1_title", "") if editing else "",
                              key=f"mc_t_{wkey}",
                              help="Their job title, e.g. 'Head of Talent "
                                   "Acquisition'.")
        linkedin = st.text_input("LinkedIn URL",
                                 row.get("contact1_linkedin", "") if editing
                                 else "",
                                 key=f"mc_l_{wkey}",
                                 placeholder="https://www.linkedin.com/in/…",
                                 help="Their profile URL — must contain "
                                      "linkedin.com/.")
        email = st.text_input("Email (optional)", prefill_email,
                              key=f"mc_e_{wkey}",
                              help="Only if you actually have it — LinkedIn "
                                   "outreach doesn't need one.")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("💾 Save contact", key=f"mc_s_{wkey}",
                         type="primary", width="stretch",
                         help="Saves to enriched_contacts.json; the contact "
                              "appears on all of this company's leads."):
                errs = []
                if not name.strip():
                    errs.append("Name is required.")
                if not title.strip():
                    errs.append("Title is required.")
                if "linkedin.com/" not in linkedin.strip().lower():
                    errs.append("LinkedIn URL must contain linkedin.com/.")
                if errs:
                    st.error(" ".join(errs))
                else:
                    new = {"name": name.strip(), "title": title.strip(),
                           "linkedin": linkedin.strip(),
                           "email": email.strip()}

                    def save(entries):
                        entries = [c for c in entries if isinstance(c, dict)]
                        idx = next(
                            (i for i, c in enumerate(entries)
                             if (c.get("name", "").strip().lower()
                                 == existing_name.lower())),
                            None) if editing else None
                        if idx is None:
                            entries.append(new)
                        else:
                            entries[idx] = new
                        return entries

                    update_company_contacts(company, save)
                    push_toast(f"Contact saved for {company}", "👤")
                    st.rerun()
        with c2:
            if editing and st.button(
                    "🗑 Remove contact", key=f"mc_r_{wkey}", width="stretch",
                    help="Deletes this contact from enriched_contacts.json "
                         "(for every lead of this company)."):
                update_company_contacts(
                    company,
                    lambda entries: [
                        c for c in entries if isinstance(c, dict)
                        and c.get("name", "").strip().lower()
                        != existing_name.lower()])
                push_toast(f"Contact removed from {company}", "🗑")
                st.rerun()



def mark_followed_up(status, key, lead_label: str = "", path=None):
    """Reset the follow-up timer and count the touch (day-3 -> day-6 -> stop).

    UI wrapper over core.apply_followup: adds the undo snapshot and toast.
    """
    _snapshot_undo(status, key, f"follow-up on {lead_label}" if lead_label
                   else "follow-up", path)
    e = apply_followup(status, key, path)
    st.session_state["_undo"]["expected"] = status_revision(status, key)
    push_toast(f"Follow-up #{e['fu']} logged"
               + (f" — {lead_label}" if lead_label else ""), "🔁")


def set_stage(status, key, stage, notes=None, lead_label: str = "", path=None):
    """UI wrapper over core.apply_stage: adds the undo snapshot and toast."""
    _snapshot_undo(status, key, f"{lead_label or 'lead'} → {stage}", path)
    apply_stage(status, key, stage, notes, path)
    st.session_state["_undo"]["expected"] = status_revision(status, key)
    icons = {"contacted": "✅", "replied": "💬", "won": "🏆", "dismissed": "🚫",
             "call_booked": "📅", "client_won": "🎉"}
    push_toast((f"{lead_label} → {stage.replace('_', ' ')}" if lead_label
                else f"Stage → {stage.replace('_', ' ')}"),
               icons.get(stage, "✅"))


# ============================ engine runner ============================
# Runs signal/enrichment/discovery engines as subprocesses of the app,
# streaming their stdout into the page. A lockfile plus the session job
# object guarantee that two engines never run concurrently.

ENGINE_LOCK = engine_runner.ENGINE_LOCK

ENGINES = {
    "sweep": {
        "label": "Company board sweep (signal_engine.py)",
        "script": "signal_engine.py",
        "icon": "🛰",
    },
    "enrich": {
        "label": "Enrichment (enrichment_engine.py)",
        "script": "enrichment_engine.py",
        "icon": "🔎",
    },
    "discover": {
        "label": "Discovery (discovery_engine.py)",
        "script": "discovery_engine.py",
        "icon": "🌱",
    },
    "prospector": {
        "label": "Prospector (prospector_engine.py)",
        "script": "prospector_engine.py",
        "icon": "🧲",
    },
    # Finding agency boards and sweeping them for mandates were two
    # buttons for one job. agency_discovery --and-sweep chains them.
    "find_mandates": {
        "label": "Find mandates (agency_discovery + sweep)",
        "script": "agency_discovery.py",
        "icon": "📌",
    },
}


class EngineJob:
    def __init__(self, label, cmd):
        self.label = label
        self.cmd = cmd
        self.lines = []
        self.done = False
        self.rc = None
        self.started = datetime.now()


def engine_running() -> bool:
    job = st.session_state.get("_engine_job")
    return bool(job and not job.done)


def _engine_reader(job: EngineJob, running: engine_runner.RunningProcess):
    """Background thread: drain stdout into the job, then release the lock.
    Touches no st.* API — only plain Python objects."""
    try:
        for line in running.process.stdout:
            job.lines.append(line.rstrip("\n"))
    finally:
        job.rc = running.process.wait()
        job.done = True
        running.finish(job.rc)


def engine_busy() -> bool:
    """True when a live engine process owns the shared runner lock."""
    return engine_runner.engine_busy()


def start_engine(name: str, extra_args=None) -> bool:
    """Launch one of ENGINES by key. Returns True if started."""
    if engine_running():
        push_toast("An engine is already running — wait for it to finish.", "⏳")
        return False
    spec = ENGINES[name]
    cmd = engine_runner.command_for(name, extra_args)
    try:
        running = engine_runner.start_locked_process(cmd, label=spec["label"])
    except LockBusy as error:
        push_toast(str(error), "🔒")
        return False
    except OSError as e:
        push_toast(f"Could not start {spec['script']}: {e}", "❌")
        return False
    job = EngineJob(spec["label"], cmd)
    st.session_state["_engine_job"] = job
    threading.Thread(target=_engine_reader, args=(job, running),
                     daemon=True).start()
    push_toast(f"Started: {spec['label']}", spec["icon"])
    return True


def render_engine_panel():
    """Live output panel for the current/last engine run. Rendered on every
    page that has engine buttons. Polls via a fragment while running."""
    job = st.session_state.get("_engine_job")

    if job is None:
        info = lock_info(ENGINE_LOCK)
        if info and info.get("active"):
            label = info.get("label", "engine")
            pid = info.get("pid", "unknown")
            st.info(f"{label} is running in another session (process {pid}).")
        elif info:
            st.caption("A previous engine process ended unexpectedly. The "
                       "next run will recover its lock automatically.")
        return

    if not job.done:
        @st.fragment(run_every="1s")
        def _live():
            j = st.session_state.get("_engine_job")
            if j is None:
                return
            with st.status(f"Running: {j.label}…", expanded=True,
                           state="running"):
                st.code("\n".join(j.lines[-40:]) or "starting…", language=None)
            if j.done:
                st.rerun(scope="app")
        _live()
        return

    ok = job.rc == 0
    head = (f"{job.label} — finished OK" if ok
            else f"{job.label} — FAILED (exit code {job.rc})")
    with st.status(head, expanded=not ok, state="complete" if ok else "error"):
        st.code("\n".join(job.lines) or "(no output)", language=None)
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Dismiss output", key="engine_dismiss",
                     help="Clears this panel. The engine's results are "
                          "already saved to the data files."):
            st.session_state.pop("_engine_job", None)
            st.rerun()
    with c2:
        st.caption(f"Started {job.started.strftime('%H:%M:%S')} · "
                   "data files refresh automatically on the next interaction.")


def engine_button(name: str, key: str, extra_args=None, label=None,
                  help_text=None, type="secondary"):
    """One 'run engine X' button with the shared lock/disable behavior."""
    spec = ENGINES[name]
    disabled = engine_running() or engine_busy()
    if st.button(label or f"{spec['icon']} Run {spec['label']}", key=key,
                 disabled=disabled, type=type, width="stretch",
                 help=help_text or f"Runs `python {spec['script']}` in the "
                      "repo root and streams its output here. Only one "
                      "engine can run at a time."):
        if start_engine(name, extra_args):
            st.rerun()


# ============================ chrome (sidebar / headers) ============================

def compute_stats():
    """The four always-on sidebar numbers."""
    leads, _ = load_leads()
    status = load_status()
    wl = load_watchlist()
    today = date.today().isoformat()
    n_due = sum(1 for r in leads if followup_due(status.get(lead_key(r))))
    n_new = sum(1 for r in leads
                if effective_status(status.get(lead_key(r))) == "new")
    touched = sum(1 for e in status.values()
                  if e.get("on") == today
                  and e.get("status") in ("contacted", "replied", "won"))
    boards = sum(len(v) for v in wl.values())
    return {"due": n_due, "new": n_new, "touched": touched, "boards": boards}


def render_sidebar_stats():
    s = compute_stats()
    with st.sidebar:
        st.divider()
        a, b = st.columns(2)
        a.metric("Follow-ups due", s["due"],
                 help="Contacted leads whose last touch is "
                      f"≥{FOLLOWUP_DAYS} days old (max {MAX_FOLLOWUPS} "
                      "follow-ups per lead). Work these first.")
        b.metric("New leads", s["new"],
                 help="Signals not yet contacted or dismissed.")
        c, d = st.columns(2)
        c.metric("Touched today", s["touched"],
                 help="Leads marked contacted/replied/won today.")
        d.metric("Boards watched", s["boards"],
                 help="Job boards swept nightly (see System → Watchlist).")


# what the "?" popover says per page + the Help section it jumps to
PAGE_BLURBS = {
    "home": "Today's numbers, the pipeline funnel, billable summary, "
            "setup checklist and engine run buttons.",
    "queue": "Your daily working view: follow-ups due first, then the "
             "top-ranked new sends. Copy the DM, send it on LinkedIn, click "
             "Sent. Focus mode walks you through one card at a time.",
    "inbox": "Every signal ever captured, with stage, notes and DM drafts. "
             "Use it to manage conversations after the first touch.",
    "prospects": "OUR pipeline: recruitment agencies we want as clients. "
                 "Found by the prospector, hooked by their fresh mandates, "
                 "armed with a one-click mini-list per prospect.",
    "clients": "Per-client lead list, metrics and the billable total; "
               "export any filtered view as a client report CSV.",
    "minilist": "Builds the free 'companies hiring in your niche' gift list "
                "for a prospect agency — CSV + branded HTML one-pager.",
    "watchlist": "The job boards swept nightly. Paste any board URL to add "
                 "one; run discovery to find more automatically.",
    "livejobs": "Every live job currently tracked across all watched boards "
                "— search, filter, export.",
    "configs": "Client profiles: filter gates, message templates, meeting "
               "rate. Test a config before trusting it, then backfill.",
    "help": "How the whole machine works, page by page.",
}


def page_header(title: str, page_key: str):
    """Page title + '?' popover linking to the page's Help section."""
    c1, c2 = st.columns([0.93, 0.07], vertical_alignment="center")
    with c1:
        st.title(title)
    with c2:
        with st.popover("?", help="What is this page?"):
            st.markdown(PAGE_BLURBS.get(page_key, ""))
            if st.button("📖 Open the full guide", key=f"help_jump_{page_key}",
                         help="Opens the Help page at this page's section."):
                st.session_state["help_jump"] = page_key
                st.switch_page("views/help.py")
