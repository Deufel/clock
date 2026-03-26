"""
Clock — task time-tracker with live updates, Google auth, and admin stats.

Modernised to use py_sse RSGI features: signed cookies, beforeware,
embedded Granian server, and simplified routing.

Run with: python main.py
"""

import asyncio, os, httpx
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from html_tags import setup_tags, to_html
from py_sse import (
    create_app, create_relay, create_signer, set_cookie,
    patch_elements, static, serve,
)
from db import (
    new_session, valid_session, get_json, set_json,
    add_task, get_tasks, get_task, task_start_tracking,
    task_stop_tracking, task_complete, task_elapsed, stop_all_tracking,
    rename_task, get_session_user, find_or_create_user_and_link,
    admin_stats,
)

setup_tags()

app   = create_app()
relay = create_relay()
static(app, "/favicon.svg", "favicon.svg")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
ADMIN_EMAIL          = os.environ.get("ADMIN_EMAIL", "")
COOKIE_SECRET        = os.environ.get("COOKIE_SECRET", "change-me-in-production")

TZ     = ZoneInfo("America/Chicago")
signer = create_signer(COOKIE_SECRET)

# ---------------------------------------------------------------------------
# Raw-HTML helper (replaces stario SafeString)
# ---------------------------------------------------------------------------

class Safe:
    """Wraps a string so html_tags.to_html() emits it unescaped."""
    __slots__ = ("_s",)
    def __init__(self, s): self._s = str(s)
    def __str__(self):  return self._s
    def __html__(self): return self._s

# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _read_sid(req) -> str | None:
    """Unsign the session cookie, return sid or None."""
    raw = req["cookies"].get("sid", "")
    return signer.unsign(raw, max_age=None)

def _write_sid(req, sid: str):
    """Sign and set the session cookie."""
    set_cookie(req, "sid", signer.sign(sid),
               httponly=True, secure=True, samesite="Lax", path="/")

def _ensure_sid(req) -> str:
    """Return an existing valid sid or create a new one."""
    sid = _read_sid(req)
    if sid and valid_session(sid):
        return sid
    sid = new_session()
    _write_sid(req, sid)
    return sid

# ---------------------------------------------------------------------------
# Beforeware — inject sid + user into every request
# ---------------------------------------------------------------------------

@app.before
async def inject_session(req):
    # Skip session injection for health check
    if req["path"] == "/health":
        return
    req["sid"]  = _ensure_sid(req)
    req["user"] = get_session_user(req["sid"])

# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

RATE_OPTIONS = [("live", 0.016), ("1s", 1.0), ("1m", 60.0), ("off", 0)]
RATE_MAP     = {k: v for k, v in RATE_OPTIONS}

def get_tasks_rate(sid): return get_json(sid, "tasks_rate", lambda: 1.0)

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def to12(h): return (h % 12 or 12, "AM" if h < 12 else "PM")

def fmt_elapsed(secs):
    if secs >= 3600:
        hh, rem = divmod(int(secs), 3600)
        return f"{hh}h {rem//60:02d}m {rem%60:02d}.{int(secs*100)%100:02d}s"
    mm = int(secs) // 60
    ss = secs - mm * 60
    return f"{mm:02d}:{ss:05.2f}"

def fmt_duration(secs):
    if secs < 60: return f"{secs:.0f}s"
    if secs < 3600:
        m, s = divmod(int(secs), 60)
        return f"{m}m {s}s"
    h, rem = divmod(int(secs), 3600)
    return f"{h}h {rem // 60}m"

# ---------------------------------------------------------------------------
# Title / meta
# ---------------------------------------------------------------------------

def make_title(sid):
    now = datetime.now(TZ)
    h12, ampm = to12(now.hour)
    date = now.strftime('%b %-d, %Y')
    time_ = f"{h12}:{now.minute:02d}{ampm.lower()}"
    tasks = get_tasks(sid)
    active = [t for t in tasks if t["track_start"] is not None]
    if active:
        return f"{date} | {time_} | {active[0]['name']}"
    return f"{date} | {time_}"

