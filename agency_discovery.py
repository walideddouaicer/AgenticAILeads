#!/usr/bin/env python3
"""
Agentic AI Leads — Agency Board Discovery.

Recruitment agencies post their CLIENT MANDATES on the same ATS platforms
we already sweep (Greenhouse/Lever/Ashby/Workable). This script probes
candidate company slugs, fetches live boards, and classifies each live
board as a probable AGENCY board (vs a normal employer board).

A live board is classified as a probable agency when EITHER:
  - the token / company name contains an agency marker
    (recruit, staffing, talent, search, personnel, headhunt, rpo,
    sourcing), OR
  - its postings look like an agency book: >= MIN_BOOK_POSTINGS postings
    spread across >= MIN_BOOK_LOCATIONS distinct locations AND
    >= MIN_BOOK_FAMILIES distinct role families (a simple title-keyword
    classifier — an in-house employer hires mostly within a few families;
    an agency working many mandates hires everywhere, in everything).

Agency boards go into agency_watchlist.json — a SEPARATE file, never the
client watchlist, and never routed through client configs. Sweep them
with prospect_signal_engine.py.

Usage:
  python agency_discovery.py --from-prospects        # slugs from prospects.csv agency names
  python agency_discovery.py --names agencies.txt    # one company name per line
  python agency_discovery.py --slugs "acme-recruiting, talentco"
  Options: --limit N (max board fetches, default 200), --dry-run,
           --retry-dead (re-probe cached dead/non-agency candidates)

Dead and non-agency results are cached in agency_discovery_state.json
(90-day expiry, same idea as discovery_state.json) so re-runs stay fast.
New agency boards are baselined silently on the next prospect sweep.
"""

import argparse
import csv
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from discovery_engine import slug_variants
from signal_engine import FETCHERS
from tls_compat import enable_os_trust_store

enable_os_trust_store()

HERE = Path(__file__).resolve().parent
AGENCY_WATCHLIST_FILE = HERE / "agency_watchlist.json"
STATE_FILE = HERE / "agency_discovery_state.json"
PROSPECTS_CSV = HERE / "prospects.csv"

PLATFORMS = ["greenhouse", "lever", "ashby", "workable"]

DELAY = 1.0             # seconds between board fetches (full job lists)
DEAD_RETRY_DAYS = 90

# ---- classifier ----

AGENCY_MARKERS = ["recruit", "staffing", "talent", "search", "personnel",
                  "headhunt", "rpo", "sourcing"]

# Simple role-family classifier on title keywords: first family with a
# keyword hit wins; unknown titles fall into "other" (counted once).
ROLE_FAMILIES = {
    "engineering": ["engineer", "developer", "devops", "sre", "architect",
                    "programmer", "software", "qa", "tester", "sdet"],
    "data": ["data", "analytics", "analyst", "scientist", "machine learning",
             "bi ", "database"],
    "product": ["product manager", "product owner", "programme", "program "],
    "design": ["designer", "design", "ux", "ui "],
    "marketing": ["marketing", "seo", "content", "brand", "growth",
                  "social media"],
    "sales": ["sales", "account executive", "business development",
              "account manager", "commercial"],
    "finance": ["finance", "accountant", "accounting", "controller",
                "auditor", "payroll", "tax"],
    "legal": ["legal", "counsel", "lawyer", "paralegal", "compliance"],
    "hr": ["hr ", "human resources", "people", "recruiter", "talent"],
    "operations": ["operations", "logistics", "supply chain", "warehouse",
                   "procurement", "buyer"],
    "healthcare": ["nurse", "doctor", "physician", "medical", "clinical",
                   "healthcare", "care ", "dental", "pharmacist", "therapist"],
    "education": ["teacher", "tutor", "teaching", "education", "instructor"],
    "construction": ["construction", "electrician", "plumber", "carpenter",
                     "site manager", "surveyor", "civil"],
    "hospitality": ["chef", "hospitality", "hotel", "restaurant", "barista",
                    "housekeeping"],
    "support": ["customer support", "customer service", "customer success",
                "help desk", "call center", "call centre"],
    "executive": ["ceo", "cfo", "coo", "cto", "chief", "vice president",
                  "vp ", "managing director", "general manager"],
    "admin": ["administrator", "administrative", "receptionist", "assistant",
              "office manager", "secretary"],
    "driver": ["driver", "delivery", "courier", "forklift"],
}

MIN_BOOK_POSTINGS = 10
MIN_BOOK_LOCATIONS = 4
MIN_BOOK_FAMILIES = 5   # "highly diverse": a normal employer rarely hires
                        # across 5+ role families at once; an agency book does.
                        # NB: large employers can still trip this — feed this
                        # script agency-biased candidates (prospects.csv),
                        # and vet --dry-run output before trusting new boards.


def classify_role_family(title: str) -> str:
    t = f" {str(title or '').lower()} "
    for family, kws in ROLE_FAMILIES.items():
        for kw in kws:
            if kw in t:
                return family
    return "other"


def has_agency_marker(name: str) -> bool:
    n = str(name or "").lower()
    return any(m in n for m in AGENCY_MARKERS)


def looks_like_agency_book(jobs: dict) -> bool:
    """jobs: {job_id: {title, location, url}} as returned by the fetchers."""
    if len(jobs) < MIN_BOOK_POSTINGS:
        return False
    locations = {str(j.get("location") or "").strip().lower()
                 for j in jobs.values()}
    locations.discard("")
    if len(locations) < MIN_BOOK_LOCATIONS:
        return False
    families = {classify_role_family(j.get("title", ""))
                for j in jobs.values()}
    return len(families) >= MIN_BOOK_FAMILIES


