#!/usr/bin/env python3
"""Backlogs (Deyao, 2026-10-27: "by default, backlog should not be cleared at all (at any stage of the pipeline), only a
manual trigger should process them"; each stage on its own, after any stop):

1. a stage's start point moves to now when it starts again after a stop: no round on record, a gap longer than its
   corpus.STAGES entry, or a block (stage_blocked); a round right after a working one keeps it; a block raised in the
   round that restarted it stays for the next round;
2. its queue (seen_cond / in_stage) leaves out posts seen before its start point and backlog posts (first_run), and
   another stage's start point is not its own;
3. the manual run (corpus.backlog_run, infra/backlog.py) lets them in from a time, the backlog posts only with
   --first-run, never before corpus.FLOOR;
4. a source read again after COLLECT_GAP without a read: that read's new posts, replies too, are backlog; within the
   gap they are not;
5. OpenRouter's limit (ai.limit_hit) and the proxy out of traffic (resi.mark) block the thread's stage;
6. a sender (Telegram) back after a stop sends nothing filed before; the scoring queue and the full-text job keep to
   their own stage.
On a throwaway database (TEST_DATABASE_URL), no network.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_backlog.py
"""
import os, sys, tempfile, unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
for k in ("OPENROUTER_API", "OPENROUTER_MANAGEMENT_KEY", "TIKHUB_API", "ANYAPI_API_KEY", "IPROYAL_API",
          "KUBERNETES_SERVICE_HOST", "MONITOR_WARM", "TELEGRAM_BOT_TOKEN"):
    os.environ.pop(k, None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, corpus, fulltext_fetch, notify, resi, streams, telegram_alerts  # noqa: E402

HKT = corpus.HKT
NOW = datetime.now(HKT)
FLOOR = (NOW - timedelta(days=2)).isoformat(timespec="seconds")
streams.settings()
N = [0]


def iso(t):
    return t.isoformat(timespec="seconds")


def post(seen, first_run=False, sid="rthk"):
    N[0] += 1
    url = f"https://news.rthk.hk/rthk/ch/component/k2/{N[0]}-20261027.htm"
    corpus.ingest([{"sid": sid, "source": "香港電台", "title": "李家超出席立法會答問大會 " + str(N[0]), "url": url,
                    "published": iso(seen)}])
    c = corpus.db()
    c.execute("UPDATE posts SET first_run = %s, seen = %s, source_type = 'News' WHERE url = %s",
              (first_run or None, iso(seen), url))
    return c.execute("SELECT id FROM posts WHERE url = %s", (url,)).fetchone()[0]


def in_queue(pid, stage):
    return bool(corpus.db().execute(f"SELECT 1 FROM posts p WHERE p.id = %s AND {corpus.seen_cond('p', stage)}",
                                    (pid,)).fetchone())


class Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(corpus, "FLOOR", FLOOR)
        p.start()
        self.addCleanup(p.stop)
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'cutoff:%%' OR k LIKE 'stage:%%'")
        corpus._cut_cache.clear()

    def working(self, stage, ago=timedelta(minutes=1)):
        """the stage has been working: its last round ended `ago`"""
        corpus.kv_set("stage:" + stage, {"ok": iso(datetime.now(HKT) - ago), "down": None})


class Stages(Base):
    def test_no_round_on_record_starts_from_now(self):
        before = post(NOW - timedelta(minutes=10))
        with corpus.stage("titles") as restarted:
            self.assertTrue(restarted)
        self.assertGreaterEqual(datetime.fromisoformat(corpus.cutoff("titles")), NOW - timedelta(seconds=5))
        self.assertFalse(in_queue(before, "titles"))
        after = post(datetime.now(HKT) + timedelta(seconds=1))
        self.assertTrue(in_queue(after, "titles"))
        with corpus.stage("titles") as restarted:  # the next round: working, nothing moves
            self.assertFalse(restarted)

    def test_a_round_within_the_gap_keeps_the_start_point(self):
        self.working("cache", timedelta(seconds=corpus.STAGES["cache"] - 60))
        p = post(NOW - timedelta(hours=1))
        with corpus.stage("cache") as restarted:
            self.assertFalse(restarted)
        self.assertEqual(corpus.cutoff("cache"), FLOOR)
        self.assertTrue(in_queue(p, "cache"))

    def test_a_gap_longer_than_the_stages_starts_from_now(self):
        self.working("fulltext", timedelta(seconds=corpus.STAGES["fulltext"] + 60))  # e.g. the CronJob suspended
        p = post(NOW - timedelta(minutes=5))
        with corpus.stage("fulltext") as restarted:
            self.assertTrue(restarted)
        self.assertFalse(in_queue(p, "fulltext"))
        self.assertIn("no round since", corpus.kv_get("cutoff:fulltext")["log"][-1]["why"])

    def test_a_failing_round_records_no_work(self):
        self.working("links", timedelta(minutes=5))
        ok = corpus.kv_get("stage:links")["ok"]
        with self.assertRaises(RuntimeError), corpus.stage("links"):
            raise RuntimeError("the site is down")
        self.assertEqual(corpus.kv_get("stage:links")["ok"], ok)

    def test_a_block_restarts_the_stage_when_it_works_again(self):
        self.working("pictures")
        p = post(NOW - timedelta(minutes=5))
        with corpus.stage("pictures") as restarted:
            self.assertFalse(restarted)
            corpus.stage_blocked("flag pictures off")  # the thread's own stage
        self.assertEqual(corpus.kv_get("stage:pictures")["down"], "flag pictures off")
        self.assertTrue(in_queue(p, "pictures"))  # nothing moved while it was off
        with corpus.stage("pictures") as restarted:  # on again
            self.assertTrue(restarted)
        self.assertFalse(in_queue(p, "pictures"))
        self.assertIn("flag pictures off", corpus.kv_get("cutoff:pictures")["log"][-1]["why"])
        self.assertIsNone(corpus.kv_get("stage:pictures")["down"])

    def test_a_block_in_the_restarting_round_stays(self):
        corpus.stage_blocked("flag transcription off", "transcription")
        with corpus.stage("transcription") as restarted:
            self.assertTrue(restarted)
            corpus.stage_blocked("flag transcription off")
        self.assertTrue(corpus.kv_get("stage:transcription")["down"])
        with corpus.stage("transcription") as restarted:
            self.assertTrue(restarted)

    def test_stages_are_separate(self):
        self.working("scoring")
        p = post(NOW - timedelta(minutes=5))
        corpus.stage_blocked("OpenRouter refused", "titles")
        with corpus.stage("titles"):
            pass
        self.assertFalse(in_queue(p, "titles"))
        self.assertTrue(in_queue(p, "scoring"))

    def test_backlog_posts_are_in_no_queue(self):
        p = post(NOW - timedelta(minutes=5), first_run=True)
        for s in corpus.STAGES:
            self.assertFalse(in_queue(p, s), s)
        self.assertFalse(corpus.in_stage({"seen": iso(NOW), "first_run": True}, "fulltext"))
        self.assertTrue(corpus.in_stage({"seen": iso(NOW)}, "fulltext"))


class Manual(Base):
    def test_run_from_a_time(self):
        corpus.stage_blocked("proxy out of traffic", "archive")
        with corpus.stage("archive"):
            pass
        old, older = post(NOW - timedelta(hours=2)), post(NOW - timedelta(hours=6))
        first = post(NOW - timedelta(hours=2), first_run=True)
        self.assertFalse(in_queue(old, "archive"))
        corpus.backlog_run("archive", iso(NOW - timedelta(hours=3)))
        self.assertTrue(in_queue(old, "archive"))
        self.assertFalse(in_queue(older, "archive"))
        self.assertFalse(in_queue(first, "archive"))  # backlog posts only with --first-run
        corpus.backlog_run("archive", iso(NOW - timedelta(hours=3)), first_run=True)
        self.assertTrue(in_queue(first, "archive"))
        self.assertTrue(corpus.in_stage({"seen": iso(NOW - timedelta(hours=2)), "first_run": True}, "archive"))
        # never before the floor
        self.assertEqual(corpus.backlog_run("archive", iso(NOW - timedelta(days=30))), FLOOR)

    def test_the_script(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("backlog_cli", os.path.join(HERE, "infra", "backlog.py"))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        cli.main(["run", "comments", "--from", iso(NOW - timedelta(hours=1))])
        self.assertEqual(corpus.cutoff("comments"), iso(NOW - timedelta(hours=1)))
        cli.main([])  # the status prints without error
        with self.assertRaises(SystemExit):
            cli.main(["run", "nosuchstage", "--from", iso(NOW)])


class Collectors(Base):
    def read(self, sid, url, last_read=None):
        if last_read is not None:
            corpus.db().execute("INSERT INTO sid_reads (sid, last_read) VALUES (%s, %s) ON CONFLICT (sid) DO UPDATE "
                                "SET last_read = excluded.last_read", (sid, last_read))
        corpus.ingest([{"sid": sid, "source": "某報", "title": "標題 " + url[-6:], "url": url, "published": iso(NOW)}])
        return corpus.db().execute("SELECT first_run FROM posts WHERE url = %s", (url,)).fetchone()[0]

    def test_first_read_after_a_gap_is_backlog(self):
        self.assertTrue(self.read("hk01", "https://www.hk01.com/a/000001"))  # a new source: its first read
        self.assertFalse(self.read("hk01", "https://www.hk01.com/a/000002", NOW - timedelta(minutes=10)))
        gap = NOW - corpus.COLLECT_GAP - timedelta(minutes=5)
        self.assertTrue(self.read("hk01", "https://www.hk01.com/a/000003", gap))  # back after the pause
        self.assertFalse(self.read("hk01", "https://www.hk01.com/a/000004"))  # the read after it: live again

    def test_replies_in_the_first_read_after_a_gap_are_backlog(self):
        def reply(url, last_read=None):
            if last_read is not None:
                corpus.db().execute("INSERT INTO sid_reads (sid, last_read) VALUES ('lihkg_cat5', %s) ON CONFLICT (sid) "
                                    "DO UPDATE SET last_read = excluded.last_read", (last_read,))
            corpus.ingest([{"sid": "lihkg_cat5", "source": "LIHKG", "title": "回覆 " + url[-2:], "url": url,
                            "thread": "https://lihkg.com/thread/1", "published": iso(NOW)}])
            return corpus.db().execute("SELECT first_run FROM posts WHERE url = %s", (url,)).fetchone()[0]
        self.assertFalse(reply("https://lihkg.com/thread/1/r/01"))  # a new forum's replies: as before, live
        self.assertFalse(reply("https://lihkg.com/thread/1/r/02", NOW - timedelta(minutes=5)))
        self.assertTrue(reply("https://lihkg.com/thread/1/r/03", NOW - corpus.COLLECT_GAP - timedelta(minutes=5)))
        self.assertFalse(reply("https://lihkg.com/thread/1/r/04"))  # the read after it: live again

    def test_no_read_on_record_uses_the_newest_post(self):
        self.read("mingpao", "https://news.mingpao.com/a/1")
        corpus.db().execute("DELETE FROM sid_reads WHERE sid = 'mingpao'")
        corpus.db().execute("UPDATE posts SET seen = %s WHERE sid = 'mingpao'", (iso(NOW - timedelta(days=1)),))
        self.assertTrue(self.read("mingpao", "https://news.mingpao.com/a/2"))
        corpus.db().execute("DELETE FROM sid_reads WHERE sid = 'mingpao'")
        corpus.db().execute("UPDATE posts SET seen = %s WHERE sid = 'mingpao'", (iso(NOW - timedelta(minutes=3)),))
        self.assertFalse(self.read("mingpao", "https://news.mingpao.com/a/3"))


class Blocks(Base):
    def test_openrouter_limit_blocks_the_threads_stage(self):
        self.working("titles")
        with mock.patch.object(notify, "deyao", return_value=True), corpus.stage("titles"):
            e = ai.limit_hit("Key limit exceeded")
        self.assertIsInstance(e, ai.LimitReached)
        self.assertIn("OpenRouter", corpus.kv_get("stage:titles")["down"])

    def test_proxy_out_blocks_the_threads_stage(self):
        self.working("fulltext")
        with mock.patch.object(notify, "deyao", return_value=True), corpus.stage("fulltext"):
            resi.mark("CONNECT tunnel failed, response 402", job="fulltext")
        self.assertEqual(corpus.kv_get("stage:fulltext")["down"], "proxy out of traffic")

    def test_outside_a_stage_nothing_is_marked(self):
        with mock.patch.object(notify, "deyao", return_value=True):
            ai.limit_hit("Key limit exceeded")
        self.assertFalse([k for (k,) in corpus.db().execute("SELECT k FROM kv WHERE k LIKE 'stage:%%'")])


class Queues(Base):
    def test_scoring_and_full_text_keep_to_their_stage(self):
        self.working("fulltext")
        p = post(NOW - timedelta(minutes=20))
        corpus.db().execute("UPDATE posts SET text = NULL, cached_at = NULL WHERE id = %s", (p,))
        corpus.stage_blocked("OpenRouter refused", "scoring")
        with corpus.stage("scoring"):
            pass
        self.assertNotIn(p, [x["id"] for x in streams.due(100)])
        self.assertIn(p, [x[0] for x in fulltext_fetch.candidates(1000)])

    def test_telegram_back_after_a_stop_sends_nothing_filed_before(self):
        s = {"id": "CEHK", "telegram_chat": "-100"}
        c = corpus.db()
        for i in range(3):
            c.execute("INSERT INTO stream_items (stream, url, item, day) VALUES ('CEHK', %s, %s, %s)",
                      (f"https://x.test/{i}", corpus.J({"title": f"t{i}"}), NOW.date().isoformat()))
        newest = c.execute("SELECT max(id) FROM stream_items WHERE stream = 'CEHK'").fetchone()[0]
        corpus.kv_set(telegram_alerts.KEY, {"CEHK": newest - 3})
        with corpus.stage("telegram") as restarted:
            self.assertTrue(restarted)
            telegram_alerts.skip_backlog([s])
        self.assertEqual(corpus.kv_get(telegram_alerts.KEY)["CEHK"], newest)


if __name__ == "__main__":
    unittest.main()
