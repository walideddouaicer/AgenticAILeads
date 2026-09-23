"""First-client delivery: verified backfill, manual sends and meetings.

This module has no Streamlit dependency. Prospect acquisition data lives in
different files and is never read or changed here.
"""

import copy
import csv
from datetime import date, datetime, timezone
import io
from pathlib import Path
import re
import uuid

import core
from evidence import load_refreshes, verified_at
from filter_engine import evaluate_job
from storage import (ConflictError, UNSET, append_csv_unique, read_json,
                     revision, update_json, update_json_record)


MEETINGS_FILE = core.DATA_DIR / "client_meetings.json"
LEAD_FIELDS = ["date", "client", "platform", "company", "title",
               "location", "score", "url", "hook"]
REPORT_FIELDS = ["record_type", "client", "date", "company", "role",
                 "stage", "contact", "source_url", "meeting_id",
                 "meeting_attendee", "chargeable", "rate",
                 "recorded_estimate"]


def client_ids(leads, profiles=None):
    """The own-prospecting `default` profile is never a paying client."""
    profiles = core.list_profiles() if profiles is None else profiles
    return sorted(({name for name in profiles if name != "default"}
                   | {row.get("client", "") for row in leads
                      if row.get("client") not in ("", "default")}))


def validate_client_id(client_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(client_id or "")):
        raise ValueError("Client ID needs letters, numbers, hyphens or underscores")
    if client_id == "default":
        raise ValueError("`default` is reserved for the app's own prospecting")
    return client_id


def save_profile(config, *, expected=UNSET, config_dir=None):
    client_id = validate_client_id(config.get("client_id"))
    path = Path(config_dir or core.CONFIG_DIR) / f"{client_id}.json"
    cleaned = copy.deepcopy(config)
    cleaned["client_id"] = client_id
    if int(cleaned.get("meeting_rate", 0)) < 0:
        raise ValueError("Meeting rate cannot be negative")

    def apply(current):
        if expected is not UNSET and revision(current or None) != expected:
            raise ConflictError("This client profile changed. Reload before saving.")
        return cleaned

    return update_json(path, apply, default={}, expected_type=dict, indent=2)


def job_identity(job):
    return revision([job.get("board_key", ""), str(job.get("job_id", ""))])[:20]


def preview_matches(config, jobs, refreshes, *, now=None):
    """Show matching live jobs; only current verified rows can be selected."""
    matches = []
    for job in jobs:
        match = evaluate_job(job, config)
        if not match:
            continue
        checked = verified_at(job.get("board_key", ""), refreshes, now)
        current = bool(checked and checked == job.get("observed_at"))
        matches.append({**match, "selection_id": job_identity(job),
                        "verified_at": checked if current else "",
                        "selectable": current})
    return sorted(matches, key=lambda row: (
        not row["selectable"], row.get("company", ""),
        row.get("title", ""), row.get("selection_id", "")))


def lead_identity(row):
    """Ignore collection date so rerunning backfill next week stays idempotent."""
    url = (row.get("url") or "").strip().casefold()
    return (row.get("client", ""), row.get("platform", ""),
            row.get("company", ""), url or
            ((row.get("title") or "").strip().casefold() + "|" +
             (row.get("location") or "").strip().casefold()))


def contacted_elsewhere(leads, status, linkedin, current_client):
    """Client names already associated with a touched version of this person."""
    target = str(linkedin or "").strip().casefold().rstrip("/")
    if not target:
        return []
    return sorted({row.get("client", "") for row in leads
                   if row.get("client") != current_client
                   and str(row.get("contact1_linkedin") or "").strip()
                   .casefold().rstrip("/") == target
                   and core.effective_status(status.get(core.lead_key(row)))
                   in ("contacted", "replied", "won")})


