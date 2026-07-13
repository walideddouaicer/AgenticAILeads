#!/usr/bin/env python3
"""
Agentic AI Leads — Watchlist discovery: probe candidate company slugs
against all 4 ATS platforms and add every real board to watchlist.json.

Usage:
  python discover_tokens.py            # probe candidates, merge hits
  python discover_tokens.py --dry-run  # probe and report, change nothing

Takes ~5-8 minutes for the full candidate list (polite 0.4s delay,
~4 requests per candidate). Re-running is safe: already-watched tokens
are skipped, merges are deduplicated.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
WATCHLIST_FILE = HERE / "watchlist.json"

TIMEOUT = 10
DELAY = 0.4
UA = {"User-Agent": "signal-engine-discovery/0.1 (one-off polite probe)"}

# Verified via live job-board pages (July 2026 searches) — added directly.
KNOWN = {
    "lever": ["mistral", "brevo", "blablacar", "didomi", "vo2-group", "palantir"],
    "ashby": ["finary", "lago", "gorgias", "lemlist", "ami", "notion"],
    "workable": ["fifty-five", "flowdesk"],
    "greenhouse": ["dataiku", "alma31"],
}

# Candidate slugs: French tech companies (plus hyphen/name variants).
# Unverified — the probe decides. Wrong guesses cost one 404 and are dropped.
CANDIDATES = [
    # health / medtech
    "doctolib", "alan", "lifen", "nabla", "owkin", "therapixel", "gleamer",
    "incepto", "implicity", "withings", "diabeloop", "dentalmonitoring",
    # fintech
    "qonto", "payfit", "pennylane", "spendesk", "swile", "lydia", "sumeria",
    "swan", "shine", "memobank", "october", "younited", "younited-credit",
    "bankin", "pretto", "agicap", "libeo", "defacto", "payflows", "ledger",
    "coinhouse", "kaiko", "dfns", "kiln", "sorare",
    # insurance
    "seyna", "wakam", "acheel", "leocare", "descartes-underwriting",
    # AI / data
    "mistral-ai", "dust", "dust-tt", "lighton", "giskard", "zama", "kyutai",
    "poolside", "hcompany", "photoroom", "algolia", "botify", "kayrros",
    "pasqal", "deepki",
    # devtools / infra
    "strapi", "meilisearch", "qovery", "koyeb", "scaleway", "ovhcloud",
    "clevercloud", "clever-cloud", "platformsh", "scalingo", "outscale",
    "shadow", "qarnot",
    # security
    "datadome", "crowdsec", "harfanglab", "yeswehack", "yogosha",
    # martech / sales
    "contentsquare", "abtasty", "ab-tasty", "kameleoon", "mirakl", "akeneo",
    "ivalua", "modjo", "aircall", "ringover", "brevo",
    # HR / work
    "welcometothejungle", "wttj", "jobteaser", "skello", "combohr",
    "combo-hr", "side", "staffme", "brigad", "comet", "hublo", "wemaintain",
    "papernest", "360learning", "malt", "yousign", "pigment",
    # marketplace / consumer
    "backmarket", "back-market", "manomano", "vestiairecollective",
    "vestiaire-collective", "ankorstore", "leboncoin", "deezer", "believe",
    "evaneos", "heetch", "ornikar", "virtuo", "getaround", "cityscoot",
    "vulog", "padam-mobility", "jow", "phenix", "lafourche",
    # logistics / industry
    "exotec", "shippeo", "wakeo", "ovrsea", "convelio", "cubyn",
    "shift-technology",
    # energy / climate
    "verkor", "electra", "greenly", "sweep", "carbo", "sami", "elmy",
    "ekwateur", "ilek", "beev", "zeplug",
    # gaming
    "voodoo", "homagames", "homa-games",
    # legal
    "doctrine", "predictice", "jusmundi", "jus-mundi",
]

PROBES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
    "lever": "https://api.lever.co/v0/postings/{t}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{t}",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{t}",
}


def probe(platform: str, token: str):
    """Return live-job count if the board exists, else None."""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    wl = {}
    if WATCHLIST_FILE.exists():
        wl = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    for p in PROBES:
        wl.setdefault(p, [])

    watched = {(p, t) for p in PROBES for t in wl[p]}
    found, dead = [], 0

    # 1) known-verified tokens: add without probing
    for platform, tokens in KNOWN.items():
        for t in tokens:
            if (platform, t) not in watched:
                found.append((platform, t, "verified"))

    # 2) candidates: probe every platform until a hit
    todo = [c for c in dict.fromkeys(CANDIDATES)
            if not any((p, c) in watched for p in PROBES)]
    print(f"Probing {len(todo)} candidates across 4 platforms "
          f"(~{len(todo) * 4 * DELAY / 60:.0f}-{len(todo) * 4 * (DELAY + 0.4) / 60:.0f} min)…\n")

    for i, slug in enumerate(todo, 1):
        hit = False
        for platform in PROBES:
            if (platform, slug) in watched:
                hit = True
                break
            n = probe(platform, slug)
            time.sleep(DELAY)
            if n is not None:
                print(f"  [{i}/{len(todo)}] ✔ {slug} -> {platform} ({n} live jobs)")
                found.append((platform, slug, f"{n} jobs"))
                hit = True
                break
        if not hit:
            dead += 1
            if i % 20 == 0:
                print(f"  [{i}/{len(todo)}] …")

    print(f"\nResult: {len(found)} new boards found, {dead} candidates had no board.")

    if args.dry_run:
        print("(dry run — watchlist.json unchanged)")
        return

    for platform, token, _ in found:
        wl[platform].append(token)
    for p in PROBES:
        wl[p] = sorted(set(wl[p]))
    WATCHLIST_FILE.write_text(json.dumps(wl, indent=2), encoding="utf-8")
    total = sum(len(wl[p]) for p in PROBES)
    print(f"watchlist.json updated — now watching {total} companies.")
    print("Next sweep will baseline the new boards silently; "
          "signals start the run after.")


if __name__ == "__main__":
    sys.exit(main())
