"""Behavioral checks for the shared agency queue and manual-send history."""

import csv
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core
import outreach
from storage import ConflictError, revision


def prospect(niche="java", linkedin="https://www.linkedin.com/in/alex"):
    return {"agency": "Atlas Search", "person_name": "Alex Smith",
            "person_title": "Founder", "linkedin": linkedin, "email": "",
            "location": "London, United Kingdom", "niche_keywords": niche}


class OutreachChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        self.patches = [patch.object(core, name, folder / filename)
                        for name, filename in (
                            ("PROSPECTS_STATUS", "prospects_status.json"),
                            ("AGENCY_BOARD_MAPPINGS", "agency_board_mappings.json"),
                            ("AGENCY_REFRESH_FILE", "agency_board_refresh.json"),
                            ("AGENCY_STATE_FILE", "prospect_signals_state.json"),
                            ("STATE_FILE", "signals_state.json"),
                            ("CONTACTS_CACHE", "enriched_contacts.json"),
                            ("PROSPECTS_CONFIG", "prospects_config.json"),
                            ("ICP_FILE", "icp.json"),
                            ("PROSPECTS_CSV", "prospects.csv"),
                            ("PROSPECT_ALIASES", "prospect_key_aliases.json"),
                            ("PROSPECT_IDENTITY_PENDING",
                             "prospect_identity_pending.json"))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.tz = patch.dict(os.environ, {"AAL_BUSINESS_TIMEZONE": "Asia/Tokyo"})
        self.tz.start()
        self.addCleanup(self.tz.stop)
        self.p = prospect()
        self.key = core.prospect_key(self.p)
        self.cfg = core.load_prospects_config()
        self.icp = core.load_icp()

    def queue(self, status=None, *, at=None, people=None, limit=15):
        return outreach.compute_queue(people or [self.p], status or {}, [],
                                      self.cfg, self.icp, now=at, limit=limit)

    def test_linkedin_only_and_missing_opener(self):
        ready = self.queue()
        self.assertEqual([i["key"] for i in ready["new"]], [self.key])
        self.assertEqual(ready["new"][0]["context"]["channel"], "linkedin")
        self.assertEqual(ready["new"][0]["context"]["sample"], [])
        no_niche = prospect(niche="")
        blocked = self.queue(people=[no_niche])
        self.assertFalse(blocked["new"])
        self.assertIn("supported opener", " ".join(blocked["blocked"][self.key]))
        self.cfg["dm_template"] = "Here are {sample_count} companies"
        blocked = self.queue()
        self.assertFalse(blocked["new"])
        self.assertIn("approve a current sample", " ".join(blocked["blocked"][self.key]))
        blocked = self.queue({self.key: {"status": "new",
                                          "drafts": {"initial": "Old sample claim"}}})
        self.assertFalse(blocked["new"])

    def test_reviewed_sample_change_regenerates_count_claim(self):
        self.cfg["dm_template"] = "Hi {first_name}, here are {sample_count} reviewed roles."
        old = [{"company": "Old"}]
        current = [{"company": "A"}, {"company": "B"}]
        entry = {"status": "new", "drafts": {"initial": "Here are 1 roles"},
                 "draft_sample_fingerprints": {"initial": revision(old)}}
        with patch.object(outreach, "apply_sample_review", return_value=current):
            context = outreach.prospect_context(self.p, entry, [], self.cfg)
        self.assertTrue(context["draft_sample_changed"])
        self.assertIn("2 reviewed roles", context["draft"])

    def test_send_snapshot_budget_duplicate_and_notes(self):
        now = datetime(2026, 9, 23, 10, tzinfo=timezone.utc)
        status = {}
        sample = [{"company": "Acme", "job_url": "https://example.test/job"}]
        action, duplicate = outreach.record_send(
            status, self.key, message="Hi Alex — final wording", sample=sample,
            channel="linkedin", action_id="click-1", expected=revision(None),
            now=now, budget=1)
        self.assertFalse(duplicate)
        self.assertEqual(action["message"], "Hi Alex — final wording")
        sample[0]["company"] = "Changed later"
        self.assertEqual(status[self.key]["activity"][0]["sample"][0]["company"], "Acme")
        self.assertEqual(self.queue(status, at=now, limit=1)["remaining_initial"], 0)
        _, duplicate = outreach.record_send(
            status, self.key, message="Hi Alex — final wording", sample=[],
            channel="linkedin", action_id="click-1", expected=revision(None),
            now=now, budget=1)
        self.assertTrue(duplicate)
        self.assertEqual(len(status[self.key]["activity"]), 1)
        original_at = status[self.key]["activity"][0]["at"]
        core.update_status_fields(status, self.key, {"notes": "Called back"},
                                  core.PROSPECTS_STATUS)
        self.assertEqual(status[self.key]["activity"][0]["at"], original_at)

    def test_followup_and_terminal_states(self):
        first = datetime(2026, 9, 19, 10, tzinfo=timezone.utc)
        due = first + timedelta(days=4)
        status = {}
        outreach.record_send(status, self.key, message="First", sample=[],
                             channel="linkedin", expected=revision(None), now=first)
        self.assertEqual(len(self.queue(status, at=due)["followups"]), 1)
        before = outreach.initial_sends_today(status, due)
        outreach.record_send(status, self.key, message="Follow-up", sample=[],
                             channel="linkedin", expected=revision(status[self.key]),
                             now=due)
        self.assertEqual(outreach.initial_sends_today(status, due), before)
        self.assertEqual(status[self.key]["fu"], 1)
        self.assertEqual(status[self.key]["activity"][-1]["type"], "followup_send")
        for stage in ("replied", "call_booked", "client_won", "dismissed"):
            entry = {**status[self.key], "status": stage}
            self.assertFalse(self.queue({self.key: entry}, at=due + timedelta(days=4))["followups"])
        no_opener = prospect(niche="")
        old_contact = {"status": "contacted", "on": "2026-09-01", "fu": 0}
        blocked = self.queue({self.key: old_contact}, at=due,
                             people=[no_opener])
        self.assertEqual(len(blocked["blocked_followups"]), 1)

    def test_business_midnight_and_conflicting_edits(self):
        # UTC 15:30 is already the next local day in Tokyo.
        before = datetime(2026, 9, 22, 14, 30, tzinfo=timezone.utc)
        after = datetime(2026, 9, 22, 15, 30, tzinfo=timezone.utc)
        status = {}
        outreach.record_send(status, self.key, message="First", sample=[],
                             channel="linkedin", expected=revision(None), now=before)
        self.assertEqual(outreach.initial_sends_today(status, before), 1)
        self.assertEqual(outreach.initial_sends_today(status, after), 0)
        stale = revision(status[self.key])
        core.update_status_fields(status, self.key, {"notes": "Other operator"},
                                  core.PROSPECTS_STATUS)
        with self.assertRaises(ConflictError):
            outreach.save_draft(status, self.key, "initial", "stale edit",
                                expected=stale)

    def test_identity_correction_preserves_send_history(self):
        row = {"agency": self.p["agency"], "person_name": self.p["person_name"],
               "linkedin": "", "email": "", "location": self.p["location"],
               "niche_keywords": self.p["niche_keywords"]}
        original_key = core.prospect_key(row)
        with core.PROSPECTS_CSV.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        status = {}
        outreach.record_send(status, original_key, message="Exact send", sample=[],
                             channel="linkedin", expected=revision(None))
        corrected = core.correct_prospect_identity(
            original_key,
            {"person_name": "Alex Jones",
             "linkedin": "https://www.linkedin.com/in/alex-jones"},
            expected=core.prospect_row_revision(row))
        self.assertEqual(core.prospect_key(corrected), original_key)
        self.assertEqual(core.prospect_key(core.load_prospects()[0]), original_key)
        self.assertEqual(core.load_status(core.PROSPECTS_STATUS)[original_key]
                         ["activity"][0]["message"], "Exact send")
        with self.assertRaises(ConflictError):
            core.correct_prospect_identity(
                original_key, {"person_name": "Stale"},
                expected=core.prospect_row_revision(row))

    def test_legacy_status_uses_raw_revision_for_safe_edit(self):
        core.PROSPECTS_STATUS.write_text(
            '{"old": {"status": "contacted", "on": "2026-09-01"}}',
            encoding="utf-8")
        status, raw_revisions = core.load_status(core.PROSPECTS_STATUS,
                                                 with_revisions=True)
        self.assertEqual(status["old"]["notes"], "")
        outreach.save_draft(status, "old", "initial", "Edited later",
                            expected=raw_revisions["old"])
        self.assertEqual(status["old"]["drafts"]["initial"], "Edited later")

    def test_interrupted_identity_correction_replays(self):
        old = {"agency": "Atlas Search", "person_name": "Alex Smith",
               "linkedin": "", "email": "", "location": "London"}
        new = {**old, "person_name": "Alex Jones",
               "linkedin": "https://www.linkedin.com/in/alex-jones"}
        key = core.prospect_key(old)
        with core.PROSPECTS_CSV.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(old))
            writer.writeheader(); writer.writerow(old)
        core.PROSPECT_IDENTITY_PENDING.write_text(json.dumps({
            "old": old, "new": new, "status_key": key}), encoding="utf-8")
        loaded = core.load_prospects()
        self.assertEqual(loaded[0]["person_name"], "Alex Jones")
        self.assertEqual(core.prospect_key(loaded[0]), key)
        self.assertFalse(core.PROSPECT_IDENTITY_PENDING.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
