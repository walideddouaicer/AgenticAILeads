#!/usr/bin/env python3
"""Small, process-safe persistence primitives for the file-backed app.

Business modules should not open JSON/CSV files for read-modify-write cycles.
Use these helpers so malformed data is never mistaken for an empty file, writes
replace files atomically, and two app/engine processes cannot overwrite each
other's updates.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import uuid


class StorageError(RuntimeError):
    """Base class for persistence failures safe to show to an operator."""


class DataFileError(StorageError):
    """An existing data file cannot be parsed or has the wrong shape."""


class LockBusy(StorageError):
    """Another live process owns the requested lock."""


class ConflictError(StorageError):
    """A record changed since the caller loaded it."""


DELETE = object()
UNSET = object()


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 87:
            # Windows reports an invalid/nonexistent PID this way rather
            # than with ProcessLookupError.
            return False
        # Windows and constrained runtimes can reject signal 0 for reasons
        # other than a missing process. Keeping the lock is the safe choice.
        return True
    return True


def _read_lock(path: Path) -> tuple[dict, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}, ""
    try:
        data = json.loads(raw)
        return (data if isinstance(data, dict) else {}), raw
    except json.JSONDecodeError:
        # Preserve non-JSON legacy lock contents so acquisition can compare
        # and retire them safely instead of leaving a permanent dead lock.
        return {}, raw


class FileLock:
    """Atomic, owner-token lock file with dead-process recovery.

    Acquisition uses O_EXCL, so checking and creating the lock are one kernel
    operation. A release removes only a lock carrying this instance's token.
    """

    def __init__(self, path, *, label="operation", timeout=0.0, poll=0.05):
        self.path = Path(path)
        self.label = label
        self.timeout = max(float(timeout), 0.0)
        self.poll = max(float(poll), 0.01)
        self.token = uuid.uuid4().hex
        self.acquired = False

    def _payload(self) -> bytes:
        return json.dumps({
            "token": self.token,
            "pid": os.getpid(),
            "label": self.label,
            "created_at": time.time(),
        }, sort_keys=True).encode("utf-8")

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                owner, raw = _read_lock(self.path)
                pid = owner.get("pid")
                if raw and not _pid_alive(pid):
                    # Compare again immediately before removal. A new owner may
                    # have replaced a dead lock between our read and this point.
                    try:
                        if self.path.read_text(encoding="utf-8") == raw:
                            self.path.unlink()
                            continue
                    except FileNotFoundError:
                        continue
                if time.monotonic() >= deadline:
                    who = f"pid {pid}" if pid else "an unknown owner"
                    raise LockBusy(f"{self.label} is already running ({who})")
                time.sleep(self.poll)
                continue
            try:
                os.write(fd, self._payload())
                os.fsync(fd)
            finally:
                os.close(fd)
            self.acquired = True
            return self

    def release(self):
        if not self.acquired:
            return
        owner, _ = _read_lock(self.path)
        if owner.get("token") == self.token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()


def lock_info(path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    data, _ = _read_lock(path)
    return {**data, "active": _pid_alive(data.get("pid"))}


def revision(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def atomic_write_bytes(path, content: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                    dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            # Directory fsync is unavailable on some platforms. os.replace is
            # still atomic; the durability guarantee is simply weaker there.
            pass
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def atomic_write_text(path, content: str) -> None:
    atomic_write_bytes(path, content.encode("utf-8"))


def atomic_write_json(path, value, *, indent=None) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=indent,
                         separators=None if indent is not None else (",", ":"))
    atomic_write_text(path, content)


def read_json(path, *, default=UNSET, expected_type=None):
    path = Path(path)
    if not path.exists():
        if default is UNSET:
            raise DataFileError(f"{path.name} is missing")
        return copy.deepcopy(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DataFileError(
            f"{path.name} is damaged or unreadable; it was left unchanged. "
            "Restore a known-good backup before continuing."
        ) from error
    if expected_type is not None and not isinstance(value, expected_type):
        name = getattr(expected_type, "__name__", str(expected_type))
        raise DataFileError(f"{path.name} must contain a JSON {name}")
    return value


def update_json(path, mutator, *, default, expected_type=dict, indent=2):
    """Lock, reload, mutate and atomically replace one JSON file."""
    path = Path(path)
    with FileLock(path.with_name(path.name + ".lock"),
                  label=f"updating {path.name}", timeout=5):
        value = read_json(path, default=default, expected_type=expected_type)
        result = mutator(value)
        if result is not None:
            value = result
        if not isinstance(value, expected_type):
            raise DataFileError(f"Update for {path.name} produced invalid data")
        atomic_write_json(path, value, indent=indent)
        return value


def update_json_record(path, key, mutator, *, expected=UNSET):
    """Update one mapping entry without overwriting unrelated records.

    `expected` is the revision returned for a previously-read record. Passing it
    detects a stale edit. The mutator receives a deep copy and may return DELETE.
    """
    outcome = {}

    def apply(records):
        current = records.get(key)
        current_revision = revision(current)
        if expected is not UNSET and current_revision != expected:
            raise ConflictError(
                f"This record changed in another session. Reload before saving."
            )
        updated = mutator(copy.deepcopy(current))
        if updated is DELETE:
            records.pop(key, None)
            saved = None
        else:
            records[key] = updated
            saved = updated
        outcome.update(value=copy.deepcopy(saved), revision=revision(saved))

    records = update_json(path, apply, default={}, expected_type=dict, indent=1)
    return records, outcome["value"], outcome["revision"]


def render_csv(rows, fieldnames) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def append_csv_unique(path, rows, fieldnames, key):
    """Append rows idempotently by rewriting one small CSV under a lock."""
    path = Path(path)
    incoming = list(rows)
    if not incoming:
        return 0
    with FileLock(path.with_name(path.name + ".lock"),
                  label=f"updating {path.name}", timeout=5):
        existing = []
        if path.exists():
            try:
                with path.open(newline="", encoding="utf-8") as stream:
                    reader = csv.DictReader(stream)
                    if reader.fieldnames != list(fieldnames):
                        raise DataFileError(
                            f"{path.name} has unexpected columns; left unchanged")
                    existing = list(reader)
            except (OSError, UnicodeError, csv.Error) as error:
                if isinstance(error, DataFileError):
                    raise
                raise DataFileError(f"{path.name} is unreadable; left unchanged") from error
        seen = {key(row) for row in existing}
        added = []
        for row in incoming:
            identity = key(row)
            if identity not in seen:
                seen.add(identity)
                added.append(row)
        if added:
            atomic_write_text(path, render_csv(existing + added, fieldnames))
        return len(added)
