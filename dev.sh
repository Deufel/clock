#!/usr/bin/env bash
# Local dev runner: regenerate templates, build, load .env, run.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "ERROR: no .env file. Copy .env.example to .env and fill it in." >&2
    exit 1
fi

templ generate
go build -o clock-go .

set -a
# shellcheck disable=SC1091
source .env
set +a

exec ./clock-go
