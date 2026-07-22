#!/usr/bin/env python3
"""
Agentic AI Leads — Discovery Engine: grow the watchlist continuously.
(Supersedes the one-off discover_tokens.py.)

Feeds watchlist.json from three free, ToS-safe sources:

1. Company-name lists — from LinkedIn browsing, directories, client target
   lists, anywhere. One name per line; names become slug variants probed
   against all 4 ATS platforms:
       python discovery_engine.py --names companies.txt

2. Hacker News "Who is hiring" threads (public Algolia API) — hundreds of
   tech companies post monthly, many with direct Greenhouse/Lever/Ashby/
   Workable links. Extracts every board URL, verifies it live:
       python discovery_engine.py --hn 3                 # last 3 monthly threads
       python discovery_engine.py --hn 3 --keyword java --keyword backend

3. Any saved page or text file — a Google results page saved as .html, a
   newsletter, a jobs listing. Extracts every ATS board URL found:
       python discovery_engine.py --extract page.html --extract more.txt

Sources combine freely in one run. Housekeeping:
  --dry-run        report, change nothing
  --limit N        cap probe requests this run (default 400)
  --retry-dead     re-probe candidates cached as dead
  Dead candidates are cached in discovery_state.json and skipped for
  90 days, so a weekly scheduled run stays fast.

New boards are baselined silently on the next sweep; signals start the
run after.
"""

import argparse
import html
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import requests

from tls_compat import enable_os_trust_store

enable_os_trust_store()

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AAL_DATA_DIR", HERE))
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
STATE_FILE = DATA_DIR / "discovery_state.json"

PLATFORMS = ["greenhouse", "lever", "ashby", "workable"]

PROBES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
    "lever": "https://api.lever.co/v0/postings/{t}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{t}",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{t}",
}

# Same patterns the app's Watchlist tab uses for single URLs.
TOKEN_PATTERNS = [
    ("greenhouse", r"greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)"),
    ("lever", r"api\.lever\.co/v0/postings/([A-Za-z0-9_-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_-]+)"),
    ("ashby", r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
    ("workable", r"apply\.workable\.com/api/v1/widget/accounts/([A-Za-z0-9_-]+)"),
    ("workable", r"apply\.workable\.com/([A-Za-z0-9_-]+)"),
]
BAD_TOKENS = {"api", "v1", "embed", "widget", "jobs", "j", "boards", "en", "fr"}

HN_API = "https://hn.algolia.com/api/v1"

TIMEOUT = 10
DELAY = 0.35            # seconds between probe requests
DEAD_RETRY_DAYS = 90    # dead candidates get another chance after this
UA = {"User-Agent": "discovery-engine/1.0 (polite one-off probes)"}

# Trailing words stripped from company names before slugging.
LEGAL_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "gmbh", "sas", "sarl", "srl", "bv",
    "ab", "as", "oy", "plc", "co", "corp", "corporation", "company",
    "group", "holdings",
}


# ----------------------------- state ------------------------------------

def load_state():
    """{'dead': {'greenhouse:acme' | '*:slug': 'YYYY-MM-DD'}} — expired
    entries are dropped on load so they get re-probed."""
    state = {"dead": {}}
    if STATE_FILE.exists():
        try:
            state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    cutoff = (date.today() - timedelta(days=DEAD_RETRY_DAYS)).isoformat()
    state["dead"] = {k: d for k, d in state.get("dead", {}).items()
                     if d >= cutoff}
    return state


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def load_watchlist():
    wl = {}
    if WATCHLIST_FILE.exists():
        try:
            wl = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("  [!] watchlist.json unreadable — starting from empty")
    for p in PLATFORMS:
        wl.setdefault(p, [])
    return wl


# --------------------------- extraction ---------------------------------

def slug_variants(name: str):
    """'Back Market, Inc.' -> ['backmarket', 'back-market']"""
    n = str(name or "").strip().lower().replace("&", " and ")
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-z0-9\s-]", " ", n)
    words = [w for w in re.split(r"[\s-]+", n) if w]
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    if not words:
        return []
    variants = []
    for v in ("".join(words), "-".join(words)):
        if len(v) >= 2 and v not in variants and v not in BAD_TOKENS:
            variants.append(v)
    return variants


def extract_tokens(text: str):
    """Any text/HTML -> {(platform, token)}. Handles HTML-escaped URLs."""
    text = html.unescape(text or "")
    found = set()
    for platform, pat in TOKEN_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            t = m.group(1).lower().strip(".-_")
            if t and t not in BAD_TOKENS:
                found.add((platform, t))
    return found


# ----------------------------- probing ----------------------------------

def probe(platform: str, token: str):
    """Live-job count if the board exists, else None."""
    try:
        r = requests.get(PROBES[platform].format(t=token),
                         timeout=TIMEOUT, headers=UA)
        if r.status_code != 200:
            return None
        data = r.json()
        if platform == "lever":
            return len(data) if isinstance(data, list) else None
        jobs = data.get("jobs")
        return len(jobs) if isinstance(jobs, list) else None
    except (requests.RequestException, ValueError):
        return None


# --------------------------- HN harvest ---------------------------------

def _get_json(url):
    r = requests.get(url, timeout=20, headers=UA)
    r.raise_for_status()
    return r.json()


