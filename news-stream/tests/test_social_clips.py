#!/usr/bin/env python3
"""Social videos transcribed (Deyao, 2026-10-15: "tiktok needs transcript too"), on a throwaway database
(TEST_DATABASE_URL; its tables are dropped and made again). No network: TikHub, the profile pages and yt-dlp are fakes.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_social_clips.py

1. TikTok (TikHub keyword search and the watched accounts): every video post carries its clip, tt:<aweme_id>, read
   from the post's page by yt-dlp with no proxy (works from the server, no expiring CDN link); a photo post has none.
2. Threads keyword search (TikHub): a post with video_versions carries threads:<code>, its own video URL, read
   directly (tikhub_fetch.meta_media, as for the watched profiles). Instagram, the watched Threads profiles and Facebook:
   tests/test_social_media_capture.py and the collectors' own tests.
3. The clips go through the same queue and model as the articles' (media.py): filed at ingest, so a post waiting for
   its transcript is visible at once: media.pending(post ids) (a post_media row whose media row is 'queued').
"""
import json, os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, media, sources, tikhub_fetch, watch_fetch  # noqa: E402



AWEME = {"aweme_id": "7695432528928296213", "desc": "李家超 施政報告 #香港", "create_time": 1792000000,
         "author": {"unique_id": "this.is.china.prc", "nickname": "This is China"},
         "video": {"duration": 79345, "play_addr": {"url_list": ["https://v45-eu.tiktokcdn.com/x/6ad1c3cf/video/"]}},
         "statistics": {"play_count": 10, "digg_count": 2}}
PHOTO = dict(AWEME, aweme_id="7695432528928296999", image_post_info={"images": [{}]})
TT_CLIP = {"key": "tt:7695432528928296213", "url": "https://www.tiktok.com/@this.is.china.prc/video/7695432528928296213",
           "kind": "ytdlp"}


class TikTok(unittest.TestCase):
    def test_search_posts_carry_their_clip(self):
        with mock.patch.object(tikhub_fetch, "call", lambda *a, **k: {"data": {"search_item_list": [
                {"aweme_info": AWEME}, {"aweme_info": PHOTO}]}}):
            got = tikhub_fetch.tiktok("李家超")
        by = {i["url"]: i for i in got}
        self.assertEqual(by[TT_CLIP["url"]]["clips"], [TT_CLIP])
        self.assertNotIn("clips", by["https://www.tiktok.com/@this.is.china.prc/video/7695432528928296999"])  # photos

    def test_watched_accounts_carry_their_clip(self):
        got = watch_fetch.tiktok_posts({"aweme_list": [AWEME, PHOTO]}, {"handle": "@this.is.china.prc"})
        self.assertEqual(got[0]["clips"], [TT_CLIP])
        self.assertNotIn("clips", got[1])


class Threads(unittest.TestCase):
    VIDEO = "https://scontent.cdninstagram.com/o1/v/t2/f2/m86/abc.mp4?efg=1&oe=6AD2"

    def test_search_video_posts(self):
        ps = [{"code": "DPv1", "taken_at": 1792000000, "user": {"username": "cgtn"}, "caption": {"text": "v"},
               "video_versions": [{"type": 101, "url": self.VIDEO}]},
              {"code": "DPt1", "taken_at": 1792000000, "user": {"username": "cgtn"}, "caption": {"text": "t"},
               "video_versions": None}]
        with mock.patch.object(tikhub_fetch, "call", lambda *a, **k: {"data": {"posts": ps}}):
            got = {i["url"]: i for i in tikhub_fetch.threads("香港")}
        self.assertEqual(got["https://www.threads.com/@cgtn/post/DPv1"]["clips"],
                         [{"key": "threads:DPv1", "url": self.VIDEO, "kind": "direct"}])
        self.assertNotIn("clips", got["https://www.threads.com/@cgtn/post/DPt1"])


