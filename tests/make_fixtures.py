"""Build fixture data dirs for app testing (never touches real repo data).

Creates tests/empty_data/ (nothing in it) and tests/fixture_data/
(signals, statuses across all stages, contacts, tracked jobs, prospects,
mandate signals). Both are consumed via the AAL_DATA_DIR env var.

Usage: python tests/make_fixtures.py
"""
import csv
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixture_data"
EMPTY = Path(__file__).resolve().parent / "empty_data"

os.environ["AAL_DATA_DIR"] = str(FIX)
sys.path.insert(0, str(ROOT))

EMPTY.mkdir(exist_ok=True)
FIX.mkdir(exist_ok=True)
(FIX / "configs").mkdir(exist_ok=True)

today = date.today()
d = lambda n: (today - timedelta(days=n)).isoformat()

# --- configs ---
(FIX / "configs" / "default.json").write_text(json.dumps({
    "client_id": "default", "label": "Own prospecting", "active": True,
    "inclusion_keywords": ["engineer", "developer", "devops", "java"],
    "exclusion_keywords": ["intern"], "allowed_locations": [],
    "keyword_weights": {"senior": 3}, "min_score": 0, "meeting_rate": 150,
}, indent=2), encoding="utf-8")
(FIX / "configs" / "acme.json").write_text(json.dumps({
    "client_id": "acme", "label": "Acme Staffing", "active": True,
    "inclusion_keywords": ["java", "backend"], "exclusion_keywords": [],
    "allowed_locations": ["united states", "remote"],
    "keyword_weights": {}, "min_score": 0, "meeting_rate": 200,
    # delivery layer: email channel with a campaign id
    "channel": "both", "campaign_id": "camp-fixture-123",
}, indent=2), encoding="utf-8")

# --- watchlist ---
(FIX / "watchlist.json").write_text(json.dumps({
    "greenhouse": ["gitlab", "datadog"], "lever": ["plaid"],
    "ashby": ["linear"], "workable": [],
}, indent=2), encoding="utf-8")

# --- signals.csv: 32 rows so inbox pagination (25/page) kicks in ---
fieldnames = ["date", "client", "platform", "company", "title", "location",
              "score", "url", "hook"]
rows = []
companies = ["gitlab", "datadog", "plaid", "linear"]
for i in range(28):
    comp = companies[i % 4]
    rows.append({
        "date": d(i % 12), "client": "default", "platform": "greenhouse",
        "company": comp, "title": f"Senior Backend Engineer {i}",
        "location": "Remote, USA", "score": str(10 + i % 5),
        "url": f"https://example.com/job/{i}",
        "hook": f"saw you just opened a Senior Backend Engineer {i} role",
    })
for i in range(4):
    rows.append({
        "date": d(i), "client": "acme", "platform": "lever",
        "company": "plaid", "title": f"Java Developer {i}",
        "location": "New York, USA", "score": "20",
        "url": f"https://example.com/acme/{i}",
        "hook": f"saw you just opened a Java Developer {i} role",
    })
with (FIX / "signals.csv").open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)

# --- statuses across all stages, incl. a follow-up due ---
# core, not common: lead_key/prospect_key live there now, so building
# fixtures no longer requires Streamlit to be installed.
import core as C  # noqa: E402  (after env var set)
assert str(C.DATA_DIR) == str(FIX), f"DATA_DIR is {C.DATA_DIR}!"

k = lambda i: C.lead_key(rows[i])
status = {
    k(0): {"status": "contacted", "on": d(5), "notes": "", "fu": 0},   # due
    k(1): {"status": "contacted", "on": d(0), "notes": "sent today"},  # not due
    k(2): {"status": "replied", "on": d(1), "notes": "asked for deck"},
    k(3): {"status": "won", "on": d(2), "notes": "meeting showed"},
    k(4): {"status": "dismissed", "on": d(3), "notes": ""},
    k(5): {"status": "contacted", "on": d(9), "notes": "", "fu": 2},   # breakup sent
    k(28): {"status": "won", "on": d(1), "notes": "acme win"},
}
(FIX / "inbox_status.json").write_text(json.dumps(status, indent=1))

# --- contacts cache (gitlab has a real email -> exportable; plaid locked) ---
(FIX / "enriched_contacts.json").write_text(json.dumps({
    "gitlab": [{"name": "Jane Doe", "title": "Head of Talent",
                "linkedin": "https://linkedin.com/in/janedoe",
                "email": "jane@gitlab.com"}],
    "plaid": [{"name": "Ana Ruiz", "title": "Recruiting Manager",
               "linkedin": "https://linkedin.com/in/anaruiz",
               "email": "(locked — reveal in Apollo UI costs 1 credit)"}],
}, indent=2), encoding="utf-8")

