"""All routine engine outputs use the configured data directory and strict IO."""

import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agency_discovery
import build_today
import discovery_engine
import enrichment_engine
import import_clay
import minilist_engine
import prospect_signal_engine
import prospector_engine
import runner
import runtime
import signal_engine
import storage


def test_writer_paths_follow_configured_data_directory():
    expected = Path(os.environ.get("AAL_DATA_DIR", ROOT))
    assert runtime.DATA_DIR == expected
    assert runtime.ENV_FILE == Path(
        os.environ.get("AAL_ENV_FILE", expected / ".env"))
    paths = [
        runner.ENGINE_LOCK, runner.RUN_STATUS,
        signal_engine.STATE_FILE, signal_engine.CSV_FILE,
        signal_engine.HISTORY_FILE, signal_engine.CHECKPOINT_FILE,
        prospect_signal_engine.STATE_FILE, prospect_signal_engine.CSV_FILE,
        prospect_signal_engine.HISTORY_FILE,
        prospect_signal_engine.CHECKPOINT_FILE,
        discovery_engine.WATCHLIST_FILE, discovery_engine.STATE_FILE,
        agency_discovery.AGENCY_WATCHLIST_FILE, agency_discovery.STATE_FILE,
        prospector_engine.PROSPECTS_CSV, prospector_engine.SEEN_FILE,
        prospector_engine.PROBE_FILE, import_clay.PROSPECTS_CSV,
        import_clay.SEEN_FILE, enrichment_engine.OUT_CSV,
        enrichment_engine.CACHE_FILE, build_today.TODAY_FILE,
        minilist_engine.OUT_DIR,
    ]
    wrong = [str(path) for path in paths if path.parent != expected]
    assert not wrong, wrong


def test_discovery_and_minilist_reject_corrupt_json():
    with tempfile.TemporaryDirectory() as directory:
        broken = Path(directory) / "broken.json"
        broken.write_text("{broken", encoding="utf-8")
        old = discovery_engine.STATE_FILE
        discovery_engine.STATE_FILE = broken
        try:
            try:
                discovery_engine.load_state()
            except storage.DataFileError:
                pass
            else:
                raise AssertionError("discovery treated corruption as empty")
            try:
                minilist_engine._load_json(broken, {})
            except storage.DataFileError:
                pass
            else:
                raise AssertionError("mini-list treated corruption as empty")
            assert broken.read_text(encoding="utf-8") == "{broken"
        finally:
            discovery_engine.STATE_FILE = old


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok   {test.__name__}")
    print(f"ALL ENGINE STORAGE TESTS PASS ({len(tests)})")


if __name__ == "__main__":
    main()
