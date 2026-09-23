#!/usr/bin/env python3
"""
Agentic AI Leads — Enrichment Engine: companies -> humans to contact.

Finds recruiting/TA decision-makers for a set of companies and caches them
in enriched_contacts.json. The vendor behind it is pluggable — see
contacts.py; this script only decides WHICH companies to enrich and
handles caching, rate limits and output.

WHICH COMPANIES (this is the part that used to be wrong)
--------------------------------------------------------
By default it enriched every company in signals.csv. But the mini-list —
the gift we actually send prospects — is built from signals_state.json,
a different and much larger pool. The two barely overlap, so the default
spent credits on companies our gift never mentions while every mini-list
still showed "0 with named contacts".

Pick the source deliberately:
  --from-minilists   companies that actually appear in prospect mini-lists,
                     ranked by how many prospects would see them. THIS IS
                     USUALLY THE ONE YOU WANT — a handful of companies
                     cover most mini-list slots.
  --companies X      explicit list: a file (one name per line) or a
                     comma-separated string
  (default)          distinct companies in signals.csv — the client-lead
                     pipeline, useful once you have paying clients

Usage:
  python enrichment_engine.py --probe gitlab        # raw response, verify FIRST
  python enrichment_engine.py --from-minilists --limit 25
  python enrichment_engine.py --companies todo.txt
  python enrichment_engine.py --list-companies --from-minilists   # dry, free
  python enrichment_engine.py                       # signals.csv companies

Setup: put APOLLO_API_KEY=xxxx in a `.env` next to this script. Without a
key the engine reports what it WOULD do and exits 0 — the nightly chain
stays quiet instead of erroring every night.

Output: enriched_contacts.json (joined with signals.csv when the app reads it)
Cache:  enriched_contacts.json (company -> contacts; companies are never
        re-queried, so re-runs only hit the API for new companies). This
        cache is permanent value: a trial spent filling it keeps paying
        after the trial ends.

Optional: company_domains.json mapping ATS tokens to real domains improves
match quality a lot:  {"gitlab": "gitlab.com", "huggingface": "huggingface.co"}
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import contacts as contacts_api
from runtime import DATA_DIR, ENV_FILE
from storage import read_json, update_json_record
from tls_compat import enable_os_trust_store

enable_os_trust_store()

SIGNALS_CSV = DATA_DIR / "signals.csv"
CACHE_FILE = DATA_DIR / "enriched_contacts.json"
DOMAINS_FILE = DATA_DIR / "company_domains.json"
MAPPINGS_FILE = DATA_DIR / "company_board_mappings.json"
LOOKUP_FILE = DATA_DIR / "contact_lookup_status.json"

RATE_LIMIT_SLEEP = 2.0     # free tier: 50/min, 600/day — stay far under


def load_json(path: Path, default):
    return read_json(path, default=default, expected_type=type(default))


# ------------------------- which companies -------------------------

def companies_from_signals() -> list:
    """Only explicitly mapped companies from client signals."""
    if not SIGNALS_CSV.exists():
        sys.exit("signals.csv not found — run signal_engine.py first.")
    with SIGNALS_CSV.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        sys.exit("signals.csv is empty.")
    mappings = load_json(MAPPINGS_FILE, {})
    targets = set()
    for row in rows:
        board = f"{row.get('platform', '')}:{row.get('company', '')}".lower()
        mapping = mappings.get(board)
        if isinstance(mapping, dict):
            key = mapping.get("contacts_key") or mapping.get("company")
            if key:
                targets.add(key)
    return sorted(targets)


def companies_from_minilists(n_per_list: int = 10) -> list:
    """Companies that actually appear in prospect mini-lists, most-featured
    first.

    This is the set whose missing contacts make every gift say '0 with
    named contacts'. It is usually far smaller than it sounds — a few
    dozen companies cover the large majority of slots — because every
    prospect's list is drawn from the same live-jobs pool.

    Imports are local: both modules are Streamlit-free, but neither is
    needed for the default path and one of them reads a large state file.
    """
    from collections import Counter

    import core
    from minilist_engine import build_minilist

    prospects = core.load_prospects()
    if not prospects:
        sys.exit("prospects.csv not found or empty — nothing to build "
                 "mini-lists from. Run the prospector or import a list.")
    mappings = load_json(MAPPINGS_FILE, {})
    tally = Counter()
    for p in prospects:
        kws = core.parse_csv_line(p.get("niche_keywords") or "")
        if not kws:
            continue
        for row in build_minilist(kws, n=n_per_list):
            if not row.get("verified_at"):
                continue
            mapping = mappings.get(row.get("source_board"), {})
            if isinstance(mapping, dict):
                key = mapping.get("contacts_key") or mapping.get("company")
                if key:
                    tally[key] += 1
    if not tally:
        sys.exit("No mapped mini-list companies — review matching jobs and "
                 "confirm their board-to-company identity before a provider lookup.")
    return [c for c, _ in tally.most_common()], tally


def merge_contacts(current, found):
    """Keep manual/corrected fields; fill blanks and add distinct people."""
    merged = [dict(c) for c in (current or []) if isinstance(c, dict)]
    for incoming in found:
        if not isinstance(incoming, dict):
            continue
        existing = next((c for c in merged if any(
            incoming.get(field) and str(incoming[field]).strip().casefold()
            == str(c.get(field) or "").strip().casefold()
            for field in ("linkedin", "email", "name"))), None)
        if existing is None:
            merged.append(dict(incoming))
        else:
            for field in ("name", "title", "linkedin", "email"):
                if not existing.get(field) and incoming.get(field):
                    existing[field] = incoming[field]
    return merged


def lookup_due(company, cache, checks, now, refresh_days=30,
               retry_empty_days=7):
    entry = checks.get(company, {})
    try:
        last = datetime.fromisoformat(entry.get("last_attempt", ""))
        if last.tzinfo is None:
            raise ValueError("naive time")
    except (TypeError, ValueError):
        # Existing contacts without provider metadata may be manual. Do not
        # spend credits refreshing them automatically.
        return not bool(cache.get(company))
    days = refresh_days if cache.get(company) else retry_empty_days
    return now - last >= timedelta(days=days)


def companies_from_arg(value: str) -> list:
    """--companies accepts a file (one per line) or a comma-separated list."""
    p = Path(value)
    if p.exists():
        names = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()]
    else:
        names = value.split(",")
    out = [n.strip() for n in names if n.strip() and not n.strip().startswith("#")]
    if not out:
        sys.exit(f"--companies {value!r} produced no company names.")
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Find decision-maker contacts for a set of companies.")
    ap.add_argument("--probe", metavar="COMPANY",
                    help="query one company and dump the raw JSON response — "
                         "run this FIRST after any plan or key change")
    ap.add_argument("--limit", type=int, default=100,
                    help="max API calls this run (default 100)")
    ap.add_argument("--refresh-days", type=int, default=30,
                    help="refresh old provider results after this many days")
    ap.add_argument("--retry-empty-days", type=int, default=7,
                    help="retry no-result lookups after this many days")
    ap.add_argument("--force-refresh", action="store_true",
                    help="explicitly recheck selected companies, including manual contacts")
    ap.add_argument("--provider", default=None,
                    help=f"force a provider "
                         f"({', '.join(c.name for c in contacts_api.PROVIDERS)}); "
                         "default is the first one with a key")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--companies", metavar="FILE_OR_LIST",
                     help="explicit companies: a file (one per line) or a "
                          "comma-separated string")
    src.add_argument("--from-minilists", action="store_true",
                     dest="from_minilists",
                     help="companies that appear in prospect mini-lists, "
                          "most-featured first — usually what you want")
    ap.add_argument("--list-companies", action="store_true",
                    dest="list_companies",
                    help="print the target companies and exit; spends nothing")
    args = ap.parse_args()

    domains = load_json(DOMAINS_FILE, {})
    mapped_domains = {}
    for mapping in load_json(MAPPINGS_FILE, {}).values():
        if isinstance(mapping, dict) and mapping.get("domain"):
            key = mapping.get("contacts_key") or mapping.get("company")
            if key:
                mapped_domains.setdefault(key, set()).add(mapping["domain"])
    for key, choices in mapped_domains.items():
        if key not in domains and len(choices) == 1:
            domains[key] = next(iter(choices))

    provider = (contacts_api.get_provider(args.provider, env_file=ENV_FILE)
                if args.provider
                else contacts_api.active_provider(env_file=ENV_FILE))

    if args.probe:
        if provider is None or not provider.is_configured():
            sys.exit("No contact provider configured; add a key before probing.")
        try:
            parsed, raw = provider.find_contacts(
                args.probe, domains.get(args.probe))
        except contacts_api.ProviderForbidden as e:
            sys.exit(str(e))
        print(json.dumps(raw, indent=2)[:4000])
        print("\n--- parsed contacts ---")
        print(json.dumps(parsed, indent=2))
        return

    # ---- pick the company set (free, no API calls) ----
    tally = None
    if args.companies:
        companies, source = companies_from_arg(args.companies), args.companies
    elif args.from_minilists:
        companies, tally = companies_from_minilists()
        source = "prospect mini-lists"
    else:
        companies, source = companies_from_signals(), "signals.csv"

    if args.list_companies:
        print(f"{len(companies)} company/companies from {source}:")
        for c in companies:
            extra = f"  (in {tally[c]} mini-list(s))" if tally else ""
            print(f"  {c}{extra}")
        return

    # ---- a missing key is not an error; say so and exit clean ----
    if provider is None or not provider.is_configured():
        names = ", ".join(f"{c.name} ({c.key_env})"
                          for c in contacts_api.PROVIDERS)
        print(f"No contact provider configured — set one of: {names} "
              f"in {ENV_FILE.name}.")
        print(f"Would enrich {len(companies)} company/companies from {source}. "
              "Nothing spent, nothing changed.")
        return

    cache = load_json(CACHE_FILE, {})
    checks = load_json(LOOKUP_FILE, {})
    now = datetime.now(timezone.utc)
    todo = [c for c in companies if args.force_refresh or lookup_due(
        c, cache, checks, now, args.refresh_days, args.retry_empty_days)]
    print(f"provider: {provider.name} | source: {source}")
    print(f"{len(companies)} company/companies — {len(todo)} need enrichment "
          "(recent lookups and manual-only contacts are held)")

    calls = 0
    for company in todo:
        if calls >= args.limit:
            print(f"Hit --limit {args.limit}, stopping (re-run to continue).")
            break
        try:
            found, _ = provider.find_contacts(company, domains.get(company))
        except contacts_api.ProviderForbidden as e:
            # Plan/key problem: stop immediately rather than burning the
            # rest of the run on calls that will all fail the same way.
            print(str(e))
            break
        except requests.RequestException as e:
            update_json_record(LOOKUP_FILE, company, lambda current: {
                "last_attempt": datetime.now(timezone.utc).isoformat(),
                "status": "failed", "provider": provider.name})
            print(f"  [!] {company}: lookup failed ({e.__class__.__name__})")
            continue
        calls += 1
        if found == contacts_api.RATE_LIMITED:
            print("  [!] rate limited — stopping, re-run later.")
            break
        if found:
            _, saved, _ = update_json_record(
                CACHE_FILE, company,
                lambda current: merge_contacts(current, found))
            cache[company] = saved
        update_json_record(LOOKUP_FILE, company, lambda current: {
            "last_attempt": datetime.now(timezone.utc).isoformat(),
            "status": "found" if found else "empty",
            "provider": provider.name})
        label = (f"{len(found)} contact(s)" if found
                 else "ZERO contacts (not cached; check name/domain)")
        print(f"  {company}: {label}")
        time.sleep(RATE_LIMIT_SLEEP)

    cache = load_json(CACHE_FILE, {})
    hit = sum(1 for c in companies if cache.get(c))
    print(f"\ncache: {hit}/{len(companies)} target companies now have "
          f"contacts ({len(cache)} total in {CACHE_FILE.name})")

    print(f"API calls used this run: {calls}. The app joins cached contacts "
          "with signals when it reads them.")


if __name__ == "__main__":
    main()
