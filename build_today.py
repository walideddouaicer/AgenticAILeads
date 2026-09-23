#!/usr/bin/env python3
"""Export the current shared outreach queue for CLI consumers.

The app calculates this queue live. today.json is an optional derived export;
it never determines eligibility or revives a prospect after a send.
"""

import argparse

import core
from outreach import current_queue
from storage import atomic_write_json
from sweep_core import enable_utf8_stdout

TODAY_FILE = core.DATA_DIR / "today.json"
DEFAULT_N = core.DAILY_QUEUE_CAP


def build(n=DEFAULT_N, minilist_n=10):
    queue = current_queue(minilist_n=minilist_n)
    items = []
    for item in queue["followups"] + queue["new"][:max(n, 0)]:
        prospect, context = item["prospect"], item["context"]
        items.append({"key": item["key"], "agency": prospect.get("agency", ""),
                      "person_name": prospect.get("person_name", ""),
                      "email": prospect.get("email", ""),
                      "linkedin": prospect.get("linkedin", ""),
                      "channel": context["channel"], "hook": context["hook"],
                      "hook_source": context["hook_source"],
                      "message": context["draft"], "sample": context["sample"],
                      "is_followup": item["is_followup"],
                      "followup_number": item["followup_number"],
                      "matches_icp": item["matches_icp"]})
    return {"date": queue["date"], "target": n, "items": items,
            "counts": {"replies": len(queue["replies"]),
                       "followups_due": len(queue["followups"]),
                       "fresh_available": queue["new_available"],
                       "initial_sent_today": queue["initial_sent_today"],
                       "remaining_initial": queue["remaining_initial"],
                       "queued": len(items),
                       "blocked": queue["blocked"]},
            "legacy_counts_incomplete": queue["legacy_counts_incomplete"]}


def main():
    enable_utf8_stdout()
    ap = argparse.ArgumentParser(description="Build the current outreach queue")
    ap.add_argument("--n", type=int, default=DEFAULT_N)
    ap.add_argument("--minilist-n", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    data = build(args.n, args.minilist_n)
    c = data["counts"]
    print(f"Today {data['date']}: {c['replies']} replies, "
          f"{c['followups_due']} follow-ups, "
          f"{len([i for i in data['items'] if not i['is_followup']])} new sends "
          f"({c['remaining_initial']} remaining in the daily budget)")
    for index, item in enumerate(data["items"], 1):
        print(f"  {index:>2}. {item['agency']} — {item['person_name']} "
              f"({'follow-up' if item['is_followup'] else 'new'}, "
              f"{item['channel']})")
    if c["blocked"]:
        print(f"{len(c['blocked'])} prospect(s) need correction.")
    if args.dry_run:
        return
    atomic_write_json(TODAY_FILE, data, indent=1)
    print(f"Wrote {TODAY_FILE.name} as a derived export.")


if __name__ == "__main__":
    main()
