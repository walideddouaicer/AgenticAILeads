#!/bin/sh
# Seed the persistent data volume from the image's committed defaults on the
# first boot, then launch Streamlit. Existing files on the volume are never
# overwritten, so user data is preserved across redeploys.
set -e

DATA_DIR="${AAL_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR" "$DATA_DIR/configs"

# Seed the curated watchlist (git-tracked) if the volume doesn't have one yet.
if [ -f /app/watchlist.json ] && [ ! -f "$DATA_DIR/watchlist.json" ]; then
    cp /app/watchlist.json "$DATA_DIR/watchlist.json"
    echo "seeded watchlist.json"
fi

# Seed any committed client configs that aren't already on the volume.
if [ -d /app/configs ]; then
    for f in /app/configs/*.json; do
        [ -e "$f" ] || continue
        base=$(basename "$f")
        if [ ! -f "$DATA_DIR/configs/$base" ]; then
            cp "$f" "$DATA_DIR/configs/$base"
            echo "seeded configs/$base"
        fi
    done
fi

PORT="${PORT:-8501}"
echo "starting Streamlit on 0.0.0.0:$PORT (data dir: $DATA_DIR)"
exec streamlit run app.py \
    --server.address=0.0.0.0 \
    --server.port="$PORT" \
    --server.headless=true \
    --browser.gatherUsageStats=false \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false
