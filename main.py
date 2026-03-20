import asyncio, time, math
from datetime import datetime
from zoneinfo import ZoneInfo
from stario import Stario, Context, Writer, data
from stario.relay import Relay
from html_tags import setup_tags
from stario.html import SafeString
setup_tags()
from db import (new_session, valid_session, get_json, set_json,
                add_task, get_tasks, get_task, task_start_tracking,
                task_stop_tracking, task_complete, task_elapsed, stop_all_tracking,
                rename_task)

TZ = ZoneInfo("America/Chicago")
relay = Relay()
FONT = "Inter, system-ui, sans-serif"
MONO = "'JetBrains Mono', 'SF Mono', monospace"

def get_sid(c, w):
    sid = c.req.cookies.get("sid", "")
    if valid_session(sid): return sid
    sid = new_session()
    w.cookie("sid", sid, httponly=True, samesite="Lax", path="/")
    return sid

def get_clock_rate(sid): return get_json(sid, "clock_rate", lambda: 1.0)
def get_tasks_rate(sid): return get_json(sid, "tasks_rate", lambda: 1.0)

def lerp(a, b, t): return a + (b - a) * t
def to12(h): return (h % 12 or 12, "AM" if h < 12 else "PM")

def time_bg(h, m):
    t = h + m / 60.0
    keys = [(0,8,0,0), (6,25,0.08,50), (12,30,0.08,85), (18,18,0.08,260), (24,8,0,0)]
    for i in range(len(keys) - 1):
        h0,l0,c0,hu0 = keys[i]; h1,l1,c1,hu1 = keys[i+1]
        if t <= h1:
            f = (t - h0) / (h1 - h0) if h1 != h0 else 0
            return f"oklch({lerp(l0,l1,f):.1f}% {lerp(c0,c1,f):.3f} {lerp(hu0,hu1,f):.0f})"
    return "oklch(8% 0 0)"

def fmt_elapsed(secs):
    if secs >= 3600:
        hh, rem = divmod(secs, 3600)
        return f"{hh}h {rem//60:02d}m {rem%60:02d}s"
    mm, ss = divmod(secs, 60)
    return f"{mm:02d}:{ss:02d}"

def make_svg(hour, minute, second=0, tracking=False, frac=None, sz=16):
    h = sz / 2
    r, circ = sz * 0.4, 2 * math.pi * sz * 0.4
    bg = "oklch(10% 0.03 160)" if tracking else time_bg(hour, minute)
    h12 = hour if tracking else to12(hour)[0]
    fs = sz * 0.69 if h12 < 10 else sz * 0.53
    if frac is None: frac = second / 60.0 if tracking else minute / 60.0
    filled = circ * frac
    accent = "#2a2" if tracking else "#e54"
    sw_w, rx = sz * 0.094, sz * 0.188
    track = f"<circle cx='{h}' cy='{h}' r='{r:.1f}' fill='none' stroke='#fff' stroke-width='{sw_w:.1f}' stroke-opacity='0.08'/>"
    ring = f"<circle cx='{h}' cy='{h}' r='{r:.1f}' fill='none' stroke='{accent}' stroke-width='{sw_w:.1f}' stroke-linecap='butt' stroke-dasharray='{filled:.2f} {circ:.2f}' transform='rotate(-90 {h} {h})'/>" if frac > 0 else ""
    txt = f"<text x='{h}' y='{h+sz*0.03}' text-anchor='middle' dominant-baseline='central' font-size='{fs:.1f}' font-family=\"{FONT}\" font-weight='700' fill='#fff'>{h12}</text>"
    sec_hand = ""
    if not tracking:
        sec_r = r * 0.85
        sec_hand = (f"<g style='animation: spin 60s linear infinite; animation-delay: -{second:.2f}s; transform-origin: {h}px {h}px'>"
                    f"<line x1='{h}' y1='{h}' x2='{h}' y2='{h - sec_r:.2f}' stroke='{accent}' stroke-width='0.15' opacity='0.6'/>"
                    f"<circle cx='{h}' cy='{h - sec_r:.2f}' r='0.25' fill='{accent}' opacity='0.8'/></g>")
    style = "<style>@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }</style>" if not tracking else ""
    return f"<svg viewBox='0 0 {sz} {sz}' xmlns='http://www.w3.org/2000/svg'>{style}<defs><clipPath id='c'><rect width='{sz}' height='{sz}' rx='{rx:.1f}'/></clipPath></defs><g clip-path='url(#c)'><rect width='{sz}' height='{sz}' fill='{bg}'/></g>{track}{ring}{sec_hand}{txt}</svg>"

