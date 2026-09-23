"""First-run setup, secure startup and operational recovery checks."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bootstrap
import recovery
import runner


def test_seed_only_empty_installation():
    with tempfile.TemporaryDirectory() as folder:
        data = Path(folder) / "data"
        assert bootstrap.seed_defaults(data)
        for name in bootstrap.DEFAULT_FILES:
            assert (data / name).exists(), name
        (data / "watchlist.json").write_text('{"greenhouse": ["mine"]}',
                                             encoding="utf-8")
        (data / "agency_watchlist.json").unlink()
        assert not bootstrap.seed_defaults(data)
        assert not (data / "agency_watchlist.json").exists()
        assert "mine" in (data / "watchlist.json").read_text()


def test_startup_guidance_and_secure_defaults():
    with tempfile.TemporaryDirectory() as folder:
        env = dict(os.environ, AAL_DATA_DIR=str(Path(folder) / "data"))
        for name in ("APP_USERNAME", "APP_PASSWORD", "APP_USERS",
                     "AAL_DISABLE_AUTH"):
            env.pop(name, None)
        command = [sys.executable, str(ROOT / "start.py"), "--check"]
        missing = subprocess.run(command, env=env, capture_output=True, text=True)
        assert missing.returncode != 0
        assert "APP_USERNAME and APP_PASSWORD" in missing.stderr
        env.update(APP_USERNAME="operator", APP_PASSWORD="test-password")
        ready = subprocess.run(command, env=env, capture_output=True, text=True)
        assert ready.returncode == 0, ready.stderr
        assert "host=127.0.0.1; CORS/XSRF enabled" in ready.stdout
        assert "defaults_would_seed=True" in ready.stdout
        assert not (Path(folder) / "data").exists()
        env["AAL_DISABLE_AUTH"] = "1"
        bypass = subprocess.run(command, env=env, capture_output=True, text=True)
        assert bypass.returncode != 0 and "isolated tests only" in bypass.stderr


def test_backup_restore_keeps_all_history_and_excludes_credentials():
    with tempfile.TemporaryDirectory() as folder:
        parent = Path(folder)
        data = parent / "data"
        assert bootstrap.seed_defaults(data)
        examples = {
            "prospects.csv": "agency,person_name\nFixture,Jane\n",
            "prospects_status.json": '{"agency-key": {"status": "contacted"}}',
            "inbox_status.json": '{"lead-key": {"status": "replied"}}',
            "enriched_contacts.json": '{"board": [{"name": "Jane"}]}',
            "company_board_mappings.json": '{"board": {"company": "Fixture"}}',
            "client_meetings.json": '{"meeting-key": {"chargeable": true}}',
            "job_history.json": '{"job": {"first_seen": "2026-09-01"}}',
            "mandate_history.json": '{"mandate": {"first_seen": "2026-09-01"}}',
            "signal_sweep.pending.json": '{"checkpoint": true}',
        }
        for name, content in examples.items():
            (data / name).write_text(content, encoding="utf-8")
        (data / "configs" / "client.json").write_text(
            '{"client_id": "client", "meeting_rate": 175}', encoding="utf-8")
        (data / ".env").write_text("APOLLO_API_KEY=private", encoding="utf-8")
        (data / "prospector_probe.json").write_text(
            '{"private": "provider response"}', encoding="utf-8")
        archive = recovery.create_backup(data, parent / "snapshot.zip")
        with zipfile.ZipFile(archive) as zipped:
            names = set(zipped.namelist())
        assert "manifest.json" in names
        assert ".env" not in names and "prospector_probe.json" not in names
        assert set(examples) <= names
        assert "configs/client.json" in names
        restored = recovery.restore_to_directory(archive, parent / "restored")
        for name, content in examples.items():
            assert (restored / name).read_text(encoding="utf-8") == content
        assert not (restored / ".env").exists()
        env = dict(os.environ, AAL_DATA_DIR=str(restored))
        read = subprocess.run(
            [sys.executable, "-c",
             "import core; assert core.load_status()['lead-key']['status'] == 'replied'"],
            cwd=ROOT, env=env, capture_output=True, text=True)
        assert read.returncode == 0, read.stderr
        try:
            recovery.restore_to_directory(archive, restored)
        except FileExistsError:
            pass
        else:
            raise AssertionError("restore overwrote active data")
        with zipfile.ZipFile(parent / "tampered.zip", "w") as zipped:
            with zipfile.ZipFile(archive) as original:
                for name in original.namelist():
                    content = original.read(name)
                    zipped.writestr(name, b"{}" if name == "job_history.json"
                                    else content)
        try:
            recovery.restore_to_directory(parent / "tampered.zip",
                                          parent / "bad-restore")
        except ValueError:
            pass
        else:
            raise AssertionError("tampered backup was accepted")
        assert not (parent / "bad-restore").exists()


def test_refresh_chain_runs_an_offline_fixture():
    with tempfile.TemporaryDirectory() as folder:
        data = Path(folder)
        previous = (runner.DATA_DIR, runner.ENGINE_LOCK, runner.RUN_STATUS,
                    runner.CHAINS["nightly"], os.environ.get("AAL_DATA_DIR"))
        runner.DATA_DIR = data
        runner.ENGINE_LOCK = data / ".engine.lock"
        runner.RUN_STATUS = data / "run_status.json"
        runner.CHAINS["nightly"] = [["tests/fixture_refresh_step.py"]]
        os.environ["AAL_DATA_DIR"] = str(data)
        try:
            assert runner.run_chain("nightly") == 0
            state = json.loads((data / "signals_state.json").read_text())
            assert "job-1" in state["greenhouse:fixture"]
            status = json.loads((data / "run_status.json").read_text())
            assert status["runs"][status["latest"]]["status"] == "ok"
        finally:
            (runner.DATA_DIR, runner.ENGINE_LOCK, runner.RUN_STATUS,
             runner.CHAINS["nightly"]) = previous[:4]
            if previous[4] is None:
                os.environ.pop("AAL_DATA_DIR", None)
            else:
                os.environ["AAL_DATA_DIR"] = previous[4]


def test_refresh_preview_does_not_write_data():
    with tempfile.TemporaryDirectory() as folder:
        data = Path(folder) / "preview-data"
        env = dict(os.environ, AAL_DATA_DIR=str(data))
        result = subprocess.run(
            [sys.executable, str(ROOT / "refresh.py"), "nightly", "--dry-run"],
            cwd=ROOT, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "signal_engine.py" in result.stdout
        assert not data.exists()


def test_linux_server_starts_on_loopback_with_persistent_data():
    if not sys.platform.startswith("linux"):
        return
    with tempfile.TemporaryDirectory() as folder:
        data = Path(folder) / "data"
        env = dict(os.environ, AAL_DATA_DIR=str(data),
                   APP_USERNAME="smoke", APP_PASSWORD="test-only")
        env.pop("AAL_DISABLE_AUTH", None)

        def start_and_stop():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "start.py"), "--port", str(port)],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise AssertionError("Linux server exited during startup")
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/_stcore/health",
                                     timeout=1) as response:
                            if response.status == 200:
                                return
                    except OSError:
                        time.sleep(0.2)
                raise AssertionError("Linux server did not become healthy")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

        start_and_stop()
        assert (data / "watchlist.json").exists()
        assert (data / "agency_watchlist.json").exists()
        assert (data / "configs" / "default.json").exists()
        status = data / "prospects_status.json"
        status.write_text('{"saved": {"status": "contacted"}}', encoding="utf-8")
        start_and_stop()
        assert json.loads(status.read_text(encoding="utf-8"))["saved"]["status"] \
            == "contacted"


def test_damaged_business_file_shows_recovery_not_traceback():
    script = (
        "from streamlit.testing.v1 import AppTest\n"
        "import sys\n"
        "at = AppTest.from_file(sys.argv[1], default_timeout=30).run()\n"
        "for page in ('views/prospects.py', 'views/settings.py'):\n"
        "    at.switch_page(page); at.run()\n"
        "    assert not at.exception, [e.value for e in at.exception]\n"
        "    assert any('damaged' in str(e.value) for e in at.error) or \\\n"
        "        page.endswith('settings.py'), page\n"
        "    assert any('Back up and recover' in str(i.value) for i in at.info)"
        " or page.endswith('settings.py'), page\n")
    with tempfile.TemporaryDirectory() as folder:
        data = Path(folder)
        status = data / "prospects_status.json"
        status.write_text('{"broken": ', encoding="utf-8")
        env = dict(os.environ, AAL_DATA_DIR=str(data), AAL_DISABLE_AUTH="1")
        run = subprocess.run([sys.executable, "-c", script, str(ROOT / "app.py")],
                             cwd=ROOT, env=env, capture_output=True, text=True)
        assert run.returncode == 0, run.stdout + run.stderr
        assert status.read_text(encoding="utf-8") == '{"broken": '


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok   {test.__name__}")
    print(f"ALL OPERATIONS TESTS PASS ({len(tests)})")
