#!/usr/bin/env python3
"""
Agentic AI Leads — Signal Engine, Phase 2 (Greenhouse + Lever + Ashby + Workable)

Watches companies' public job boards across 4 ATS platforms and reports
roles opened since the last run — each new role is an outreach signal.

Usage:
  python signal_engine.py            # normal run (fetch, diff, report)
  python signal_engine.py --baseline # record state without alerting

State: signals_state.json | Output: printed + appended to signals.csv

How to find board tokens (Google these, token is in the result URLs):
  Greenhouse: site:boards.greenhouse.io "devops"     -> boards.greenhouse.io/<TOKEN>
  Lever:      site:jobs.lever.co "data engineer"     -> jobs.lever.co/<TOKEN>
  Ashby:      site:jobs.ashbyhq.com "backend"        -> jobs.ashbyhq.com/<TOKEN>
  Workable:   site:apply.workable.com "security"     -> apply.workable.com/<TOKEN>
"""

import argparse
import json
import sys
import time
from datetime import date

import requests

from evidence import (BoardResponseError, board_key, record_refreshes,
                      utc_now, validate_snapshot)
import sweep_core
from runtime import DATA_DIR
from sweep_core import (commit_sweep_batch, enable_utf8_stdout, load_history,
                        load_json_state, record_observations,
                        recover_sweep_checkpoint, summarize)
from tls_compat import enable_os_trust_store

enable_os_trust_store()

# Phase 3: per-client filtering. If filter_engine.py + configs/ exist,
# every new job is routed through every active client profile.
try:
    from filter_engine import load_configs, evaluate_job
except ImportError:
    load_configs = None

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

# Watchlist lives in watchlist.json (manage it from the app's Watchlist tab).
# The dict below is only the fallback if that file is missing/broken.
_FALLBACK_WATCHLIST = {
    "greenhouse": [
        "gitlab", "datadog", "elastic", "cloudflare",
        "mongodb", "figma", "postman", "vercel",
    ],
    "lever": ["plaid", "spotify"],
    "ashby": ["linear", "sentry"],
    "workable": ["huggingface"],
}

_WATCHLIST_FILE = DATA_DIR / "watchlist.json"


def load_watchlist():
    if _WATCHLIST_FILE.exists():
        try:
            wl = json.loads(_WATCHLIST_FILE.read_text(encoding="utf-8"))
            return {p: [str(t).strip().lower() for t in wl.get(p, []) if str(t).strip()]
                    for p in ["greenhouse", "lever", "ashby", "workable"]}
        except (json.JSONDecodeError, OSError):
            print("  [!] watchlist.json unreadable — using built-in fallback list")
    return _FALLBACK_WATCHLIST


WATCHLIST = load_watchlist()

# FALLBACK ONLY — used when no configs/ directory exists. Once Phase 3
# configs are in place, edit filters in configs/*.json (or via app.py UI).
KEYWORDS = [
    "engineer", "developer", "devops", "data", "backend", "frontend",
    "security", "cloud", "machine learning", "sre", "platform",
]

REQUEST_DELAY_SECONDS = 1.0
TIMEOUT = 15
UA = {"User-Agent": "signal-engine/0.2 (polite nightly sweep)"}

# ----------------------------------------------------------------------

STATE_FILE = DATA_DIR / "signals_state.json"
CSV_FILE = DATA_DIR / "signals.csv"
# Append-only job history — days_open / repost_count / rewritten live here.
# Unlike STATE_FILE this is never rebuilt from scratch; see sweep_core.
HISTORY_FILE = DATA_DIR / "job_history.json"
CHECKPOINT_FILE = DATA_DIR / "signal_sweep.pending.json"
REFRESH_FILE = DATA_DIR / "board_refresh.json"
FIELDNAMES = ["date", "client", "platform", "company", "title",
              "location", "score", "url", "hook"]
SIGNAL_KEY_FIELDS = ["date", "client", "platform", "company", "url"]

# Greenhouse only returns `offices` (the real city) when content=true, at
# the cost of shipping every job description too. Worth it: without it,
# boards that type "Hybrid" into the location box are ungeocodable, and
# those are some of the largest boards we track. Flip to False if sweeps
# become too slow.
GREENHOUSE_CONTENT = True


