// Package state holds the application's source-of-truth and a per-session
// broadcast hub. Ports db.py and the relay logic from the Python clock app.
//
// Hub topics:
//   - "tasks.{sid}.update" — fired when a task is added/changed/completed.
//   - "tasks.{sid}.tick"   — fired by the ticker on the configured rate.
//   - "tasks.{sid}.rate"   — fired when the user changes the tick rate;
//                            used to interrupt a sleeping ticker.
//
// The stream handler subscribes to "tasks.{sid}.*" (any topic for this
// session) and re-renders on every event.
package state

import (
	"crypto/rand"
	"database/sql"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	_ "modernc.org/sqlite"
)

// ---- DB --------------------------------------------------------------------

type DB struct {
	*sql.DB
}

func Open(path string) (*DB, error) {
	d, err := sql.Open("sqlite", path+"?_pragma=journal_mode(WAL)&_pragma=synchronous(NORMAL)&_pragma=busy_timeout(5000)&_pragma=foreign_keys(on)&_pragma=cache_size(-64000)")
	if err != nil {
		return nil, err
	}
	if err := d.Ping(); err != nil {
		return nil, err
	}
	// SQLite + WAL works best with a single writer connection.
	d.SetMaxOpenConns(1)

	db := &DB{d}
	if err := db.migrate(); err != nil {
		return nil, err
	}
	return db, nil
}

func (db *DB) migrate() error {
	stmts := []string{
		`CREATE TABLE IF NOT EXISTS users(
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			email TEXT UNIQUE NOT NULL,
			name TEXT NOT NULL DEFAULT '',
			google_id TEXT UNIQUE,
			created_at REAL NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS sessions(
			sid TEXT PRIMARY KEY,
			user_id INTEGER REFERENCES users(id),
			created REAL NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS state(
			sid TEXT NOT NULL,
			key TEXT NOT NULL,
			val TEXT NOT NULL DEFAULT '{}',
			PRIMARY KEY(sid, key),
			FOREIGN KEY(sid) REFERENCES sessions(sid) ON DELETE CASCADE)`,
		`CREATE TABLE IF NOT EXISTS tasks(
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			sid TEXT NOT NULL,
			name TEXT NOT NULL,
			elapsed REAL NOT NULL DEFAULT 0,
			track_start REAL,
			done INTEGER NOT NULL DEFAULT 0,
			created REAL NOT NULL,
			FOREIGN KEY(sid) REFERENCES sessions(sid) ON DELETE CASCADE)`,
	}
	for _, s := range stmts {
		if _, err := db.Exec(s); err != nil {
			return fmt.Errorf("migrate: %w", err)
		}
	}
	return nil
}

// now returns wall-clock seconds as float64 so timestamps are byte-compatible
// with the Python version's time.time().
func now() float64 {
	return float64(time.Now().UnixNano()) / 1e9
}

// ---- Types -----------------------------------------------------------------

type User struct {
	ID        int64
	Email     string
	Name      string
	GoogleID  sql.NullString
	CreatedAt float64
}

type Task struct {
	ID         int64
	SID        string
	Name       string
	Elapsed    float64
	TrackStart sql.NullFloat64
	Done       bool
	Created    float64
}

// Tracking reports whether this task is currently being tracked.
func (t Task) Tracking() bool { return t.TrackStart.Valid }

// CurrentElapsed returns the cumulative elapsed seconds including any in-
// progress tracking window.
func (t Task) CurrentElapsed() float64 {
	e := t.Elapsed
	if t.TrackStart.Valid {
		e += now() - t.TrackStart.Float64
	}
	return e
}

// ---- Session helpers -------------------------------------------------------

