"""Storage primitives: corruption, atomicity, conflicts and process locks."""

import json
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import storage
import core


def test_corrupt_json_is_preserved():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "data.json"
        path.write_text("{broken", encoding="utf-8")
        try:
            storage.read_json(path, default={}, expected_type=dict)
        except storage.DataFileError:
            pass
        else:
            raise AssertionError("corrupt JSON was accepted")
        assert path.read_text(encoding="utf-8") == "{broken"


def test_failed_atomic_write_preserves_old_file():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "data.json"
        storage.atomic_write_json(path, {"old": True})

        class Broken:
            pass

        try:
            storage.atomic_write_json(path, Broken())
        except TypeError:
            pass
        else:
            raise AssertionError("unserializable value was accepted")
        assert storage.read_json(path, expected_type=dict) == {"old": True}


def test_record_updates_merge_and_stale_edit_conflicts():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "status.json"
        storage.atomic_write_json(path, {"a": {"n": 0}, "b": {"n": 0}})
        loaded = storage.read_json(path, expected_type=dict)
        stale = storage.revision(loaded["a"])
        barrier = threading.Barrier(2)

        def update(key):
            barrier.wait()
            storage.update_json_record(path, key,
                                       lambda current: {"n": current["n"] + 1})

        threads = [threading.Thread(target=update, args=(key,))
                   for key in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert storage.read_json(path, expected_type=dict) == {
            "a": {"n": 1}, "b": {"n": 1}}
        try:
            storage.update_json_record(path, "a", lambda current: {"n": 9},
                                       expected=stale)
        except storage.ConflictError:
            pass
        else:
            raise AssertionError("stale edit overwrote a newer record")


def test_core_status_actions_merge_and_stale_undo_stops():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "status.json"
        storage.atomic_write_json(path, {
            "a": {"status": "new", "on": "", "notes": ""},
            "b": {"status": "new", "on": "", "notes": ""},
        })
        first = core.load_status(path)
        second = core.load_status(path)
        barrier = threading.Barrier(2)

        def change(status, key):
            barrier.wait()
            core.apply_stage(status, key, "contacted", path=path)

        threads = [threading.Thread(target=change, args=(first, "a")),
                   threading.Thread(target=change, args=(second, "b"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        saved = core.load_status(path)
        assert saved["a"]["status"] == saved["b"]["status"] == "contacted"

        expected = core.status_revision(saved, "a")
        core.update_status_fields(saved, "a", {"notes": "newer edit"}, path)
        try:
            core.restore_status_record(saved, "a", None, path,
                                       expected=expected)
        except storage.ConflictError:
            pass
        else:
            raise AssertionError("stale undo removed another session's edit")
        assert core.load_status(path)["a"]["notes"] == "newer edit"


def test_core_does_not_treat_corrupt_status_as_empty():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "status.json"
        path.write_text("not json", encoding="utf-8")
        try:
            core.load_status(path)
        except storage.DataFileError:
            pass
        else:
            raise AssertionError("corrupt status was treated as empty")


def test_lock_is_atomic_and_only_owner_releases():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "engine.lock"
        first = storage.FileLock(path, label="first").acquire()
        second = storage.FileLock(path, label="second")
        try:
            try:
                second.acquire()
            except storage.LockBusy:
                pass
            else:
                raise AssertionError("second owner acquired an active lock")
            second.release()
            assert path.exists(), "non-owner removed another owner's lock"
        finally:
            first.release()
        assert not path.exists()


def test_dead_lock_is_recovered_without_manual_deletion():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "engine.lock"
        path.write_text(json.dumps({"token": "dead", "pid": 999_999_999,
                                    "label": "old"}), encoding="utf-8")
        with storage.FileLock(path, label="new") as lock:
            assert storage.lock_info(path)["token"] == lock.token
        assert not path.exists()


def test_legacy_text_lock_is_recovered():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "engine.lock"
        path.write_text("123 old scheduled lock", encoding="utf-8")
        with storage.FileLock(path, label="new") as lock:
            assert storage.lock_info(path)["token"] == lock.token
        assert not path.exists()


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok   {test.__name__}")
    print(f"ALL STORAGE TESTS PASS ({len(tests)})")


if __name__ == "__main__":
    main()
