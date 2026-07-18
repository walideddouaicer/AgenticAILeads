#!/usr/bin/env python3
"""
Agentic AI Leads — shared helpers for the Streamlit CRM.

Everything the pages in views/ have in common lives here: file paths,
constants, data loaders, the lead-status store, DM rendering, watchlist
helpers, stage badges, toast/undo plumbing and the sidebar stats block.

Business rules preserved from the original single-file app:
  - lead_key() hashing (md5 of date|client|company|title, 12 hex chars)
  - stage names: new / contacted / replied / won / dismissed (+ legacy map)
  - follow-up counting: due FOLLOWUP_DAYS after last touch, stop after
    MAX_FOLLOWUPS (the breakup message)
  - double-DM guard: same LinkedIn contact never messaged for two clients

Data files (formats unchanged — other scripts depend on them):
  signals.csv, signals_state.json, inbox_status.json, watchlist.json,
  enriched_contacts.json, configs/*.json, discovery_state.json

AAL_DATA_DIR env var overrides the data directory (used by tests only);
default is the repo root.
"""

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import streamlit as st

import delivery
from filter_engine import DEFAULTS

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AAL_DATA_DIR", HERE))
CONFIG_DIR = DATA_DIR / "configs"
SIGNALS_CSV = DATA_DIR / "signals.csv"
ENRICHED_CSV = DATA_DIR / "enriched_signals.csv"
CONTACTS_CACHE = DATA_DIR / "enriched_contacts.json"
STATE_FILE = DATA_DIR / "signals_state.json"
INBOX_STATUS = DATA_DIR / "inbox_status.json"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
DISCOVERY_STATE = DATA_DIR / "discovery_state.json"
ENV_FILE = DATA_DIR / ".env"

# --- own-client prospecting (separate pipeline, separate files) ---
PROSPECTS_CSV = DATA_DIR / "prospects.csv"
PROSPECT_SIGNALS_CSV = DATA_DIR / "prospect_signals.csv"
PROSPECTS_STATUS = DATA_DIR / "prospects_status.json"
PROSPECTS_CONFIG = DATA_DIR / "prospects_config.json"
AGENCY_WATCHLIST_FILE = DATA_DIR / "agency_watchlist.json"

DEFAULT_TEMPLATE = (
    "Hi {first_name} — {hook}. Roles like that are exactly what we place. "
    "Worth a quick chat about how we can help you fill them faster?"
)
DEFAULT_EMAIL_SUBJECT = "Your {title} opening"
DEFAULT_EMAIL_TEMPLATE = (
    "Hi {first_name},\n\n"
    "{hook} — filling roles like that is exactly what we do.\n\n"
    "Worth a quick call this week? Happy to share how we'd approach it.\n\n"
    "Best,\n"
)
DEFAULT_FOLLOWUP_TEMPLATE = (
    "Hi {first_name} — floating this back up. {hook}, and roles like that "
    "don't stay open long. Worth a quick chat this week?"
)
DEFAULT_FOLLOWUP2_TEMPLATE = (
    "Hi {first_name} — last note from me. If filling roles faster ever becomes "
    "a priority, happy to show you how we work: you pay per meeting that "
    "actually happens, first one free. Door's open."
)
FOLLOWUP_DAYS = 3
MAX_FOLLOWUPS = 2   # after follow-up #2 (breakup), lead leaves the queue
FRESH_DAYS = 7      # signals older than this switch to "currently hiring" wording
DEFAULT_MEETING_RATE = 150
DAILY_QUEUE_CAP = 15   # default new sends/day (LinkedIn-safe)

STAGES = ["contacted", "replied", "won"]
# legacy stage names from earlier versions -> current
LEGACY_MAP = {"call booked": "replied", "showed": "won", "snoozed": "new"}

# prospect pipeline stages (OUR outreach to agencies) — distinct from leads
PROSPECT_STAGES = ["contacted", "replied", "call_booked", "client_won"]