def _get_json(url: str):
    r = requests.get(url, timeout=TIMEOUT, headers=UA)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _job_rows(data, platform):
    """Reject malformed ATS responses before an empty snapshot is inferred."""
    rows = data if platform == "lever" else (
        data.get("jobs") if isinstance(data, dict) else None)
    if not isinstance(rows, list) or any(not isinstance(j, dict) for j in rows):
        raise BoardResponseError(f"{platform}: expected a jobs list")
    return rows


# --- one fetcher per platform; all return {job_id: normalized_job} or None ---

def _offices(j):
    """Greenhouse office names — the real city when `location.name` is a
    working arrangement like 'Hybrid'. Only present with content=true.

    Verified against a live Cloudflare response:
        "location": {"name": "Hybrid"}
        "offices": [{"id": 19994, "name": "Washington, DC",
                     "location": "Washington, DC, United States"}]

    Note `offices[].location` is a plain STRING here, not the {"name": ...}
    object that the job-level `location` field uses. Prefer it — it carries
    the country — but fall back to `name`, and never assume either type.
    """
    out = []
    for o in (j.get("offices") or []):
        if not isinstance(o, dict):
            continue
        loc = o.get("location")
        if isinstance(loc, dict):
            loc = loc.get("name")
        out.append(str(loc or o.get("name") or "").strip())
    # Two is plenty: enough for a multi-site role to match either market,
    # short enough to stay readable in the table.
    return ", ".join(x for x in out if x)[:120] if out else ""


def fetch_greenhouse(token):
    # content=true costs a bigger payload (it includes the job description)
    # but is the only way to get `offices`. Without it, boards that type
    # "Hybrid" or "In-Office" into the location box give us no place at all
    # — 199 Cloudflare jobs and 274 SpaceX jobs were unusable for geo
    # matching. Set GREENHOUSE_CONTENT = False if sweeps get too slow.
    suffix = "?content=true" if GREENHOUSE_CONTENT else ""
    data = _get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs{suffix}")
    if data is None:
        return None
    out = {}
    for j in _job_rows(data, "greenhouse"):
        raw = (j.get("location") or {}).get("name") or ""
        place = sweep_core.best_location(raw, _offices(j))
        _, workplace = sweep_core.split_workplace(raw)
        out[str(j["id"])] = {
            "title": (j.get("title") or "").strip(),
            # Blank when nothing geographic is recoverable. Repeating
            # "Hybrid" in a column called `location` is the bug
            # we are fixing; `workplace` carries that already.
            "location": place,
            "workplace": workplace,
            "url": j.get("absolute_url", ""),
        }
    return out


def fetch_lever(token):
    data = _get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    if data is None:
        return None
    out = {}
    for j in _job_rows(data, "lever"):
        cats = j.get("categories") or {}
        raw = cats.get("location") or ""
        # Lever carries the arrangement separately in workplaceType, and
        # often a country/allLocations list alongside the display string.
        all_locs = j.get("allLocations") or cats.get("allLocations") or []
        place = sweep_core.best_location(
            raw, ", ".join(x for x in all_locs if isinstance(x, str)),
            j.get("country") or "")
        workplace = (j.get("workplaceType") or "").lower()
        if not workplace:
            _, workplace = sweep_core.split_workplace(raw)
        out[str(j.get("id"))] = {
            "title": (j.get("text") or "").strip(),
            "location": place,
            "workplace": workplace,
            "url": j.get("hostedUrl", ""),
        }
    return out


def fetch_ashby(token):
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    if data is None:
        return None
    out = {}
    for j in _job_rows(data, "ashby"):
        raw = j.get("location") or ""
        # Ashby exposes a structured postal address and a secondary list;
        # either can name a city when `location` is generic.
        postal = ((j.get("address") or {}).get("postalAddress") or {})
        structured = ", ".join(x for x in [
            postal.get("addressLocality"), postal.get("addressRegion"),
            postal.get("addressCountry")] if x)
        secondary = ", ".join(
            s.get("location", "") for s in (j.get("secondaryLocations") or [])
            if isinstance(s, dict))
        place = sweep_core.best_location(raw, structured, secondary)
        _, workplace = sweep_core.split_workplace(raw)
        if j.get("isRemote") and not workplace:
            workplace = "remote"
        out[str(j.get("id") or j.get("jobUrl") or j.get("title"))] = {
            "title": (j.get("title") or "").strip(),
            "location": place,
            "workplace": workplace,
            "url": j.get("jobUrl", ""),
        }
    return out


