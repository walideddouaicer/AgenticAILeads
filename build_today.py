#!/usr/bin/env python3
"""
Agentic AI Leads — build today's outreach queue.

Writes today.json: a ranked, self-contained list of the N prospects to
contact today, each with the hook, the drafted message and the mini-list
gift already generated. The Today page in the CRM only renders this file —
it computes nothing.

WHY A FILE INSTEAD OF DOING IT IN THE PAGE
------------------------------------------
Because the ranking will get smarter, and the page shouldn't have to
change when it does. Today it ranks on hook quality; later it can rank on
`days_open`, repost counts, and reply-rate feedback from real sends. All
of that lands here, in a script that can run nightly, while views/today.py
stays a dumb renderer. It's also why this script imports core (not
common) — no Streamlit anywhere, so it can run from Task Scheduler.

RANKING (deliberately simple, meant to be replaced)
  1. follow-ups that are due   — warmest: they've already heard from us
  2. fresh prospects, best hook first:
       live mandate > derived niche > stated niche
     tie-broken by how strong a gift we can build (companies with named
     contacts beat companies without)

Prospects already contacted, replied, won or dismissed never appear.

Usage:
  python build_today.py                 # default N, writes today.json
  python build_today.py --n 20
  python build_today.py --dry-run       # print the queue, write nothing
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import core
from filter_engine import location_ok
from minilist_engine import DEFAULT_PITCH, build_minilist
from sweep_core import enable_utf8_stdout

DATA_DIR = core.DATA_DIR
TODAY_FILE = DATA_DIR / "today.json"

DEFAULT_N = 20
MINILIST_N = 10

# Stages that mean "already in play" — never re-surface these.
DONE_STAGES = {"replied", "won", "call_booked", "client_won", "dismissed"}

# ------------------------- the small/bulk split -------------------------
# Two lists, because the channels have different capacity. LinkedIn is
# capped around 100-150 connection requests a week; email is not. So the
# scarce channel goes to the prospects with the best odds, and everyone
# else gets an email sequence.
#
# These are RULES, not weights. Every one is a count you can verify by
# looking at the gift — no invented coefficients, because with zero reply
# data any weighting would be a guess dressed up as arithmetic. Once a few
# hundred sends have happened, replace them with thresholds fitted to
# measured reply rates.
#
# Deliberately NOT in here: company size, country, job title. Those are
# ICP entry conditions (icp.json). If the prospector only collects 10-50
# person agencies, re-checking size when ranking filters nothing. A factor
# earns a place here only if it still varies after the ICP filter.
MIN_GIFT_IN_MARKET = 10   # companies hiring their niche, in their country
MIN_GIFT_CONTACTS = 10    # of those, how many name a person to contact

TIER_SMALL = "small"      # all channels: LinkedIn + email + phone
TIER_BULK = "bulk"        # email sequence only


def hook_for(prospect, mandates):
    """(hook, source). Mandate-based when we have one, else the niche."""
    if mandates:
        m = mandates[0]
        title = (m.get("mandate_title") or "").strip()
        loc = (m.get("location") or "").strip()
        try:
            age = (date.today()
                   - date.fromisoformat(m.get("date", ""))).days
        except ValueError:
            age = None
        where = f" in {loc}" if loc else ""
        if age is not None and age > core.FRESH_DAYS:
            return f"saw you've been working a {title} mandate{where}", "mandate"
        return (m.get("hook") or
                f"saw you're working a {title} mandate{where}"), "mandate"
    return "", "none"


def niche_for(prospect, entry, mandates):
    """Same priority as the Prospects page: override > mandates > CSV."""
    if (entry.get("niche") or "").strip():
        return core.parse_csv_line(entry["niche"]), "override"
    derived = core.derive_niche_keywords(
        [m.get("mandate_title", "") for m in mandates])
    if derived:
        return derived, "mandates"
    stated = (prospect.get("niche_keywords") or "").strip()
    if stated:
        return core.parse_csv_line(stated), "stated"
    return [], "none"


def build(n=DEFAULT_N, minilist_n=MINILIST_N):
    prospects = core.load_prospects()
    if not prospects:
        return {"date": date.today().isoformat(), "items": [],
                "note": "prospects.csv is empty — import a list or run the "
                        "prospector."}

    status = core.load_status(core.PROSPECTS_STATUS)
    signals = core.load_prospect_signals()
    cfg = core.load_prospects_config()
    icp = core.load_icp()

    due, fresh = [], []
    skipped = Counter()

    for p in prospects:
        key = core.prospect_key(p)
        entry = status.get(key) or {}
        stage = core.effective_status(entry)

        if stage in DONE_STAGES:
            skipped[stage] += 1
            continue
        if stage == "contacted" and not core.followup_due(entry):
            skipped["contacted, not due yet"] += 1
            continue

        mandates = core.mandates_for_agency(p.get("agency", ""), signals)
        hook, hook_src = hook_for(p, mandates)
        kws, niche_src = niche_for(p, entry, mandates)

        if not hook and kws:
            hook = f"saw you specialize in {' / '.join(kws[:3])} placements"
            hook_src = "niche"
        if not hook:
            # Playbook rule: no hook = don't send.
            skipped["no hook (no mandate, no niche)"] += 1
            continue

        # Geo-match the gift. A Leeds agency cannot place a candidate at a
        # Toronto company; handing them one proves we didn't look.
        market = core.market_tokens(p.get("location", ""))
        gift = build_minilist(kws, locations=market,
                              n=minilist_n) if kws else []
        with_contact = sum(1 for r in gift if r.get("contact_name"))
        in_market = sum(1 for r in gift
                        if location_ok(r.get("location", ""), market,
                                       r.get("hiring_now", "")))

        # The four rules. All must hold — a prospect you can't email is not
        # an all-channel prospect no matter how good the gift is.
        # (code, human text) — the code is what gets counted in the
        # summary, the text is what the Today page shows. Parsing the
        # human string back out to count it was fragile and produced
        # nonsense labels like "gift (gift)".
        reasons = []
        if not (p.get("email") or "").strip():
            reasons.append(("no_email", "no email address"))
        if not (p.get("linkedin") or "").strip():
            reasons.append(("no_linkedin", "no LinkedIn URL"))
        if in_market < MIN_GIFT_IN_MARKET:
            reasons.append((
                "gift_in_market",
                f"gift has {in_market}/{MIN_GIFT_IN_MARKET} companies in "
                "their market"))
        if with_contact < MIN_GIFT_CONTACTS:
            reasons.append((
                "gift_contacts",
                f"gift has {with_contact}/{MIN_GIFT_CONTACTS} named "
                "contacts"))
        tier = TIER_SMALL if not reasons else TIER_BULK

        is_followup = stage == "contacted"
        fu = int(entry.get("fu", 0) or 0)
        template = (cfg["followup2_template"] if fu >= core.MAX_FOLLOWUPS - 1
                    else cfg["followup_template"]) if is_followup \
            else cfg["dm_template"]

        item = {
            "key": key,
            "agency": p.get("agency", ""),
            "person_name": p.get("person_name", ""),
            "person_title": p.get("person_title", ""),
            "linkedin": p.get("linkedin", ""),
            "email": p.get("email", ""),
            "location": p.get("location", ""),
            "size": p.get("size", ""),
            "hook": hook,
            "hook_source": hook_src,
            "niche": kws,
            "niche_source": niche_src,
            "message": core.render_prospect_dm(template, p, hook),
            "is_followup": is_followup,
            "followup_number": fu + 1 if is_followup else 0,
            "mandate_url": (mandates[0].get("url", "") if mandates else ""),
            "gift": gift,
            "gift_with_contacts": with_contact,
            "gift_in_market": in_market,
            "tier": tier,
            # Why they landed where they did. Stored, not recomputed, so
            # the Today page can explain a demotion without guessing.
            "tier_reasons": [txt for _, txt in reasons],
            "_codes": [code for code, _ in reasons],
            "matches_icp": core.matches_icp(p, icp),
            "pitch": DEFAULT_PITCH,
        }
        (due if is_followup else fresh).append(item)

    # Rank: a live mandate beats everything, then the strength of the gift.
    rank = {"mandate": 0, "niche": 1, "none": 2}
    def order(i):
        return (rank.get(i["hook_source"], 9),
                -i["gift_with_contacts"], -i["gift_in_market"])

    fresh.sort(key=order)
    due.sort(key=lambda i: -i["followup_number"])

    # Follow-ups come first regardless of tier — someone who already heard
    # from us is warmer than any cold prospect, however good their gift.
    small = [i for i in (due + fresh) if i["tier"] == TIER_SMALL]
    bulk = [i for i in fresh if i["tier"] == TIER_BULK]
    blocked_codes = Counter(c for i in (due + fresh) for c in i["_codes"])
    for i in (due + fresh):
        i.pop("_codes", None)

    return {
        "date": date.today().isoformat(),
        "target": n,
        "items": small[:n],          # the Today page reads this
        "bulk": bulk,                # queued for the email sequence
        "counts": {
            "followups_due": len(due),
            "fresh_available": len(fresh),
            "small_eligible": len(small),
            "bulk_queued": len(bulk),
            "queued": len(small[:n]),
            "off_icp": sum(1 for i in (due + fresh) if not i["matches_icp"]),
            "skipped": dict(skipped),
            "blocked_by": _blocked_reasons(blocked_codes),
        },
    }


_REASON_LABELS = {
    "no_email": "no email address (can't be all-channel)",
    "no_linkedin": "no LinkedIn URL",
    "gift_in_market": f"fewer than {MIN_GIFT_IN_MARKET} gift companies in "
                      "their market -> add boards in that country",
    "gift_contacts": f"fewer than {MIN_GIFT_CONTACTS} gift companies with a "
                     "named contact -> run enrichment",
}


def _blocked_reasons(codes):
    """Why prospects failed the small-list rules, most common first.

    This is the number that tells you what to FIX. If 150 rows say 'fewer
    than 10 named contacts', the answer is an enrichment run, not a change
    to the rules. A prospect can fail several rules, so the counts sum to
    more than the number of prospects — that's intended.
    """
    return {_REASON_LABELS.get(k, k): v for k, v in codes.most_common()}


def main():
    enable_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--n", type=int, default=DEFAULT_N,
                    help=f"how many to queue (default {DEFAULT_N})")
    ap.add_argument("--minilist-n", type=int, default=MINILIST_N,
                    dest="minilist_n", help="companies per gift")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the queue, write nothing")
    args = ap.parse_args()

    data = build(args.n, args.minilist_n)
    c = data.get("counts", {})
    if data.get("note"):
        print(data["note"])

    print(f"Today {data['date']}: {c.get('queued', 0)} queued for the "
          f"small list ({c.get('small_eligible', 0)} eligible, "
          f"{c.get('followups_due', 0)} follow-up(s) due)")
    print(f"  bulk list (email only): {c.get('bulk_queued', 0)}")
    if c.get("off_icp"):
        print(f"  {c['off_icp']} prospect(s) no longer match icp.json "
              "(collected under an older ICP)")
    for i, item in enumerate(data["items"], 1):
        tag = f"FU#{item['followup_number']}" if item["is_followup"] else "new"
        print(f"  {i:>2}. [{tag}] {item['agency']} — {item['person_name']} "
              f"({item['hook_source']} hook, gift {len(item['gift'])} "
              f"companies / {item['gift_with_contacts']} with contacts)")
    if c.get("blocked_by"):
        print("\n  why prospects fell to the bulk list:")
        for why, n in c["blocked_by"].items():
            print(f"    {n:>4}  {why}")
        print("  ^ fix the most common one to grow the small list.")
    if c.get("skipped"):
        print("  skipped entirely:")
        for why, n in sorted(c["skipped"].items(), key=lambda kv: -kv[1]):
            print(f"    {n:>4}  {why}")

    if args.dry_run:
        print("\nDry run — today.json not written.")
        return

    TODAY_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"\nWrote {TODAY_FILE.name}. Open the CRM's Today page and send.")


if __name__ == "__main__":
    sys.exit(main())
