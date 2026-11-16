#!/usr/bin/env python3
"""Picture analysis and transcription off (Deyao, 2026-10-20: "Turn off image and transcript. They cost too much").
On a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again), no network: the model, yt-dlp,
the caption file and the proxy are fakes.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_pictures_transcription_off.py

1. Two flagd flags, `pictures` and `transcription`, OFF in the shipped flags.json on every site (really evaluated),
   each with its reason 'turned off by Deyao (cost)' (flags.off).
2. Pictures off: the picture worker claims and scores nothing; the stream scorer is text-only (no picture attached,
   none downloaded), and a meaningful picture is no longer a reason to score a near-empty post.
3. Transcription off: the transcriber sends nothing to the model and downloads no audio; a queued YouTube clip still
   gets its own subtitles (one metadata + one caption request through the ISP route, no model, no OpenRouter), kept
   in media.captions, and the post's transcript is those subtitles; the clip stays queued (turning transcription on
   again transcribes it); other clips are left queued, untouched. The broadcast job files nothing.
4. The near-empty gate (thin.py) waits for no picture and no transcript that won't come: only for a YouTube clip's
   subtitles not read yet.
5. The item page (Python item.view and the edge app's item.view): a turned-off feature is absent, never announced
   (Deyao, 2026-10-20; NAVIGATION.md): the clips and pictures waiting on it are left out (no note, no queue place);
   pictures analysed before and a clip's YouTube subtitles show as before.
6. The monitor: Deployment images, CronJob broadcast and the pipeline's pictures / transcribe panels say
   "turned off by Deyao (cost)", never failing; the page shows it.
7. The manifests: images at 0 replicas, broadcast suspended (both hold after the 2026-10-19 cluster-wide stop is
   reverted), the transcriber (subtitles only while off) and the picture worker read flagd.
"""
import io, json, os, sys, tempfile, unittest
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("FLAGD_HOST", None)  # the in-process provider on the checkout's flags.json
os.environ.pop("FLAGS_FILE", None)
for k in ("MEDIA_ROUTES", "ISP_PROXY"):
    os.environ.pop(k, None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, corpus, edge_config, flags, images, insights, item, media, streams, thin  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)
from edge_call import edge  # noqa: E402
from test_youtube_captions import Fakes, yt_info, URL as YT_URL  # noqa: E402
os.environ.pop("FLAGS_FILE", None)  # test_youtube_captions forces the flags on (flags_on): this test is the real file
assert flags.FILE.endswith(os.path.join("deploy", "apps", "flags", "flags.json"))

HOSTS = ("newswatch.yyc-internal.com", flags.BETA_HOST)
WHY = "turned off by Deyao (cost)"


def jpeg():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(b, "JPEG")
    return b.getvalue()


def post(url, title="新聞", text=None, sid="rthk_en", source="fixture", source_type="News"):
    corpus.ingest([{"url": url, "title": title, "text": text, "sid": sid, "source": source,
                    "published": insights.now().isoformat(timespec="seconds")}])
    pid = corpus.db().execute("SELECT id FROM posts WHERE url=%s", (url,)).fetchone()[0]
    corpus.db().execute("UPDATE posts SET source_type=%s WHERE id=%s", (source_type, pid))
    return pid


def full(pid):
    return dict(corpus.db().execute(f"SELECT p.*, {streams.HASH} AS hash FROM posts p WHERE id=%s", (pid,)).fetchone())


def wipe():
    for t in ("post_media", "media", "media_scan", "post_images", "images", "image_scan", "thin_posts", "posts"):
        corpus.db().execute(f"DELETE FROM {t}")


def picture(pid, url, score=None):
    h = None
    if score is not None:
        h = "h" + str(abs(hash(url)))
        corpus.db().execute("""INSERT INTO images (hash, score, reason, text, model, at, width, height)
                               VALUES (%s,%s,'表格','圖中文字','m','2026-10-20T10:00:00+08:00',400,300)""", (h, score))
    corpus.db().execute("INSERT INTO post_images (post_id, url, pos, hash) VALUES (%s,%s,0,%s)", (pid, url, h))


def clip(pid, key, url, kind="direct", host="news.fixture.example"):
    corpus.db().execute("""INSERT INTO media (key, url, kind, host, status, first_seen, newest)
                           VALUES (%s,%s,%s,%s,'queued',%s,%s)""", (key, url, kind, host, media._now(), media._now()))
    corpus.db().execute("INSERT INTO post_media (post_id, key) VALUES (%s,%s)", (pid, key))


