
import asyncio, time, math
from datetime import datetime
from stario import Stario, Span, Context, Writer, data
from stario.html import (Html, Head, Meta, Title, Script, Style, Link, Body,
                          Div, H1, P, Button, Input, Span as HSpan, Small, A, Nav)

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

state = dict(countdownMins=5, timerPaused=False, timerPausedRemaining=0)

R, CIRC = 206, 2 * math.pi * 206

def lerp(a, b, t): return a + (b - a) * t

def time_bg(hour, minute):
    t = hour + minute / 60.0
    keys = [(0,8,0,0), (6,25,0.08,50), (12,30,0.08,85), (18,18,0.08,260), (24,8,0,0)]
    for i in range(len(keys) - 1):
        h0,l0,c0,hu0 = keys[i]; h1,l1,c1,hu1 = keys[i+1]
        if t <= h1:
            f = (t-h0)/(h1-h0) if h1!=h0 else 0
            return f"oklch({lerp(l0,l1,f):.1f}% {lerp(c0,c1,f):.3f} {lerp(hu0,hu1,f):.0f})"
    return "oklch(8% 0 0)"

def to12(hour):
    ampm = "AM" if hour < 12 else "PM"
    h = hour % 12
    if h == 0: h = 12
    return h, ampm

def make_svg(hour, minute, second=0, mode="time", frac_override=None, size=16, font="'Courier New',monospace"):
    s,h = size, size/2
    r = s*0.4
    circ = 2*math.pi*r
    bg = time_bg(hour, minute) if mode == "time" else "oklch(12% 0.02 260)" if mode == "countdown" else "oklch(10% 0.03 160)"
    h12, _ = to12(hour) if mode == "time" else (hour, "")
    h_str = str(h12)
    fs = s*0.69 if h12 < 10 else s*0.53
    h_txt = f"<text x='{h}' y='{h+s*0.03}' text-anchor='middle' dominant-baseline='central' font-size='{fs:.1f}' font-family=\"{font}\" font-weight='700' fill='#fff'>{h_str}</text>"
    frac = frac_override if frac_override is not None else (minute/60.0 if mode == "time" else second/60.0)
    filled = circ * frac
    accent = "#e54" if mode != "stopwatch" else "#2a2"
    sw = s*0.094
    rx = s*0.188
    ring_track = f"<circle cx='{h}' cy='{h}' r='{r:.1f}' fill='none' stroke='#fff' stroke-width='{sw:.1f}' stroke-opacity='0.08'/>"
    ring_fill = f"<circle cx='{h}' cy='{h}' r='{r:.1f}' fill='none' stroke='{accent}' stroke-width='{sw:.1f}' stroke-linecap='butt' stroke-dasharray='{filled:.2f} {circ:.2f}' transform='rotate(-90 {h} {h})'/>" if frac > 0 else ""
    return f"<svg viewBox='0 0 {s} {s}' xmlns='http://www.w3.org/2000/svg'><defs><clipPath id='c'><rect width='{s}' height='{s}' rx='{rx:.1f}'/></clipPath></defs><g clip-path='url(#c)'><rect width='{s}' height='{s}' fill='{bg}'/></g>{ring_track}{ring_fill}{h_txt}</svg>"

FAVICON_EFFECT = """document.querySelector('#favicon').href = 'data:image/svg+xml,' + encodeURIComponent($favSvg);"""

def shell(*children, title="Favicon Clock", active="clock", init_svg="", init_meta="\u00a0"):
    return Html({"lang": "en"},
        Head(
            Meta({"charset": "UTF-8"}),
            Meta({"name": "viewport", "content": "width=device-width, initial-scale=1.0"}),
            Title(title),
            Script({"type": "module", "src": "https://cdn.jsdelivr.net/gh/starfederation/datastar@1.0.0-RC.8/bundles/datastar.js"}),
            Style(CSS),
            Link({"rel": "icon", "type": "image/svg+xml", "id": "favicon"})),
        Body(data.signals({"favSvg": init_svg, "favMeta": init_meta}),
            HSpan({"style": "display:none"}, data.effect(FAVICON_EFFECT)),
            Nav(A({"href": "/", "class": "on" if active == "clock" else ""}, "Clock"),
                A({"href": "/timer", "class": "on" if active == "timer" else ""}, "Timer"),
                A({"href": "/stopwatch", "class": "on" if active == "stopwatch" else ""}, "Stopwatch")),
            *children))

def fmt_timer(remaining):
    if remaining >= 3600:
        hh, rem = divmod(remaining, 3600)
        mm = rem // 60
        return hh, mm, f"{hh}h {mm:02d}m remaining"
    mm, ss = divmod(remaining, 60)
    return mm, ss, f"{mm:02d}:{ss:02d} remaining"

