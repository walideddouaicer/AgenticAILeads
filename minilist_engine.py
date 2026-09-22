#!/usr/bin/env python3
"""
Agentic AI Leads — Mini-List Generator (the $0 door-opener).

Builds an unsolicited gift for a prospect agency: N companies currently
hiring in THEIR niche, pulled from the boards you already track, with
decision-maker contacts where enrichment has them.

Send it with: "Here's 10 companies hiring in your niche right now, with
contacts. Want 50 more per week?"

Usage:
  python minilist_engine.py --prospect "Acme Staffing" --keywords "java, .net"
  python minilist_engine.py --prospect "Acme" --keywords "devops" \
      --locations "united states, remote" --n 10

Outputs: minilists/minilist_<prospect>_<date>.csv and .html
(print the HTML to PDF with Ctrl+P for a clean attachment).

Also imported by app.py (Mini-List tab).
"""

import argparse
import csv
import html
import io
import re
import sys
from datetime import date
from pathlib import Path

from filter_engine import evaluate_job
from runtime import DATA_DIR
from storage import atomic_write_text, read_json

STATE_FILE = DATA_DIR / "signals_state.json"
CONTACTS_FILE = DATA_DIR / "enriched_contacts.json"
OUT_DIR = DATA_DIR / "minilists"

DEFAULT_N = 10
DEFAULT_EXCLUSIONS = ["intern", "internship", "unpaid", "apprentice"]

# Open-role count is not employee count, so it cannot justify a hidden company
# size gate. Keep the optional CLI override for a deliberate operator choice.
MAX_BOARD_ROLES = 0
HIT_CAP = 5
DEFAULT_PITCH = ("Want 50 more of these per week — with the meetings already "
                 "booked for you? Pay per showed meeting, first one free. "
                 "Just reply.")


def _load_json(path: Path, default):
    return read_json(path, default=default, expected_type=type(default))


def tracked_jobs(state_file: Path = STATE_FILE):
    """Every live job currently tracked by the signal engine."""
    state = _load_json(state_file, {})
    jobs = []
    for board_key, board_jobs in state.items():
        platform, _, company = board_key.partition(":")
        for j in board_jobs.values():
            jobs.append({**j, "company": company, "platform": platform})
    return jobs


def board_sizes(state_file: Path = STATE_FILE):
    """{company: total open roles on their board} — the headcount proxy."""
    state = _load_json(state_file, {})
    sizes = {}
    for board_key, board_jobs in state.items():
        company = board_key.partition(":")[2]
        sizes[company] = sizes.get(company, 0) + len(board_jobs)
    return sizes


def build_minilist(keywords, locations=None, exclusions=None, n=DEFAULT_N,
                   state_file=STATE_FILE, contacts_file=CONTACTS_FILE,
                   max_board_roles=None):
    """Match tracked jobs against a prospect's niche. One row per company
    (best-matching role as headline, count of further matches). Companies
    with a named contact and multiple matching roles rank first.

    Megacap boards are excluded (see MAX_BOARD_ROLES) so the gift stays
    credible for a boutique agency; pass max_board_roles=0 to keep them.
    """
    ceiling = (MAX_BOARD_ROLES if max_board_roles is None
               else max_board_roles)
    cfg = {
        "client_id": "minilist",
        "inclusion_keywords": [k for k in (keywords or []) if str(k).strip()],
        "exclusion_keywords": (exclusions if exclusions is not None
                               else DEFAULT_EXCLUSIONS),
        "allowed_locations": list(locations) if locations is not None else [],
        "keyword_weights": {},
        "min_score": 0,
    }
    contacts = _load_json(contacts_file, {})
    sizes = board_sizes(state_file) if ceiling else {}

    # An explicitly supplied but unresolved market is a review requirement,
    # not permission to draw companies from anywhere. None means the caller
    # deliberately requested an unrestricted list.
    if locations is not None and not locations:
        return []

    by_company = {}
    for job in tracked_jobs(state_file):
        r = evaluate_job(job, cfg)
        if r:
            by_company.setdefault(job["company"], []).append(r)

    rows = []
    for company, hits in by_company.items():
        if ceiling and sizes.get(company, 0) > ceiling:
            continue        # too big for a boutique agency to place at
        # There is no meaningful "best" role. With no keyword_weights every
        # job scores 10 per matched keyword and almost every job matches
        # exactly one: measured on live data, 840 of 843 matched jobs scored
        # 10, and 90 of 92 companies had ALL their jobs tied. Sorting by
        # score then picking [0] was dressing up an arbitrary pick.
        # Sort by score, then title, so at least the output is STABLE —
        # the same input gives the same list every run.
        hits.sort(key=lambda h: (-h["lead_score"], h.get("title", "")))
        best = hits[0]
        cs = contacts.get(company) or []
        c = cs[0] if cs else {}
        email = c.get("email", "") or ""
        if "@" not in email or "locked" in email.lower():
            email = ""
        rows.append({
            "company": company,
            # An EXAMPLE of what they're hiring, not a ranked pick.
            "hiring_now": best.get("title", ""),
            "also_hiring": " / ".join(
                h.get("title", "") for h in hits[1:3]),
            "more_matching_roles": max(len(hits) - 1, 0),
            "location": best.get("location", ""),
            "job_url": best.get("url", ""),
            "contact_name": c.get("name", ""),
            "contact_title": c.get("title", ""),
            "contact_linkedin": c.get("linkedin", ""),
            "contact_email": email,
            # min(): raw len(hits) let megacaps win every niche on volume
            "_rank": (bool(c), min(len(hits), HIT_CAP), best["lead_score"]),
        })

    rows.sort(key=lambda r: r["_rank"], reverse=True)
    for r in rows:
        r.pop("_rank")
    return rows[:n]


