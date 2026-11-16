#!/usr/bin/env python3
"""A planned primary move (a CloudNativePG switchover) as the news stream sees it: every connection cut, new ones
refused for a few seconds, then the new primary at the same address. Statements and db().run transactions caught by
it wait and run again (corpus.retrying), so callers see a slower answer, never an error, and the work lands once.
A TCP proxy in front of TEST_DATABASE_URL plays the switchover.

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5433/news python3 tests/test_db_switchover.py
"""
import os, socket, sys, threading, time, unittest
from urllib.parse import urlsplit, urlunsplit

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
REAL = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")


class Proxy:
    """cut(): every connection through it ends and new ones are refused; restore(): they pass again"""
    def __init__(self, url):
        u = urlsplit(url)
        self.target = (u.hostname or "127.0.0.1", u.port or 5432)
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(64)
        self.port = self.srv.getsockname()[1]
        self.down, self.socks, self.lock = False, [], threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()
        netloc = (f"{u.username}@" if u.username else "") + f"127.0.0.1:{self.port}"
        self.url = urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))

    def _accept(self):
        while True:
            c, _ = self.srv.accept()
            if self.down:
                c.close()
                continue
            try:
                s = socket.create_connection(self.target)
            except OSError:
                c.close()
                continue
            with self.lock:
                self.socks += [c, s]
            for a, b in ((c, s), (s, c)):
                threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

    @staticmethod
    def _pipe(a, b):
        try:
            while data := a.recv(65536):
                b.sendall(data)
        except OSError:
            pass
        for x in (a, b):
            try:
                x.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def cut(self):
        self.down = True
        with self.lock:
            for x in self.socks:
                try:
                    x.shutdown(socket.SHUT_RDWR)
                    x.close()
                except OSError:
                    pass
            self.socks = []

    def restore(self):
        self.down = False

    def restore_after(self, seconds):
        threading.Timer(seconds, self.restore).start()


PROXY = Proxy(REAL)
os.environ["DATABASE_URL"] = PROXY.url
import psycopg  # noqa: E402
import corpus  # noqa: E402


def direct(sql, *args):
    with psycopg.connect(REAL, autocommit=True) as c:
        cur = c.execute(sql, args)
        return cur.fetchall() if cur.description else []


class Switchover(unittest.TestCase):
    def setUp(self):
        direct("DROP TABLE IF EXISTS sw_t")
        direct("CREATE TABLE sw_t (id int PRIMARY KEY, n int NOT NULL DEFAULT 0)")
        corpus.db().execute("SELECT 1")  # the pool holds a connection, cut below

    def test_a_statement_during_the_gap_waits_and_lands_once(self):
        PROXY.cut()
        PROXY.restore_after(3)
        t = time.monotonic()
        corpus.db().execute("INSERT INTO sw_t (id) VALUES (1)")
        self.assertGreaterEqual(time.monotonic() - t, 2.9)
        self.assertLess(time.monotonic() - t, 8, "it waited long after the database was back")
        self.assertEqual(direct("SELECT count(*) FROM sw_t")[0][0], 1)

    def test_a_transaction_cut_in_the_middle_runs_again_once(self):
        tries = []

        def work(c):
            tries.append(1)
            c.execute("INSERT INTO sw_t (id) VALUES (1)")
            if len(tries) == 1:
                PROXY.cut()  # the old primary stops: this try never commits
                PROXY.restore_after(1.5)
            c.execute("UPDATE sw_t SET n = n + 1 WHERE id = 1")
            return "done"
        self.assertEqual(corpus.db().run(work), "done")
        self.assertEqual(len(tries), 2)
        self.assertEqual(direct("SELECT n FROM sw_t WHERE id = 1")[0][0], 1)

    def test_a_real_error_is_not_retried(self):
        t = time.monotonic()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            corpus.db().execute("INSERT INTO sw_t (id) VALUES (1), (1)")
        self.assertLess(time.monotonic() - t, 2)

    def test_a_lock_holder_survives(self):
        with corpus.lock("sw-test") as got:
            self.assertTrue(got)
        PROXY.cut()
        PROXY.restore_after(2)
        with corpus.lock("sw-test") as got:  # connecting waits for the database
            self.assertTrue(got)

    def tearDown(self):
        PROXY.restore()


if __name__ == "__main__":
    unittest.main()
