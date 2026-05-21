// Package handlers wires HTTP routes to the state and auth packages.
package handlers

import (
	"context"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/Deufel/clock-go/internal/auth"
	"github.com/Deufel/clock-go/internal/state"
	"github.com/Deufel/clock-go/web"

	"github.com/a-h/templ"
	datastar "github.com/starfederation/datastar-go/datastar"
)

// tickInterval is the SSE re-render cadence while at least one task is
// actively tracking. ~60fps.
const tickInterval = 16 * time.Millisecond

type Server struct {
	DB         *state.DB
	Hub        *state.Hub
	Signer     *auth.Signer
	Google     auth.GoogleConfig
	AdminEmail string
	PublicURL  string // e.g. "https://clock.deufel.dev" (no trailing slash). Empty = infer from request.
}

// Routes returns a mux with every route wired up.
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /", s.handleIndex)
	mux.HandleFunc("GET /tasks", s.handleTasks)
	mux.HandleFunc("GET /tasks/stream", s.handleStream)
	mux.HandleFunc("POST /tasks/add", s.handleAdd)
	mux.HandleFunc("POST /tasks/track", s.handleTrack)
	mux.HandleFunc("POST /tasks/stop", s.handleStop)
	mux.HandleFunc("POST /tasks/done", s.handleDone)
	mux.HandleFunc("POST /tasks/rename", s.handleRename)

	mux.HandleFunc("GET /oauth/google", s.handleOAuthStart)
	mux.HandleFunc("GET /oauth/callback", s.handleOAuthCallback)
	mux.HandleFunc("GET /logout", s.handleLogout)

	mux.HandleFunc("GET /admin", s.handleAdmin)
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("GET /favicon.svg", s.handleFavicon)

	return mux
}

// ---- Session middleware ----------------------------------------------------

// session returns the effective session id, creating one if needed.
func (s *Server) session(w http.ResponseWriter, r *http.Request) (string, error) {
	if raw := s.Signer.ReadSignedCookie(r, "sid"); raw != "" && s.DB.ValidSession(raw) {
		return raw, nil
	}
	sid, err := s.DB.NewSession()
	if err != nil {
		return "", err
	}
	s.Signer.SetSignedCookie(w, "sid", sid, 60*60*24*365)
	return sid, nil
}

func (s *Server) sessionUser(sid string) *state.User {
	u, err := s.DB.GetSessionUser(sid)
	if err != nil {
		log.Printf("sessionUser: %v", err)
		return nil
	}
	return u
}

// publicURL returns the base URL for building OAuth redirect_uris.
func (s *Server) publicURL(r *http.Request) string {
	if s.PublicURL != "" {
		return s.PublicURL
	}
	scheme := "https"
	if r.TLS == nil && r.Header.Get("X-Forwarded-Proto") == "" {
		scheme = "http"
	}
	return scheme + "://" + r.Host
}

// anyTracking reports whether this session has at least one task actively
// being tracked.
func (s *Server) anyTracking(sid string) bool {
	tasks, err := s.DB.GetTasks(sid, false)
	if err != nil {
		return false
	}
	for _, t := range tasks {
		if t.Tracking() {
			return true
		}
	}
	return false
}

// ---- Handlers --------------------------------------------------------------

func (s *Server) handleIndex(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	sid, err := s.session(w, r)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	user := s.sessionUser(sid)
	if user != nil {
		http.Redirect(w, r, "/tasks", http.StatusFound)
		return
	}
	flash := ""
	switch r.URL.Query().Get("reason") {
	case "revoked":
		flash = "You've been signed out."
	}
	templ.Handler(web.Landing(flash)).ServeHTTP(w, r)
}

func (s *Server) handleTasks(w http.ResponseWriter, r *http.Request) {
	sid, err := s.session(w, r)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	user := s.sessionUser(sid)
	tasks, err := s.DB.GetTasks(sid, false)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	content := web.TasksContent(sid, tasks)
	templ.Handler(web.Shell("Timer", "/tasks/stream", user, content)).ServeHTTP(w, r)
}

