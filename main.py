import asyncio, math
from datetime import datetime
from zoneinfo import ZoneInfo
from stario import Stario, Context, Writer, data
from stario.relay import Relay
from html_tags import setup_tags
from stario.html import SafeString
SafeString.__str__ = lambda self: self.safe_str
SafeString.__html__ = lambda self: self.safe_str
setup_tags()
from db import (new_session, valid_session, get_json, set_json,
                add_task, get_tasks, get_task, task_start_tracking,
                task_stop_tracking, task_complete, task_elapsed, stop_all_tracking,
                rename_task)

TZ = ZoneInfo("America/Chicago")
relay = Relay()
MONO = "'JetBrains Mono', 'SF Mono', monospace"
FAV_FONT = "'Berkeley Mono', monospace"

def get_sid(c, w):
    sid = c.req.cookies.get("sid", "")
    if valid_session(sid): return sid
    sid = new_session()
    w.cookie("sid", sid, httponly=True, samesite="Lax", path="/")
    return sid

RATE_OPTIONS = [("live", 0.016), ("1s", 1.0), ("1m", 60.0), ("off", 0)]
RATE_MAP = {k: v for k, v in RATE_OPTIONS}

def get_tasks_rate(sid): return get_json(sid, "tasks_rate", lambda: 1.0)
def get_show_clock(sid): return get_json(sid, "show_clock", lambda: False)

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
        hh, rem = divmod(int(secs), 3600)
        return f"{hh}h {rem//60:02d}m {rem%60:02d}.{int(secs*100)%100:02d}s"
    mm = int(secs) // 60
    ss = secs - mm * 60
    return f"{mm:02d}:{ss:05.2f}"

def make_svg(hour, minute, second=0, tracking=False, frac=None, sz=16):
    h = sz / 2
    r, circ = sz * 0.4, 2 * math.pi * sz * 0.4
    bg = "oklch(10% 0.03 160)" if tracking else time_bg(hour, minute)
    h12 = hour if tracking else to12(hour)[0]
    fs = sz * 0.69 if h12 < 10 else sz * 0.38 if h12 >= 100 else sz * 0.53
    if frac is None: frac = second / 60.0 if tracking else minute / 60.0
    filled = circ * frac
    accent = "#2a2" if tracking else "#e54"
    sw_w, rx = sz * 0.094, sz * 0.188
    track = f"<circle cx='{h}' cy='{h}' r='{r:.1f}' fill='none' stroke='#fff' stroke-width='{sw_w:.1f}' stroke-opacity='0.08'/>"
    ring = f"<circle cx='{h}' cy='{h}' r='{r:.1f}' fill='none' stroke='{accent}' stroke-width='{sw_w:.1f}' stroke-linecap='butt' stroke-dasharray='{filled:.2f} {circ:.2f}' transform='rotate(-90 {h} {h})'/>" if frac > 0 else ""
    txt = f"<text x='{h}' y='{h}' text-anchor='middle' dominant-baseline='central' font-size='{fs:.1f}' font-family=\"{FAV_FONT}\" font-weight='700' fill='#fff'>{h12}</text>"
    sec_hand = ""
    if not tracking:
        sec_deg = (second / 60.0) * 360
        sec_r = r * 0.85
        sx = h + sec_r * math.sin(math.radians(sec_deg))
        sy = h - sec_r * math.cos(math.radians(sec_deg))
        sec_hand = (f"<line x1='{h}' y1='{h}' x2='{sx:.2f}' y2='{sy:.2f}' stroke='{accent}' stroke-width='0.15' opacity='0.6'/>"
                    f"<circle cx='{sx:.2f}' cy='{sy:.2f}' r='0.25' fill='{accent}' opacity='0.8'/>")
    style = ""
    return f"<svg viewBox='0 0 {sz} {sz}' xmlns='http://www.w3.org/2000/svg'>{style}<defs><clipPath id='c'><rect width='{sz}' height='{sz}' rx='{rx:.1f}'/></clipPath></defs><g clip-path='url(#c)'><rect width='{sz}' height='{sz}' fill='{bg}'/></g>{track}{ring}{sec_hand}{txt}</svg>"

