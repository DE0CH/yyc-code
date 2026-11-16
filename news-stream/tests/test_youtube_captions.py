#!/usr/bin/env python3
"""YouTube through YYC's IPRoyal ISP IP only; no audio through the residential pool (Deyao, 2026-10-16: the
transcriber's YouTube audio used 5.19 GB of IPRoyal yycstream in 6 hours; then: "use the ISP IP for all YouTube" and
"run the transcription for all now"). On a throwaway database (TEST_DATABASE_URL; its tables are dropped and made
again). No network: yt-dlp, the caption file, the model and the proxy are fakes.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_youtube_captions.py

1. Every YouTube clip is transcribed from its audio by the model (「AI 轉錄」), the audio read through the route
   env MEDIA_ROUTES names for family youtube (the ISP IP, env ISP_PROXY), never RESI_PROXY. Its subtitles are kept
   too (media.captions): the uploader's in the video's language, else YouTube's auto-generated track of the video's
   language (`<lang>-orig`, never a translation), labelled 「YouTube 上載者字幕（lang）」 / 「YouTube 自動字幕（lang）」.
2. The ISP IP serves one client at a time: every YouTube fetch holds media.youtube_lock (a process lock and the
   Postgres advisory lock 'yyc youtube', shared with broadcast_fetch.py); the model's work runs outside it. A 403 on
   the media download is tried again.
3. The ISP IP's bytes are measured on the wire (resi.metered_url) and counted in kv isp:bytes:<day> and
   isp:jobbytes:<job>:<hour>, never in yycstream's proxy:bytes.
4. No YouTube route configured → no_direct. Other clips: their audio read directly or no_direct, never a proxy.
5. Posts waiting on a transcript stop waiting once their clip is no_direct (thin.py).
6. The item page shows the AI transcript and the subtitles; the broadcast CronJob's YouTube bulletins use the same
   route and lock; the monitor shows the ISP IP's traffic against its 100 GB / 30 days Fair Usage allowance.
"""
import json, os, re, sys, unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import flags_on  # noqa: E402,F401  picture analysis and transcription on (off since 2026-10-20: tests/test_pictures_transcription_off.py)
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
for k in ("MEDIA_ROUTES", "ISP_PROXY"):
    os.environ.pop(k, None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, item, media, resi, thin  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)

URL = "https://www.youtube.com/watch?v=abcdefghijk"
ISP = "http://ispuser:isppw@81.181.135.61:12323"
RESI = "http://yycstream:pw@geo.iproyal.com:12321"
ROUTES = {"MEDIA_ROUTES": json.dumps({"youtube": "ISP_PROXY"}), "ISP_PROXY": ISP, "RESI_PROXY": RESI}
VTT = """WEBVTT

00:00:01.000 --> 00:00:04.000
大家好，欢迎收睇

00:01:02.000 --> 00:01:05.000
今日嘅新闻
"""


def yt_info(manual=None, auto=None, language="yue", live="not_live", duration=125):
    sub = lambda langs: {l: [{"ext": "vtt", "url": f"https://www.youtube.com/api/timedtext?lang={l}&fmt=vtt"}]
                         for l in langs or []}
    return {"id": "abcdefghijk", "language": language, "live_status": live, "duration": duration,
            "subtitles": sub(manual), "automatic_captions": sub(auto)}


def post(url=URL, title="新聞", sid="rthk_youtube", source="RTHK (YouTube)"):
    corpus.ingest([{"url": url, "title": title, "text": None, "sid": sid, "source": source,
                    "published": media._now()}])
    return corpus.db().execute("SELECT id FROM posts WHERE url=%s", (url,)).fetchone()[0]


def wipe():
    for t in ("post_media", "media", "media_scan", "posts", "thin_posts"):
        corpus.db().execute(f"DELETE FROM {t}")
    corpus.db().execute("DELETE FROM kv WHERE k LIKE 'proxy:%%' OR k LIKE 'isp:%%'")


def row(key="yt:abcdefghijk"):
    return dict(corpus.db().execute("SELECT * FROM media WHERE key=%s", (key,)).fetchone())


def queued_yt():
    pid = post()
    media.record(pid, URL, "")
    return pid