def hn_harvest(months: int, keywords=None):
    """Extract ATS tokens from the last N monthly 'Who is hiring' threads.
    keywords (optional): only comments containing at least one keyword."""
    data = _get_json(
        f"{HN_API}/search_by_date?query=%22who%20is%20hiring%22"
        "&tags=story,author_whoishiring&hitsPerPage=24")
    stories = [h for h in data.get("hits", [])
               if "who is hiring" in (h.get("title") or "").lower()][:months]

    kws = [k.lower() for k in (keywords or [])]
    tokens = set()
    for s in stories:
        sid, title = s["objectID"], s.get("title", "")
        n_comments, page = 0, 0
        while True:
            data = _get_json(f"{HN_API}/search_by_date?tags=comment,story_{sid}"
                             f"&hitsPerPage=1000&page={page}")
            hits = data.get("hits", [])
            for h in hits:
                txt = h.get("comment_text") or ""
                if kws and not any(k in txt.lower() for k in kws):
                    continue
                tokens |= extract_tokens(txt)
            n_comments += len(hits)
            page += 1
            if not hits or page >= data.get("nbPages", 1):
                break
            time.sleep(0.25)
        print(f"  '{title}': {n_comments} comments scanned, "
              f"{len(tokens)} boards so far")
    return tokens


# ------------------------------ main ------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Grow watchlist.json from names, HN threads, or saved pages.")
    ap.add_argument("--names", metavar="FILE",
                    help="text file, one company name per line")
    ap.add_argument("--hn", type=int, metavar="N", default=0,
                    help="harvest the last N monthly 'Who is hiring' threads")
    ap.add_argument("--keyword", action="append", default=[],
                    help="(with --hn) only comments containing this keyword; repeatable")
    ap.add_argument("--extract", action="append", default=[], metavar="FILE",
                    help="extract board URLs from a saved page/text file; repeatable")
    ap.add_argument("--limit", type=int, default=400,
                    help="max probe requests this run (default 400)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retry-dead", action="store_true",
                    help="re-probe candidates cached as dead")
    args = ap.parse_args()

    if not (args.names or args.hn or args.extract):
        ap.error("nothing to do — pass --names, --hn and/or --extract")

    wl = load_watchlist()
    state = load_state()
    if args.retry_dead:
        state["dead"] = {}
    dead = state["dead"]
    watched = {(p, t) for p in PLATFORMS for t in wl[p]}
    today = date.today().isoformat()

    probes_used = 0

    def budget_left():
        return probes_used < args.limit

    found = []   # (platform, token, n_jobs, source)

    # ---- sources that yield exact (platform, token) pairs ----
    exact = set()
    if args.hn:
        print(f"Harvesting last {args.hn} 'Who is hiring' thread(s)…")
        exact |= hn_harvest(args.hn, args.keyword)
    for f in args.extract:
        p = Path(f)
        if not p.exists():
            print(f"  [!] {f}: not found, skipped")
            continue
        got = extract_tokens(p.read_text(encoding="utf-8", errors="replace"))
        print(f"  {f}: {len(got)} board URL(s) extracted")
        exact |= got

    todo = sorted(t for t in exact
                  if t not in watched and f"{t[0]}:{t[1]}" not in dead)
    if exact:
        print(f"\n{len(exact)} unique boards from URLs — {len(todo)} new, "
              f"verifying…")
    for platform, token in todo:
        if not budget_left():
            print(f"  [!] probe limit {args.limit} reached — re-run to continue")
            break
        n = probe(platform, token)
        probes_used += 1
        time.sleep(DELAY)
        if n is None:
            dead[f"{platform}:{token}"] = today
        else:
            found.append((platform, token, n, "url"))
            watched.add((platform, token))

    # ---- company-name probing ----
    if args.names:
        p = Path(args.names)
        if not p.exists():
            sys.exit(f"--names file not found: {args.names}")
        names = [ln.strip() for ln in
                 p.read_text(encoding="utf-8", errors="replace").splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        print(f"\nProbing {len(names)} company name(s)…")
        for i, name in enumerate(names, 1):
            if not budget_left():
                print(f"  [!] probe limit {args.limit} reached — re-run to continue")
                break
            hit = False
            for slug in slug_variants(name):
                if any((pf, slug) in watched for pf in PLATFORMS):
                    hit = True   # already watching this company
                    break
                if f"*:{slug}" in dead:
                    continue
                probed_all = True
                for platform in PLATFORMS:
                    if not budget_left():
                        probed_all = False
                        break
                    n = probe(platform, slug)
                    probes_used += 1
                    time.sleep(DELAY)
                    if n is not None:
                        print(f"  [{i}/{len(names)}] + {name} -> "
                              f"{platform}:{slug} ({n} live jobs)")
                        found.append((platform, slug, n, "name"))
                        watched.add((platform, slug))
                        hit = True
                        break
                if hit:
                    break
                if probed_all:   # only bury slugs we fully checked
                    dead[f"*:{slug}"] = today
            if not hit and i % 15 == 0:
                print(f"  [{i}/{len(names)}] …")

    # ---- report + merge ----
    print(f"\nResult: {len(found)} new board(s), {probes_used} probe(s) used, "
          f"{len(dead)} dead candidate(s) cached.")
    for platform, token, n, src in found:
        print(f"  + {platform}:{token} ({n} jobs, via {src})")

    if args.dry_run:
        print("(dry run — nothing written)")
        return

    for platform, token, _, _ in found:
        wl[platform].append(token)
    for p in PLATFORMS:
        wl[p] = sorted(set(wl[p]))
    WATCHLIST_FILE.write_text(json.dumps(wl, indent=2), encoding="utf-8")
    save_state(state)
    total = sum(len(wl[p]) for p in PLATFORMS)
    print(f"watchlist.json updated — now watching {total} companies.")


if __name__ == "__main__":
    sys.exit(main())