DEFAULT_PROSPECT_DM = (
    "Hi {first_name} — {hook}. We run signal-based outbound for recruitment "
    "agencies: we find companies actively hiring in your niche and book "
    "direct-client meetings with the decision-makers. You pay per meeting "
    "that actually happens — first one free so you can judge the quality "
    "yourself. Worth a 15-minute call?"
)
DEFAULT_PROSPECT_FOLLOWUP = (
    "Hi {first_name} — floating this back up. {hook} — happy to send a free "
    "sample first: 10 companies hiring exactly what you place, with the "
    "decision-maker contacts. Pay per showed meeting, first one free. "
    "Want the list?"
)
DEFAULT_PROSPECT_FOLLOWUP2 = (
    "Hi {first_name} — last note from me. If direct-client acquisition ever "
    "becomes a priority: we book the meetings, you pay only when they show, "
    "first one free. Door's open."
)

PLATFORMS = ["greenhouse", "lever", "ashby", "workable"]

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
    if u["prev"] is None:
        status.pop(u["key"], None)
    else:
        status[u["key"]] = u["prev"]
    save_status(status, path)
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


# ============================ profiles ============================

def list_profiles():
    if not CONFIG_DIR.exists():
        return []
    return sorted(p.stem for p in CONFIG_DIR.glob("*.json"))


def load_profile(name):
    try:
        cfg = json.loads((CONFIG_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg.setdefault("client_id", name)
    cfg.setdefault("dm_template", DEFAULT_TEMPLATE)
    cfg.setdefault("email_subject", DEFAULT_EMAIL_SUBJECT)
    cfg.setdefault("email_template", DEFAULT_EMAIL_TEMPLATE)
    cfg.setdefault("followup_template", DEFAULT_FOLLOWUP_TEMPLATE)
    cfg.setdefault("followup2_template", DEFAULT_FOLLOWUP2_TEMPLATE)
    cfg.setdefault("meeting_rate", DEFAULT_MEETING_RATE)
    # optional delivery fields (added by the channel-ready delivery layer;
    # absent in older config files, which stay valid)
    cfg.setdefault("channel", "linkedin")
    cfg.setdefault("campaign_id", "")
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)())
    return cfg


def parse_csv_line(text):
    return [x.strip() for x in text.split(",") if x.strip()]


def parse_weights(text):
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            try:
                out[k.strip()] = int(v.strip())
            except ValueError:
                continue
    return out


def weights_to_text(w):
    return "\n".join(f"{k}: {v}" for k, v in (w or {}).items())


# ============================ leads & status ============================

def all_tracked_jobs():
    if not STATE_FILE.exists():
        return []
    try:
        state = json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return []
    jobs = []
    for board_key, board_jobs in state.items():
        platform, _, company = board_key.partition(":")
        for j in board_jobs.values():
            jobs.append({**j, "company": company, "platform": platform})
    return jobs


def lead_key(row):
    raw = "|".join(row.get(k, "") or "" for k in ["date", "client", "company", "title"])
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def load_leads():
    """signals.csv is the source of truth; contacts are joined in live from
    enriched_contacts.json, so new signals never wait on an enrichment
    re-run. Falls back to enriched_signals.csv if signals.csv is missing."""
    src = SIGNALS_CSV if SIGNALS_CSV.exists() else ENRICHED_CSV
    if not src.exists():
        return [], src.name
    rows = list(csv.DictReader(src.open(encoding="utf-8")))
    try:
        cache = json.loads(CONTACTS_CACHE.read_text(encoding="utf-8")) \
            if CONTACTS_CACHE.exists() else {}
    except json.JSONDecodeError:
        cache = {}
    for r in rows:
        if not r.get("client"):
            r["client"] = "default"
        if not r.get("contact1_name"):
            for i, c in enumerate((cache.get(r.get("company", "")) or [])[:2], 1):
                for k, v in c.items():
                    r[f"contact{i}_{k}"] = v
    label = src.name + (" + contacts" if cache else "")
    return rows, label


