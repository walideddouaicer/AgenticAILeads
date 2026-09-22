#!/usr/bin/env python3
"""One process-safe runner for app buttons and optional refresh chains."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from storage import FileLock, LockBusy, atomic_write_json, read_json, update_json


HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AAL_DATA_DIR", HERE))
ENGINE_LOCK = DATA_DIR / ".engine.lock"
RUN_STATUS = DATA_DIR / "run_status.json"

ENGINE_SCRIPTS = {
    "sweep": "signal_engine.py",
    "signal": "signal_engine.py",
    "enrich": "enrichment_engine.py",
    "discover": "discovery_engine.py",
    "prospector": "prospector_engine.py",
    "find_mandates": "agency_discovery.py",
    "build_today": "build_today.py",
}

CHAINS = {
    "nightly": [
        ["signal_engine.py"],
        ["enrichment_engine.py", "--from-minilists", "--limit", "25"],
        ["agency_discovery.py", "--from-prospects", "--and-sweep"],
        ["build_today.py", "--n", "20"],
    ],
    "weekly": [
        ["discovery_engine.py", "--hn", "1"],
        ["agency_discovery.py", "--from-prospects", "--and-sweep"],
    ],
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def child_environment():
    return dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def command_for(name, extra_args=None):
    if name not in ENGINE_SCRIPTS:
        raise KeyError(f"Unknown engine: {name}")
    return [sys.executable, "-u", str(HERE / ENGINE_SCRIPTS[name]),
            *[str(arg) for arg in (extra_args or [])]]


def record_run(run_id, **fields):
    def mutate(data):
        runs = data.setdefault("runs", {})
        entry = runs.setdefault(run_id, {})
        entry.update(fields)
        data["latest"] = run_id
        # Bounded operational history; this is diagnosis, not business data.
        ordered = sorted(runs, key=lambda key: runs[key].get("started_at", ""))
        for old in ordered[:-50]:
            runs.pop(old, None)
        return data

    update_json(RUN_STATUS, mutate, default={}, expected_type=dict, indent=2)


class RunningProcess:
    def __init__(self, process, lock, run_id, command):
        self.process = process
        self.lock = lock
        self.run_id = run_id
        self.command = command

    def finish(self, return_code):
        try:
            record_run(self.run_id, finished_at=utc_now(),
                       status="ok" if return_code == 0 else "failed",
                       return_code=return_code)
        finally:
            self.lock.release()


def start_locked_process(command, *, label, cwd=HERE, env=None):
    """Acquire the engine lock before spawning; release if spawn fails."""
    lock = FileLock(ENGINE_LOCK, label=label).acquire()
    run_id = uuid.uuid4().hex
    try:
        process = subprocess.Popen(
            command, cwd=str(cwd), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", env=env or child_environment())
    except Exception:
        lock.release()
        raise
    try:
        record_run(run_id, label=label, command=[str(part) for part in command],
                   pid=process.pid, started_at=utc_now(), status="running")
    except Exception:
        process.terminate()
        process.wait()
        lock.release()
        raise
    return RunningProcess(process, lock, run_id, command)


def engine_busy():
    from storage import lock_info
    info = lock_info(ENGINE_LOCK)
    return bool(info and info.get("active"))


def backup_histories(keep=30):
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    for name in ("job_history.json", "mandate_history.json"):
        source = DATA_DIR / name
        if source.exists():
            target = backup_dir / f"{source.stem}_{stamp}.json"
            shutil.copy2(source, target)
    for stem in ("job_history", "mandate_history"):
        files = sorted(backup_dir.glob(f"{stem}_*.json"), reverse=True)
        for old in files[keep:]:
            old.unlink()


def run_chain(name):
    if name not in CHAINS:
        raise KeyError(f"Unknown chain: {name}")
    lock = FileLock(ENGINE_LOCK, label=f"{name} refresh").acquire()
    run_id = uuid.uuid4().hex
    try:
        record_run(run_id, label=f"{name} refresh", started_at=utc_now(),
                   status="running")
        backup_histories()
        for parts in CHAINS[name]:
            command = [sys.executable, "-u", str(HERE / parts[0]), *parts[1:]]
            result = subprocess.run(command, cwd=HERE, env=child_environment())
            if result.returncode:
                record_run(run_id, finished_at=utc_now(), status="failed",
                           return_code=result.returncode, failed_step=parts[0])
                return result.returncode
        record_run(run_id, finished_at=utc_now(), status="ok", return_code=0)
        return 0
    except Exception as error:
        try:
            record_run(run_id, finished_at=utc_now(), status="failed",
                       error=f"{type(error).__name__}: {error}")
        finally:
            raise
    finally:
        lock.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    one = sub.add_parser("run", help="run one named engine")
    one.add_argument("engine", choices=sorted(ENGINE_SCRIPTS))
    one.add_argument("args", nargs=argparse.REMAINDER)
    chain = sub.add_parser("chain", help="run a refresh chain")
    chain.add_argument("name", choices=sorted(CHAINS))
    args = parser.parse_args()
    try:
        if args.action == "chain":
            return run_chain(args.name)
        running = start_locked_process(
            command_for(args.engine, args.args), label=args.engine)
        for line in running.process.stdout:
            print(line, end="")
        code = running.process.wait()
        running.finish(code)
        return code
    except LockBusy as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
