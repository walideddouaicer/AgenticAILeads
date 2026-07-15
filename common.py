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

PLATFORMS = ["greenhouse", "lever", "ashby", "workable"]

# App-wide stage colors (also the badge palette; keep in sync with Help page)
STAGE_COLORS = {
    "new": "#D8FF3E",
    "contacted": "#4A90D9",
    "replied": "#F5A623",
    "won": "#3CB371",
    "dismissed": "#999999",
}
# light backgrounds get dark text, the rest white
_BADGE_FG = {"new": "#111111", "replied": "#111111"}


# ============================ look & feel ============================

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap');
h1, h2, h3, h4, h5,
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
[data-testid="stMetricValue"] {
    font-family: 'Space Grotesk', 'Source Sans Pro', sans-serif !important;
    letter-spacing: -0.01em;
}
/* primary buttons: dark text on the acid-lime primary color */
button[kind="primary"], button[kind="primaryFormSubmit"] {
    color: #111111 !important;
    font-weight: 600;
}
.aal-badge {
    display: inline-block;
    padding: 1px 9px;
    border-radius: 9px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: .02em;
    margin-right: 5px;
    white-space: nowrap;
    font-family: 'Space Grotesk', sans-serif;
}
.aal-funnel-row { display: flex; align-items: center; margin: 4px 0; }
.aal-funnel-label { width: 92px; font-size: 0.82rem; color: #444; }
.aal-funnel-track { flex: 1; background: #F2F2EE; border-radius: 4px; }
.aal-funnel-bar { height: 20px; border-radius: 4px; min-width: 2px; }
.aal-funnel-count { width: 56px; text-align: right; font-size: 0.85rem;
                    font-weight: 600; color: #111; padding-left: 8px; }
</style>
"""


def inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)


def stage_badge(stage: str, due: bool = False) -> str:
    """Colored HTML badge for a stage; render with unsafe_allow_html=True."""
    bg = STAGE_COLORS.get(stage, "#999999")
    fg = _BADGE_FG.get(stage, "#ffffff")
    out = f'<span class="aal-badge" style="background:{bg};color:{fg}">{stage}</span>'
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


def _snapshot_undo(status: dict, key: str, label: str):
    prev = status.get(key)
    st.session_state["_undo"] = {
        "key": key,
        "prev": dict(prev) if isinstance(prev, dict) else prev,
        "label": label,
    }


def undo_available():
    return st.session_state.get("_undo")


def undo_last():
    """Restore the status entry saved before the last stage change."""
    u = st.session_state.pop("_undo", None)
    if not u:
        return None
    status = load_status()
    if u["prev"] is None:
        status.pop(u["key"], None)
    else:
        status[u["key"]] = u["prev"]
    save_status(status)
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


def load_status():
    """{lead_key: {'status': str, 'on': iso, 'notes': str}} — migrates all
    earlier formats (plain string, legacy stage names, snooze fields)."""
    if not INBOX_STATUS.exists():
        return {}
    raw = json.loads(INBOX_STATUS.read_text())
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


def save_status(status):
    INBOX_STATUS.write_text(json.dumps(status, indent=1))


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


def mark_followed_up(status, key, lead_label: str = ""):
    """Reset the follow-up timer and count the touch (day-3 -> day-6 -> stop)."""
    _snapshot_undo(status, key, f"follow-up on {lead_label}" if lead_label
                   else "follow-up")
    e = status.get(key, {"status": "contacted", "on": "", "notes": ""})
    e["on"] = date.today().isoformat()
    e["fu"] = int(e.get("fu", 0) or 0) + 1
    status[key] = e
    save_status(status)
    push_toast(f"Follow-up #{e['fu']} logged"
               + (f" — {lead_label}" if lead_label else ""), "🔁")


def set_stage(status, key, stage, notes=None, lead_label: str = ""):
    _snapshot_undo(status, key, f"{lead_label or 'lead'} → {stage}")
    entry = status.get(key, {"status": "new", "on": "", "notes": ""})
    entry["status"] = stage
    entry["on"] = date.today().isoformat()
    if notes is not None:
        entry["notes"] = notes
    status[key] = entry
    save_status(status)
    icons = {"contacted": "✅", "replied": "💬", "won": "🏆", "dismissed": "🚫"}
    push_toast((f"{lead_label} → {stage}" if lead_label else f"Stage → {stage}"),
               icons.get(stage, "✅"))


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
