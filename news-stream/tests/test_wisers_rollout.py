#!/usr/bin/env python3
"""A rollout of the WiseSearch relay fails no request and logs in no second time (Deyao, 2026-10-16: "No downtime").

    CHROME=/usr/bin/google-chrome python3 tests/test_wisers_rollout.py

What Kubernetes does in a RollingUpdate (maxSurge 1, maxUnavailable 0), played with two relay processes sharing one
data directory (the PVC: the saved session and the leader lock) in front of the fake WisersOne (fake_wisers.py):
relay A serves; B starts, waits for the lock, reports ready (/healthz) and joins the pool; A gets SIGTERM and stays in
the pool for one more second (endpoint propagation); A drains and exits; B takes the lock and the saved session.
Three clients search the whole time through a round-robin proxy over the pool (the Service), as the page does: api/search,
then api/job/<id> until the job ends; a fourth starts a 10 s search just before A's SIGTERM, which A finishes while it
drains and B answers for from the job's file on the shared volume (searches are jobs, Deyao 2026-10-20).
- every search answers ok (none fails, none times out); the 10 s search too, with one Wisers search;
- Wisers sees exactly one login (A's first; B takes over A's saved session) and never two browsers at once
  (max_inflight 1: only the lock holder drives Wisers);
- A exits within its grace period; while draining, its /healthz answers 503;
- after the rollout B's stats port says the session is held (session.json, Deyao 2026-10-21), with no login of its own.
"""
import json, os, signal, socket, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_wisers import FakeWisers, new_ns  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wisers-rollout-")  # the pods' log files only
NS = new_ns("rollout")  # both "pods" share it, as both pods share the database
W = FakeWisers(captcha=False, latency=0.4)
POOL = []  # backend ports the "Service" sends to


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Proxy(BaseHTTPRequestHandler):
    n = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        Proxy.n += 1
        port = POOL[Proxy.n % len(POOL)]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{self.path}", timeout=70) as r:
                code, body = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, body = e.code, e.read()
        except Exception as e:  # a connection refused: the Service sent to a pod that is gone
            code, body = 599, str(e).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


STATS = {}


def start_relay(name):
    port = free_port()
    STATS[name] = free_port()
    # the health check is off: its call beside a search would read as two browsers (test_wisers_health.py has it)
    env = dict(os.environ, **W.env(NS), WISERS_DRAIN_SECS="3", PYTHONUNBUFFERED="1", WISERS_PROBE_SECS="0",
               WISERS_STATS_PORT=str(STATS[name]))
    log = open(os.path.join(TMP, name + ".log"), "w")  # noqa: SIM115
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "wisers", "relay.py"), "--port", str(port),
                          "--prefix", "/app/wisers/"], env=env, stdout=log, stderr=subprocess.STDOUT)
    return p, port


def health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def wait_ready(port, secs=60):
    t = time.time()
    while time.time() - t < secs:
        if health(port) == 200:
            return True
        time.sleep(0.2)
    return False


class Rollout(unittest.TestCase):
    def test_rollout(self):
        W.stale_state(NS)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        a, pa = start_relay("a")
        self.assertTrue(wait_ready(pa), "A ready")
        POOL.append(pa)
        results, stop = [], threading.Event()

        base = f"http://127.0.0.1:{proxy.server_port}/app/wisers/api/"

        def one(q):
            """a search to its end (start, then the job until done): (status, ok, seconds, what)"""
            t0 = time.time()
            try:
                with urllib.request.urlopen(base + f"search?q={q}&period=today", timeout=40) as r:
                    d = json.loads(r.read())
                while d.get("job", {}).get("state") == "running" and time.time() - t0 < 120:
                    with urllib.request.urlopen(base + f"job/{d['job']['id']}?wait=20", timeout=40) as r:
                        d = json.loads(r.read())
                return (200, d.get("job", {}).get("state") == "ok", round(time.time() - t0, 1), q if d.get("result") else d)
            except urllib.error.HTTPError as e:
                return (e.code, False, round(time.time() - t0, 1), e.read()[:200])
            except Exception as e:
                return (None, False, round(time.time() - t0, 1), str(e))

        def client(i):
            n = 0
            while not stop.is_set():
                n += 1
                results.append(one(f"c{i}-{n}"))
        threads = [threading.Thread(target=client, args=(i,), daemon=True) for i in range(3)]
        for t in threads:
            t.start()
        t0 = time.time()
        while len(results) < 6 and time.time() - t0 < 60:
            time.sleep(0.2)
        self.assertGreaterEqual(len(results), 6, results)
        self.assertEqual(W.S["logins"], 1, "A logged in once")

        b, pb = start_relay("b")  # maxSurge 1: the new pod first
        self.assertTrue(wait_ready(pb), "B ready while A still holds the lock")
        POOL.append(pb)
        time.sleep(2)
        slow, n_slow = [], W.S["searches"]
        st = threading.Thread(target=lambda: slow.append(one("slow10-rollout")), daemon=True)
        st.start()
        time.sleep(1.5)  # A has it (A holds the lock; B's worker waits)
        a.send_signal(signal.SIGTERM)  # maxUnavailable 0: the old pod stops only now
        time.sleep(0.3)
        self.assertEqual(health(pa), 503, "a draining relay is not ready")
        time.sleep(0.7)
        POOL.remove(pa)  # the endpoint is gone a moment after SIGTERM
        self.assertEqual(a.wait(timeout=60), 0, "A exits cleanly within its grace period")
        st.join(timeout=120)
        self.assertEqual(len(slow), 1)
        self.assertTrue(slow[0][1], f"the search in flight at SIGTERM: {slow[0]}")
        time.sleep(5)
        stop.set()
        for t in threads:
            t.join(timeout=90)
        with urllib.request.urlopen(f"http://127.0.0.1:{STATS['b']}/stats", timeout=10) as r:
            ss = json.loads(r.read())["session"]
        self.assertEqual((ss["state"], ss["logins"], ss["pod"] is not None), ("held", 1, True), ss)
        b.send_signal(signal.SIGTERM)
        b.wait(timeout=60)
        bad = [r for r in results if not (r[0] == 200 and r[1])]
        self.assertEqual(bad, [], f"failed requests during the rollout (of {len(results)})")
        self.assertGreater(len(results), 15)
        self.assertEqual(W.S["logins"], 1, "B took over A's saved session: no second login")
        self.assertEqual(W.S["max_inflight"], 1, "only the lock holder drives Wisers")
        print(f"{len(results)} searches, slowest {max(r[2] for r in results)} s, logins {W.S['logins']}")


if __name__ == "__main__":
    unittest.main()
