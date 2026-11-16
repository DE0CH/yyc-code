#!/usr/bin/env python3
"""corpus.lock on Neon: the lock's connection can die while the block runs (an idle SSL connection cut after a
272-second LIHKG run, 2026-10-16: the job's work was done, the unlock raised, the Job failed). A dead connection has
released its advisory lock already, so the block's end must not fail; and pool connections keep TCP keepalives so an
idle lock connection isn't cut in the first place. TEST_DATABASE_URL, a throwaway database.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_lock_dead_connection.py
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402
import corpus  # noqa: E402


class DeadLockConnection(unittest.TestCase):
    def test_block_end_survives_a_dead_connection(self):
        with corpus.lock("t-dead", wait=False) as got:
            self.assertTrue(got)
            with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:  # the server cuts the lock's connection
                c.execute("""SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory'
                             AND objid = hashtext('t-dead')::oid AND pid <> pg_backend_pid()""")
        with corpus.lock("t-dead", wait=False) as again:  # released: the next run gets it
            self.assertTrue(again)

    def test_pool_connections_keep_alive(self):
        with corpus.pool().connection() as c:
            self.assertEqual(c.info.get_parameters().get("keepalives"), "1")


if __name__ == "__main__":
    unittest.main()
