#!/usr/bin/env python3
"""
Agentic AI Leads — Block 2: Enrichment Engine (Apollo.io free-tier API).

Takes companies from signals.csv and finds the humans to contact:
recruiting/TA decision-makers, via Apollo's api_search endpoint
(free tier: no credits consumed for search; emails often locked — you
still get name, title, LinkedIn URL, which is all LinkedIn outreach needs).

Setup:
  1. Apollo -> Settings -> Integrations -> API -> create key
  2. Put it in a file named `.env` next to this script:  APOLLO_API_KEY=xxxx

Usage:
  python enrichment_engine.py --probe gitlab   # dump raw API response for one
                                               # company (run this FIRST to
                                               # verify the response shape)
  python enrichment_engine.py                  # enrich all companies in signals.csv
  python enrichment_engine.py --limit 20       # cap API calls this run

Output: enriched_signals.csv (signals rows + contact columns)
Cache:  enriched_contacts.json (company -> contacts; companies are never
        re-queried, so re-runs only hit the API for new companies)

Optional: company_domains.json mapping ATS tokens to real domains improves
match quality a lot:  {"gitlab": "gitlab.com", "huggingface": "huggingface.co"}
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import requests

from tls_compat import enable_os_trust_store

enable_os_trust_store()

HERE = Path(__file__).resolve().parent
SIGNALS_CSV = HERE / "signals.csv"
OUT_CSV = HERE / "enriched_signals.csv"
CACHE_FILE = HERE / "enriched_contacts.json"
DOMAINS_FILE = HERE / "company_domains.json"
ENV_FILE = HERE / ".env"

API_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"

# Shared with prospector_engine.py — the same endpoint, the same failure mode.
MASTER_KEY_HINT = (
    "Most common cause: your key is a SCOPED key — this endpoint "
    "requires a MASTER API key. Fix: Apollo -> Settings -> "
    "Integrations -> API -> enable 'master API key' on your key "
    "(or create a new one with master enabled), update .env, retry.\n"
    "If the message says the endpoint isn't available on your plan, "
    "people search via API may require a paid tier for your account "
    "— fall back to manual Apollo UI searches (free credits) until "
    "revenue funds the upgrade.")

# Decision-maker titles for "company is hiring" signals: the people who own
# hiring and would buy recruitment-agency services.
TARGET_TITLES = [
    "talent acquisition", "technical recruiter", "recruiting manager",
    "head of talent", "head of people", "head of hr", "hr director",
    "people operations", "founder", "co-founder",
]

RATE_LIMIT_SLEEP = 2.0     # free tier: 50/min, 600/day — stay far under
CONTACTS_PER_COMPANY = 2
TIMEOUT = 20


def load_api_key() -> str:
    import os
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.strip().startswith("APOLLO_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    key = os.environ.get("APOLLO_API_KEY", "")
    if not key:
        sys.exit("No API key. Create a .env file containing: APOLLO_API_KEY=your_key")
    return key


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return default


def search_company(api_key: str, company: str, domain: str | None):
    """One API call. Returns (contacts_list, raw_response)."""
    payload = {
        "person_titles": TARGET_TITLES,
        "page": 1,
        "per_page": 10,
    }
    if domain:
        payload["q_organization_domains_list"] = [domain]
    else:
        payload["q_organization_name"] = company

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
    data = r.json()

    # Response shape is defensive: people may arrive in 'people' and/or 'contacts'
    raw_people = (data.get("people") or []) + (data.get("contacts") or [])
    contacts = []
    for p in raw_people:
        name = p.get("name") or " ".join(
            x for x in [p.get("first_name"), p.get("last_name")] if x)
        email = p.get("email") or ""
        if "not_unlocked" in email:
            email = "(locked — reveal in Apollo UI costs 1 credit)"
        contacts.append({
            "name": name,
            "title": p.get("title") or "",
            "linkedin": p.get("linkedin_url") or "",
            "email": email,
        })
        if len(contacts) >= CONTACTS_PER_COMPANY:
            break
    return contacts, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", metavar="COMPANY",
                    help="query one company and dump the raw JSON response")
    ap.add_argument("--limit", type=int, default=100,
                    help="max API calls this run (default 100)")
    args = ap.parse_args()

    api_key = load_api_key()
    domains = load_json(DOMAINS_FILE, {})

    if args.probe:
        contacts, raw = search_company(api_key, args.probe, domains.get(args.probe))
        print(json.dumps(raw, indent=2)[:4000])
        print("\n--- parsed contacts ---")
        print(json.dumps(contacts, indent=2))
        return

    if not SIGNALS_CSV.exists():
        sys.exit("signals.csv not found — run signal_engine.py first.")

    rows = list(csv.DictReader(SIGNALS_CSV.open(encoding="utf-8")))
    if not rows:
        sys.exit("signals.csv is empty.")

    cache = load_json(CACHE_FILE, {})
    companies = sorted({r["company"] for r in rows})
    todo = [c for c in companies if c not in cache]
    print(f"{len(companies)} companies in signals.csv — "
          f"{len(todo)} need enrichment (rest cached)")

    calls = 0
    for company in todo:
        if calls >= args.limit:
            print(f"Hit --limit {args.limit}, stopping (re-run to continue).")
            break
        contacts, _ = search_company(api_key, company, domains.get(company))
        calls += 1
        if contacts == "RATE_LIMITED":
            print("  [!] rate limited — stopping, re-run later.")
            break
        cache[company] = contacts
        label = f"{len(contacts)} contact(s)" if contacts else "ZERO contacts (check name/domain)"
        print(f"  {company}: {label}")
        time.sleep(RATE_LIMIT_SLEEP)

    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    # Merge contacts into every signal row
    fieldnames = list(rows[0].keys())
    for i in range(1, CONTACTS_PER_COMPANY + 1):
        for f in ["name", "title", "linkedin", "email"]:
            col = f"contact{i}_{f}"
            if col not in fieldnames:
                fieldnames.append(col)

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            contacts = cache.get(r["company"], [])
            for i, c in enumerate(contacts[:CONTACTS_PER_COMPANY], 1):
                for k, v in c.items():
                    r[f"contact{i}_{k}"] = v
            w.writerow(r)

    print(f"\nWrote {OUT_CSV.name} ({len(rows)} rows). "
          f"API calls used this run: {calls}")


if __name__ == "__main__":
    main()