// handleStream is the long-lived SSE connection.
//
// Two sources of re-render:
//  1. State changes (add/track/stop/done/rename) publish "tasks.{sid}.update"
//     via the hub. The subscriber wakes and re-renders.
//  2. A per-session ticker emits ticks while at least one task is tracking.
//     Each tick re-renders so elapsed time advances smoothly on-screen.
//
// The ticker is dormant when nothing is being tracked — a session with no
// active tracking costs zero CPU between state changes.
func (s *Server) handleStream(w http.ResponseWriter, r *http.Request) {
	sid, err := s.session(w, r)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}

	sse := datastar.NewSSE(w, r)

	updates, unsub := s.Hub.Subscribe("tasks." + sid + ".")
	defer unsub()

	// Separate subscription for session-level events (revocation).
	sessionEvents, unsubSession := s.Hub.Subscribe("session." + sid + ".")
	defer unsubSession()

	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	go s.runTicker(ctx, sid)

	if err := s.pushUpdate(sse, sid); err != nil {
		return
	}

	// Slow keepalive heartbeat. caddy-docker-proxy can hold a connection
	// open from the Go server's perspective after the browser has gone
	// away. When the ticker is dormant (nothing tracking), no writes
	// happen, and the dead connection never surfaces — leaving leaked
	// subscribers + a leaked goroutine. Writing every 10s forces a write
	// failure within ~10s of a real disconnect, so the handler returns
	// and cleanup runs.
	heartbeat := time.NewTicker(10 * time.Second)
	defer heartbeat.Stop()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-heartbeat.C:
			// Re-render rather than write a raw SSE comment so we go
			// through the Datastar SDK's writer path. The morph is a
			// no-op when state is unchanged.
			if err := s.pushUpdate(sse, sid); err != nil {
				return
			}
		case _, ok := <-updates:
			if !ok {
				return
			}
			// Coalesce: drain any other pending events before rendering, so
			// a burst of ticks becomes one render. The view is a pure
			// function of state — rendering N times in a row would produce
			// identical-or-near-identical output. One render at the end
			// captures the latest state correctly.
			drained := false
			for !drained {
				select {
				case <-updates:
					// discard
				default:
					drained = true
				}
			}
			if err := s.pushUpdate(sse, sid); err != nil {
				return
			}
		case ev, ok := <-sessionEvents:
			if !ok {
				return
			}
			// We only publish "session.{sid}.revoked" today; if more events
			// are added later, switch on the topic name.
			_ = ev
			// Tell the browser to navigate to /logout, which clears its own
			// device cookie and redirects to /. The SSE stream then ends
			// naturally when the browser navigates away.
			if err := sse.Redirect("/logout?reason=revoked"); err != nil {
				return
			}
			return
		}
	}
}

// pushUpdate sends one fat morph of the live region.
func (s *Server) pushUpdate(sse *datastar.ServerSentEventGenerator, sid string) error {
	tasks, err := s.DB.GetTasks(sid, false)
	if err != nil {
		log.Printf("pushUpdate get tasks: %v", err)
		return err
	}
	html, err := renderToString(web.LiveRegion(tasks))
	if err != nil {
		return err
	}
	return sse.PatchElements(html)
}

// renderToString runs a templ.Component to a string, suitable for passing to
// Datastar's PatchElements / RemoveElement APIs which take raw HTML.
func renderToString(c templ.Component) (string, error) {
	var buf strings.Builder
	if err := c.Render(context.Background(), &buf); err != nil {
		return "", err
	}
	return buf.String(), nil
}

