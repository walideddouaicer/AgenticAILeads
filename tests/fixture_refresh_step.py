"""Offline fixed-result collector step used only by test_operations.py."""

import json
import os
from pathlib import Path

data = Path(os.environ["AAL_DATA_DIR"])
(data / "signals_state.json").write_text(json.dumps({
    "greenhouse:fixture": {
        "job-1": {"title": "Java Engineer", "location": "London",
                  "url": "https://jobs.example.test/1"}
    }
}), encoding="utf-8")
print("fixture board refreshed")
