#!/bin/sh
# Use the same startup path as local installs. It seeds only an entirely
# empty volume, requires login credentials, and keeps CORS/XSRF enabled.
set -e

PORT="${PORT:-8501}"
exec python /app/start.py --host 0.0.0.0 --port "$PORT"