def render_table(rows, limit: int = 10) -> str:
    """Plain-text table for pasting straight into a DM or email body.

    This is the default way to send a mini-list. Nobody wants an HTML file
    attached to a cold message — it looks like malware and half of them
    can't open it on a phone. A short table in the body is readable
    everywhere, needs no download, and survives being forwarded.
    """
    out = []
    for r in rows[:limit]:
        line = f"• {r['company']} — {r['hiring_now']}"
        if r.get("more_matching_roles"):
            line += f" (+{r['more_matching_roles']} more)"
        if r.get("location"):
            line += f" — {r['location']}"
        if r.get("contact_name"):
            line += f"\n  → {r['contact_name']}"
            if r.get("contact_title"):
                line += f", {r['contact_title']}"
            if r.get("contact_email"):
                line += f" · {r['contact_email']}"
        out.append(line)
    return "\n".join(out)


def render_csv(rows) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()),
                       lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def render_html(prospect: str, rows, pitch: str = DEFAULT_PITCH) -> str:
    """Branded one-pager (site style: acid lime, Space Grotesk)."""
    e = html.escape
    today = date.today().strftime("%B %d, %Y")
    trs = []
    for i, r in enumerate(rows, 1):
        role = e(r["hiring_now"])
        if r["more_matching_roles"]:
            role += (f' <span class="more">+{r["more_matching_roles"]} more '
                     'matching role(s)</span>')
        contact = "&mdash;"
        if r["contact_name"]:
            contact = f'<strong>{e(r["contact_name"])}</strong>'
            if r["contact_title"]:
                contact += f'<br><span class="sub">{e(r["contact_title"])}</span>'
            if r["contact_linkedin"]:
                contact += (f'<br><a href="{e(r["contact_linkedin"])}">'
                            'LinkedIn &#8599;</a>')
            if r["contact_email"]:
                contact += f'<br><span class="sub">{e(r["contact_email"])}</span>'
        link = (f'<a href="{e(r["job_url"])}">posting &#8599;</a>'
                if r["job_url"] else "")
        trs.append(
            f'<tr><td class="n">{i:02d}</td>'
            f'<td class="co">{e(r["company"])}</td>'
            f'<td>{role}<br><span class="sub">{e(r["location"])}</span></td>'
            f'<td>{contact}</td><td>{link}</td></tr>')

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{len(rows)} companies hiring in your niche — for {e(prospect)}</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=Space+Mono&display=swap" rel="stylesheet">
<style>
  :root {{ --lime: #D8FF3E; --ink: #111; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Space Grotesk', sans-serif; color: var(--ink);
         margin: 0; padding: 48px 40px; max-width: 960px; }}
  .tag {{ font-family: 'Space Mono', monospace; font-size: 12px;
          text-transform: uppercase; letter-spacing: .12em;
          background: var(--lime); display: inline-block; padding: 4px 10px; }}
  h1 {{ font-size: 34px; line-height: 1.15; margin: 18px 0 6px; }}
  .meta {{ font-family: 'Space Mono', monospace; font-size: 13px;
           color: #555; margin-bottom: 28px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th {{ text-align: left; font-family: 'Space Mono', monospace; font-size: 11px;
        text-transform: uppercase; letter-spacing: .1em; padding: 8px 10px;
        border-bottom: 2px solid var(--ink); }}
  td {{ padding: 12px 10px; border-bottom: 1px solid #ddd;
        vertical-align: top; }}
  td.n {{ font-family: 'Space Mono', monospace; color: #999; }}
  td.co {{ font-weight: 700; }}
  .sub {{ color: #777; font-size: 12.5px; }}
  .more {{ background: var(--lime); font-size: 11.5px; padding: 1px 6px;
           white-space: nowrap; }}
  a {{ color: var(--ink); }}
  .pitch {{ margin-top: 32px; background: var(--ink); color: #fff;
            padding: 22px 24px; font-size: 16px; }}
  .pitch b {{ background: var(--lime); color: var(--ink); padding: 0 6px; }}
  .foot {{ font-family: 'Space Mono', monospace; font-size: 11px;
           color: #888; margin-top: 14px; }}
  @media print {{ body {{ padding: 20px; }} }}
</style></head><body>
  <span class="tag">Agentic AI Leads · free intel</span>
  <h1>{len(rows)} companies hiring in your niche — right now.</h1>
  <div class="meta">Prepared for {e(prospect)} · {today} · live job-board
  signals, verified today</div>
  <table>
    <thead><tr><th></th><th>Company</th><th>Hiring now</th>
    <th>Who to talk to</th><th>Proof</th></tr></thead>
    <tbody>{''.join(trs)}</tbody>
  </table>
  <div class="pitch"><b>{e(pitch)}</b></div>
  <div class="foot">Agentic AI Leads — signal-based meetings for recruitment
  agencies. No retainer, no contract, pay per showed meeting.</div>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description="Build a free mini-list for a prospect agency.")
    ap.add_argument("--prospect", required=True, help="prospect agency name")
    ap.add_argument("--keywords", required=True,
                    help="their niche keywords, comma-separated")
    ap.add_argument("--locations", default="",
                    help="their market, comma-separated (empty = anywhere)")
    ap.add_argument("--exclude", default=None,
                    help=f"exclusion keywords (default: {', '.join(DEFAULT_EXCLUSIONS)})")
    ap.add_argument("--n", type=int, default=DEFAULT_N)
    ap.add_argument("--max-board-size", type=int, default=MAX_BOARD_ROLES,
                    dest="max_board_size",
                    help=f"skip companies with more than this many open "
                         "roles (default 0 = no inferred size gate)")
    ap.add_argument("--pitch", default=DEFAULT_PITCH)
    args = ap.parse_args()

    def split(s):
        return [x.strip() for x in s.split(",") if x.strip()]

    rows = build_minilist(
        split(args.keywords), split(args.locations) or None,
        split(args.exclude) if args.exclude is not None else None, args.n,
        max_board_roles=args.max_board_size)
    if not rows:
        sys.exit("No tracked jobs match — widen keywords, grow the watchlist, "
                 "or raise --max-board-size (currently "
                 f"{args.max_board_size or 'no ceiling'}).")

    OUT_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", args.prospect.lower()).strip("-") or "prospect"
    base = OUT_DIR / f"minilist_{slug}_{date.today().isoformat()}"
    atomic_write_text(Path(f"{base}.csv"), render_csv(rows))
    atomic_write_text(
        Path(f"{base}.html"), render_html(args.prospect, rows, args.pitch))
    with_contact = sum(1 for r in rows if r["contact_name"])
    print(f"{len(rows)} companies ({with_contact} with named contacts)")
    print(f"  -> {base}.csv")
    print(f"  -> {base}.html   (Ctrl+P -> PDF for a clean attachment)")


if __name__ == "__main__":
    main()
