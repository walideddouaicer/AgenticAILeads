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

Output: enriched_signals.csv (signals rows + contact columns)
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
import os
import sys
import time
from pathlib import Path

import contacts as contacts_api
from tls_compat import enable_os_trust_store

enable_os_trust_store()

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AAL_DATA_DIR", HERE))
SIGNALS_CSV = DATA_DIR / "signals.csv"
OUT_CSV = DATA_DIR / "enriched_signals.csv"
CACHE_FILE = DATA_DIR / "enriched_contacts.json"
DOMAINS_FILE = DATA_DIR / "company_domains.json"
ENV_FILE = HERE / ".env"

RATE_LIMIT_SLEEP = 2.0     # free tier: 50/min, 600/day — stay far under
CONTACTS_PER_COMPANY = contacts_api.CONTACTS_PER_COMPANY


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return default


# ------------------------- which companies -------------------------

def companies_from_signals() -> list:
    """Distinct companies in signals.csv — the client-lead pipeline."""
    if not SIGNALS_CSV.exists():
        sys.exit("signals.csv not found — run signal_engine.py first.")
    rows = list(csv.DictReader(SIGNALS_CSV.open(encoding="utf-8")))
    if not rows:
        sys.exit("signals.csv is empty.")
    return sorted({r["company"] for r in rows if r.get("company")})


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
    tally = Counter()
    for p in prospects:
        kws = core.parse_csv_line(p.get("niche_keywords") or "")
        if not kws:
            continue
        for row in build_minilist(kws, n=n_per_list):
            tally[row["company"]] += 1
    if not tally:
        sys.exit("No mini-list companies — prospects have no niche_keywords, "
                 "or no tracked jobs match them.")
    return [c for c, _ in tally.most_common()], tally


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

    provider = (contacts_api.get_provider(args.provider, env_file=ENV_FILE)
                if args.provider
                else contacts_api.active_provider(env_file=ENV_FILE))

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

    if args.probe:
        try:
            parsed, raw = provider.find_contacts(
                args.probe, domains.get(args.probe))
        except contacts_api.ProviderForbidden as e:
            sys.exit(str(e))
        print(json.dumps(raw, indent=2)[:4000])
        print("\n--- parsed contacts ---")
        print(json.dumps(parsed, indent=2))
        return

    cache = load_json(CACHE_FILE, {})
    todo = [c for c in companies if c not in cache]
    print(f"provider: {provider.name} | source: {source}")
    print(f"{len(companies)} company/companies — {len(todo)} need enrichment "
          f"(rest already cached, never re-queried)")

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
        calls += 1
        if found == contacts_api.RATE_LIMITED:
            print("  [!] rate limited — stopping, re-run later.")
            break
        cache[company] = found
        label = (f"{len(found)} contact(s)" if found
                 else "ZERO contacts (check name/domain)")
        print(f"  {company}: {label}")
        time.sleep(RATE_LIMIT_SLEEP)

    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    hit = sum(1 for c in companies if cache.get(c))
    print(f"\ncache: {hit}/{len(companies)} target companies now have "
          f"contacts ({len(cache)} total in {CACHE_FILE.name})")

    # The signals join below only makes sense for the signals.csv source.
    if source != "signals.csv" or not SIGNALS_CSV.exists():
        return

    # Merge contacts into every signal row
    rows = list(csv.DictReader(SIGNALS_CSV.open(encoding="utf-8")))
    if not rows:
        return
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
