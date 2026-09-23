# Optional container operation

The local startup and recovery guide is [README.md](README.md). This image is an optional way to run the same app on a host with Docker. Building or running it is a deliberate operator action; no deployment or schedule is created by this repository.

## Build and run

Set login credentials outside Git, mount a persistent volume at `/data`, and publish only through an access path you control. The container listens on port 8501 inside its network. For a local-only check:

```sh
docker build -t agentic-ai-leads .
docker volume create agentic-ai-leads-data
docker run --rm -p 127.0.0.1:8501:8501 \
  -v agentic-ai-leads-data:/data \
  -e APP_USERNAME=operator -e APP_PASSWORD='choose-a-private-password' \
  agentic-ai-leads
```

For shared access, place HTTPS and access control in front of the app, keep `APP_USERNAME`/`APP_PASSWORD` or `APP_USERS` configured, and keep the volume attached across restarts. Provider keys are optional. Supply them as secrets or through an `AAL_ENV_FILE` path that is outside the image; do not bake `.env` into the image.

The entrypoint calls `start.py` with container binding. It seeds the tracked employer and agency watchlists and the default profile only when `/data` is entirely empty. Existing volume contents are not overwritten or partly reseeded. Login is required. CORS and XSRF protection remain enabled.

## Recover

Stop the container and use `recovery.py backup` against its mounted data directory, then `recovery.py restore` into a separate directory. Validate and activate deliberately as described in README. Do not replace a running volume in place.

The Phase 7 container configuration was reviewed and checked locally; an actual Docker run requires a working Docker daemon on the target host. No container was deployed as part of the cleanup.