// runTicker emits "tick" events at tickInterval while anyTracking(sid) is
// true. When nothing is tracking, it sleeps on the hub and wakes only when
// the session's state changes (a "tasks.{sid}.update" event), at which
// point it re-evaluates whether to start ticking again.
func (s *Server) runTicker(ctx context.Context, sid string) {
	wake, unsub := s.Hub.Subscribe("tasks." + sid + ".update")
	defer unsub()

	for {
		if s.anyTracking(sid) {
			select {
			case <-ctx.Done():
				return
			case <-wake:
				// State changed — loop and re-check.
				continue
			case <-time.After(tickInterval):
				s.Hub.Publish("tasks." + sid + ".tick")
			}
		} else {
			select {
			case <-ctx.Done():
				return
			case <-wake:
				// Something changed; re-check whether to start ticking.
				continue
			}
		}
	}
}

// ---- Commands --------------------------------------------------------------

// taskBelongsToSession verifies that the task identified by id is owned by
// sid. Returns true on match. Logs and returns false on any mismatch or DB
// error — callers should respond 204 anyway to avoid leaking ID existence.
func (s *Server) taskBelongsToSession(id int64, sid string) bool {
	if id <= 0 || sid == "" {
		return false
	}
	t, err := s.DB.GetTask(id)
	if err != nil {
		log.Printf("taskBelongsToSession lookup id=%d: %v", id, err)
		return false
	}
	if t == nil {
		return false
	}
	return t.SID == sid
}