def is_probable_agency(name_or_token: str, jobs: dict) -> tuple[bool, str]:
    """-> (verdict, reason)"""
    if has_agency_marker(name_or_token):
        return True, "name marker"
    if looks_like_agency_book(jobs):
        return True, (f"agency book: {len(jobs)} postings, "
                      "diverse locations & role families")
    return False, "looks like a normal employer board"


# ---- state / io ----

def load_state():
    state = {"dead": {}, "not_agency": {}}
    if STATE_FILE.exists():
        try:
            state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    cutoff = (date.today() - timedelta(days=DEAD_RETRY_DAYS)).isoformat()
    for k in ("dead", "not_agency"):
        state[k] = {kk: d for kk, d in state.get(k, {}).items() if d >= cutoff}
    return state


def load_agency_watchlist():
    wl = {}
    if AGENCY_WATCHLIST_FILE.exists():
        try:
            wl = json.loads(AGENCY_WATCHLIST_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("  [!] agency_watchlist.json unreadable — starting empty")
    for p in PLATFORMS:
        wl.setdefault(p, [])
    return wl


def prospect_agency_names():
    if not PROSPECTS_CSV.exists():
        sys.exit("prospects.csv not found — run prospector_engine.py first "
                 "(or use --names / --slugs).")
    names = {r.get("agency", "").strip()
             for r in csv.DictReader(PROSPECTS_CSV.open(encoding="utf-8"))}
    return sorted(n for n in names if n)


# ---- main ----

def main():
    ap = argparse.ArgumentParser(
        description="Find recruitment-agency boards and add them to "
                    "agency_watchlist.json (kept separate from the client "
                    "watchlist).")
    ap.add_argument("--from-prospects", action="store_true",
                    help="derive candidate slugs from prospects.csv agency names")
    ap.add_argument("--names", metavar="FILE",
                    help="text file, one agency name per line")
    ap.add_argument("--slugs", default="",
                    help="explicit candidate slugs, comma-separated")
    ap.add_argument("--limit", type=int, default=200,
                    help="max board fetches this run (default 200)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retry-dead", action="store_true",
                    help="re-probe candidates cached as dead/non-agency")
    args = ap.parse_args()

    if not (args.from_prospects or args.names or args.slugs):
        ap.error("nothing to do — pass --from-prospects, --names and/or --slugs")

    names = []
    if args.from_prospects:
        names += prospect_agency_names()
    if args.names:
        p = Path(args.names)
        if not p.exists():
            sys.exit(f"--names file not found: {args.names}")
        names += [ln.strip() for ln in
                  p.read_text(encoding="utf-8", errors="replace").splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]

    # name -> slug candidates (plus explicit slugs verbatim)
    slugs = []
    for n in names:
        for s in slug_variants(n):
            if s not in slugs:
                slugs.append(s)
    for s in (x.strip().lower() for x in args.slugs.split(",")):
        if s and s not in slugs:
            slugs.append(s)

    state = load_state()
    if args.retry_dead:
        state["dead"], state["not_agency"] = {}, {}
    wl = load_agency_watchlist()
    watched = {(p, t) for p in PLATFORMS for t in wl[p]}
    today = date.today().isoformat()

    print(f"{len(slugs)} candidate slug(s) — probing across "
          f"{len(PLATFORMS)} platforms (limit {args.limit} fetches)…")

    fetches, found, rejected = 0, [], 0
    for i, slug in enumerate(slugs, 1):
        if any((p, slug) in watched for p in PLATFORMS):
            continue   # already watching this agency
        if f"*:{slug}" in state["dead"] or f"*:{slug}" in state["not_agency"]:
            continue
        live_anywhere = False
        probed_all = True
        for platform in PLATFORMS:
            if fetches >= args.limit:
                print(f"  [!] fetch limit {args.limit} reached — "
                      "re-run to continue")
                probed_all = False
                break
            try:
                jobs = FETCHERS[platform](slug)
            except requests.RequestException:
                jobs = None
                probed_all = False   # network blip: don't bury the slug
            fetches += 1
            time.sleep(DELAY)
            if jobs is None:
                continue
            live_anywhere = True
            verdict, reason = is_probable_agency(slug, jobs)
            if verdict:
                print(f"  [{i}/{len(slugs)}] + {platform}:{slug} "
                      f"({len(jobs)} postings) — {reason}")
                found.append((platform, slug))
                watched.add((platform, slug))
            else:
                print(f"  [{i}/{len(slugs)}]   {platform}:{slug} live but "
                      f"rejected — {reason}")
                state["not_agency"][f"*:{slug}"] = today
                rejected += 1
            break   # a slug lives on one platform; stop probing the rest
        if fetches >= args.limit and not probed_all:
            break
        if not live_anywhere and probed_all:
            state["dead"][f"*:{slug}"] = today

    print(f"\nResult: {len(found)} agency board(s), {rejected} live-but-not-"
          f"agency, {fetches} fetch(es) used.")
    if args.dry_run:
        print("(dry run — nothing written)")
        return

    for platform, token in found:
        wl[platform].append(token)
    for p in PLATFORMS:
        wl[p] = sorted(set(wl[p]))
    AGENCY_WATCHLIST_FILE.write_text(json.dumps(wl, indent=2),
                                     encoding="utf-8")
    STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    total = sum(len(wl[p]) for p in PLATFORMS)
    print(f"agency_watchlist.json updated — {total} agency board(s) watched. "
          "Sweep them with: python prospect_signal_engine.py")


if __name__ == "__main__":
    sys.exit(main())
