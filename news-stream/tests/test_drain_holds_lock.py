#!/usr/bin/env python3
"""A singleton keeps its lock while it drains, and only lets go once its work in hand is done (real processes, a real
Postgres: TEST_DATABASE_URL; its tables are dropped and made again; the work itself is stubbed).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_drain_holds_lock.py

Seen on the cluster (2026-10-21 03:29 and 03:31 UTC, two poller rollouts): lock connections end themselves after
corpus.LOCK_IDLE (90 s) idle unless the holder speaks (the dead-holder protection, tests/test_lock_ghost.py). On SIGTERM
the old poller stopped its lock heartbeat (keep_lock ran until STOP) while it still finished the poll and the scoring
in hand; 90 s later Postgres ended the silent session, the lock was free, and the new pod took it about 85 s after the
SIGTERM while the old one was still draining: two pollers at once. podlock.Lock (whatsapp, images, transcribe's scan)
had the same pattern (its keeper also ran until the copy's stop event).

Here LOCK_IDLE is 2 s and every drain lasts 6 s: before the fix the waiting copy takes the lock mid-drain. Covers the
poller (app.py), images, whatsapp (closing the gateway) and transcribe's page scan (podlock), and that a holder that
is frozen (SIGSTOP: a ghost that never speaks again) still loses its lock within LOCK_IDLE seconds.
"""
import os, signal, subprocess, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import flags_on  # noqa: E402,F401  picture analysis and transcription on, so images/transcribe run their loops
URL = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATABASE_URL"] = URL
sys.path.insert(0, HERE)
import psycopg  # noqa: E402

