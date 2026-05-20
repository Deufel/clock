// Package handlers wires HTTP routes to the state package and auth package.
package handlers

import (
	"context"
	"encoding/json"
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
	mux.HandleFunc("POST /tasks/rate", s.handleRate)

	mux.HandleFunc("GET /oauth/google", s.handleOAuthStart)
	mux.HandleFunc("GET /oauth/callback", s.handleOAuthCallback)
	mux.HandleFunc("GET /logout", s.handleLogout)

	mux.HandleFunc("GET /admin", s.handleAdmin)
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("GET /favicon.svg", s.handleFavicon)

	return mux
}

// ---- Session middleware (called explicitly per handler) --------------------

// session returns the effective session id, creating one if needed. The
// cookie is written back on creation.
func (s *Server) session(w http.ResponseWriter, r *http.Request) (string, error) {
	if raw := s.Signer.ReadSignedCookie(r, "sid"); raw != "" && s.DB.ValidSession(raw) {
		return raw, nil
	}
	sid, err := s.DB.NewSession()
	if err != nil {
		return "", err
	}
	s.Signer.SetSignedCookie(w, "sid", sid, 60*60*24*365) // 1 year
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

// ---- Helpers ---------------------------------------------------------------

// rateKey looks up the JSON-encoded rate key for a session; default is "1s".
func (s *Server) rateKey(sid string) string {
	if v, ok := s.DB.GetJSON(sid, "tasks_rate"); ok {
		var k string
		if err := json.Unmarshal([]byte(v), &k); err == nil && k != "" {
			return k
		}
	}
	return "1s"
}

// rateSeconds maps a rate key to a duration. 0 = ticker paused.
func rateSeconds(k string) float64 {
	switch k {
	case "live":
		return 0.016
	case "1s":
		return 1.0
	case "1m":
		return 60.0
	default:
		return 0
	}
}

// publicURL returns the base URL for building OAuth redirect_uris. Prefers
// the configured value; falls back to inferring from request.
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
	templ.Handler(web.Landing()).ServeHTTP(w, r)
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
	content := web.TasksContent(sid, tasks, s.rateKey(sid))
	templ.Handler(web.Shell("Timer", "/tasks/stream", user, content)).ServeHTTP(w, r)
}

// handleStream is the long-lived SSE connection. Sends one fat morph + a
// title-update script on every state change for this session.
func (s *Server) handleStream(w http.ResponseWriter, r *http.Request) {
	sid, err := s.session(w, r)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}

	sse := datastar.NewSSE(w, r)

	// Subscribe to all topics for this session.
	prefix := "tasks." + sid + "."
	updates, unsub := s.Hub.Subscribe(prefix)
	defer unsub()

	// Start a per-session ticker.
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	go s.runTicker(ctx, sid)

	// Initial render.
	if err := s.pushUpdate(sse, sid); err != nil {
		return
	}

	for {
		select {
		case <-r.Context().Done():
			return
		case topic, ok := <-updates:
			if !ok {
				return
			}
			_ = topic // we don't need to disambiguate
			if err := s.pushUpdate(sse, sid); err != nil {
				return
			}
		}
	}
}

// pushUpdate sends one full re-render plus a title script.
func (s *Server) pushUpdate(sse *datastar.ServerSentEventGenerator, sid string) error {
	tasks, err := s.DB.GetTasks(sid, false)
	if err != nil {
		log.Printf("pushUpdate get tasks: %v", err)
		return err
	}
	rate := s.rateKey(sid)
	if err := sse.PatchElementTempl(web.LiveRegion(sid, tasks, rate)); err != nil {
		return err
	}
	title := web.TitleText(tasks)
	// JSON-encode the title for safe injection into a script literal.
	encoded, _ := json.Marshal(title)
	return sse.ExecuteScript("document.title = " + string(encoded))
}

// runTicker emits "tick" events at the session's configured rate. A rate
// change publishes "tasks.{sid}.rate" which wakes this goroutine so it
// picks up the new interval immediately.
func (s *Server) runTicker(ctx context.Context, sid string) {
	rateCh, unsub := s.Hub.Subscribe("tasks." + sid + ".rate")
	defer unsub()

	for {
		secs := rateSeconds(s.rateKey(sid))
		if secs == 0 {
			// Paused; wait for rate change or shutdown.
			select {
			case <-ctx.Done():
				return
			case <-rateCh:
				continue
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-rateCh:
			continue
		case <-time.After(time.Duration(secs * float64(time.Second))):
			s.Hub.Publish("tasks." + sid + ".tick")
		}
	}
}

func (s *Server) handleAdd(w http.ResponseWriter, r *http.Request) {
	sid, _ := s.session(w, r)
	name := strings.TrimSpace(r.URL.Query().Get("name"))
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
	if id > 0 {
		_ = s.DB.StopAllTracking(sid)
		_ = s.DB.TaskStartTracking(id)
		s.Hub.Publish("tasks." + sid + ".update")
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleStop(w http.ResponseWriter, r *http.Request) {
	sid, _ := s.session(w, r)
	id := s.parseTaskID(r)
	if id > 0 {
		_ = s.DB.TaskStopTracking(id)
		s.Hub.Publish("tasks." + sid + ".update")
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleDone(w http.ResponseWriter, r *http.Request) {
	sid, _ := s.session(w, r)
	id := s.parseTaskID(r)
	if id > 0 {
		_ = s.DB.TaskComplete(id)
		s.Hub.Publish("tasks." + sid + ".update")
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleRename(w http.ResponseWriter, r *http.Request) {
	sid, _ := s.session(w, r)
	id := s.parseTaskID(r)
	name := strings.TrimSpace(r.URL.Query().Get("name"))
	if id > 0 && name != "" {
		_ = s.DB.RenameTask(id, name)
		s.Hub.Publish("tasks." + sid + ".update")
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleRate(w http.ResponseWriter, r *http.Request) {
	sid, _ := s.session(w, r)
	k := r.URL.Query().Get("r")
	switch k {
	case "live", "1s", "1m", "off":
	default:
		k = "1s"
	}
	encoded, _ := json.Marshal(k)
	if err := s.DB.SetJSON(sid, "tasks_rate", string(encoded)); err != nil {
		log.Printf("SetJSON rate: %v", err)
	}
	s.Hub.Publish("tasks." + sid + ".rate")
	s.Hub.Publish("tasks." + sid + ".update")
	w.WriteHeader(http.StatusNoContent)
}

// ---- OAuth ----

func (s *Server) handleOAuthStart(w http.ResponseWriter, r *http.Request) {
	if _, err := s.session(w, r); err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	state, err := auth.NewRandomState()
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	// Short-lived state cookie (5 min) signed with our key.
	s.Signer.SetSignedCookie(w, "oauth_state", state, 300)
	redir := s.publicURL(r) + "/oauth/callback"
	http.Redirect(w, r, s.Google.AuthorizeURL(redir, state), http.StatusFound)
}

func (s *Server) handleOAuthCallback(w http.ResponseWriter, r *http.Request) {
	sid, err := s.session(w, r)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}

	// CSRF check.
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
		// Fallback to local-part of email.
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
	auth.ClearCookie(w, "sid")
	http.Redirect(w, r, "/", http.StatusFound)
}

// ---- Admin / health -------------------------------------------------------

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
	activeSubs, totalTicks, droppedTicks := s.Hub.Stats()

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
		{"Hub: active subscribers", strconv.FormatInt(activeSubs, 10)},
		{"Hub: total publishes", strconv.FormatInt(totalTicks, 10)},
		{"Hub: dropped publishes", strconv.FormatInt(droppedTicks, 10)},
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