class Fakes:
    """yt-dlp (-J → info, the download → a file), ffmpeg → a WAV path, the caption file, the model; records every
    yt-dlp command, and whether media.youtube_lock was held for it"""

    def __init__(self, info, vtt=VTT, fail_403=0, nbytes=5000):
        self.info, self.vtt, self.fail_403, self.nbytes = info, vtt, fail_403, nbytes
        self.cmds, self.held, self.locked, self.model_locked = [], [], False, []
        self.metered = []

    def run(self, cmd, timeout):
        if cmd[0] == "yt-dlp":
            self.cmds.append(cmd)
            self.held.append(self.locked)
            if "-J" in cmd:
                return json.dumps(self.info).encode()
            if self.fail_403:
                self.fail_403 -= 1
                raise RuntimeError("yt-dlp failed: ERROR: unable to download video data: HTTP Error 403: Forbidden")
            open(cmd[cmd.index("-o") + 1].replace("%(ext)s", "m4a"), "wb").close()
            return b""
        if cmd[0] == "ffmpeg":
            open(cmd[-1], "wb").close()
            return b""
        raise AssertionError(cmd)

    def caption_get(self, url, proxy):
        self.held.append(self.locked)
        self.caption_proxy = proxy
        return self.vtt

    def transcribe_wav(self, wav, model=None):
        self.model_locked.append(self.locked)
        return [(0, "模型轉寫")], 125.0, 0.0006

    @contextmanager
    def lock(self):
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    @contextmanager
    def metered_url(self, proxy):
        n = {"bytes": 0}
        self.metered.append(proxy)
        yield "http://127.0.0.1:9", n
        n["bytes"] = self.nbytes

    @contextmanager
    def patched(self):
        with mock.patch.object(media, "run", self.run), mock.patch.object(media, "caption_get", self.caption_get), \
                mock.patch.object(media, "transcribe_wav", self.transcribe_wav), \
                mock.patch.object(media, "youtube_lock", self.lock), \
                mock.patch.object(resi, "metered_url", self.metered_url), mock.patch.object(media.time, "sleep", lambda s: None), \
                mock.patch.dict(os.environ, ROUTES):
            yield self


class Choice(unittest.TestCase):
    def test_uploader_first_then_auto_in_its_language(self):
        def pick(info):  # → (lang, auto?) or None
            r = media.pick_captions(info)
            return r and (r[0], r[2])
        self.assertEqual(pick(yt_info(manual=["zh-HK"], auto=["yue-orig"])), ("zh-HK", False))
        self.assertEqual(pick(yt_info(auto=["yue-orig", "yue", "en", "zh-Hant"])), ("yue", True))
        self.assertEqual(pick(yt_info(auto=["zh-Hant"], language="zh-Hant")), ("zh-Hant", True))  # only one track
        self.assertIsNone(pick(yt_info(auto=["en", "zh-Hant"])))      # translations of a yue video: not what was said
        self.assertIsNone(pick(yt_info(auto=["en-orig"], language=None)))
        self.assertEqual(pick(yt_info(auto=["en-orig"], language="en")), ("en", True))
        self.assertIsNone(pick(yt_info(manual=["live_chat"])))
        # the -orig track's own URL is the one asked for (the auto-translated tracks answer 429)
        self.assertIn("lang=yue-orig", media.pick_captions(yt_info(auto=["yue-orig", "yue"]))[1])


