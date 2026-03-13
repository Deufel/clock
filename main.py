import asyncio, time, math
from datetime import datetime
from zoneinfo import ZoneInfo
from stario import Stario, Span, Context, Writer, data
from stario.html import Html, Head, Meta, Title, Script, Style, Link, Body, Div, P, Button, Input, Span as HSpan, Small, A, Nav
from stario.relay import Relay

TZ = ZoneInfo("America/Chicago")
relay = Relay()
timer = dict(mins=5, end=0, running=False, paused=False, paused_rem=0)
sw = dict(start=0, running=False, paused=False, paused_elapsed=0)


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

def make_svg(hour, minute, second=0, mode="time", frac=None, sz=16, font="'Courier New',monospace"):
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
    txt = f"<text x='{h}' y='{h+sz*0.03}' text-anchor='middle' dominant-baseline='central' font-size='{fs:.1f}' font-family=\"{font}\" font-weight='700' fill='#fff'>{h12}</text>"
    return f"<svg viewBox='0 0 {sz} {sz}' xmlns='http://www.w3.org/2000/svg'><defs><clipPath id='c'><rect width='{sz}' height='{sz}' rx='{rx:.1f}'/></clipPath></defs><g clip-path='url(#c)'><rect width='{sz}' height='{sz}' fill='{bg}'/></g>{track}{ring}{txt}</svg>"


def clock_sigs():
    now = datetime.now(TZ)
    h12, ampm = to12(now.hour)
    return dict(favSvg=make_svg(now.hour, now.minute), favMeta=f"{h12}:{now.minute:02d} {ampm}")

def timer_sigs():
    if timer["running"]:
        rem = max(0, int(timer["end"] - time.monotonic()))
        top, bot, label = fmt_hms(rem)
        return dict(favSvg=make_svg(top, bot, bot, mode="countdown", frac=bot / 60.0), favMeta=f"{label} remaining")
    if timer["paused_rem"] > 0:
        top, bot, label = fmt_hms(timer["paused_rem"])
        return dict(favSvg=make_svg(top, bot, bot, mode="countdown", frac=bot / 60.0), favMeta=f"paused · {label}")
    total = timer["mins"] * 60
    top, bot, label = fmt_hms(total)
    return dict(favSvg=make_svg(top, bot, 0, mode="countdown"), favMeta=f"{label} · ready")

def sw_sigs():
    if sw["running"]:
        elapsed = int(time.monotonic() - sw["start"])
        mm, ss = divmod(elapsed, 60)
        return dict(favSvg=make_svg(mm, ss, ss, mode="stopwatch"), favMeta=f"{mm:02d}:{ss:02d} elapsed")
    if sw["paused_elapsed"] > 0:
        mm, ss = divmod(int(sw["paused_elapsed"]), 60)
        return dict(favSvg=make_svg(mm, ss, ss, mode="stopwatch"), favMeta=f"paused · {mm:02d}:{ss:02d}")
    return dict(favSvg=make_svg(0, 0, 0, mode="stopwatch"), favMeta="00:00 · ready")


def cmd_timer_start():
    total = timer["paused_rem"] if timer["paused_rem"] > 0 else timer["mins"] * 60
    timer.update(dict(end=time.monotonic() + total, running=True, paused=False, paused_rem=0))
    relay.publish("timer.changed", None)

def cmd_timer_pause():
    if not timer["running"]: return
    timer.update(dict(paused_rem=max(0, int(timer["end"] - time.monotonic())), running=False, paused=True))
    relay.publish("timer.changed", None)

def cmd_timer_reset():
    timer.update(dict(end=0, running=False, paused=False, paused_rem=0))
    relay.publish("timer.changed", None)

def cmd_timer_duration(h, m):
    timer.update(dict(mins=h * 60 + m, paused_rem=0, running=False, paused=False, end=0))
    relay.publish("timer.changed", None)

def cmd_sw_start():
    sw.update(dict(start=time.monotonic() - sw["paused_elapsed"], running=True, paused=False, paused_elapsed=0))
    relay.publish("sw.changed", None)

def cmd_sw_pause():
    if not sw["running"]: return
    sw.update(dict(paused_elapsed=time.monotonic() - sw["start"], running=False, paused=True))
    relay.publish("sw.changed", None)

def cmd_sw_reset():
    sw.update(dict(start=0, running=False, paused=False, paused_elapsed=0))
    relay.publish("sw.changed", None)


async def _clock_loop(w):
    last_min = -1
    async for _ in w.alive():
        now = datetime.now(TZ)
        if now.minute != last_min:
            last_min = now.minute
            w.sync(clock_sigs())
        await asyncio.sleep(1)

async def _timer_loop(w):
    async for _ in w.alive():
        if timer["running"] and timer["end"] - time.monotonic() <= 0:
            timer.update(dict(running=False))
            relay.publish("timer.changed", None)
        w.sync(timer_sigs())
        await asyncio.sleep(1)

async def _sw_loop(w):
    async for _ in w.alive():
        w.sync(sw_sigs())
        await asyncio.sleep(1)


CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; }
body { font-family: system-ui, sans-serif; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; background: #0a0a0a; color: #eee; padding: 2rem; gap: 2rem; }
@media (prefers-color-scheme: light) { body { background: #f8f8f8; color: #222; } }
nav { display: flex; gap: 2rem; }
nav a { color: #666; text-decoration: none; font-size: 0.9rem; font-weight: 500; letter-spacing: 0.05em; text-transform: uppercase; padding: 0.4rem 0; border-bottom: 2px solid transparent; transition: all 0.2s; }
nav a:hover { color: #eee; }
@media (prefers-color-scheme: light) { nav a:hover { color: #222; } }
nav a.on { color: #e54; border-bottom-color: #e54; }
.face { width: min(80vw, 420px); aspect-ratio: 1; }
.face svg { width: 100%; height: 100%; }
.meta { text-align: center; color: #555; font-size: 0.85rem; letter-spacing: 0.1em; text-transform: uppercase; min-height: 1.4em; }
.controls { display: flex; gap: 0.75rem; align-items: center; justify-content: center; min-height: 3rem; }
button { padding: 0.6rem 1.4rem; border-radius: 0.5rem; border: 1px solid #333; background: #151515; color: #eee; font: inherit; cursor: pointer; font-size: 0.9rem; transition: all 0.15s; }
@media (prefers-color-scheme: light) { button { background: #fff; color: #222; border-color: #ddd; } }
button:hover { background: #222; border-color: #555; }
@media (prefers-color-scheme: light) { button:hover { background: #eee; } }
button.on { background: #e54; border-color: #e54; color: #fff; }
input[type=number] { padding: 0.6rem; border-radius: 0.5rem; border: 1px solid #333; background: #151515; color: #fff; font: inherit; width: 5rem; text-align: center; font-size: 1.1rem; }
@media (prefers-color-scheme: light) { input[type=number] { background: #fff; color: #222; border-color: #ddd; } }
input:focus { outline: 2px solid #e54; outline-offset: 2px; }
"""

FAVICON_EFFECT = "document.querySelector('#favicon').href = 'data:image/svg+xml,' + encodeURIComponent($favSvg);"

def shell(*children, title="Clock", active="clock", sigs=None):
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
            HSpan({"style": "display:none"}, data.effect(FAVICON_EFFECT)),
            Nav(A({"href": "/", "class": "on" if active == "clock" else ""}, "Clock"),
                A({"href": "/timer", "class": "on" if active == "timer" else ""}, "Timer"),
                A({"href": "/stopwatch", "class": "on" if active == "stopwatch" else ""}, "Stopwatch")),
            *children))

def clock_view():
    return shell(
        Div({"class": "face"}, data.effect("el.innerHTML = $favSvg"),
            data.init("@get('/clock/stream', {openWhenHidden: true})")),
        P({"class": "meta"}, data.text("$favMeta")),
        active="clock", title="Clock", sigs=clock_sigs())

def timer_view():
    hrs, mins = timer["mins"] // 60, timer["mins"] % 60
    return shell(
        Div({"class": "face"}, data.effect("el.innerHTML = $favSvg"),
            data.init("@get('/timer/stream', {openWhenHidden: true})")),
        P({"class": "meta"}, data.text("$favMeta")),
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
        active="timer", title="Timer", sigs=timer_sigs())

def sw_view():
    return shell(
        Div({"class": "face"}, data.effect("el.innerHTML = $favSvg"),
            data.init("@get('/stopwatch/stream', {openWhenHidden: true})")),
        P({"class": "meta"}, data.text("$favMeta")),
        Div({"class": "controls"},
            Button(data.on("click", "@post('/stopwatch/start')"), "Start"),
            Button(data.on("click", "@post('/stopwatch/pause')"), "Pause"),
            Button(data.on("click", "@post('/stopwatch/reset')"), "Reset")),
        active="stopwatch", title="Stopwatch", sigs=sw_sigs())


async def h_home(c: Context, w: Writer): w.html(clock_view())
async def h_timer(c: Context, w: Writer): w.html(timer_view())
async def h_sw(c: Context, w: Writer): w.html(sw_view())
async def h_clock_stream(c: Context, w: Writer): await _clock_loop(w)
async def h_timer_stream(c: Context, w: Writer): await _timer_loop(w)
async def h_sw_stream(c: Context, w: Writer): await _sw_loop(w)

async def h_timer_start(c: Context, w: Writer):
    cmd_timer_start()
    w.sync(timer_sigs())

async def h_timer_pause(c: Context, w: Writer):
    cmd_timer_pause()
    w.sync(timer_sigs())

async def h_timer_reset(c: Context, w: Writer):
    cmd_timer_reset()
    w.sync(timer_sigs())

async def h_timer_duration(c: Context, w: Writer):
    h = max(0, min(99, int(c.req.query.get("h", "0"))))
    m = max(0, min(59, int(c.req.query.get("m", "0"))))
    cmd_timer_duration(h, m)
    w.sync(timer_sigs())

async def h_sw_start(c: Context, w: Writer):
    cmd_sw_start()
    w.sync(sw_sigs())

async def h_sw_pause(c: Context, w: Writer):
    cmd_sw_pause()
    w.sync(sw_sigs())

async def h_sw_reset(c: Context, w: Writer):
    cmd_sw_reset()
    w.sync(sw_sigs())

async def h_health(c: Context, w: Writer): w.text("ok")

async def bootstrap(app: Stario, span: Span):
    app.get("/", h_home)
    app.get("/timer", h_timer)
    app.get("/stopwatch", h_sw)
    app.get("/clock/stream", h_clock_stream)
    app.get("/timer/stream", h_timer_stream)
    app.get("/stopwatch/stream", h_sw_stream)
    app.post("/timer/start", h_timer_start)
    app.post("/timer/pause", h_timer_pause)
    app.post("/timer/reset", h_timer_reset)
    app.post("/timer/duration", h_timer_duration)
    app.post("/stopwatch/start", h_sw_start)
    app.post("/stopwatch/pause", h_sw_pause)
    app.post("/stopwatch/reset", h_sw_reset)
    app.get("/health", h_health)
