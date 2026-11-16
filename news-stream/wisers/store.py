"""WiseSearch relay's state in Postgres, not on a disk (Deyao, 2026-11-08: state lives in the application layer, so the
relay runs on any node; no Kubernetes volume). The relay keeps its old layout as paths ("session.json", "jobs/<id>.json",
"raw/<name>.json", "images/<doc>/<n>.jpg", "events.jsonl"…), stored here:

- files: table wisers_file (ns, path, data, mtime): read / write (atomic: one upsert) / exists / listdir / mtime / remove;
- logs: table wisers_line (ns, path, id, line): append one line, lines() in order;
- the leader lock: a Postgres advisory lock ("yyc wisers leader <ns>", podlock.Lock: kept alive, taken again after a
  dropped connection, `on_lost` when another pod got it in the gap).

`ns` (env WISERS_STORE_NS, default "wisers") keeps tests apart from the live relay in one database. Every call goes
through corpus.db() (DATABASE_URL, autocommit); the tables are created on first use.

    store.write("jobs/123.json", json.dumps(j))   # bytes or str
    store.read_json("jobs/123.json")              # None when missing or not JSON
    store.append("searches.jsonl", json.dumps(row))
"""
import json
import os
import threading
import time

import corpus

NS = os.environ.get("WISERS_STORE_NS", "wisers")
_ready = threading.Event()
_ready_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS wisers_file (ns TEXT NOT NULL, path TEXT NOT NULL, data BYTEA NOT NULL,
  mtime DOUBLE PRECISION NOT NULL, PRIMARY KEY (ns, path));
CREATE INDEX IF NOT EXISTS wisers_file_dir ON wisers_file (ns, path text_pattern_ops);
CREATE TABLE IF NOT EXISTS wisers_line (id BIGSERIAL PRIMARY KEY, ns TEXT NOT NULL, path TEXT NOT NULL,
  line TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS wisers_line_path ON wisers_line (ns, path, id);
"""


def _db():
    if not _ready.is_set():
        with _ready_lock:
            if not _ready.is_set():
                db = corpus.db()
                with db:  # one transaction: two pods starting at once, one creates
                    db.execute("SELECT pg_advisory_xact_lock(hashtext('yyc wisers schema'))")
                    for stmt in (s for s in SCHEMA.split(";") if s.strip()):
                        db.execute(stmt)
                _ready.set()
    return corpus.db()


def _p(path):
    return path.strip("/")


def write(path, data):
    """the whole file at once (a reader never sees half of it)"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    _db().execute("INSERT INTO wisers_file (ns, path, data, mtime) VALUES (%s, %s, %s, %s) "
                  "ON CONFLICT (ns, path) DO UPDATE SET data = excluded.data, mtime = excluded.mtime",
                  (NS, _p(path), data, time.time()))


def read(path):
    """the file's bytes, or None"""
    r = _db().execute("SELECT data FROM wisers_file WHERE ns = %s AND path = %s", (NS, _p(path))).fetchone()
    return bytes(r[0]) if r else None


def read_text(path):
    b = read(path)
    return b.decode("utf-8") if b is not None else None


def write_json(path, d, indent=None):
    write(path, json.dumps(d, ensure_ascii=False, indent=indent))


def read_json(path):
    """the parsed file, or None when it is missing or not JSON"""
    b = read(path)
    if b is None:
        return None
    try:
        return json.loads(b)
    except ValueError:
        return None


def exists(path):
    return _db().execute("SELECT 1 FROM wisers_file WHERE ns = %s AND path = %s", (NS, _p(path))).fetchone() is not None


def mtime(path):
    """when the file was last written (epoch seconds), or None"""
    r = _db().execute("SELECT mtime FROM wisers_file WHERE ns = %s AND path = %s", (NS, _p(path))).fetchone()
    return r[0] if r else None


def listdir(d):
    """the names directly in directory d (files, and the subdirectories that hold files), as os.listdir"""
    pre = _p(d) + "/" if _p(d) else ""
    rows = _db().execute("SELECT path FROM wisers_file WHERE ns = %s AND path LIKE %s", (NS, _like(pre) + "%")).fetchall()
    return sorted({r[0][len(pre):].split("/", 1)[0] for r in rows})


def files(d):
    """[(path, mtime)] of every file under directory d, at any depth"""
    pre = _p(d) + "/"
    return _db().execute("SELECT path, mtime FROM wisers_file WHERE ns = %s AND path LIKE %s ORDER BY path",
                         (NS, _like(pre) + "%")).fetchall()


def remove(path):
    _db().execute("DELETE FROM wisers_file WHERE ns = %s AND path = %s", (NS, _p(path)))


def _like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def append(path, line):
    """one line at the end of a log (no newline needed)"""
    _db().execute("INSERT INTO wisers_line (ns, path, line) VALUES (%s, %s, %s)", (NS, _p(path), line.rstrip("\n")))


def lines(path, since_id=0):
    """the log's lines, oldest first"""
    return [r[0] for r in _db().execute("SELECT line FROM wisers_line WHERE ns = %s AND path = %s AND id > %s ORDER BY id",
                                        (NS, _p(path), since_id)).fetchall()]


def leader_lock(stop, on_lost=None):
    """the relay's leader lock (podlock.Lock): .take() waits until held (False if stop is set first), .release()"""
    import podlock
    return podlock.Lock(f"wisers leader {NS}", stop, on_lost=on_lost)

