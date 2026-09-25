"""Offline contracts for ATS snapshots, board identity and sample claims."""

from datetime import datetime, timedelta, timezone
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core
import evidence
import enrichment_engine
import minilist_engine
import signal_engine
from storage import read_json


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _job(url, title="Java Engineer", checked=None):
    return {"title": title, "location": "London", "url": url,
            "observed_at": checked}


def test_malformed_and_failed_fetch_preserve_snapshot_then_empty_closes_it():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        paths = {"STATE_FILE": root / "state.json",
                 "HISTORY_FILE": root / "history.json",
                 "CSV_FILE": root / "signals.csv",
                 "CHECKPOINT_FILE": root / "pending.json",
                 "REFRESH_FILE": root / "refresh.json"}
        old = {"greenhouse:acme": {"1": _job("https://example.test/1",
                                              checked="2026-09-20T00:00:00+00:00")}}
        _write(paths["STATE_FILE"], old)
        _write(paths["HISTORY_FILE"], {"greenhouse:acme": {
            "1": {"first_seen": "2026-09-20", "last_seen": "2026-09-20",
                  "times_seen": 1, "repost_count": 0, "title_changes": 0,
                  "gone_since": None, "th": "old", "tt": "old"}}})
        evidence.record_refreshes(paths["REFRESH_FILE"], {
            "greenhouse:acme": {"status": "success", "at":
                                "2026-09-20T00:00:00+00:00", "job_count": 1}})
        with (patch.multiple(signal_engine, **paths,
                             WATCHLIST={"greenhouse": ["acme"]},
                             FETCHERS={"greenhouse": lambda token: []},
                             load_configs=lambda: []),
              patch.object(signal_engine.time, "sleep", lambda seconds: None),
              patch.object(sys, "argv", ["signal_engine.py"])):
            signal_engine.main()
        assert read_json(paths["STATE_FILE"], expected_type=dict) == old
        failed = evidence.load_refreshes(paths["REFRESH_FILE"])["greenhouse:acme"]
        assert failed["status"] == "failed"
        assert failed["last_success"] == "2026-09-20T00:00:00+00:00"

        with (patch.multiple(signal_engine, **paths,
                             WATCHLIST={"greenhouse": ["acme"]},
                             FETCHERS={"greenhouse": lambda token: {}},
                             load_configs=lambda: []),
              patch.object(signal_engine.time, "sleep", lambda seconds: None),
              patch.object(sys, "argv", ["signal_engine.py"])):
            signal_engine.main()
        assert read_json(paths["STATE_FILE"], expected_type=dict) == {
            "greenhouse:acme": {}}
        assert evidence.load_refreshes(paths["REFRESH_FILE"])[
            "greenhouse:acme"]["job_count"] == 0
        assert read_json(paths["HISTORY_FILE"], expected_type=dict)[
            "greenhouse:acme"]["1"]["gone_since"]


def test_confirmed_agency_mapping_needs_platform_and_current_observation():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        now = datetime(2026, 9, 23, tzinfo=timezone.utc)
        at = now.isoformat()
        mappings, checks, state = [root / name for name in
                                   ("mappings.json", "refresh.json", "state.json")]
        _write(mappings, {"acme staffing": ["greenhouse:acme"]})
        _write(checks, {key: {"status": "success", "last_success": at}
                        for key in ("greenhouse:acme", "lever:acme")})
        _write(state, {key: {"1": _job(f"https://jobs.test/{key}", checked=at)}
                       for key in ("greenhouse:acme", "lever:acme")})
        signals = [{"agency": key, "url": f"https://jobs.test/{key}"}
                   for key in ("greenhouse:acme", "lever:acme")]
        args = {"mappings_file": mappings, "refresh_file": checks,
                "state_file": state, "now": now}
        assert core.mandates_for_agency("Acme Staffing", signals, **args) == [
            signals[0]]
        assert core.mandates_for_agency("Acme Staffing Group", signals,
                                       **args) == []
        assert core.mandates_for_agency("Acme Staffing", [{"agency": "acme",
                    "url": signals[0]["url"]}], **args) == []
        _write(checks, {"greenhouse:acme": {"status": "failed",
                                            "last_success": at}})
        assert core.mandates_for_agency("Acme Staffing", signals, **args) == []


