#!/usr/bin/env python3
"""A rollout that adds a column to `posts` must not break the outgoing pod: its pooled connections ran `SELECT p.*`
queries often enough for psycopg to prepare them on the server, and Postgres then refuses the cached plan
("cached plan must not change result type", FeatureNotSupported) once the new pod's schema adds a column. The pool's
connections must not keep server-side prepared statements. Throwaway database (TEST_DATABASE_URL; its tables are
dropped and made again).

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_prepared_plans.py
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
import corpus  # noqa: E402

Q = "SELECT p.* FROM posts p WHERE p.id = %s"


class PreparedPlans(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("""INSERT INTO posts (url, source, title, published, day)
                               VALUES ('https://example.com/a', 't', 'a', '2026-10-14T00:00:00+00:00', '2026-10-14')
                               ON CONFLICT DO NOTHING""")
        self.pid = corpus.db().execute("SELECT id FROM posts WHERE url = 'https://example.com/a'").fetchone()[0]

    def tearDown(self):
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
            c.execute("ALTER TABLE posts DROP COLUMN IF EXISTS rollout_new_col")

    def add_column(self):
        """the new pod's schema change, from another connection"""
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
            c.execute("ALTER TABLE posts ADD COLUMN rollout_new_col text")

    def test_pooled_connection_survives_added_column(self):
        with corpus.pool().connection() as conn:
            for _ in range(10):  # past psycopg's default prepare_threshold (5)
                conn.execute(Q, (self.pid,)).fetchall()
            self.add_column()
            row = conn.execute(Q, (self.pid,)).fetchone()  # raised FeatureNotSupported with prepared plans
            self.assertIsNotNone(row)

    def test_db_execute_survives_added_column(self):
        for _ in range(3 * corpus.POOL_MAX):
            corpus.db().execute(Q, (self.pid,))
        self.add_column()
        for _ in range(3 * corpus.POOL_MAX):  # every pooled connection, whichever the pool hands out
            r = corpus.db().execute(Q, (self.pid,)).fetchone()
            self.assertIn("rollout_new_col", r.keys())

    def test_pool_prepares_nothing(self):
        with corpus.pool().connection() as conn:
            for _ in range(10):
                conn.execute(Q, (self.pid,)).fetchall()
            self.assertEqual(conn.execute("SELECT count(*) FROM pg_prepared_statements").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
