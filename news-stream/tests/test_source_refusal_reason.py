#!/usr/bin/env python3
"""A source that refuses us with a reason in its body shows that reason on the monitor, not a bare
"<HTTPError 403: 'Forbidden'>" (live 16 Oct 2026: FSTB LinkedIn's r.jina.ai answered 403 with
"Anonymous access to domain www.linkedin.com blocked until …" in its JSON, and the monitor showed only Forbidden).
Local server, no network, no database.

    python3 tests/test_source_refusal_reason.py
"""
import gzip, http.server, json, os, sys, threading, unittest, urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sources  # noqa: E402

JINA = {"data": None, "code": 403, "name": "AbuseAlleviationError", "status": 40305,
        "message": "Anonymous access to domain www.linkedin.com blocked until Fri Oct 16 2026 09:08:12 GMT+0000 "
                   "(Coordinated Universal Time) due to previous abuse found on https://www.linkedin.com/in/x: …",
        "readableMessage": "AbuseAlleviationError: Anonymous access to domain www.linkedin.com blocked until Fri Oct 16 "
                           "2026 09:08:12 GMT+0000 (Coordinated Universal Time) due to previous abuse"}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        enc = None
        if self.path == "/jina":
            b, ct = json.dumps(JINA).encode(), "application/json"
        elif self.path == "/jina-gzip":  # what r.jina.ai sends us: we ask for gzip (sources.fetch), it compresses the error
            b, ct, enc = gzip.compress(json.dumps(JINA).encode()), "application/json; charset=utf-8", "gzip"
        else:
            b, ct = b"<html><body>" + b"x" * 5000 + b"</body></html>", "text/html"
        self.send_response(403)
        self.send_header("Content-Type", ct)
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


class Reason(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_json_reason_is_kept(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            sources.fetch(self.base + "/jina")
        self.assertEqual(c.exception.code, 403)
        self.assertIn("Anonymous access to domain www.linkedin.com blocked until Fri Oct 16 2026 09:08:12", repr(c.exception))

    def test_gzip_json_reason_is_kept(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            sources.fetch(self.base + "/jina-gzip")
        self.assertIn("Anonymous access to domain www.linkedin.com blocked until", repr(c.exception))

    def test_html_body_stays_short(self):
        """an HTML error page is no reason: the error stays the status line (never 5 KB of markup on the monitor)"""
        with self.assertRaises(urllib.error.HTTPError) as c:
            sources.fetch(self.base + "/html")
        self.assertEqual(c.exception.code, 403)
        self.assertLess(len(repr(c.exception)), 120)


if __name__ == "__main__":
    unittest.main()
