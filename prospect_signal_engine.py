#!/usr/bin/env python3
"""
Agentic AI Leads — Prospect Signal Engine (mandate sweep).

Sweeps the AGENCY boards in agency_watchlist.json (built by
agency_discovery.py) exactly like signal_engine.py sweeps client boards:
fetch, diff against last run, report what's new. Each NEW posting on an
agency board is a prospect signal — "this agency just took a mandate" —
the freshest possible hook for OUR own outreach to that agency.

Usage:
  python prospect_signal_engine.py             # normal run (fetch, diff, report)
  python prospect_signal_engine.py --baseline  # record state without alerting

State:  prospect_signals_state.json   (same shape as signals_state.json)
Output: appended to prospect_signals.csv
        (date, agency, mandate_title, location, url, hook)

Like the main engine, the first run ever and the first sweep of any newly
added board record a baseline silently — no false "new mandate" flood.
Kept fully separate from the client pipeline: separate watchlist,
separate state, separate CSV; nothing here touches client configs.
"""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

from signal_engine import FETCHERS, REQUEST_DELAY_SECONDS
from sweep_core import (commit_sweep_batch, enable_utf8_stdout, load_history,
                        load_json_state, record_observations,
                        recover_sweep_checkpoint, summarize)
from tls_compat import enable_os_trust_store

enable_os_trust_store()

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AAL_DATA_DIR", HERE))
AGENCY_WATCHLIST_FILE = DATA_DIR / "agency_watchlist.json"
STATE_FILE = DATA_DIR / "prospect_signals_state.json"
CSV_FILE = DATA_DIR / "prospect_signals.csv"
# Separate history file — the two pipelines never share state, and an
# agency's mandate age is a different thing from a client's req age.
HISTORY_FILE = DATA_DIR / "mandate_history.json"
CHECKPOINT_FILE = DATA_DIR / "mandate_sweep.pending.json"

FIELDNAMES = ["date", "agency", "mandate_title", "location", "url", "hook"]
SIGNAL_KEY_FIELDS = ["date", "agency", "url"]


def make_hook(title: str, location: str) -> str:
    where = f" in {location}" if location else ""
    return f"saw you're working a {title} mandate{where}"


def load_agency_watchlist():
    if not AGENCY_WATCHLIST_FILE.exists():
        sys.exit("agency_watchlist.json not found — build it first:\n"
                 "  python agency_discovery.py --from-prospects")
    try:
        wl = json.loads(AGENCY_WATCHLIST_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        sys.exit("agency_watchlist.json unreadable — fix or delete it, then "
                 "re-run agency_discovery.py")
    return {p: [str(t).strip().lower() for t in wl.get(p, []) if str(t).strip()]
            for p in ["greenhouse", "lever", "ashby", "workable"]}


def load_state():
    return load_json_state(STATE_FILE)


def main():
    enable_utf8_stdout()
    ap = argparse.ArgumentParser(
        description="Sweep agency boards for new client mandates "
                    "(prospect signals).")
    ap.add_argument("--baseline", action="store_true",
                    help="record state without alerting")
    args = ap.parse_args()

    recovered = recover_sweep_checkpoint(
        CHECKPOINT_FILE, STATE_FILE, HISTORY_FILE, CSV_FILE,
        FIELDNAMES, SIGNAL_KEY_FIELDS)
    if recovered:
        print("Recovered the previous interrupted mandate sweep.")

    watchlist = load_agency_watchlist()
    state = load_state()
    history = load_history(HISTORY_FILE)
    first_run = not state
    today = date.today().isoformat()
    signals = []
    total = sum(len(v) for v in watchlist.values())

    if total == 0:
        sys.exit("agency_watchlist.json is empty — add boards with:\n"
                 "  python agency_discovery.py --from-prospects")

    print(f"Mandate sweep — {today} — {total} agency board(s)\n")

    for platform, tokens in watchlist.items():
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
            fresh_key = key not in state   # new board -> baseline silently
            new_jobs = [jobs[j] for j in jobs if j not in known]

            if new_jobs and not (first_run or fresh_key or args.baseline):
                print(f"● {key}: {len(new_jobs)} new mandate(s)")
                for j in new_jobs:
                    print(f"    - {j['title']}  [{j['location']}]")
                    signals.append({
                        "date": today, "agency": token,
                        "mandate_title": j["title"],
                        "location": j["location"], "url": j["url"],
                        "hook": make_hook(j["title"], j["location"]),
                    })
            else:
                note = (" (baseline)" if (first_run or fresh_key or args.baseline)
                        else ", no new")
                print(f"  {key}: {len(jobs)} live mandate(s) tracked{note}")

            # Mandate age is the hook: an agency still advertising a role
            # after 6 weeks is visibly struggling to fill it.
            record_observations(history, key, jobs, today)

            state[key] = jobs

    appended = commit_sweep_batch(
        CHECKPOINT_FILE, STATE_FILE, HISTORY_FILE, CSV_FILE,
        FIELDNAMES, SIGNAL_KEY_FIELDS, state, history, signals)

    s = summarize(history, today)
    print(f"\nMandate history: {s['jobs_tracked']} mandate(s), {s['live']} "
          f"live, {s['days_of_history']} day(s) of history.")

    if signals:
        print(f"\n{appended} prospect signal(s) appended to "
              f"{CSV_FILE.name} — work them from the app's Prospects page.")
    elif not (first_run or args.baseline):
        print("\nNo new mandates today.")
    else:
        print(f"\nBaseline recorded for {len(state)} agency board(s). "
              "Run again tomorrow — new mandates become prospect signals.")


if __name__ == "__main__":
    sys.exit(main())
