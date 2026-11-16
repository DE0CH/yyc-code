#!/usr/bin/env python3
"""The pool's check on Neon: every checkout tested its connection with a round trip (~24 ms from the cluster to Neon,
2026-10-16), doubling the time of every statement run outside a transaction; the chatty scoring and ingest paths
slowed several-fold after the switch. A connection returned moments ago (under CHECK_IDLE) is handed out as it is;
one idle longer is still tested, so one that died with a Postgres restart is replaced. TEST_DATABASE_URL.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_pool_check.py
"""
import os, sys, time, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402
from psycopg_pool import ConnectionPool  # noqa: E402
import corpus  # noqa: E402


CALLS = [0]
_real = ConnectionPool.check_connection


def _counting(conn):
    CALLS[0] += 1
    return _real(conn)


ConnectionPool.check_connection = staticmethod(_counting)  # before the pool exists: whatever check it binds counts


class PoolCheck(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("SELECT 1")
        CALLS[0] = 0

    @property
    def calls(self):
        return CALLS[0]

    def test_a_busy_connection_is_not_tested_each_time(self):
        for _ in range(20):
            corpus.db().execute("SELECT 1")
        self.assertLessEqual(self.calls, 1)

    def test_an_idle_connection_is_tested_and_a_dead_one_replaced(self):
        corpus.db().execute("SELECT 1")
        time.sleep(corpus.CHECK_IDLE + 0.5)
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:  # a restart: the server cuts them all
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='yyc-news-stream' "
                      "AND pid <> pg_backend_pid()")
        self.assertEqual(corpus.db().execute("SELECT 1").fetchone()[0], 1)
        self.assertGreaterEqual(self.calls, 1)


if __name__ == "__main__":
    unittest.main()
