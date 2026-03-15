import asyncio, time, math
from datetime import datetime
from zoneinfo import ZoneInfo
from stario import Stario, Span, Context, Writer, data
from stario.html import (Html, Head, Meta, Title, Script, Style, Link, Body,
                          Div, H1, H3, P, Button, Input, Span as HSpan, Small, A, Nav, Ul, Li, Form)
from stario.relay import Relay
from db import (new_session, valid_session, get_json, set_json,
                add_task, get_tasks, get_task, task_start_tracking,
                task_stop_tracking, task_complete, task_elapsed, stop_all_tracking)

TZ = ZoneInfo("America/Chicago")
relay = Relay()
TIMER_DEFAULT = dict(mins=5, end=0, running=False, paused=False, paused_rem=0)
SW_DEFAULT = dict(start=0, running=False, paused=False, paused_elapsed=0)
FONT = "Inter, system-ui, sans-serif"
MONO = "'JetBrains Mono', 'SF Mono', monospace"

def get_sid(c, w):
    sid = c.req.cookies.get("sid", "")
    if valid_session(sid): return sid
    sid = new_session()
    w.cookie("sid", sid, httponly=True, samesite="Lax", path="/")
    return sid

def get_timer(sid): return get_json(sid, "timer", lambda: dict(TIMER_DEFAULT))
def get_sw(sid): return get_json(sid, "sw", lambda: dict(SW_DEFAULT))

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

def fmt_hms(secs):
    if secs >= 3600:
        hh, rem = divmod(secs, 3600)
        return hh, rem // 60, f"{hh}h {rem//60:02d}m"
    mm, ss = divmod(secs, 60)
    return mm, ss, f"{mm:02d}:{ss:02d}"

def fmt_elapsed(secs):
    if secs >= 3600:
        hh, rem = divmod(secs, 3600)
        return f"{hh}h {rem//60:02d}m {rem%60:02d}s"
    mm, ss = divmod(secs, 60)
    return f"{mm:02d}:{ss:02d}"

def make_svg(hour, minute, second=0, mode="time", frac=None, sz=16):
    h = sz / 2
    r, circ = sz * 0.4, 2 * math.pi * sz * 0.4
    bg = time_bg(hour, minute) if mode == "time" else "oklch(12% 0.02 260)" if mode == "countdown" else "oklch(10% 0.03 160)"
    h12 = to12(hour)[0] if mode == "time" else hour
    fs = sz * 0.69 if h12 < 10 else sz * 0.53
    if frac is None: frac = minute / 60.0 if mode == "time" else second / 60.0
    filled = circ * frac
    accent = "#e54" if mode != "stopwatch" else "#2a2"
    sw_w, rx = sz * 0.094, sz * 0.188
    track = f"<circle cx='{h}' cy='{h}' r='{r:.1f}' fill='none' stroke='#fff' stroke-width='{sw_w:.1f}' stroke-opacity='0.08'/>"
    ring = f"<circle cx='{h}' cy='{h}' r='{r:.1f}' fill='none' stroke='{accent}' stroke-width='{sw_w:.1f}' stroke-linecap='butt' stroke-dasharray='{filled:.2f} {circ:.2f}' transform='rotate(-90 {h} {h})'/>" if frac > 0 else ""
    txt = f"<text x='{h}' y='{h+sz*0.03}' text-anchor='middle' dominant-baseline='central' font-size='{fs:.1f}' font-family=\"{FONT}\" font-weight='700' fill='#fff'>{h12}</text>"
    sec_hand = ""
    if mode == "time":
        sec_deg = (second / 60.0) * 360
        sec_r = r * 0.85
        sec_hand = (f"<g style='animation: spin 60s linear infinite; animation-delay: -{second:.2f}s; transform-origin: {h}px {h}px'>"
                    f"<line x1='{h}' y1='{h}' x2='{h}' y2='{h - sec_r:.2f}' stroke='{accent}' stroke-width='0.15' opacity='0.6'/>"
                    f"<circle cx='{h}' cy='{h - sec_r:.2f}' r='0.25' fill='{accent}' opacity='0.8'/></g>")
    style = "<style>@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }</style>" if mode == "time" else ""
    return f"<svg viewBox='0 0 {sz} {sz}' xmlns='http://www.w3.org/2000/svg'>{style}<defs><clipPath id='c'><rect width='{sz}' height='{sz}' rx='{rx:.1f}'/></clipPath></defs><g clip-path='url(#c)'><rect width='{sz}' height='{sz}' fill='{bg}'/></g>{track}{ring}{sec_hand}{txt}</svg>"