def load_status(path=None):
    """{key: {'status': str, 'on': iso, 'notes': str}} — migrates all
    earlier formats (plain string, legacy stage names, snooze fields).
    Default file is the lead inbox; pass PROSPECTS_STATUS for prospects."""
    path = path or INBOX_STATUS
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    out = {}
    for k, v in raw.items():
        if isinstance(v, str):
            v = {"status": v, "on": ""}
        v.pop("until", None)
        v.setdefault("notes", "")
        s = v.get("status", "new")
        v["status"] = LEGACY_MAP.get(s, s)
        out[k] = v
    return out


def save_status(status, path=None):
    (path or INBOX_STATUS).write_text(json.dumps(status, indent=1))


def effective_status(entry) -> str:
    if not entry:
        return "new"
    return entry.get("status", "new")


def followup_due(entry) -> bool:
    if not entry or entry.get("status") != "contacted":
        return False
    try:
        if int(entry.get("fu", 0) or 0) >= MAX_FOLLOWUPS:
            return False   # breakup already sent — stop chasing
    except (TypeError, ValueError):
        pass
    on = entry.get("on") or ""
    try:
        return (date.today() - datetime.strptime(on, "%Y-%m-%d").date()).days >= FOLLOWUP_DAYS
    except ValueError:
        return False


def all_clients(leads):
    return sorted(set(list_profiles()) | {r.get("client", "default") for r in leads})


def display_hook(row):
    """Hook wording follows the signal's age: fresh (<= FRESH_DAYS) keeps the
    stored 'just opened' hook; older signals degrade to 'currently hiring'
    so a DM never claims recency the posting doesn't have."""
    d = row.get("date") or ""
    try:
        age = (date.today() - datetime.strptime(d, "%Y-%m-%d").date()).days
    except ValueError:
        age = None
    if age is not None and age > FRESH_DAYS:
        loc = row.get("location", "")
        return (f"saw you're currently hiring a {row.get('title', '')}"
                + (f" in {loc}" if loc else ""))
    return row.get("hook", "")


def render_dm(template, row):
    contact = row.get("contact1_name", "")
    first = contact.split()[0] if contact else "there"
    fields = {
        "first_name": first,
        "hook": display_hook(row),
        "company": row.get("company", ""),
        "title": row.get("title", ""),
        "location": row.get("location", ""),
        "client": row.get("client", ""),
    }
    try:
        return template.format(**fields)
    except (KeyError, IndexError, ValueError):
        return template


def mailto_link(email, subject, body):
    return f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"


# --------- delivery layer (email tools plug in via .env keys) ---------

def delivery_adapters():
    """All adapters, bound to this app's .env (AAL_DATA_DIR-aware)."""
    return delivery.all_adapters(env_file=ENV_FILE)


def active_email_adapter():
    """First configured email adapter, or None (manual excluded)."""
    return delivery.active_email_adapter(env_file=ENV_FILE)


def apply_replies(adapter, campaign_id=None):
    """Poll the adapter for replies and move matched leads to 'replied'.

    Matches on contact email; only leads currently 'new' or 'contacted'
    are advanced (never downgrades 'won'/'dismissed'). Writes status
    directly (no toast/undo) so it's usable from the CLI too — the app
    shows its own toast afterwards. Returns a summary dict."""
    try:
        emails = adapter.sync_replies(campaign_id)
    except Exception as e:  # network/HTTP/parse — surface, don't crash
        return {"error": f"{e.__class__.__name__}: {e}", "matched": 0}
    email_set = {str(e).strip().lower() for e in (emails or []) if e}
    if not email_set:
        return {"emails": 0, "matched": 0, "changed": []}
    leads, _ = load_leads()
    status = load_status()
    changed = []
    for r in leads:
        em = (r.get("contact1_email") or "").strip().lower()
        if not em or em not in email_set:
            continue
        k = lead_key(r)
        if effective_status(status.get(k)) in ("new", "contacted"):
            e = status.get(k, {"status": "new", "on": "", "notes": ""})
            e["status"] = "replied"
            e["on"] = date.today().isoformat()
            status[k] = e
            changed.append(r)
    if changed:
        save_status(status)
    return {"emails": len(email_set), "matched": len(changed),
            "changed": changed}