def fetch_workable(token):
    data = _get_json(f"https://apply.workable.com/api/v1/widget/accounts/{token}")
    if data is None:
        return None
    jobs = {}
    for j in _job_rows(data, "workable"):
        jid = str(j.get("shortcode") or j.get("id") or j.get("url"))
        # Workable is the well-behaved one: city and country are already
        # separate fields, so this rarely needs rescuing.
        loc = ", ".join(x for x in [j.get("city"), j.get("state"),
                                    j.get("country")] if x)
        place = sweep_core.best_location(loc, j.get("location") or "")
        workplace = "remote" if j.get("telecommuting") else ""
        if not workplace:
            _, workplace = sweep_core.split_workplace(loc)
        jobs[jid] = {
            "title": (j.get("title") or "").strip(),
            "location": place,
            "workplace": workplace,
            "url": j.get("url", ""),
        }
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workable": fetch_workable,
}


def fetch_board(platform, token):
    """Fetch and validate one complete snapshot before any caller uses it."""
    key = board_key(platform, token)
    try:
        jobs = FETCHERS[platform](token)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise BoardResponseError(f"{key}: malformed ATS response") from error
    if jobs is None:  # 404 is a failed check, never an empty board.
        return None
    return validate_snapshot(jobs, key)


def matches_keywords(title: str) -> bool:
    if not KEYWORDS:
        return True
    t = title.lower()
    return any(k in t for k in KEYWORDS)


def make_hook(new_jobs: list) -> str:
    n = len(new_jobs)
    sample = new_jobs[0]["title"]
    loc = new_jobs[0]["location"]
    where = f" in {loc}" if loc else ""
    if n == 1:
        return f"saw your board advertising a {sample} role{where}"
    return (f"saw your board advertising {n} roles{where}, "
            f"including {sample}")


def load_state():
    # migrate Phase-1 keys ("gitlab") to Phase-2 keys ("greenhouse:gitlab")
    return load_json_state(
        STATE_FILE,
        migrate=lambda state: {
            (k if ":" in k else f"greenhouse:{k}"): v
            for k, v in state.items()})


def probe_board(spec: str):
    """Fetch ONE board and show what we'd store. Run this before trusting a
    fetcher change — the four ATS APIs disagree about where the real city
    lives, and the only way to know is to look.

        python signal_engine.py --probe greenhouse:cloudflare
    """
    platform, _, token = spec.partition(":")
    if platform not in FETCHERS:
        sys.exit(f"unknown platform {platform!r}; "
                 f"use one of {', '.join(FETCHERS)}")
    jobs = fetch_board(platform, token)
    if jobs is None:
        sys.exit(f"board not found: {spec}")
    print(f"{spec}: {len(jobs)} job(s)\n")
    blank = sum(1 for j in jobs.values() if not j.get("location"))
    generic = sum(1 for j in jobs.values()
                  if sweep_core.split_workplace(
                      j.get("location", ""))[0] == "")
    for j in list(jobs.values())[:15]:
        wp = f"  [{j.get('workplace')}]" if j.get("workplace") else ""
        print(f"  {j['location'][:44]:<46}{wp:<12} {j['title'][:40]}")
    print(f"\n  {blank} blank location(s), {generic} still generic "
          f"(no city recoverable) out of {len(jobs)}")
    if generic > len(jobs) // 2:
        print("  [!] most of this board has no recoverable place — the "
              "company genuinely didn't publish one.")


