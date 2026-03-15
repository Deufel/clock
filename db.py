import apsw, json, secrets, time

db = apsw.Connection(":memory:")
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA synchronous=NORMAL")
db.execute("PRAGMA busy_timeout=5000")
db.execute("PRAGMA cache_size=-64000")
db.execute("PRAGMA foreign_keys=ON")

db.execute("CREATE TABLE IF NOT EXISTS sessions(sid TEXT PRIMARY KEY, created REAL NOT NULL)")
db.execute("CREATE TABLE IF NOT EXISTS state(sid TEXT NOT NULL, key TEXT NOT NULL, val TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(sid, key), FOREIGN KEY(sid) REFERENCES sessions(sid) ON DELETE CASCADE)")

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
