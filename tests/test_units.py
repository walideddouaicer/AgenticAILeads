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

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL UNIT TESTS PASS")
