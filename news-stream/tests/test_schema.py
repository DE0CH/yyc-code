#!/usr/bin/env python3
"""corpus._apply_schema: an empty database gets SCHEMA; an existing one gets only the numbered migrations after its
kv 'schema_version', each on its own (indexes CONCURRENTLY), never the whole SCHEMA (its ALTER TABLEs deadlocked
against live traffic), on a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_schema.py
"""
import hashlib, os, re, subprocess, sys, threading, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import json
import psycopg  # noqa: E402
from psycopg_pool import ConnectionPool  # noqa: E402

URL = os.environ["DATABASE_URL"]
with psycopg.connect(URL, autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
import corpus  # noqa: E402

TABLES = ("posts", "scores", "scored", "media", "media_scan", "post_images", "image_scan")


def catalog():
    """→ the indexes and reloptions of the tables the migrations change"""
    with psycopg.connect(URL) as c:
        idx = c.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = ANY(%s) ORDER BY 1",
                        (list(TABLES),)).fetchall()
        opts = c.execute("SELECT relname, reloptions FROM pg_class WHERE relname = ANY(%s) ORDER BY 1",
                         (list(TABLES),)).fetchall()
    return idx, opts


def to_version_0():
    """the database as SCHEMA left it before the migrations existed: their objects gone, no schema_version"""
    with psycopg.connect(URL, autocommit=True) as c:
        for _, m in corpus.MIGRATIONS:
            if isinstance(m, corpus.Index):
                c.execute(f"DROP INDEX IF EXISTS {m.name}")
        c.execute("ALTER TABLE post_images RESET (autovacuum_vacuum_insert_scale_factor)")
        c.execute("ALTER TABLE media_scan RESET (autovacuum_vacuum_insert_scale_factor)")
        c.execute("ALTER TABLE scores RESET (autovacuum_vacuum_insert_scale_factor)")
        c.execute("ALTER TABLE scored RESET (autovacuum_vacuum_insert_scale_factor, autovacuum_vacuum_scale_factor)")
        c.execute("ALTER TABLE media RESET (autovacuum_vacuum_insert_scale_factor, autovacuum_vacuum_scale_factor)")
        c.execute("DELETE FROM kv WHERE k = 'schema_version'")
        c.execute("""INSERT INTO kv (k, v) VALUES ('schema_hash', '"924bdb91086cd9a3"')
                     ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v""")


