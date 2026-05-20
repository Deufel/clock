#!/bin/sh
set -e

mkdir -p /app/data

# Restore DB from S3 if there's no local copy yet.
if [ ! -f "$DB_PATH" ]; then
    echo "No local DB at $DB_PATH, attempting Litestream restore..."
    litestream restore -if-replica-exists -config /app/litestream.yml "$DB_PATH" || \
        echo "No backup found, starting with empty DB."
fi

# Replicate WAL changes while the app is running.
exec litestream replicate -exec "/app/clock-go" -config /app/litestream.yml
