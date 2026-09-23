"""Queue and budget regressions; run via scripts/check.py in isolated data."""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "fixture_data"
sys.path.insert(0, str(ROOT))
os.environ["AAL_DATA_DIR"] = str(DATA)
os.environ["AAL_DISABLE_AUTH"] = "1"

from streamlit.testing.v1 import AppTest


class WorkflowGaps(unittest.TestCase):
    def setUp(self):
        subprocess.run([sys.executable, str(ROOT / "tests/make_fixtures.py")],
                       cwd=ROOT, check=True, capture_output=True, text=True)
        self.app = AppTest.from_file(ROOT / "app.py", default_timeout=30)
        # Preconditions and UI execution happen in setUp.
        if self._testMethodName in (
                "test_page_uses_the_builders_eligible_new_prospects",
                "test_edited_message_is_the_recorded_message"):
            built = json.loads((DATA / "today.json").read_text())
            self.expected = {item["key"] for item in built["items"]
                             if not item["is_followup"]}
            self.actual = {b.key.removeprefix("pct_") for b in self.new_send_buttons()}
            print(f"Queue observation: builder={len(self.expected)}, "
                  f"page={len(self.actual)} new prospects")
        else:
            self.prepare_budget_observation()

    def new_send_buttons(self):
        self.app.run()
        self.assertFalse(self.app.exception, [e.value for e in self.app.exception])
        return [b for b in self.app.button if (b.key or "").startswith("pct_")]

    def test_page_uses_the_builders_eligible_new_prospects(self):
        self.assertEqual(self.actual, self.expected,
                         "Page recommends prospects excluded by queue builder")
        (DATA / "today.json").write_text(
            json.dumps({"date": "2099-01-01", "items": []}), encoding="utf-8")
        rerendered = {b.key.removeprefix("pct_")
                      for b in self.new_send_buttons()}
        self.assertEqual(rerendered, self.expected,
                         "Stale queue export changed live eligibility")
        (DATA / "today.json").unlink()
        without_cache = {b.key.removeprefix("pct_")
                         for b in self.new_send_buttons()}
        self.assertEqual(without_cache, self.expected,
                         "Missing queue export changed live eligibility")

    def test_recorded_initial_send_consumes_todays_budget(self):
        self.assertEqual(self.actual, self.expected,
                         "Page refills the full budget after recording a send")

    def test_edited_message_is_the_recorded_message(self):
        self.assertEqual(len(self.actual), 1)
        key = next(iter(self.actual))
        draft = [field for field in self.app.text_area
                 if field.key == f"draft_initial_{key}"]
        self.assertEqual(len(draft), 1)
        draft[0].set_value("Hi Alex, here is the exact note I sent.")
        self.app.run()
        button = [b for b in self.app.button if b.key == f"pct_{key}"]
        self.assertEqual(len(button), 1)
        button[0].click()
        self.app.run()
        recorded = json.loads((DATA / "prospects_status.json").read_text())
        self.assertEqual(recorded[key]["activity"][-1]["message"],
                         "Hi Alex, here is the exact note I sent.")

    def prepare_budget_observation(self):
        import core
        count = core.DAILY_QUEUE_CAP + 2
        rows = [dict(date_found="2026-09-21", agency=f"Agency {i}",
                     person_name=f"Person {i}", person_title="Founder",
                     linkedin=f"https://www.linkedin.com/in/fixture-{i}",
                     email="", location="London, United Kingdom", size="12",
                     source="test", niche_keywords="java") for i in range(count)]
        with (DATA / "prospects.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        (DATA / "prospects_status.json").write_text("{}", encoding="utf-8")
        before = self.new_send_buttons()
        self.assertEqual(len(before), core.DAILY_QUEUE_CAP)
        before[0].click()
        after = self.new_send_buttons()
        recorded = json.loads((DATA / "prospects_status.json").read_text())
        self.assertEqual(sum(e["status"] == "contacted" for e in recorded.values()), 1)
        self.actual = len(after)
        self.expected = core.DAILY_QUEUE_CAP - 1
        print(f"Budget observation: after one send expected={self.expected}, "
              f"page={self.actual} new prospects")


if __name__ == "__main__":
    unittest.main(verbosity=2)
