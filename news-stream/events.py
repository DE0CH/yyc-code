"""The pipeline's event log for Deyao's live dashboard (/app/live; Deyao, 2026-10-21: "watch the pipeline live"):
one append-only table `events` (corpus.SCHEMA: id, at, kind, post_id, data JSONB), written by the poller and the jobs at
the moments the database records nothing else: a page read for its text or failed (fulltext), comments read for a
post (comments), a stage-1 and a stage-2 scoring (stage1, stage2), a Telegram/WhatsApp send (alert), a source or job
starting to fail or recovering (problem), and YYC AI's activity (ai: written by monitor.py's live loop from the
operator snapshot, not through emit()).
Stages that already leave a timestamped row are read from it directly (ingest = posts.id, filed = stream_items.id).

Never in the pipeline's way: emit() only appends to an in-memory queue and returns; a daemon thread of the process
writes the queue in batches every FLUSH seconds on its own pooled connection (never inside the caller's transaction),
and at exit (atexit, at most EXIT_WAIT seconds). A failed write is logged and those events are dropped; a full queue
drops new events (counted, logged once a minute). Retention: rows older than KEEP are deleted by the writer at most
every PRUNE seconds (any process; the delete is by the `at` index).

Payloads are small (strings cut to CUT characters, MAX_DATA bytes at most): the page joins posts for titles and
sources at read time, so an event carries only what the post row doesn't (a reason, a route, scores).
"""
import atexit, json, threading, time
from collections import deque
from datetime import datetime, timezone

KINDS = ("fulltext", "comments", "stage1", "stage2", "alert", "problem", "ai")
FLUSH = 1.0          # seconds between writes
BATCH = 500          # rows per INSERT
QUEUE_MAX = 20000    # events held at most while the database is slow; newer ones are dropped
CUT = 700            # characters per string value (Full text shows the first ~700)
MAX_DATA = 6000      # bytes of JSON per event
KEEP = "3 days"      # retention
PRUNE = 600          # seconds between two deletes of old rows (per process)
EXIT_WAIT = 5.0      # seconds the process waits at exit for the last write

_q = deque()
_lock = threading.Lock()
_wake = threading.Event()
_state = {"thread": None, "dropped": 0, "dropped_said": 0.0, "pruned": 0.0, "off": False}


def _cut(v, depth=0):
    if isinstance(v, str):
        return v[:CUT]
    if isinstance(v, dict) and depth < 4:
        return {str(k)[:60]: _cut(x, depth + 1) for k, x in list(v.items())[:40]}
    if isinstance(v, (list, tuple)) and depth < 4:
        return [_cut(x, depth + 1) for x in list(v)[:40]]
    if v is None or isinstance(v, (bool, int, float)):
        return v
    return str(v)[:CUT]


def emit(kind, post_id=None, **data):
    """record one event (never raises, never waits)"""
    try:
        if _state["off"]:
            return
        body = json.dumps(_cut(data), ensure_ascii=False, default=str)
        if len(body.encode()) > MAX_DATA:
            body = json.dumps({"cut": True, **{k: v for k, v in _cut(data).items() if not isinstance(v, (dict, list))}},
                              ensure_ascii=False, default=str)[:MAX_DATA]
        pid = int(post_id) if post_id is not None else None
        with _lock:
            if len(_q) >= QUEUE_MAX:
                _state["dropped"] += 1
                if time.time() - _state["dropped_said"] > 60:
                    _state["dropped_said"] = time.time()
                    print(f"events: queue full, {_state['dropped']} event(s) dropped so far", flush=True)
                return
            _q.append((datetime.now(timezone.utc), kind, pid, body))  # the moment it happened, not the write's
        _start()
    except Exception as e:  # noqa: BLE001: the dashboard misses one event; the pipeline goes on
        print("events: emit failed:", repr(e)[:200], flush=True)


def _start():
    if _state["thread"] is not None:
        return
    with _lock:
        if _state["thread"] is not None:
            return
        t = threading.Thread(target=_loop, name="events", daemon=True)
        _state["thread"] = t
        t.start()


def _take():
    with _lock:
        n = min(len(_q), BATCH)
        return [_q.popleft() for _ in range(n)]


def flush():
    """write what is queued now (the writer thread, at exit, and tests) → rows written"""
    import corpus
    done = 0
    while True:
        rows = _take()
        if not rows:
            break
        try:
            corpus.db().executemany("INSERT INTO events (at, kind, post_id, data) VALUES (%s, %s, %s, %s::jsonb)", rows)
            done += len(rows)
        except Exception as e:  # noqa: BLE001: these events are lost, the next ones are tried afresh
            print(f"events: write of {len(rows)} event(s) failed: {e!r}"[:300], flush=True)
            break
    if done and time.time() - _state["pruned"] > PRUNE:
        _state["pruned"] = time.time()
        try:
            corpus.db().execute(f"DELETE FROM events WHERE at < now() - interval '{KEEP}'")
        except Exception as e:  # noqa: BLE001
            print("events: prune failed:", repr(e)[:200], flush=True)
    return done


def _loop():
    while True:
        _wake.wait(FLUSH)
        _wake.clear()
        flush()


def _at_exit():
    if not _q:
        return
    t = threading.Thread(target=flush, daemon=True)
    t.start()
    t.join(EXIT_WAIT)


atexit.register(_at_exit)