def test_sample_keeps_unknown_boards_separate_and_exposes_coverage():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        now = datetime(2026, 9, 23, tzinfo=timezone.utc)
        at = now.isoformat()
        old = (now - timedelta(hours=49)).isoformat()
        state = root / "state.json"
        contacts = root / "contacts.json"
        mappings = root / "company_board_mappings.json"
        checks = root / "board_refresh.json"
        _write(state, {
            "greenhouse:acme": {
                "1": _job("https://jobs.test/1", checked=at),
                "2": _job("https://jobs.test/2", checked=at)},
            "lever:acme": {"3": _job("https://jobs.test/3", checked=at)},
            "ashby:actual": {"4": _job("https://jobs.test/4", checked=old)}})
        _write(mappings, {"ashby:actual": {"company": "Actual Ltd",
                                            "contacts_key": "actual"}})
        _write(contacts, {"acme": [{"name": "Wrong Person"}],
                          "actual": [{"name": "Right Person",
                                      "email": "right@example.test"}]})
        _write(checks, {key: {"status": "success", "last_success": stamp}
                        for key, stamp in (("greenhouse:acme", at),
                                           ("lever:acme", at),
                                           ("ashby:actual", old))})
        rows = minilist_engine.build_minilist(
            ["java"], locations=["london"], state_file=state,
            contacts_file=contacts, mappings_file=mappings,
            refresh_file=checks, now=now)
        assert len(rows) == 3  # Same token on two platforms is not one company.
        by_board = {r["source_board"]: r for r in rows}
        assert by_board["greenhouse:acme"]["more_matching_roles"] == 0
        assert by_board["greenhouse:acme"]["contact_name"] == ""
        assert by_board["lever:acme"]["contact_name"] == ""
        assert by_board["ashby:actual"]["contact_name"] == "Right Person"
        assert by_board["ashby:actual"]["verified_at"] == ""
        assert "refresh required" in minilist_engine.render_table(rows)
        assert "50 more" not in minilist_engine.render_html("Agency", rows)
        assert "verified today" not in minilist_engine.render_html("Agency", rows)


def test_only_shared_confirmed_domain_groups_roles_across_boards():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        now = datetime(2026, 9, 23, tzinfo=timezone.utc)
        at = now.isoformat()
        state, contacts, mappings, checks = [root / name for name in
                                             ("state.json", "contacts.json",
                                              "company_board_mappings.json",
                                              "board_refresh.json")]
        _write(state, {
            "greenhouse:acme": {"1": _job("https://jobs.test/1",
                                           checked=at)},
            "lever:acme": {"2": _job("https://jobs.test/2",
                                       title="Java Architect", checked=at)}})
        _write(contacts, {"Acme Ltd": [{"name": "Hiring Lead"}]})
        _write(checks, {key: {"status": "success", "last_success": at}
                        for key in ("greenhouse:acme", "lever:acme")})
        _write(mappings, {key: {"company": "Acme Ltd",
                                "domain": "acme.example",
                                "contacts_key": "Acme Ltd"}
                          for key in ("greenhouse:acme", "lever:acme")})
        rows = minilist_engine.build_minilist(
            ["java"], locations=["london"], state_file=state,
            contacts_file=contacts, mappings_file=mappings,
            refresh_file=checks, now=now)
        assert len(rows) == 1
        assert rows[0]["more_matching_roles"] == 1
        assert rows[0]["contact_coverage"] == "named contact"
        assert rows[0]["company_domain"] == "acme.example"


def test_csv_formula_is_escaped_without_changing_source_row():
    row = {"company": "=HYPERLINK(\"https://bad.test\")", "count": 2}
    output = minilist_engine.render_csv([row])
    assert "'=HYPERLINK" in output
    assert row["company"].startswith("=")


def test_prospect_outputs_hide_internal_evidence_fields():
    row = {"company": "Acme <b>", "hiring_now": "Java Engineer",
           "more_matching_roles": 2, "location": "London",
           "job_url": "https://jobs.test/1", "contact_name": "Jane Doe",
           "contact_title": "Head of Talent", "contact_email": "jane@acme.test",
           "contact_linkedin": "https://www.linkedin.com/in/jane",
           "source_board": "greenhouse:acme", "source_platform": "greenhouse",
           "verified_at": "2026-09-23T00:00:00+00:00",
           "evidence_status": "current board check",
           "identity_status": "confirmed", "review_note": "approved"}
    text = minilist_engine.render_prospect_text([row])
    table = minilist_engine.render_email_table([row])
    exported = list(csv.DictReader(io.StringIO(
        minilist_engine.render_prospect_csv([row]))))
    assert list(exported[0]) == list(minilist_engine.PROSPECT_COLUMNS)
    for output in (text, table, str(exported)):
        for internal in ("greenhouse:acme", "2026-09-23", "confirmed",
                         "current board check", "approved"):
            assert internal not in output, (internal, output)
        assert "jane@acme.test" in output and "https://jobs.test/1" in output
    assert "(+2 more roles)" in text and "Acme &lt;b&gt;" in table
    locked = {**row, "contact_email": "email_not_unlocked@domain.com"}
    assert "unlocked" not in minilist_engine.render_prospect_text([locked])


