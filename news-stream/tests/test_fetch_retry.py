#!/usr/bin/env python3
"""Transient network errors are retried with backoff (sources.fetch, every reader's GET; Deyao, 2026-10-20: "make them
more resilient"): a reset or refused connection, a proxy tunnel's 502/503/504, an HTTP 502/503/504 answer, up to
RETRIES more tries (BACKOFF seconds between them); a timeout once more. Never retried: a 4xx (403/404/429: a refusal
or a rate limit, asked again only by the next run), IPRoyal's 402 (out of traffic, resi.ProxyOut), a POST. A local
server, no network.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_fetch_retry.py
"""
import http.server, os, socket, sys, threading, time, unittest, urllib.error

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import sources  # noqa: E402

PLAN = {}  # path → list of answers still to give: an int status, "reset", "slow", or "ok"
SEEN = {}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def answer(self):
        SEEN[self.path] = SEEN.get(self.path, 0) + 1
        todo = PLAN.get(self.path) or ["ok"]
        a = todo.pop(0) if len(todo) > 1 else todo[0]
        if a == "reset":
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
            self.connection.close()
            return
        if a == "slow":
            time.sleep(1.5)
            a = "ok"
        body = b"<html><body>the page</body></html>" if a == "ok" else b"busy"
        self.send_response(200 if a == "ok" else a)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = answer


class Retry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        cls.saved = sources.BACKOFF
        sources.BACKOFF = (0.05, 0.1)

    @classmethod
    def tearDownClass(cls):
        sources.BACKOFF = cls.saved
        cls.srv.shutdown()

    def setUp(self):
        PLAN.clear(); SEEN.clear()

    def test_503_then_ok(self):
        PLAN["/a"] = [503, 502, "ok"]
        self.assertIn("the page", sources.fetch(self.base + "/a"))
        self.assertEqual(SEEN["/a"], 3)

    def test_reset_then_ok(self):
        PLAN["/b"] = ["reset", "ok"]
        self.assertIn("the page", sources.fetch(self.base + "/b"))
        self.assertEqual(SEEN["/b"], 2)

    def test_gives_up_after_retries(self):
        PLAN["/c"] = [504]
        with self.assertRaises(urllib.error.HTTPError):
            sources.fetch(self.base + "/c")
        self.assertEqual(SEEN["/c"], 1 + sources.RETRIES)

    def test_refusals_are_not_retried(self):
        for code in (403, 404, 429):
            PLAN[f"/{code}"] = [code]
            with self.assertRaises(urllib.error.HTTPError):
                sources.fetch(self.base + f"/{code}")
            self.assertEqual(SEEN[f"/{code}"], 1, code)

    def test_post_is_not_retried(self):
        PLAN["/p"] = [503, "ok"]
        with self.assertRaises(urllib.error.HTTPError):
            sources.fetch(self.base + "/p", data=b"x=1")
        self.assertEqual(SEEN["/p"], 1)

    def test_a_timeout_once_more(self):
        PLAN["/t"] = ["slow"]
        with self.assertRaises(Exception):
            sources.fetch(self.base + "/t", timeout=0.3)
        time.sleep(3.5)  # the server's slow answers finish
        self.assertEqual(SEEN["/t"], 2)

    def test_transient(self):
        t = sources.transient
        self.assertTrue(t(urllib.error.URLError(OSError("Tunnel connection failed: 504 Gateway Timeout"))))
        self.assertTrue(t(ConnectionResetError(104, "Connection reset by peer")))
        self.assertFalse(t(urllib.error.URLError(OSError("Tunnel connection failed: 402 Payment Required"))))
        self.assertFalse(t(urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))))


if __name__ == "__main__":
    unittest.main()