# --- signals_state.json (live jobs for Live Jobs / mini-list / dry-run) ---
state = {}
for pfx, comp in [("greenhouse", "gitlab"), ("greenhouse", "datadog"),
                  ("lever", "plaid"), ("ashby", "linear")]:
    state[f"{pfx}:{comp}"] = {
        str(j): {"title": t, "location": loc,
                 "url": f"https://x.com/{comp}/{j}"}
        for j, (t, loc) in enumerate([
            ("Senior Java Developer", "Remote, United States"),
            ("DevOps Engineer", "Berlin, Germany"),
            ("Backend Engineer", "New York, USA"),
        ])}
(FIX / "signals_state.json").write_text(json.dumps(state))

# --- .env with a fake key so the Home checklist passes ---
(FIX / ".env").write_text("APOLLO_API_KEY=test-key-not-real\n")

# ---------------- prospect pipeline fixtures ----------------

pfields = ["date_found", "agency", "person_name", "person_title", "linkedin",
           "email", "location", "size", "source", "niche_keywords"]
prospects = [
    {"date_found": d(2), "agency": "Nimbus Staffing",
     "person_name": "Rita Patel", "person_title": "Founder",
     "linkedin": "https://linkedin.com/in/ritapatel", "email": "",
     "location": "London, United Kingdom", "size": "8",
     "source": "apollo", "niche_keywords": ""},           # niche via mandates
    {"date_found": d(4), "agency": "TalentBridge Search",
     "person_name": "Marc Dubois", "person_title": "Managing Director",
     "linkedin": "https://linkedin.com/in/marcdubois", "email": "",
     "location": "Paris, France", "size": "12",
     "source": "apollo", "niche_keywords": "java, backend"},  # niche via csv
    {"date_found": d(6), "agency": "Peak Recruiters",
     "person_name": "Sam Ortiz", "person_title": "Owner",
     "linkedin": "", "email": "(locked)",
     "location": "Austin, United States", "size": "5",
     "source": "apollo", "niche_keywords": ""},           # no hook at all
    {"date_found": d(9), "agency": "Nimbus Staffing",
     "person_name": "Leo Grant", "person_title": "Principal Consultant",
     "linkedin": "https://linkedin.com/in/leogrant", "email": "",
     "location": "London, United Kingdom", "size": "8",
     "source": "apollo", "niche_keywords": ""},
]
with (FIX / "prospects.csv").open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=pfields)
    w.writeheader()
    w.writerows(prospects)

# mandate signals for Nimbus (token nimbusstaffing = slug of the name)
psig_fields = ["date", "agency", "mandate_title", "location", "url", "hook"]
psigs = [
    {"date": d(1), "agency": "nimbusstaffing",
     "mandate_title": "Senior Java Developer", "location": "London",
     "url": "https://x.com/nimbus/1",
     "hook": "saw you're working a Senior Java Developer mandate in London"},
    {"date": d(3), "agency": "nimbusstaffing",
     "mandate_title": "Java Backend Engineer", "location": "Leeds",
     "url": "https://x.com/nimbus/2",
     "hook": "saw you're working a Java Backend Engineer mandate in Leeds"},
    {"date": d(10), "agency": "nimbusstaffing",
     "mandate_title": "DevOps Engineer", "location": "Remote",
     "url": "https://x.com/nimbus/3",
     "hook": "saw you're working a DevOps Engineer mandate"},
]
with (FIX / "prospect_signals.csv").open("w", newline="",
                                         encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=psig_fields)
    w.writeheader()
    w.writerows(psigs)

(FIX / "agency_watchlist.json").write_text(json.dumps({
    "greenhouse": ["nimbusstaffing"], "lever": [], "ashby": [],
    "workable": [],
}, indent=2), encoding="utf-8")

pk = lambda i: C.prospect_key(prospects[i])
pstatus = {
    pk(1): {"status": "contacted", "on": d(4), "notes": "", "fu": 0},  # due
    pk(3): {"status": "replied", "on": d(1), "notes": "wants the list"},
}
(FIX / "prospects_status.json").write_text(json.dumps(pstatus, indent=1))

# today.json — built by the real generator against these fixtures, so the
# Today page is tested on genuine output rather than a hand-written stub.
# The empty dir deliberately gets none, which exercises the "no queue yet"
# state.
import subprocess  # noqa: E402

_r = subprocess.run(
    [sys.executable, str(ROOT / "build_today.py"), "--n", "5"],
    env=dict(os.environ, AAL_DATA_DIR=str(FIX)),
    capture_output=True, text=True)
if _r.returncode:
    print("  [!] build_today failed for fixtures:\n" + _r.stdout + _r.stderr)

print(f"fixtures written: {FIX} ({len(rows)} signals, {len(prospects)} "
      f"prospects, {len(psigs)} mandate signals) and {EMPTY} (empty)")
