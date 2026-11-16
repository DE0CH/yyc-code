#!/usr/bin/env python3
"""The 20 Oct 2026 restart (Deyao: "just start watching and capturing from this point, ignore the past"): no post
seen before corpus.FLOOR is scored live, first or second scoring, and no unit's comments are read again or
at capture for it. Posts seen from then on are scored as usual. On a throwaway database (TEST_DATABASE_URL), no network.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_restart_cutoff.py
"""
import os, sys, tempfile, unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, corpus, social_twopass, streams, twopass  # noqa: E402

HKT = app.HKT
NOW = datetime.now(HKT)
CUT = NOW - timedelta(hours=1)
streams.WAIT_TEXT = timedelta(0)
streams.FIRST_WAIT = timedelta(seconds=-60)
streams.settings()


def post(url, seen, sid="tikhub_tiktok"):
    corpus.ingest([{"sid": sid, "source": "someone", "section": "TikTok · 測試", "title": "李家超今日出席立法會答問大會",
                    "url": url, "published": NOW.isoformat(timespec="seconds"),
                    "text": "李家超今日出席立法會答問大會，回應議員就房屋及經濟的提問。"}])
    c = corpus.db()
    c.execute("UPDATE posts SET first_run = FALSE, seen = %s WHERE url = %s", (seen.isoformat(timespec="seconds"), url))
    return c.execute("SELECT id FROM posts WHERE url = %s", (url,)).fetchone()[0]


class RestartCutoff(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(corpus, "FLOOR", CUT.isoformat(timespec="seconds"))
        p.start()
        self.addCleanup(p.stop)

    def test_live_scoring_only_after_cutoff(self):
        old = post("https://www.tiktok.com/@a/video/1", CUT - timedelta(minutes=5))
        new = post("https://www.tiktok.com/@a/video/2", CUT + timedelta(minutes=5))
        ids = [p["id"] for p in streams.due(500)]
        self.assertIn(new, ids)
        self.assertNotIn(old, ids)

    def test_units_first_and_second_scoring_and_rereads_only_after_cutoff(self):
        old_url, new_url = "https://www.facebook.com/somepage/posts/111", "https://www.facebook.com/somepage/posts/222"
        old = post(old_url, CUT - timedelta(minutes=5), sid="facebook")
        new = post(new_url, CUT + timedelta(minutes=5), sid="facebook")
        reads = []
        with mock.patch.object(social_twopass, "read", lambda p: reads.append(p["url"])):
            social_twopass.run_once()
        self.assertEqual(reads, [new_url])  # no capture read for the old post
        ids = [p["id"] for p in streams.unit_due(500)]
        self.assertIn(new, ids)
        self.assertNotIn(old, ids)
        # a pre-cutoff unit scored once, its second scoring due: neither re-read nor scored again
        c = corpus.db()
        c.execute("INSERT INTO scored (post_id, hash) VALUES (%s, 'x')", (old,))
        c.execute("""INSERT INTO two_pass (url, captured_at, first_at, due_at) VALUES (%s, now(), now(), now() - interval '1 hour')
                     ON CONFLICT (url) DO UPDATE SET captured_at = now(), first_at = now(), due_at = now() - interval '1 hour'""",
                  (old_url,))
        self.assertEqual(twopass.due_for_fetch("https://www.facebook.com/%"), [])
        self.assertNotIn(old, [p["id"] for p in streams.unit_due(500)])


    def test_backfill_only_after_cutoff(self):
        """the history backfill (off since 20 Oct) never takes a post seen before the cutoff: switched on, after a
        brief change (other stored definitions) or never scored, only posts seen since are due and counted"""
        old_pub = (NOW - streams.MAX_AGE - timedelta(days=1)).isoformat(timespec="seconds")
        before, after = [], []
        for k, (seen, out) in enumerate([(CUT - timedelta(minutes=5), before), (CUT + timedelta(minutes=5), after)]):
            for j, defs in enumerate([None, "olddefs"]):  # never scored, and scored on other definitions
                url = f"https://news.test/backfill/{k}/{j}"
                corpus.ingest([{"sid": "hk01", "source": "香港01", "title": "李家超出席活動", "url": url,
                                "published": old_pub, "text": "李家超今日出席活動並致辭，談及房屋及經濟政策。" * 5}])
                c = corpus.db()
                c.execute("UPDATE posts SET first_run = FALSE, seen = %s WHERE url = %s", (seen.isoformat(timespec="seconds"), url))
                pid = c.execute("SELECT id FROM posts WHERE url = %s", (url,)).fetchone()[0]
                if defs:
                    c.execute(f"INSERT INTO scored (post_id, hash, defs, at) SELECT id, {streams.HASH}, %s, %s FROM posts p "
                              "WHERE id = %s", (defs, NOW.isoformat(timespec="seconds"), pid))
                out.append(pid)
        due = [p["id"] for p in streams.backfill_due(500, app.STREAMS)]
        for pid in after:
            self.assertIn(pid, due)
        for pid in before:
            self.assertNotIn(pid, due)
        left = streams.backfill_left(app.STREAMS)
        with mock.patch.object(corpus, "FLOOR", (CUT - timedelta(days=1)).isoformat(timespec="seconds")):
            self.assertEqual(streams.backfill_left(app.STREAMS), left + len(before))  # the cutoff alone keeps them out


if __name__ == "__main__":
    unittest.main()