with psycopg.connect(URL, autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
import corpus  # noqa: E402

corpus.db().execute("SELECT 1")  # the schema

DRAIN = 6  # seconds each copy's work in hand takes after SIGTERM: 3x the lock's idle timeout

PRELUDE = r"""
import os, sys, tempfile, threading, time
sys.path.insert(0, sys.argv[2])
name = sys.argv[1]
import corpus
corpus.LOCK_IDLE = 2  # a silent lock session ends after 2 s (90 s in production)
import podlock
podlock.CHECK, podlock.POLL = 0.5, 0.2
d = tempfile.mkdtemp()
DRAIN = %d
""" % DRAIN

POLLER = PRELUDE + r"""
import app
app.LOCK_CHECK = 0.5
def poll_once():  # a 10 s poll; once SIGTERM comes, what is left of it (and the scoring in hand) takes DRAIN s
    print(name, "poll start", flush=True)
    for _ in range(100):
        if app.STOP.is_set():
            time.sleep(DRAIN)
            break
        time.sleep(0.1)
    print(name, "poll done", flush=True)
app.poll_once, app.POLL = poll_once, 0.5
app.STARTED, app.ALIVE = d + "/started", d + "/alive"
app.main()
"""

IMAGES = PRELUDE + r"""
import images
def loop():
    while not images.STOP.is_set():
        print(name, "work", flush=True)
        images.STOP.wait(0.2)
    print(name, "finishing", flush=True)
    time.sleep(DRAIN)
images.scan_loop = images.work_loop = loop
images.WORKERS = 1
images.MODEL = "test/model"  # IMAGE_MODEL has no default
images.STARTED, images.ALIVE = d + "/started", d + "/alive"
images.main()
"""

WHATSAPP = PRELUDE + r"""
import subprocess
import whatsapp_alerts as w
def start_gateway():  # a gateway that takes DRAIN s to close its connection after SIGTERM
    w.held["proc"] = subprocess.Popen(["sh", "-c", f"trap 'sleep {DRAIN}; echo {name} drained; exit 0' TERM; "
                                       "while :; do sleep 0.1; done"])
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
def scan_once():  # a 10 s scan; once SIGTERM comes, what is left of it takes DRAIN s
    print(name, "work", flush=True)
    for _ in range(100):
        if media.STOP.is_set():
            time.sleep(DRAIN)
            print(name, "scan drained", flush=True)
            break
        time.sleep(0.1)
    return 1
media.scan_once = scan_once
media.work_loop = lambda: None
media.STARTED, media.ALIVE = d + "/started", d + "/alive"
media.main()
"""


class Pod:
    def __init__(self, script, name):
        env = dict(os.environ, DATABASE_URL=URL, DATA_DIR=tempfile.mkdtemp(), TELEGRAM_BOT_TOKEN="")
        self.p = subprocess.Popen([sys.executable, "-c", script, name, HERE], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self.name, self.lines, self.exited = name, [], None
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.lines.append((time.time(), line.rstrip()))
        self.p.wait()
        self.exited = time.time()

    def at(self, text, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            for t, l in list(self.lines):
                if text in l:
                    return t
            time.sleep(0.05)
        raise AssertionError(f"{self.name}: no {text!r} in {[l for _, l in self.lines][-40:]}")

    def has(self, text):
        return any(text in l for _, l in list(self.lines))

    def wait_exit(self, timeout=30):
        self.p.wait(timeout)
        end = time.time() + 5
        while self.exited is None and time.time() < end:
            time.sleep(0.05)
        return self.exited

    def stop(self):
        try:
            os.killpg(self.p.pid, signal.SIGKILL)  # the copy and its children (the stub gateway)
        except ProcessLookupError:
            pass
        self.p.wait(10)


def holders(name):
    with psycopg.connect(URL, autocommit=True) as c:
        return c.execute("""SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted
                            AND objid = hashtext(%s)::oid""", (name,)).fetchone()[0]


class Drain:
    script = lock = took = waiting = None
    drained = "drained"  # the last line of the old copy's drain

    def test_lock_held_until_the_drain_is_done(self):
        a = Pod(self.script, "A")
        pods = [a]
        try:
            a.at(self.took)
            a.at("A " + self.work)
            b = Pod(self.script, "B")  # a rollout's new pod, started before the old one is told to stop
            pods.append(b)
            b.at(self.waiting)
            time.sleep(0.5)
            t_term = time.time()
            a.p.send_signal(signal.SIGTERM)
            t_exit = a.wait_exit(DRAIN + 20)
            self.assertEqual(a.p.returncode, 0)
            t_drained = a.at(self.drained)
            self.assertGreaterEqual(t_drained - t_term, DRAIN - 1, "the drain really took DRAIN s (> LOCK_IDLE)")
            t_b = b.at(self.took, timeout=15)
            self.assertGreaterEqual(t_b, t_drained, f"B took the lock {t_drained - t_b:.1f} s before A had drained")
            self.assertGreaterEqual(t_b, t_exit - 0.5, "B took the lock while A was still running")
            self.assertLess(t_b - t_exit, 3, "no gap: B takes the lock as soon as A exits")
            self.assertEqual(holders(self.lock), 1)
        finally:
            for p in pods:
                p.stop()

    def test_a_frozen_holder_still_loses_the_lock(self):
        """the dead-holder protection stays: a holder that never speaks again (SIGSTOP; as a pod whose network path
        died) loses the lock within LOCK_IDLE seconds and the next copy takes it"""
        a = Pod(self.script, "A")
        pods = [a]
        try:
            a.at(self.took)
            b = Pod(self.script, "B")
            pods.append(b)
            b.at(self.waiting)
            time.sleep(0.5)
            os.kill(a.p.pid, signal.SIGSTOP)
            t = time.time()
            t_b = b.at(self.took, timeout=15)
            self.assertLess(t_b - t, 2 + 4, "freed within LOCK_IDLE (2 s) + a check or two")
        finally:
            if a.p.poll() is None:
                os.kill(a.p.pid, signal.SIGCONT)
            for p in pods:
                p.stop()


class Poller(Drain, unittest.TestCase):
    script, lock, took, waiting, work = POLLER, "yyc poller", "took poller lock", "waiting for poller lock", "poll start"


class Images(Drain, unittest.TestCase):
    script, lock, took, waiting, work = IMAGES, "yyc images", "took images lock", "waiting for images lock", "work"


class WhatsApp(Drain, unittest.TestCase):
    script, lock, took, waiting, work = WHATSAPP, "yyc whatsapp", "took whatsapp lock", "waiting for whatsapp lock", "work"


class TranscribeScan(Drain, unittest.TestCase):
    script, lock, took, waiting, work = MEDIA, "yyc transcribe", "took transcribe lock", "waiting for transcribe lock", "work"


class StageTwoDrain(unittest.TestCase):
    """the poller's drain is bounded (deploy/apps/poller.yaml terminationGracePeriodSeconds): a stage-2 call runs up to
    6 min (Gemini 3.8 Flash at effort xhigh: 362 s measured on 20 Oct), so once SIGTERM has come no NEW stage-2 call
    starts and no second try; the posts not confirmed leave the result (not stored, not filed: due for the next pod)"""
    def setUp(self):
        import streams
        self.streams, self.saved = streams, (streams._ask, streams.filed_streams)
        streams.STOP.clear()

    def tearDown(self):
        self.streams._ask, self.streams.filed_streams = self.saved
        self.streams.STOP.clear()

    def test_no_new_stage_two_call_while_draining(self):
        st = self.streams
        calls = []

        def ask(streams, posts, purpose, model=None, checks=None):
            calls.append(len(posts))
            st.STOP.set()  # SIGTERM arrives during the first stage-2 call
            return {j: {"sentiment": None, "labels": [], "streams": {"CEHK": {"score": 80, "reason": "r", "category": None}}}
                    for j in range(len(posts))}
        st._ask, st.filed_streams = ask, lambda streams, posts: {}
        n = st.CONFIRM_BATCH * 2 + 3
        posts = [{"id": i, "url": f"https://x/{i}"} for i in range(n)]
        res = {i: {"sentiment": None, "labels": [], "tag_model": "h",
                   "streams": {"CEHK": {"score": 60, "reason": "h", "category": None}}} for i in range(n)}
        out = st.confirm([{"id": "CEHK"}], posts, res, "scoring")
        self.assertEqual(calls, [st.CONFIRM_BATCH], "one stage-2 call: the one in hand when SIGTERM came")
        self.assertEqual(sorted(out), list(range(st.CONFIRM_BATCH)), "the rest stay due")

    def test_no_second_try_while_draining(self):
        st, ai = self.streams, __import__("ai")
        st._ask = self.saved[0]
        tries = []

        def jsonc(*a, **k):
            tries.append(k.get("model"))
            st.STOP.set()
            raise ValueError("not JSON")
        saved, ai.jsonc = ai.jsonc, jsonc
        try:
            self.assertIsNone(st._ask([{"id": "CEHK", "name": "CE", "brief": "b"}], [{"id": 1, "url": "u", "title": "t", "text": "x"}],
                                      "scoring_verify", model=st.CONFIRM_MODEL, checks={0: ["CEHK"]}))
        finally:
            ai.jsonc = saved
        self.assertEqual(len(tries), 1)


class Grace(unittest.TestCase):
    """k8s never SIGKILLs a draining singleton: each grace period covers its longest drain"""
    def grace(self, name):
        import re
        y = open(os.path.join(HERE, "deploy", "apps", name + ".yaml")).read()
        return int(re.search(r"terminationGracePeriodSeconds: (\d+)", y).group(1))

    def test_poller(self):
        import streams
        stage1 = 2 * 180  # Haiku: two tries (non-JSON) at ai.jsonc's default timeout
        stage2 = max(o.get("timeout", 180) for o in streams.CONFIRM_OPTIONS.values())  # one call while draining
        titles, filing = 180, 60
        self.assertGreaterEqual(self.grace("poller"), stage1 + stage2 + titles + filing)

    def test_whatsapp(self):
        import inspect, whatsapp_alerts
        call = inspect.signature(whatsapp_alerts.gateway).parameters["timeout"].default
        self.assertGreater(self.grace("whatsapp"), call + 20 + 10)  # the call in hand + closing the gateway + exit


if __name__ == "__main__":
    unittest.main()