def make_meta(sid, tasks=None):
    if tasks is None: tasks = get_tasks(sid)
    active = [t for t in tasks if t["track_start"] is not None]
    if active:
        t = active[0]
        meta_text = f"tracking · {t['name']} · {fmt_elapsed(task_elapsed(t))}"
    else:
        now = datetime.now(TZ)
        h12, ampm = to12(now.hour)
        meta_text = f"{h12}:{now.minute:02d} {ampm} · {now.strftime('%b %-d')}"
    return meta_text, make_title(sid)

# ---------------------------------------------------------------------------
# Task rendering
# ---------------------------------------------------------------------------

def task_row(t):
    tid = t["id"]
    elapsed = fmt_elapsed(task_elapsed(t))
    tracking = t["track_start"] is not None
    toggle_url = f"/tasks/stop?id={tid}" if tracking else f"/tasks/track?id={tid}"
    toggle_label = "Stop" if tracking else "Track"
    toggle_cls = "task-btn on" if tracking else "task-btn"
    return Li({"class": "task-row", "id": f"task-row-{tid}"},
        Span({"class": "task-name", "contenteditable": "true", "data-ignore-morph": True,
              "data-on:blur": f"@post('/tasks/rename?id={tid}&name=' + encodeURIComponent(el.innerText.trim()))",
              "data-on:keydown": "if(event.key==='Enter'){event.preventDefault();el.blur()} if(event.key==='Escape'){el.innerText=el.dataset.original;el.blur()}",
              "data-original": t["name"]}, t["name"]),
        Span({"class": "task-time", "id": f"task-time-{tid}"}, elapsed),
        Button({"class": toggle_cls, "data-url": toggle_url}, toggle_label),
        Button({"class": "task-btn", "data-url": f"/tasks/done?id={tid}"}, "✓"))

def task_list(tasks):
    if not tasks: return P({"class": "task-empty"}, "no tasks yet")
    return Ul({"class": "task-list"}, *[task_row(t) for t in tasks])

def task_bar(tasks):
    total = sum(task_elapsed(t) for t in tasks)
    if total == 0: return Span()
    colors = ["#e54", "#2a2", "#47f", "#f90", "#c4f", "#0cc", "#fa0", "#f47"]
    bars, legend = [], []
    for i, t in enumerate(tasks):
        e = task_elapsed(t)
        if e <= 0: continue
        pct, color = e / total * 100, colors[i % len(colors)]
        bars.append(Div({"class": "bar-seg", "style": f"width:{pct:.1f}%;background:{color}", "title": f"{t['name']}: {round(pct)}%"}))
        legend.append(Span({"class": "bar-legend-item"}, Span({"style": f"color:{color}"}, "●"), f" {t['name']} ", Span({"class": "bar-pct"}, f"{round(pct)}%")))
    return Div({"class": "task-bar"}, Div({"class": "bar-track"}, *bars), Div({"class": "bar-legend"}, *legend))

def task_panel(tasks):
    return Div(Div({"id": "task-bar"}, task_bar(tasks)), task_list(tasks))

# ---------------------------------------------------------------------------
# Task commands (mutate state + publish)
# ---------------------------------------------------------------------------

def cmd_task_add(sid, name):
    add_task(sid, name.strip())
    relay.publish(f"tasks.{sid}.update", None)

def cmd_task_track(sid, tid):
    stop_all_tracking(sid)
    task_start_tracking(tid)
    relay.publish(f"tasks.{sid}.update", None)

def cmd_task_stop(sid, tid):
    task_stop_tracking(tid)
    relay.publish(f"tasks.{sid}.update", None)

def cmd_task_done(sid, tid):
    task_complete(tid)
    relay.publish(f"tasks.{sid}.update", None)

def cmd_task_rename(sid, tid, name):
    rename_task(tid, name)
    relay.publish(f"tasks.{sid}.update", None)

# ---------------------------------------------------------------------------
# Rate toggle
# ---------------------------------------------------------------------------

def rate_label(key):
    return {"live": "Live", "1s": "1s", "1m": "1m", "off": "Off"}[key]

def rate_toggle(current_rate):
    current_key = next((k for k, v in RATE_OPTIONS if v == current_rate), "1s")
    buttons = []
    for key, _ in RATE_OPTIONS:
        cls = "active" if key == current_key else ""
        buttons.append(Button({"class": cls, "data-on:click": f"@post('/tasks/rate?r={key}')"},
            rate_label(key)))
    return Div({"class": "toggle-bar"}, *buttons)

