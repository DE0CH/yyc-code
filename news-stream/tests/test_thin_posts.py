#!/usr/bin/env python3
"""Posts with almost no content (Deyao, 2026-10-15: "our scrape comes up with almost zero data (e.g. just a few
hashtag or a url) and the scorer should reject it"). Measured in production (posts of 8–15 Oct): TikTok posts whose
whole caption is hashtags, Facebook / Instagram photo posts with a two-word caption, AnyAPI Facebook results titled
with their author's name, bare links that could not be read; the scorer scored them and some were filed and sent.
On a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again), no network, the model stubbed.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_thin_posts.py

Covers:
  1. thin.units(): what a post says once URLs, hashtags, @mentions, emoji, punctuation and the collectors' own
     placeholders ("(post without text)") are taken out; a CJK character counts one, a Latin/digit word counts one.
  2. A near-empty post (under thin.THRESHOLD) with nothing more on the way is rejected: never scored (no model call,
     no `scored` row), never filed or sent; table thin_posts says why; it is not due again.
  3. While content may still come (a queued transcript, pictures not read yet, a link not read yet) it waits, at most
     thin.WAIT from when it was seen; the content arriving makes it due and it is scored; past WAIT it is rejected.
  4. A meaningful picture (images.py score at or above the threshold) is content: such a post is scored.
  5. A bare link that links.py gave up on is rejected (it used to stay held, unseen, for ever).
  6. A short post that says something (「李家超，你接受嗎？」) is scored; a thread scored with its replies counts them.
  7. A filed item stays filed (Deyao's rule): rejecting never removes a stream item.
  8. item.view and the item page's AI analysis (the edge app's item.aiScores) say 「內容不足，未評分」 and why;
     數據分析's post lists carry the mark (the edge app's dashboard.markThin; both run in Node by tests/edge_call.py).
  9. links.py reads past hashtags and @mentions: a post that is a link plus hashtags is "only a link" (held, resolved).
"""
import json, os, sys, tempfile, unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import flags_on  # noqa: E402,F401  picture analysis and transcription on (off since 2026-10-20: tests/test_pictures_transcription_off.py)
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, edge_config, item, links, streams, titles  # noqa: E402
from edge_call import edge  # noqa: E402
import thin  # noqa: E402

HKT = app.HKT
NOW = datetime.now(HKT)
CALLS = []


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000, **kw):
    CALLS.append((purpose, prompt))
    if purpose == "titles":
        return {"titles": []}
    if not purpose.startswith("scoring"):
        return {}
    body = prompt.split("Posts:\n", 1)[1]
    import re
    return {"posts": [{"i": int(i), "sentiment": "neutral", "labels": [],
                       "streams": [{"id": "HKIC", "score": 80, "reason": "測試", "category": "其他"}]}
                      for i in re.findall(r"(?m)^(\d+)\. \[", body)]}


import stage2  # noqa: E402
ai.jsonc = stage2.agree(fake_jsonc, CALLS)  # stage 2 answers as stage 1 (tests/stage2.py)
streams.WAIT_TEXT = timedelta(0)
streams.FIRST_WAIT = timedelta(seconds=-60)
streams.settings()
titles.SETTLE = timedelta(seconds=-60)


def social(url, title, text=None, sid="tikhub_tiktok", source="someone (TikTok)", section="TikTok · 李家超", **kw):
    return dict({"sid": sid, "source": source, "section": section, "title": title, "url": url,
                 "published": NOW.isoformat(timespec="seconds"), "text": text, "author": "someone"}, **kw)


def ingest(p):
    corpus.ingest([p])
    corpus.db().execute("UPDATE posts SET first_run = FALSE")
    return row(p["url"])


def row(url):
    r = corpus.db().execute("SELECT * FROM posts WHERE url=%s", (url,)).fetchone()
    return dict(r) if r else None


def due_ids():
    return [p["id"] for p in streams.due(500)] + [p["id"] for p in streams.unit_due(500)]


