#!/usr/bin/env python3
"""The restart cutoff (corpus.FLOOR; Deyao, 2026-10-20: "ignore the past", "no need to clear backlog"):
a post seen before it is ignored, not waiting. No job spends work or money on it and no monitor count shows it:

1. the jobs' queues leave it out: the full-text job (candidates, growing-page rereads, YouTube descriptions, refill,
   retry), the cache job, the archive.today job, link reads, the clip and picture queues (also the YouTube-subtitles
   pass while transcription is off), AI titles, the split streams' market backlog; the item page's text_due
   (fulltext_fetch.will_read) agrees;
2. the monitor's waiting counts leave it out: posts awaiting score (a new source's first read counted apart), full
   text waiting (2 days and all), full text by source, clips to transcribe, pictures to score.
On a throwaway database (TEST_DATABASE_URL), no network.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_cutoff_work.py
"""
import os, sys, tempfile, unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
for k in ("OPENROUTER_API", "OPENROUTER_MANAGEMENT_KEY", "TIKHUB_API", "ANYAPI_API_KEY", "IPROYAL_API",
          "KUBERNETES_SERVICE_HOST", "MONITOR_WARM"):
    os.environ.pop(k, None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, cache_fetch, corpus, fulltext_fetch, images, links, media, monitor, paywall, sources, streams, titles  # noqa: E402

HKT = corpus.HKT
NOW = datetime.now(HKT)
CUT = NOW - timedelta(hours=3)
OLD, NEW = CUT - timedelta(minutes=30), CUT + timedelta(minutes=30)
streams.settings()
N = [0]


def post(seen, sid="rthk", source_type="News", text=None, url=None, title="李家超今日出席立法會答問大會", **cols):
    N[0] += 1
    url = url or f"https://news.rthk.hk/rthk/ch/component/k2/{N[0]}-20261020.htm"
    corpus.ingest([{"sid": sid, "source": "某報", "section": "港聞", "title": title, "url": url,
                    "published": NOW.isoformat(timespec="seconds"), "text": text}])
    c = corpus.db()
    c.execute("UPDATE posts SET first_run = FALSE, seen = %s, source_type = %s WHERE url = %s",
              (seen.isoformat(timespec="seconds"), source_type, url))
    for k, v in cols.items():
        c.execute(f"UPDATE posts SET {k} = %s WHERE url = %s", (v, url))
    return c.execute("SELECT id FROM posts WHERE url = %s", (url,)).fetchone()[0]


def row(pid):
    return dict(corpus.db().execute("SELECT * FROM posts WHERE id = %s", (pid,)).fetchone())


class Cutoff(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(corpus, "FLOOR", CUT.isoformat(timespec="seconds"))
        p.start()
        self.addCleanup(p.stop)

    def test_fulltext_job(self):
        old, new = post(OLD), post(NEW)
        ids = [x[0] for x in fulltext_fetch.candidates(1000)]
        self.assertIn(new, ids)
        self.assertNotIn(old, ids)
        self.assertFalse(fulltext_fetch.will_read(row(old)))  # the item page's text_due
        self.assertTrue(fulltext_fetch.will_read(row(new)))
        # refill and retry never reach before the cutoff, even when asked to
        with mock.patch.object(fulltext_fetch, "run") as run:
            fulltext_fetch.refill((CUT - timedelta(days=1)).isoformat(timespec="seconds"))
        self.assertEqual({t[0] for t in run.call_args[0][0]} & {old}, set())
        c = corpus.db()
        c.execute("UPDATE posts SET cached_at = %s, text = '' WHERE id = ANY(%s)", (NOW.isoformat(), [old, new]))
        fulltext_fetch.retry("news.rthk.hk")
        self.assertIsNotNone(row(old)["cached_at"])
        self.assertIsNone(row(new)["cached_at"])
        # growing pages (RTHK) read short: read again only when seen since the cutoff
        c.execute("UPDATE posts SET cached_at = %s, text = '短' WHERE id = ANY(%s)",
                  ((NOW - timedelta(hours=2)).isoformat(), [old, new]))
        fulltext_fetch.reread()
        self.assertIsNotNone(row(old)["cached_at"])
        self.assertIsNone(row(new)["cached_at"])

    def test_youtube_descriptions(self):
        old = post(OLD, sid="yt_rthk", source_type="Video", url="https://www.youtube.com/watch?v=aaaaaaaaaa1")
        new = post(NEW, sid="yt_rthk", source_type="Video", url="https://www.youtube.com/watch?v=aaaaaaaaaa2")
        corpus.db().execute("UPDATE posts SET text = NULL, cached_at = NULL WHERE id = ANY(%s)", ([old, new],))
        asked = []
        with mock.patch.object(sources, "youtube_info", lambda xs: asked.extend(x["url"] for x in xs) or {}):
            sources.youtube_fill_text()
        self.assertIn(row(new)["url"], asked)
        self.assertNotIn(row(old)["url"], asked)

    def test_cache_and_archive_jobs(self):
        old, new = post(OLD, url="https://www.mingpao.com/a/1"), post(NEW, url="https://www.mingpao.com/a/2")
        c = corpus.db()
        for pid in (old, new):
            c.execute("UPDATE posts SET text = NULL, cached_at = NULL WHERE id = %s", (pid,))
            c.execute("INSERT INTO tags (profile, post_id, relevant) VALUES ('CEHK', %s, 1)", (pid,))
        ids = [x[0] for x in cache_fetch.candidates()]
        self.assertIn(new, ids)
        self.assertNotIn(old, ids)
        c.execute("UPDATE posts SET text_via = 'excerpt', text = '摘要' WHERE id = ANY(%s)", ([old, new],))
        ids = [r["id"] for r in paywall.archive_candidates()]
        self.assertIn(new, ids)
        self.assertNotIn(old, ids)

    def test_link_reads(self):
        corpus.kv_set("links_upto", None)
        old = post(OLD, sid="x_someone", source_type="Social Media", title="https://example.com/story/1",
                   text="https://example.com/story/1", url="https://x.com/a/status/1")
        new = post(NEW, sid="x_someone", source_type="Social Media", title="https://example.com/story/2",
                   text="https://example.com/story/2", url="https://x.com/a/status/2")
        ids = [r["id"] for r in links.due(100)]
        self.assertIn(new, ids)
        self.assertNotIn(old, ids)
        c = corpus.db()
        for pid in (old, new):  # a retry come due
            c.execute("""INSERT INTO link_scan (post_id, url, tries, status, next_try) VALUES (%s, 'u', 1, 'pending', %s)
                         ON CONFLICT (post_id) DO UPDATE SET status='pending', next_try=excluded.next_try""",
                      (pid, (NOW - timedelta(minutes=1)).isoformat()))
        ids = [r["id"] for r in links.due(100)]
        self.assertIn(new, ids)
        self.assertNotIn(old, ids)

    def _clip(self, key, pid):
        c = corpus.db()
        c.execute("""INSERT INTO media (key, url, kind, host, status, first_seen, newest)
                     VALUES (%s, %s, 'video', 'youtube.com', 'queued', %s, %s)""",
                  (key, "https://www.youtube.com/watch?v=" + key[3:], NOW.isoformat(), NOW.isoformat()))
        c.execute("INSERT INTO post_media (post_id, key) VALUES (%s, %s)", (pid, key))

    def test_clip_and_picture_queues_and_monitor(self):
        old, new = post(OLD), post(NEW)
        self._clip("yt:oldclip0001", old)
        self._clip("yt:newclip0001", new)
        keys = [r["key"] for r in media.due(10)]
        self.assertEqual(keys, ["yt:newclip0001"])
        got = media.claim_captions()
        self.assertEqual(got["key"], "yt:newclip0001")
        media.release(got["key"])
        corpus.db().execute("UPDATE media SET claimed_until = NULL, captions_at = now()::text WHERE key = %s", (got["key"],))
        self.assertIsNone(media.claim_captions())  # the old clip's subtitles are never read
        got = media.claim()
        self.assertEqual(got["key"], "yt:newclip0001")
        c = corpus.db()
        for pid in (old, new):
            c.execute("INSERT INTO post_images (post_id, url, pos) VALUES (%s, %s, 0)", (pid, f"https://img/{pid}.jpg"))
        self.assertEqual([r["post_id"] for r in images.due(10)], [new])
        p = monitor.pipeline()
        self.assertEqual(p["transcribe"]["queued"], 1)
        self.assertEqual(p["pictures"]["due"], 1)

    def test_titles(self):
        old = post(OLD - timedelta(minutes=20), sid="facebook", source_type="Social Media",
                   url="https://www.facebook.com/p/111")
        new = post(NEW - timedelta(minutes=20), sid="facebook", source_type="Social Media",
                   url="https://www.facebook.com/p/222")
        for pid in (old, new):  # scored: a post with no title of its own waits for the scorer (titles.due)
            corpus.db().execute(f"INSERT INTO scored (post_id, hash, at) SELECT p.id, {streams.HASH}, 'x' FROM posts p "
                                "WHERE p.id = %s", (pid,))
        ids = [r["id"] for r in titles.due(100)]
        self.assertIn(new, ids)
        self.assertNotIn(old, ids)

    def test_monitor_scoring_and_full_text_counts(self):
        c = corpus.db()
        c.execute("DELETE FROM posts")
        old, new = post(OLD, text="x" * 600), post(NEW, text="x" * 600)
        first = post(NEW, text="x" * 600)
        c.execute("UPDATE posts SET first_run = TRUE WHERE id = %s", (first,))
        nt_old, nt_new = post(OLD), post(NEW)
        c.execute("UPDATE posts SET text = NULL, cached_at = NULL WHERE id = ANY(%s)", ([nt_old, nt_new],))
        dq = streams.due_counts()
        ids = [p["id"] for p in streams.due(100)]
        self.assertNotIn(old, ids)
        # a backlog post (first_run) is not in the queue (Deyao, 2026-10-27: only a manual run takes a backlog)
        self.assertNotIn(first, ids)
        self.assertEqual((dq["live"], dq["first_run"]), (len(ids), 0))
        p = monitor.pipeline()
        self.assertEqual(p["scoring"]["awaiting"], dq["live"])
        self.assertEqual(p["scoring"]["first_run"], 0)
        self.assertEqual(p["fulltext"]["waiting_recent_2d"], 1)
        self.assertEqual(p["fulltext"]["waiting_all"], 1)
        self.assertEqual(sum(s["posts"] for s in p["fulltext"]["sources"]), 3)  # the three seen since the cutoff


if __name__ == "__main__":
    unittest.main()