class Flags(unittest.TestCase):
    def test_off_everywhere_really_evaluated(self):
        for name in ("pictures", "transcription"):
            self.assertIn(name, flags.FLAGS)
            for h in HOSTS:
                from openfeature.evaluation_context import EvaluationContext
                d = flags.client().get_boolean_details(name, True, EvaluationContext(attributes={"host": h}))
                self.assertIsNone(d.error_code, f"{name} on {h}: {d.error_code}")
                self.assertFalse(d.value)
                self.assertFalse(flags.on(name, h))
            self.assertEqual(flags.off(name), WHY)


class Pictures(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_worker_scores_nothing(self):
        pid = post("https://news.tvb.com/tc/off1")
        picture(pid, "https://a/p.jpg")
        with mock.patch.object(images, "score_one", side_effect=AssertionError("scored while off")):
            self.assertFalse(images.work_one())
        r = corpus.db().execute("SELECT tries, hash FROM post_images WHERE post_id=%s", (pid,)).fetchone()
        self.assertEqual((r["tries"], r["hash"]), (0, None), "the picture stays queued")

    def test_scoring_is_text_only(self):
        pid = post("https://news.tvb.com/tc/off2", text="消防條例")
        picture(pid, "https://a/t.jpg", score=95)
        sent = []
        with mock.patch.object(ai, "chat", lambda msgs, *a, **k: sent.append(msgs) or {"content": '{"posts": []}'}), \
                mock.patch.object(images, "download", side_effect=AssertionError("picture downloaded")):
            streams.score_posts(insights.streams(), [full(pid)])
        self.assertTrue(sent)
        content = sent[0][0]["content"]
        kinds = [c["type"] for c in content] if isinstance(content, list) else ["text"]
        self.assertNotIn("image_url", kinds)

    def test_thin_waits_for_no_picture(self):
        pid = post("https://news.tvb.com/tc/off3", title="圖")
        picture(pid, "https://a/q.jpg")  # not read yet
        self.assertNotIn("pictures", thin._pending([full(pid)])[pid])
        other = post("https://news.tvb.com/tc/off4", title="圖")  # never scanned: the scan would have looked
        self.assertNotIn("pictures", thin._pending([full(other)])[other])
        good = post("https://news.tvb.com/tc/off5", title="圖")
        picture(good, "https://a/m.jpg", score=95)
        self.assertEqual(thin._meaningful([good]), set(), "no picture is shown to the scorer")


class Transcription(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_youtube_subtitles_only(self):
        pid = post(YT_URL, sid="rthk_youtube", source="RTHK (YouTube)", source_type="Video")
        media.record(pid, YT_URL, "")
        f = Fakes(yt_info(auto=["yue-orig"]))
        with f.patched():
            self.assertTrue(media.work_one())
            self.assertFalse(media.work_one(), "subtitles read once")
        self.assertEqual(f.model_locked, [], "no model call")
        self.assertTrue(all("-J" in c for c in f.cmds), f"audio downloaded: {f.cmds}")
        m = dict(corpus.db().execute("SELECT * FROM media WHERE key='yt:abcdefghijk'").fetchone())
        self.assertEqual(m["status"], "queued", "stays queued: transcribed once transcription is on")
        self.assertIsNotNone(m["captions_at"])
        self.assertIn("今日嘅新聞", m["captions"])
        self.assertTrue(m["captions_model"].startswith(media.AUTO_CAPTIONS))
        self.assertIn("今日嘅新聞", corpus.db().execute("SELECT transcript FROM posts WHERE id=%s", (pid,)).fetchone()[0])
        self.assertTrue(f.metered, "through the ISP route")

    def test_other_clips_untouched(self):
        pid = post("https://news.fixture.example/v1")
        clip(pid, "https://news.fixture.example/v1.mp4", "https://news.fixture.example/v1.mp4")
        with mock.patch.object(media, "run", side_effect=AssertionError("fetched")), \
                mock.patch.object(media, "transcribe_wav", side_effect=AssertionError("model called")):
            self.assertFalse(media.work_one())
        r = corpus.db().execute("SELECT status, tries FROM media").fetchone()
        self.assertEqual((r["status"], r["tries"]), ("queued", 0))

    def test_broadcast_files_nothing(self):
        import broadcast_fetch
        with mock.patch.object(broadcast_fetch, "todo", side_effect=AssertionError("looked for episodes")), \
                mock.patch.object(broadcast_fetch, "transcribe", side_effect=AssertionError("transcribed")):
            broadcast_fetch.run()

    def test_thin_waits_only_for_subtitles(self):
        a = post("https://news.fixture.example/v2", title="片")
        clip(a, "https://news.fixture.example/v2.mp4", "https://news.fixture.example/v2.mp4")
        self.assertEqual([w for w in thin._pending([full(a)])[a] if w in ("transcript", "captions")], [])
        b = post(YT_URL, title="片", sid="rthk_youtube", source_type="Video")
        media.record(b, YT_URL, "")
        self.assertIn("captions", thin._pending([full(b)])[b])
        corpus.db().execute("UPDATE media SET captions_at=%s", (media._now(),))
        self.assertNotIn("captions", thin._pending([full(b)])[b])
        self.assertIn("captions", thin.WAITS_ZH)


class ItemPage(unittest.TestCase):
    """Deyao, 2026-10-20: a turned-off feature is absent, never announced (NAVIGATION.md): the clips and pictures that
    wait on it are left out (no "off" note, no queue place); what exists stays (scored pictures, subtitles)"""
    def setUp(self):
        wipe()

    def check(self, v):
        self.assertEqual([m["key"] for m in v["media"]], ["yt:abcdefghijk"], "only the clip with its subtitles")
        self.assertTrue(v["media"][0]["captions_name"].startswith("YouTube"))
        self.assertIsNone(v["media"][0]["queue"])
        self.assertNotIn(v["media"][0]["status"], ("queued",))
        self.assertEqual([(p["url"], p["status"]) for p in v["pictures"]["items"]], [("https://a/s.jpg", "scored")])

    def test_left_out(self):
        pid = post(YT_URL, sid="rthk_youtube", source="RTHK (YouTube)", source_type="Video")
        media.record(pid, YT_URL, "")
        corpus.db().execute("""UPDATE media SET captions='[00:01] 大家好', captions_model='youtube-auto-captions:yue',
                               captions_at=%s""", (media._now(),))
        clip(pid, "https://news.fixture.example/v3.mp4", "https://news.fixture.example/v3.mp4")
        picture(pid, "https://a/r.jpg")  # never analysed: absent
        picture(pid, "https://a/s.jpg", score=95)  # analysed before: shown as before
        self.check(item.view(pid))
        edge_config.publish()
        self.check(edge("item.view", pid))
        empty = post("https://news.fixture.example/v4")
        clip(empty, "https://news.fixture.example/v4.mp4", "https://news.fixture.example/v4.mp4")
        picture(empty, "https://a/t.jpg")
        for v in (item.view(empty), edge("item.view", empty)):
            self.assertEqual(v["media"], [])
            self.assertEqual(v["pictures"]["items"], [])

    def test_page_never_announces(self):
        page = open("web-app/src/routes/-ItemPage.tsx").read()
        for s in ("轉錄已關閉", "圖片分析已關閉", "已關閉", "已停用"):
            self.assertNotIn(s, page)
        nav = open("NAVIGATION.md").read()
        self.assertIn("absent, never announced", nav)
        cfg = open("web-app/src/server/config.ts").read()
        self.assertIn("pipelineOn", cfg)


class Monitor(unittest.TestCase):
    def test_jobs_and_panels(self):
        import monitor
        deps = {"items": [{"metadata": {"name": n}, "spec": {"replicas": r, "template": {"spec": {"containers": [{"image": "x"}]}}},
                           "status": {}} for n, r in (("images", 0), ("transcribe", 1), ("poller", 1))]}
        cjs = {"items": [{"metadata": {"name": "broadcast"}, "spec": {"schedule": "20 * * * *", "suspend": True}, "status": {}}]}

        def k8s(path):
            return deps if "deployments" in path else cjs if "cronjobs" in path else {"items": []}
        with mock.patch.object(monitor, "k8s", k8s):
            j = monitor.jobs()
        d = {x["name"]: x for x in j["deployments"]}
        self.assertEqual(d["images"]["off"], WHY)
        self.assertIn(WHY, d["transcribe"]["off"])
        self.assertIsNone(d["poller"].get("off"))
        self.assertEqual(j["cronjobs"][0]["off"], WHY)
        p = monitor.pipeline()
        self.assertEqual(p["pictures"]["off"], WHY)
        self.assertEqual(p["transcribe"]["off"], WHY)
        page = open("web-app/src/routes/-MonitorPage.tsx").read()
        for s in ("d.off", "cj.off", "pc.off", "tr.off"):
            self.assertIn(s, page)


class Manifests(unittest.TestCase):
    def test_off_in_git(self):
        import re
        img = open("deploy/apps/images.yaml").read()
        self.assertRegex(img, r"(?m)^  replicas: 0\b")
        self.assertIn("FLAGD_HOST", img)
        tr = open("deploy/apps/transcribe.yaml").read()
        self.assertIn("FLAGD_HOST", tr)
        cj = open("deploy/apps/cronjobs.yaml").read()
        bc = cj[cj.index("  name: broadcast\n"):]
        bc = bc[:bc.index("\n---") if "\n---" in bc else len(bc)]
        self.assertRegex(bc, r"(?m)^  suspend: true\b")


if __name__ == "__main__":
    unittest.main()