def clock_sigs():
    now = datetime.now(TZ)
    h12, ampm = to12(now.hour)
    svg = make_svg(now.hour, now.minute, now.second + now.microsecond / 1e6)
    return dict(favSvg=svg, favMeta=f"{h12}:{now.minute:02d}:{now.second:02d} {ampm}")

def tasks_sigs(sid):
    tasks = get_tasks(sid)
    active = [t for t in tasks if t["track_start"] is not None]
    if active:
        t = active[0]
        e = task_elapsed(t)
        mm, ss = divmod(e, 60)
        return dict(favSvg=make_svg(mm, ss, ss, tracking=True), favMeta=f"tracking · {t['name']} · {fmt_elapsed(e)}")
    return dict(favSvg=clock_sigs()["favSvg"], favMeta=f"{len(tasks)} task{'s' if len(tasks) != 1 else ''} · idle")

def task_row(t):
    "Render one task as a list item"
    tid = t["id"]
    elapsed = fmt_elapsed(task_elapsed(t))
    tracking = t["track_start"] is not None
    toggle_url = f"/tasks/stop?id={tid}" if tracking else f"/tasks/track?id={tid}"
    toggle_label = "Stop" if tracking else "Track"
    toggle_cls = "task-btn on" if tracking else "task-btn"
    return Li({"class": "task-row"},
        Span({"class": "task-name", "contenteditable": "true", "data-original": t["name"],
              "data-on:blur": f"@post('/tasks/rename?id={tid}&name=' + encodeURIComponent(el.innerText.trim()))",
              "data-on:keydown": "if(event.key==='Enter'){event.preventDefault();el.blur()} if(event.key==='Escape'){el.innerText=el.dataset.original;el.blur()}"}, t["name"]),
        Span({"class": "task-time"}, elapsed),
        Button({"class": toggle_cls, "data-url": toggle_url}, toggle_label),
        Button({"class": "task-btn", "data-url": f"/tasks/done?id={tid}"}, "✓"))

def task_list(tasks):
    "Render all tasks as an unordered list"
    if not tasks: return P({"class": "task-empty"}, "no tasks yet")
    return Ul({"class": "task-list"}, *[task_row(t) for t in tasks])

def task_bar(tasks):
    "Render a proportional time bar with legend"
    total = sum(task_elapsed(t) for t in tasks)
    if total == 0: return Span()
    colors = ["#e54", "#2a2", "#47f", "#f90", "#c4f", "#0cc", "#fa0", "#f47"]
    bars, legend = [], []
    for i, t in enumerate(tasks):
        e = task_elapsed(t)
        if e <= 0: continue
        pct, color = e / total * 100, colors[i % len(colors)]
        bars.append(Div({"class": "bar-seg", "style": f"width:{pct:.1f}%;background:{color}", "title": f"{t['name']}: {fmt_elapsed(e)}"}))
        legend.append(Span({"class": "bar-legend-item"}, Span({"style": f"color:{color}"}, "●"), f" {t['name']} {fmt_elapsed(e)}"))
    return Div({"class": "task-bar"}, Div({"class": "bar-track"}, *bars), Div({"class": "bar-legend"}, *legend))

def task_panel(tasks):
    "Full tasks content: list + bar chart"
    return Div(task_list(tasks), task_bar(tasks))

def cmd_task_add(sid, name):
    add_task(sid, name.strip())
    relay.publish(f"tasks.{sid}", None)

def cmd_task_track(sid, tid):
    stop_all_tracking(sid)
    task_start_tracking(tid)
    relay.publish(f"tasks.{sid}", None)

def cmd_task_stop(sid, tid):
    task_stop_tracking(tid)
    relay.publish(f"tasks.{sid}", None)

def cmd_task_done(sid, tid):
    task_complete(tid)
    relay.publish(f"tasks.{sid}", None)

def cmd_task_rename(sid, tid, name):
    rename_task(tid, name)
    relay.publish(f"tasks.{sid}", None)

async def _clock_loop(w, sid):
    async for _ in w.alive():
        w.sync(clock_sigs())
        await asyncio.sleep(get_clock_rate(sid))

async def _tasks_loop(w, sid):
    async for _ in w.alive():
        tasks = get_tasks(sid)
        w.patch(SafeString(str(task_panel(tasks))), mode="inner", selector="#task-list")
        w.sync(tasks_sigs(sid))
        await asyncio.sleep(get_tasks_rate(sid))

