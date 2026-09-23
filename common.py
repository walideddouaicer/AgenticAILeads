#!/usr/bin/env python3
"""
Agentic AI Leads — Streamlit glue for the CRM.

This module contains UI helpers: theme/CSS, toasts and undo, the manual
contact editor, engine subprocess runner, sidebar and page headers. Domain
logic lives in core.py, which imports no Streamlit. Pages import their
domain operations directly from core.

UI stage changes go through ``set_stage`` here; the wrapper adds the undo
snapshot and toast. Pages do not write status files directly.

AAL_DATA_DIR env var overrides the data directory (tests, container
deploy); default is the repo root.
"""

import threading
from datetime import datetime
from pathlib import Path

import streamlit as st

import runner as engine_runner
from storage import LockBusy, lock_info

from core import (DAILY_QUEUE_CAP, INBOX_STATUS, PROSPECTS_STATUS,
                  apply_stage, effective_status,
                  followup_due, load_prospects, load_status, prospect_key,
                  restore_status_record, status_revision,
                  update_company_contacts)
from storage import ConflictError

# --- presentation-only constants (domain constants live in core) ---
# App-wide stage colors and badge palette
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



def render_contact_editor(row, wkey):
    """Add / edit / remove a manually-found contact for this exact board.

    Writes enriched_contacts.json in the exact structure the enrichment
    engine uses, so load_leads() picks the contact up on every lead of the
    company automatically. Rendered as a popover because the lead cards are
    already expanders (Streamlit forbids nesting them)."""
    company = (row.get("company") or "").strip()
    board = f"{row.get('platform', '')}:{company}".lower()
    if not company or not row.get("platform"):
        return
    existing_name = (row.get("contact1_name") or "").strip()
    editing = bool(existing_name)
    prefill_email = row.get("contact1_email", "") if editing else ""
    if "locked" in prefill_email.lower():
        prefill_email = ""

    label = "✏️ Edit / remove contact" if editing else "➕ Add contact manually"
    with st.popover(label,
                    help="Contacts are stored per board identity in "
                         "enriched_contacts.json — saving here updates "
                         "every lead from this board."):
        st.caption(f"Board: **{board}** — find the decision-maker on "
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
                              "appears on leads from this board."):
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

                    update_company_contacts(board, save)
                    push_toast(f"Contact saved for {board}", "👤")
                    st.rerun()
        with c2:
            if editing and st.button(
                    "🗑 Remove contact", key=f"mc_r_{wkey}", width="stretch",
                    help="Deletes this contact from enriched_contacts.json "
                         "(for every lead of this board)."):
                update_company_contacts(
                    board,
                    lambda entries: [
                        c for c in entries if isinstance(c, dict)
                        and c.get("name", "").strip().lower()
                        != existing_name.lower()])
                push_toast(f"Contact removed from {board}", "🗑")
                st.rerun()



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


def record_prospect_send(status, key, *, message, sample, channel,
                         lead_label="", action_id=None, expected=None):
    """Record a manual send with the same conflict-safe undo as stage edits."""
    from outreach import record_send

    previous_undo = st.session_state.get("_undo")
    _snapshot_undo(status, key, f"send to {lead_label or key}", PROSPECTS_STATUS)
    try:
        action, duplicate = record_send(
            status, key, message=message, sample=sample, channel=channel,
            action_id=action_id,
            operator=st.session_state.get("_user") or "local operator",
            expected=expected if expected is not None else status_revision(status, key))
    except Exception:
        if previous_undo is None:
            st.session_state.pop("_undo", None)
        else:
            st.session_state["_undo"] = previous_undo
        raise
    if duplicate:
        if previous_undo is None:
            st.session_state.pop("_undo", None)
        else:
            st.session_state["_undo"] = previous_undo
        return None
    st.session_state["_undo"]["expected"] = status_revision(status, key)
    push_toast(f"Manual send recorded for {lead_label or key}", "✅")
    return action


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
    "agency_sweep": {
        "label": "Agency board refresh",
        "script": "prospect_signal_engine.py",
        "icon": "📌",
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
            with st.status(f"Running: {j.label}…", expanded=False,
                           state="running"):
                st.caption("The source is being checked. Results will appear "
                           "when the run finishes.")
                with st.expander("Technical log"):
                    st.code("\n".join(j.lines[-40:]) or "starting…", language=None)
            if j.done:
                st.rerun(scope="app")
        _live()
        return

    ok = job.rc == 0
    head = (f"{job.label} — finished OK" if ok
            else f"{job.label} — FAILED (exit code {job.rc})")
    with st.status(head, expanded=not ok, state="complete" if ok else "error"):
        if ok:
            st.caption("Reload this page to inspect the updated results.")
        else:
            st.error("The refresh did not finish. Review the technical log, "
                     "then retry after correcting the cause.")
        with st.expander("Technical log"):
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
    """Acquisition counts for the sidebar; eligibility remains in outreach."""
    from outreach import initial_sends_today, local_now

    prospects = load_prospects()
    status = load_status(PROSPECTS_STATUS)
    now = local_now()
    keys = {prospect_key(row) for row in prospects}
    return {
        "prospects": len(keys),
        "replies": sum(effective_status(status.get(key)) in
                       ("replied", "call_booked") for key in keys),
        "due": sum(followup_due(status.get(key), now=now) for key in keys),
        "remaining": max(DAILY_QUEUE_CAP - initial_sends_today(status, now), 0),
    }


def render_sidebar_stats():
    s = compute_stats()
    with st.sidebar:
        st.divider()
        a, b = st.columns(2)
        a.metric("Replies", s["replies"])
        b.metric("Follow-ups due", s["due"])
        c, d = st.columns(2)
        c.metric("Prospects", s["prospects"])
        d.metric("Sends left", s["remaining"],
                 help="Remaining in today's operator-chosen initial-send budget.")


# what the "?" popover says per page + the Help section it jumps to
PAGE_BLURBS = {
    "prospects": "Review agencies, approved samples and final messages. "
                 "Record a send only after sending it manually.",
    "clients": "Set up and deliver work for an actual client.",
    "signals": "Check employer and agency boards, their refresh results "
               "and the jobs they advertise.",
    "settings": "Adjust prospect criteria and templates, inspect data "
                "location, backups and run history.",
}


def page_header(title: str, page_key: str):
    """Page title and concise inline help."""
    c1, c2 = st.columns([0.93, 0.07], vertical_alignment="center")
    with c1:
        st.title(title)
    with c2:
        with st.popover("?", help="What is this page?"):
            st.markdown(PAGE_BLURBS.get(
                page_key, "This is a client delivery tool. Select a section "
                          "at the top of Clients to switch tasks."))