def make_tracking_svg(elapsed, sz=16):
    h = sz / 2
    r, circ = sz * 0.4, 2 * math.pi * sz * 0.4
    bg = "oklch(10% 0.03 160)"
    sw_w, rx = sz * 0.094, sz * 0.188
    YEAR = 365.25 * 86400
    total_yrs, total_days = elapsed / YEAR, elapsed / 86400
    total_hrs, total_mins = elapsed / 3600, elapsed / 60
    if total_yrs >= 1:
        num, num_color, ring_color = f"{int(total_yrs):02d}", "#ed0", "#aa9"
        frac = (elapsed % YEAR) / YEAR
    elif total_days >= 1:
        num, num_color, ring_color = f"{int(total_days):02d}", "#b4f", "#a9b"
        frac = (elapsed % 86400) / 86400
    elif total_hrs >= 1:
        num, num_color, ring_color = f"{int(total_hrs):02d}", "#e54", "#a98"
        frac = (elapsed % 3600) / 3600
    elif total_mins >= 1:
        num, num_color, ring_color = f"{int(total_mins):02d}", "#2a2", "#8a9"
        frac = (elapsed % 60) / 60
    else:
        num, num_color, ring_color = f"{int(elapsed % 60):02d}", "#47f", "#89a"
        frac = (elapsed * 100 % 100) / 100
    fs = sz * 0.53
    filled = circ * frac
    track = f"<circle cx='{h}' cy='{h}' r='{r:.1f}' fill='none' stroke='#fff' stroke-width='{sw_w:.1f}' stroke-opacity='0.08'/>"
    ring = f"<circle cx='{h}' cy='{h}' r='{r:.1f}' fill='none' stroke='{ring_color}' stroke-width='{sw_w:.1f}' stroke-linecap='butt' stroke-dasharray='{filled:.2f} {circ:.2f}' transform='rotate(-90 {h} {h})'/>" if frac > 0 else ""
    txt = f"<text x='{h}' y='{h+sz*0.03}' text-anchor='middle' dominant-baseline='central' font-size='{fs:.1f}' font-family=\"'Berkeley Mono', monospace\" font-weight='700' fill='{num_color}'>{num}</text>"
    return f"<svg viewBox='0 0 {sz} {sz}' xmlns='http://www.w3.org/2000/svg'><defs><clipPath id='c'><rect width='{sz}' height='{sz}' rx='{rx:.1f}'/></clipPath></defs><g clip-path='url(#c)'><rect width='{sz}' height='{sz}' fill='{bg}'/></g>{track}{ring}{txt}</svg>"

def make_title(sid):
    now = datetime.now(TZ)
    h12, ampm = to12(now.hour)
    date = now.strftime('%b %-d, %Y')
    time = f"{h12}:{now.minute:02d}{ampm.lower()}"
    tasks = get_tasks(sid)
    active = [t for t in tasks if t["track_start"] is not None]
    if active:
        return f"{date} | {time} | {active[0]['name']}"
    return f"{date} | {time}"

def tasks_sigs(sid):
    tasks = get_tasks(sid)
    active = [t for t in tasks if t["track_start"] is not None]
    if active:
        t = active[0]
        e = task_elapsed(t)
        return dict(favSvg=make_tracking_svg(e), favMeta=f"tracking · {t['name']} · {fmt_elapsed(e)}", title=make_title(sid))
    now = datetime.now(TZ)
    h12, ampm = to12(now.hour)
    return dict(favSvg=make_svg(now.hour, now.minute, now.second + now.microsecond / 1e6),
                favMeta=f"{h12}:{now.minute:02d} {ampm} · {now.strftime('%b %-d')}",
                title=make_title(sid))

