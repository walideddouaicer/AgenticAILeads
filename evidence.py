"""Board identity and refresh evidence shared by collectors and samples."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage import read_json, update_json


FRESH_HOURS = 48  # Operational policy, not a claim about ATS publication lag.


class BoardResponseError(ValueError):
    """An ATS answered, but its payload cannot safely replace a snapshot."""


def board_key(platform, token):
    return f"{str(platform).strip().lower()}:{str(token).strip().lower()}"


def validate_snapshot(jobs, key):
    """Accept an empty board, but never an incomplete or malformed job list."""
    if not isinstance(jobs, dict):
        raise BoardResponseError(f"{key}: jobs are not a mapping")
    for job_id, job in jobs.items():
        if (not str(job_id).strip() or str(job_id) == "None"
                or not isinstance(job, dict)
                or not isinstance(job.get("title"), str)
                or not job["title"].strip()
                or not isinstance(job.get("url"), str)
                or not job["url"].startswith(("https://", "http://"))):
            raise BoardResponseError(f"{key}: incomplete job entry")
    return jobs


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_refreshes(path, outcomes):
    """Save attempted checks after the sweep batch is durably committed.

    A failed attempt retains last_success for inspection, but its status is
    failed, so it cannot authorize a present-tense claim.
    """
    if not outcomes:
        return

    def merge(latest):
        for key, result in outcomes.items():
            previous = latest.get(key, {})
            entry = {"status": result["status"],
                     "last_attempt": result["at"],
                     "last_success": previous.get("last_success"),
                     "job_count": previous.get("job_count"),
                     "reason": result.get("reason", "")}
            if result["status"] == "success":
                entry["last_success"] = result["at"]
                entry["job_count"] = result["job_count"]
            latest[key] = entry
        return latest

    update_json(path, merge, default={}, expected_type=dict, indent=2)


def load_refreshes(path):
    return read_json(Path(path), default={}, expected_type=dict)


def verified_at(key, refreshes, now=None, max_age_hours=FRESH_HOURS):
    """Return the last check time only for a recent successful board check."""
    entry = refreshes.get(key, {})
    if not isinstance(entry, dict) or entry.get("status") != "success":
        return None
    try:
        stamp = datetime.fromisoformat(entry["last_success"])
        if stamp.tzinfo is None:
            return None
        current = now or datetime.now(timezone.utc)
        age = current - stamp
        if timedelta(0) <= age <= timedelta(hours=max_age_hours):
            return entry["last_success"]
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return None


def confirmed_boards(agency_name, mappings):
    """Only an explicit agency-name entry authorizes a board association."""
    name = str(agency_name or "").strip().casefold()
    if not name:
        return set()
    return {str(key).strip().lower() for key in mappings.get(name, [])
            if isinstance(key, str) and ":" in key}
