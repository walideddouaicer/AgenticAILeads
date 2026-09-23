"""Shared niche and location matching contracts for leads and samples."""

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core
from filter_engine import evaluate_job, keyword_matches, location_ok
from minilist_engine import build_minilist


def config(**overrides):
    value = {
        "client_id": "test", "inclusion_keywords": [],
        "exclusion_keywords": [], "allowed_locations": [],
        "keyword_weights": {}, "min_score": 0,
    }
    value.update(overrides)
    return value


def test_technical_terms_do_not_collapse_into_substrings():
    assert keyword_matches("Senior Java Engineer", "java")
    assert not keyword_matches("Senior JavaScript Engineer", "java")
    assert keyword_matches("JavaScript / Node.js Developer", "javascript")
    assert keyword_matches("Senior .NET Engineer", ".net")
    assert keyword_matches("C++ Systems Developer", "c++")
    assert keyword_matches("Head of Data Engineering", "data engineering")
    assert not keyword_matches("Data Analyst", "data engineering")

    java = config(inclusion_keywords=["java"])
    assert evaluate_job({"title": "Java Engineer"}, java)
    assert evaluate_job({"title": "JavaScript Engineer"}, java) is None


def test_remote_and_geography_are_separate_evidence():
    uk = ["united kingdom", "london"]
    assert location_ok("London, United Kingdom", uk)
    assert not location_ok("", uk, workplace="remote")
    assert not location_ok("EMEA", uk, workplace="remote")
    assert location_ok("", ["remote"], workplace="remote")
    assert location_ok("United States", ["united states"],
                       workplace="remote")
    assert not location_ok("United States", uk, workplace="remote")

    assert core.market_tokens("London, UK")
    assert "remote" not in core.market_tokens("London, UK")
    assert core.market_tokens("Remote") == ["remote"]
    assert core.market_tokens("EMEA") == []


def test_minilist_uses_shared_matcher_and_requires_known_explicit_market():
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        state = directory / "state.json"
        contacts = directory / "contacts.json"
        state.write_text(json.dumps({
            "greenhouse:java-co": {
                "1": {"title": "Java Engineer", "location": "London",
                      "url": "https://jobs.test/java"}},
            "lever:js-co": {
                "2": {"title": "JavaScript Engineer", "location": "London",
                      "url": "https://jobs.test/js"}},
        }), encoding="utf-8")
        contacts.write_text("{}", encoding="utf-8")

        rows = build_minilist(
            ["java"], locations=["london"], state_file=state,
            contacts_file=contacts)
        assert [row["company"] for row in rows] == ["java-co"]
        assert build_minilist(
            ["java"], locations=[], state_file=state,
            contacts_file=contacts) == []


def test_default_sample_has_no_fake_employee_size_gate():
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        state = directory / "state.json"
        contacts = directory / "contacts.json"
        jobs = {
            str(i): {"title": f"Java Engineer {i}", "location": "London",
                     "url": f"https://jobs.test/{i}"}
            for i in range(61)
        }
        state.write_text(json.dumps({"greenhouse:large-board": jobs}),
                         encoding="utf-8")
        contacts.write_text("{}", encoding="utf-8")
        rows = build_minilist(
            ["java"], locations=["london"], state_file=state,
            contacts_file=contacts)
        assert rows and rows[0]["company"] == "large-board"


def test_niche_derivation_preserves_technical_phrases():
    terms = core.derive_niche_keywords([
        "Senior .NET Engineer", "C++ Engineer", "Head of Data Engineering",
        "JavaScript Developer"])
    assert ".net" in terms
    assert "c++" in terms
    assert "data engineering" in terms
    assert "javascript" in terms
    # A single Data Engineer posting must not widen into every "data" role.
    assert core.derive_niche_keywords(["Senior Data Engineer"]) == ["data engineer"]
    assert not keyword_matches("Data Entry Clerk", "data engineer")


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok   {test.__name__}")
    print(f"ALL MATCHING TESTS PASS ({len(tests)})")


if __name__ == "__main__":
    main()