# ---------------------------------------------------------------------------
# SSE ticker
# ---------------------------------------------------------------------------

async def _tasks_ticker(sid):
    sub = relay.subscribe(f"tasks.{sid}.rate")
    rate_event = asyncio.ensure_future(sub.__anext__())
    while True:
        rate = get_tasks_rate(sid)
        if rate == 0:
            await rate_event
            rate_event = asyncio.ensure_future(sub.__anext__())
            continue
        sleep = asyncio.ensure_future(asyncio.sleep(rate))
        done, _ = await asyncio.wait({sleep, rate_event}, return_when=asyncio.FIRST_COMPLETED)
        if rate_event in done:
            sleep.cancel()
            rate_event = asyncio.ensure_future(sub.__anext__())
            continue
        relay.publish(f"tasks.{sid}.tick", None)

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

TASK_CSS = """
.task-btn { padding: 0.4rem 0.8rem; font-size: 0.8rem; min-width: 3.5rem; }
.task-btn.on { background: #e54; border-color: #e54; color: #fff; }
.task-input { padding: 0.6rem; border-radius: 0.5rem; border: 1px solid #333; background: #151515; color: #fff; font: inherit; flex: 1; font-size: 16px; }
@media (prefers-color-scheme: light) { .task-input { background: #fff; color: #222; border-color: #ddd; } }
.task-input:empty::before { content: "new task..."; color: #666; }
.task-input:focus { outline: 2px solid #e54; outline-offset: 2px; }
.task-time { color: #888; font-size: 0.85rem; font-family: 'JetBrains Mono', 'SF Mono', monospace; min-width: 6rem; text-align: right; }
.task-row { display: flex; align-items: center; gap: 0.75rem; padding: 0.5rem 0; border-bottom: 1px solid #222; }
.task-name { flex: 1; font-size: 1rem; cursor: text; outline: none; border-radius: 0.25rem; padding: 0.1rem 0.3rem; }
.task-name:focus { background: #1a1a1a; outline: 2px solid #e54; outline-offset: 2px; }
@media (prefers-color-scheme: light) { .task-name:focus { background: #f0f0f0; } }
.task-empty { color: #555; text-align: center; }
.task-list { list-style: none; padding: 0; width: 100%; }
.bar-track { width: 100%; height: 1.2rem; border-radius: 0.4rem; overflow: hidden; background: #1a1a1a; display: flex; }
.bar-seg { height: 100%; }
.bar-legend { margin-top: 0.4rem; display: flex; flex-wrap: wrap; gap: 0.6rem; justify-content: center; }
.bar-legend-item { font-size: 0.75rem; color: #888; }
.bar-pct { font-family: 'JetBrains Mono', 'SF Mono', monospace; }
.task-bar { margin-top: 1rem; }
.toggle-bar { display: inline-flex; border-radius: 0.5rem; overflow: hidden; border: 1px solid #333; font-size: 0.75rem; }
.toggle-bar button { padding: 0.4rem 1rem; border: none; border-radius: 0; background: #151515; color: #888; cursor: pointer; border-right: 1px solid #333; transition: all 0.15s; font-family: inherit; font-size: inherit; }
.toggle-bar button:last-child { border-right: none; }
.toggle-bar button:hover { background: #222; color: #eee; }
.toggle-bar button.active { background: #e54; color: #fff; }
@media (prefers-color-scheme: light) { .toggle-bar button { background: #fff; color: #888; border-color: #ddd; } .toggle-bar { border-color: #ddd; } .toggle-bar button:hover { background: #eee; color: #222; } }
"""

