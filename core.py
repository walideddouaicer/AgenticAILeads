#!/usr/bin/env python3
"""
Agentic AI Leads — core domain logic. NO Streamlit, NO UI.

Everything here is a pure function or a plain file read/write: paths,
constants, the lead + prospect stores, the stage machine, follow-up rules,
DM rendering, the watchlist, and the delivery-layer bindings.

WHY THIS MODULE EXISTS
----------------------
All of this used to live in common.py, which imports streamlit. That meant
`lead_key()`, `followup_due()` and the stage rules could only be called
from inside a browser session — so no scheduled job, no CLI tool and no
headless script could touch the business logic. Splitting it out is what
makes automation possible at all.

THE RULE (same one delivery.py and sweep_core.py follow):
    common.py may import core.  core must NEVER import common or streamlit.
tests/test_units.py enforces it.

common.py re-exports every public name here, so pages keep calling
`C.lead_key(...)` and nothing in views/ had to change.

Business rules preserved exactly:
  - lead_key()  = md5 of date|client|company|title, first 12 hex chars.
                  Change it and every inbox_status.json entry is orphaned.
  - stages      new -> contacted -> replied -> won, plus dismissed;
                LEGACY_MAP migrates old names on load.
  - follow-ups  due FOLLOWUP_DAYS after the last touch; after
                MAX_FOLLOWUPS (the breakup) the lead leaves the queue.
  - hooks       present-tense wording requires a recent successful board
                check and a still-listed role (display_hook).

Mutators come in two halves. apply_stage()/apply_followup() here do the
file write and nothing else, so a script can use them. common.set_stage()
and common.mark_followed_up() wrap them with the undo snapshot and toast.
Pages must keep using the common.* versions — never write status files
directly.

AAL_DATA_DIR relocates the data directory (tests, container deploy).
"""

import csv
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import delivery
from evidence import confirmed_boards, load_refreshes, verified_at
from filter_engine import DEFAULTS, keyword_matches
from storage import (DELETE, UNSET, atomic_write_json, read_json, revision,
                     update_json, update_json_record)
from runtime import DATA_DIR, ENV_FILE, HERE

CONFIG_DIR = DATA_DIR / "configs"
SIGNALS_CSV = DATA_DIR / "signals.csv"
ENRICHED_CSV = DATA_DIR / "enriched_signals.csv"
CONTACTS_CACHE = DATA_DIR / "enriched_contacts.json"
COMPANY_BOARD_MAPPINGS = DATA_DIR / "company_board_mappings.json"
STATE_FILE = DATA_DIR / "signals_state.json"
INBOX_STATUS = DATA_DIR / "inbox_status.json"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
DISCOVERY_STATE = DATA_DIR / "discovery_state.json"

# --- own-client prospecting (separate pipeline, separate files) ---
PROSPECTS_CSV = DATA_DIR / "prospects.csv"
PROSPECT_SIGNALS_CSV = DATA_DIR / "prospect_signals.csv"
PROSPECTS_STATUS = DATA_DIR / "prospects_status.json"
PROSPECTS_CONFIG = DATA_DIR / "prospects_config.json"
AGENCY_WATCHLIST_FILE = DATA_DIR / "agency_watchlist.json"
AGENCY_BOARD_MAPPINGS = DATA_DIR / "agency_board_mappings.json"
AGENCY_REFRESH_FILE = DATA_DIR / "agency_board_refresh.json"
AGENCY_STATE_FILE = DATA_DIR / "prospect_signals_state.json"
BOARD_REFRESH_FILE = DATA_DIR / "board_refresh.json"

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
FRESH_DAYS = 7      # legacy UI constant; current evidence uses 48-hour checks
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
    "Hi {first_name} — floating this back up. {hook} — happy to review "
    "relevant hiring posts and any contacts we can confirm together. "
    "Pay per showed meeting, first one free. Want to see it?"
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

# ============================ profiles ============================