# --------- manual contacts (Apollo API enrichment is plan-gated) ---------

def load_contacts_cache():
    """enriched_contacts.json — same file/format enrichment_engine writes:
    {company: [{name, title, linkedin, email}]}."""
    if CONTACTS_CACHE.exists():
        try:
            return json.loads(CONTACTS_CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_contacts_cache(cache):
    CONTACTS_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


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
                    cache = load_contacts_cache()
                    entries = [c for c in (cache.get(company) or [])
                               if isinstance(c, dict)]
                    new = {"name": name.strip(), "title": title.strip(),
                           "linkedin": linkedin.strip(),
                           "email": email.strip()}
                    idx = next(
                        (i for i, c in enumerate(entries)
                         if (c.get("name", "").strip().lower()
                             == existing_name.lower())),
                        None) if editing else None
                    if idx is None:
                        entries.append(new)
                    else:
                        entries[idx] = new
                    cache[company] = entries
                    save_contacts_cache(cache)
                    push_toast(f"Contact saved for {company}", "👤")
                    st.rerun()
        with c2:
            if editing and st.button(
                    "🗑 Remove contact", key=f"mc_r_{wkey}", width="stretch",
                    help="Deletes this contact from enriched_contacts.json "
                         "(for every lead of this company)."):
                cache = load_contacts_cache()
                entries = [c for c in (cache.get(company) or [])
                           if isinstance(c, dict)
                           and c.get("name", "").strip().lower()
                           != existing_name.lower()]
                if entries:
                    cache[company] = entries
                else:
                    cache.pop(company, None)
                save_contacts_cache(cache)
                push_toast(f"Contact removed from {company}", "🗑")
                st.rerun()


def mark_followed_up(status, key, lead_label: str = "", path=None):
    """Reset the follow-up timer and count the touch (day-3 -> day-6 -> stop)."""
    _snapshot_undo(status, key, f"follow-up on {lead_label}" if lead_label
                   else "follow-up", path)
    e = status.get(key, {"status": "contacted", "on": "", "notes": ""})
    e["on"] = date.today().isoformat()
    e["fu"] = int(e.get("fu", 0) or 0) + 1
    status[key] = e
    save_status(status, path)
    push_toast(f"Follow-up #{e['fu']} logged"
               + (f" — {lead_label}" if lead_label else ""), "🔁")


def set_stage(status, key, stage, notes=None, lead_label: str = "", path=None):
    _snapshot_undo(status, key, f"{lead_label or 'lead'} → {stage}", path)
    entry = status.get(key, {"status": "new", "on": "", "notes": ""})
    entry["status"] = stage
    entry["on"] = date.today().isoformat()
    if notes is not None:
        entry["notes"] = notes
    status[key] = entry
    save_status(status, path)
    icons = {"contacted": "✅", "replied": "💬", "won": "🏆", "dismissed": "🚫",
             "call_booked": "📅", "client_won": "🎉"}
    push_toast((f"{lead_label} → {stage.replace('_', ' ')}" if lead_label
                else f"Stage → {stage.replace('_', ' ')}"),
               icons.get(stage, "✅"))


# ============================ prospects (our own clients) ============================

def load_prospects():
    """Rows from prospects.csv (prospector_engine output + user edits)."""
    if not PROSPECTS_CSV.exists():
        return []
    return list(csv.DictReader(PROSPECTS_CSV.open(encoding="utf-8")))


def load_prospect_signals():
    """Rows from prospect_signals.csv (mandate sweep output), newest first."""
    if not PROSPECT_SIGNALS_CSV.exists():
        return []
    rows = list(csv.DictReader(PROSPECT_SIGNALS_CSV.open(encoding="utf-8")))
    rows.sort(key=lambda r: r.get("date", ""), reverse=True)
    return rows


def prospect_key(row):
    """Stable key for prospects_status.json: LinkedIn URL when present,
    else agency+person name — never collides with lead_key space because
    it lives in a separate file."""
    lk = (row.get("linkedin") or "").strip().lower().rstrip("/")
    raw = lk or (f"{(row.get('agency') or '').strip().lower()}"
                 f"|{(row.get('person_name') or '').strip().lower()}")
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def mandates_for_agency(agency_name, signals):
    """prospect_signals rows for one agency, fuzzy-matched on the
    normalized (slugged) agency name vs the board token, newest first."""
    from discovery_engine import slug_variants
    variants = slug_variants(agency_name)
    if not variants:
        return []
    hits = [s for s in signals if s.get("agency") in variants]
    if not hits:
        hits = [s for s in signals
                if any(v in (s.get("agency") or "") or (s.get("agency") or "") in v
                       for v in variants)]
    return hits


_NICHE_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "with", "to", "in", "at",
    "on", "sr", "jr", "senior", "junior", "lead", "principal", "staff",
    "head", "chief", "manager", "director", "vp", "intern", "graduate",
    "trainee", "assistant", "associate", "specialist", "coordinator",
    "executive", "officer", "remote", "hybrid", "onsite", "on-site",
    "contract", "permanent", "freelance", "temporary", "temp", "part",
    "full", "time", "fte", "i", "ii", "iii", "iv", "v", "m", "f", "d",
    "x", "new", "urgent", "immediate", "role", "position", "job", "jobs",
    "month", "months", "year", "years", "all", "levels", "level",
}


