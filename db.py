import apsw, json, secrets, time, os

DB_PATH = os.environ.get("DB_PATH", "data/clock.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
db = apsw.Connection(DB_PATH)
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA synchronous=NORMAL")
db.execute("PRAGMA busy_timeout=5000")
db.execute("PRAGMA cache_size=-64000")
db.execute("PRAGMA foreign_keys=ON")

db.execute("""CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    google_id TEXT UNIQUE,
    created_at REAL NOT NULL)""")
db.execute("CREATE TABLE IF NOT EXISTS sessions(sid TEXT PRIMARY KEY, user_id INTEGER, created REAL NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id))")
try: db.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER REFERENCES users(id)")
except: pass
db.execute("CREATE TABLE IF NOT EXISTS state(sid TEXT NOT NULL, key TEXT NOT NULL, val TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(sid, key), FOREIGN KEY(sid) REFERENCES sessions(sid) ON DELETE CASCADE)")
db.execute("""CREATE TABLE IF NOT EXISTS tasks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sid TEXT NOT NULL,
    name TEXT NOT NULL,
    elapsed REAL NOT NULL DEFAULT 0,
    track_start REAL,
    done INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    FOREIGN KEY(sid) REFERENCES sessions(sid) ON DELETE CASCADE)""")

def new_session():
    sid = secrets.token_urlsafe(16)
    db.execute("INSERT INTO sessions(sid, created) VALUES(?, ?)", (sid, time.time()))
    return sid

def valid_session(sid): return sid and db.execute("SELECT 1 FROM sessions WHERE sid=?", (sid,)).fetchone() is not None

def get_json(sid, key, default=None):
    row = db.execute("SELECT val FROM state WHERE sid=? AND key=?", (sid, key)).fetchone()
    return json.loads(row[0]) if row else (default() if callable(default) else default)

def set_json(sid, key, obj): db.execute("REPLACE INTO state(sid, key, val) VALUES(?, ?, ?)", (sid, key, json.dumps(obj)))

def del_session(sid): db.execute("DELETE FROM sessions WHERE sid=?", (sid,))

def add_task(sid, name):
    db.execute("INSERT INTO tasks(sid, name, created) VALUES(?, ?, ?)", (sid, name, time.time()))
    return db.last_insert_rowid()

def get_tasks(sid, include_done=False):
    q = "SELECT id, name, elapsed, track_start, done, created FROM tasks WHERE sid=?"
    if not include_done: q += " AND done=0"
    return [dict(id=r[0], name=r[1], elapsed=r[2], track_start=r[3], done=r[4], created=r[5]) for r in db.execute(q + " ORDER BY created", (sid,))]

def get_task(tid):
    r = db.execute("SELECT id, name, elapsed, track_start, done, created, sid FROM tasks WHERE id=?", (tid,)).fetchone()
    return dict(id=r[0], name=r[1], elapsed=r[2], track_start=r[3], done=r[4], created=r[5], sid=r[6]) if r else None

def task_start_tracking(tid):
    db.execute("UPDATE tasks SET track_start=? WHERE id=? AND track_start IS NULL AND done=0", (time.time(), tid))

def task_stop_tracking(tid):
    t = get_task(tid)
    if not t or t["track_start"] is None: return
    extra = time.time() - t["track_start"]
    db.execute("UPDATE tasks SET elapsed=elapsed+?, track_start=NULL WHERE id=?", (extra, tid))

def task_complete(tid):
    task_stop_tracking(tid)
    db.execute("UPDATE tasks SET done=1 WHERE id=?", (tid,))

def task_elapsed(t):
    e = t["elapsed"]
    if t["track_start"] is not None: e += time.time() - t["track_start"]
    return e

def rename_task(tid, name): db.execute("UPDATE tasks SET name=? WHERE id=?", (name, tid))

def stop_all_tracking(sid):
    for t in get_tasks(sid):
        if t["track_start"] is not None: task_stop_tracking(t["id"])

# --- Auth ---

def find_user_by_google_id(google_id):
    r = db.execute("SELECT id, email, name, google_id, created_at FROM users WHERE google_id=?", (google_id,)).fetchone()
    return dict(id=r[0], email=r[1], name=r[2], google_id=r[3], created_at=r[4]) if r else None

def find_user_by_email(email):
    r = db.execute("SELECT id, email, name, google_id, created_at FROM users WHERE email=?", (email,)).fetchone()
    return dict(id=r[0], email=r[1], name=r[2], google_id=r[3], created_at=r[4]) if r else None

def create_user(email, name, google_id):
    db.execute("INSERT INTO users(email, name, google_id, created_at) VALUES(?, ?, ?, ?)",
               (email, name, google_id, time.time()))
    return db.last_insert_rowid()

def link_session_to_user(sid, user_id):
    db.execute("UPDATE sessions SET user_id=? WHERE sid=?", (user_id, sid))

def get_session_user(sid):
    r = db.execute("SELECT user_id FROM sessions WHERE sid=?", (sid,)).fetchone()
    if not r or r[0] is None: return None
    return find_user_by_id(r[0])

def find_user_by_id(user_id):
    r = db.execute("SELECT id, email, name, google_id, created_at FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(id=r[0], email=r[1], name=r[2], google_id=r[3], created_at=r[4]) if r else None

def get_user_session(user_id):
    r = db.execute("SELECT sid FROM sessions WHERE user_id=? ORDER BY created DESC LIMIT 1", (user_id,)).fetchone()
    return r[0] if r else None

def migrate_tasks_to_session(from_sid, to_sid):
    db.execute("UPDATE tasks SET sid=? WHERE sid=?", (to_sid, from_sid))
    db.execute("INSERT OR IGNORE INTO state(sid, key, val) SELECT ?, key, val FROM state WHERE sid=?", (to_sid, from_sid))
    db.execute("DELETE FROM state WHERE sid=?", (from_sid,))

def find_or_create_user_and_link(sid, email, name, google_id):
    user = find_user_by_google_id(google_id)
    if not user:
        user = find_user_by_email(email)
        if user:
            db.execute("UPDATE users SET google_id=? WHERE id=?", (google_id, user["id"]))
            user["google_id"] = google_id
    if not user:
        uid = create_user(email, name, google_id)
        user = find_user_by_id(uid)
        link_session_to_user(sid, user["id"])
        return user, sid
    existing_sid = get_user_session(user["id"])
    if existing_sid and existing_sid != sid:
        migrate_tasks_to_session(sid, existing_sid)
        del_session(sid)
        return user, existing_sid
    link_session_to_user(sid, user["id"])
    return user, sid

# --- Admin stats ---

def admin_stats():
    s = {}
    s["users"] = db.execute("SELECT count(*) FROM users").fetchone()[0]
    s["sessions"] = db.execute("SELECT count(*) FROM sessions").fetchone()[0]
    s["sessions_authed"] = db.execute("SELECT count(*) FROM sessions WHERE user_id IS NOT NULL").fetchone()[0]
    s["sessions_anon"] = s["sessions"] - s["sessions_authed"]
    s["tasks_total"] = db.execute("SELECT count(*) FROM tasks").fetchone()[0]
    s["tasks_active"] = db.execute("SELECT count(*) FROM tasks WHERE done=0").fetchone()[0]
    s["tasks_done"] = db.execute("SELECT count(*) FROM tasks WHERE done=1").fetchone()[0]
    s["tasks_tracking"] = db.execute("SELECT count(*) FROM tasks WHERE track_start IS NOT NULL").fetchone()[0]
    row = db.execute("SELECT coalesce(sum(elapsed), 0) FROM tasks").fetchone()
    s["total_elapsed"] = row[0]
    for r in db.execute("SELECT track_start FROM tasks WHERE track_start IS NOT NULL"):
        s["total_elapsed"] += time.time() - r[0]
    return s