# ============================ profiles ============================

def list_profiles():
    if not CONFIG_DIR.exists():
        return []
    return sorted(p.stem for p in CONFIG_DIR.glob("*.json"))


def load_profile(name):
    cfg = read_json(CONFIG_DIR / f"{name}.json", default={},
                    expected_type=dict)
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

# ============================ leads & status ============================

def all_tracked_jobs():
    state = read_json(STATE_FILE, default={}, expected_type=dict)
    jobs = []
    for board_key, board_jobs in state.items():
        platform, _, company = board_key.partition(":")
        for job_id, j in board_jobs.items():
            # job_id and board_key are carried through so callers can join
            # against job_history.json, which is keyed the same way. Without
            # them the only join key is the URL, which means a linear scan.
            jobs.append({**j, "company": company, "platform": platform,
                         "job_id": str(job_id), "board_key": board_key})
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
    with src.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    cache = read_json(CONTACTS_CACHE, default={}, expected_type=dict)
    mappings = read_json(COMPANY_BOARD_MAPPINGS, default={}, expected_type=dict)
    for r in rows:
        if not r.get("client"):
            r["client"] = "default"
        if not r.get("contact1_name"):
            board = f"{r.get('platform', '')}:{r.get('company', '')}".lower()
            mapping = mappings.get(board, {})
            linked_key = (mapping.get("contacts_key") or mapping.get("company")) \
                if isinstance(mapping, dict) else ""
            matched = cache.get(board) or (cache.get(linked_key) if linked_key
                                           else []) or []
            for i, c in enumerate(matched[:2], 1):
                for k, v in c.items():
                    r[f"contact{i}_{k}"] = v
    label = src.name + (" + contacts" if cache else "")
    return rows, label


def load_status(path=None):
    """{key: {'status': str, 'on': iso, 'notes': str}} — migrates all
    earlier formats (plain string, legacy stage names, snooze fields).
    Default file is the lead inbox; pass PROSPECTS_STATUS for prospects."""
    path = path or INBOX_STATUS
    raw = read_json(path, default={}, expected_type=dict)
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
    """Atomically replace a complete status map (legacy/bulk callers only)."""
    atomic_write_json(path or INBOX_STATUS, status, indent=1)


def status_revision(status, key):
    return revision(status.get(key))


def update_status_fields(status, key, fields, path=None, *, expected=UNSET):
    """Merge fields into one latest status record under a file lock."""
    path = Path(path or INBOX_STATUS)

    def mutate(current):
        entry = current if isinstance(current, dict) else {
            "status": "new", "on": "", "notes": ""}
        entry.update(fields)
        return entry

    latest, entry, saved_revision = update_json_record(
        path, key, mutate, expected=expected)
    status.clear()
    status.update(latest)
    return entry, saved_revision


def restore_status_record(status, key, previous, path=None, *, expected):
    path = Path(path or INBOX_STATUS)
    value = DELETE if previous is None else previous
    latest, entry, saved_revision = update_json_record(
        path, key, lambda _current: value, expected=expected)
    status.clear()
    status.update(latest)
    return entry, saved_revision