def statements(appliers=1):
    """→ the SQL each _apply_schema run executed (several runs at once, each with its own pool, as pods starting)"""
    seen, guard = [], threading.Lock()
    real = psycopg.Connection.execute

    def spy(conn, query, *a, **k):
        with guard:
            seen.append(query)
        return real(conn, query, *a, **k)

    def run():
        p = ConnectionPool(URL, min_size=1, max_size=1, configure=corpus._configure, open=True)
        try:
            corpus._apply_schema(p)
        finally:
            p.close()
    with mock.patch.object(psycopg.Connection, "execute", spy):
        threads = [threading.Thread(target=run) for _ in range(appliers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    return seen


def ddl(seen):
    return [q for q in seen if isinstance(q, str) and q.lstrip().startswith(("CREATE", "DROP", "ALTER", "LOCK"))]


def dump():
    """the whole schema as pg_dump writes it (every table, column, index, function, trigger, reloption)"""
    out = subprocess.run(["pg_dump", "--schema-only", "--no-owner", "--no-privileges", URL], capture_output=True, text=True, check=True).stdout
    return "\n".join(l for l in out.splitlines() if l.strip() and not l.startswith("--") and not l.startswith("\\"))


V0 = os.path.join(HERE, "tests", "schema_v0.sql")


class Schema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.pool()  # a fresh database: the first start creates everything and records the latest version
        cls.fresh = catalog()
        cls.fresh_dump = dump()

    def test_version_0_plus_the_migrations_is_schema(self):
        """the database SCHEMA made at version 0 (tests/schema_v0.sql, frozen), plus every migration, is exactly what
        SCHEMA makes now, in the whole pg_dump: a SCHEMA edit with no migration (which would never reach the live
        database) fails here"""
        with open(V0) as f:
            v0 = "".join(f.readlines()[2:])
        self.assertEqual(hashlib.sha256(v0.encode()).hexdigest()[:16], corpus.V0_HASH)
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
            c.execute(v0)
            c.execute("""INSERT INTO kv (k, v) VALUES ('schema_hash', %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v""",
                      (f'"{corpus.V0_HASH}"',))
        statements()
        self.assertEqual(corpus.kv_get("schema_version"), corpus.SCHEMA_VERSION)
        self.assertEqual(dump(), self.fresh_dump)

    def test_unknown_existing_database_stops(self):
        """an existing database with no schema_version whose schema_hash isn't version 0's: an error, no DDL skipped"""
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute("DELETE FROM kv WHERE k = 'schema_version'")
            c.execute("""INSERT INTO kv (k, v) VALUES ('schema_hash', '"0000000000000000"')
                         ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v""")
        p = ConnectionPool(URL, min_size=1, max_size=2, open=True)
        try:
            with self.assertRaises(RuntimeError):
                corpus._apply_schema(p)
        finally:
            p.close()
            corpus.kv_set("schema_version", corpus.SCHEMA_VERSION)

    def test_old_applier_is_not_held_up(self):
        """while the migrations' lock is held, a pod of the image before them takes its own key ('yyc schema') at once"""
        with psycopg.connect(URL, autocommit=True) as a, psycopg.connect(URL, autocommit=True) as b:
            a.execute("SELECT pg_advisory_lock(hashtext('yyc schema migrations'))")
            t = time.time()
            with b.transaction():
                b.execute("SELECT pg_advisory_xact_lock(hashtext('yyc schema'))")
            self.assertLess(time.time() - t, 1)
            a.execute("SELECT pg_advisory_unlock(hashtext('yyc schema migrations'))")

    def setUp(self):
        corpus.MIGRATE_SLEEP = (0.05, 0.1)

    def test_status_events_renamed(self):
        """migration 6: YYC AI's stored 'verdict' events become 'status', other events stay as they were"""
        with psycopg.connect(URL, autocommit=True) as c:
            ids = [c.execute("INSERT INTO events (at, kind, data) VALUES ('2026-11-02T08:00:00Z', %s, %s::jsonb) RETURNING id",
                             (k, json.dumps({"what": w, "state": "warming"}))).fetchone()[0]
                   for k, w in (("ai", "verdict"), ("ai", "start"), ("stage2", "verdict"))]
            dict(corpus.MIGRATIONS)[6].apply(c, corpus.SCHEMA_VERSION)
            got = [c.execute("SELECT data->>'what' FROM events WHERE id = %s", (i,)).fetchone()[0] for i in ids]
            c.execute("DELETE FROM events WHERE id = ANY(%s)", (ids,))
        self.assertEqual(got, ["status", "start", "verdict"])

    def test_fresh_database_gets_everything(self):
        self.assertEqual(corpus.kv_get("schema_version"), corpus.SCHEMA_VERSION)
        names = {n for n, _ in self.fresh[0]}
        for _, m in corpus.MIGRATIONS:
            if isinstance(m, corpus.Index):
                self.assertIn(m.name, names)
        opts = dict(self.fresh[1])
        self.assertIn("autovacuum_vacuum_insert_scale_factor=0.02", opts["scores"])
        self.assertIn("autovacuum_vacuum_scale_factor=0.05", opts["media"])

    def test_existing_database_gets_only_the_migrations(self):
        """an existing database at the seeded version: exactly the migrations run (indexes CONCURRENTLY, the
        reloptions in one transaction that locks its tables first), never SCHEMA, and it ends where SCHEMA ends"""
        to_version_0()
        seen = statements()
        self.assertNotIn(corpus.SCHEMA, seen)
        made = [q for q in ddl(seen) if q.startswith("CREATE")]
        self.assertEqual(len(made), 4, made)
        self.assertTrue(all(q.startswith("CREATE INDEX CONCURRENTLY IF NOT EXISTS") for q in made), made)
        self.assertIn("LOCK TABLE media, media_scan, post_images, scored, scores IN SHARE UPDATE EXCLUSIVE MODE",
                      ddl(seen))
        self.assertEqual(corpus.kv_get("schema_version"), corpus.SCHEMA_VERSION)
        self.assertEqual(catalog(), self.fresh)
        self.assertEqual(ddl(statements()), [])  # a second run is a no-op

    def test_no_version_seeds_without_ddl(self):
        """a database SCHEMA made before the migrations (no schema_version) is recorded as version 0 without any DDL"""
        corpus.kv_set("schema_version", corpus.SCHEMA_VERSION)
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute("DELETE FROM kv WHERE k = 'schema_version'")
        with mock.patch.object(corpus, "MIGRATIONS", ()):
            self.assertEqual(ddl(statements()), [])
        self.assertEqual(corpus.kv_get("schema_version"), 0)
        corpus.kv_set("schema_version", corpus.SCHEMA_VERSION)

    def test_concurrent_appliers_serialize(self):
        """pods starting at once: each migration runs once, by whichever holds the lock; the others then find nothing
        to do"""
        to_version_0()
        seen = statements(appliers=4)
        made = [q for q in ddl(seen) if q.startswith("CREATE")]
        self.assertEqual(len(made), 4, made)
        self.assertEqual(sum(q.startswith("LOCK TABLE") for q in ddl(seen)), sum(isinstance(m, corpus.Change) for _, m in corpus.MIGRATIONS))
        self.assertEqual(catalog(), self.fresh)

    def test_invalid_index_is_rebuilt(self):
        """a CONCURRENTLY build that failed leaves an invalid index; the next run drops and builds it again"""
        to_version_0()
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute("CREATE INDEX image_scan_at ON image_scan (at)")
            c.execute("UPDATE pg_index SET indisvalid = FALSE WHERE indexrelid = 'image_scan_at'::regclass")
        seen = ddl(statements())
        self.assertIn("DROP INDEX CONCURRENTLY IF EXISTS image_scan_at", seen)
        with psycopg.connect(URL) as c:
            self.assertTrue(c.execute("SELECT indisvalid FROM pg_index WHERE indexrelid = 'image_scan_at'::regclass")
                            .fetchone()[0])

    def test_lock_wait_retries(self):
        """a migration that can't get its locks within lock_timeout rolls back and tries again, so a long transaction
        holding the table never has readers queued behind the migration for more than 2 s"""
        to_version_0()
        corpus.kv_set("schema_version", 4)
        held = threading.Event()

        def hold():
            with psycopg.connect(URL) as c:
                c.execute("LOCK TABLE scores IN SHARE UPDATE EXCLUSIVE MODE")
                held.set()
                time.sleep(3)
        t = threading.Thread(target=hold)
        t.start()
        held.wait()
        start = time.monotonic()
        seen = statements()
        t.join()
        self.assertGreaterEqual(sum(q.startswith("LOCK TABLE") for q in ddl(seen)), 2)
        self.assertGreater(time.monotonic() - start, 2)
        self.assertEqual(corpus.kv_get("schema_version"), corpus.SCHEMA_VERSION)
        self.assertEqual(catalog()[1], self.fresh[1])

    def test_url_key_is_indexed(self):
        """posts.url_key has its own index (assign_articles looks every new post up by it on the alert path). Its
        index was named posts_url_key, the name Postgres had already given the UNIQUE (url) constraint, so CREATE
        INDEX IF NOT EXISTS skipped it: each lookup was a 7.5 s scan of the whole table (live 16 Oct 2026, 745k
        posts), and one ingest of the outage catch-up held its row locks for minutes"""
        defs = [r[0] for r in corpus.db().execute("SELECT indexdef FROM pg_indexes WHERE tablename = 'posts'")]
        self.assertTrue(any(d.endswith("(url_key)") for d in defs), defs)


if __name__ == "__main__":
    unittest.main()
