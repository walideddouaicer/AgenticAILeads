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
import json
import sys
import time
from datetime import date

import requests
from storage import (append_csv_unique, atomic_write_json, read_json,
                     update_json)

# Same Apollo endpoint and the same failure mode as contact enrichment —
# both live in the provider layer so there is one place to fix when a plan
# or an endpoint changes.
from contacts import ApolloProvider, load_env_key
from runtime import DATA_DIR, ENV_FILE

API_URL = ApolloProvider.url
MASTER_KEY_HINT = ApolloProvider.setup_hint


def load_api_key() -> str:
    key = load_env_key("APOLLO_API_KEY", ENV_FILE)
    if not key:
        sys.exit("No API key. Create a .env file containing: "
                 "APOLLO_API_KEY=your_key")
    return key
from tls_compat import enable_os_trust_store

enable_os_trust_store()

PROSPECTS_CSV = DATA_DIR / "prospects.csv"
SEEN_FILE = DATA_DIR / "prospects_seen.json"
PROBE_FILE = DATA_DIR / "prospector_probe.json"

# The ICP lives in icp.json and is edited in the app — see core.load_icp.
# These are only the fallback when core can't be imported (it always can,
# but an engine that dies on an import is worse than one that runs with
# sane defaults). CLI flags still override everything.
try:
    from core import icp_size_range, load_icp
    _ICP = load_icp()
    DEFAULT_TITLES = list(_ICP["titles"])
    DEFAULT_KEYWORDS = list(_ICP["keywords"])
    DEFAULT_SIZE = icp_size_range(_ICP)
    DEFAULT_LOCATIONS = list(_ICP["countries"])
except Exception:                                    # pragma: no cover
    DEFAULT_TITLES = ["founder", "co-founder", "managing director", "owner",
                      "director"]
    DEFAULT_KEYWORDS = ["staffing", "recruiting", "recruitment",
                        "talent acquisition", "executive search"]
    DEFAULT_SIZE = "10,50"
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
            # Apollo already returns the agency's own blurb and tags; we
            # were throwing them away and writing niche_keywords blank,
            # which is the ONE field Clay gave us that Apollo "didn't".
            # It did — we just didn't read it. See derive_niche_from_org.
            "niche_keywords": derive_niche_from_org(org),
        })
    return out


# Words that appear in every recruitment agency's blurb and therefore
# distinguish nobody. Without this the derived niche for all of them is
# "recruitment, talent, staffing".
_ORG_STOPWORDS = {
    "recruitment", "recruiting", "recruiter", "recruiters", "staffing",
    "talent", "acquisition", "search", "headhunting", "headhunters",
    "consultancy", "consulting", "agency", "agencies", "solutions",
    "services", "partners", "group", "ltd", "limited", "inc", "llc",
    "specialists", "specialising", "specializing", "specialists",
    "leading", "global", "international", "best", "top", "premier",
    "trusted", "proven", "years", "experience", "clients", "candidates",
    "people", "team", "teams", "business", "businesses", "companies",
    "hiring", "jobs", "roles", "placement", "placements", "permanent",
    "contract", "freelance", "market", "markets", "sector", "sectors",
    "industry", "industries", "based", "across", "help", "helping",
    "work", "working", "connect", "connecting", "find", "finding",
}


def derive_niche_from_org(org: dict, top_n: int = 5) -> str:
    """Agency blurb + Apollo tags -> a comma-separated niche string.

    Reads `short_description`, `keywords` and `industry` — all of which
    Apollo returns and the old parser ignored. Output goes straight into
    the niche_keywords column, so a prospector-sourced row arrives with
    the same field Clay-sourced rows had.

    Known limit, same as Clay's: this is what the agency ADVERTISES, not
    what it is currently working. Live mandates beat it — that's why
    niche_for() ranks mandates above this column.
    """
    import re as _re
    from collections import Counter

    # core already knows the role nouns and generic title words; add the
    # English function words a prose blurb brings that a job title doesn't.
    from core import _NICHE_STOPWORDS
    stop = (_ORG_STOPWORDS | _NICHE_STOPWORDS | {
        "we", "our", "us", "you", "your", "they", "their", "it", "its",
        "is", "are", "was", "were", "be", "been", "has", "have", "had",
        "do", "does", "did", "will", "would", "can", "could", "who",
        "what", "which", "that", "this", "these", "those", "from", "by",
        "as", "into", "over", "under", "than", "then", "also", "more",
        "most", "very", "well", "since", "up", "out", "about", "between",
    })

    text = " ".join(str(x) for x in [
        org.get("short_description") or "",
        org.get("industry") or "",
        " ".join(org.get("keywords") or []) if isinstance(
            org.get("keywords"), list) else (org.get("keywords") or ""),
    ])
    counts = Counter()
    for w in _re.findall(r"[a-z0-9.+#/]+", text.lower()):
        w = w.strip("./")
        if len(w) < 2 or w.isdigit() or w in stop:
            continue
        counts[w] += 1
    return ", ".join(w for w, _ in counts.most_common(top_n))


def seen_key(person: dict) -> str:
    """Dedupe key: LinkedIn URL, else normalized agency+name."""
    lk = (person.get("linkedin") or "").strip().lower().rstrip("/")
    if lk:
        return lk
    return (f"{(person.get('agency') or '').strip().lower()}"
            f"|{(person.get('person_name') or '').strip().lower()}")


def load_seen():
    return read_json(SEEN_FILE, default={}, expected_type=dict)


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
        atomic_write_json(PROBE_FILE, data, indent=2)
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
            # niche_keywords now comes from parse_people (derived from the
            # org blurb); the old blank default would overwrite it.
            new_rows.append({
                "date_found": today, "source": "apollo", **person,
            })
            added += 1
        time.sleep(RATE_LIMIT_SLEEP)

    if new_rows:
        # append-only: existing rows (and your edits to them) are never touched
        added = append_csv_unique(
            PROSPECTS_CSV, new_rows, FIELDNAMES, key=seen_key)

    discovered = {seen_key(row): today for row in new_rows}
    if discovered:
        update_json(SEEN_FILE, lambda latest: latest.update(discovered),
                    default={}, expected_type=dict, indent=1)

    print(f"\n{added} new prospect(s) appended to {PROSPECTS_CSV.name} "
          f"({skipped} already known, {calls} API call(s) used).")
    if added:
        print("Next: fill niche_keywords in the CSV (or let the Prospects "
              "page derive them from mandate signals), then work them from "
              "the app.")


if __name__ == "__main__":
    main()
