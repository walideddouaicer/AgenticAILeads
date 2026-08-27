#!/usr/bin/env python3
"""
Agentic AI Leads — shared sweep primitives + the job history recorder.

Deliberately Streamlit-free and common-free (same rule as delivery.py):
pure functions, CLI- and unit-testable, importable by any engine.
common.py may import this; this must never import common.

WHY THIS EXISTS
---------------
Both sweeps end each board loop with `state[key] = jobs` and then overwrite
the whole state file. That gives a *snapshot* — what is live right now —
and throws away the *history*. But the history is the only part of this
business a competitor cannot buy: they can crawl the same boards tomorrow,
they cannot crawl them last month.

Everything below exists to record, per job, when we first saw it, when we
last saw it, whether it vanished and came back, and whether its text was
rewritten. From those four facts the useful features fall out:

    days_open       how long the role has been open  (in-house is failing)
    repost_count    pulled and relisted              (a confession)
    rewritten       the spec changed                 (they're struggling)
    req_burst       N roles opened in a short window (capacity shock)
    close_velocity  opened vs closed, trailing       (are they coping?)

None of these can be backfilled for a job we already stopped tracking, so
the recorder runs from the first sweep after install and the data is only
as old as the day it was switched on.

STORAGE
-------
History lives in its own file, never inside the frozen state files:

    job_history.json       leads pipeline   (signal_engine)
    mandate_history.json   prospect pipeline (prospect_signal_engine)

The two pipelines stay separate here exactly as they do everywhere else —
callers pass the path, like the status helpers in common.py do.

Shape:
    {"platform:token": {job_id: {
        "first_seen": "YYYY-MM-DD",
        "last_seen":  "YYYY-MM-DD",
        "times_seen": int,
        "repost_count": int,     # vanished then returned, N times
        "title_changes": int,    # description/title rewritten N times
        "gone_since": "YYYY-MM-DD" | null,   # null while live
        "th": "abc123def456",    # title hash, detects rewrites
    }}}

Recording is IDEMPOTENT per day: running the sweep twice on the same date
does not double-count times_seen or invent reposts. That matters because
the sweep is meant to run unattended and retries are normal.
"""

import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


# Values companies type into the "location" box that describe a WORKING
# ARRANGEMENT rather than a place. Cloudflare's Greenhouse board says
# "Hybrid" for 199 jobs; SpaceX says "In-Office". Those are useless for
# geo-matching a mini-list, and worse, they hide the real city that other
# fields on the same record usually carry.
_WORKPLACE_WORDS = {
    "remote", "hybrid", "in-office", "in office", "onsite", "on-site",
    "on site", "distributed", "anywhere", "flexible", "wfh",
    "remote (us)", "fully remote", "office", "various",
}


def split_workplace(raw: str):
    """'Remote - United States' -> ('United States', 'remote').

    Returns (place, workplace). `place` is the geographic part with the
    arrangement stripped; `workplace` is remote/hybrid/onsite or "".
    Either can be empty — a bare "Hybrid" yields ("", "hybrid").
    """
    s = (raw or "").strip()
    if not s:
        return "", ""
    low = s.lower()

    workplace = ""
    for word, tag in (("hybrid", "hybrid"), ("remote", "remote"),
                      ("wfh", "remote"), ("distributed", "remote"),
                      ("in-office", "onsite"), ("in office", "onsite"),
                      ("on-site", "onsite"), ("on site", "onsite"),
                      ("onsite", "onsite")):
        if word in low:
            workplace = tag
            break

    if low in _WORKPLACE_WORDS:
        return "", workplace

    # "Remote - United States", "Hybrid (London)", "Remote, EMEA"
    place = re.sub(
        r"\b(fully\s+)?(remote|hybrid|in[- ]?office|on[- ]?site|onsite|"
        r"distributed|wfh)\b", "", s, flags=re.I)
    place = place.strip(" ,-–—()/|").strip()
    return place, workplace


def best_location(*candidates):
    """First candidate that names an actual place.

    Fetchers pass several fields in priority order; the first one that
    isn't blank and isn't a bare working-arrangement wins.
    """
    for c in candidates:
        if not c:
            continue
        place, _ = split_workplace(str(c))
        if place:
            return place
    return ""


