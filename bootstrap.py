"""Choose one data directory and seed curated defaults on its first use."""

import json
import os
from pathlib import Path
import shutil


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_FILES = ("watchlist.json", "agency_watchlist.json",
                 "configs/default.json")


def data_directory() -> Path:
    """Use a local data folder for new installs; honor an existing override."""
    path = Path(os.environ.get("AAL_DATA_DIR") or PROJECT_DIR / "data")
    path = path.expanduser().resolve()
    os.environ["AAL_DATA_DIR"] = str(path)
    return path


def seed_defaults(target: Path) -> bool:
    """Copy defaults only when the entire destination is empty."""
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        return False
    # Validate every source before creating a partly seeded directory.
    for name in DEFAULT_FILES:
        source = PROJECT_DIR / name
        with source.open(encoding="utf-8") as stream:
            json.load(stream)
    target.mkdir(parents=True, exist_ok=True)
    target.chmod(0o700)
    for name in DEFAULT_FILES:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.chmod(0o700)
        shutil.copy2(PROJECT_DIR / name, destination)
        destination.chmod(0o600)
    return True