def clock_page():
    now = datetime.now()
    svg = make_svg(now.hour, now.minute)
    h12, ampm = to12(now.hour)
    return shell(
        Div({"class": "face"}, data.effect("el.innerHTML = $favSvg")),
        P({"class": "meta"}, data.text("$favMeta")),
        Div({"class": "controls"}, Button(data.on("click", "@get('/clock/start', {openWhenHidden: true})"), "Start")),
        active="clock", title="Clock", init_svg=svg, init_meta=f"{h12}:{now.minute:02d} {ampm}")


def timer_page():
    total = state["countdownMins"] * 60
    top, bot, meta = fmt_timer(total)
    svg = make_svg(top, bot, 0, mode="countdown")
    hrs, mins = state["countdownMins"] // 60, state["countdownMins"] % 60
    return shell(
        Div({"class": "face"}, data.effect("el.innerHTML = $favSvg")),
        P({"class": "meta"}, data.text("$favMeta")),
        Div({"class": "controls"},
            Input({"type": "number", "min": "0", "max": "99", "value": str(hrs), "id": "timer-hrs", "style": "width:4rem", "data-on:change": "@post('/timer/duration?h=' + el.value + '&m=' + document.querySelector('#timer-mins').value)"}),
            Small({"style": "color:#666"}, "hr"),
            Input({"type": "number", "min": "0", "max": "59", "value": str(mins), "id": "timer-mins", "style": "width:4rem", "data-on:change": "@post('/timer/duration?h=' + document.querySelector('#timer-hrs').value + '&m=' + el.value)"}),
            Small({"style": "color:#666"}, "min"),
            Button(data.on("click", "@get('/timer/start', {openWhenHidden: true})"), "Start"),
            Button(data.on("click", "@post('/timer/pause')"), "Pause"),
            Button(data.on("click", "@post('/timer/reset')"), "Reset")),
        active="timer", title="Timer", init_svg=svg, init_meta=meta.replace("remaining", "· ready"))

def stopwatch_page():
    svg = make_svg(0, 0, 0, mode="stopwatch")
    return shell(
        Div({"class": "face"}, data.effect("el.innerHTML = $favSvg")),
        P({"class": "meta"}, data.text("$favMeta")),
        Div({"class": "controls"},
            Button(data.on("click", "@get('/stopwatch/start', {openWhenHidden: true})"), "Start"),
            Button(data.on("click", "@post('/stopwatch/pause')"), "Pause"),
            Button(data.on("click", "@post('/stopwatch/reset')"), "Reset")),
        active="stopwatch", title="Stopwatch", init_svg=svg, init_meta="00:00 · ready")

async def timer_duration(c: Context, w: Writer):
    h = max(0, min(99, int(c.req.query.get("h", "0"))))
    m = max(0, min(59, int(c.req.query.get("m", "0"))))
    state["countdownMins"] = h * 60 + m
    state["timerPausedRemaining"] = 0
    total = state["countdownMins"] * 60
    top, bot, meta = fmt_timer(total)
    w.sync({"favSvg": make_svg(top, bot, 0, mode="countdown"), "favMeta": meta.replace("remaining", "· ready")})

async def timer_start(c: Context, w: Writer):
    state["timerPaused"] = False
    if state["timerPausedRemaining"] > 0: total = state["timerPausedRemaining"]
    else: total = state["countdownMins"] * 60
    end = time.monotonic() + total
    state["timerPausedRemaining"] = 0
    async for _ in w.alive():
        if state["timerPaused"]:
            state["timerPausedRemaining"] = max(0, int(end - time.monotonic()))
            top, bot, meta = fmt_timer(state["timerPausedRemaining"])
            frac = bot / 60.0
            w.sync({"favSvg": make_svg(top, bot, bot, mode="countdown", frac_override=frac), "favMeta": f"paused · {meta.split(' ')[0]}"})
            return
        remaining = max(0, int(end - time.monotonic()))
        top, bot, meta = fmt_timer(remaining)
        frac = bot / 60.0
        w.sync({"favSvg": make_svg(top, bot, bot, mode="countdown", frac_override=frac), "favMeta": meta})
        if remaining <= 0:
            w.sync({"favMeta": "done"})
            break
        await asyncio.sleep(1)

async def timer_reset(c: Context, w: Writer):
    state["timerPaused"] = False
    state["timerPausedRemaining"] = 0
    total = state["countdownMins"] * 60
    top, bot, meta = fmt_timer(total)
    w.sync({"favSvg": make_svg(top, bot, 0, mode="countdown"), "favMeta": meta.replace("remaining", "· ready")})

