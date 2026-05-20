package web

import (
	"fmt"
	"time"
)

// Timezone matches the Python version's "America/Chicago".
var ChicagoTZ *time.Location

func init() {
	tz, err := time.LoadLocation("America/Chicago")
	if err != nil {
		tz = time.UTC
	}
	ChicagoTZ = tz
}

// CurrentTimeMeta renders the idle "no tracking" meta line.
// Format: "9:42 AM · Nov 20"
func CurrentTimeMeta() string {
	now := time.Now().In(ChicagoTZ)
	h12, ampm := To12(now.Hour())
	return fmt.Sprintf("%d:%02d %s · %s", h12, now.Minute(), ampm, now.Format("Jan 2"))
}

// CurrentDateTime returns "Nov 20, 2026 | 9:42am" — used as the document
// title prefix in TitleText.
func CurrentDateTime() string {
	now := time.Now().In(ChicagoTZ)
	h12, ampm := To12(now.Hour())
	return fmt.Sprintf("%s | %d:%02d%s",
		now.Format("Jan 2, 2006"),
		h12, now.Minute(),
		toLowerAMPM(ampm),
	)
}

func toLowerAMPM(s string) string {
	if s == "AM" {
		return "am"
	}
	return "pm"
}

// ---------- SVG ----------

const LogoSVG = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round"><path d="M12 11.4V9.1"/><path d="m12 17 6.59-6.59"/><path d="m15.05 5.7-.218-.691a3 3 0 0 0-5.663 0L4.418 19.695A1 1 0 0 0 5.37 21h13.253a1 1 0 0 0 .951-1.31L18.45 16.2"/><circle cx="20" cy="9" r="2"/></svg>`

const GitHubSVG = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round"><path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/></svg>`

const GoogleIconSVG = `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>`

const FaviconSVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 11.4V9.1"/><path d="m12 17 6.59-6.59"/><path d="m15.05 5.7-.218-.691a3 3 0 0 0-5.663 0L4.418 19.695A1 1 0 0 0 5.37 21h13.253a1 1 0 0 0 .951-1.31L18.45 16.2"/><circle cx="20" cy="9" r="2"/></svg>`

// ---------- CSS ----------

func AppCSS() string {
	return `
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
`
}

func LandingCSS() string {
	return `
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
`
}

func AdminCSS() string {
	return `
.admin { max-width: 500px; margin: 2rem auto; padding: 1rem; }
.admin h1 { font-size: 1.25rem; margin-bottom: 1rem; }
.stat-table { width: 100%; border-collapse: collapse; }
.stat-table td { padding: 0.5rem 0; border-bottom: 1px solid #222; }
.stat-table td:last-child { text-align: right; font-family: 'JetBrains Mono', monospace; font-size: 0.9rem; }
@media (prefers-color-scheme: light) { .stat-table td { border-color: #ddd; } }
`
}