func randomToken(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

// NewSession creates a fresh anonymous session.
func (db *DB) NewSession() (string, error) {
	sid, err := randomToken(16)
	if err != nil {
		return "", err
	}
	if _, err := db.Exec("INSERT INTO sessions(sid, created) VALUES(?, ?)", sid, now()); err != nil {
		return "", err
	}
	return sid, nil
}

// ValidSession reports whether sid exists.
func (db *DB) ValidSession(sid string) bool {
	if sid == "" {
		return false
	}
	var x int
	err := db.QueryRow("SELECT 1 FROM sessions WHERE sid=?", sid).Scan(&x)
	return err == nil
}

// DelSession removes a session and (via cascade) its state and tasks.
func (db *DB) DelSession(sid string) error {
	_, err := db.Exec("DELETE FROM sessions WHERE sid=?", sid)
	return err
}

// ---- JSON state per session ------------------------------------------------

func (db *DB) GetJSON(sid, key string) (string, bool) {
	var v string
	err := db.QueryRow("SELECT val FROM state WHERE sid=? AND key=?", sid, key).Scan(&v)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false
	}
	if err != nil {
		return "", false
	}
	return v, true
}

func (db *DB) SetJSON(sid, key, val string) error {
	_, err := db.Exec("REPLACE INTO state(sid, key, val) VALUES(?, ?, ?)", sid, key, val)
	return err
}

// ---- Tasks -----------------------------------------------------------------

func (db *DB) AddTask(sid, name string) (int64, error) {
	res, err := db.Exec("INSERT INTO tasks(sid, name, created) VALUES(?, ?, ?)", sid, name, now())
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

func (db *DB) GetTasks(sid string, includeDone bool) ([]Task, error) {
	q := "SELECT id, sid, name, elapsed, track_start, done, created FROM tasks WHERE sid=?"
	if !includeDone {
		q += " AND done=0"
	}
	q += " ORDER BY created"
	rows, err := db.Query(q, sid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Task
	for rows.Next() {
		var t Task
		var done int
		if err := rows.Scan(&t.ID, &t.SID, &t.Name, &t.Elapsed, &t.TrackStart, &done, &t.Created); err != nil {
			return nil, err
		}
		t.Done = done != 0
		out = append(out, t)
	}
	return out, rows.Err()
}

func (db *DB) GetTask(tid int64) (*Task, error) {
	var t Task
	var done int
	err := db.QueryRow("SELECT id, sid, name, elapsed, track_start, done, created FROM tasks WHERE id=?", tid).
		Scan(&t.ID, &t.SID, &t.Name, &t.Elapsed, &t.TrackStart, &done, &t.Created)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	t.Done = done != 0
	return &t, nil
}

func (db *DB) TaskStartTracking(tid int64) error {
	_, err := db.Exec("UPDATE tasks SET track_start=? WHERE id=? AND track_start IS NULL AND done=0", now(), tid)
	return err
}

func (db *DB) TaskStopTracking(tid int64) error {
	t, err := db.GetTask(tid)
	if err != nil || t == nil || !t.TrackStart.Valid {
		return err
	}
	extra := now() - t.TrackStart.Float64
	_, err = db.Exec("UPDATE tasks SET elapsed=elapsed+?, track_start=NULL WHERE id=?", extra, tid)
	return err
}

func (db *DB) TaskComplete(tid int64) error {
	if err := db.TaskStopTracking(tid); err != nil {
		return err
	}
	_, err := db.Exec("UPDATE tasks SET done=1 WHERE id=?", tid)
	return err
}

func (db *DB) RenameTask(tid int64, name string) error {
	_, err := db.Exec("UPDATE tasks SET name=? WHERE id=?", name, tid)
	return err
}

func (db *DB) StopAllTracking(sid string) error {
	rows, err := db.Query("SELECT id FROM tasks WHERE sid=? AND track_start IS NOT NULL", sid)
	if err != nil {
		return err
	}
	var ids []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			rows.Close()
			return err
		}
		ids = append(ids, id)
	}
	rows.Close()
	for _, id := range ids {
		if err := db.TaskStopTracking(id); err != nil {
			return err
		}
	}
	return nil
}

// ---- Users / OAuth ---------------------------------------------------------

func (db *DB) FindUserByGoogleID(gid string) (*User, error) {
	return db.findUser("google_id=?", gid)
}

func (db *DB) FindUserByEmail(email string) (*User, error) {
	return db.findUser("email=?", email)
}

func (db *DB) FindUserByID(id int64) (*User, error) {
	return db.findUser("id=?", id)
}

