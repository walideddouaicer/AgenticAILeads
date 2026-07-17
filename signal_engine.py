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
import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

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

_WATCHLIST_FILE = Path(__file__).resolve().parent / "watchlist.json"


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

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "signals_state.json"
CSV_FILE = HERE / "signals.csv"


def _get_json(url: str):
    r = requests.get(url, timeout=TIMEOUT, headers=UA)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


# --- one fetcher per platform; all return {job_id: normalized_job} or None ---

def fetch_greenhouse(token):
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    if data is None:
        return None
    return {
        str(j["id"]): {
            "title": (j.get("title") or "").strip(),
            "location": ((j.get("location") or {}).get("name") or ""),
            "url": j.get("absolute_url", ""),
        }
        for j in data.get("jobs", [])
    }


def fetch_lever(token):
    data = _get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    if data is None:
        return None
    return {
        str(j.get("id")): {
            "title": (j.get("text") or "").strip(),
            "location": ((j.get("categories") or {}).get("location") or ""),
            "url": j.get("hostedUrl", ""),
        }
        for j in data
    }


def fetch_ashby(token):
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    if data is None:
        return None
    return {
        str(j.get("id") or j.get("jobUrl") or j.get("title")): {
            "title": (j.get("title") or "").strip(),
            "location": j.get("location") or "",
            "url": j.get("jobUrl", ""),
        }
        for j in data.get("jobs", [])
    }


def fetch_workable(token):
    data = _get_json(f"https://apply.workable.com/api/v1/widget/accounts/{token}")
    if data is None:
        return None
    jobs = {}
    for j in data.get("jobs", []):
        jid = str(j.get("shortcode") or j.get("id") or j.get("url"))
        loc = ", ".join(x for x in [j.get("city"), j.get("country")] if x)
        jobs[jid] = {
            "title": (j.get("title") or "").strip(),
            "location": loc,
            "url": j.get("url", ""),
        }
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workable": fetch_workable,
}


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
        return f"saw you just opened a {sample} role{where}"
    return f"saw you just opened {n} new roles{where}, incl. {sample}"


def load_state():
    if not STATE_FILE.exists():
        return {}
    state = json.loads(STATE_FILE.read_text())
    # migrate Phase-1 keys ("gitlab") to Phase-2 keys ("greenhouse:gitlab")
    return {(k if ":" in k else f"greenhouse:{k}"): v for k, v in state.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true")
    args = ap.parse_args()

    state = load_state()
    first_run = not state
    today = date.today().isoformat()
    all_signals = []
    total = sum(len(v) for v in WATCHLIST.values())

    configs = load_configs() if load_configs else []
    mode = f"{len(configs)} client config(s)" if configs else "legacy KEYWORDS filter"
    print(f"Signal engine sweep — {today} — {total} companies, "
          f"{sum(1 for v in WATCHLIST.values() if v)} platform(s) — {mode}\n")

    for platform, tokens in WATCHLIST.items():
        fetch = FETCHERS[platform]
        for token in tokens:
            key = f"{platform}:{token}"
            try:
                jobs = fetch(token)
            except requests.RequestException as e:
                print(f"  [!] {key}: fetch failed ({e.__class__.__name__})")
                time.sleep(REQUEST_DELAY_SECONDS)
                continue
            time.sleep(REQUEST_DELAY_SECONDS)

            if jobs is None:
                print(f"  [!] {key}: board not found (check token)")
                continue

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

            state[key] = jobs

    STATE_FILE.write_text(json.dumps(state))

    if all_signals:
        new_file = not CSV_FILE.exists()
        with CSV_FILE.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(all_signals[0].keys()))
            if new_file:
                w.writeheader()
            w.writerows(all_signals)
        print(f"\n{len(all_signals)} signal(s) appended to {CSV_FILE.name}")
    elif not (first_run or args.baseline):
        print("\nNo new signals today.")
    else:
        print(f"\nBaseline recorded for {len(state)} boards. "
              "Run again tomorrow — differences become signals.")


if __name__ == "__main__":
    sys.exit(main())