def enable_utf8_stdout():
    """Make prints survive a cp1252 console.

    The engines print '●' and '—'. Under Task Scheduler the console is
    cp1252, and printing '●' raises UnicodeEncodeError *mid-loop* — which
    killed the nightly sweep silently for weeks before anyone read the log.
    common.start_engine sets PYTHONUTF8=1 for app-launched runs; this makes
    a bare `python signal_engine.py` safe too, whoever starts it.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass        # already wrapped, or not a real stream (tests)

# Keep closed jobs for a year — close_velocity and repost detection both
# need the dead ones. Older than this and they're only taking up space.
DEFAULT_KEEP_DAYS = 365

# A job absent for this long is treated as genuinely closed rather than a
# transient fetch failure, for close_velocity purposes.
CLOSED_AFTER_DAYS = 2


# ------------------------------------------------------------------ state

def load_json_state(path: Path, migrate=None) -> dict:
    """Read a sweep state file. `migrate` remaps keys on load (signal_engine
    uses it to upgrade Phase-1 keys like "gitlab" to "greenhouse:gitlab")."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return migrate(state) if migrate else state


def save_json_state(path: Path, state: dict) -> None:
    Path(path).write_text(json.dumps(state), encoding="utf-8")


# ---------------------------------------------------------------- history

def title_hash(job: dict) -> str:
    """Legacy combined hash (title + location). Kept for existing entries."""
    raw = f"{job.get('title', '')}|{job.get('location', '')}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _title_only_hash(job: dict) -> str:
    """Hash of the TITLE alone — what 'rewritten' should actually mean.

    The combined title+location hash conflated two different events. A
    company renaming "Backend Engineer" to "Staff Backend Engineer
    (Payments)" is a rewrite and a real signal that they're struggling
    with the spec. A location string changing is not.

    It also made the recorder fragile against ourselves: when the fetchers
    learned to read Greenhouse `offices` (so "Hybrid" became "Washington,
    DC"), ~511 jobs would have logged a rewrite that never happened —
    corrupting the one file here that cannot be rebuilt.
    """
    return hashlib.md5(
        str(job.get("title", "")).encode("utf-8")).hexdigest()[:12]


