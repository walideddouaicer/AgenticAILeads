"""Start the local app with authentication and web protections enabled."""

import argparse
import os
import subprocess
import sys

from bootstrap import data_directory, seed_defaults


def credentials_configured() -> bool:
    users = os.environ.get("APP_USERS", "").strip()
    if users:
        return all(":" in pair and all(part.strip() for part in pair.split(":", 1))
                   for pair in users.split(","))
    return bool(os.environ.get("APP_USERNAME", "").strip()
                and os.environ.get("APP_PASSWORD", ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address; default is local loopback")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--check", action="store_true",
                        help="Inspect setup without creating data or starting the app")
    args = parser.parse_args()
    if os.environ.get("AAL_DISABLE_AUTH") == "1":
        parser.error("AAL_DISABLE_AUTH is for isolated tests only; unset it to start the app")
    if not credentials_configured():
        parser.error("Set APP_USERNAME and APP_PASSWORD (or APP_USERS) in the environment")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    data = data_directory()
    if data.exists() and not data.is_dir():
        parser.error(f"Data path is not a directory: {data}")
    if args.check:
        empty = not data.exists() or not any(data.iterdir())
        print(f"Ready: data={data}; defaults_would_seed={empty}; host={args.host}; "
              "CORS/XSRF enabled")
        return 0
    seed_defaults(data)
    command = [sys.executable, "-m", "streamlit", "run", "app.py",
               f"--server.address={args.host}", f"--server.port={args.port}",
               "--server.headless=true", "--server.enableCORS=true",
               "--server.enableXsrfProtection=true",
               "--browser.gatherUsageStats=false"]
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    if os.name == "nt":
        return subprocess.call(command, env=os.environ)
    os.execvpe(sys.executable, command, os.environ)


if __name__ == "__main__":
    sys.exit(main())