def clock_sigs():
    now = datetime.now(TZ)
    h12, ampm = to12(now.hour)
    svg = make_svg(now.hour, now.minute, now.second + now.microsecond / 1e6)
    return dict(favSvg=svg, favMeta=f"{h12}:{now.minute:02d}:{now.second:02d} {ampm}")

def timer_sigs(sid):
    t = get_timer(sid)
    if t["running"]:
        rem = max(0, int(t["end"] - time.monotonic()))
        top, bot, label = fmt_hms(rem)
        return dict(favSvg=make_svg(top, bot, bot, mode="countdown", frac=bot / 60.0), favMeta=f"{label} remaining")
    if t["paused_rem"] > 0:
        top, bot, label = fmt_hms(t["paused_rem"])
        return dict(favSvg=make_svg(top, bot, bot, mode="countdown", frac=bot / 60.0), favMeta=f"paused · {label}")
    total = t["mins"] * 60
    top, bot, label = fmt_hms(total)
    return dict(favSvg=make_svg(top, bot, 0, mode="countdown"), favMeta=f"{label} · ready")

def sw_sigs(sid):
    s = get_sw(sid)
    if s["running"]:
        elapsed = int(time.monotonic() - s["start"])
        mm, ss = divmod(elapsed, 60)
        return dict(favSvg=make_svg(mm, ss, ss, mode="stopwatch"), favMeta=f"{mm:02d}:{ss:02d} elapsed")
    if s["paused_elapsed"] > 0:
        mm, ss = divmod(int(s["paused_elapsed"]), 60)
        return dict(favSvg=make_svg(mm, ss, ss, mode="stopwatch"), favMeta=f"paused · {mm:02d}:{ss:02d}")
    return dict(favSvg=make_svg(0, 0, 0, mode="stopwatch"), favMeta="00:00 · ready")

def tasks_sigs(sid):
    tasks = get_tasks(sid)
    active = [t for t in tasks if t["track_start"] is not None]
    if active:
        t = active[0]
        e = task_elapsed(t)
        mm, ss = divmod(e, 60)
        svg = make_svg(mm, ss, ss, mode="stopwatch")
        return dict(favSvg=svg, favMeta=f"tracking · {t['name']} · {fmt_elapsed(e)}", taskHtml=tasks_html(tasks))
    return dict(favSvg=clock_sigs()["favSvg"], favMeta=f"{len(tasks)} task{'s' if len(tasks) != 1 else ''} · idle", taskHtml=tasks_html(tasks))

def bar_chart_html(tasks):
    if not tasks: return ""
    total = sum(task_elapsed(t) for t in tasks)
    if total == 0: return ""
    colors = ["#e54", "#2a2", "#47f", "#f90", "#c4f", "#0cc", "#fa0", "#f47"]
    bars = []
    for i, t in enumerate(tasks):
        e = task_elapsed(t)
        pct = e / total * 100
        if pct < 1: continue
        c, n = colors[i % len(colors)], t["name"]
        bars.append(f"<div style='width:{pct:.1f}%; background:{c}; height:100%; display:inline-block' title='{n}: {fmt_elapsed(e)}'></div>")
    items = []
    for i, t in enumerate(tasks):
        e = task_elapsed(t)
        if e <= 0: continue
        c, n = colors[i % len(colors)], t["name"]
        items.append(f"<span style='font-size:0.75rem; color:#888'><span style='color:{c}'>●</span> {n} {fmt_elapsed(e)}</span>")
    legend = " ".join(items)
    return (f"<div style='margin-top:1rem'>"
            f"<div style='width:100%; height:1.2rem; border-radius:0.4rem; overflow:hidden; background:#1a1a1a; display:flex'>{''.join(bars)}</div>"
            f"<div style='margin-top:0.4rem; display:flex; flex-wrap:wrap; gap:0.6rem; justify-content:center'>{legend}</div>"
            f"</div>")