def scored(pid):
    return bool(corpus.db().execute("SELECT 1 FROM scored WHERE post_id=%s", (pid,)).fetchone())


def filed(url):
    return bool(corpus.db().execute("SELECT 1 FROM stream_items WHERE url=%s", (url,)).fetchone())


def model_saw(text):
    return any(text in p for k, p in CALLS if k.startswith("scoring"))


def state(pid):
    r = corpus.db().execute("SELECT * FROM thin_posts WHERE post_id=%s", (pid,)).fetchone()
    return dict(r) if r else None


class Units(unittest.TestCase):
    def test_counts(self):
        u = thin.units
        self.assertEqual(u("#台股#股市#股票"), 0)
        self.assertEqual(u("#台股 #股市 #股票 #投資 #理財"), 0)
        self.assertEqual(u("https://t.co/3DS8gH0wK7"), 0)
        self.assertEqual(u("(post without text)"), 0)
        self.assertEqual(u("(post without caption)"), 0)
        self.assertEqual(u("@InsightNingbo"), 0)
        self.assertEqual(u("？？？ ​"), 0)
        self.assertEqual(u("Haha"), 1)
        self.assertEqual(u("李家超🤣"), 3)
        self.assertEqual(u("好長情！ #魏浚笙 #am730"), 3)
        self.assertEqual(u("Financial imbalances and AI boom"), 5)
        self.assertEqual(u("李家超，你接受嗎？"), 7)
        # a hashtag ends at punctuation: the sentence after it is content
        self.assertGreaterEqual(u("#李家超：口岸開通具三大意義 #新皇崗口岸 #深圳 #am730"), 9)
        # a Weibo mention ends at the bracket that follows it
        self.assertGreaterEqual(u("@香港特區政府運輸及物流局【新皇岗口岸启用仪式】今日运输及物流局局长，陈美宝出席了"), 20)
        # a resolved link's block is content
        self.assertGreater(u("https://x.co/a\n\n〔連結內容：伍富橋宣布BB早產一個月〕母子平安"), 10)

    def test_post_units_title_and_text_counted_once(self):
        self.assertEqual(thin.post_units({"title": "好長情！ #魏浚笙", "text": "好長情！ #魏浚笙 #am730"}), 3)
        self.assertEqual(thin.post_units({"title": "亂想", "text": "胡思亂想中 大跌 12%"}), 8)  # the title is inside the text
        # a transcript's [mm:ss] marks are not words
        self.assertEqual(thin.post_units({"title": "1100 新聞簡報", "text": None, "transcript": "[00:00] 今日新聞簡報，" * 3}), 1 + 4 + 18)
        self.assertGreater(thin.post_units({"title": "韓股", "text": None, "replies_text": ["跌到咁", "真係慘，全部蝕晒"]}), 4)