def transition_status_if(status, key, allowed, fields, path=None):
    """Atomically apply a transition only from one of the allowed stages."""
    path = Path(path or INBOX_STATUS)
    changed = {"value": False}

    def mutate(current):
        entry = current if isinstance(current, dict) else {
            "status": "new", "on": "", "notes": ""}
        if effective_status(entry) not in allowed:
            return entry
        entry.update(fields)
        changed["value"] = True
        return entry

    latest, entry, _ = update_json_record(path, key, mutate)
    status.clear()
    status.update(latest)
    return changed["value"], entry


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
    """Only a recent successful board check can support a current claim."""
    platform = row.get("platform", "")
    token = row.get("company", "")
    board = f"{platform}:{token}" if platform and token else ""
    checks = load_refreshes(BOARD_REFRESH_FILE)
    checked = verified_at(board, checks) if board else None
    state = read_json(STATE_FILE, default={}, expected_type=dict)
    live = any(j.get("url") == row.get("url")
               and j.get("observed_at") == checked
               for j in state.get(board, {}).values()) if checked else False
    if live:
        return (f"saw your board advertising a {row.get('title', '')} role"
                + (f" in {row['location']}" if row.get("location") else ""))
    return f"previously saw a {row.get('title', '')} posting on your board"


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
    changed = []
    for r in leads:
        em = (r.get("contact1_email") or "").strip().lower()
        if not em or em not in email_set:
            continue
        k = lead_key(r)
        status = load_status()
        advanced, _ = transition_status_if(
            status, k, {"new", "contacted"},
            {"status": "replied", "on": date.today().isoformat()})
        if advanced:
            changed.append(r)
    return {"emails": len(email_set), "matched": len(changed),
            "changed": changed}


# --------- manual contacts (Apollo API enrichment is plan-gated) ---------

def load_contacts_cache():
    """enriched_contacts.json — same file/format enrichment_engine writes:
    {company: [{name, title, linkedin, email}]}."""
    return read_json(CONTACTS_CACHE, default={}, expected_type=dict)


def save_contacts_cache(cache):
    atomic_write_json(CONTACTS_CACHE, cache, indent=2)


def update_company_contacts(company, mutator):
    """Update one company's contact list without replacing other companies."""
    latest, contacts, _ = update_json_record(
        CONTACTS_CACHE, company,
        lambda current: mutator(current if isinstance(current, list) else []))
    return latest, contacts


# ======================= stage mutations (pure) =======================
# The file-writing half of set_stage / mark_followed_up. No toast, no undo,
# no Streamlit — so scheduled jobs and CLI tools can advance a stage too.
# The UI wrappers in common.py add the snapshot and the toast on top.

def apply_stage(status, key, stage, notes=None, path=None):
    """Set a stage and stamp today's date. Mutates `status`, writes the file."""
    fields = {"status": stage, "on": date.today().isoformat()}
    if notes is not None:
        fields["notes"] = notes
    return update_status_fields(status, key, fields, path)[0]


def apply_followup(status, key, path=None):
    """Reset the follow-up timer and count the touch. Returns the entry, so
    callers can read entry['fu'] for the message they just sent."""
    path = Path(path or INBOX_STATUS)

    def mutate(current):
        entry = current if isinstance(current, dict) else {
            "status": "contacted", "on": "", "notes": ""}
        entry["on"] = date.today().isoformat()
        entry["fu"] = int(entry.get("fu", 0) or 0) + 1
        return entry

    latest, entry, _ = update_json_record(path, key, mutate)
    status.clear()
    status.update(latest)
    return entry


# ==================== prospects (our own clients) ====================

# ============================ prospects (our own clients) ============================

def load_prospects():
    """Rows from prospects.csv (prospector_engine output + user edits)."""
    if not PROSPECTS_CSV.exists():
        return []
    with PROSPECTS_CSV.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def load_prospect_signals():
    """Rows from prospect_signals.csv (mandate sweep output), newest first."""
    if not PROSPECT_SIGNALS_CSV.exists():
        return []
    with PROSPECT_SIGNALS_CSV.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
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


def mandates_for_agency(agency_name, signals, *, mappings_file=None,
                       refresh_file=None, state_file=None, now=None):
    """Current postings on explicitly mapped, recently checked agency boards.

    Legacy token-only signal rows have no platform identity and stay visible in
    the CSV, but cannot substantiate a prospect's copy.
    """
    mappings = read_json(mappings_file or AGENCY_BOARD_MAPPINGS,
                         default={}, expected_type=dict)
    boards = confirmed_boards(agency_name, mappings)
    if not boards:
        return []
    checks = load_refreshes(refresh_file or AGENCY_REFRESH_FILE)
    state = read_json(state_file or AGENCY_STATE_FILE,
                      default={}, expected_type=dict)
    live_urls = {key: {j.get("url") for j in state.get(key, {}).values()
                       if j.get("observed_at") == verified_at(key, checks, now)}
                 for key in boards}
    return [s for s in signals
            if s.get("agency") in boards
            and verified_at(s["agency"], checks, now)
            and s.get("url") in live_urls.get(s["agency"], set())]


