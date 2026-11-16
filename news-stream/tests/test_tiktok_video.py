#!/usr/bin/env python3
"""TikTok videos of the posts passing stage 1 (tiktok_video.py; Deyao, 2026-10-22). On a throwaway database
(TEST_DATABASE_URL; its tables are dropped and made again); the models, yt-dlp and the bucket are stubbed.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_tiktok_video.py

1. Queueing at stage 1 (streams.score_posts): a TikTok video post Haiku scores 45 for a stream is queued (how stage1,
   screen 45, that stream), whatever stage 2 then says; one at 19 is not; a YouTube or Instagram post at 90 is not; a
   TikTok photo post at 90 is not. streams.SCREEN and tiktok_video.SCREEN agree.
2. The backlog / sweep: stored pairs whose stage-1 score reached 20 (scores.screen, or Haiku's own score where stage 2
   never ran) are queued once; a pair stage 1 put under 20 is not, even when stage 2 scored it high (filed for another
   reason); posts already queued are left alone; the sweep's day limit holds.
3. The download state: ok (the object key tiktok/<id>.mp4, bytes, size, codecs; the upload gets video/mp4), gone
   (TikTok's "Video not available"), a transient failure (HTTP 429) waits and counts a try, TRIES of them give it up
   (failed with the reason), a photo post (no video formats) fails at once; a second post with the same video is
   saved without a download; claims keep two workers off one post.
4. The download itself (a fake yt-dlp on PATH writing a real MP4 whose index is at the end): the command asks for the
   best quality with audio sorted H.264 first and no proxy; the result is a faststart MP4 (moov before mdat) with
   its duration, size and codecs read by ffprobe; an HEVC video is re-encoded to H.264 (same size, AAC kept).
5. S3 SigV4: s3.py and the edge app's server/s3.ts give AWS's published examples (presigned GET; GET with a Range
   header), and the same link for the same inputs.
6. The page link: item.view (Python and the edge app) carries the video's state; server/video.ts videoLinks gives an
   approved account a 1-hour presigned link to the object for playing and one that downloads it (attachment, the
   file's name), nothing for a video not saved; the route is /app/item/video (under /app/, behind Access, approved
   accounts only), and the item page plays it in a <video> from that link with a 下載影片 link.
7. The monitor's panel `videos`: per day passed / downloaded / bytes (a same-video row not counted twice), the queue,
   failures with their reasons, the bucket's own figure.
"""
import json, os, re, shutil, stat, subprocess, sys, tempfile, threading, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, s3, streams, tiktok_video as tv  # noqa: E402

TT = "https://www.tiktok.com/@rthk_news/video/{}"
NOW = datetime.now(corpus.HKT)


def post(url, title="t", seen=None):
    r = corpus.db().execute("""INSERT INTO posts (url, title, text, sid, source, seen, day) VALUES (%s, %s, %s, 'tikhub', 'x', %s, %s)
                               ON CONFLICT (url) DO UPDATE SET title = EXCLUDED.title RETURNING id""",
                            (url, title, title + " body", (seen or NOW).isoformat(timespec="seconds"), (seen or NOW).strftime("%Y-%m-%d"))).fetchone()
    return {"id": r["id"], "url": url, "title": title, "text": title + " body"}


def rows():
    return {r["post_id"]: dict(r) for r in corpus.db().execute("SELECT * FROM tiktok_videos").fetchall()}