func (s *Server) handleAdd(w http.ResponseWriter, r *http.Request) {
	sid, _ := s.session(w, r)
	name := strings.TrimSpace(r.URL.Query().Get("name"))
	if len(name) > 200 {
		name = name[:200]
	}
	if name != "" {
		if _, err := s.DB.AddTask(sid, name); err != nil {
			log.Printf("AddTask: %v", err)
		}
		s.Hub.Publish("tasks." + sid + ".update")
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) parseTaskID(r *http.Request) int64 {
	id, _ := strconv.ParseInt(r.URL.Query().Get("id"), 10, 64)
	return id
}

func (s *Server) handleTrack(w http.ResponseWriter, r *http.Request) {
	sid, _ := s.session(w, r)
	id := s.parseTaskID(r)
	if s.taskBelongsToSession(id, sid) {
		// Multiple tasks can be tracked simultaneously. Each task has its
		// own track_start; the 60fps ticker re-renders all of them at once.
		_ = s.DB.TaskStartTracking(id)
		s.Hub.Publish("tasks." + sid + ".update")
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleStop(w http.ResponseWriter, r *http.Request) {
	sid, _ := s.session(w, r)
	id := s.parseTaskID(r)
	if s.taskBelongsToSession(id, sid) {
		_ = s.DB.TaskStopTracking(id)
		s.Hub.Publish("tasks." + sid + ".update")
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleDone(w http.ResponseWriter, r *http.Request) {
	sid, _ := s.session(w, r)
	id := s.parseTaskID(r)
	if s.taskBelongsToSession(id, sid) {
		_ = s.DB.TaskComplete(id)
		s.Hub.Publish("tasks." + sid + ".update")
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleRename(w http.ResponseWriter, r *http.Request) {
	sid, _ := s.session(w, r)
	id := s.parseTaskID(r)
	name := strings.TrimSpace(r.URL.Query().Get("name"))
	// Enforce a sane upper bound on task name length to prevent abuse.
	if len(name) > 200 {
		name = name[:200]
	}
	if name != "" && s.taskBelongsToSession(id, sid) {
		_ = s.DB.RenameTask(id, name)
		s.Hub.Publish("tasks." + sid + ".update")
	}
	w.WriteHeader(http.StatusNoContent)
}

// ---- OAuth ----------------------------------------------------------------

func (s *Server) handleOAuthStart(w http.ResponseWriter, r *http.Request) {
	if _, err := s.session(w, r); err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	csrf, err := auth.NewRandomState()
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	s.Signer.SetSignedCookie(w, "oauth_state", csrf, 300)
	redir := s.publicURL(r) + "/oauth/callback"
	http.Redirect(w, r, s.Google.AuthorizeURL(redir, csrf), http.StatusFound)
}

func (s *Server) handleOAuthCallback(w http.ResponseWriter, r *http.Request) {
	sid, err := s.session(w, r)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}

	wantState := s.Signer.ReadSignedCookie(r, "oauth_state")
	gotState := r.URL.Query().Get("state")
	if wantState == "" || gotState == "" || wantState != gotState {
		http.Redirect(w, r, "/?error=bad_state", http.StatusFound)
		return
	}
	auth.ClearCookie(w, "oauth_state")

	code := r.URL.Query().Get("code")
	if code == "" {
		http.Redirect(w, r, "/?error=no_code", http.StatusFound)
		return
	}
	redir := s.publicURL(r) + "/oauth/callback"
	info, err := s.Google.Exchange(code, redir)
	if err != nil {
		log.Printf("oauth exchange: %v", err)
		http.Redirect(w, r, "/?error=oauth_failed", http.StatusFound)
		return
	}
	email := strings.ToLower(info.Email)
	name := info.Name
	if name == "" {
		if i := strings.Index(email, "@"); i > 0 {
			name = email[:i]
		}
	}
	_, newSID, err := s.DB.FindOrCreateUserAndLink(sid, email, name, info.Sub)
	if err != nil {
		log.Printf("FindOrCreateUserAndLink: %v", err)
		http.Redirect(w, r, "/?error=user_failed", http.StatusFound)
		return
	}
	if newSID != sid {
		s.Signer.SetSignedCookie(w, "sid", newSID, 60*60*24*365)
	}
	http.Redirect(w, r, "/tasks", http.StatusFound)
}

func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request) {
	// Read the sid before clearing — we need it to broadcast.
	sid := s.Signer.ReadSignedCookie(r, "sid")

	auth.ClearCookie(w, "sid")

	// Broadcast revocation to any other open SSE streams for this session.
	// Streams will see this event and navigate themselves to /logout, which
	// will then clear their own device's cookie. The current device's
	// cookie was already cleared above by ClearCookie.
	if sid != "" {
		s.Hub.Publish("session." + sid + ".revoked")
	}

	// "reason=revoked" lets the / page show a flash if it wants.
	target := "/"
	if r.URL.Query().Get("reason") == "revoked" {
		target = "/?reason=revoked"
	}
	http.Redirect(w, r, target, http.StatusFound)
}

// ---- Admin / health --------------------------------------------------------

func (s *Server) handleAdmin(w http.ResponseWriter, r *http.Request) {
	sid, err := s.session(w, r)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	user := s.sessionUser(sid)
	if user == nil || s.AdminEmail == "" || !strings.EqualFold(user.Email, s.AdminEmail) {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}
	stats, err := s.DB.AdminStats()
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	activeSubs, totalPubs, droppedPubs := s.Hub.Stats()

	rows := []web.StatRow{
		{"Users", strconv.Itoa(stats.Users)},
		{"Sessions (total)", strconv.Itoa(stats.Sessions)},
		{"Sessions (authenticated)", strconv.Itoa(stats.SessionsAuthed)},
		{"Sessions (anonymous)", strconv.Itoa(stats.SessionsAnon)},
		{"Tasks (total)", strconv.Itoa(stats.TasksTotal)},
		{"Tasks (active)", strconv.Itoa(stats.TasksActive)},
		{"Tasks (completed)", strconv.Itoa(stats.TasksDone)},
		{"Tasks (tracking now)", strconv.Itoa(stats.TasksTracking)},
		{"Total time tracked", web.FmtDuration(stats.TotalElapsed)},
		{"Hub subscribers", strconv.FormatInt(activeSubs, 10)},
		{"Hub publishes (total)", strconv.FormatInt(totalPubs, 10)},
		{"Hub publishes (dropped)", strconv.FormatInt(droppedPubs, 10)},
	}
	templ.Handler(web.Admin(rows)).ServeHTTP(w, r)
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/plain")
	_, _ = w.Write([]byte("ok"))
}

func (s *Server) handleFavicon(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "image/svg+xml")
	w.Header().Set("Cache-Control", "public, max-age=86400")
	_, _ = w.Write([]byte(web.FaviconSVG))
}