def derive_niche_keywords(titles, top_n=5):
    """Most frequent meaningful role keywords across mandate titles —
    the automatic niche guess for a prospect's mini-list."""
    from collections import Counter
    c = Counter()
    for t in titles:
        for w in re.findall(r"[a-z0-9.+#]+", str(t or "").lower()):
            w = w.strip(".")
            if not w or w.isdigit() or len(w) < 2 or w in _NICHE_STOPWORDS:
                continue
            c[w] += 1
    return [w for w, _ in c.most_common(top_n)]


def load_prospects_config():
    """Prospect DM templates (first touch / follow-up / breakup) —
    editable on the Prospects page, stored in prospects_config.json."""
    cfg = {}
    if PROSPECTS_CONFIG.exists():
        try:
            cfg = json.loads(PROSPECTS_CONFIG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cfg = {}
    cfg.setdefault("dm_template", DEFAULT_PROSPECT_DM)
    cfg.setdefault("followup_template", DEFAULT_PROSPECT_FOLLOWUP)
    cfg.setdefault("followup2_template", DEFAULT_PROSPECT_FOLLOWUP2)
    return cfg


def save_prospects_config(cfg):
    PROSPECTS_CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def render_prospect_dm(template, prospect, hook):
    contact = prospect.get("person_name", "")
    first = contact.split()[0] if contact else "there"
    fields = {
        "first_name": first,
        "agency": prospect.get("agency", ""),
        "hook": hook,
        "person_title": prospect.get("person_title", ""),
        "location": prospect.get("location", ""),
    }
    try:
        return template.format(**fields)
    except (KeyError, IndexError, ValueError):
        return template


# ============================ watchlist ============================

ATS_URL_PATTERNS = [
    ("greenhouse", r"greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)"),
    ("lever", r"api\.lever\.co/v0/postings/([A-Za-z0-9_-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_-]+)"),
    ("ashby", r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
    ("workable", r"apply\.workable\.com/api/v1/widget/accounts/([A-Za-z0-9_-]+)"),
    ("workable", r"apply\.workable\.com/([A-Za-z0-9_-]+)"),
]


def parse_board_url(url: str):
    """Any job-board URL -> (platform, token) or (None, None)."""
    url = (url or "").strip()
    for platform, pat in ATS_URL_PATTERNS:
        m = re.search(pat, url)
        if m:
            token = m.group(1).lower()
            if token in ("api", "v1", "embed", "widget"):
                continue
            return platform, token
    return None, None


def load_watchlist():
    if WATCHLIST_FILE.exists():
        try:
            wl = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            wl = {}
    else:
        wl = {}
    return {p: sorted(set(str(t).strip().lower() for t in wl.get(p, []) if str(t).strip()))
            for p in PLATFORMS}


def save_watchlist(wl):
    WATCHLIST_FILE.write_text(json.dumps(wl, indent=2), encoding="utf-8")


def tracked_job_counts():
    """{platform:token -> live job count} from the engine's state file."""
    if not STATE_FILE.exists():
        return {}
    try:
        state = json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return {}
    return {k: len(v) for k, v in state.items()}


# ============================ engine runner ============================
# Runs signal/enrichment/discovery engines as subprocesses of the app,
# streaming their stdout into the page. A lockfile plus the session job
# object guarantee that two engines never run concurrently.

ENGINE_LOCK = DATA_DIR / ".engine.lock"

ENGINES = {
    "sweep": {
        "label": "Nightly sweep (signal_engine.py)",
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
    "agency_discover": {
        "label": "Agency board discovery (agency_discovery.py)",
        "script": "agency_discovery.py",
        "icon": "🏢",
    },
    "prospect_sweep": {
        "label": "Mandate sweep (prospect_signal_engine.py)",
        "script": "prospect_signal_engine.py",
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


def _engine_reader(job: EngineJob, proc: subprocess.Popen):
    """Background thread: drain stdout into the job, then release the lock.
    Touches no st.* API — only plain Python objects."""
    try:
        for line in proc.stdout:
            job.lines.append(line.rstrip("\n"))
    finally:
        job.rc = proc.wait()
        job.done = True
        try:
            ENGINE_LOCK.unlink()
        except OSError:
            pass


def start_engine(name: str, extra_args=None) -> bool:
    """Launch one of ENGINES by key. Returns True if started."""
    if engine_running():
        push_toast("An engine is already running — wait for it to finish.", "⏳")
        return False
    if ENGINE_LOCK.exists():
        push_toast("Engine lock present (another session running?) — see the "
                   "engine panel to clear it if stale.", "🔒")
        return False
    spec = ENGINES[name]
    cmd = [sys.executable, "-u", str(HERE / spec["script"])] + [
        str(a) for a in (extra_args or [])]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(HERE), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", env=env)
    except OSError as e:
        push_toast(f"Could not start {spec['script']}: {e}", "❌")
        return False
    ENGINE_LOCK.write_text(
        f"{proc.pid} {spec['script']} {datetime.now().isoformat()}",
        encoding="utf-8")
    job = EngineJob(spec["label"], cmd)
    st.session_state["_engine_job"] = job
    threading.Thread(target=_engine_reader, args=(job, proc),
                     daemon=True).start()
    push_toast(f"Started: {spec['label']}", spec["icon"])
    return True


def render_engine_panel():
    """Live output panel for the current/last engine run. Rendered on every
    page that has engine buttons. Polls via a fragment while running."""
    job = st.session_state.get("_engine_job")

    if job is None:
        if ENGINE_LOCK.exists():
            st.warning("An engine lockfile exists but no engine is running in "
                       "this session. Either another window is running one, "
                       "or a previous run crashed.")
            if st.button("Clear stale engine lock", key="clear_lock",
                         help="Only click this if you're sure no engine is "
                              "running in another window."):
                ENGINE_LOCK.unlink(missing_ok=True)
                st.rerun()
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
    disabled = engine_running() or ENGINE_LOCK.exists()
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