class Stage1(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM tiktok_videos")

    def test_screen_agrees(self):
        self.assertEqual(tv.SCREEN, streams.SCREEN)

    def test_queued_at_stage1(self):
        st = app.STREAMS
        sid = st[0]["id"]
        ps = [post(TT.format(1001), "a"), post(TT.format(1002), "b"), post("https://www.youtube.com/watch?v=zz", "c"),
              post("https://www.instagram.com/reel/abc/", "d"), post("https://www.tiktok.com/@a/photo/1005", "e")]
        h = {0: 45, 1: 19, 2: 90, 3: 90, 4: 90}

        def fake(streams_, posts, purpose, model=None, checks=None):
            out = {}
            for i, p in enumerate(posts):
                n = h[[q["url"] for q in ps].index(p["url"])]
                s2 = 10 if model else n  # stage 2 rejects everything: the video is still kept (stage 1 decides)
                out[i] = {"sentiment": None, "labels": [], "raw": {}, "streams": {sid: {"score": s2, "reason": "", "category": None}}}
            return out
        old = streams._ask
        streams._ask = fake
        try:
            streams.score_posts(app.STREAMS, ps)
        finally:
            streams._ask = old
        got = rows()
        self.assertEqual(set(got), {ps[0]["id"]})
        r = got[ps[0]["id"]]
        self.assertEqual((r["status"], r["how"], r["screen"], r["stream"], r["video_id"]), ("queued", "stage1", 45, sid, "1001"))

    def test_hook_never_raises(self):
        self.assertEqual(tv.queue_stage1([{"id": None, "url": TT.format(5)}], {0: {"streams": {"X": {"score": 99}}}}), 0)
        self.assertEqual(tv.queue_stage1(None, {0: {}}), 0)


class Backlog(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM tiktok_videos")

    def score(self, pid, stream, score, model, screen=None):
        corpus.db().execute("""INSERT INTO scores (stream, post_id, score, model, screen, scored_at) VALUES (%s, %s, %s, %s, %s, %s)
                               ON CONFLICT (stream, post_id) DO UPDATE SET score=EXCLUDED.score, model=EXCLUDED.model, screen=EXCLUDED.screen""",
                            (stream, pid, score, model, screen, NOW.isoformat()))

    def test_backlog(self):
        a = post(TT.format(2001))["id"]   # stage 2 checked: screen 50
        b = post(TT.format(2002))["id"]   # stage 1 only, Haiku 41
        c = post(TT.format(2003))["id"]   # stage 1 under 20 (filed for another reason, stage 2 80): no
        d = post(TT.format(2004))["id"]   # Haiku 15: no
        e = post("https://www.youtube.com/watch?v=yy")["id"]
        old = post(TT.format(2006), seen=NOW - timedelta(days=10))["id"]
        self.score(a, "CEHK", 20, streams.CONFIRM_MODEL, 50)
        self.score(b, "CEHK", 41, ai.MODEL)
        self.score(c, "CEHK", 80, streams.CONFIRM_MODEL, 10)
        self.score(d, "CEHK", 15, ai.MODEL)
        self.score(e, "CEHK", 95, ai.MODEL)
        self.score(old, "HKIC", 70, streams.CONFIRM_MODEL, 60)
        self.score(old, "CEHK", 45, streams.CONFIRM_MODEL, 44)
        self.assertEqual(tv.backlog(3, how="sweep"), 2)
        self.assertEqual(set(rows()), {a, b})
        self.assertEqual(tv.backlog(), 1)  # all days: the 10-day-old post too, with its best stage-1 stream
        r = rows()[old]
        self.assertEqual((r["how"], r["screen"], r["stream"]), ("backlog", 60, "HKIC"))
        self.assertEqual(tv.backlog(), 0)


class FakeClient:
    bucket = "test-bucket"

    def __init__(self):
        self.puts = []

    def put_file(self, key, path, ct, extra=None):
        self.puts.append((key, ct, os.path.getsize(path)))
        return os.path.getsize(path)


class Worker(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM tiktok_videos")
        self.dl = tv.download

    def tearDown(self):
        tv.download = self.dl

    def run_one(self, vid, outcome):
        p = post(TT.format(vid))
        tv.queue(p["id"], p["url"], 50, "CEHK", "stage1")

        def fake(url, tmp):
            if isinstance(outcome, Exception):
                raise outcome
            f = os.path.join(tmp, "out.mp4")
            open(f, "wb").write(b"x" * 1234)
            return f, {"bytes": 1234, "duration": 12.3, "width": 1080, "height": 1920, "vcodec": "h264", "acodec": "aac", "format": "h264_1080p"}
        tv.download = fake
        cl = FakeClient()
        row = tv.claim()
        self.assertEqual(row["post_id"], p["id"])
        msg = tv.work(row, cl)
        tv.release(row["post_id"])
        return p["id"], rows()[p["id"]], cl, msg

    def test_ok(self):
        pid, r, cl, _ = self.run_one(3001, None)
        self.assertEqual((r["status"], r["s3_key"], r["bytes"], r["width"], r["height"], r["vcodec"]),
                         ("ok", "tiktok/3001.mp4", 1234, 1080, 1920, "h264"))
        self.assertEqual(cl.puts, [("tiktok/3001.mp4", "video/mp4", 1234)])
        # the same video on another post: saved without a download
        p2 = post("https://www.tiktok.com/@other/video/3001")
        tv.queue(p2["id"], p2["url"], 60, "HKIC", "stage1")
        tv.download = lambda *a: self.fail("downloaded again")
        row = tv.claim()
        tv.work(row, cl)
        r2 = rows()[p2["id"]]
        self.assertEqual((r2["status"], r2["s3_key"], r2["format"]), ("ok", "tiktok/3001.mp4", f"same video as post {pid}"))

    def test_gone(self):
        _, r, cl, _ = self.run_one(3002, tv.classify("ERROR: [TikTok] 3002: Video not available, status code 10204"))
        self.assertEqual(r["status"], "gone")
        self.assertIn("not available", r["error"])
        self.assertEqual(cl.puts, [])

    def test_login_only_is_not_gone(self):
        _, r, _, _ = self.run_one(3005, tv.classify("ERROR: [TikTok] 3005: This post may not be comfortable for some audiences. "
                                                     "Log in for access. Use --cookies-from-browser or --cookies"))
        self.assertEqual((r["status"], r["tries"]), ("failed", 1))
        self.assertIn("signed in", r["error"])

    def test_region_block_fails_at_once(self):
        _, r, _, _ = self.run_one(3006, tv.classify("ERROR: [TikTok] 3006: Your IP address is blocked from accessing this post"))
        self.assertEqual((r["status"], r["tries"]), ("failed", 1))
        self.assertIn("cluster's IP", r["error"])

    def test_no_video(self):
        _, r, _, _ = self.run_one(3003, tv.classify("ERROR: [TikTok] 3003: No video formats found!"))
        self.assertEqual((r["status"], r["tries"]), ("failed", 1))

    def test_transient_then_failed(self):
        pid, r, _, _ = self.run_one(3004, tv.classify("ERROR: [TikTok] 3004: Unable to download webpage: HTTP Error 429: Too Many Requests"))
        self.assertEqual((r["status"], r["tries"]), ("queued", 1))
        self.assertGreater(r["next_try"], datetime.now(timezone.utc) + timedelta(seconds=tv.RETRY[0] - 30))
        self.assertIsNone(tv.claim())  # waiting: not due yet
        for n in range(2, tv.TRIES + 1):
            corpus.db().execute("UPDATE tiktok_videos SET next_try = now() - interval '1 s' WHERE post_id=%s", (pid,))
            row = tv.claim()
            tv.work(row, FakeClient())
            tv.release(pid)
        r = rows()[pid]
        self.assertEqual((r["status"], r["tries"]), ("failed", tv.TRIES))
        self.assertIn("429", r["error"])

    def test_claims(self):
        a = post(TT.format(3010))
        tv.queue(a["id"], a["url"], 50, "CEHK", "stage1")
        got = []
        ts = [threading.Thread(target=lambda: got.append(tv.claim())) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sum(1 for g in got if g), 1)


FAKE_YTDLP = r"""#!/bin/sh
VC=${FAKE_VCODEC:-libx264}
printf '%s\n' "$@" > "$ARGS_FILE"
out=""; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
f=$(echo "$out" | sed 's/%(ext)s/mp4/')
ffmpeg -v error -y -f lavfi -i testsrc=size=320x568:rate=25:duration=2 -f lavfi -i sine=frequency=440:duration=2 \
  -c:v $VC -pix_fmt yuv420p -c:a aac -shortest "$f" || exit 1
printf '%s\t%s\n' "$f" "h264_540p_123-1"
"""


class Download(unittest.TestCase):
    def test_real_mp4(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("no ffmpeg")
        d = tempfile.mkdtemp()
        y = os.path.join(d, "yt-dlp")
        open(y, "w").write(FAKE_YTDLP)
        os.chmod(y, os.stat(y).st_mode | stat.S_IEXEC)
        args = os.path.join(d, "args")
        old = os.environ["PATH"]
        os.environ.update(PATH=d + ":" + old, ARGS_FILE=args)
        try:
            path, info = tv.download(TT.format(4001), tempfile.mkdtemp())
        finally:
            os.environ["PATH"] = old
        a = open(args).read().split("\n")
        self.assertEqual(a[a.index("-f") + 1], "bv*+ba/b")
        self.assertTrue(a[a.index("-S") + 1].startswith("res,vcodec:h264"))
        self.assertEqual(a[a.index("--proxy") + 1], "")
        self.assertEqual(a[-2], TT.format(4001))
        self.assertEqual((info["width"], info["height"], info["vcodec"], info["acodec"], info["format"]),
                         (320, 568, "h264", "aac", "h264_540p_123-1"))
        self.assertAlmostEqual(info["duration"], 2, delta=0.2)
        head = open(path, "rb").read()
        self.assertLess(head.find(b"moov"), head.find(b"mdat"), "not faststart: the index is after the data")

    def test_hevc_becomes_h264(self):
        if "libx265" not in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout:
            self.skipTest("no libx265")
        d = tempfile.mkdtemp()
        y = os.path.join(d, "yt-dlp")
        open(y, "w").write(FAKE_YTDLP)
        os.chmod(y, os.stat(y).st_mode | stat.S_IEXEC)
        old = os.environ["PATH"]
        os.environ.update(PATH=d + ":" + old, ARGS_FILE=os.path.join(d, "args"), FAKE_VCODEC="libx265")
        try:
            path, info = tv.download(TT.format(4002), tempfile.mkdtemp())
        finally:
            os.environ["PATH"] = old
            os.environ.pop("FAKE_VCODEC")
        self.assertEqual((info["vcodec"], info["acodec"], info["width"], info["height"]), ("h264", "aac", 320, 568))
        self.assertIn("hevc → h264", info["format"])
        head = open(path, "rb").read()
        self.assertLess(head.find(b"moov"), head.find(b"mdat"))


EX = {"endpoint": "https://examplebucket.s3.amazonaws.com", "region": "us-east-1", "accessKey": "AKIAIOSFODNN7EXAMPLE",
      "secretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "bucket": ""}
T0 = datetime(2013, 5, 24, tzinfo=timezone.utc)


def pyclient(c):
    return s3.Client(c["endpoint"], c["region"], c["accessKey"], c["secretKey"], c["bucket"])


class SigV4(unittest.TestCase):
    def test_aws_examples(self):
        c = pyclient(EX)
        self.assertTrue(c.presign("test.txt", 86400, now=T0).endswith(
            "X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"))
        _, h = c.sign("GET", "test.txt", headers={"Range": "bytes=0-9"}, payload=s3.EMPTY, now=T0)
        self.assertTrue(h["authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"))

    def test_edge_matches(self):
        from edge_call import edge
        self.assertEqual(edge("s3.presignForTest", EX, "test.txt", 86400, {}, "2013-05-24T00:00:00Z"), pyclient(EX).presign("test.txt", 86400, now=T0))
        hz = dict(EX, endpoint="https://fsn1.your-objectstorage.com", region="fsn1", bucket="yyc-tiktok-videos")
        p = {"response-content-disposition": 'attachment; filename="tiktok-1.mp4"', "response-content-type": "video/mp4"}
        self.assertEqual(edge("s3.presignForTest", hz, "tiktok/1.mp4", 3600, p, "2026-10-22T03:00:00Z"),
                         pyclient(hz).presign("tiktok/1.mp4", 3600, p, now=datetime(2026, 10, 22, 3, tzinfo=timezone.utc)))


HZ = {"endpoint": "https://fsn1.your-objectstorage.com", "region": "fsn1", "accessKey": "AK", "secretKey": "SK",
      "bucket": "yyc-tiktok-videos"}


class PageLink(unittest.TestCase):
    def test_links(self):
        from edge_call import edge
        import edge_config, item
        corpus.db().execute("DELETE FROM tiktok_videos")
        ok, q = post(TT.format(5001)), post(TT.format(5002))
        for p in (ok, q):
            tv.queue(p["id"], p["url"], 50, "CEHK", "stage1")
        tv.done(ok["id"], "tiktok/5001.mp4", {"bytes": 4567, "duration": 15.4, "width": 720, "height": 1280,
                                              "vcodec": "h264", "acodec": "aac", "format": "x"})
        edge_config.publish()
        ms = int(datetime(2026, 10, 22, 3, tzinfo=timezone.utc).timestamp() * 1000)
        L = edge("video.videoLinks", ok["id"], HZ, ms)
        self.assertEqual((L["status"], L["bytes"], L["duration"], L["filename"]), ("ok", 4567, 15, "tiktok-5001.mp4"))
        now = datetime(2026, 10, 22, 3, tzinfo=timezone.utc)
        self.assertEqual(L["url"], pyclient(HZ).presign("tiktok/5001.mp4", 3600, {"response-content-type": "video/mp4"}, now=now))
        self.assertTrue(L["url"].startswith("https://fsn1.your-objectstorage.com/yyc-tiktok-videos/tiktok/5001.mp4?"))
        self.assertIn("X-Amz-Expires=3600", L["url"])
        self.assertIn("response-content-disposition=attachment%3B%20filename%3D%22tiktok-5001.mp4%22", L["download"])
        self.assertEqual(L["expires_at"], "2026-10-22T04:00:00.000Z")
        Q = edge("video.videoLinks", q["id"], HZ, ms)
        self.assertEqual((Q["status"], Q["url"], Q["download"]), ("queued", None, None))
        self.assertIsNone(edge("video.videoLinks", post("https://www.youtube.com/watch?v=q1")["id"], HZ, ms))
        # the public item page: the state, never a link (Python item.view = the edge app's)
        v = edge("item.view", ok["id"])["video"]
        self.assertEqual(v, {"status": "ok", "bytes": 4567, "duration": 15, "width": 720, "height": 1280})
        self.assertEqual(item.video(corpus.db().execute("SELECT * FROM posts WHERE id=%s", (ok["id"],)).fetchone()), v)

    def test_route_and_page(self):
        route = open(os.path.join(HERE, "web-app/src/routes/app.item.video.ts")).read()
        self.assertIn('createFileRoute("/app/item/video")', route)
        self.assertIn("approved(", route)
        page = open(os.path.join(HERE, "web-app/src/routes/-ItemPage.tsx")).read()
        card = page[page.index("function VideoCard"):page.index("const mmss")]
        self.assertRegex(card, r"<video[^>]*src=\{L\.url\}[^>]*controls[^>]*playsInline")
        self.assertRegex(card, r"<a href=\{L\.download!\} download=\{L\.filename\}[^>]*>下載影片</a>")
        self.assertIn('"/app/item/video"', open(os.path.join(HERE, "web-app/src/api.ts")).read())
        edge_js = open(os.path.join(HERE, "edge/worker.js")).read()
        self.assertNotIn("item\\/video", edge_js)  # the edge app's own route: never the cluster


class Monitor(unittest.TestCase):
    def test_panel(self):
        import monitor
        corpus.db().execute("DELETE FROM tiktok_videos")
        a, b, c, d = (post(TT.format(n)) for n in (6001, 6002, 6003, 6004))
        for p in (a, b, c, d):
            tv.queue(p["id"], p["url"], 50, "CEHK", "stage1")
        tv.done(a["id"], "tiktok/6001.mp4", {"bytes": 5_000_000, "duration": 30})
        tv.done(d["id"], "tiktok/6001.mp4", {"bytes": 5_000_000, "duration": 30, "format": tv.SAME + str(a["id"])})
        tv.failed(b["id"], tv.Gone("Video not available"))
        tv.failed(c["id"], tv.Transient("HTTP Error 503"))
        corpus.kv_set("tiktok_video:bucket", {"objects": 1, "bytes": 5_000_000, "bucket": "b", "at": NOW.isoformat()})
        v = monitor.videos()
        today = NOW.strftime("%Y-%m-%d")
        day = v["by_day"][0]
        self.assertEqual((day["day"], day["queued"], day["ok"], day["bytes"], day["gone"]), (today, 4, 1, 5_000_000, 1))
        self.assertEqual((v["status"]["ok"]["n"], v["status"]["queued"]["n"], v["retrying"]), (2, 1, 1))
        self.assertEqual({r["status"] for r in v["recent_failures"]}, {"gone", "queued"})
        self.assertEqual(v["reasons"][0]["reason"], "Video not available")
        self.assertEqual(v["bucket"]["objects"], 1)
        self.assertIn("videos", monitor.PANELS)
        self.assertIn('"videos"', open(os.path.join(HERE, "web-app/src/server/monitor.ts")).read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
