#!/usr/bin/env python3
"""The singleton workers never run twice at once and never sit idle through a rollout, with real processes and a real
Postgres (TEST_DATABASE_URL; its tables are dropped and made again); the work itself is stubbed (no model, no
download, no WhatsApp).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_worker_locks.py

Seen on the cluster (2026-10-16 11:34 UTC): three transcribe pods, Terminating, still transcribing next to the new pod
that held the lock 'yyc transcribe': each had lost its lock's connection without noticing, so the next pod took the
lock while the old one kept working (and, the claims being in memory, could take the same clip).

1. images and whatsapp (one copy at a time, lock 'yyc images' / 'yyc whatsapp'): when the lock's connection is cut
   (pg_terminate_backend, as a database restart or a dropped connection does) while the next pod waits, the old copy
   notices within seconds, stops (the WhatsApp sender closes its connection) and exits, so the two never work side by
   side for long; cut with no other copy waiting, it takes the lock again and carries on.
2. transcribe: every copy works, a rollout's new pod at once (clips run up to hours: the new pod never waits for the
   old one's clips in hand); a clip is claimed in the database, so no clip is transcribed twice; a SIGTERM lets the
   copy finish its clip in hand and exit; a SIGKILLed copy's clip is taken by another copy once its claim lapses; the
   page scan runs in one copy at a time (lock 'yyc transcribe').
"""
import os, signal, subprocess, sys, threading, time, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import flags_on  # noqa: E402,F401  picture analysis and transcription on (off since 2026-10-20: tests/test_pictures_transcription_off.py)
URL = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATABASE_URL"] = URL
sys.path.insert(0, HERE)
import psycopg  # noqa: E402

