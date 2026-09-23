"""Operational backups without credentials and separate-directory restores.

Stop the app and refresh processes before taking a snapshot. Restoring never
changes the active data directory; the operator chooses when to activate it.
"""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import zipfile

from bootstrap import data_directory
from storage import lock_info


DATA_FILES = frozenset({
    "agency_board_mappings.json", "agency_board_refresh.json",
    "agency_board_suggestions.json", "agency_discovery_state.json",
    "agency_watchlist.json", "board_refresh.json", "client_meetings.json",
    "company_board_mappings.json", "company_domains.json",
    "contact_lookup_status.json", "discovery_state.json",
    "enriched_contacts.json", "enriched_signals.csv", "icp.json",
    "inbox_status.json", "job_history.json", "mandate_history.json",
    "mandate_sweep.pending.json", "prospect_identity_pending.json",
    "prospect_key_aliases.json", "prospect_signals.csv",
    "prospect_signals_state.json", "prospects.csv", "prospects_config.json",
    "prospects_seen.json", "prospects_status.json", "run_status.json",
    "signal_sweep.pending.json", "signals.csv", "signals_state.json",
    "today.json", "watchlist.json",
})
MANIFEST = "manifest.json"
FORMAT_VERSION = 1


def _valid_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (not path.is_absolute() and ".." not in path.parts
            and (name in DATA_FILES or
                 (len(path.parts) == 2 and path.parts[0] == "configs"
                  and path.suffix == ".json" and not path.name.startswith("."))))


def _validate_content(name: str, content: bytes) -> None:
    text = content.decode("utf-8-sig")
    if name.endswith(".json"):
        if not isinstance(json.loads(text), dict):
            raise ValueError(f"Expected a JSON object in {name}")
    elif name.endswith(".csv"):
        list(csv.reader(io.StringIO(text)))


def _snapshot_files(data: Path) -> dict[str, bytes]:
    if not data.is_dir():
        raise ValueError(f"Data directory does not exist: {data}")
    lock = lock_info(data / ".engine.lock")
    if lock and lock.get("active"):
        raise ValueError("A refresh owns the data directory; wait for it to finish")
    paths = [data / name for name in sorted(DATA_FILES) if (data / name).exists()]
    config_dir = data / "configs"
    if config_dir.is_symlink():
        raise ValueError("Refusing a symlinked configs directory")
    if config_dir.is_dir():
        paths.extend(sorted(config_dir.glob("*.json")))
    files = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Refusing non-regular data file: {path}")
        name = path.relative_to(data).as_posix()
        if not _valid_name(name):
            raise ValueError(f"Unexpected data path: {name}")
        content = path.read_bytes()
        _validate_content(name, content)
        files[name] = content
    if not files:
        raise ValueError("No operational data files to back up")
    return files


def create_backup(data: Path, output: Path) -> Path:
    data, output = Path(data).resolve(), Path(output).resolve()
    files = _snapshot_files(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Backup already exists: {output}")
    manifest = {
        "format": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": {name: {"sha256": hashlib.sha256(content).hexdigest(),
                         "size": len(content)} for name, content in files.items()},
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".backup-", suffix=".zip",
                                         dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2))
            for name, content in files.items():
                archive.writestr(name, content)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output


def read_backup(archive_path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or names.count(MANIFEST) != 1:
            raise ValueError("Backup has duplicate entries or no manifest")
        manifest = json.loads(archive.read(MANIFEST))
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_VERSION:
            raise ValueError("Unsupported backup format")
        entries = manifest.get("files")
        if not isinstance(entries, dict) or not entries:
            raise ValueError("Backup manifest has no data files")
        if set(names) != set(entries) | {MANIFEST}:
            raise ValueError("Backup files do not match its manifest")
        files = {}
        for name, expected in entries.items():
            if not _valid_name(name):
                raise ValueError(f"Unsafe or unexpected backup path: {name}")
            if not isinstance(expected, dict):
                raise ValueError(f"Invalid backup manifest entry: {name}")
            content = archive.read(name)
            if (len(content) != expected.get("size") or
                    hashlib.sha256(content).hexdigest() != expected.get("sha256")):
                raise ValueError(f"Backup integrity check failed: {name}")
            _validate_content(name, content)
            files[name] = content
        return files


def restore_to_directory(archive_path: Path, target: Path) -> Path:
    """Validate all content, then create a separate directory; never activate."""
    files = read_backup(archive_path)
    target = Path(target).resolve()
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Restore target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".restore-", dir=target.parent))
    try:
        for name, content in files.items():
            path = temporary / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)
            path.write_bytes(content)
            path.chmod(0o600)
        # A same-parent rename makes the validated tree visible at once.
        temporary.rename(target)
    finally:
        if temporary.exists():
            if temporary.resolve().parent != target.parent:
                raise ValueError("Temporary restore path escaped its target directory")
            shutil.rmtree(temporary)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    backup = sub.add_parser("backup", help="archive operational data without secrets")
    backup.add_argument("--data-dir", type=Path)
    backup.add_argument("--output", type=Path)
    restore = sub.add_parser("restore", help="validate and unpack to a new directory")
    restore.add_argument("archive", type=Path)
    restore.add_argument("target", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "backup":
            data = args.data_dir or data_directory()
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output = args.output or Path(data) / "backups" / f"operational-{stamp}.zip"
            created = create_backup(data, output)
            print(f"Backup saved: {created}")
        else:
            restored = restore_to_directory(args.archive, args.target)
            print(f"Validated restore: {restored}")
            print(f"To activate deliberately, start with AAL_DATA_DIR={restored}")
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError,
            zipfile.BadZipFile, KeyError, TypeError) as error:
        print(f"Recovery stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
