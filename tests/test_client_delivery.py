"""Offline first-client contracts, including rows versus real meetings."""

import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import client_delivery as D
import core
from storage import ConflictError, revision


class ClientDeliveryChecks(unittest.TestCase):
    def test_saved_profiles_verified_selection_and_idempotent_backfill(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            configs = data / "configs"
            state = data / "signals_state.json"
            refresh = data / "board_refresh.json"
            signals = data / "signals.csv"
            prospect_status = data / "prospects_status.json"
            prospect_status.write_text('{"agency-key": {"status": "contacted"}}')
            now = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
            stamp = now.isoformat()
            state.write_text(json.dumps({"greenhouse:example": {
                "1": {"title": "Java Engineer", "location": "London",
                      "url": "https://jobs.test/java", "observed_at": stamp},
                "2": {"title": "Python Engineer", "location": "London",
                      "url": "https://jobs.test/python", "observed_at": stamp}}}))
            refresh.write_text(json.dumps({"greenhouse:example": {
                "status": "success", "last_success": stamp}}))
            java = {"client_id": "java_client", "active": True,
                    "inclusion_keywords": ["java"],
                    "allowed_locations": ["London"], "meeting_rate": 175}
            python = {**java, "client_id": "python_client",
                      "inclusion_keywords": ["python"], "meeting_rate": 225}
            D.save_profile(java, expected=revision(None), config_dir=configs)
            D.save_profile(python, expected=revision(None), config_dir=configs)
            jobs = [dict(job, board_key="greenhouse:example", job_id=job_id,
                         platform="greenhouse", company="example")
                    for job_id, job in json.loads(state.read_text())[
                        "greenhouse:example"].items()]
            checks = json.loads(refresh.read_text())
            jmatches = D.preview_matches(java, jobs, checks, now=now)
            pmatches = D.preview_matches(python, jobs, checks, now=now)
            self.assertEqual([m["title"] for m in jmatches], ["Java Engineer"])
            self.assertEqual([m["title"] for m in pmatches], ["Python Engineer"])
            self.assertFalse(D.client_ids([], profiles=["default"]))
            jsel, psel = jmatches[0]["selection_id"], pmatches[0]["selection_id"]
            self.assertEqual(D.backfill_selected(
                "java_client", [jsel], state_file=state, refresh_file=refresh,
                csv_file=signals, config_dir=configs, now=now), (1, 0))
            self.assertEqual(D.backfill_selected(
                "python_client", [psel], state_file=state, refresh_file=refresh,
                csv_file=signals, config_dir=configs, now=now), (1, 0))
            self.assertEqual(D.backfill_selected(
                "java_client", [jsel], state_file=state, refresh_file=refresh,
                csv_file=signals, config_dir=configs,
                now=now + timedelta(days=1)), (0, 1))
            with signals.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual({r["client"]: r["title"] for r in rows},
                             {"java_client": "Java Engineer",
                              "python_client": "Python Engineer"})
            self.assertEqual(prospect_status.read_text(),
                             '{"agency-key": {"status": "contacted"}}')
            refresh.write_text(json.dumps({"greenhouse:example": {
                "status": "failed", "last_success": stamp}}))
            with self.assertRaisesRegex(ValueError, "no longer verified"):
                D.backfill_selected("java_client", [jsel], state_file=state,
                                    refresh_file=refresh, csv_file=signals,
                                    config_dir=configs, now=now)

    def test_manual_send_exact_message_and_duplicate_action(self):
        with tempfile.TemporaryDirectory() as folder:
            status_file = Path(folder) / "inbox_status.json"
            status = {}
            now = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
            action, duplicate = D.record_send(
                status, "lead-1", message="Final edited text", channel="linkedin",
                contact="Jane Doe", expected=revision(None),
                action_id="send-1", now=now, status_file=status_file)
            self.assertFalse(duplicate)
            self.assertEqual(action["message"], "Final edited text")
            self.assertEqual(status["lead-1"]["status"], "contacted")
            _, duplicate = D.record_send(
                status, "lead-1", message="Different text", channel="linkedin",
                contact="Jane Doe", expected=revision(None),
                action_id="send-1", now=now, status_file=status_file)
            self.assertTrue(duplicate)
            self.assertEqual(len(status["lead-1"]["activity"]), 1)
            with self.assertRaises(ConflictError):
                D.record_send(status, "lead-1", message="Stale", channel="linkedin",
                              contact="Jane Doe", expected=revision(None),
                              now=now + timedelta(days=4),
                              status_file=status_file)
            rows = [{"date": "2026-09-23", "client": "one",
                     "company": "example", "title": "Java Engineer",
                     "contact1_linkedin": "https://linkedin.test/jane/"},
                    {"date": "2026-09-23", "client": "two",
                     "company": "other", "title": "Java Engineer",
                     "contact1_linkedin": "https://linkedin.test/jane"}]
            status[core.lead_key(rows[0])] = {"status": "contacted"}
            self.assertEqual(D.contacted_elsewhere(
                rows, status, "https://linkedin.test/jane", "two"), ["one"])

    def test_one_meeting_across_vacancies_free_first_and_per_client_rate(self):
        with tempfile.TemporaryDirectory() as folder:
            meetings_file = Path(folder) / "client_meetings.json"
            leads = [
                {"date": "2026-09-23", "client": "java_client",
                 "platform": "greenhouse", "company": "example",
                 "title": title, "url": f"https://jobs.test/{index}"}
                for index, title in enumerate(("Java Engineer", "Java Lead"))]
            keys = [core.lead_key(row) for row in leads]
            self.assertFalse(D.default_chargeable("java_client", {}))
            with self.assertRaisesRegex(ValueError, "future date"):
                D.save_meeting("java_client", "future", "2026-09-25",
                               "Jane Doe", keys, chargeable=False, leads=leads,
                               path=meetings_file,
                               today=datetime(2026, 9, 24).date())
            meetings, _, rev = D.save_meeting(
                "java_client", "calendar-101", "2026-09-23", "Jane Doe", keys,
                chargeable=False, leads=leads, path=meetings_file,
                today=datetime(2026, 9, 24).date())
            self.assertTrue(D.default_chargeable("java_client", meetings))
            with self.assertRaisesRegex(ValueError, "already recorded"):
                D.save_meeting("java_client", "calendar-101", "2026-09-23",
                               "Jane Doe", keys, chargeable=True, leads=leads,
                               path=meetings_file,
                               today=datetime(2026, 9, 24).date())
            self.assertEqual(D.meeting_summary(
                "java_client", meetings, 175)[1], 0)
            meetings, _, _ = D.save_meeting(
                "java_client", "calendar-102", "2026-09-24", "Jane Doe", keys,
                chargeable=True, leads=leads, path=meetings_file,
                today=datetime(2026, 9, 24).date())
            self.assertEqual(D.meeting_summary(
                "java_client", meetings, 175)[1], 175)
            self.assertEqual(D.meeting_summary(
                "java_client", meetings, 225)[1], 225)
            self.assertIsNone(D.meeting_summary(
                "java_client", meetings, None)[1])
            report = D.report_rows("java_client", leads, {}, meetings, 175)
            self.assertEqual(sum(r["record_type"] == "lead" for r in report), 2)
            self.assertEqual(sum(r["record_type"] == "meeting" for r in report), 2)
            self.assertEqual(sum(int(r["recorded_estimate"] or 0)
                                 for r in report), 175)
            parsed = list(csv.DictReader(io.StringIO(D.report_csv(report))))
            self.assertEqual(parsed, [{key: str(value) for key, value in row.items()}
                                      for row in report])
            only_one = D.report_rows("java_client", leads[:1], {}, meetings, 175)
            self.assertEqual(len(only_one), 3)
            with self.assertRaises(ConflictError):
                D.save_meeting("java_client", "calendar-101", "2026-09-23",
                               "Jane Doe", keys, chargeable=True, leads=leads,
                               path=meetings_file, expected=revision(None),
                               today=datetime(2026, 9, 24).date())
            meetings, edited, _ = D.save_meeting(
                "java_client", "calendar-101", "2026-09-23", "Jane Doe", keys,
                chargeable=False, leads=leads, path=meetings_file,
                expected=rev, today=datetime(2026, 9, 24).date())
            self.assertFalse(edited["chargeable"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