async def home(c: Context, w: Writer): w.html(clock_page())
async def timer_home(c: Context, w: Writer): w.html(timer_page())
async def stopwatch_home(c: Context, w: Writer): w.html(stopwatch_page())

async def clock_start(c: Context, w: Writer):
    last_min = -1
    now = datetime.now()
    svg = make_svg(now.hour, now.minute)
    h12, ampm = to12(now.hour)
    w.sync({"favSvg": svg, "favMeta": f"live \u00b7 {h12}:{now.minute:02d} {ampm}"})
    async for _ in w.alive():
        now = datetime.now()
        if now.minute != last_min:
            last_min = now.minute
            h12, ampm = to12(now.hour)
            w.sync({"favSvg": make_svg(now.hour, now.minute), "favMeta": f"live \u00b7 {h12}:{now.minute:02d} {ampm}"})
        await asyncio.sleep(1)

async def timer_start(c: Context, w: Writer):
    state["timerPaused"] = False
    if state["timerPausedRemaining"] > 0: total = state["timerPausedRemaining"]
    else: total = state["countdownMins"] * 60
    end = time.monotonic() + total
    state["timerPausedRemaining"] = 0
    async for _ in w.alive():
        if state["timerPaused"]:
            state["timerPausedRemaining"] = max(0, int(end - time.monotonic()))
            mm, ss = divmod(state["timerPausedRemaining"], 60)
            w.sync({"favSvg": make_svg(mm, ss, ss, mode="countdown"), "favMeta": f"paused \u00b7 {mm:02d}:{ss:02d}"})
            return
        remaining = max(0, int(end - time.monotonic()))
        mm, ss = divmod(remaining, 60)
        w.sync({"favSvg": make_svg(mm, ss, ss, mode="countdown"), "favMeta": f"{mm:02d}:{ss:02d} remaining"})
        if remaining <= 0:
            w.sync({"favMeta": "done"})
            break
        await asyncio.sleep(1)

async def timer_pause(c: Context, w: Writer):
    state["timerPaused"] = True
    w.sync({})

async def timer_duration(c: Context, w: Writer):
    state["countdownMins"] = max(1, min(99, int(c.req.query.get("m", "5"))))
    state["timerPausedRemaining"] = 0
    w.sync({"favSvg": make_svg(state["countdownMins"], 0, 0, mode="countdown"), "favMeta": f"{state['countdownMins']}:00 \u00b7 ready"})

async def timer_reset(c: Context, w: Writer):
    state["timerPaused"] = False
    state["timerPausedRemaining"] = 0
    w.sync({"favSvg": make_svg(state["countdownMins"], 0, 0, mode="countdown"), "favMeta": f"{state['countdownMins']}:00 \u00b7 ready"})

async def stopwatch_start(c: Context, w: Writer):
    state["swPaused"] = False
    offset = state.get("swPausedElapsed", 0)
    start = time.monotonic() - offset
    state["swPausedElapsed"] = 0
    async for _ in w.alive():
        if state.get("swPaused"):
            state["swPausedElapsed"] = time.monotonic() - start
            elapsed = int(state["swPausedElapsed"])
            mm, ss = divmod(elapsed, 60)
            w.sync({"favSvg": make_svg(mm, ss, ss, mode="stopwatch"), "favMeta": f"paused \u00b7 {mm:02d}:{ss:02d}"})
            return
        elapsed = int(time.monotonic() - start)
        mm, ss = divmod(elapsed, 60)
        w.sync({"favSvg": make_svg(mm, ss, ss, mode="stopwatch"), "favMeta": f"{mm:02d}:{ss:02d} elapsed"})
        await asyncio.sleep(1)

async def stopwatch_pause(c: Context, w: Writer):
    state["swPaused"] = True
    w.sync({})

async def stopwatch_reset(c: Context, w: Writer):
    state["swPaused"] = False
    state["swPausedElapsed"] = 0
    w.sync({"favSvg": make_svg(0, 0, 0, mode="stopwatch"), "favMeta": "00:00 \u00b7 ready"})

async def bootstrap(app: Stario, span: Span):
    app.get("/", home)
    app.get("/timer", timer_home)
    app.get("/stopwatch", stopwatch_home)
    app.get("/clock/start", clock_start)
    app.get("/timer/start", timer_start)
    app.post("/timer/pause", timer_pause)
    app.post("/timer/duration", timer_duration)
    app.post("/timer/reset", timer_reset)
    app.get("/stopwatch/start", stopwatch_start)
    app.post("/stopwatch/pause", stopwatch_pause)
    app.post("/stopwatch/reset", stopwatch_reset)