class Queue(unittest.TestCase):
    def setUp(self):
        for t in ("post_media", "media", "media_scan", "posts"):
            corpus.db().execute(f"DELETE FROM {t}")

    def test_ingest_queues_and_pending_is_visible(self):
        it = {"url": TT_CLIP["url"], "title": "李家超 施政報告", "sid": "tikhub", "source": "This is China (TikTok)",
              "published": "2026-10-15T12:00:00+08:00", "clips": [TT_CLIP]}
        it.update(sources.extras("tikhub", it))
        corpus.ingest([it])
        pid = corpus.db().execute("SELECT id FROM posts WHERE url=%s", (TT_CLIP["url"],)).fetchone()[0]
        m = corpus.db().execute("SELECT kind, status FROM media WHERE key=%s", (TT_CLIP["key"],)).fetchone()
        self.assertEqual((m["kind"], m["status"]), ("ytdlp", "queued"))
        self.assertEqual(media.pending([pid, 999999]), {pid})  # the signal: content may still come
        media.finish(TT_CLIP["key"], "[00:00] 施政報告", 79.3, 0.0006, "qwen/qwen3-asr-1.7b")
        self.assertEqual(media.pending([pid]), set())
        self.assertEqual(corpus.db().execute("SELECT transcript FROM posts WHERE id=%s", (pid,)).fetchone()[0],
                         "[00:00] 施政報告")

    def test_ytdlp_kind_reads_without_proxy(self):
        cmds = []

        def run(cmd, timeout):
            cmds.append(cmd)
            if cmd[0] == "yt-dlp":
                open(cmd[cmd.index("-o") + 1].replace("%(ext)s", "m4a"), "wb").close()
            return ""
        with mock.patch.object(media, "run", run), mock.patch.dict(os.environ, {"RESI_PROXY": "socks5://p"}):
            media.fetch_audio(dict(TT_CLIP), media.tempfile.mkdtemp())
        self.assertEqual(cmds[0][0], "yt-dlp")
        self.assertNotIn("--proxy", cmds[0])
        self.assertEqual(cmds[0][-1], TT_CLIP["url"])
        self.assertEqual(cmds[1][0], "ffmpeg")


class ProxyOut(unittest.TestCase):
    """live, 2026-10-15: the IPRoyal residential balance ran out ("Tunnel connection failed: 402 Payment Required"),
    and 107 YouTube / Facebook / TVB clips were given up in 35 minutes, 3 tries each. A proxy out of credit is a safety
    stop like OpenRouter's: the clip waits (RETRY_AFTER), no try counted, however old it is"""
    ERR = ("ERROR: [youtube] -ErEC4hkTHo: Unable to download API page: <urlopen error Tunnel connection failed: "
           "402 Payment Required> (caused by URLError(OSError('Tunnel connection failed: 402 Payment Required')))")

    def setUp(self):
        for t in ("post_media", "media", "media_scan", "posts"):
            corpus.db().execute(f"DELETE FROM {t}")

    def test_run_raises_proxy_out(self):
        fake = mock.Mock(returncode=1, stderr=self.ERR.encode(), stdout=b"")
        with mock.patch.object(media.subprocess, "run", lambda *a, **k: fake):
            with self.assertRaises(media.ProxyOut):
                media.run(["yt-dlp", "x"], 10)

    def test_clip_waits_without_a_try(self):
        corpus.db().execute("""INSERT INTO media (key, url, kind, host, status, first_seen, newest)
                               VALUES ('yt:old', 'https://www.youtube.com/watch?v=old', 'proxy', 'www.youtube.com', 'queued',
                                       '2026-01-01T00:00:00+08:00', '2026-01-01T00:00:00+08:00')""")
        m = dict(corpus.db().execute("SELECT * FROM media WHERE key='yt:old'").fetchone())
        with mock.patch.object(media, "transcribe_source", mock.Mock(side_effect=media.ProxyOut(self.ERR))):
            media._work(m)
        r = corpus.db().execute("SELECT status, tries, next_try FROM media WHERE key='yt:old'").fetchone()
        self.assertEqual((r["status"], r["tries"]), ("queued", 0))
        self.assertTrue(r["next_try"])



class AudioFormat(unittest.TestCase):
    """a configured audio route (media.AUDIO_ROUTES) is paid traffic too: the smallest audio-only format, never video"""
    def test_audio_format_smallest_first(self):
        self.assertTrue(media.AUDIO_FORMAT.startswith("600/599/"))
        self.assertNotIn("best/", media.AUDIO_FORMAT)
        self.assertNotIn("bestvideo", media.AUDIO_FORMAT)


if __name__ == "__main__":
    unittest.main()
