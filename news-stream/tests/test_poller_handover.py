#!/usr/bin/env python3
"""The poller's gapless handover with real processes and a real Postgres (TEST_DATABASE_URL; its tables are dropped and
made again), the poll itself stubbed (a 3 s poll + a 2 s after_poll, no source is fetched).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_poller_handover.py

Covers what a rolling deploy does: a second poller waits on the lock while the first works; SIGTERM mid-poll lets the
first finish its poll and its after_poll, then it exits and the second takes the lock within a second; SIGTERM to a
poller still waiting exits at once; a SIGKILLed poller's lock is released (the second takes over).
"""
import os, signal, subprocess, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(URL, autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")

POD = r"""
import os, sys, tempfile, threading, time
sys.path.insert(0, sys.argv[2])
import app
name = sys.argv[1]
def poll_once():
    print(name, "poll start", flush=True)
    time.sleep(3)
    def after():
        time.sleep(2)
        print(name, "after_poll done", flush=True)
    app.state["after"] = threading.Thread(target=after, daemon=True)
    app.state["after"].start()
    print(name, "poll done", flush=True)
d = tempfile.mkdtemp()
app.poll_once, app.POLL = poll_once, 1
app.STARTED, app.ALIVE = d + "/started", d + "/alive"
app.main()  # exits the process itself after the drain
"""


class Pod:
    def __init__(self, name):
        env = dict(os.environ, DATABASE_URL=URL, DATA_DIR=tempfile.mkdtemp(), TELEGRAM_BOT_TOKEN="")
        self.p = subprocess.Popen([sys.executable, "-c", POD, name, HERE], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
        self.lines = []  # (time, line)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.lines.append((time.time(), line.strip()))

    def at(self, text, timeout=30):
        """when a line containing text appeared (waits for it)"""
        end = time.time() + timeout
        while time.time() < end:
            for t, l in self.lines:
                if text in l:
                    return t
            time.sleep(0.05)
        raise AssertionError(f"no {text!r} in {[l for _, l in self.lines]}")

    def has(self, text):
        return any(text in l for _, l in self.lines)


class Handover(unittest.TestCase):
    def test_rolling_deploy(self):
        a = Pod("A")
        a.at("took poller lock")
        a.at("A poll start")
        b = Pod("B")  # the new pod of a deploy: started before the old one is told to stop
        b.at("waiting for poller lock")
        time.sleep(1)
        self.assertFalse(b.has("took poller lock"))
        a.p.send_signal(signal.SIGTERM)  # mid-poll
        self.assertEqual(a.p.wait(30), 0)
        t_done, t_after, t_drained = a.at("A poll done"), a.at("A after_poll done"), a.at("drained")
        self.assertLessEqual(t_done, t_after)
        self.assertLessEqual(t_after, t_drained)
        t_b = b.at("took poller lock")
        self.assertGreaterEqual(t_b, t_drained)
        self.assertLess(t_b - t_drained, 1.5)  # no gap
        self.assertFalse(any("A poll start" in l for _, l in a.lines[[l for _, l in a.lines].index("A poll done"):]))

        c = Pod("C")  # a pod still waiting is stopped at once
        c.at("waiting for poller lock")
        time.sleep(0.5)
        c.p.send_signal(signal.SIGTERM)
        self.assertEqual(c.p.wait(5), 0)
        self.assertFalse(c.has("took poller lock"))

        d = Pod("D")
        d.at("waiting for poller lock")
        b.p.kill()  # a hard-killed pod: Postgres drops its lock
        b.p.wait(5)
        d.at("took poller lock", timeout=10)
        d.p.send_signal(signal.SIGTERM)
        self.assertEqual(d.p.wait(30), 0)


if __name__ == "__main__":
    unittest.main()
