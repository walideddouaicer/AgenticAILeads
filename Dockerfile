# Agentic AI Leads — container image for Coolify (or any Docker host).
#
# The app stores its data as files under AAL_DATA_DIR. In production that
# points at /data, which Coolify mounts as a PERSISTENT VOLUME so data
# survives redeploys. The entrypoint seeds the separate board lists and
# default profile only on an entirely empty first boot.
FROM python:3.13-slim

# Faster, cleaner Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AAL_DATA_DIR=/data

WORKDIR /app

# Install dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY . .

# Persistent data directory (mount a volume here in Coolify).
RUN mkdir -p /data && chmod +x /app/docker-entrypoint.sh

EXPOSE 8501

ENTRYPOINT ["/app/docker-entrypoint.sh"]