def main():
    enable_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--probe", metavar="PLATFORM:TOKEN",
                    help="fetch one board and print the locations we'd "
                         "store, without writing anything")
    args = ap.parse_args()

    if args.probe:
        return probe_board(args.probe)

    recovered = recover_sweep_checkpoint(
        CHECKPOINT_FILE, STATE_FILE, HISTORY_FILE, CSV_FILE,
        FIELDNAMES, SIGNAL_KEY_FIELDS)
    if recovered:
        print("Recovered the previous interrupted signal sweep.")

    state = load_state()
    history = load_history(HISTORY_FILE)
    hist_totals = {"new": 0, "still_open": 0, "reposted": 0,
                   "rewritten": 0, "closed": 0}
    first_run = not state
    today = date.today().isoformat()
    all_signals = []
    outcomes = {}
    total = sum(len(v) for v in WATCHLIST.values())

    configs = load_configs() if load_configs else []
    mode = f"{len(configs)} client config(s)" if configs else "legacy KEYWORDS filter"
    print(f"Signal engine sweep — {today} — {total} companies, "
          f"{sum(1 for v in WATCHLIST.values() if v)} platform(s) — {mode}\n")

    for platform, tokens in WATCHLIST.items():
        for token in tokens:
            key = board_key(platform, token)
            try:
                jobs = fetch_board(platform, token)
            except (requests.RequestException, BoardResponseError) as e:
                print(f"  [!] {key}: fetch failed ({e.__class__.__name__})")
                outcomes[key] = {"status": "failed", "at": utc_now(),
                                 "reason": e.__class__.__name__}
                time.sleep(REQUEST_DELAY_SECONDS)
                continue
            time.sleep(REQUEST_DELAY_SECONDS)

            if jobs is None:
                print(f"  [!] {key}: board not found (check token)")
                outcomes[key] = {"status": "failed", "at": utc_now(),
                                 "reason": "not_found"}
                continue

            checked_at = utc_now()
            jobs = {job_id: {**job, "observed_at": checked_at}
                    for job_id, job in jobs.items()}
            outcomes[key] = {"status": "success", "at": checked_at,
                             "job_count": len(jobs)}

            known = set(state.get(key, {}))
            fresh_key = key not in state  # newly added company -> baseline it silently
            raw_new = [jobs[j] for j in jobs if j not in known]

            # Route new jobs: per-client configs (Phase 3) or legacy keywords
            if configs:
                matched = []
                for cfg in configs:
                    matched += [r for r in (evaluate_job(j, cfg) for j in raw_new) if r]
            else:
                matched = [dict(j, lead_score="", client_id="default")
                           for j in raw_new if matches_keywords(j["title"])]

            if matched and not (first_run or fresh_key or args.baseline):
                # group per client so each client gets their own hook sentence
                by_client = {}
                for m in matched:
                    by_client.setdefault(m["client_id"], []).append(m)
                print(f"● {key}: {len(matched)} new signal(s) "
                      f"[{', '.join(by_client)}]")
                for client, jobs_c in by_client.items():
                    hook = make_hook(jobs_c)
                    for j in jobs_c:
                        print(f"    - ({client}, {j['lead_score']}) {j['title']}  [{j['location']}]")
                        all_signals.append({
                            "date": today, "client": client, "platform": platform,
                            "company": token, "title": j["title"],
                            "location": j["location"], "score": j["lead_score"],
                            "url": j["url"], "hook": hook,
                        })
            else:
                note = " (baseline)" if (first_run or fresh_key or args.baseline) else ", no new"
                print(f"  {key}: {len(jobs)} live jobs tracked{note}")

            # Record BEFORE overwriting state: the history is the only
            # thing here that cannot be re-derived later.
            for k, v in record_observations(history, key, jobs, today).items():
                hist_totals[k] += v

            state[key] = jobs

    appended = commit_sweep_batch(
        CHECKPOINT_FILE, STATE_FILE, HISTORY_FILE, CSV_FILE,
        FIELDNAMES, SIGNAL_KEY_FIELDS, state, history, all_signals)
    record_refreshes(REFRESH_FILE, outcomes)

    s = summarize(history, today)
    print(f"\nHistory: {s['jobs_tracked']} job(s) across {s['boards']} board(s)"
          f" — {s['live']} live, {s['closed']} closed, "
          f"{s['days_of_history']} day(s) of history."
          f"\n  this run: +{hist_totals['new']} new, "
          f"{hist_totals['closed']} closed, "
          f"{hist_totals['reposted']} repost(s), "
          f"{hist_totals['rewritten']} rewritten")

    if all_signals:
        print(f"\n{appended} signal(s) appended to {CSV_FILE.name}")
    elif not (first_run or args.baseline):
        print("\nNo new signals today.")
    else:
        print(f"\nBaseline recorded for {len(state)} boards. "
              "Run again tomorrow — differences become signals.")


if __name__ == "__main__":
    sys.exit(main())
