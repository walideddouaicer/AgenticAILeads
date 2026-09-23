"""One Streamlit-free decision path for agency outreach and its send history."""

from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import core
from minilist_engine import apply_sample_review, build_minilist
from storage import ConflictError, revision, update_json


def business_timezone():
    name = os.environ.get("AAL_BUSINESS_TIMEZONE", "UTC")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown AAL_BUSINESS_TIMEZONE: {name}") from error


def local_now(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    return now.astimezone(business_timezone())


def initial_sends_today(status, now=None):
    """Count distinct recorded first sends; old stages are only a lower bound."""
    day = local_now(now).date()
    seen = set()
    for key, entry in status.items():
        if not isinstance(entry, dict):
            continue
        actions = entry.get("activity") or []
        has_initial = False
        for action in actions:
            if not isinstance(action, dict) or action.get("type") != "initial_send":
                continue
            has_initial = True
            try:
                when = datetime.fromisoformat(action["at"])
                if when.tzinfo and when.astimezone(business_timezone()).date() == day:
                    seen.add(action.get("id") or (key, action["at"]))
            except (KeyError, TypeError, ValueError):
                continue
        # An old entry has no reliable action history. Count a same-day
        # contacted stage conservatively and label the total as incomplete.
        if not has_initial and entry.get("status") == "contacted" and entry.get("on") == day.isoformat():
            seen.add((key, "legacy"))
    return len(seen)


def _channel(prospect):
    linkedin = (prospect.get("linkedin") or "").strip()
    email = (prospect.get("email") or "").strip()
    parsed = urlsplit(linkedin)
    if (parsed.scheme == "https" and parsed.hostname in
            ("linkedin.com", "www.linkedin.com") and
            parsed.path.startswith(("/in/", "/pub/"))):
        return "linkedin"
    if ("@" in email and "." in email.split("@")[-1]
            and "locked" not in email.lower() and " " not in email):
        return "email"
    return ""


def prospect_context(prospect, entry, signals, cfg, *, minilist_n=10):
    """Build the same hook, reviewed sample and draft for page and CLI."""
    mandates = core.mandates_for_agency(prospect.get("agency", ""), signals)
    if (entry.get("niche") or "").strip():
        niche = core.parse_csv_line(entry["niche"])
        niche_source = "override"
    else:
        niche = core.derive_niche_keywords(
            [m.get("mandate_title", "") for m in mandates])
        niche_source = "board postings" if niche else "stated"
        if not niche:
            niche = core.parse_csv_line(prospect.get("niche_keywords", ""))
    if mandates:
        role = mandates[0].get("mandate_title", "").strip()
        location = mandates[0].get("location", "").strip()
        hook = f"saw your board advertising a {role} role" + (f" in {location}" if location else "")
        hook_source = "board posting"
    elif niche:
        hook = f"saw you specialize in {' / '.join(niche[:3])} placements"
        hook_source = "stated niche"
    else:
        hook, hook_source = "", "none"
    market = core.market_tokens(prospect.get("location", ""))
    rows = (build_minilist(niche, locations=market, n=minilist_n,
                           state_file=core.STATE_FILE,
                           contacts_file=core.CONTACTS_CACHE)
            if niche else [])
    approved = apply_sample_review(rows, entry.get("sample_review"))
    fu = int(entry.get("fu", 0) or 0)
    stage = core.effective_status(entry)
    kind = "followup2" if stage == "contacted" and fu else (
        "followup1" if stage == "contacted" else "initial")
    template = {"initial": cfg["dm_template"],
                "followup1": cfg["followup_template"],
                "followup2": cfg["followup2_template"]}[kind]
    generated = core.render_prospect_dm(template, prospect, hook, approved)
    sample_claim = any(token in template for token in
                       ("{sample_count", "{contact_count", "{sample_summary"))
    saved_draft = (entry.get("drafts") or {}).get(kind)
    sample_changed = bool(saved_draft is not None and sample_claim and
                          (entry.get("draft_sample_fingerprints") or {}).get(kind)
                          != revision(approved))
    draft = generated if sample_changed else (
        saved_draft if saved_draft is not None else generated)
    if generated.startswith("Template needs correction:"):
        # A saved draft must not keep an old sample claim eligible after its
        # approved rows become stale or are withdrawn.
        draft = generated
    return {"mandates": mandates, "niche": niche,
            "niche_source": niche_source, "hook": hook,
            "hook_source": hook_source, "sample": approved,
            "sample_rows": rows, "draft": draft, "draft_kind": kind,
            "draft_sample_changed": sample_changed,
            "channel": _channel(prospect)}


def compute_queue(prospects, status, signals, cfg, icp, *, now=None,
                  limit=core.DAILY_QUEUE_CAP, minilist_n=10):
    """Return actionable work and explicit blockers without reading today.json."""
    now = local_now(now)
    by_key = {core.prospect_key(p): p for p in prospects}
    replies, followups, candidates, blocked_followups = [], [], [], []
    blocked = {}
    for key, prospect in by_key.items():
        entry = status.get(key) or {}
        stage = core.effective_status(entry)
        if stage in ("replied", "call_booked"):
            replies.append({"key": key, "prospect": prospect, "stage": stage})
            continue
        if stage in ("dismissed", "client_won", "won"):
            continue
        if stage == "contacted" and not core.followup_due(entry, now=now):
            continue
        context = prospect_context(prospect, entry, signals, cfg,
                                   minilist_n=minilist_n)
        reasons = []
        if not (prospect.get("agency") or "").strip() or not (prospect.get("person_name") or "").strip():
            reasons.append("Confirm agency and person identity")
        if not context["channel"]:
            reasons.append("Add a usable LinkedIn URL or email")
        if not context["hook"]:
            reasons.append("Add a supported opener or niche")
        if not context["draft"].strip() or context["draft"].startswith("Template needs correction:"):
            reasons.append("Correct the message or approve a current sample")
        if reasons:
            blocked[key] = reasons
            if stage == "contacted":
                blocked_followups.append({"key": key, "prospect": prospect,
                                          "reasons": reasons})
            continue
        item = {"key": key, "prospect": prospect, "context": context,
                "is_followup": stage == "contacted",
                "followup_number": int(entry.get("fu", 0) or 0) + 1 if stage == "contacted" else 0,
                "matches_icp": core.matches_icp(prospect, icp)}
        if stage == "contacted":
            followups.append(item)
        else:
            candidates.append(item)

    def newest_verified(item):
        stamps = []
        for row in item["context"]["sample"]:
            try:
                stamps.append(datetime.fromisoformat(row["verified_at"]).timestamp())
            except (KeyError, TypeError, ValueError):
                continue
        return max(stamps, default=0)

    # Recent verified evidence, reviewed sample size and stable identity.
    candidates.sort(key=lambda item: (
        -newest_verified(item), -len(item["context"]["sample"]),
        item["key"]))
    followups.sort(key=lambda item: (-item["followup_number"], item["key"]))
    blocked_followups.sort(key=lambda item: item["key"])
    replies.sort(key=lambda item: item["key"])
    used = initial_sends_today(status, now)
    remaining = max(int(limit) - used, 0)
    return {"date": now.date().isoformat(), "replies": replies,
            "followups": followups, "blocked_followups": blocked_followups,
            "new": candidates[:remaining],
            "new_available": len(candidates), "blocked": blocked,
            "initial_sent_today": used, "remaining_initial": remaining,
            "budget": int(limit), "legacy_counts_incomplete": any(
                isinstance(e, dict) and e.get("status") in
                ("contacted", "replied", "call_booked", "client_won")
                and not e.get("activity") for e in status.values())}


def current_queue(*, now=None, limit=core.DAILY_QUEUE_CAP, minilist_n=10):
    return compute_queue(core.load_prospects(),
                         core.load_status(core.PROSPECTS_STATUS),
                         core.load_prospect_signals(),
                         core.load_prospects_config(), core.load_icp(),
                         now=now, limit=limit, minilist_n=minilist_n)


def save_draft(status, key, kind, message, *, expected, sample=()):
    if kind not in ("initial", "followup1", "followup2"):
        raise ValueError("Unknown draft kind")
    def apply(entry):
        entry = entry or {"status": "new", "on": "", "notes": ""}
        drafts = dict(entry.get("drafts") or {})
        drafts[kind] = str(message)
        entry["drafts"] = drafts
        fingerprints = dict(entry.get("draft_sample_fingerprints") or {})
        fingerprints[kind] = revision(list(sample))
        entry["draft_sample_fingerprints"] = fingerprints
        return entry
    from storage import update_json_record
    latest, entry, _ = update_json_record(core.PROSPECTS_STATUS, key, apply,
                                          expected=expected)
    status.clear(); status.update(latest)
    return entry


def record_send(status, key, *, message, sample, channel, action_id=None,
                operator="founder", expected, now=None,
                budget=core.DAILY_QUEUE_CAP):
    """Atomically record a real manual send and enforce the shared daily budget."""
    if not message or not str(message).strip():
        raise ValueError("Enter the final message before recording a send")
    if str(message).startswith("Template needs correction:"):
        raise ValueError("Correct the message template before recording a send")
    if channel not in ("linkedin", "email"):
        raise ValueError("Choose a usable send channel")
    now = local_now(now)
    action_id = action_id or uuid.uuid4().hex
    outcome = {}

    def apply(records):
        entry = records.get(key) or {"status": "new", "on": "", "notes": ""}
        if any(a.get("id") == action_id for a in entry.get("activity", [])
               if isinstance(a, dict)):
            outcome["duplicate"] = True
            return
        if revision(records.get(key)) != expected:
            raise ConflictError("This prospect changed in another session. Reload before recording the send.")
        stage = core.effective_status(entry)
        if stage == "new":
            if initial_sends_today(records, now) >= budget:
                raise ValueError("Today's initial-send budget is used. Resume tomorrow.")
            kind = "initial_send"
        elif stage == "contacted" and core.followup_due(entry, now=now):
            kind = "followup_send"
        else:
            raise ValueError("This prospect is not ready for a send. Reload the queue.")
        action = {"id": action_id, "at": now.astimezone(timezone.utc).isoformat(),
                  "operator": operator, "type": kind, "channel": channel,
                  "message": str(message), "sample": copy.deepcopy(list(sample))}
        entry = copy.deepcopy(entry)
        entry.setdefault("activity", []).append(action)
        entry["status"] = "contacted"
        entry["on"] = now.date().isoformat()
        if kind == "followup_send":
            entry["fu"] = int(entry.get("fu", 0) or 0) + 1
        records[key] = entry
        outcome["action"] = action

    latest = update_json(core.PROSPECTS_STATUS, apply, default={},
                         expected_type=dict, indent=1)
    status.clear(); status.update(latest)
    return outcome.get("action"), outcome.get("duplicate", False)