def set_agency_boards(agency_name, boards):
    """Operator-confirmed agency association; discovery never calls this."""
    name = str(agency_name or "").strip().casefold()
    if not name:
        raise ValueError("Choose an agency name first")
    watchlist = read_json(AGENCY_WATCHLIST_FILE, default={}, expected_type=dict)
    known = {f"{p}:{token}" for p, tokens in watchlist.items()
             for token in tokens}
    chosen = {str(key).strip().lower() for key in boards}
    if not chosen.issubset(known):
        raise ValueError("Choose only boards in the agency watchlist")

    def save(latest):
        if chosen:
            latest[name] = sorted(chosen)
        else:
            latest.pop(name, None)
        return latest

    return update_json(AGENCY_BOARD_MAPPINGS, save, default={},
                       expected_type=dict, indent=2)


def set_company_board(board, company, contacts_key="", domain=""):
    """Operator-confirmed employer identity and optional contact cache key."""
    board = str(board or "").strip().lower()
    watchlist = read_json(WATCHLIST_FILE, default={}, expected_type=dict)
    known = {f"{p}:{token}" for p, tokens in watchlist.items()
             for token in tokens}
    if board not in known:
        raise ValueError("Choose a tracked employer board")
    company = str(company or "").strip()
    contacts_key = str(contacts_key or "").strip()
    domain = str(domain or "").strip().lower().removeprefix("https://") \
        .removeprefix("http://").strip("/")
    if domain and not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        raise ValueError("Enter a domain such as example.com")

    def save(latest):
        if company:
            latest[board] = {"company": company,
                             "contacts_key": contacts_key,
                             "domain": domain}
        else:
            latest.pop(board, None)
        return latest

    return update_json(COMPANY_BOARD_MAPPINGS, save, default={},
                       expected_type=dict, indent=2)


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
    # Generic role nouns. These are what the person IS, not what they
    # specialise in — and derived keywords are used as mini-list inclusion
    # filters, where "engineer" matches essentially every tech posting and
    # makes the gift generic. Keep technologies and specialisms
    # (java, devops, sre, backend, security); drop the job-title nouns.
    "developer", "developers", "engineer", "engineers", "engineering",
    "software", "programmer", "architect", "analyst", "consultant",
    "administrator", "admin", "scientist", "technician", "technologist",
    "professional", "expert", "support", "tech", "technology",
}


# Job-board locations are free text and wildly inconsistent: "Costa Mesa,
# California, United States", "Hawthorne, CA", "London", "Remote". So a
# prospect's market has to expand into a bag of tokens that covers the
# realistic spellings. filter_engine.location_ok does substring matching
# both directions, so country names, city names and ", XX" state suffixes
# all work.
#
_MARKETS = {
    "uk": ["united kingdom", "england", "scotland", "wales", "britain",
           "london", "manchester", "leeds", "birmingham", "bristol",
           "edinburgh", "glasgow", "cambridge", "oxford", "reading",
           "sheffield", "liverpool", "newcastle", "nottingham", "brighton",
           "cardiff", "belfast", "milton keynes"],
    "us": ["united states", "usa", "new york", "san francisco", "boston",
           "austin", "seattle", "chicago", "los angeles", "denver",
           "atlanta", "dallas", "houston", "miami", "san jose", "palo alto",
           "mountain view", "san diego", "philadelphia", "portland",
           "phoenix", "california", "texas", "washington", "virginia",
           "massachusetts", "colorado", "georgia", "florida", "illinois",
           ", ny", ", tx", ", wa", ", ma", ", il", ", va", ", fl", ", az",
           ", or", ", nc", ", pa", ", oh", ", mi", ", mn", ", ut"],
    "fr": ["france", "paris", "lyon", "toulouse", "bordeaux", "lille",
           "nantes", "marseille", "grenoble", "sophia antipolis"],
}
# ", ca" is deliberately absent: it also matches ", canada". California is
# covered by the full state name and by its city names instead.


