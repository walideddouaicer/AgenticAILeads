#!/usr/bin/env python3
"""Run checks offline in a disposable source/data copy, never working data."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
GUARD = '''import socket
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_create_connection = socket.create_connection
def _local(address):
    return (isinstance(address, tuple) and address and
            address[0] in ("127.0.0.1", "::1", "localhost"))
def _offline_connect(sock, address):
    if _local(address):
        return _connect(sock, address)
    raise RuntimeError("External network disabled in offline test suite")
def _offline_connect_ex(sock, address):
    if _local(address):
        return _connect_ex(sock, address)
    raise RuntimeError("External network disabled in offline test suite")
def _offline_create(address, *args, **kwargs):
    if _local(address):
        return _create_connection(address, *args, **kwargs)
    raise RuntimeError("External network disabled in offline test suite")
socket.socket.connect = _offline_connect
socket.socket.connect_ex = _offline_connect_ex
socket.create_connection = _offline_create
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-dir", type=Path,
                        help="Optional directory for full check output and summary")
    args = parser.parse_args()
    logs = args.logs_dir.resolve() if args.logs_dir else None
    if logs:
        logs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aal-check-") as temp:
        root = Path(temp)
        # Source only: no .env, live data, private probes, generated fixtures or Git.
        files = list(ROOT.glob("*.py"))
        for folder, pattern in (("views", "*.py"), ("tests", "*.py"),
                                ("tests/fixtures", "*.json"),
                                (".streamlit", "*.toml")):
            files.extend((ROOT / folder).glob(pattern))
        for source in files:
            target = root / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        guard = root / "_offline"
        guard.mkdir()
        (guard / "sitecustomize.py").write_text(GUARD, encoding="utf-8")
        data = root / "data"
        data.mkdir()
        scratch = root / "scratch"
        scratch.mkdir()
        env = dict(os.environ, AAL_DATA_DIR=str(data),
                   PYTHONPATH=str(guard), PYTHONDONTWRITEBYTECODE="1",
                   TMPDIR=str(scratch),
                   STREAMLIT_BROWSER_GATHER_USAGE_STATS="false")
        for key in ("APOLLO_API_KEY", "INSTANTLY_API_KEY", "SMARTLEAD_API_KEY",
                    "APP_USERS", "APP_USERNAME", "APP_PASSWORD",
                    "AAL_SHOW_CLIENT_PAGES", "AAL_DISABLE_AUTH"):
            env.pop(key, None)
        guard_check = (
            "import socket\n"
            "try:\n    socket.create_connection(('203.0.113.1', 9))\n"
            "except RuntimeError as e:\n"
            "    assert 'External network disabled' in str(e)\n"
            "else:\n    raise AssertionError('Offline guard is not active')\n"
        )
        commands = [("offline-guard", ["-c", guard_check]),
                    ("syntax", ["-m", "compileall", "-q", "."]),
                    ("bare-app", ["app.py"]),
                    ("units", ["tests/test_units.py"]),
                    ("storage", ["tests/test_storage.py"]),
                    ("runner", ["tests/test_runner.py"]),
                    ("sweep-commit", ["tests/test_sweep_commit.py"]),
                    ("engine-storage", ["tests/test_engine_storage.py"]),
                    ("matching", ["tests/test_matching.py"]),
                    ("evidence", ["tests/test_evidence.py"]),
                    ("sample-review-page", ["tests/test_sample_review_page.py"]),
                    ("outreach", ["tests/test_outreach.py"]),
                    ("prospect-import", ["tests/test_prospect_import.py"]),
                    ("delivery", ["tests/test_delivery.py"]),
                    ("pages", ["tests/test_pages.py"]),
                    ("workflow-gaps", ["tests/test_workflow_gaps.py"])]
        results = []
        for name, command in commands:
            try:
                run = subprocess.run([sys.executable, *command], cwd=root,
                                     env=env, capture_output=True, text=True,
                                     timeout=180)
                output = run.stdout + run.stderr
                code = run.returncode
            except subprocess.TimeoutExpired:
                output, code = "Check exceeded 180 seconds", 124
            results.append({"name": name, "exit_code": code})
            label = "PASS" if code == 0 else "FAIL"
            print(f"{label} {name}", flush=True)
            if name == "workflow-gaps" or code:
                print(output[-12000:], flush=True)
            if logs:
                (logs / f"{name}.log").write_text(output, encoding="utf-8")
        if logs:
            (logs / "summary.json").write_text(json.dumps(results, indent=2),
                                                encoding="utf-8")
        print("All checks passed." if not any(result["exit_code"] for result in results)
              else "Some checks failed.")
        return int(any(result["exit_code"] for result in results))


if __name__ == "__main__":
    sys.exit(main())
