"""Runner locking, failed starts, results and configured data directory."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import runner
import storage


def test_data_paths_follow_configured_directory():
    expected = Path(os.environ.get("AAL_DATA_DIR", ROOT))
    assert runner.DATA_DIR == expected
    assert runner.ENGINE_LOCK.parent == expected
    assert runner.RUN_STATUS.parent == expected


def test_failed_spawn_releases_lock():
    with tempfile.TemporaryDirectory() as directory:
        old = runner.ENGINE_LOCK
        runner.ENGINE_LOCK = Path(directory) / ".engine.lock"
        try:
            try:
                runner.start_locked_process(
                    [str(Path(directory) / "missing-program")], label="broken")
            except FileNotFoundError:
                pass
            else:
                raise AssertionError("missing executable unexpectedly started")
            assert not runner.ENGINE_LOCK.exists()
        finally:
            runner.ENGINE_LOCK = old


def test_two_starts_allow_one_owner_and_record_result():
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        old_lock, old_status = runner.ENGINE_LOCK, runner.RUN_STATUS
        runner.ENGINE_LOCK = directory / ".engine.lock"
        runner.RUN_STATUS = directory / "runs.json"
        barrier = threading.Barrier(2)
        outcomes = []

        def start():
            barrier.wait()
            try:
                running = runner.start_locked_process(
                    [sys.executable, "-c", "import time; time.sleep(.2)"],
                    label="test")
            except storage.LockBusy:
                outcomes.append("busy")
                return
            outcomes.append("started")
            code = running.process.wait()
            running.finish(code)

        try:
            threads = [threading.Thread(target=start) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert sorted(outcomes) == ["busy", "started"], outcomes
            status = storage.read_json(runner.RUN_STATUS, expected_type=dict)
            assert status["runs"][status["latest"]]["status"] == "ok"
            assert not runner.ENGINE_LOCK.exists()
        finally:
            runner.ENGINE_LOCK, runner.RUN_STATUS = old_lock, old_status


def test_chain_exception_records_failure_and_releases_lock():
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        old_lock, old_status = runner.ENGINE_LOCK, runner.RUN_STATUS
        old_backup = runner.backup_histories
        runner.ENGINE_LOCK = directory / ".engine.lock"
        runner.RUN_STATUS = directory / "runs.json"

        def fail_backup():
            raise OSError("simulated backup failure")

        runner.backup_histories = fail_backup
        try:
            try:
                runner.run_chain("nightly")
            except OSError:
                pass
            else:
                raise AssertionError("chain ignored an unexpected failure")
            status = storage.read_json(runner.RUN_STATUS, expected_type=dict)
            latest = status["runs"][status["latest"]]
            assert latest["status"] == "failed"
            assert "simulated backup failure" in latest["error"]
            assert not runner.ENGINE_LOCK.exists()
        finally:
            runner.backup_histories = old_backup
            runner.ENGINE_LOCK, runner.RUN_STATUS = old_lock, old_status


def test_history_backup_can_be_restored_without_losing_current_copy():
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        old_data = runner.DATA_DIR
        runner.DATA_DIR = directory
        history = directory / "job_history.json"
        storage.atomic_write_json(history, {"original": {}})
        try:
            runner.backup_histories()
            backup = next((directory / "backups").glob("job_history_*.json"))
            storage.atomic_write_json(history, {"current": {}})
            runner.restore_history_backup(backup, history)
            assert storage.read_json(history, expected_type=dict) == {
                "original": {}}
            emergency = list((directory / "backups").glob(
                "job_history_pre_restore_*.json"))
            assert len(emergency) == 1
            assert storage.read_json(emergency[0], expected_type=dict) == {
                "current": {}}
        finally:
            runner.DATA_DIR = old_data


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok   {test.__name__}")
    print(f"ALL RUNNER TESTS PASS ({len(tests)})")


if __name__ == "__main__":
    main()
