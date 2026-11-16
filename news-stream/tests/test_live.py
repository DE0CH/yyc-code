#!/usr/bin/env python3
"""Live updates (live.py, corpus.SCHEMA `live_*` triggers): every change a page shows is announced on channel
yyc_live as its page's topic, and live.py sends the topics to the site's LiveHub. On a throwaway database
(TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_live.py
"""
import json, os, sys, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
import corpus  # noqa: E402

corpus.db().execute("SELECT 1")  # the schema


def heard(fn):
    """the topics announced while fn(c) runs on its own connection"""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as listen, \
            psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        listen.execute("LISTEN yyc_live")
        fn(c)
        return [n.payload for n in listen.notifies(timeout=0.5)]


def new_post(c, url, **kw):
    cols = dict(url=url, title="t", seen="2026-10-15T10:00:00+08:00", day="2026-10-15", **kw)
    return c.execute(f"INSERT INTO posts ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                     list(cols.values())).fetchone()[0]


class Triggers(unittest.TestCase):
    def test_what_a_page_shows(self):
        ids = {}
        def a(c):
            ids["p"] = new_post(c, "https://x/1")
        self.assertEqual(heard(a), [])  # a new post alone changes no page (串流 changes with its stream item)
        p = ids["p"]
        self.assertEqual(heard(lambda c: new_post(c, "https://x/1#c", thread="https://x/1")), [f"item:{p}"])  # a comment
        self.assertEqual(heard(lambda c: c.execute("UPDATE posts SET text='全文' WHERE id=%s", (p,))), [f"item:{p}"])
        self.assertEqual(heard(lambda c: c.execute("UPDATE posts SET hay='x' WHERE id=%s", (p,))), [])  # search columns only
        # 串流's day is the notification's (item.seen), not the publish day (Deyao, 2026-10-20)
        self.assertEqual(heard(lambda c: c.execute(
            "INSERT INTO stream_items (day, stream, url, item) VALUES ('2026-10-14', 'CEHK', 'https://x/1', "
            "'{\"seen\": \"2026-10-15T00:10:00+08:00\"}')")),
            ["feed:2026-10-15", "scores:2026-10-15"])
        for sql in ("INSERT INTO scored (post_id, hash, at) VALUES (%s, 'h', 'now')",
                    "INSERT INTO scores (stream, post_id, score) VALUES ('CEHK', %s, 70)",
                    "INSERT INTO tags (profile, post_id, relevant) VALUES ('CEHK', %s, 1)"):
            self.assertEqual(heard(lambda c: c.execute(sql, (p,))), [f"item:{p}"], sql)


class Sender(unittest.TestCase):
    def test_batches_to_the_livehub(self):
        got = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                got.append((self.headers.get("X-Live-Secret"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(200); self.end_headers(); self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        import live
        live.URL, live.SECRET = f"http://127.0.0.1:{srv.server_port}/live/notify", "s3cret"
        stop = threading.Event()
        t = threading.Thread(target=live.run, args=(stop,), daemon=True)
        t.start()
        import time
        time.sleep(1)
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
            p = new_post(c, "https://x/2")
            c.execute("UPDATE posts SET text='a' WHERE id=%s", (p,))
            c.execute("UPDATE posts SET text='b' WHERE id=%s", (p,))
        for _ in range(50):
            if got:
                break
            time.sleep(0.1)
        stop.set()
        srv.shutdown()
        self.assertEqual(got, [("s3cret", {"topics": [f"item:{p}"]})])  # two changes, one topic, one POST


if __name__ == "__main__":
    unittest.main()