# ---------------------------------------------------------------- ICP
# WHO COUNTS AS A PROSPECT. One definition, edited in the app, read by the
# prospector when it searches Apollo.
#
# It is NOT read by the daily ranking, and that is deliberate: if the
# prospector only collects 10-50 person agencies, then re-checking "is this
# 10-50 people?" when ranking filters nothing. A factor belongs in the
# ranking only if it still varies AFTER this filter has been applied.
ICP_FILE = DATA_DIR / "icp.json"

ICP_DEFAULTS = {
    "titles": ["founder", "co-founder", "managing director", "owner",
               "director"],
    "keywords": ["staffing", "recruiting", "recruitment",
                 "talent acquisition", "executive search"],
    # 2-20 was the original band and it was a mistake: a three-person
    # agency cannot pay $1,500/month. 10-50 is where the budget starts.
    "size_min": 10,
    "size_max": 50,
    "countries": ["United States", "United Kingdom", "France"],
}


def load_icp():
    """The current ICP, with every key guaranteed present."""
    cfg = read_json(ICP_FILE, default={}, expected_type=dict)
    for k, v in ICP_DEFAULTS.items():
        cfg.setdefault(k, list(v) if isinstance(v, list) else v)
    try:
        cfg["size_min"] = int(cfg["size_min"])
        cfg["size_max"] = int(cfg["size_max"])
    except (TypeError, ValueError):
        cfg["size_min"], cfg["size_max"] = (ICP_DEFAULTS["size_min"],
                                            ICP_DEFAULTS["size_max"])
    if cfg["size_min"] > cfg["size_max"]:
        cfg["size_min"], cfg["size_max"] = cfg["size_max"], cfg["size_min"]
    return cfg


def save_icp(cfg):
    atomic_write_json(ICP_FILE, cfg, indent=2)


def icp_size_range(cfg=None) -> str:
    """Apollo wants the band as the string "min,max"."""
    cfg = cfg or load_icp()
    return f"{cfg['size_min']},{cfg['size_max']}"


def matches_icp(prospect, cfg=None) -> bool:
    """Was this row collected under the CURRENT ICP?

    Only used to flag legacy rows — your first 176 came from a 2-20 band —
    never to rank. Size arrives from Apollo as a bucket ("11-50") or a
    number, so parse whatever is there and be lenient when it's missing.
    """
    cfg = cfg or load_icp()
    raw = str(prospect.get("size") or "").strip()
    if not raw:
        return True                      # unknown size: don't punish it
    nums = [int(n) for n in re.findall(r"\d+", raw)]
    if not nums:
        return True
    lo, hi = min(nums), max(nums)
    return not (hi < cfg["size_min"] or lo > cfg["size_max"])


def market_tokens(location: str) -> list:
    """A prospect's location -> location filter for their mini-list.

    A Leeds agency cannot use a list of Toronto companies. Without this the
    gift ignores geography entirely, which is the fastest way to prove you
    didn't look at who you're writing to.

    Remote is returned only when the prospect explicitly says remote. Unknown
    or ambiguous locations return an empty list so a sample requires review
    instead of silently matching every market.
    """
    loc = (location or "").lower()
    if keyword_matches(loc, "remote"):
        return ["remote"]
    aliases = {
        "uk": ["uk", "u.k.", "united kingdom", "great britain"],
        "us": ["us", "u.s.", "usa", "united states"],
        "fr": ["fr", "france"],
    }
    for market, tokens in _MARKETS.items():
        if any(keyword_matches(loc, term)
               for term in aliases.get(market, []) + tokens):
            return tokens
    return []