with psycopg.connect(URL, autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
import corpus  # noqa: E402

corpus.db().execute("SELECT 1")  # the schema

PRELUDE = r"""
import os, sys, tempfile, threading, time
sys.path.insert(0, sys.argv[2])
name = sys.argv[1]
try:
    import podlock
    podlock.CHECK, podlock.POLL = 1, 0.2  # the lock checked every second (the test's clock)
except ImportError:
    pass
d = tempfile.mkdtemp()
"""

IMAGES = PRELUDE + r"""
import images
def loop():
    while not images.STOP.is_set():
        print(name, "work", flush=True)
        images.STOP.wait(0.2)
images.scan_loop = images.work_loop = loop
images.WORKERS = 1
images.MODEL = "test/model"  # IMAGE_MODEL has no default
images.STARTED, images.ALIVE = d + "/started", d + "/alive"
images.main()
"""

WHATSAPP = PRELUDE + r"""
import subprocess
import whatsapp_alerts as w
def start_gateway():
    w.held["proc"] = subprocess.Popen(["sleep", "600"])
    print(name, "gateway up", flush=True)
w.start_gateway = start_gateway
w.gateway = lambda *a, **k: {"state": "linked"}
w.check_alerts = lambda st: None
def send_due(streams):
    print(name, "work", flush=True)
    return 0
w.send_due, w.IDLE = send_due, 0.2
w.STARTED, w.ALIVE = d + "/started", d + "/alive"
w.run()
"""

MEDIA = PRELUDE + r"""
import media
def scan_once():
    print(name, "scan", flush=True)
    return 0
def transcribe_source(m, model=None):
    print(name, "clip start", m["key"], flush=True)
    time.sleep(30 if m["key"].startswith("long") else 1.5)
    print(name, "clip done", m["key"], flush=True)
    return "text", 60.0, 0.0, "m", None
media.scan_once, media.transcribe_source = scan_once, transcribe_source
media.CLIPS = 2
media.LEASE = 3  # seconds a claim lasts unrenewed (the test's clock)
media.STARTED, media.ALIVE = d + "/started", d + "/alive"
media.main()
"""


class Pod:
    def __init__(self, script, name):
        self.p = subprocess.Popen([sys.executable, "-c", script, name, HERE], env=dict(os.environ, DATABASE_URL=URL),
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.name, self.lines = name, []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.lines.append((time.time(), line.rstrip()))

    def at(self, text, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            for t, l in list(self.lines):
                if text in l:
                    return t
            time.sleep(0.05)
        raise AssertionError(f"{self.name}: no {text!r} in {[l for _, l in self.lines][-40:]}")

    def has(self, text, after=0):
        return any(text in l and t > after for t, l in list(self.lines))

    def stop(self):
        if self.p.poll() is None:
            self.p.kill()
        self.p.wait(10)


def cut_lock(name):
    """the lock holder's connection ends (a database restart, a dropped connection): the server frees the lock"""
    with psycopg.connect(URL, autocommit=True) as c:
        n = c.execute("""SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory' AND granted
                         AND objid = hashtext(%s)::oid""", (name,)).fetchall()
    assert n, f"nobody holds {name!r}"


def holders(name):
    with psycopg.connect(URL, autocommit=True) as c:
        return c.execute("""SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted
                            AND objid = hashtext(%s)::oid""", (name,)).fetchone()[0]


class Singleton:
    script = lock = took = None

    def test_lost_lock_while_the_next_pod_waits(self):
        a = Pod(self.script, "A")
        pods = [a]
        try:
            a.at(self.took)
            a.at("A work")
            b = Pod(self.script, "B")
            pods.append(b)
            b.at("waiting for")
            time.sleep(1)
            self.assertFalse(b.has(self.took))
            cut_lock(self.lock)
            t_b = b.at(self.took, timeout=15)
            self.assertEqual(a.p.wait(15), 0, "A noticed the lost lock, drained and exited")
            t_exit = time.time()
            self.assertLess(t_exit - t_b, 8, "A stopped within seconds of B taking over")
            b.at("B work")
            self.assertFalse(a.has("A work", after=t_b + 3), "A kept working beside B")
        finally:
            for p in pods:
                p.stop()

    def test_lost_lock_alone_is_taken_again(self):
        a = Pod(self.script, "A")
        try:
            a.at(self.took)
            cut_lock(self.lock)
            t = time.time()
            a.at("retook", timeout=15)
            time.sleep(1)
            self.assertIsNone(a.p.poll(), "still running")
            self.assertTrue(a.has("A work", after=t + 1))
            self.assertEqual(holders(self.lock), 1)
        finally:
            a.stop()


class Images(Singleton, unittest.TestCase):
    script, lock, took = IMAGES, "yyc images", "took images lock"


class WhatsApp(Singleton, unittest.TestCase):
    script, lock, took = WHATSAPP, "yyc whatsapp", "took whatsapp lock"


def queue(keys):
    """queued clips, each on a post seen now: a clip is due only for a post in stage transcription's work
    (corpus.seen_cond), as every real clip is found on a post's page"""
    from datetime import datetime
    c = corpus.db()
    c.execute("DELETE FROM post_media; DELETE FROM media")
    seen = datetime.now(corpus.HKT).isoformat(timespec="seconds")
    for i, k in enumerate(keys):
        url = "https://example.org/post/" + k
        corpus.ingest([{"sid": "rthk", "source": "RTHK", "title": "片段 " + k, "url": url, "published": seen}], seen)
        c.execute("INSERT INTO media (key, url, kind, status, newest) VALUES (%s, %s, 'direct', 'queued', %s)",
                  (k, "https://example.org/" + k, f"2026-10-16T12:{i:02d}:00+08:00"))
        c.execute("INSERT INTO post_media (post_id, key) VALUES ((SELECT id FROM posts WHERE url=%s), %s)", (url, k))
    # live posts of a stage that has been working: a source's first read is backlog, and a stage with no round on
    # record starts from now (corpus.stage), either of which would leave these clips out
    c.execute("UPDATE posts SET first_run = NULL WHERE sid = 'rthk'")
    corpus.kv_set("stage:transcription", {"ok": seen, "down": None})


def done():
    return {r["key"] for r in corpus.db().execute("SELECT key FROM media WHERE status = 'done'")}


class Transcribe(unittest.TestCase):
    def test_two_copies_share_the_queue(self):
        keys = [f"c{i}" for i in range(10)]
        queue(keys)
        a = Pod(MEDIA, "A")
        b = Pod(MEDIA, "B")  # a rollout's new pod: works at once
        try:
            end = time.time() + 40
            while done() != set(keys) and time.time() < end:
                time.sleep(0.2)
            self.assertEqual(done(), set(keys))
            starts = [l.split()[-1] for p in (a, b) for _, l in p.lines if " clip start " in l]
            self.assertEqual(sorted(starts), sorted(keys), "every clip transcribed once")
            self.assertTrue(a.has("A clip start") and b.has("B clip start"), "both copies worked")
            self.assertFalse(a.has("A scan") and b.has("B scan"), "one copy scans")
        finally:
            a.stop()
            b.stop()

    def test_sigterm_finishes_the_clip_in_hand(self):
        queue([f"c{i}" for i in range(6)])
        a = Pod(MEDIA, "A")
        b = None
        try:
            a.at("A clip start")
            b = Pod(MEDIA, "B")
            a.p.send_signal(signal.SIGTERM)
            self.assertEqual(a.p.wait(20), 0)
            started = {l.split()[-1] for _, l in a.lines if " clip start " in l}
            finished = {l.split()[-1] for _, l in a.lines if " clip done " in l}
            self.assertEqual(started, finished, "A finished every clip it had started")
            self.assertTrue(started <= done())
            end = time.time() + 30
            while len(done()) < 6 and time.time() < end:
                time.sleep(0.2)
            self.assertEqual(len(done()), 6, "B did the rest")
            b.at("B scan", timeout=10)  # the scan moved to B once A let go of the lock
        finally:
            a.stop()
            if b:
                b.stop()

    def test_killed_copys_clip_is_taken_after_its_claim_lapses(self):
        queue(["long0"])
        a = Pod(MEDIA, "A")
        b = Pod(MEDIA, "B")
        try:
            t0 = time.time()
            while not (a.has("clip start long0") or b.has("clip start long0")) and time.time() - t0 < 20:
                time.sleep(0.1)
            first, other = (a, b) if a.has("clip start long0") else (b, a)
            time.sleep(4)
            self.assertFalse(other.has("clip start long0"), "a live claim is renewed: nobody takes it")
            first.p.kill()
            first.p.wait(5)
            other.at("clip start long0", timeout=15)
        finally:
            a.stop()
            b.stop()


if __name__ == "__main__":
    unittest.main()
