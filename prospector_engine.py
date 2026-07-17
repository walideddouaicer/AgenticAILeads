#!/usr/bin/env python3
"""
Agentic AI Leads — Prospector Engine: find OUR OWN clients.

Searches Apollo's api_search endpoint for decision-makers at small
recruitment/staffing agencies (the people who buy pay-per-meeting lead
gen). Replaces the manual Apollo-UI → Google-Sheet workflow.

RUN --probe FIRST. Apollo's accepted filter parameter names vary by
plan/version, so before trusting a full run:

  python prospector_engine.py --probe

...dumps the exact payload sent and the raw JSON response (also saved to
prospector_probe.json). Check that (a) the response actually respects the
filters (agency-ish companies, 2-20 employees, right countries) and
(b) people arrive under 'people' and/or 'contacts'. If a filter is
ignored or rejected, adjust build_payload() to the names your plan
accepts — candidates to try: q_organization_keyword_tags,
organization_industry_tag_ids, organization_num_employees_ranges,
person_locations.

Usage:
  python prospector_engine.py --probe          # one search, dump raw JSON
  python prospector_engine.py                  # full run with defaults
  python prospector_engine.py --titles "founder, owner" \
      --keywords "staffing, recruiting" --size 2,20 \
      --locations "United States, United Kingdom" --pages 3 --limit 20

Output: rows appended to prospects.csv (never overwrites your edits —
existing rows are untouched; dedupe happens via prospects_seen.json,
keyed on LinkedIn URL, falling back to agency+person name).

Rate limits (Apollo free tier: 50/min, 600/day): 2s sleep between calls,
hard cap per run via --limit (default 20 calls).

Setup: same .env as enrichment_engine.py (APOLLO_API_KEY=..., master key).
"""

import argparse
import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

from enrichment_engine import API_URL, MASTER_KEY_HINT, load_api_key
from tls_compat import enable_os_trust_store

enable_os_trust_store()

HERE = Path(__file__).resolve().parent
PROSPECTS_CSV = HERE / "prospects.csv"
SEEN_FILE = HERE / "prospects_seen.json"
PROBE_FILE = HERE / "prospector_probe.json"

DEFAULT_TITLES = ["founder", "co-founder", "managing director", "owner",
                  "director", "principal consultant"]
DEFAULT_KEYWORDS = ["staffing", "recruiting", "recruitment",
                    "talent acquisition", "executive search", "headhunting"]
DEFAULT_SIZE = "2,20"
DEFAULT_LOCATIONS = ["United States", "United Kingdom", "France"]

RATE_LIMIT_SLEEP = 2.0     # free tier: 50/min, 600/day — stay far under
PER_PAGE = 25
TIMEOUT = 20

FIELDNAMES = ["date_found", "agency", "person_name", "person_title",
              "linkedin", "email", "location", "size", "source",
              "niche_keywords"]


def build_payload(titles, keywords, size, locations, page,
                  per_page=PER_PAGE):
    """The search payload.

    NOTE: filter parameter names vary by Apollo plan/version — this is
    the candidate set to verify with --probe. person_titles and
    pagination are stable (enrichment_engine uses them); the org-side
    filters are the ones to watch. If the probe shows a filter being
    ignored, try the alternatives listed in the module docstring.
    """
    return {
        "person_titles": titles,
        "person_locations": locations,
        "q_organization_keyword_tags": keywords,
        "organization_num_employees_ranges": [size],
        "page": page,
        "per_page": per_page,
    }


def search(api_key: str, payload: dict):
    """One API call. Returns ('RATE_LIMITED', None) on 429."""
    r = requests.post(
        API_URL, json=payload, timeout=TIMEOUT,
        headers={"X-Api-Key": api_key, "Content-Type": "application/json",
                 "Cache-Control": "no-cache"})
    if r.status_code == 403:
        sys.exit("403 from Apollo. Raw response:\n" + r.text[:800]
                 + "\n\n" + MASTER_KEY_HINT)
    if r.status_code == 429:
        return "RATE_LIMITED", None
    r.raise_for_status()
    return None, r.json()


