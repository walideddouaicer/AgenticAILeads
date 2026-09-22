"""Central runtime paths shared by the app, CLI tools, and engines."""

import os
from pathlib import Path


HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AAL_DATA_DIR", HERE)).expanduser()
ENV_FILE = Path(os.environ.get("AAL_ENV_FILE", DATA_DIR / ".env")).expanduser()