class Gate(unittest.TestCase):
    def setUp(self):
        CALLS.clear()

    def test_hashtags_only_rejected(self):
        url = "https://www.tiktok.com/@a/video/7695760276519456020"
        pid = ingest(social(url, "#台股#股市#股票"))["id"]
        streams.run_once(app.STREAMS)
        self.assertFalse(scored(pid))
        self.assertFalse(filed(url))
        self.assertFalse(model_saw("#台股#股市#股票"))
        st = state(pid)
        self.assertEqual((st["status"], st["units"]), ("rejected", 0))
        self.assertNotIn(pid, due_ids())
        v = item.view(pid)
        self.assertEqual(v["not_scored"], "thin")
        self.assertEqual(v["thin"]["status"], "rejected")
        self.assertEqual(v["thin"]["units"], 0)
        self.assertEqual(v["thin"]["label"], "內容不足，未評分")
        corpus.kv_set("edge:config", edge_config.build())
        self.assertEqual(edge("item.aiScores", pid)["not_scored"], "thin")

    def test_waits_for_queued_transcript_then_scored(self):
        url = "https://www.tiktok.com/@a/video/7695332144758983954"
        pid = ingest(social(url, "#李家超 #香港"))["id"]
        import media
        media.file_clips(pid, [{"key": "tt:7695332144758983954", "url": url, "kind": "video"}])
        streams.run_once(app.STREAMS)
        self.assertFalse(scored(pid))
        st = state(pid)
        self.assertEqual(st["status"], "waiting")
        self.assertIn("transcript", json.loads(st["waits"]))
        self.assertNotIn(pid, due_ids())  # not taken again before its next check
        v = item.view(pid)
        self.assertEqual(v["not_scored"], "thin_wait")
        self.assertIn("transcript", v["thin"]["waits"])
        self.assertTrue(v["thin"]["until"])
        # the transcript lands: the post's content changes, it is due and scored on it
        corpus.db().execute("UPDATE media SET status='done', transcript=%s, done_at=%s WHERE key=%s",
                            ("[00:00] 行政長官李家超今日宣布新一份施政報告的重點", NOW.isoformat(), "tt:7695332144758983954"))
        media.refresh_post(pid)
        self.assertIn(pid, due_ids())
        streams.run_once(app.STREAMS)
        self.assertTrue(scored(pid))
        self.assertTrue(model_saw("行政長官李家超今日宣布"))
        self.assertTrue(filed(url))
        self.assertIsNone(item.view(pid)["not_scored"])

    def test_wait_has_a_limit(self):
        url = "https://www.tiktok.com/@a/video/7695296187380845845"
        pid = ingest(social(url, "#台股 #股票投資"))["id"]
        import media
        media.file_clips(pid, [{"key": "tt:7695296187380845845", "url": url, "kind": "video"}])
        streams.run_once(app.STREAMS)
        self.assertEqual(state(pid)["status"], "waiting")
        later = NOW + thin.WAIT + timedelta(minutes=1)
        with mock.patch.object(streams, "clock", lambda: later), mock.patch.object(thin, "clock", lambda: later):
            self.assertIn(pid, due_ids())
            streams.run_once(app.STREAMS)
        self.assertEqual(state(pid)["status"], "rejected")
        self.assertFalse(scored(pid))
        self.assertFalse(filed(url))

    def test_waits_for_pictures_then_scored_on_their_text(self):
        url = "https://www.instagram.com/p/DeWpO0QkhVc/"
        pid = ingest(social(url, "開飯嘞", "開飯嘞", sid="instagram", source="am730 (Instagram)", section="am730hk"))["id"]
        import images
        images.link(pid, ["https://cdn.test/menu.jpg"])
        streams.run_once(app.STREAMS)
        self.assertFalse(scored(pid))
        self.assertIn("pictures", json.loads(state(pid)["waits"]))
        # the picture is read: its text joins the post (images.py), which is then scored
        corpus.db().execute("INSERT INTO images (hash, url, score, reason, text, at) VALUES ('h1', %s, 90, '告示', %s, %s)",
                            ("https://cdn.test/menu.jpg", "政府宣布今日起全港食肆須張貼衞生評級", NOW.isoformat()))
        corpus.db().execute("UPDATE post_images SET hash='h1' WHERE post_id=%s", (pid,))
        corpus.db().execute("UPDATE posts SET image_text=%s WHERE id=%s", ("政府宣布今日起全港食肆須張貼衞生評級", pid))
        streams.run_once(app.STREAMS)
        self.assertTrue(scored(pid))
        self.assertTrue(model_saw("政府宣布今日起全港食肆"))

    def test_meaningful_picture_is_content(self):
        url = "https://www.instagram.com/p/DeWJlc5zlr_/"
        pid = ingest(social(url, "好敬業！", "好敬業！ #朴寶劍", sid="instagram", source="am730 (Instagram)", section="am730hk"))["id"]
        import images
        images.link(pid, ["https://cdn.test/photo.jpg"])
        corpus.db().execute("INSERT INTO images (hash, url, score, reason, text, at) VALUES ('h2', %s, 70, '現場', '', %s)",
                            ("https://cdn.test/photo.jpg", NOW.isoformat()))
        corpus.db().execute("UPDATE post_images SET hash='h2' WHERE post_id=%s", (pid,))
        streams.run_once(app.STREAMS)
        self.assertTrue(scored(pid))
        self.assertIsNone(state(pid))

    def test_failed_link_rejected_not_held_for_ever(self):
        url = "https://www.threads.net/@x/post/gone2"
        pid = ingest(social(url, "https://example.invalid/gone", "https://example.invalid/gone", sid="instagram",
                            source="x (Threads)", section="x"))["id"]
        self.assertNotIn(pid, due_ids())  # held while links.py may still read it
        with mock.patch.object(links, "fetch_page", side_effect=OSError("dns")):
            for _ in range(links.TRIES):
                corpus.db().execute("UPDATE link_scan SET next_try=%s WHERE post_id=%s", (NOW.isoformat(), pid))
                links.run_once()
        self.assertEqual(corpus.db().execute("SELECT status FROM link_scan WHERE post_id=%s", (pid,)).fetchone()[0], "failed")
        streams.run_once(app.STREAMS)
        self.assertEqual(state(pid)["status"], "rejected")
        self.assertFalse(scored(pid))
        self.assertNotIn(pid, due_ids())
        self.assertEqual(item.view(pid)["not_scored"], "thin")

    def test_short_real_post_scored(self):
        url = "https://www.threads.com/@hkg.louis/post/DedqW00oKQi"
        pid = ingest(social(url, "李家超，你接受嗎？", sid="tikhub_threads", source="hkg.louis (Threads)",
                            section="Threads · 李家超"))["id"]
        streams.run_once(app.STREAMS)
        self.assertTrue(scored(pid))
        self.assertIsNone(state(pid))

    def test_filed_item_stays(self):
        url = "https://www.facebook.com/am730hk/posts/pfbid0filed"
        pid = ingest(social(url, "好長情！ #魏浚笙 #am730", "好長情！ #魏浚笙 #am730", sid="facebook",
                            source="am730 (Facebook)", section="am730hk"))["id"]
        corpus.db().execute("""INSERT INTO stream_items (day, stream, url, item) VALUES (%s, 'HKIC', %s, %s)""",
                            (NOW.strftime("%Y-%m-%d"), url, json.dumps({"url": url, "title": "好長情！", "score": 70})))
        streams.run_once(app.STREAMS)
        self.assertEqual(state(pid)["status"], "rejected")
        self.assertTrue(filed(url))  # filed means member: never taken back

    def test_dashboard_marks_rejected_posts(self):
        url = "https://www.tiktok.com/@b/video/7695293659700301077"
        pid = ingest(social(url, "#台股 #股票學習"))["id"]
        streams.run_once(app.STREAMS)
        conf = json.loads(json.dumps(edge_config.build(), default=str))
        got = edge("dashboard.markThin", conf, [{"id": pid}, {"id": -1}])
        self.assertEqual(got[0]["thin"], "內容不足，未評分")
        self.assertNotIn("thin", got[1])


class LinksReadPastHashtags(unittest.TestCase):
    def test_link_and_hashtags_is_only_a_link(self):
        t = "https://topick.hket.com/article/4207776 #晴報健康 #健康 #蔡堅 #醫生你有病"
        self.assertTrue(links.mostly_link(t))
        url = "https://www.facebook.com/Skyposthk/posts/pfbid0hashtaglink"
        pid = ingest(social(url, t, t, sid="facebook", source="晴報 (Facebook)", section="Skyposthk"))["id"]
        self.assertNotIn(pid, due_ids())  # held until the link is read
        self.assertIn(pid, [p["id"] for p in links.due(50)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