def parse_people(data: dict):
    """Defensive parse of one response page -> list of prospect dicts.

    Mirrors enrichment_engine: people may arrive under 'people' and/or
    'contacts'; emails are often locked on the free tier.
    """
    raw_people = (data.get("people") or []) + (data.get("contacts") or [])
    out = []
    for p in raw_people:
        if not isinstance(p, dict):
            continue
        name = p.get("name") or " ".join(
            x for x in [p.get("first_name"), p.get("last_name")] if x)
        email = p.get("email") or ""
        if "not_unlocked" in email:
            email = "(locked — reveal in Apollo UI costs 1 credit)"
        org = p.get("organization") or p.get("account") or {}
        if not isinstance(org, dict):
            org = {}
        location = ", ".join(
            x for x in [p.get("city"), p.get("state"), p.get("country")] if x)
        out.append({
            "agency": org.get("name") or "",
            "person_name": name,
            "person_title": p.get("title") or "",
            "linkedin": p.get("linkedin_url") or "",
            "email": email,
            "location": location,
            "size": str(org.get("estimated_num_employees") or ""),
        })
    return out


def seen_key(person: dict) -> str:
    """Dedupe key: LinkedIn URL, else normalized agency+name."""
    lk = (person.get("linkedin") or "").strip().lower().rstrip("/")
    if lk:
        return lk
    return (f"{(person.get('agency') or '').strip().lower()}"
            f"|{(person.get('person_name') or '').strip().lower()}")


def load_seen():
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def split_csv(s):
    return [x.strip() for x in s.split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser(
        description="Find decision-makers at small recruitment agencies "
                    "via Apollo api_search. Run --probe first.")
    ap.add_argument("--probe", action="store_true",
                    help="run ONE search, print the payload and raw JSON "
                         "response, save it to prospector_probe.json, and "
                         "exit — do this before the first real run")
    ap.add_argument("--titles", default=", ".join(DEFAULT_TITLES),
                    help="person titles, comma-separated")
    ap.add_argument("--keywords", default=", ".join(DEFAULT_KEYWORDS),
                    help="organization keyword tags, comma-separated")
    ap.add_argument("--size", default=DEFAULT_SIZE,
                    help='employee range as "min,max" (default 2,20)')
    ap.add_argument("--locations", default=", ".join(DEFAULT_LOCATIONS),
                    help="person locations, comma-separated")
    ap.add_argument("--pages", type=int, default=3,
                    help="result pages to fetch this run (default 3)")
    ap.add_argument("--limit", type=int, default=20,
                    help="hard cap on API calls this run (default 20)")
    args = ap.parse_args()

    api_key = load_api_key()
    titles = split_csv(args.titles)
    keywords = split_csv(args.keywords)
    locations = split_csv(args.locations)

    if args.probe:
        payload = build_payload(titles, keywords, args.size, locations,
                                page=1, per_page=5)
        print("Payload sent:")
        print(json.dumps(payload, indent=2))
        err, data = search(api_key, payload)
        if err == "RATE_LIMITED":
            sys.exit("Rate limited (429) — wait a minute and retry.")
        PROBE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print("\nRaw response (first 4000 chars — full copy in "
              f"{PROBE_FILE.name}):")
        print(json.dumps(data, indent=2)[:4000])
        print("\n--- parsed prospects ---")
        print(json.dumps(parse_people(data), indent=2))
        print("\nCheck: are these small recruitment agencies in the right "
              "countries? If a filter was ignored, adjust build_payload() "
              "(see module docstring) and probe again.")
        return

    seen = load_seen()
    today = date.today().isoformat()
    calls, added, skipped = 0, 0, 0
    new_rows = []

    for page in range(1, args.pages + 1):
        if calls >= args.limit:
            print(f"Hit --limit {args.limit}, stopping (re-run to continue).")
            break
        payload = build_payload(titles, keywords, args.size, locations, page)
        err, data = search(api_key, payload)
        calls += 1
        if err == "RATE_LIMITED":
            print("  [!] rate limited — stopping, re-run later.")
            break
        people = parse_people(data)
        print(f"  page {page}: {len(people)} people")
        if not people:
            break
        for person in people:
            k = seen_key(person)
            if k in seen:
                skipped += 1
                continue
            seen[k] = today
            new_rows.append({
                "date_found": today, **person, "source": "apollo",
                "niche_keywords": "",
            })
            added += 1
        time.sleep(RATE_LIMIT_SLEEP)

    if new_rows:
        # append-only: existing rows (and your edits to them) are never touched
        new_file = not PROSPECTS_CSV.exists()
        with PROSPECTS_CSV.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            if new_file:
                w.writeheader()
            w.writerows(new_rows)
    SEEN_FILE.write_text(json.dumps(seen, indent=1), encoding="utf-8")

    print(f"\n{added} new prospect(s) appended to {PROSPECTS_CSV.name} "
          f"({skipped} already known, {calls} API call(s) used).")
    if added:
        print("Next: fill niche_keywords in the CSV (or let the Prospects "
              "page derive them from mandate signals), then work them from "
              "the app.")


if __name__ == "__main__":
    main()
