# clock-go

Go + templ + Datastar port of [clock](https://github.com/Deufel/clock).
Real-time task tracker. SQLite + Litestream. Google sign-in.

## Stack

- **Go 1.24** — single static binary, no runtime dependencies
- **[templ](https://github.com/a-h/templ)** — HTML generation
- **[Datastar](https://data-star.dev)** — frontend reactivity via `data-*` attributes
- **[modernc.org/sqlite](https://gitlab.com/cznic/sqlite)** — pure-Go SQLite, no CGO
- **[Litestream](https://litestream.io)** — continuous replication to S3

## Architecture

CQRS with a fat-morph SSE pattern:

- Commands (POST endpoints) mutate the DB and publish a topic to an in-memory hub.
- One long-lived SSE stream per browser tab subscribes to its session's topics.
- On every published event, the stream re-renders the entire dynamic region.
- A per-session ticker emits "tick" events at the configured rate; rate
  changes interrupt the sleeping ticker via a separate "rate" topic.

The view is a pure function of state. No client-side reactivity framework.

## Run locally

```bash
go mod tidy
templ generate
go build -o clock-go .
DB_PATH=./clock-go.db ./clock-go
```

Visit http://localhost:8000.

## Environment

- `DB_PATH` — path to SQLite file (default `/app/data/clock-go.db`)
- `PORT` — HTTP port (default `8000`)
- `COOKIE_SECRET` — HMAC key for signed session cookies (required for production)
- `PUBLIC_URL` — e.g. `https://clock.deufel.dev` (used for OAuth redirect_uri)
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` — for `/oauth/google`
- `ADMIN_EMAIL` — email allowed to access `/admin`
- `MINIO_ENDPOINT`, `LITESTREAM_ACCESS_KEY_ID`, `LITESTREAM_SECRET_ACCESS_KEY` — for backups