def backfill_selected(client_id, selected_ids, *, state_file=None,
                      refresh_file=None, csv_file=None, config_dir=None,
                      now=None):
    """Recheck saved profile and current snapshots before appending selections."""
    validate_client_id(client_id)
    config_dir = Path(config_dir or core.CONFIG_DIR)
    raw = read_json(config_dir / f"{client_id}.json", expected_type=dict)
    config = {**raw, "client_id": client_id}
    if not config.get("active", True):
        raise ValueError("This client profile is paused")
    state_file = Path(state_file or core.STATE_FILE)
    refresh_file = Path(refresh_file or core.BOARD_REFRESH_FILE)
    csv_file = Path(csv_file or core.SIGNALS_CSV)
    state = read_json(state_file, default={}, expected_type=dict)
    jobs = []
    for board_key, board_jobs in state.items():
        platform, _, company = board_key.partition(":")
        for job_id, job in board_jobs.items():
            jobs.append({**job, "board_key": board_key, "job_id": str(job_id),
                         "platform": platform, "company": company})
    current = {row["selection_id"]: row for row in preview_matches(
        config, jobs, load_refreshes(refresh_file), now=now)
        if row["selectable"]}
    selected_ids = set(selected_ids)
    missing = selected_ids - set(current)
    if missing:
        raise ValueError("A selected job is no longer verified or matching. Refresh the preview.")
    today = (now.date() if now is not None else date.today()).isoformat()
    rows = []
    for selection_id in sorted(selected_ids):
        job = current[selection_id]
        title = job.get("title", "")
        location = job.get("location", "")
        rows.append({
            "date": today, "client": client_id,
            "platform": job.get("platform", ""),
            "company": job.get("company", ""), "title": title,
            "location": location, "score": str(job.get("lead_score", "")),
            "url": job.get("url", ""),
            "hook": f"saw your board advertising a {title} role"
                    + (f" in {location}" if location else ""),
        })
    added = append_csv_unique(csv_file, rows, LEAD_FIELDS, key=lead_identity)
    return added, len(rows) - added


def record_send(status, key, *, message, channel, contact, expected,
                action_id=None, operator="local operator", now=None,
                status_file=None):
    """Record the exact manually sent client message under the status lock."""
    if not str(message or "").strip():
        raise ValueError("Enter the final message before recording a send")
    if channel not in ("linkedin", "email"):
        raise ValueError("Choose LinkedIn or email")
    if str(message).startswith("Template needs correction:"):
        raise ValueError("Correct the message template before recording a send")
    if not str(contact or "").strip():
        raise ValueError("Add a named contact before recording a send")
    now = now or datetime.now(timezone.utc)
    action_id = action_id or uuid.uuid4().hex
    outcome = {}

    def apply(records):
        current = records.get(key)
        entry = copy.deepcopy(current) if isinstance(current, dict) else {
            "status": "new", "on": "", "notes": ""}
        if any(action.get("id") == action_id
               for action in entry.get("activity", [])
               if isinstance(action, dict)):
            outcome["duplicate"] = True
            return
        if revision(current) != expected:
            raise ConflictError("This lead changed. Reload before recording the send.")
        stage = core.effective_status(entry)
        if stage == "new":
            kind = "initial_send"
        elif stage == "contacted" and core.followup_due(entry, now=now):
            kind = "followup_send"
        else:
            raise ValueError("This lead is not ready for a send")
        action = {"id": action_id, "type": kind,
                  "at": now.astimezone(timezone.utc).isoformat(),
                  "operator": operator, "channel": channel,
                  "contact": str(contact).strip(), "message": str(message)}
        entry.setdefault("activity", []).append(action)
        entry["status"] = "contacted"
        entry["on"] = now.date().isoformat()
        if kind == "followup_send":
            entry["fu"] = int(entry.get("fu", 0) or 0) + 1
        records[key] = entry
        outcome["action"] = action

    latest = update_json(status_file or core.INBOX_STATUS, apply, default={},
                         expected_type=dict, indent=1)
    status.clear(); status.update(latest)
    return outcome.get("action"), outcome.get("duplicate", False)


def meeting_key(client_id, meeting_id):
    return f"{client_id}|{str(meeting_id or '').strip().casefold()}"


def load_meetings(path=None):
    return read_json(path or MEETINGS_FILE, default={}, expected_type=dict)


def default_chargeable(client_id, meetings):
    """The first recorded meeting is free unless an operator chooses otherwise."""
    return any(item.get("client") == client_id for item in meetings.values())