def tasks_html(tasks):
    if not tasks: return "<p style='color:#555; text-align:center'>no tasks yet</p>"
    rows = []
    for t in tasks:
        e = fmt_elapsed(task_elapsed(t))
        tracking = t["track_start"] is not None
        tid = t["id"]
        toggle = f"@post('/tasks/stop?id={tid}')" if tracking else f"@post('/tasks/track?id={tid}')"
        btn_label, btn_cls = ("Stop", " on") if tracking else ("Track", "")
        rows.append(
            f"<li style='display:flex; align-items:center; gap:0.75rem; padding:0.5rem 0; border-bottom:1px solid #222'>"
            f"<span style='flex:1; font-size:1rem'>{t['name']}</span>"
            f"<span style='color:#888; font-size:0.85rem; font-family:{MONO}; min-width:6rem; text-align:right'>{e}</span>"
            f"<button class='task-btn{btn_cls}' data-on:click=\"{toggle}\">{btn_label}</button>"
            f"<button class='task-btn' data-on:click=\"@post('/tasks/done?id={tid}')\">✓</button>"
            f"</li>")
    return f"<ul style='list-style:none; padding:0; width:100%'>{''.join(rows)}</ul>{bar_chart_html(tasks)}"


def cmd_timer_start(sid):
    t = get_timer(sid)
    total = t["paused_rem"] if t["paused_rem"] > 0 else t["mins"] * 60
    t.update(dict(end=time.monotonic() + total, running=True, paused=False, paused_rem=0))
    set_json(sid, "timer", t)
    relay.publish(f"timer.{sid}", None)

def cmd_timer_pause(sid):
    t = get_timer(sid)
    if not t["running"]: return
    t.update(dict(paused_rem=max(0, int(t["end"] - time.monotonic())), running=False, paused=True))
    set_json(sid, "timer", t)
    relay.publish(f"timer.{sid}", None)

def cmd_timer_reset(sid):
    t = get_timer(sid)
    t.update(dict(end=0, running=False, paused=False, paused_rem=0))
    set_json(sid, "timer", t)
    relay.publish(f"timer.{sid}", None)

def cmd_timer_duration(sid, h, m):
    t = get_timer(sid)
    t.update(dict(mins=h * 60 + m, paused_rem=0, running=False, paused=False, end=0))
    set_json(sid, "timer", t)
    relay.publish(f"timer.{sid}", None)

def cmd_sw_start(sid):
    s = get_sw(sid)
    s.update(dict(start=time.monotonic() - s["paused_elapsed"], running=True, paused=False, paused_elapsed=0))
    set_json(sid, "sw", s)
    relay.publish(f"sw.{sid}", None)

def cmd_sw_pause(sid):
    s = get_sw(sid)
    if not s["running"]: return
    s.update(dict(paused_elapsed=time.monotonic() - s["start"], running=False, paused=True))
    set_json(sid, "sw", s)
    relay.publish(f"sw.{sid}", None)

def cmd_sw_reset(sid):
    s = get_sw(sid)
    s.update(dict(start=0, running=False, paused=False, paused_elapsed=0))
    set_json(sid, "sw", s)
    relay.publish(f"sw.{sid}", None)

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


async def _clock_loop(w):
    async for _ in w.alive():
        w.sync(clock_sigs())
        await asyncio.sleep(1)