def derive_niche_keywords(titles, top_n=5):
    """Most frequent meaningful role keywords across mandate titles —
    the automatic niche guess for a prospect's mini-list."""
    from collections import Counter
    c = Counter()
    for t in titles:
        text = str(t or "").lower()
        for phrase in ("data engineering", "machine learning",
                       "artificial intelligence", "quality assurance",
                       "cyber security", "cloud security"):
            if keyword_matches(text, phrase):
                c[phrase] += 1
                text = re.sub(re.escape(phrase), " ", text)
        for w in re.findall(r"\.net|c\+\+|c#|[a-z0-9]+(?:[.-][a-z0-9]+)*",
                            text):
            if w != ".net":
                w = w.strip(".")
            if not w or w.isdigit() or len(w) < 2 or w in _NICHE_STOPWORDS:
                continue
            c[w] += 1
    return [w for w, _ in c.most_common(top_n)]


def load_prospects_config():
    """Prospect DM templates (first touch / follow-up / breakup) —
    editable on the Prospects page, stored in prospects_config.json."""
    cfg = read_json(PROSPECTS_CONFIG, default={}, expected_type=dict)
    cfg.setdefault("dm_template", DEFAULT_PROSPECT_DM)
    cfg.setdefault("followup_template", DEFAULT_PROSPECT_FOLLOWUP)
    cfg.setdefault("followup2_template", DEFAULT_PROSPECT_FOLLOWUP2)
    return cfg


def save_prospects_config(cfg):
    atomic_write_json(PROSPECTS_CONFIG, cfg, indent=2)


def render_prospect_dm(template, prospect, hook, sample_rows=None):
    contact = prospect.get("person_name", "")
    first = contact.split()[0] if contact else "there"
    fields = {
        "first_name": first,
        "agency": prospect.get("agency", ""),
        "hook": hook,
        "person_title": prospect.get("person_title", ""),
        "location": prospect.get("location", ""),
    }
    sample_rows = list(sample_rows or [])
    n_contacts = sum(bool(row.get("contact_name")) for row in sample_rows)
    fields.update({
        "sample_count": len(sample_rows),
        "contact_count": n_contacts,
        "sample_summary": (f"{len(sample_rows)} reviewed job-board "
                           f"example(s), {n_contacts} with named contacts")
        if sample_rows else "",
    })
    if not sample_rows and any(token in str(template) for token in
                               ("{sample_count", "{contact_count",
                                "{sample_summary")):
        return ("Template needs correction: approve a current sample "
                "before using sample placeholders.")
    try:
        return template.format(**fields)
    except (KeyError, IndexError, ValueError):
        return ("Template needs correction: check its placeholders before "
                "sending this message.")


# ============================ watchlist ============================

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
    wl = read_json(WATCHLIST_FILE, default={}, expected_type=dict)
    return {p: sorted(set(str(t).strip().lower() for t in wl.get(p, []) if str(t).strip()))
            for p in PLATFORMS}


def save_watchlist(wl):
    atomic_write_json(WATCHLIST_FILE, wl, indent=2)


def update_watchlist(*, add=(), remove=()):
    """Atomically add/remove (platform, token) pairs and return latest data."""
    add, remove = set(add), set(remove)

    def mutate(raw):
        for platform in PLATFORMS:
            values = {str(token).strip().lower()
                      for token in raw.get(platform, []) if str(token).strip()}
            values.update(token for p, token in add if p == platform and token)
            values.difference_update(token for p, token in remove if p == platform)
            raw[platform] = sorted(values)
        return raw

    return update_json(WATCHLIST_FILE, mutate, default={}, expected_type=dict,
                       indent=2)


def tracked_job_counts():
    """{platform:token -> live job count} from the engine's state file."""
    state = read_json(STATE_FILE, default={}, expected_type=dict)
    return {k: len(v) for k, v in state.items()}