def save_meeting(client_id, meeting_id, held_on, attendee, lead_keys, *,
                 chargeable, notes="", leads=(), path=None, expected=UNSET,
                 today=None):
    """One record per actual meeting, even if linked to several vacancies."""
    validate_client_id(client_id)
    meeting_id = str(meeting_id or "").strip()
    if not meeting_id:
        raise ValueError("Enter a meeting ID or calendar reference")
    try:
        meeting_date = date.fromisoformat(str(held_on))
    except ValueError as error:
        raise ValueError("Enter a valid meeting date") from error
    if meeting_date > (today or date.today()):
        raise ValueError("An attended meeting cannot have a future date")
    if not str(attendee or "").strip():
        raise ValueError("Enter the person who attended")
    allowed = {core.lead_key(row) for row in leads
               if row.get("client") == client_id}
    lead_keys = sorted(set(lead_keys))
    if not lead_keys or not set(lead_keys) <= allowed:
        raise ValueError("Select at least one lead belonging to this client")
    key = meeting_key(client_id, meeting_id)
    value = {"client": client_id, "meeting_id": meeting_id,
             "held_on": str(held_on), "attendee": str(attendee).strip(),
             "lead_keys": lead_keys, "chargeable": bool(chargeable),
             "notes": str(notes or "").strip()}

    def apply(current):
        if expected is UNSET and current is not None:
            raise ValueError("This meeting is already recorded. Select it to edit.")
        if expected is not UNSET and revision(current) != expected:
            raise ConflictError("This meeting changed. Reload before saving.")
        return value

    latest, saved, saved_revision = update_json_record(
        path or MEETINGS_FILE, key, apply, expected=UNSET)
    return latest, saved, saved_revision


def meeting_summary(client_id, meetings, rate, *, selected_lead_keys=None):
    selected = None if selected_lead_keys is None else set(selected_lead_keys)
    items = [value for value in meetings.values()
             if value.get("client") == client_id
             and (selected is None or selected.intersection(
                 value.get("lead_keys") or []))]
    items.sort(key=lambda item: (item.get("held_on", ""),
                                 item.get("meeting_id", "")))
    chargeable = sum(bool(item.get("chargeable")) for item in items)
    return items, (chargeable * int(rate) if rate is not None else None)


def report_rows(client_id, leads, status, meetings, rate):
    """CSV rows match the selected leads and their distinct actual meetings."""
    rows = []
    for lead in leads:
        rows.append({
            "record_type": "lead", "client": client_id,
            "date": lead.get("date", ""), "company": lead.get("company", ""),
            "role": lead.get("title", ""),
            "stage": core.effective_status(status.get(core.lead_key(lead))),
            "contact": lead.get("contact1_name", ""),
            "source_url": lead.get("url", ""), "meeting_id": "",
            "meeting_attendee": "", "chargeable": "", "rate": "",
            "recorded_estimate": "",
        })
    keys = {core.lead_key(lead) for lead in leads}
    selected_meetings, _ = meeting_summary(
        client_id, meetings, rate, selected_lead_keys=keys)
    by_key = {core.lead_key(lead): lead for lead in leads}
    for meeting in selected_meetings:
        billable = bool(meeting.get("chargeable"))
        companies = sorted({by_key[key].get("company", "")
                            for key in meeting.get("lead_keys", [])
                            if key in by_key and by_key[key].get("company")})
        rows.append({
            "record_type": "meeting", "client": client_id,
            "date": meeting.get("held_on", ""),
            "company": "; ".join(companies), "role": "",
            "stage": "attended", "contact": "", "source_url": "",
            "meeting_id": meeting.get("meeting_id", ""),
            "meeting_attendee": meeting.get("attendee", ""),
            "chargeable": "yes" if billable else "no",
            "rate": str(rate) if billable and rate is not None else "",
            "recorded_estimate": (str(rate) if billable and rate is not None
                                  else "0" if not billable else ""),
        })
    return rows


def report_csv(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=REPORT_FIELDS,
                            extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: ("'" + value if isinstance(value, str)
                               and value.lstrip().startswith(("=", "+", "-", "@"))
                               else value)
                         for key, value in row.items()})
    return stream.getvalue()
