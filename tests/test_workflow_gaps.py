"""Executable release blockers. Expected failures are NOT fixed features.

Phase 3 must implement the contracts and remove expectedFailure decorators.
Run with scripts/check.py so fixture generation stays in a temporary copy.
"""
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
        # Preconditions and UI execution happen in setUp: a fixture/runtime
        # error must be a real error, never swallowed as an expected failure.
        if self._testMethodName == "test_page_uses_the_builders_eligible_new_prospects":
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

    @unittest.expectedFailure
    def test_page_uses_the_builders_eligible_new_prospects(self):
        self.assertEqual(self.actual, self.expected,
                         "Page recommends prospects excluded by queue builder")

    @unittest.expectedFailure
    def test_recorded_initial_send_consumes_todays_budget(self):
        self.assertEqual(self.actual, self.expected,
                         "Page refills the full budget after recording a send")

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
