# Timer

Real-time task tracking. Multi-device. No JavaScript framework.

**[clock.event-os.pro](https://clock.event-os.pro)**

## Stack

- **[Stario](https://github.com/nicois/stario)** — async Python web framework with SSE
- **[Datastar](https://data-star.dev)** — frontend reactivity via `data-*` attributes
- **[html-tags](https://pypi.org/project/html-tags/)** — HTML generation (our own library)
- **SQLite + apsw** — single-file database
- **Litestream** — continuous replication to S3

## Architecture

Commands write. Reads stream. The relay connects them.

```
  BROWSER
  ───────────────────────────────────────────────
  POST /tasks/add          GET /tasks/stream
  POST /tasks/track        (single long-lived SSE)
  POST /tasks/stop              │
  POST /tasks/done              │ receives patches
  POST /tasks/rename            │
       │                        ▲
       ▼                        │
  COMMAND HANDLERS         STREAM HANDLER
       │                        ▲
       │  write                 │  subscribe
       ▼                        │
       DB ──────► RELAY ────────┘
                    ▲
                    │  tick
                TICKER
```

Each command writes to SQLite, then publishes an event to the relay.
The stream handler subscribes to the relay and re-renders from the DB on every event.
Every device with an open connection gets the update. No polling. No WebSockets.

## Features

- Inline-editable task names
- One-click time tracking with color-coded tiers
- Proportional duration bar across tasks
- Dynamic favicon and browser title
- Configurable update rate (60fps / 1s / 1m / off)
- Google OAuth with anonymous fallback
- Task migration when anonymous users sign in
- Persistent SQLite with Litestream backup
- Admin console with server stats

## Why This Stack

**Datastar over React** — No build step. No client-side state. The server is the source of truth. The browser renders what it's told.

**SSE over WebSockets** — SSE is HTTP. It compresses, caches, proxies, and load-balances like any other response. WebSockets don't.

**SQLite over Postgres** — One file. No network round-trips. Litestream gives durability. For a single-server app, there's nothing faster.

**[html-tags](https://pypi.org/project/html-tags/)** — a small HTML generation library. Built during this project, published to PyPI. Composes like functions, escapes by default, supports the `__html__` protocol for interop with Jinja2, Django, and MarkupSafe.

## License

MIT