func (db *DB) findUser(where string, arg any) (*User, error) {
	var u User
	err := db.QueryRow("SELECT id, email, name, google_id, created_at FROM users WHERE "+where, arg).
		Scan(&u.ID, &u.Email, &u.Name, &u.GoogleID, &u.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &u, nil
}

func (db *DB) CreateUser(email, name, googleID string) (int64, error) {
	res, err := db.Exec("INSERT INTO users(email, name, google_id, created_at) VALUES(?, ?, ?, ?)",
		email, name, googleID, now())
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

func (db *DB) LinkSessionToUser(sid string, uid int64) error {
	_, err := db.Exec("UPDATE sessions SET user_id=? WHERE sid=?", uid, sid)
	return err
}

func (db *DB) GetSessionUser(sid string) (*User, error) {
	var uid sql.NullInt64
	err := db.QueryRow("SELECT user_id FROM sessions WHERE sid=?", sid).Scan(&uid)
	if err != nil || !uid.Valid {
		return nil, nil
	}
	return db.FindUserByID(uid.Int64)
}

// GetUserSession returns the most recent session id for a given user, or ""
// if the user has no prior sessions.
func (db *DB) GetUserSession(uid int64) (string, error) {
	var sid string
	err := db.QueryRow("SELECT sid FROM sessions WHERE user_id=? ORDER BY created DESC LIMIT 1", uid).Scan(&sid)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return sid, err
}

// MigrateTasksToSession reassigns all tasks and per-session state from one
// sid to another. Used when an anonymous user signs in and we want to
// merge their work into their existing authenticated session.
func (db *DB) MigrateTasksToSession(fromSID, toSID string) error {
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.Exec("UPDATE tasks SET sid=? WHERE sid=?", toSID, fromSID); err != nil {
		return err
	}
	if _, err := tx.Exec(
		"INSERT OR IGNORE INTO state(sid, key, val) SELECT ?, key, val FROM state WHERE sid=?",
		toSID, fromSID); err != nil {
		return err
	}
	if _, err := tx.Exec("DELETE FROM state WHERE sid=?", fromSID); err != nil {
		return err
	}
	return tx.Commit()
}

// FindOrCreateUserAndLink is the OAuth callback's main DB operation.
//
// Logic mirrors find_or_create_user_and_link in db.py:
//   - If a user exists with this google_id, use them.
//   - Else if a user exists with this email, attach the google_id to them.
//   - Else create a new user.
//   - If the user already has a prior session, migrate the current
//     anonymous session's data into that session and return its sid.
//   - Otherwise link the current sid to the user and return it.
//
// Returns (user, effective sid). The caller should write the returned sid
// back to the cookie since it may have changed.
func (db *DB) FindOrCreateUserAndLink(sid, email, name, googleID string) (*User, string, error) {
	user, err := db.FindUserByGoogleID(googleID)
	if err != nil {
		return nil, "", err
	}
	if user == nil {
		user, err = db.FindUserByEmail(email)
		if err != nil {
			return nil, "", err
		}
		if user != nil {
			if _, err := db.Exec("UPDATE users SET google_id=? WHERE id=?", googleID, user.ID); err != nil {
				return nil, "", err
			}
			user.GoogleID = sql.NullString{String: googleID, Valid: true}
		}
	}
	if user == nil {
		uid, err := db.CreateUser(email, name, googleID)
		if err != nil {
			return nil, "", err
		}
		user, err = db.FindUserByID(uid)
		if err != nil {
			return nil, "", err
		}
		if err := db.LinkSessionToUser(sid, user.ID); err != nil {
			return nil, "", err
		}
		return user, sid, nil
	}
	existing, err := db.GetUserSession(user.ID)
	if err != nil {
		return nil, "", err
	}
	if existing != "" && existing != sid {
		if err := db.MigrateTasksToSession(sid, existing); err != nil {
			return nil, "", err
		}
		if err := db.DelSession(sid); err != nil {
			return nil, "", err
		}
		return user, existing, nil
	}
	if err := db.LinkSessionToUser(sid, user.ID); err != nil {
		return nil, "", err
	}
	return user, sid, nil
}

// ---- Admin stats -----------------------------------------------------------

type AdminStats struct {
	Users          int
	Sessions       int
	SessionsAuthed int
	SessionsAnon   int
	TasksTotal     int
	TasksActive    int
	TasksDone      int
	TasksTracking  int
	TotalElapsed   float64
}

func (db *DB) AdminStats() (AdminStats, error) {
	var s AdminStats
	scan := func(q string, dest *int) error {
		return db.QueryRow(q).Scan(dest)
	}
	if err := scan("SELECT count(*) FROM users", &s.Users); err != nil {
		return s, err
	}
	if err := scan("SELECT count(*) FROM sessions", &s.Sessions); err != nil {
		return s, err
	}
	if err := scan("SELECT count(*) FROM sessions WHERE user_id IS NOT NULL", &s.SessionsAuthed); err != nil {
		return s, err
	}
	s.SessionsAnon = s.Sessions - s.SessionsAuthed
	if err := scan("SELECT count(*) FROM tasks", &s.TasksTotal); err != nil {
		return s, err
	}
	if err := scan("SELECT count(*) FROM tasks WHERE done=0", &s.TasksActive); err != nil {
		return s, err
	}
	if err := scan("SELECT count(*) FROM tasks WHERE done=1", &s.TasksDone); err != nil {
		return s, err
	}
	if err := scan("SELECT count(*) FROM tasks WHERE track_start IS NOT NULL", &s.TasksTracking); err != nil {
		return s, err
	}
	if err := db.QueryRow("SELECT coalesce(sum(elapsed), 0) FROM tasks").Scan(&s.TotalElapsed); err != nil {
		return s, err
	}
	rows, err := db.Query("SELECT track_start FROM tasks WHERE track_start IS NOT NULL")
	if err != nil {
		return s, err
	}
	defer rows.Close()
	for rows.Next() {
		var ts float64
		if err := rows.Scan(&ts); err != nil {
			return s, err
		}
		s.TotalElapsed += now() - ts
	}
	return s, nil
}

// ---- Hub -------------------------------------------------------------------
//
// A topic-based fan-out broadcaster. Subscribers register interest in topics
// matching a prefix pattern (e.g. "tasks.abc.*" matches any "tasks.abc.X").
// Publishes are non-blocking; if a subscriber's buffer is full the tick is
// dropped — subscribers re-read fresh state on every wake, so a missed tick
// just collapses into the next one.
//
// This is a port of py_sse's create_relay() with prefix matching.

type Hub struct {
	mu          sync.RWMutex
	subscribers map[*subscriber]struct{}

	activeSubs   atomic.Int64
	totalTicks   atomic.Int64
	droppedTicks atomic.Int64
}

type subscriber struct {
	prefix string // "" matches all
	ch     chan string
}

func NewHub() *Hub {
	return &Hub{subscribers: make(map[*subscriber]struct{})}
}

// Subscribe registers a subscriber for topics that start with prefix (use
// "tasks.{sid}." to receive any topic for one session). Returns a channel of
// matched topic names and an unsubscribe func.
func (h *Hub) Subscribe(prefix string) (<-chan string, func()) {
	s := &subscriber{prefix: prefix, ch: make(chan string, 8)}
	h.mu.Lock()
	h.subscribers[s] = struct{}{}
	h.mu.Unlock()
	h.activeSubs.Add(1)
	return s.ch, func() {
		h.mu.Lock()
		delete(h.subscribers, s)
		h.mu.Unlock()
		h.activeSubs.Add(-1)
		close(s.ch)
	}
}

// Publish delivers topic to every subscriber whose prefix matches. Non-
// blocking — a full subscriber buffer drops the topic for that subscriber.
func (h *Hub) Publish(topic string) {
	h.totalTicks.Add(1)
	h.mu.RLock()
	defer h.mu.RUnlock()
	for s := range h.subscribers {
		if s.prefix != "" && !strings.HasPrefix(topic, s.prefix) {
			continue
		}
		select {
		case s.ch <- topic:
		default:
			h.droppedTicks.Add(1)
		}
	}
}

// Stats returns counter snapshots for /metrics.
func (h *Hub) Stats() (active, total, dropped int64) {
	return h.activeSubs.Load(), h.totalTicks.Load(), h.droppedTicks.Load()
}
