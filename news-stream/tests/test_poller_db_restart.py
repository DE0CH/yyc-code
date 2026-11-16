#!/usr/bin/env python3
"""The poller rides out a short Postgres restart (an OOM kill, a CNPG switchover) instead of crash-looping, with real
processes and a real Postgres of its own: the test starts a throwaway server (initdb in a temp dir, a free port) so it
can stop and start it. The poll itself is stubbed as in test_poller_handover.py.

    python3 tests/test_poller_db_restart.py      (needs the Postgres server binaries: pg_ctl, initdb)

Covers: a poller started while the database is down waits and retries (with backoff) instead of exiting, then takes the
lock and polls; a database restart mid-run leaves the poller running, it takes the lock again and keeps polling; when
another poller got the lock during the gap, the first one drains and exits 0 (never two pollers at once).
"""
import glob, os, shutil, signal, socket, subprocess, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the newest version with the SERVER binaries (a newer client-only package, e.g. postgresql-client-17 for pg_dump of a
# managed Postgres 17, has no pg_ctl/initdb), else pg_ctl on PATH
BIN = ([b for b in sorted(glob.glob("/usr/lib/postgresql/*/bin"), key=lambda b: int(b.split("/")[-2]) if b.split("/")[-2].isdigit() else 0)
        if all(os.path.exists(os.path.join(b, x)) for x in ("pg_ctl", "initdb"))]
       or [os.path.dirname(shutil.which("pg_ctl") or "/")])[-1]
if not all(os.path.exists(os.path.join(BIN, x)) for x in ("pg_ctl", "initdb")):
    sys.exit("no Postgres server binaries (pg_ctl, initdb)")
import psycopg  # noqa: E402


class Server:
    """a throwaway Postgres: initdb in a temp dir, trust auth, a free port"""
    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.data = os.path.join(self.dir, "data")
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        subprocess.run([os.path.join(BIN, "initdb"), "-D", self.data, "-A", "trust", "-U", "postgres"], check=True,
                       stdout=subprocess.DEVNULL)
        self.url = f"postgresql://postgres@127.0.0.1:{self.port}/postgres"

    def start(self):
        subprocess.run([os.path.join(BIN, "pg_ctl"), "-D", self.data, "-w", "-l", os.path.join(self.dir, "log"),
                        "-o", f"-p {self.port} -k {self.dir} -c listen_addresses=127.0.0.1", "start"], check=True,
                       stdout=subprocess.DEVNULL)

    def stop(self, mode="immediate"):  # immediate: what an OOM kill looks like to the clients (no goodbye)
        subprocess.run([os.path.join(BIN, "pg_ctl"), "-D", self.data, "-m", mode, "-w", "stop"], check=True,
                       stdout=subprocess.DEVNULL)

    def lock_holders(self):
        with psycopg.connect(self.url, autocommit=True) as c:
            return c.execute("""SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted
                                AND objid = hashtext('yyc poller')::oid""").fetchone()[0]


POD = r"""
import os, sys, tempfile, threading, time
sys.path.insert(0, sys.argv[2])
import app
name = sys.argv[1]
def poll_once():
    print(name, "poll start", flush=True)
    time.sleep(0.5)
    print(name, "poll done", flush=True)
d = tempfile.mkdtemp()
app.poll_once, app.POLL = poll_once, 1
app.LOCK_CHECK = 1
app.STARTED, app.ALIVE = d + "/started", d + "/alive"
app.main()  # exits the process itself after the drain
"""


class Pod:
    def __init__(self, name, url):
        env = dict(os.environ, DATABASE_URL=url, DATA_DIR=tempfile.mkdtemp(), TELEGRAM_BOT_TOKEN="")
        self.p = subprocess.Popen([sys.executable, "-c", POD, name, HERE], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
        self.lines = []  # (time, line)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.lines.append((time.time(), line.strip()))

    def at(self, text, timeout=60, after=0):
        """when a line containing text appeared after `after` (waits for it)"""
        end = time.time() + timeout
        while time.time() < end:
            for t, l in list(self.lines):
                if t >= after and text in l:
                    return t
            time.sleep(0.05)
        raise AssertionError(f"no {text!r} after {after} in {[l for _, l in self.lines][-40:]}")

    def has(self, text):
        return any(text in l for _, l in self.lines)

    def stop(self):
        if self.p.poll() is None:
            self.p.kill()
            self.p.wait()


class DbRestart(unittest.TestCase):
    def setUp(self):
        self.db = Server()
        self.pods = []

    def tearDown(self):
        for p in self.pods:
            p.stop()
        try:
            self.db.stop()
        except subprocess.CalledProcessError:
            pass
        shutil.rmtree(self.db.dir, ignore_errors=True)

    def pod(self, name):
        p = Pod(name, self.db.url)
        self.pods.append(p)
        return p

    def test_starts_while_database_down(self):
        a = self.pod("A")  # the database isn't up yet: the poller must wait, not exit
        a.at("database unavailable")
        time.sleep(3)
        self.assertIsNone(a.p.poll(), [l for _, l in a.lines])
        self.db.start()
        a.at("took poller lock")
        a.at("A poll done")
        self.assertIsNone(a.p.poll())

    def test_database_restart_mid_run(self):
        self.db.start()
        a = self.pod("A")
        a.at("A poll done")
        self.db.stop()
        t_down = time.time()
        time.sleep(4)  # a short outage: an OOM-killed Postgres comes back in seconds
        self.assertIsNone(a.p.poll(), [l for _, l in a.lines])
        self.db.start()
        a.at("retook poller lock", after=t_down)
        a.at("A poll done", after=time.time())  # polling goes on
        self.assertIsNone(a.p.poll())
        self.assertEqual(self.db.lock_holders(), 1)

    def test_lock_taken_by_another_poller_during_the_gap(self):
        self.db.start()
        a = self.pod("A")
        a.at("A poll done")
        b = self.pod("B")  # a second poller (a rolling deploy) waiting on the lock
        b.at("waiting for poller lock")
        a.p.send_signal(signal.SIGSTOP)  # A frozen across the restart: B gets the lock first
        self.db.stop()
        self.db.start()
        b.at("took poller lock")
        a.p.send_signal(signal.SIGCONT)
        self.assertEqual(a.p.wait(60), 0)  # A finds the lock gone to B: drains and exits
        self.assertTrue(a.has("poller lock lost"))
        b.at("B poll done", after=time.time())
        self.assertEqual(self.db.lock_holders(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