async def _timer_loop(w, sid):
    async for _ in w.alive():
        t = get_timer(sid)
        if t["running"] and t["end"] - time.monotonic() <= 0:
            t.update(dict(running=False))
            set_json(sid, "timer", t)
            relay.publish(f"timer.{sid}", None)
        w.sync(timer_sigs(sid))
        await asyncio.sleep(1)

async def _sw_loop(w, sid):
    async for _ in w.alive():
        w.sync(sw_sigs(sid))
        await asyncio.sleep(1)

async def _tasks_loop(w, sid):
    async for _ in w.alive():
        w.sync(tasks_sigs(sid))
        await asyncio.sleep(1)


TASK_CSS = """
.task-btn { padding: 0.4rem 0.8rem; font-size: 0.8rem; min-width: 3.5rem; }
.task-btn.on { background: #e54; border-color: #e54; color: #fff; }
.task-input { padding: 0.6rem; border-radius: 0.5rem; border: 1px solid #333; background: #151515; color: #fff; font: inherit; flex: 1; font-size: 0.95rem; }
@media (prefers-color-scheme: light) { .task-input { background: #fff; color: #222; border-color: #ddd; } }
.task-input:focus { outline: 2px solid #e54; outline-offset: 2px; }
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
input[type=number] { padding: 0.5rem; border-radius: 0.5rem; border: 1px solid #333; background: #151515; color: #fff; font: inherit; width: 4.5rem; text-align: center; font-size: 1rem; }
@media (prefers-color-scheme: light) { input[type=number] { background: #fff; color: #222; border-color: #ddd; } }
input:focus { outline: 2px solid #e54; outline-offset: 2px; }
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
                    A({"href": "/timer", "class": "on" if active == "timer" else ""}, "Timer"),
                    A({"href": "/stopwatch", "class": "on" if active == "stopwatch" else ""}, "Stopwatch"),
                    A({"href": "/tasks", "class": "on" if active == "tasks" else ""}, "Tasks")),
                Div({"class": "clock-row"},
                    HSpan({"style": "display:none"}, data.effect(FAVICON_EFFECT)),
                    Div({"class": "face"}, data.effect("el.innerHTML = $favSvg"),
                        data.init(f"@get('{stream_url}', {{openWhenHidden: true}})")),
                    P({"class": "meta"}, data.text("$favMeta"))),
                Div({"class": "content"}, *content_children))))

def clock_view(): return shell(active="clock", title="Clock", sigs=clock_sigs(), stream_url="/clock/stream")

def timer_view(sid):
    t = get_timer(sid)
    hrs, mins = t["mins"] // 60, t["mins"] % 60
    return shell(
        Div({"class": "controls"},
            Input({"type": "number", "min": "0", "max": "99", "value": str(hrs), "id": "timer-hrs", "style": "width:4rem",
                   "data-on:change": "@post('/timer/duration?h=' + el.value + '&m=' + document.querySelector('#timer-mins').value)"}),
            Small({"style": "color:#666"}, "hr"),
            Input({"type": "number", "min": "0", "max": "59", "value": str(mins), "id": "timer-mins", "style": "width:4rem",
                   "data-on:change": "@post('/timer/duration?h=' + document.querySelector('#timer-hrs').value + '&m=' + el.value)"}),
            Small({"style": "color:#666"}, "min"),
            Button(data.on("click", "@post('/timer/start')"), "Start"),
            Button(data.on("click", "@post('/timer/pause')"), "Pause"),
            Button(data.on("click", "@post('/timer/reset')"), "Reset")),
        active="timer", title="Timer", sigs=timer_sigs(sid), stream_url="/timer/stream")

def sw_view(sid):
    return shell(
        Div({"class": "controls"},
            Button(data.on("click", "@post('/stopwatch/start')"), "Start"),
            Button(data.on("click", "@post('/stopwatch/pause')"), "Pause"),
            Button(data.on("click", "@post('/stopwatch/reset')"), "Reset")),
        active="stopwatch", title="Stopwatch", sigs=sw_sigs(sid), stream_url="/stopwatch/stream")

def tasks_view(sid):
    sigs = tasks_sigs(sid)
    sigs["taskName"] = ""
    return shell(
        Div({"class": "controls", "style": "width:100%"},
            Input({"class": "task-input", "type": "text", "placeholder": "new task...",
                   "data-bind": "taskName",
                   "data-on:keydown": "if (event.key === 'Enter') @post('/tasks/add')"}),
            Button(data.on("click", "@post('/tasks/add')"), "Add")),
        Div({"id": "task-list", "style": "width:100%"}, data.effect("el.innerHTML = $taskHtml")),
        active="tasks", title="Tasks", sigs=sigs, stream_url="/tasks/stream")


async def h_home(c: Context, w: Writer):
    get_sid(c, w)
    w.html(clock_view())

async def h_timer(c: Context, w: Writer):
    sid = get_sid(c, w)
    w.html(timer_view(sid))

async def h_sw(c: Context, w: Writer):
    sid = get_sid(c, w)
    w.html(sw_view(sid))

async def h_tasks(c: Context, w: Writer):
    sid = get_sid(c, w)
    w.html(tasks_view(sid))

async def h_clock_stream(c: Context, w: Writer): await _clock_loop(w)

async def h_timer_stream(c: Context, w: Writer):
    sid = get_sid(c, w)
    await _timer_loop(w, sid)

async def h_sw_stream(c: Context, w: Writer):
    sid = get_sid(c, w)
    await _sw_loop(w, sid)

async def h_tasks_stream(c: Context, w: Writer):
    sid = get_sid(c, w)
    await _tasks_loop(w, sid)

async def h_timer_start(c: Context, w: Writer):
    sid = get_sid(c, w)
    cmd_timer_start(sid)
    w.sync(timer_sigs(sid))

async def h_timer_pause(c: Context, w: Writer):
    sid = get_sid(c, w)
    cmd_timer_pause(sid)
    w.sync(timer_sigs(sid))

async def h_timer_reset(c: Context, w: Writer):
    sid = get_sid(c, w)
    cmd_timer_reset(sid)
    w.sync(timer_sigs(sid))

async def h_timer_duration(c: Context, w: Writer):
    sid = get_sid(c, w)
    h = max(0, min(99, int(c.req.query.get("h", "0"))))
    m = max(0, min(59, int(c.req.query.get("m", "0"))))
    cmd_timer_duration(sid, h, m)
    w.sync(timer_sigs(sid))

async def h_sw_start(c: Context, w: Writer):
    sid = get_sid(c, w)
    cmd_sw_start(sid)
    w.sync(sw_sigs(sid))

async def h_sw_pause(c: Context, w: Writer):
    sid = get_sid(c, w)
    cmd_sw_pause(sid)
    w.sync(sw_sigs(sid))

async def h_sw_reset(c: Context, w: Writer):
    sid = get_sid(c, w)
    cmd_sw_reset(sid)
    w.sync(sw_sigs(sid))

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

async def h_health(c: Context, w: Writer): w.text("ok")

async def bootstrap(app: Stario, span: Span):
    app.get("/", h_home)
    app.get("/timer", h_timer)
    app.get("/stopwatch", h_sw)
    app.get("/tasks", h_tasks)
    app.get("/clock/stream", h_clock_stream)
    app.get("/timer/stream", h_timer_stream)
    app.get("/stopwatch/stream", h_sw_stream)
    app.get("/tasks/stream", h_tasks_stream)
    app.post("/timer/start", h_timer_start)
    app.post("/timer/pause", h_timer_pause)
    app.post("/timer/reset", h_timer_reset)
    app.post("/timer/duration", h_timer_duration)
    app.post("/stopwatch/start", h_sw_start)
    app.post("/stopwatch/pause", h_sw_pause)
    app.post("/stopwatch/reset", h_sw_reset)
    app.post("/tasks/add", h_task_add)
    app.post("/tasks/track", h_task_track)
    app.post("/tasks/stop", h_task_stop)
    app.post("/tasks/done", h_task_done)
    app.get("/health", h_health)