def task_row(t):
    "Render one task as a list item"
    tid = t["id"]
    elapsed = fmt_elapsed(task_elapsed(t))
    tracking = t["track_start"] is not None
    toggle_url = f"/tasks/stop?id={tid}" if tracking else f"/tasks/track?id={tid}"
    toggle_label = "Stop" if tracking else "Track"
    toggle_cls = "task-btn on" if tracking else "task-btn"
    return Li({"class": "task-row"},
        Span({"class": "task-name", "contenteditable": "true", "data-ignore-morph": True, "data-original": t["name"],
              "data-on:blur": f"@post('/tasks/rename?id={tid}&name=' + encodeURIComponent(el.innerText.trim()))",
              "data-on:keydown": "if(event.key==='Enter'){event.preventDefault();el.blur()} if(event.key==='Escape'){el.innerText=el.dataset.original;el.blur()}"}, t["name"]),
        Span({"class": "task-time", "id": f"task-time-{tid}"}, elapsed),
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
        bars.append(Div({"class": "bar-seg", "style": f"width:{pct:.1f}%;background:{color}", "title": f"{t['name']}: {round(pct)}%"}))
        legend.append(Span({"class": "bar-legend-item"}, Span({"style": f"color:{color}"}, "●"), f" {t['name']} ", Span({"class": "bar-pct"}, f"{round(pct)}%")))
    return Div({"class": "task-bar"}, Div({"class": "bar-track"}, *bars), Div({"class": "bar-legend"}, *legend))

def clock_display(sid):
    "Render the big clock SVG for the clock area"
    tasks = get_tasks(sid)
    active = [t for t in tasks if t["track_start"] is not None]
    if active:
        t = active[0]
        e = task_elapsed(t)
        svg = make_tracking_svg(e, sz=400)
    else:
        now = datetime.now(TZ)
        svg = make_svg(now.hour, now.minute, now.second + now.microsecond / 1e6, sz=400)
    return Div({"class": "clock-display"}, SafeString(svg))

def task_panel(tasks, sid=None):
    "Full tasks content: bar above list, optional clock below"
    parts = [Div({"id": "task-bar"}, task_bar(tasks)), task_list(tasks)]
    if sid and get_show_clock(sid):
        parts.append(Div({"id": "clock-area"}, clock_display(sid)))
    else:
        parts.append(Div({"id": "clock-area"}))
    return Div(*parts)

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

def safe(t): return SafeString(str(t))

def rate_label(key):
    return {"live": "Live", "1s": "1s", "1m": "1m", "off": "Off"}[key]

def rate_toggle(current_rate):
    "Render the update rate toggle bar"
    current_key = next((k for k, v in RATE_OPTIONS if v == current_rate), "1s")
    buttons = []
    for key, _ in RATE_OPTIONS:
        cls = "active" if key == current_key else ""
        buttons.append(Button({"class": cls, "data-on:click": f"@post('/tasks/rate?r={key}')"},
            rate_label(key)))
    return Div({"class": "toggle-bar"}, *buttons)


async def _tasks_ticker(sid):
    while True:
        rate = get_tasks_rate(sid)
        if rate == 0:
            await asyncio.sleep(0.5)
            continue
        elapsed = 0.0
        while elapsed < rate:
            chunk = min(0.25, rate - elapsed)
            await asyncio.sleep(chunk)
            elapsed += chunk
            new_rate = get_tasks_rate(sid)
            if new_rate != rate:
                break
        relay.publish(f"tasks.{sid}.tick", None)

async def _tasks_loop(w, sid):
    tasks = get_tasks(sid)
    w.patch(safe(task_panel(tasks, sid)), mode="inner", selector="#task-list")
    w.sync(tasks_sigs(sid))
    tick = asyncio.create_task(_tasks_ticker(sid))
    try:
        async for _, _ in w.alive(relay.subscribe(f"tasks.{sid}.*")):
            tasks = get_tasks(sid)
            w.patch(safe(task_panel(tasks, sid)), mode="inner", selector="#task-list")
            w.sync(tasks_sigs(sid))
    finally:
        tick.cancel()

TASK_CSS = """
.task-btn { padding: 0.4rem 0.8rem; font-size: 0.8rem; min-width: 3.5rem; }
.task-btn.on { background: #e54; border-color: #e54; color: #fff; }
.task-input { padding: 0.6rem; border-radius: 0.5rem; border: 1px solid #333; background: #151515; color: #fff; font: inherit; flex: 1; font-size: 0.95rem; }
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
.gh-link { color: #666; transition: color 0.15s; }
.gh-link:hover { color: #eee; }
@media (prefers-color-scheme: light) { .gh-link:hover { color: #222; } }
.meta { text-align: center; color: #555; font-size: 0.8rem; font-family: 'JetBrains Mono', monospace; letter-spacing: 0.1em; text-transform: uppercase; min-height: 1.4em; }
.settings { display: flex; gap: 1.5rem; align-items: center; font-size: 0.75rem; color: #666; flex-wrap: wrap; justify-content: center; }
.setting { display: flex; align-items: center; gap: 0.4rem; cursor: pointer; }
.setting input[type=checkbox] { accent-color: #e54; }
.content { width: min(90vw, 500px); display: flex; flex-direction: column; align-items: center; gap: 1rem; }
.controls { display: flex; gap: 0.75rem; align-items: center; justify-content: center; flex-wrap: wrap; }
button { padding: 0.5rem 1.2rem; border-radius: 0.5rem; border: 1px solid #333; background: #151515; color: #eee; font: inherit; cursor: pointer; font-size: 0.85rem; transition: all 0.15s; }
@media (prefers-color-scheme: light) { button { background: #fff; color: #222; border-color: #ddd; } }
button:hover { background: #222; border-color: #555; }
@media (prefers-color-scheme: light) { button:hover { background: #eee; } }
button.on { background: #e54; border-color: #e54; color: #fff; }
.clock-display { display: flex; justify-content: center; }
.clock-display svg { width: min(400px, 80vw); height: auto; border-radius: 1.5rem; }
""" + TASK_CSS

EFFECTS = "if($_favEnabled) document.querySelector('#favicon').href = 'data:image/svg+xml,' + encodeURIComponent($favSvg); document.title = $title;"

def shell(*content_children, title="Tasks", sigs=None, stream_url="/tasks/stream", show_clock=False):
    if sigs is None: sigs = {}
    sigs["_favEnabled"] = True
    sigs["showClock"] = show_clock
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
                Span({"style": "display:none"}, data.effect(EFFECTS),
                    data.init(f"@get('{stream_url}', {{openWhenHidden: true}})")),
                Div({"class": "app-header"},
                    Span({"class": "app-logo"}, SafeString(LOGO_SVG), "Timer"),
                    A({"href": "https://github.com/Deufel/clock", "target": "_blank", "class": "gh-link", "aria-label": "Source code"}, SafeString(GH_SVG))),
                P({"class": "meta"}, data.text("$favMeta")),
                Div({"class": "content"}, *content_children),
                Div({"class": "settings"},
                    Label({"class": "setting"}, Input({"type": "checkbox", "data-bind": "_favEnabled"}), "Favicon"),
                    Label({"class": "setting"}, Input({"type": "checkbox", "data-bind": "showClock",
                        "data-on:change": "@post('/tasks/show-clock')"}), "Clock")))))