TASK_CSS = """
.task-btn { padding: 0.4rem 0.8rem; font-size: 0.8rem; min-width: 3.5rem; }
.task-btn.on { background: #e54; border-color: #e54; color: #fff; }
.task-input { padding: 0.6rem; border-radius: 0.5rem; border: 1px solid #333; background: #151515; color: #fff; font: inherit; flex: 1; font-size: 0.95rem; }
@media (prefers-color-scheme: light) { .task-input { background: #fff; color: #222; border-color: #ddd; } }
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
.task-bar { margin-top: 1rem; }
.rate-ctrl { display: flex; align-items: center; gap: 0.75rem; font-size: 0.8rem; color: #888; }
.rate-ctrl input[type=range] { flex: 1; accent-color: #e54; }
.rate-label { font-family: 'JetBrains Mono', monospace; min-width: 5rem; }
"""

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@200;300;400;500;600;700&family=JetBrains+Mono:wght@400;700&display=swap');
*, *::before, *::after { box-sizing: border-box; margin: 0; }
body { font-family: Inter, system-ui, sans-serif; min-height: 100vh; background: #0a0a0a; color: #eee; margin: 0; }
@media (prefers-color-scheme: light) { body { background: #f8f8f8; color: #222; } }
.page { display: grid; grid-template-rows: auto auto 1fr; align-items: start; justify-items: center; min-height: 100vh; padding: 2rem 1rem; gap: 1.5rem; }
nav { display: flex; gap: 2rem; }
nav a { color: #666; text-decoration: none; font-size: 0.8rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; padding: 0.4rem 0; border-bottom: 2px solid transparent; transition: all 0.2s; }
nav a:hover { color: #eee; }
@media (prefers-color-scheme: light) { nav a:hover { color: #222; } }
nav a.on { color: #e54; border-bottom-color: #e54; }
.clock-row { display: flex; flex-direction: column; align-items: center; gap: 0.75rem; }
.face { width: 240px; height: 240px; }
.face svg { width: 100%; height: 100%; }
.meta { text-align: center; color: #555; font-size: 0.8rem; font-family: 'JetBrains Mono', monospace; letter-spacing: 0.1em; text-transform: uppercase; min-height: 1.4em; }
.content { width: min(90vw, 500px); display: flex; flex-direction: column; align-items: center; gap: 1rem; }
.controls { display: flex; gap: 0.75rem; align-items: center; justify-content: center; flex-wrap: wrap; }
button { padding: 0.5rem 1.2rem; border-radius: 0.5rem; border: 1px solid #333; background: #151515; color: #eee; font: inherit; cursor: pointer; font-size: 0.85rem; transition: all 0.15s; }
@media (prefers-color-scheme: light) { button { background: #fff; color: #222; border-color: #ddd; } }
button:hover { background: #222; border-color: #555; }
@media (prefers-color-scheme: light) { button:hover { background: #eee; } }
button.on { background: #e54; border-color: #e54; color: #fff; }
""" + TASK_CSS

FAVICON_EFFECT = "document.querySelector('#favicon').href = 'data:image/svg+xml,' + encodeURIComponent($favSvg);"

def shell(*content_children, title="Clock", active="clock", sigs=None, stream_url="/clock/stream"):
    if sigs is None: sigs = clock_sigs()
    return Html({"lang": "en"},
        Head(
            Meta({"charset": "UTF-8"}),
            Meta({"name": "viewport", "content": "width=device-width, initial-scale=1.0"}),
            Title(title),
            Script({"type": "module", "src": "https://cdn.jsdelivr.net/gh/starfederation/datastar@1.0.0-RC.8/bundles/datastar.js"}),
            Style(CSS),
            Link({"rel": "icon", "type": "image/svg+xml", "id": "favicon"})),
        Body(data.signals(sigs),
            Div({"class": "page"},
                Nav(A({"href": "/", "class": "on" if active == "clock" else ""}, "Clock"),
                    A({"href": "/tasks", "class": "on" if active == "tasks" else ""}, "Tasks")),
                Div({"class": "clock-row"},
                    Span({"style": "display:none"}, data.effect(FAVICON_EFFECT)),
                    Div({"class": "face"}, data.effect("el.innerHTML = $favSvg"),
                        data.init(f"@get('{stream_url}', {{openWhenHidden: true}})")),
                    P({"class": "meta"}, data.text("$favMeta"))),
                Div({"class": "content"}, *content_children))))

def clock_view(sid):
    sigs = clock_sigs()
    sigs["clockRate"] = int(get_clock_rate(sid) * 1000)
    return shell(
        Div({"class": "rate-ctrl"},
            Label({"for": "rate"}, "Update rate: "),
            Input({"type": "range", "id": "rate", "min": "16", "max": "2000", "step": "1",
                   "data-bind": "clockRate", "data-on:input": "@post('/clock/rate')"}),
            Span({"class": "rate-label"}, data.text("$clockRate + 'ms'"))),
        active="clock", title="Clock", sigs=sigs, stream_url="/clock/stream")

def tasks_view(sid):
    sigs = tasks_sigs(sid)
    sigs["taskName"] = ""
    sigs["tasksRate"] = int(get_tasks_rate(sid) * 1000)
    return shell(
        Div({"class": "controls", "style": "width:100%"},
            Input({"class": "task-input", "type": "text", "placeholder": "new task...",
                   "data-bind": "taskName",
                   "data-on:keydown": "if (event.key === 'Enter') @post('/tasks/add')"}),
            Button(data.on("click", "@post('/tasks/add')"), "Add")),
        Div({"id": "task-list", "style": "width:100%",
             "data-on:click": "const btn = evt.target.closest('[data-url]'); if (btn) @post(btn.dataset.url)"}),
        Div({"class": "rate-ctrl"},
            Label({"for": "trate"}, "Update rate: "),
            Input({"type": "range", "id": "trate", "min": "16", "max": "2000", "step": "1",
                   "data-bind": "tasksRate", "data-on:input": "@post('/tasks/rate')"}),
            Span({"class": "rate-label"}, data.text("$tasksRate + 'ms'"))),
        active="tasks", title="Tasks", sigs=sigs, stream_url="/tasks/stream")

async def h_tasks(c: Context, w: Writer):
    sid = get_sid(c, w)
    w.html(SafeString(str(tasks_view(sid))))

async def h_home(c: Context, w: Writer):
    sid = get_sid(c, w)
    w.html(SafeString(str(clock_view(sid))))

async def h_clock_stream(c: Context, w: Writer):
    sid = get_sid(c, w)
    await _clock_loop(w, sid)

async def h_clock_rate(c: Context, w: Writer):
    sid = get_sid(c, w)
    s = await c.signals()
    rate = max(0.016, min(2.0, int(s.get("clockRate", 1000)) / 1000.0))
    set_json(sid, "clock_rate", rate)

async def h_tasks_rate(c: Context, w: Writer):
    sid = get_sid(c, w)
    s = await c.signals()
    rate = max(0.016, min(2.0, int(s.get("tasksRate", 1000)) / 1000.0))
    set_json(sid, "tasks_rate", rate)

async def h_tasks_stream(c: Context, w: Writer):
    sid = get_sid(c, w)
    await _tasks_loop(w, sid)

async def h_task_add(c: Context, w: Writer):
    sid = get_sid(c, w)
    s = await c.signals()
    name = s.get("taskName", "").strip()
    if name:
        cmd_task_add(sid, name)
        w.sync({"taskName": "", **tasks_sigs(sid)})

async def h_task_track(c: Context, w: Writer):
    sid = get_sid(c, w)
    tid = int(c.req.query.get("id", "0"))
    cmd_task_track(sid, tid)
    w.sync(tasks_sigs(sid))

async def h_task_stop(c: Context, w: Writer):
    sid = get_sid(c, w)
    tid = int(c.req.query.get("id", "0"))
    cmd_task_stop(sid, tid)
    w.sync(tasks_sigs(sid))

async def h_task_done(c: Context, w: Writer):
    sid = get_sid(c, w)
    tid = int(c.req.query.get("id", "0"))
    cmd_task_done(sid, tid)
    w.sync(tasks_sigs(sid))

async def h_task_rename(c: Context, w: Writer):
    sid = get_sid(c, w)
    tid = int(c.req.query.get("id", "0"))
    name = c.req.query.get("name", "").strip()
    if name: cmd_task_rename(sid, tid, name)
    w.sync(tasks_sigs(sid))

async def h_health(c: Context, w: Writer): w.text("ok")

async def bootstrap(app: Stario, span: Span):
    app.get("/", h_home)
    app.get("/tasks", h_tasks)
    app.get("/clock/stream", h_clock_stream)
    app.post("/clock/rate", h_clock_rate)
    app.get("/tasks/stream", h_tasks_stream)
    app.post("/tasks/rate", h_tasks_rate)
    app.post("/tasks/add", h_task_add)
    app.post("/tasks/track", h_task_track)
    app.post("/tasks/stop", h_task_stop)
    app.post("/tasks/done", h_task_done)
    app.post("/tasks/rename", h_task_rename)
    app.get("/health", h_health)