LOGO_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round"><path d="M12 11.4V9.1"/><path d="m12 17 6.59-6.59"/><path d="m15.05 5.7-.218-.691a3 3 0 0 0-5.663 0L4.418 19.695A1 1 0 0 0 5.37 21h13.253a1 1 0 0 0 .951-1.31L18.45 16.2"/><circle cx="20" cy="9" r="2"/></svg>'
GH_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round"><path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/></svg>'
GOOGLE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>'

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@200;300;400;500;600;700&family=JetBrains+Mono:wght@400;700&display=swap');
*, *::before, *::after { box-sizing: border-box; margin: 0; }
body { font-family: Inter, system-ui, sans-serif; background: #0a0a0a; color: #eee; margin: 0; }
@media (prefers-color-scheme: light) { body { background: #f8f8f8; color: #222; } }
.page { display: grid; grid-template-rows: auto auto 1fr auto; align-items: start; justify-items: center; max-height: 100svh; min-height: 100svh; padding: 1rem 1rem; gap: 1rem; overflow-y: auto; }
.app-header { width: min(90vw, 500px); display: flex; align-items: center; justify-content: space-between; }
.app-logo { display: flex; align-items: center; gap: 0.5rem; font-size: 1.25rem; font-weight: 700; color: #eee; text-decoration: none; }
@media (prefers-color-scheme: light) { .app-logo { color: #222; } }
.app-logo svg { opacity: 0.8; }
.header-actions { display: flex; align-items: center; gap: 1rem; }
.auth-link { color: #888; font-size: 0.8rem; text-decoration: none; transition: color 0.15s; }
.auth-link:hover { color: #eee; }
@media (prefers-color-scheme: light) { .auth-link:hover { color: #222; } }
.gh-link { color: #666; transition: color 0.15s; }
.gh-link:hover { color: #eee; }
@media (prefers-color-scheme: light) { .gh-link:hover { color: #222; } }
.meta { text-align: center; color: #555; font-size: 0.8rem; font-family: 'JetBrains Mono', monospace; letter-spacing: 0.1em; text-transform: uppercase; min-height: 1.4em; }
.content { width: min(90vw, 500px); display: flex; flex-direction: column; align-items: center; gap: 1rem; }
.controls { display: flex; gap: 0.75rem; align-items: center; justify-content: center; flex-wrap: wrap; }
button { padding: 0.5rem 1.2rem; border-radius: 0.5rem; border: 1px solid #333; background: #151515; color: #eee; font: inherit; cursor: pointer; font-size: 0.85rem; transition: all 0.15s; }
@media (prefers-color-scheme: light) { button { background: #fff; color: #222; border-color: #ddd; } }
button:hover { background: #222; border-color: #555; }
@media (prefers-color-scheme: light) { button:hover { background: #eee; } }
button.on { background: #e54; border-color: #e54; color: #fff; }
""" + TASK_CSS

LANDING_CSS = """
.landing { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2rem; min-height: 100svh; padding: 2rem; }
.landing-logo { display: flex; align-items: center; gap: 0.75rem; font-size: 2rem; font-weight: 700; }
.landing-logo svg { opacity: 0.8; }
.landing-subtitle { color: #888; font-size: 1rem; margin-top: -1rem; }
.landing-actions { display: flex; flex-direction: column; gap: 1rem; align-items: center; }
.btn-google { display: flex; align-items: center; gap: 0.75rem; padding: 0.75rem 1.5rem; border-radius: 0.5rem; border: 1px solid #333; background: #151515; color: #eee; font: inherit; cursor: pointer; font-size: 1rem; transition: all 0.15s; text-decoration: none; }
.btn-google:hover { background: #222; border-color: #555; }
@media (prefers-color-scheme: light) { .btn-google { background: #fff; color: #222; border-color: #ddd; } .btn-google:hover { background: #eee; } }
.btn-public { color: #666; font-size: 0.85rem; text-decoration: underline; cursor: pointer; background: none; border: none; font: inherit; }
.btn-public:hover { color: #eee; }
@media (prefers-color-scheme: light) { .btn-public:hover { color: #222; } }
"""

# ---------------------------------------------------------------------------
# Page shells
# ---------------------------------------------------------------------------

def shell(*content_children, title="Tasks", stream_url="/tasks/stream", user=None):
    return Html({"lang": "en"},
        Head(
            Meta({"charset": "UTF-8"}),
            Meta({"name": "viewport", "content": "width=device-width, initial-scale=1.0"}),
            Title(title),
            Script({"type": "module", "src": "https://cdn.jsdelivr.net/gh/starfederation/datastar@1.0.0-RC.8/bundles/datastar.js"}),
            Style(CSS),
            Link({"rel": "icon", "type": "image/svg+xml", "href": "/favicon.svg"})),
        Body(
            Div({"class": "page",
                 "data-init": f"@get('{stream_url}', {{openWhenHidden: true}})"},
                Div({"class": "app-header"},
                    Span({"class": "app-logo"}, Safe(LOGO_SVG), "Timer"),
                    Div({"class": "header-actions"},
                        A({"href": "/logout", "class": "auth-link"}, "Sign out") if user else A({"href": "/oauth/google", "class": "auth-link"}, "Sign in"),
                        A({"href": "https://github.com/Deufel/clock", "target": "_blank", "class": "gh-link", "aria-label": "Source code"}, Safe(GH_SVG)))),
                P({"class": "meta", "id": "meta"}),
                Div({"class": "content"}, *content_children))))

def tasks_view(sid, user=None):
    return shell(
        Div({"class": "controls", "style": "width:100%"},
            Span({"class": "task-input", "contenteditable": "true", "data-ignore-morph": True,
                  "role": "textbox", "aria-label": "New task name",
                  "data-on:keydown": "if(event.key==='Enter'){event.preventDefault(); let n=el.innerText.trim(); if(n){@post('/tasks/add?name='+encodeURIComponent(n))} el.innerText=''}"}),
            Button({"data-on:click": "var inp=el.closest('.controls').querySelector('[contenteditable]'); var n=inp.innerText.trim(); if(n){@post('/tasks/add?name='+encodeURIComponent(n))} inp.innerText=''"}, "Add")),
        Div({"id": "task-list", "style": "width:100%",
             "data-on:click": "const btn = evt.target.closest('[data-url]'); if (btn) @post(btn.dataset.url)"}),
        Div({"id": "rate-toggle"}, rate_toggle(get_tasks_rate(sid))),
        user=user)

def landing_page():
    return Html({"lang": "en"},
        Head(
            Meta({"charset": "UTF-8"}),
            Meta({"name": "viewport", "content": "width=device-width, initial-scale=1.0"}),
            Title("Timer"),
            Style(CSS + LANDING_CSS)),
        Body(
            Div({"class": "landing"},
                Span({"class": "landing-logo"}, Safe(LOGO_SVG), "Timer"),
                P({"class": "landing-subtitle"}, "Track your time, simply."),
                Div({"class": "landing-actions"},
                    A({"href": "/oauth/google", "class": "btn-google"}, Safe(GOOGLE_ICON), "Sign in with Google"),
                    A({"href": "/tasks", "class": "btn-public"}, "Use without an account")))))

# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

def admin_page(stats):
    rows = [
        ("Users", stats["users"]),
        ("Sessions (total)", stats["sessions"]),
        ("Sessions (authenticated)", stats["sessions_authed"]),
        ("Sessions (anonymous)", stats["sessions_anon"]),
        ("Tasks (total)", stats["tasks_total"]),
        ("Tasks (active)", stats["tasks_active"]),
        ("Tasks (completed)", stats["tasks_done"]),
        ("Tasks (tracking now)", stats["tasks_tracking"]),
        ("Total time tracked", fmt_duration(stats["total_elapsed"])),
    ]
    return Html({"lang": "en"},
        Head(
            Meta({"charset": "UTF-8"}),
            Meta({"name": "viewport", "content": "width=device-width, initial-scale=1.0"}),
            Title("Admin — Timer"),
            Style(CSS + """
.admin { max-width: 500px; margin: 2rem auto; padding: 1rem; }
.admin h1 { font-size: 1.25rem; margin-bottom: 1rem; }
.stat-table { width: 100%; border-collapse: collapse; }
.stat-table td { padding: 0.5rem 0; border-bottom: 1px solid #222; }
.stat-table td:last-child { text-align: right; font-family: 'JetBrains Mono', monospace; font-size: 0.9rem; }
@media (prefers-color-scheme: light) { .stat-table td { border-color: #ddd; } }
""")),
        Body(
            Div({"class": "admin"},
                H1("Admin"),
                Table({"class": "stat-table"},
                    *[Tr(Td(label), Td(str(value))) for label, value in rows]),
                P({"style": "margin-top:1.5rem; text-align:center"},
                    A({"href": "/tasks"}, "← Back to tasks")))))

# ---------------------------------------------------------------------------
# SSE update helper
# ---------------------------------------------------------------------------

def _yield_update(sid, tasks=None):
    if tasks is None: tasks = get_tasks(sid)
    meta_text, title_text = make_meta(sid, tasks)
    return [
        patch_elements(task_panel(tasks), mode="inner", selector="#task-list"),
        patch_elements(to_html(P({"class": "meta", "id": "meta"}, meta_text))),
        patch_elements(to_html(Title(title_text))),
    ]

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def home(req):
    if req["user"]:
        return ("/tasks", 302)
    return landing_page()


@app.get("/tasks")
async def tasks(req):
    return tasks_view(req["sid"], req["user"])


@app.get("/tasks/stream")
async def tasks_stream(req):
    sid = req["sid"]
    for ev in _yield_update(sid): yield ev
    tick = asyncio.create_task(_tasks_ticker(sid))
    try:
        async for _, _ in relay.subscribe(f"tasks.{sid}.*"):
            for ev in _yield_update(sid): yield ev
    finally:
        tick.cancel()


@app.post("/tasks/rate")
async def tasks_rate(req):
    sid = req["sid"]
    key = req["query"].get("r", "1s")
    rate = RATE_MAP.get(key, 1.0)
    set_json(sid, "tasks_rate", rate)
    relay.publish(f"tasks.{sid}.rate", None)
    relay.publish(f"tasks.{sid}.update", None)
    yield patch_elements(to_html(rate_toggle(rate)), mode="inner", selector="#rate-toggle")


@app.post("/tasks/add")
async def task_add(req):
    name = req["query"].get("name", "").strip()
    if name: cmd_task_add(req["sid"], name)
    return None


@app.post("/tasks/track")
async def task_track(req):
    cmd_task_track(req["sid"], int(req["query"].get("id", "0")))
    return None


@app.post("/tasks/stop")
async def task_stop(req):
    cmd_task_stop(req["sid"], int(req["query"].get("id", "0")))
    return None


@app.post("/tasks/done")
async def task_done(req):
    cmd_task_done(req["sid"], int(req["query"].get("id", "0")))
    return None


@app.post("/tasks/rename")
async def task_rename(req):
    sid = req["sid"]
    tid = int(req["query"].get("id", "0"))
    name = req["query"].get("name", "").strip()
    if name: cmd_task_rename(sid, tid, name)
    return None


@app.get("/oauth/google")
async def oauth_google(req):
    redirect_uri = f"https://{req['headers'].get('host', 'localhost')}/oauth/callback"
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "email profile",
        "access_type": "offline",
        "prompt": "consent",
    }
    return (f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}", 302)


@app.get("/oauth/callback")
async def oauth_callback(req):
    code = req["query"].get("code")
    if not code:
        return ("/?error=oauth_failed", 302)
    redirect_uri = f"https://{req['headers'].get('host', 'localhost')}/oauth/callback"
    token_resp = httpx.post("https://oauth2.googleapis.com/token", data={
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }, timeout=10.0)
    if token_resp.status_code != 200:
        return ("/?error=token_failed", 302)
    token_data = token_resp.json()
    user_resp = httpx.get("https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
        timeout=10.0)
    if user_resp.status_code != 200:
        return ("/?error=userinfo_failed", 302)
    info = user_resp.json()
    email = info["email"].lower()
    name = info.get("name", email.split("@")[0])
    google_id = info["sub"]
    sid = req["sid"]
    user, sid = find_or_create_user_and_link(sid, email, name, google_id)
    _write_sid(req, sid)
    return ("/tasks", 302)


@app.get("/logout")
async def logout(req):
    set_cookie(req, "sid", "", httponly=True, secure=True, samesite="Lax", path="/", max_age=0)
    return ("/", 302)


@app.get("/admin")
async def admin(req):
    user = req["user"]
    if not user or user["email"] != ADMIN_EMAIL:
        return ("/", 302)
    return admin_page(admin_stats())


@app.get("/health")
async def health(req):
    return "ok"


# ---------------------------------------------------------------------------
# Embedded server
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    serve(app, host="0.0.0.0", port=8000)