def test_unknown_template_field_requires_correction():
    rendered = core.render_prospect_dm(
        "Hello {first_name}, {unavailable_field}",
        {"person_name": "Alex Smith", "agency": "Acme"}, "observed role")
    assert rendered.startswith("Template needs correction:")
    assert "{unavailable_field}" not in rendered


def test_review_controls_export_and_template_counts():
    at = datetime(2026, 9, 23, tzinfo=timezone.utc).isoformat()
    row = {"company": "Acme", "hiring_now": "Java Engineer",
           "location": "London", "job_url": "https://jobs.test/1",
           "source_board": "greenhouse:acme", "verified_at": at,
           "identity_status": "confirmed", "contact_name": "Jane"}
    row_id = minilist_engine.sample_row_id(row)
    assert minilist_engine.apply_sample_review([row], {}) == []
    reviewed = minilist_engine.apply_sample_review([row], {
        "approved": [row_id],
        "corrections": {row_id: {"hiring_now": "Senior Java Engineer"}}})
    assert len(reviewed) == 1
    assert reviewed[0]["hiring_now"] == "Senior Java Engineer"
    assert reviewed[0]["source_title"] == "Java Engineer"
    assert row["hiring_now"] == "Java Engineer"
    exported = list(csv.DictReader(io.StringIO(
        minilist_engine.render_csv(reviewed))))
    assert exported[0]["job_url"] == row["job_url"]
    assert exported[0]["source_board"] == row["source_board"]
    assert exported[0]["verified_at"] == row["verified_at"]
    assert exported[0]["source_title"] == "Java Engineer"
    message = core.render_prospect_dm(
        "{sample_count} roles, {contact_count} contacts", {}, "", reviewed)
    assert message == "1 roles, 1 contacts"
    assert core.render_prospect_dm("{sample_count} roles", {}, "").startswith(
        "Template needs correction:")
    stale = {**row, "verified_at": ""}
    assert minilist_engine.apply_sample_review(
        [stale], {"approved": [row_id]}) == []


def test_contact_lookup_retries_and_keeps_manual_corrections():
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    manual = [{"name": "Correct Name", "title": "Head of Talent",
               "linkedin": "https://linkedin.test/person", "email": ""}]
    found = [{"name": "Wrong Provider Name", "title": "Recruiter",
              "linkedin": "https://linkedin.test/person",
              "email": "person@example.test"}]
    merged = enrichment_engine.merge_contacts(manual, found)
    assert len(merged) == 1
    assert merged[0]["name"] == "Correct Name"
    assert merged[0]["title"] == "Head of Talent"
    assert merged[0]["email"] == "person@example.test"
    assert not enrichment_engine.lookup_due("acme", {"acme": manual}, {}, now)
    recent = {"acme": {"last_attempt": (now - timedelta(days=1)).isoformat(),
                       "status": "empty"}}
    assert not enrichment_engine.lookup_due("acme", {}, recent, now)
    old = {"acme": {"last_attempt": (now - timedelta(days=8)).isoformat(),
                    "status": "empty"}}
    assert enrichment_engine.lookup_due("acme", {}, old, now)


def test_client_contacts_are_board_scoped_until_mapping_is_confirmed():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        signals, contacts, mappings = [root / name for name in
                                       ("signals.csv", "contacts.json",
                                        "mappings.json")]
        fields = ["date", "client", "platform", "company", "title", "location",
                  "score", "url", "hook"]
        with signals.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows([{**dict.fromkeys(fields, ""),
                               "platform": platform, "company": "acme"}
                              for platform in ("greenhouse", "lever")])
        _write(contacts, {"greenhouse:acme": [{"name": "Board Person"}],
                          "acme": [{"name": "Legacy Person"}]})
        _write(mappings, {})
        with patch.multiple(core, SIGNALS_CSV=signals, CONTACTS_CACHE=contacts,
                            COMPANY_BOARD_MAPPINGS=mappings):
            rows, _ = core.load_leads()
            assert rows[0]["contact1_name"] == "Board Person"
            assert not rows[1].get("contact1_name")
            _write(mappings, {"lever:acme": {"company": "Acme",
                                              "contacts_key": "acme"}})
            rows, _ = core.load_leads()
            assert rows[1]["contact1_name"] == "Legacy Person"


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok   {test.__name__}")
    print(f"ALL EVIDENCE TESTS PASS ({len(tests)})")


if __name__ == "__main__":
    main()
