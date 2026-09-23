#!/usr/bin/env python3
"""
Agentic AI Leads — Mini-List Generator (the $0 door-opener).

Builds a reviewable sample of matching job-board postings for a prospect
agency. Each row states its source, check time and contact coverage.

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

from evidence import load_refreshes, verified_at
from filter_engine import evaluate_job
from runtime import DATA_DIR
from storage import atomic_write_text, read_json

STATE_FILE = DATA_DIR / "signals_state.json"
CONTACTS_FILE = DATA_DIR / "enriched_contacts.json"
REFRESH_FILE = DATA_DIR / "board_refresh.json"
MAPPINGS_FILE = DATA_DIR / "company_board_mappings.json"
OUT_DIR = DATA_DIR / "minilists"

DEFAULT_N = 10
DEFAULT_EXCLUSIONS = ["intern", "internship", "unpaid", "apprentice"]

# Open-role count is not employee count, so it cannot justify a hidden company
# size gate. Keep the optional CLI override for a deliberate operator choice.
MAX_BOARD_ROLES = 0
HIT_CAP = 5
DEFAULT_PITCH = ("Want to review these examples together? We can discuss "
                 "pay per attended meeting, with the first meeting free. "
                 "Just reply.")


def _load_json(path: Path, default):
    return read_json(path, default=default, expected_type=type(default))


def tracked_jobs(state_file: Path = STATE_FILE):
    """Every job in the latest stored employer-board snapshots."""
    state = _load_json(state_file, {})
    jobs = []
    for board_key, board_jobs in state.items():
        platform, _, company = board_key.partition(":")
        for job_id, j in board_jobs.items():
            jobs.append({**j, "company": company, "platform": platform,
                         "board_key": board_key, "job_id": str(job_id)})
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
                   max_board_roles=None, refresh_file=None,
                   mappings_file=None, now=None):
    """Match tracked jobs against a prospect's niche. One row per company
    (best-matching role as headline, count of further matches). Companies
    with a named contact and multiple matching roles rank first.

    Unknown board identities stay separate. Contacts attach only through an
    explicit board-to-company mapping. Old/failed checks remain inspectable
    but are labeled as requiring refresh.
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
    refresh_file = refresh_file or Path(state_file).with_name("board_refresh.json")
    mappings_file = mappings_file or Path(state_file).with_name(
        "company_board_mappings.json")
    refreshes = load_refreshes(refresh_file)
    mappings = _load_json(mappings_file, {})
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
            mapping = mappings.get(job["board_key"])
            confirmed = (isinstance(mapping, dict)
                         and isinstance(mapping.get("company"), str)
                         and bool(mapping["company"].strip()))
            domain = str(mapping.get("domain") or "").strip().casefold() \
                if confirmed else ""
            group = (("domain", domain) if domain
                     else ("board", job["board_key"]))
            by_company.setdefault(group, {"hits": [], "mapping": mapping
                                          if confirmed else {}})["hits"].append(r)

    rows = []
    for group, data in by_company.items():
        hits = data["hits"]
        if ceiling and all(sizes.get(h["company"], 0) > ceiling for h in hits):
            continue
        # There is no meaningful "best" role. With no keyword_weights every
        # job scores 10 per matched keyword and almost every job matches
        # exactly one: measured on live data, 840 of 843 matched jobs scored
        # 10, and 90 of 92 companies had ALL their jobs tied. Sorting by
        # score then picking [0] was dressing up an arbitrary pick.
        # Sort by score, then title, so at least the output is STABLE —
        # the same input gives the same list every run.
        def checked_at(hit):
            checked = verified_at(hit["board_key"], refreshes, now)
            return checked if checked == hit.get("observed_at") else None

        hits.sort(key=lambda h: (
            not bool(checked_at(h)),
            -h["lead_score"], h.get("title", ""), h["board_key"]))
        seen_roles = set()
        distinct = []
        for hit in hits:
            identity = (hit.get("title", "").casefold(),
                        hit.get("location", "").casefold())
            if identity not in seen_roles:
                distinct.append(hit)
                seen_roles.add(identity)
        hits = distinct
        best = hits[0]
        mapping = data["mapping"]
        company = mapping.get("company") or best["company"]
        contact_key = ((mapping.get("contacts_key") or company)
                       if mapping else "")
        cs = contacts.get(best["board_key"]) or contacts.get(contact_key) or []
        c = cs[0] if cs else {}
        email = c.get("email", "") or ""
        if "@" not in email or "locked" in email.lower():
            email = ""
        checked = checked_at(best)
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
            "source_board": best["board_key"],
            "source_platform": best["platform"],
            "verified_at": checked or "",
            "evidence_status": "current board check" if checked else "refresh required",
            "identity_status": "confirmed" if mapping else "board identity unconfirmed",
            "company_domain": mapping.get("domain", ""),
            "contact_coverage": "named contact" if c.get("name") else "no named contact",
            # min(): raw len(hits) let megacaps win every niche on volume
            "_rank": (bool(checked), bool(c), min(len(hits), HIT_CAP),
                      best["lead_score"]),
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
        prefix = ("Board advertises" if r.get("verified_at")
                  else "Previously observed; refresh required before sending")
        line = f"• {r['company']} — {r['hiring_now']} ({prefix})"
        if r.get("more_matching_roles"):
            line += f" (+{r['more_matching_roles']} more)"
        if r.get("location"):
            line += f" — {r['location']}"
        line += (f"\n  Source: {r.get('source_board', 'unknown')} · "
                 f"checked: {r.get('verified_at') or 'unverified'} · "
                 f"{r.get('identity_status', 'identity unconfirmed')}")
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
    for row in rows:
        # Spreadsheet apps can execute a cell beginning with these characters.
        # Escape only the export; keep the original source data unchanged.
        w.writerow({key: ("'" + value if isinstance(value, str)
                          and value.lstrip().startswith(("=", "+", "-", "@"))
                          else value)
                    for key, value in row.items()})
    return buf.getvalue()