def load_history(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Never silently start a fresh history — that would look like the
        # recorder is working while quietly resetting the moat to zero.
        raise SystemExit(
            f"{path.name} is corrupt. Do NOT delete it — it cannot be "
            "rebuilt. Restore it from a backup, or move it aside "
            "deliberately if you accept losing the history to date.")


def save_history(path: Path, history: dict) -> None:
    Path(path).write_text(json.dumps(history), encoding="utf-8")


def record_observations(history: dict, board_key: str, jobs: dict,
                        today: str = None) -> dict:
    """Fold one board's live jobs into the history. Mutates `history`.

    `jobs` is the sweep's {job_id: {title, location, url}} for this board —
    exactly what gets written to the state file, so the caller passes the
    same object it already has.

    Returns counters for this board: new, still_open, reposted, rewritten,
    closed.
    """
    today = today or date.today().isoformat()
    board = history.setdefault(board_key, {})
    stats = {"new": 0, "still_open": 0, "reposted": 0,
             "rewritten": 0, "closed": 0}

    for job_id, job in (jobs or {}).items():
        job_id = str(job_id)
        th = title_hash(job)
        tt = _title_only_hash(job)
        entry = board.get(job_id)

        if entry is None:
            board[job_id] = {
                "first_seen": today, "last_seen": today, "times_seen": 1,
                "repost_count": 0, "title_changes": 0,
                "gone_since": None, "th": th, "tt": tt,
            }
            stats["new"] += 1
            continue

        # Migration: entries written before `tt` existed get it backfilled
        # SILENTLY. Comparing a title-only hash against a legacy combined
        # hash would flag every job in the file as rewritten.
        if "tt" not in entry:
            entry["tt"] = tt
            entry["th"] = th

        # Vanished and came back — the strongest single signal in here.
        if entry.get("gone_since"):
            entry["repost_count"] = entry.get("repost_count", 0) + 1
            entry["gone_since"] = None
            stats["reposted"] += 1

        # Rewrite = the TITLE changed. Location moves are not rewrites.
        if entry.get("tt") and entry["tt"] != tt:
            entry["title_changes"] = entry.get("title_changes", 0) + 1
            stats["rewritten"] += 1
        entry["tt"] = tt
        entry["th"] = th        # kept current, no longer used for detection

        # Idempotent: a second sweep the same day must not inflate counts.
        if entry.get("last_seen") != today:
            entry["times_seen"] = entry.get("times_seen", 0) + 1
            entry["last_seen"] = today
        stats["still_open"] += 1

    # Anything we knew about that isn't in this fetch has disappeared.
    live = {str(j) for j in (jobs or {})}
    for job_id, entry in board.items():
        if job_id not in live and not entry.get("gone_since"):
            entry["gone_since"] = today
            stats["closed"] += 1

    return stats


def prune_history(history: dict, today: str = None,
                  keep_days: int = DEFAULT_KEEP_DAYS) -> int:
    """Drop jobs closed longer ago than keep_days. Returns rows removed."""
    today = today or date.today().isoformat()
    cutoff = (_parse(today) - timedelta(days=keep_days)).isoformat()
    removed = 0
    for board in history.values():
        for job_id in [j for j, e in board.items()
                       if e.get("gone_since") and e["gone_since"] < cutoff]:
            del board[job_id]
            removed += 1
    return removed


# --------------------------------------------------------------- features

def _parse(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


def _days(a: str, b: str) -> int:
    try:
        return (_parse(a) - _parse(b)).days
    except (TypeError, ValueError):
        return 0


def job_features(entry: dict, today: str = None) -> dict:
    """Per-job features derived from the recorded history.

    days_open counts to today while the job is live, and freezes at the
    day it disappeared once it's closed — so a closed req reports how long
    it stayed open, not how long ago it existed.
    """
    today = today or date.today().isoformat()
    gone = entry.get("gone_since")
    return {
        "days_open": _days(gone or today, entry.get("first_seen", today)),
        "repost_count": entry.get("repost_count", 0),
        "rewritten": entry.get("title_changes", 0) > 0,
        "title_changes": entry.get("title_changes", 0),
        "is_live": not gone,
        "first_seen": entry.get("first_seen"),
        "last_seen": entry.get("last_seen"),
    }


def board_features(history: dict, board_key: str, today: str = None,
                   window_days: int = 30) -> dict:
    """Per-company features. req_burst = roles opened inside the window;
    close_velocity = closed ÷ opened over the same window (>1 means they're
    keeping up, <1 means the backlog is growing — that's the interesting
    direction)."""
    today = today or date.today().isoformat()
    board = history.get(board_key, {})
    since = (_parse(today) - timedelta(days=window_days)).isoformat()

    opened = sum(1 for e in board.values()
                 if (e.get("first_seen") or "") >= since)
    closed = sum(1 for e in board.values()
                 if (e.get("gone_since") or "") >= since)
    live = [e for e in board.values() if not e.get("gone_since")]
    ages = sorted(job_features(e, today)["days_open"] for e in live)

    return {
        "live_count": len(live),
        "req_burst": opened,
        "closed_in_window": closed,
        "close_velocity": round(closed / opened, 2) if opened else None,
        "median_days_open": ages[len(ages) // 2] if ages else 0,
        "max_days_open": ages[-1] if ages else 0,
        "reposts": sum(e.get("repost_count", 0) for e in board.values()),
    }


def stale_jobs(history: dict, min_days: int = 30, today: str = None):
    """Live jobs open at least min_days — the Warm Req candidate pool.
    Sorted oldest first. Yields (board_key, job_id, features)."""
    today = today or date.today().isoformat()
    out = []
    for board_key, board in history.items():
        for job_id, entry in board.items():
            f = job_features(entry, today)
            if f["is_live"] and f["days_open"] >= min_days:
                out.append((board_key, job_id, f))
    return sorted(out, key=lambda t: -t[2]["days_open"])


def summarize(history: dict, today: str = None) -> dict:
    today = today or date.today().isoformat()
    boards = len(history)
    jobs = sum(len(b) for b in history.values())
    live = sum(1 for b in history.values() for e in b.values()
               if not e.get("gone_since"))
    reposts = sum(e.get("repost_count", 0)
                  for b in history.values() for e in b.values())
    firsts = [e.get("first_seen") for b in history.values()
              for e in b.values() if e.get("first_seen")]
    return {
        "boards": boards, "jobs_tracked": jobs, "live": live,
        "closed": jobs - live, "total_reposts": reposts,
        "history_starts": min(firsts) if firsts else None,
        "days_of_history": _days(today, min(firsts)) if firsts else 0,
    }
