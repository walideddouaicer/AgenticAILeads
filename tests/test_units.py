"""Unit tests (plain asserts, no framework): agency classifier, niche
keyword derivation, prospector response parsing.

Usage: python tests/test_units.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

failures = []


def check(name, fn):
    try:
        fn()
        print(f"  ok   {name}")
    except AssertionError as e:
        failures.append(name)
        print(f"  FAIL {name}: {e}")


# ---------------- agency classifier ----------------

from agency_discovery import (classify_role_family, has_agency_marker,  # noqa: E402
                              is_probable_agency, looks_like_agency_book)


def t_role_families():
    assert classify_role_family("Senior Java Developer") == "engineering"
    assert classify_role_family("Registered Nurse - ICU") == "healthcare"
    assert classify_role_family("Head Chef") == "hospitality"
    assert classify_role_family("HGV Driver Class 1") == "driver"
    assert classify_role_family("Underwater Basket Weaver") == "other"


def t_markers():
    for name in ["acme-staffing", "TalentBridge", "peak-recruiters",
                 "executive-search-co", "rpo-solutions"]:
        assert has_agency_marker(name), name
    for name in ["gitlab", "datadog", "figma"]:
        assert not has_agency_marker(name), name


AGENCY_BOOK = {str(i): {"title": t, "location": l} for i, (t, l) in enumerate([
    ("Java Developer", "London"), ("Registered Nurse", "Leeds"),
    ("Site Manager", "Bristol"), ("Head Chef", "Manchester"),
    ("Sales Executive", "Birmingham"), ("Accountant", "Glasgow"),
    ("HGV Driver", "London"), ("Paralegal", "Leeds"),
    ("Marketing Manager", "Bristol"), ("Data Analyst", "Manchester"),
    ("Receptionist", "Birmingham"), ("Electrician", "Glasgow")])}

EMPLOYER_BOARD = {str(i): {"title": f"Backend Engineer {i}",
                           "location": "Berlin" if i % 2 else "Remote"}
                  for i in range(12)}


def t_agency_book():
    assert looks_like_agency_book(AGENCY_BOOK)
    assert not looks_like_agency_book(EMPLOYER_BOARD)
    # small-but-diverse boards don't qualify
    tiny = {str(i): {"title": t, "location": l} for i, (t, l) in enumerate(
        [("Java Dev", "London"), ("Nurse", "Leeds"),
         ("Chef", "York"), ("Accountant", "Bath")])}
    assert not looks_like_agency_book(tiny)


def t_verdicts():
    assert is_probable_agency("randomco", AGENCY_BOOK)[0]
    assert not is_probable_agency("gitlab", EMPLOYER_BOARD)[0]
    assert is_probable_agency("acme-recruiting", EMPLOYER_BOARD)[0]  # marker wins


check("role-family classifier", t_role_families)
check("agency name markers", t_markers)
check("agency-book heuristic", t_agency_book)
check("combined verdicts", t_verdicts)

# ---------------- niche keyword derivation ----------------

from common import derive_niche_keywords  # noqa: E402


def t_niche_basic():
    kws = derive_niche_keywords([
        "Senior Java Developer", "Java Backend Engineer",
        "Java Developer (Remote)", "DevOps Engineer", "Lead Java Engineer"])
    assert kws[0] == "java", kws
    assert "engineer" in kws or "developer" in kws, kws
    assert "senior" not in kws and "remote" not in kws and "lead" not in kws


def t_niche_stopwords_and_noise():
    kws = derive_niche_keywords(
        ["Senior Manager of the Year 2026", "", None, "The A Team"])
    assert "senior" not in kws and "the" not in kws and "of" not in kws
    assert "2026" not in kws


def t_niche_empty():
    assert derive_niche_keywords([]) == []


check("niche derivation ranks the niche first", t_niche_basic)
check("niche derivation filters noise", t_niche_stopwords_and_noise)
check("niche derivation empty input", t_niche_empty)

# ---------------- prospector parsing ----------------

from prospector_engine import parse_people, seen_key  # noqa: E402

# Prefer the REAL saved probe (prospector_probe.json in the repo root,
# written by `python prospector_engine.py --probe`); fall back to the
# synthetic fixture that mirrors enrichment_engine's known response shape.
real_probe = ROOT / "prospector_probe.json"
fixture = Path(__file__).resolve().parent / "fixtures" / "apollo_probe_sample.json"
probe_path = real_probe if real_probe.exists() else fixture
probe = json.loads(probe_path.read_text(encoding="utf-8"))
print(f"  (parsing fixture: {probe_path.name}"
      + (" — REAL probe" if probe_path == real_probe else " — synthetic")
      + ")")


def t_parse_probe():
    people = parse_people(probe)
    assert isinstance(people, list)
    assert people, "no people parsed from the probe response"
    for p in people:
        for col in ["agency", "person_name", "person_title", "linkedin",
                    "email", "location", "size"]:
            assert col in p, f"missing {col}"
        assert "not_unlocked" not in (p["email"] or "")


def t_parse_defensive():
    assert parse_people({}) == []
    assert parse_people({"people": None, "contacts": None}) == []
    assert parse_people({"people": [{"first_name": "A"}], "contacts": ["junk"]})


def t_seen_key():
    a = {"linkedin": "https://linkedin.com/in/x/", "agency": "A",
         "person_name": "B"}
    b = {"linkedin": "HTTPS://LINKEDIN.COM/IN/X", "agency": "Other",
         "person_name": "Name"}
    assert seen_key(a) == seen_key(b)          # linkedin normalized
    c = {"linkedin": "", "agency": "Acme Staffing", "person_name": "Jo Doe"}
    assert seen_key(c) == "acme staffing|jo doe"


check("probe response parses", t_parse_probe)
check("parsing is defensive", t_parse_defensive)
check("dedupe key normalization", t_seen_key)


# ---------------------------------------------------------------- history
from sweep_core import (board_features, job_features,  # noqa: E402
                        record_observations, stale_jobs)

J1 = {"title": "Staff Backend Engineer", "location": "London", "url": "u1"}
J2 = {"title": "Senior Go Engineer", "location": "Remote", "url": "u2"}
KEY = "greenhouse:acme"


def t_first_sight():
    h = {}
    st = record_observations(h, KEY, {"1": J1}, "2026-08-01")
    assert st["new"] == 1 and st["still_open"] == 0
    e = h[KEY]["1"]
    assert e["first_seen"] == e["last_seen"] == "2026-08-01"
    assert e["times_seen"] == 1 and e["gone_since"] is None


def t_same_day_rerun_is_idempotent():
    """A retried sweep must not inflate times_seen or invent a repost."""
    h = {}
    record_observations(h, KEY, {"1": J1}, "2026-08-01")
    record_observations(h, KEY, {"1": J1}, "2026-08-01")
    record_observations(h, KEY, {"1": J1}, "2026-08-01")
    assert h[KEY]["1"]["times_seen"] == 1
    assert h[KEY]["1"]["repost_count"] == 0


def t_days_open_accumulates():
    h = {}
    record_observations(h, KEY, {"1": J1}, "2026-08-01")
    record_observations(h, KEY, {"1": J1}, "2026-08-20")
    f = job_features(h[KEY]["1"], "2026-08-20")
    assert f["days_open"] == 19 and f["is_live"] and f["repost_count"] == 0


def t_close_freezes_days_open():
    """A closed req reports how long it stayed open, not how long ago."""
    h = {}
    record_observations(h, KEY, {"1": J1}, "2026-08-01")
    record_observations(h, KEY, {}, "2026-08-11")          # vanished
    assert h[KEY]["1"]["gone_since"] == "2026-08-11"
    f = job_features(h[KEY]["1"], "2026-09-30")
    assert f["days_open"] == 10 and not f["is_live"]


def t_repost_detected():
    h = {}
    record_observations(h, KEY, {"1": J1}, "2026-08-01")
    record_observations(h, KEY, {}, "2026-08-10")           # pulled
    st = record_observations(h, KEY, {"1": J1}, "2026-08-15")  # relisted
    assert st["reposted"] == 1
    assert h[KEY]["1"]["repost_count"] == 1
    assert h[KEY]["1"]["gone_since"] is None
    # first_seen must survive a repost — days_open counts from the original
    assert job_features(h[KEY]["1"], "2026-08-15")["days_open"] == 14


def t_rewrite_detected():
    h = {}
    record_observations(h, KEY, {"1": J1}, "2026-08-01")
    changed = dict(J1, title="Staff Backend Engineer (Payments)")
    st = record_observations(h, KEY, {"1": changed}, "2026-08-05")
    assert st["rewritten"] == 1
    assert job_features(h[KEY]["1"], "2026-08-05")["rewritten"] is True


def t_board_and_stale():
    h = {}
    record_observations(h, KEY, {"1": J1, "2": J2}, "2026-07-01")
    record_observations(h, KEY, {"1": J1}, "2026-08-01")     # job 2 closed
    b = board_features(h, KEY, "2026-08-01", window_days=60)
    assert b["live_count"] == 1 and b["req_burst"] == 2
    assert b["closed_in_window"] == 1 and b["close_velocity"] == 0.5
    assert b["max_days_open"] == 31
    stale = stale_jobs(h, min_days=30, today="2026-08-01")
    assert len(stale) == 1 and stale[0][1] == "1"


def t_missing_board_is_safe():
    assert board_features({}, "nope")["live_count"] == 0
    assert record_observations({}, KEY, None)["new"] == 0


check("history: first sighting recorded", t_first_sight)
check("history: same-day re-run is idempotent", t_same_day_rerun_is_idempotent)
check("history: days_open accumulates", t_days_open_accumulates)
check("history: closing freezes days_open", t_close_freezes_days_open)
check("history: repost detected, first_seen survives", t_repost_detected)
check("history: rewrite detected", t_rewrite_detected)
check("history: board features + stale pool", t_board_and_stale)
check("history: empty/missing input is safe", t_missing_board_is_safe)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL UNIT TESTS PASS")