class YouTube(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_transcribed_and_subtitles_kept(self):
        pid = queued_yt()
        self.assertEqual([m["key"] for m in media.due(10)], ["yt:abcdefghijk"])  # the transcriber's, like any clip
        with Fakes(yt_info(auto=["yue-orig", "zh-Hant"])).patched() as f:
            media._work(row())
        r = row()
        self.assertEqual((r["status"], r["model"], r["transcript"]), ("done", media.MODEL, "[00:00] 模型轉寫"))
        self.assertEqual(r["captions_model"], "youtube-auto-captions:yue")
        self.assertEqual(r["captions"], "[00:01] 大家好，歡迎收睇\n[01:02] 今日嘅新聞")
        # every YouTube request through the ISP route, under the one-client lock; the model outside it
        px = [c[c.index("--proxy") + 1] for c in f.cmds]
        self.assertEqual(set(px), {"http://127.0.0.1:9"})
        self.assertEqual(set(f.metered), {ISP})
        self.assertEqual(f.caption_proxy, "http://127.0.0.1:9")
        self.assertTrue(all(f.held), f.held)
        self.assertEqual(f.model_locked, [False])
        self.assertIn("--load-info-json", f.cmds[1])  # one extraction per clip
        self.assertEqual(f.cmds[1][f.cmds[1].index("-f") + 1], media.AUDIO_FORMAT)
        v = json.loads(json.dumps(item.view(pid), default=str))["media"][0]
        self.assertEqual((v["by_ai"], v["captions_name"]), (True, "YouTube 自動字幕（yue）"))
        self.assertGreater(v["captions_chars"], 0)
        t = json.loads(json.dumps(item.text(pid), default=str))
        self.assertEqual(t["media"]["yt:abcdefghijk"], "[00:00] 模型轉寫")
        self.assertIn("歡迎收睇", t["media_captions"]["yt:abcdefghijk"])

    def test_uploader_captions_label(self):
        pid = queued_yt()
        with Fakes(yt_info(manual=["zh-Hans"])).patched():
            media._work(row())
        self.assertEqual(row()["captions_model"], "youtube-captions:zh-Hans")
        v = json.loads(json.dumps(item.view(pid), default=str))["media"][0]
        self.assertEqual(v["captions_name"], "YouTube 上載者字幕（zh-Hans）")

    def test_no_subtitles_still_transcribed(self):
        queued_yt()
        with Fakes(yt_info()).patched():
            media._work(row())
        r = row()
        self.assertEqual((r["status"], r["captions"], r["captions_model"]), ("done", None, None))

    def test_403_on_download_tried_again(self):
        queued_yt()
        with Fakes(yt_info(), fail_403=1).patched() as f:
            media._work(row())
        self.assertEqual(row()["status"], "done")
        self.assertEqual(sum("--load-info-json" in c for c in f.cmds), 2)

    def test_isp_bytes_counted_apart_from_yycstream(self):
        queued_yt()
        with Fakes(yt_info(), nbytes=7_000_000).patched(), mock.patch.dict(os.environ, {"YYC_JOB": "transcribe"}), \
                mock.patch("costlog._job", None):
            media._work(row())
        t = datetime.now(resi.HKT)
        self.assertEqual(corpus.kv_get(f"isp:bytes:{t:%Y-%m-%d}"), 7_000_000)
        self.assertEqual(corpus.kv_get(f"isp:jobbytes:transcribe:{t:%Y-%m-%dT%H}"), 7_000_000)
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM kv WHERE k LIKE 'proxy:%%'").fetchone()[0], 0)

    def test_live_waits(self):
        queued_yt()
        with Fakes(yt_info(live="is_live")).patched():
            media._work(row())
        r = row()
        self.assertEqual((r["status"], r["tries"]), ("queued", 0))
        self.assertGreater(r["next_try"], media._now())

    def test_no_route_is_no_direct(self):
        queued_yt()
        with mock.patch.object(media, "run", side_effect=AssertionError("nothing fetched")), \
                mock.patch.dict(os.environ, {"RESI_PROXY": RESI}):
            media._work(row())
        self.assertEqual(row()["status"], "no_direct")

    def test_the_lock_is_one_postgres_lock(self):
        for f in ("media.py", "sites_fetch.py"):  # one Postgres lock, corpus.lock's (freed when a holder vanishes)
            self.assertIn('corpus.lock("yyc youtube"', open(f).read(), f)
        self.assertIn("media.youtube_clip_audio", open("broadcast_fetch.py").read())


class NoProxyAudio(unittest.TestCase):
    def setUp(self):
        wipe()

    def _clip(self, key, url, kind):
        corpus.db().execute("""INSERT INTO media (key, url, kind, host, status, first_seen, newest)
                               VALUES (%s, %s, %s, 'h', 'queued', %s, %s)""", (key, url, kind, media._now(), media._now()))

    def test_failed_direct_download_is_final_and_never_proxied(self):
        cmds = []

        def run(cmd, timeout):
            cmds.append(cmd)
            raise RuntimeError("yt-dlp failed: ERROR: login required")
        self._clip("fb:1", "https://www.facebook.com/reel/1/", "ytdlp")
        with mock.patch.object(media, "run", run), mock.patch.dict(os.environ, ROUTES):
            media._work(row("fb:1"))
        self.assertTrue(cmds)
        self.assertFalse(any("--proxy" in c for c in cmds))
        self.assertEqual(row("fb:1")["status"], "no_direct")
        self.assertEqual(media.due(10), [])

    def test_tvb_geo_without_a_route(self):
        self._clip("tvb:x", "https://news.tvb.com/tc/local/x", "tvb_geo")
        with mock.patch.object(media, "run", side_effect=AssertionError("nothing fetched")), \
                mock.patch("sources.fetch", side_effect=AssertionError("no page read through a proxy")), \
                mock.patch.dict(os.environ, ROUTES):
            media._work(row("tvb:x"))
        self.assertEqual(row("tvb:x")["status"], "no_direct")

    def test_no_resi_proxy_in_media(self):
        src = open("media.py").read()
        self.assertNotIn("resi.url(", src)
        self.assertNotIn("resi.metered(", src)

    def test_item_page(self):
        page = open("web-app/src/routes/-ItemPage.tsx").read()
        for s in ("無法直接下載，未轉錄", "captions_name", "media_captions"):
            self.assertIn(s, page)
        self.assertNotIn("等待讀取 YouTube 字幕", page)
        ts = open("web-app/src/server/item.ts").read()
        self.assertIn("youtube-auto-captions:", ts)
        self.assertIn("media_captions", ts)


class Thin(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_no_wait_once_final(self):
        pid = queued_yt()
        p = dict(corpus.db().execute("SELECT * FROM posts WHERE id=%s", (pid,)).fetchone())
        self.assertIn("transcript", thin._pending([p])[pid])
        corpus.db().execute("UPDATE media SET status='no_direct'")
        self.assertNotIn("transcript", thin._pending([p])[pid])


class Deploy(unittest.TestCase):
    def test_routes_and_secret(self):
        cj = open("deploy/apps/cronjobs.yaml").read()
        self.assertNotIn("  name: captions\n", cj)
        bc = cj[cj.index("  name: broadcast\n"):]
        bc = bc[:bc.index("\n---")] if "\n---" in bc else bc
        tr = open("deploy/apps/transcribe.yaml").read()
        self.assertRegex(tr, r"\n  replicas: 1\b")
        for name, seg in (("transcribe", tr), ("broadcast", bc)):
            self.assertRegex(seg, r'MEDIA_ROUTES[\s\S]{0,80}\\?"youtube\\?": *\\?"ISP_PROXY', name)
            self.assertIn("isp-proxy", seg, name)
        sec = open("deploy/apps/sealed/isp-proxy.yaml").read()  # a SealedSecret keeps the key names in clear
        self.assertRegex(sec, r"(?m)^\s+ISP_PROXY: Ag")
        self.assertIn("  - isp-proxy.yaml", open("deploy/apps/sealed/kustomization.yaml").read())


class Broadcast(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_youtube_bulletin_through_the_route(self):
        import broadcast_fetch
        ep = {"url": "https://www.youtube.com/watch?v=bulletin0001", "title": "有線新聞 2026-10-16",
              "published": datetime(2026, 10, 16, 6, 0, tzinfo=corpus.HKT), "media": "https://www.youtube.com/watch?v=bulletin0001"}
        prog = {"sid": "bc_icable", "who": "有線新聞", "name": "新聞", "kind": "youtube"}
        with Fakes(yt_info()).patched() as f:
            broadcast_fetch.file_episode(prog, ep)
        self.assertTrue(f.cmds and all(f.held))
        self.assertEqual(set(f.metered), {ISP})
        x = corpus.db().execute("SELECT text_via, text FROM posts WHERE url=%s", (ep["url"],)).fetchone()
        self.assertEqual(x["text_via"], "asr:" + media.MODEL)
        self.assertIn("模型轉寫", x["text"])

    def test_youtube_bulletin_without_a_route_not_retried(self):
        import broadcast_fetch
        ep = {"url": "https://www.youtube.com/watch?v=bulletin0002", "title": "x",
              "published": datetime(2026, 10, 16, 6, 0, tzinfo=corpus.HKT), "media": "https://www.youtube.com/watch?v=bulletin0002"}
        prog = {"sid": "bc_icable", "who": "有線新聞", "name": "新聞", "kind": "youtube"}
        with mock.patch.object(broadcast_fetch, "todo", lambda days: [(prog, ep)]), \
                mock.patch.object(media, "run", side_effect=AssertionError("nothing fetched")):
            broadcast_fetch.run()
        self.assertGreaterEqual(int(corpus.kv_get("bc:fail:" + ep["url"], 0)), 3)


class Monitor(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_isp_usage(self):
        import monitor
        t = datetime.now(resi.HKT)
        corpus.kv_set(f"isp:bytes:{t:%Y-%m-%d}", 2_000_000_000)
        corpus.kv_set(f"isp:bytes:{(t - timedelta(days=3)):%Y-%m-%d}", 5_000_000_000)
        corpus.kv_set(f"isp:bytes:{(t - timedelta(days=40)):%Y-%m-%d}", 9_000_000_000)  # outside the 30 days
        u = monitor.isp_usage()
        self.assertEqual((u["today_bytes"], u["last30_bytes"], u["allowance_bytes"]), (2e9, 7e9, 100e9))
        self.assertEqual(u["ip"], "81.181.135.61")
        self.assertIn("Fair Usage", open("web-app/src/routes/-MonitorPage.tsx").read())


if __name__ == "__main__":
    unittest.main()
