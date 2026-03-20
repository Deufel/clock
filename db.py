import apsw, json, secrets, time

db = apsw.Connection(":memory:")
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA synchronous=NORMAL")
db.execute("PRAGMA busy_timeout=5000")
db.execute("PRAGMA cache_size=-64000")
db.execute("PRAGMA foreign_keys=ON")

db.execute("CREATE TABLE IF NOT EXISTS sessions(sid TEXT PRIMARY KEY, created REAL NOT NULL)")
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
    db.execute("UPDATE tasks SET track_start=? WHERE id=? AND track_start IS NULL AND done=0", (time.monotonic(), tid))

def task_stop_tracking(tid):
    t = get_task(tid)
    if not t or t["track_start"] is None: return
    extra = time.monotonic() - t["track_start"]
    db.execute("UPDATE tasks SET elapsed=elapsed+?, track_start=NULL WHERE id=?", (extra, tid))

def task_complete(tid):
    task_stop_tracking(tid)
    db.execute("UPDATE tasks SET done=1 WHERE id=?", (tid,))

def task_elapsed(t):
    e = t["elapsed"]
    if t["track_start"] is not None: e += time.monotonic() - t["track_start"]
    return int(e)

def rename_task(tid, name): db.execute("UPDATE tasks SET name=? WHERE id=?", (name, tid))

def stop_all_tracking(sid):
    for t in get_tasks(sid):
        if t["track_start"] is not None: task_stop_tracking(t["id"])
