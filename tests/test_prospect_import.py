"""Prospect import rejects bad rows and preserves existing corrections."""

import csv
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core
import prospect_import


class ImportChecks(unittest.TestCase):
    def test_csv_preview_append_and_duplicate_handling(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "prospects.csv"
            with patch.object(core, "PROSPECTS_CSV", path):
                existing = {field: "" for field in prospect_import.COLUMNS}
                existing.update(agency="Atlas Search", person_name="Alex Smith",
                                linkedin="https://www.linkedin.com/in/alex",
                                niche_keywords="manually corrected")
                prospect_import.import_rows([existing])
                raw = ("agency,person_name,linkedin,niche_keywords\n"
                       "Atlas Search,Alex Smith,https://www.linkedin.com/in/alex,old\n"
                       "Other Agency,Sam Lee,https://www.linkedin.com/in/sam,java\n"
                       "Missing Person,,,\n"
                       "Other Agency,Sam Lee,https://www.linkedin.com/in/sam,java\n")
                accepted, rejected = prospect_import.preview_csv(
                    raw.encode("utf-8"), [existing])
                self.assertEqual(len(accepted), 1)
                self.assertEqual([item["row"] for item in rejected], [2, 4, 5])
                self.assertEqual(prospect_import.import_rows(accepted), 1)
                self.assertEqual(prospect_import.import_rows(accepted), 0)
                with path.open(newline="", encoding="utf-8") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["niche_keywords"],
                                 "manually corrected")

    def test_invalid_headers_and_encoding_do_not_write(self):
        with self.assertRaisesRegex(ValueError, "agency and person_name"):
            prospect_import.preview_csv(b"agency,email\nA,test@example.com\n")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            prospect_import.preview_csv(b"\xff\xfe")


if __name__ == "__main__":
    unittest.main(verbosity=2)
