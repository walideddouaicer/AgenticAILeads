"""Crash recovery for the state/history/CSV sweep transaction."""

import csv
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import storage
import sweep_core


FIELDS = ["date", "client", "url", "hook"]
KEY_FIELDS = ["date", "client", "url"]


def paths(directory):
    root = Path(directory)
    return (root / "sweep.pending.json", root / "state.json",
            root / "history.json", root / "signals.csv")


def assert_recovered(checkpoint, state_path, history_path, csv_path,
                     state, history, signals):
    assert sweep_core.recover_sweep_checkpoint(
        checkpoint, state_path, history_path, csv_path, FIELDS, KEY_FIELDS)
    assert storage.read_json(state_path, expected_type=dict) == state
    assert storage.read_json(history_path, expected_type=dict) == history
    with csv_path.open(newline="", encoding="utf-8") as stream:
        assert list(csv.DictReader(stream)) == signals
    assert not checkpoint.exists()
    assert not sweep_core.recover_sweep_checkpoint(
        checkpoint, state_path, history_path, csv_path, FIELDS, KEY_FIELDS)


def test_every_write_boundary_is_recoverable_and_idempotent():
    state = {"greenhouse:acme": {"job-2": {"title": "Engineer"}}}
    history = {"greenhouse:acme": {
        "job-2": {"first_seen": "2026-09-22", "last_seen": "2026-09-22"}}}
    signals = [{"date": "2026-09-22", "client": "demo",
                "url": "https://example.test/job-2", "hook": "new role"}]

    for boundary in ("checkpoint", "state", "history", "csv"):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint, state_path, history_path, csv_path = paths(directory)
            storage.atomic_write_json(state_path, {"old": {}})
            storage.atomic_write_json(history_path, {"old": {}})
            try:
                sweep_core.commit_sweep_batch(
                    checkpoint, state_path, history_path, csv_path,
                    FIELDS, KEY_FIELDS, state, history, signals,
                    _interrupt_after=boundary)
            except RuntimeError as error:
                assert "simulated interruption" in str(error)
            else:
                raise AssertionError(f"{boundary} interruption did not fire")
            assert checkpoint.exists(), boundary
            assert_recovered(checkpoint, state_path, history_path, csv_path,
                             state, history, signals)

            # A retry of the same logical batch cannot duplicate the signal.
            added = sweep_core.commit_sweep_batch(
                checkpoint, state_path, history_path, csv_path,
                FIELDS, KEY_FIELDS, state, history, signals)
            assert added == 0
            with csv_path.open(newline="", encoding="utf-8") as stream:
                assert list(csv.DictReader(stream)) == signals


def test_corrupt_checkpoint_is_preserved_for_operator_recovery():
    with tempfile.TemporaryDirectory() as directory:
        checkpoint, state_path, history_path, csv_path = paths(directory)
        checkpoint.write_text("{broken", encoding="utf-8")
        try:
            sweep_core.recover_sweep_checkpoint(
                checkpoint, state_path, history_path, csv_path,
                FIELDS, KEY_FIELDS)
        except storage.DataFileError:
            pass
        else:
            raise AssertionError("corrupt checkpoint was treated as empty")
        assert checkpoint.read_text(encoding="utf-8") == "{broken"


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok   {test.__name__}")
    print(f"ALL SWEEP COMMIT TESTS PASS ({len(tests)})")


if __name__ == "__main__":
    main()