def tasks_view(sid):
    sigs = tasks_sigs(sid)
    show_clock = get_show_clock(sid)
    return shell(
        Div({"class": "controls", "style": "width:100%"},
            Span({"class": "task-input", "contenteditable": "true", "data-ignore-morph": True,
                  "role": "textbox", "aria-label": "New task name",
                  "data-on:keydown": "if(event.key==='Enter'){event.preventDefault(); let n=el.innerText.trim(); if(n){@post('/tasks/add?name='+encodeURIComponent(n))} el.innerText=''}"}),
            Button({"data-on:click": "let el=evt.target.closest('.controls').querySelector('[contenteditable]'); let n=el.innerText.trim(); if(n){@post('/tasks/add?name='+encodeURIComponent(n))} el.innerText=''"}, "Add")),
        Div({"id": "task-list", "style": "width:100%",
             "data-on:click": "const btn = evt.target.closest('[data-url]'); if (btn) @post(btn.dataset.url)"}),
        Div({"id": "rate-toggle"}, rate_toggle(get_tasks_rate(sid))),
        sigs=sigs, show_clock=show_clock)

async def h_tasks(c: Context, w: Writer):
    sid = get_sid(c, w)
    w.html(safe(tasks_view(sid)))

async def h_home(c: Context, w: Writer): w.redirect("/tasks")

async def h_show_clock(c: Context, w: Writer):
    sid = get_sid(c, w)
    s = await c.signals()
    set_json(sid, "show_clock", bool(s.get("showClock", False)))
    relay.publish(f"tasks.{sid}.update", None)

async def h_tasks_rate(c: Context, w: Writer):
    sid = get_sid(c, w)
    key = c.req.query.get("r", "1s")
    rate = RATE_MAP.get(key, 1.0)
    set_json(sid, "tasks_rate", rate)
    w.patch(safe(rate_toggle(rate)), mode="inner", selector="#rate-toggle")
    relay.publish(f"tasks.{sid}.update", None)

async def h_tasks_stream(c: Context, w: Writer):
    sid = get_sid(c, w)
    await _tasks_loop(w, sid)

async def h_task_add(c: Context, w: Writer):
    sid = get_sid(c, w)
    name = c.req.query.get("name", "").strip()
    if name: cmd_task_add(sid, name)

async def h_task_track(c: Context, w: Writer):
    sid = get_sid(c, w)
    cmd_task_track(sid, int(c.req.query.get("id", "0")))

async def h_task_stop(c: Context, w: Writer):
    sid = get_sid(c, w)
    cmd_task_stop(sid, int(c.req.query.get("id", "0")))

async def h_task_done(c: Context, w: Writer):
    sid = get_sid(c, w)
    cmd_task_done(sid, int(c.req.query.get("id", "0")))

async def h_task_rename(c: Context, w: Writer):
    sid = get_sid(c, w)
    tid = int(c.req.query.get("id", "0"))
    name = c.req.query.get("name", "").strip()
    if name: cmd_task_rename(sid, tid, name)

async def h_health(c: Context, w: Writer): w.text("ok")

async def bootstrap(app: Stario, span: Span):
    app.get("/", h_home)
    app.get("/tasks", h_tasks)
    app.get("/tasks/stream", h_tasks_stream)
    app.post("/tasks/rate", h_tasks_rate)
    app.post("/tasks/show-clock", h_show_clock)
    app.post("/tasks/add", h_task_add)
    app.post("/tasks/track", h_task_track)
    app.post("/tasks/stop", h_task_stop)
    app.post("/tasks/done", h_task_done)
    app.post("/tasks/rename", h_task_rename)
    app.get("/health", h_health)
