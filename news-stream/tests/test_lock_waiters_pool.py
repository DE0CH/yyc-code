#!/usr/bin/env python3
"""corpus.lock never holds one of the pool's connections (fulltext run of 20 Oct 2026, 01:22 UTC: IPRoyal answered 402,
16 host threads called resi.mark at once; each waited in pg_advisory_lock('proxy_out') on a pool connection, so the
holder found no connection for its kv read, and the run died with psycopg_pool.PoolTimeout after 30 s). Many threads
waiting on one lock, each doing pool statements inside it, all finish. TEST_DATABASE_URL, a throwaway database.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_lock_waiters_pool.py
"""
import os, sys, threading, time, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DB_POOL_MAX"] = "3"
import corpus, notify, resi  # noqa: E402

notify.deyao = lambda text: True


def crowd(fn, n=8):
    errors, threads = [], []

    def run():
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    for _ in range(n):
        threads.append(threading.Thread(target=run))
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    return errors, time.time() - t0


class LockWaiters(unittest.TestCase):
    def test_waiters_leave_the_pool_free(self):
        self.assertEqual(corpus.POOL_MAX, 3)

        def one():
            with corpus.lock("t-crowd"):
                corpus.kv_get("t-crowd")
                time.sleep(0.1)
                corpus.kv_set("t-crowd", {"at": time.time()})
        errors, took = crowd(one)
        self.assertEqual(errors, [])
        self.assertLess(took, 25)

    def test_proxy_out_marked_by_many_threads(self):
        corpus.kv_set("proxy_out", None)
        corpus.kv_set("proxy_out_told", None)
        errors, took = crowd(lambda: resi.mark("402 Payment Required", job="fulltext_fetch"), n=16)
        self.assertEqual(errors, [])
        self.assertTrue(corpus.kv_get("proxy_out"))
        self.assertLess(took, 25)
        corpus.kv_set("proxy_out", None)


if __name__ == "__main__":
    unittest.main()
