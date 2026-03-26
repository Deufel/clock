#!/bin/bash
set -e

mkdir -p /app/data

# Restore DB from MinIO if it doesn't exist locally
if [ ! -f /app/data/clock.db ]; then
    echo "No local DB found, attempting restore from MinIO..."
    litestream restore -if-replica-exists -config /app/litestream.yml /app/data/clock.db || echo "No backup found, starting fresh."
fi

# Run the app under Litestream (it replicates WAL changes continuously)
exec litestream replicate -exec "uv run python main.py" -config /app/litestream.yml