def _safe_href(value):
    value = str(value or "").strip()
    return html.escape(value, quote=True) if value.startswith(
        ("https://", "http://")) else ""


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
            contact_href = _safe_href(r["contact_linkedin"])
            if contact_href:
                contact += (f'<br><a href="{contact_href}">'
                            'LinkedIn &#8599;</a>')
            if r["contact_email"]:
                contact += f'<br><span class="sub">{e(r["contact_email"])}</span>'
        job_href = _safe_href(r["job_url"])
        link = (f'<a href="{job_href}">posting &#8599;</a>'
                if job_href else "")
        evidence = (f'{e(r.get("source_board", "unknown"))}<br>'
                    f'<span class="sub">{e(r.get("verified_at") or "refresh required")}'
                    f' · {e(r.get("identity_status", "unconfirmed"))}</span>')
        trs.append(
            f'<tr><td class="n">{i:02d}</td>'
            f'<td class="co">{e(r["company"])}</td>'
            f'<td>{role}<br><span class="sub">{e(r["location"])}</span></td>'
            f'<td>{contact}</td><td>{link}<br>{evidence}</td></tr>')

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{len(rows)} job-board examples — for {e(prospect)}</title>
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
  <h1>{len(rows)} matching job-board examples.</h1>
  <div class="meta">Prepared for {e(prospect)} · {today} · check each source's
  verification time before describing it as current</div>
  <table>
    <thead><tr><th></th><th>Board/company</th><th>Observed role</th>
    <th>Contact found</th><th>Source and check</th></tr></thead>
    <tbody>{''.join(trs)}</tbody>
  </table>
  <div class="pitch"><b>{e(pitch)}</b></div>
  <div class="foot">Agentic AI Leads — job-board examples for review.
  Contact availability is shown per row.</div>
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
