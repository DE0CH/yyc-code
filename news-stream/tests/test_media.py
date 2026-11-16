#!/usr/bin/env python3
"""Video and audio in articles and posts → transcripts (media.py), on a throwaway database (TEST_DATABASE_URL; its
tables are dropped and made again). No network and no model calls: OpenRouter, the media download and Discord are
replaced by fakes; ffmpeg runs for real on a generated WAV.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_media.py

1. Detection, per source, from saved real pages (tests/fixtures/media/*.html.gz, read 2026-10-14; their URLs in
   pages.json): TVB (own clip, geo-restricted clip, none), Now, 有線, RTHK (audio + video), 星島, 點新聞 / 大公文匯網 /
   文匯網, 香港01 (self-hosted + YouTube), 商報, 紫荊, The Standard, 政府新聞網, 鳳凰網, on.cc, HKET (a programme, as a post
   of its own), 巴士的報 (video.js + Facebook, YouTube), SCMP (YouTube), am730 (Facebook, YouTube). Related-article
   videos on the page are not the article's.
2. Text-to-speech readouts of the article are never taken (SCMP articleSpeeches, 文匯 text2Audio, 巴士的報 article-audio).
3. Dedupe: one clip on several articles (the same dams clip on 點新聞 and 大公文匯網) is one queue row linked to every
   post, and a post found after the clip was transcribed gets the transcript at once.
4. The queue: newest post first; a failure counts, TRIES of them give the clip up; a busy host (429 / 5xx) waits and
   does not count; each post is scanned once; the full-text readers' pages are used (seen_page) without a refetch.
5. The model call is the benchmark's (prompt "verbatim" word for word, temperature 0, max_tokens 16000, reasoning
   low, MP3 input); the model is one setting; the transcript is the pieces in order, '[mm:ss] text' about once a
   minute (cut at a quiet point), Hong Kong Traditional characters, spoken Cantonese kept; spend is logged under
   purpose transcribe.
6. The transcript joins the post's search text and the scorer's input, and the post is due for scoring again.
7. OpenRouter refusing for credit: ai.py's safety stop (kv openrouter_limit, shown on the monitor, Discord once
   through notify.py), the clip stays queued (no try counted); the next success clears it.
8. One post's data (item.py): item.view lists the clips without their text; item.text has the transcript; the edge
   app's ItemPage has the 「影片逐字稿」 section.
9. The broadcast CronJob is on and uses the same model; the transcriber is a single-replica Deployment with a lock.
"""
import gzip, io, json, os, re, subprocess, sys, tempfile, unittest, urllib.error, urllib.parse
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import flags_on  # noqa: E402,F401  picture analysis and transcription on (off since 2026-10-20: tests/test_pictures_transcription_off.py)
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, corpus, item, media, streams  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)

FIX = os.path.join(HERE, "tests", "fixtures", "media")
URLS = json.load(open(os.path.join(FIX, "pages.json")))


def page(name):
    with gzip.open(os.path.join(FIX, name + ".html.gz"), "rt") as f:
        return f.read()


def keys(name):
    return [c["key"] for c in media.detect(URLS[name], page(name))]


# what each saved page carries, read by hand from the page (2026-10-14)
EXPECTED = {
    "tvb_video": ["tvb:9f22d17b181346ab8be36e5ef400380c"],
    "tvb_geo": ["tvb:debf571101f14de8a6e801fe14ff97fe"],
    "tvb_none": [],  # the related articles' clips on the page are not this article's
    "now_video": ["now:665829"],
    "now_none": [],  # videoStatus 0
    "icable_video": ["https://vod.i-cable.com/N_20261014_CT-ISLAND-261014-1800_logo/NEWSHLS/N_20261014_CT-ISLAND-261014-1800_logo.m3u8"],
    "icable_none": [],
    "rthk_audio_video": ["https://newsstatic.rthk.hk/audios/mfile_1873950_1_20261014185443.mp3",
                         "https://newsstatic.rthk.hk/videos/vfile_1873950_1_20261014203542.mp4"],
    "stheadline_video": ["hkhl:4098cffac7ab71f18cbce6f6c55a0102"],
    "stheadline_none": [],
    "dotdot_old_clip": ["dams:6a4342b3e4b08c07fcfe3405"],  # the article reuses a June clip: still its own
    "tkww_shared": ["dams:69beb00ee4b08c07fcdcfbd5"],
    "dotdot_shared": ["dams:69beb00ee4b08c07fcdcfbd5"],
    "wenweipo_video_tts": ["dams:6acee7c0e4b08c07fd2194cf"],
    "wenweipo_tts_only": [],
    "hk01_selfhost_yt": ["hk01:ef2656c4-7838-467e-ad87-c5ff64eb0b2c", "yt:qCLb2AI-Y9w"],
    "hk01_youtube": ["yt:1pXboxvIw1I"],
    "hk01_none": [],  # a related article's YouTube video is on the page
    "hkcd_video": ["https://img.hkcd.com/video/202610/202610142149182057724116.mp4"],
    "hkcd_none": [],
    "bauhinia_video": ["https://res.bauhk.com/contentcloud/44/REPRINT/MEDIA/VIDEO/2026/10/14/35aadbca77ef4679b1d9d74dade6e286_wh720x1280.mp4"],
    "bauhinia_none": [],
    "standard_video": ["https://video.hkstandard.com.hk/758be0f5-819e-41e4-ac28-c5f47cf8829f/mp4/1818705.mp4"],
    "standard_none": [],  # every page lists other articles' videos (Social Buzz)
    "newsgov_video": ["https://www.news.gov.hk/chi/2026/10/20261014/20261014_133436_950/videos/20261014173642894.mp4"],
    "newsgov_none": [],
    "ifeng_video": ["https://video19.ifeng.com/video09/2026/10/14/p7516072385422831616-102-174818.mp4"],
    "ifeng_sidebar_only": [],  # the sidebar's videoPlayUrl list is not the article's
    "oncc_video": ["oncc:OEN261014-142546-1934-M"],
    "oncc_none": [],
    "hket_programme": ["hket:4207783"],
    "hket_none": [],
    "bastille_video_fb": ["https://media.bastillepost.com/wp-content/uploads/hongkong/2026/10/20261014%E8%AD%A6%E6%96%B9.mp4",
                          "fb:4663117527258279"],
    "bastille_youtube": ["yt:gOEhB9IgoQo"],
    "bastille_tts_only": [],
    "scmp_youtube_tts": ["yt:_XZmr1ousHc", "yt:cyVTEGt_pLA"],
    "scmp_tts_only": [],
    "am730_facebook": ["fb:1418588630451337"],
    "am730_youtube": ["yt:3uLaL1qt6M4"],
}


class Detect(unittest.TestCase):
    def test_every_saved_page(self):
        self.assertEqual(sorted(EXPECTED), sorted(URLS), "every saved page has its expectation")
        for name, want in EXPECTED.items():
            with self.subTest(name):
                self.assertEqual(keys(name), want)

    def test_tvb_geo_page_through_a_route(self):
        """live 2026-10-14: the geo-restricted clip's page URL has Chinese in its path, and urllib refused it (ascii).
        Read only over a configured Hong Kong route (AUDIO_ROUTES "tvb"), never RESI_PROXY (Deyao, 2026-10-16)"""
        import sources
        asked = []

        def fetch(url, proxy=None, timeout=30, **kw):
            url.encode("ascii")
            asked.append((url, proxy))
            return page("tvb_video").replace("1200840", "1200854")
        with mock.patch.object(sources, "fetch", fetch):
            got = media.tvb_geo_url(URLS["tvb_geo"], "http://hk-route:1")
        self.assertTrue(got.startswith("https://pro-vod-cdn.tvb.com/"))
        self.assertEqual(asked[0][1], "http://hk-route:1")

    def test_media_urls_and_kinds(self):
        got = {c["key"]: c for n in EXPECTED for c in media.detect(URLS[n], page(n))}
        self.assertEqual(got["now:665829"]["url"], "https://news-videos.now.com/nownews/665829/hls/665829.m3u8")
        self.assertTrue(got["tvb:9f22d17b181346ab8be36e5ef400380c"]["url"].startswith("https://pro-vod-cdn.tvb.com/9f22d17b"))
        geo = got["tvb:debf571101f14de8a6e801fe14ff97fe"]
        self.assertEqual((geo["kind"], geo["url"]), ("tvb_geo", URLS["tvb_geo"]))  # the page again, through the HK proxy
        self.assertEqual(got["oncc:OEN261014-142546-1934-M"]["url"],
                         "https://video-cdn.on.cc/Video/202610/OEN261014-142546-1934-M.m3u8")
        self.assertTrue(got["dams:69beb00ee4b08c07fcdcfbd5"]["url"].endswith(".m3u8"))
        self.assertEqual(got["https://www.news.gov.hk/chi/2026/10/20261014/20261014_133436_950/videos/20261014173642894.mp4"]["url"],
                         "https://www.news.gov.hk/videodownload/chi/2026/10/20261014/20261014_133436_950/videos/20261014173642894.mp4")
        self.assertEqual(got["hk01:ef2656c4-7838-467e-ad87-c5ff64eb0b2c"]["url"],
                         "https://vdo-cdn-static.hk01.com/elemental/ef2656c4-7838-467e-ad87-c5ff64eb0b2c_20261014_120129_120550.m3u8")
        for k in ("yt:qCLb2AI-Y9w", "yt:gOEhB9IgoQo"):
            self.assertEqual(got[k]["kind"], "proxy", k)  # YouTube: through RESI_PROXY (it refuses the server's IP)
        self.assertEqual(got["fb:1418588630451337"]["kind"], "ytdlp")  # Facebook: from the server, no proxy (measured)
        self.assertEqual(got["yt:gOEhB9IgoQo"]["url"], "https://www.youtube.com/watch?v=gOEhB9IgoQo")
        self.assertEqual(got["fb:1418588630451337"]["url"], "https://www.facebook.com/reel/1418588630451337/")
        prog = got["hket:4207783"]["program"]
        self.assertEqual((prog["url"], prog["published"], prog["section"]),
                         ("https://video.hket.com/video/4207783", "2026-10-12", "美股FocUS"))
        self.assertTrue(prog["title"].startswith("納指破頂畏高"))
        self.assertTrue(got["hket:4207783"]["url"].startswith("https://video-playback.hket.com/iet/converts/4207783/hls/"))

    def test_text_to_speech_never_taken(self):
        for name in EXPECTED:
            for c in media.detect(URLS[name], page(name)):
                with self.subTest(name):
                    self.assertNotRegex(c["url"], r"text2Audio|article-audio|articleSpeeches|bpt2a|aliyuncs\.com/audio")
        self.assertIn("text2Audio", page("wenweipo_tts_only"))       # the pages do carry a readout
        self.assertIn("article-audio", page("bastille_tts_only"))
        self.assertIn("articleSpeeches", page("scmp_tts_only"))


def post(url, title="新聞", published="2026-10-14T10:00:00+08:00", text=None, sid="tvb", source="TVB 無綫新聞"):
    corpus.ingest([{"url": url, "title": title, "text": text, "sid": sid, "source": source, "published": published}])
    return corpus.db().execute("SELECT id FROM posts WHERE url=%s", (url,)).fetchone()[0]


def wipe():
    for t in ("post_media", "media", "media_scan", "scored", "scores", "posts", "ai_spend", "ai_runs"):
        corpus.db().execute(f"DELETE FROM {t}")
    corpus.db().execute("DELETE FROM kv WHERE k IN ('openrouter_limit', 'openrouter_limit_told')")


class Queue(unittest.TestCase):
    def setUp(self):
        wipe()
        media._hosts.clear()

    def test_dedupe_across_articles(self):
        a = post(URLS["dotdot_shared"], sid="dotdotnews", source="點新聞")
        b = post(URLS["tkww_shared"], sid="tkww", source="大公文匯網", published="2026-10-14T12:00:00+08:00")
        media.record(a, URLS["dotdot_shared"], page("dotdot_shared"))
        media.record(b, URLS["tkww_shared"], page("tkww_shared"))
        db = corpus.db()
        self.assertEqual(db.execute("SELECT count(*) FROM media").fetchone()[0], 1)
        self.assertEqual(sorted(r[0] for r in db.execute("SELECT post_id FROM post_media")), sorted([a, b]))
        self.assertEqual(db.execute("SELECT newest FROM media").fetchone()[0], "2026-10-14T12:00:00+08:00")
        media.finish("dams:69beb00ee4b08c07fcdcfbd5", "[00:00] 測試逐字稿", 90.0, 0.01, media.MODEL)
        c = post("https://www.wenweipo.com/a/202610/14/AP6acf87a3e4b0e1e2ee73a816.html", sid="wenweipo_hk", source="香港文匯報")
        media.record(c, URLS["tkww_shared"], page("tkww_shared"))  # a third article after it was transcribed
        self.assertEqual(db.execute("SELECT transcript FROM posts WHERE id=%s", (c,)).fetchone()[0], "[00:00] 測試逐字稿")
        self.assertEqual(db.execute("SELECT count(*) FROM media").fetchone()[0], 1)

    def test_programme_is_its_own_post(self):
        a = post(URLS["hket_programme"], sid="hket_sitemap", source="香港經濟日報")
        media.record(a, URLS["hket_programme"], page("hket_programme"))
        db = corpus.db()
        prog = db.execute("SELECT * FROM posts WHERE url='https://video.hket.com/video/4207783'").fetchone()
        self.assertIsNotNone(prog)
        self.assertEqual((prog["website"], prog["board"]), ("香港經濟日報", "美股FocUS"))
        self.assertEqual([r[0] for r in db.execute("SELECT post_id FROM post_media")], [prog["id"]])  # not the article
        self.assertEqual(db.execute("SELECT count(*) FROM media_scan WHERE post_id=%s", (prog["id"],)).fetchone()[0], 1)
        # its place in the queue is the programme's own date, not today's article that shows the player (live
        # 2026-10-14: days-old programmes went first)
        self.assertEqual(db.execute("SELECT newest FROM media").fetchone()[0], "2026-10-12T23:59+08:00")
        self.assertTrue(prog["date_only"])  # the player gives only the day

    def test_newest_first_and_retries(self):
        old = post(URLS["now_video"], published="2026-10-13T08:00:00+08:00", sid="now_local", source="NOW 新聞")
        new = post(URLS["icable_video"], published="2026-10-14T18:00:00+08:00", sid="icable", source="有線新聞")
        media.record(old, URLS["now_video"], page("now_video"))
        media.record(new, URLS["icable_video"], page("icable_video"))
        self.assertTrue(media.due(1)[0]["key"].startswith("https://vod.i-cable.com/"))
        k = media.due(1)[0]["key"]
        media.fail(k, "HTTP 429", busy=True)  # a busy host: later, not counted
        row = corpus.db().execute("SELECT status, tries, next_try FROM media WHERE key=%s", (k,)).fetchone()
        self.assertEqual((row["status"], row["tries"]), ("queued", 0))
        self.assertIsNotNone(row["next_try"])
        self.assertEqual(media.due(5)[0]["key"], "now:665829")  # the busy one waits
        for i in range(media.TRIES):
            self.assertEqual(corpus.db().execute("SELECT status FROM media WHERE key='now:665829'").fetchone()[0], "queued")
            media.fail("now:665829", f"broken {i}")
        self.assertEqual(corpus.db().execute("SELECT status, tries FROM media WHERE key='now:665829'").fetchone(),
                         ("failed", media.TRIES))
        self.assertEqual(media.due(5), [])

    def test_scan_once_per_post_and_seen_page(self):
        a = post(URLS["rthk_audio_video"], sid="rthk_local", source="RTHK 香港電台",
                 published=media._now())
        corpus.db().execute("UPDATE posts SET seen=%s WHERE id=%s", ("2026-01-01T00:00:00+08:00", a))
        self.assertEqual(media.scan_due(), [(a, URLS["rthk_audio_video"])])
        media.seen_page(a, URLS["rthk_audio_video"], page("rthk_audio_video"))  # the full-text reader's page
        self.assertEqual(media.scan_due(), [])  # not read again
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM post_media WHERE post_id=%s", (a,)).fetchone()[0], 2)
        b = post("https://www.mingpao.com/x", sid="mingpao", source="明報", published=media._now())
        corpus.db().execute("UPDATE posts SET seen=%s WHERE id=%s", ("2026-01-01T00:00:00+08:00", b))
        self.assertEqual(media.scan_due(), [])  # a host with no video detector is not read

    def test_scan_busy_site_is_tried_again(self):
        """live 2026-10-14: HKET answered 429 to the scan across its subdomains, and those posts were marked looked-at
        with nothing found"""
        a = post(URLS["hket_none"], sid="hket_sitemap", source="香港經濟日報", published=media._now())
        corpus.db().execute("UPDATE posts SET seen=%s WHERE id=%s", ("2026-01-01T00:00:00+08:00", a))
        import cache_fetch
        with mock.patch.object(cache_fetch, "get", side_effect=RuntimeError("HTTP 429")):
            media.scan_one(a, URLS["hket_none"])
        self.assertEqual(media.scan_due(), [(a, URLS["hket_none"])])  # still to look at
        self.assertGreater(media._hosts.get(media.site(URLS["hket_none"]), 0), media.time.monotonic() + 20)  # backs off
        for _ in range(media.SCAN_TRIES - 1):
            with mock.patch.object(cache_fetch, "get", side_effect=RuntimeError("HTTP 429")), \
                    mock.patch.object(media, "_slot", lambda h: None):
                media.scan_one(a, URLS["hket_none"])
        self.assertEqual(media.scan_due(), [])  # given up after SCAN_TRIES
        b = post(URLS["hket_programme"], sid="hket_sitemap", source="香港經濟日報", published=media._now())
        with mock.patch.object(cache_fetch, "get", side_effect=urllib.error.HTTPError("u", 404, "Not Found", {}, None)), \
                mock.patch.object(media, "_slot", lambda h: None):
            media.scan_one(b, URLS["hket_programme"])
        self.assertEqual(corpus.db().execute("SELECT found FROM media_scan WHERE post_id=%s", (b,)).fetchone()[0], None)
        # one politeness slot per site: HKET's subdomains share it
        self.assertEqual(media.site("https://inews.hket.com/a"), media.site("https://china.hket.com/b"))
        self.assertEqual(media.site("https://www.am730.com.hk/x"), "am730.com.hk")

    def test_fulltext_hands_over_its_page(self):
        import cache_fetch, fulltext_now
        a = post(URLS["stheadline_video"], sid="stheadline", source="星島頭條")
        with mock.patch.object(cache_fetch.sources, "fetch", return_value=page("stheadline_video")):
            fulltext_now.fetch(a, URLS["stheadline_video"], "stheadline")
        self.assertEqual([r[0] for r in corpus.db().execute("SELECT key FROM post_media WHERE post_id=%s", (a,))],
                         ["hkhl:4098cffac7ab71f18cbce6f6c55a0102"])


def tone_wav(path, parts):
    """a WAV of tones and silences: parts = [(seconds, tone?)]"""
    inputs, labels = [], []
    for i, (sec, tone) in enumerate(parts):
        src = f"sine=frequency=440:duration={sec}:sample_rate=16000" if tone else f"anullsrc=r=16000:cl=mono:d={sec}"
        inputs += ["-f", "lavfi", "-i", src]
        labels.append(f"[{i}:a]")
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *inputs, "-filter_complex",
                    "".join(labels) + f"concat=n={len(parts)}:v=0:a=1", "-ac", "1", "-ar", "16000", path], check=True)


class FakeOpenRouter:
    """urlopen for openrouter.ai: answers each piece with its number in colloquial Cantonese, records the request bodies
    and URLs. The speech-to-text endpoint (Qwen3-ASR) answers like the live one did (2026-10-15: mostly Traditional with
    stray Simplified characters, 消委会), here 讲 / 为 for s2hk to settle; the chat endpoint (Gemini) answers in
    Traditional with a variant (爲) for t2hk."""
    def __init__(self, refuse=False):
        self.bodies, self.urls, self.refuse = [], [], refuse

    def __call__(self, req, timeout=None):
        body = json.loads(req.data)
        if self.refuse:
            raise urllib.error.HTTPError(req.full_url, 402, "Payment Required", {}, io.BytesIO(
                b'{"error":{"code":402,"message":"Insufficient credits. Add more using https://openrouter.ai/settings/credits"}}'))
        self.bodies.append(body)
        self.urls.append(req.full_url)
        n = len(self.bodies)
        if req.full_url.endswith("/audio/transcriptions"):
            out = {"text": f"第{n}段：佢哋喺度讲嘢，因为好多人。\n下一句。", "usage": {"seconds": 50.0, "cost": 0.000375}}
        else:
            out = {"choices": [{"message": {"content": f"第{n}段：佢哋喺度講嘢，因爲好多人。\n下一句。"}}],
                   "usage": {"prompt_tokens": 1900, "completion_tokens": 300, "cost": 0.0055}}
        return io.BytesIO(json.dumps(out).encode())


class Transcribe(unittest.TestCase):
    def setUp(self):
        wipe()
        os.environ["OPENROUTER_API"] = "test-key"
        self.tmp = tempfile.mkdtemp()
        media._inflight.clear()

    def test_cuts_at_quiet_points(self):
        self.assertEqual(media.cuts(50, []), [(0.0, 50)])
        got = media.cuts(200, [(54.0, 56.0), (118.0, 119.0)])
        self.assertEqual([round(a) for a, _ in got], [0, 55, 118, 178])  # the quiet stretch nearest to each minute, else 60 s
        self.assertTrue(all(media.CHUNK_MIN <= b - a <= media.CHUNK_MAX for a, b in got[:-1]))
        self.assertEqual([round(a) for a, _ in media.cuts(200, [])], [0, 60, 120, 180])  # no quiet point: every 60 s

    def test_short_pauses_are_cut_points(self):
        """a newsreader's pauses between phrases are short (live, 2026-10-15, a TVB clip: at -35 dB / 0.3 s no pause
        between 15 s and 39 s, so the piece was cut at 25 s mid-word, "以高。 / 高度集中"; at -30 dB / 0.2 s, pauses every
        few seconds)"""
        w = os.path.join(self.tmp, "p.wav")
        tone_wav(w, [(20, True), (0.25, False), (10, True)])
        self.assertTrue(any(19.9 <= a <= 20.3 for a, b in media.silences(w)), media.silences(w))

    def test_model_is_qwen3_asr_on_the_transcription_endpoint(self):
        """Deyao, 2026-10-15: one cheap model, Qwen3-ASR-1.7B (benchmark 14 Oct: CER 6.22 %, 59/71 names, USD 0.00045
        a minute as billed). It is a speech-to-text model: OpenRouter's /audio/transcriptions, MP3 in, no prompt
        (the endpoint takes none), temperature 0, the language detected by the model"""
        self.assertEqual(media.MODEL, "qwen/qwen3-asr-1.7b")
        self.assertTrue(media.is_stt("qwen/qwen3-asr-1.7b"))
        self.assertFalse(media.is_stt("google/gemini-4-argon"))
        url, b = media.request(b"mp3")
        self.assertEqual(url, "https://openrouter.ai/api/v1/audio/transcriptions")
        self.assertEqual(b, {"model": "qwen/qwen3-asr-1.7b", "temperature": 0,
                             "input_audio": {"data": "bXAz", "format": "mp3"}})
        # the model stays one setting: a chat model with audio input still works (the benchmark's prompt and settings)
        url, b = media.request(b"mp3", "google/gemini-4-argon")
        self.assertEqual(url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual((b["model"], b["temperature"], b["max_tokens"], b["reasoning"]),
                         ("google/gemini-4-argon", 0, 16000, {"effort": "low"}))
        self.assertEqual(b["messages"][0]["content"][0]["text"], media.PROMPT)
        self.assertEqual(b["messages"][0]["content"][1]["input_audio"]["format"], "mp3")
        src = open("deploy/apps/transcribe.yaml").read() + open("deploy/apps/cronjobs.yaml").read()
        self.assertNotIn("gemini", src.lower())  # no env overrides the default anywhere

    def test_stt_pieces_stay_inside_the_providers_window(self):
        """live, 2026-10-15: OpenRouter's Qwen3-ASR provider cuts a longer piece every 30 s wherever that falls
        (mid-word: "聯儲局發表。 反映經濟情況嘅學皮書", "削減同。 烏克蘭嘅情報共享"), so a speech-to-text model gets pieces under
        30 s, cut at our own quiet points; a chat model keeps the minute-long pieces"""
        size, lo, hi = media.chunks("qwen/qwen3-asr-1.7b")
        self.assertLess(hi, 30)
        self.assertEqual(media.chunks("google/gemini-4-argon"), (60, 45, 75))
        got = media.cuts(200, [(24.0, 25.0), (60.0, 61.0)], (size, lo, hi))
        self.assertTrue(all(b - a < 30 for a, b in got), got)
        self.assertEqual(round(got[1][0], 1), 24.5)  # the quiet point
        self.assertTrue(all(b - a < 30 for a, b in media.cuts(200, [], (size, lo, hi))))

    def test_stt_answer_in_hk_characters_and_spend(self):
        fake = FakeOpenRouter()
        with mock.patch("urllib.request.urlopen", fake):
            text, cost = media.call_model(b"mp3")
        self.assertEqual(text, "第1段：佢哋喺度講嘢，因為好多人。\n下一句。")  # s2hk: 讲 → 講, 为 → 為
        self.assertAlmostEqual(cost, 0.000375)
        r = corpus.db().execute("SELECT model, usd FROM ai_runs WHERE purpose='transcribe'").fetchone()
        self.assertEqual((r["model"], round(r["usd"], 6)), ("qwen/qwen3-asr-1.7b", 0.000375))
        self.assertEqual(media.to_hk("Hong Kong's CEO said 'AI' 3 times"), "Hong Kong's CEO said 'AI' 3 times")

    def test_transcript_format_spend_search_and_rescore(self):
        a = post(URLS["now_video"], sid="now_local", source="NOW 新聞", text="巴士加價" * 200,
                 published=media._now())
        media.record(a, URLS["now_video"], page("now_video"))
        corpus.kv_set("scoring", {"from_id": 0, "since": "2026-01-01"})
        p = corpus.db().execute(f"SELECT p.id, {streams.HASH} AS hash FROM posts p WHERE id=%s", (a,)).fetchone()
        corpus.db().execute("INSERT INTO scored (post_id, hash, at) VALUES (%s, %s, %s)", (a, p["hash"], media._now()))
        self.assertNotIn(a, [x["id"] for x in streams.due(50)])  # scored, unchanged
        fake = FakeOpenRouter()

        def fetch_audio(m, tmp):
            w = os.path.join(tmp, "a.wav")
            tone_wav(w, [(24, True), (1.0, False), (24, True), (1.0, False), (20, True)])  # 70 s, two quiet gaps
            return w
        with mock.patch.object(media, "fetch_audio", fetch_audio), mock.patch("urllib.request.urlopen", fake):
            self.assertTrue(media.work_one())
        m = corpus.db().execute("SELECT * FROM media WHERE key='now:665829'").fetchone()
        self.assertEqual((m["status"], round(m["duration"]), m["model"]), ("done", 70, "qwen/qwen3-asr-1.7b"))
        self.assertEqual(len(fake.bodies), 3)  # cut at the two quiet points
        lines = m["transcript"].split("\n")
        self.assertEqual([re.match(r"\[(\d\d:\d\d)\] ", x).group(1) for x in lines], ["00:00", "00:24", "00:49"])
        self.assertRegex(lines[0], r"^\[00:00\] 第\d段：佢哋喺度講嘢，因為好多人。下一句。$")  # Cantonese kept; s2hk 讲 → 講; one line
        self.assertAlmostEqual(m["cost"], 0.001125)
        self.assertAlmostEqual(corpus.db().execute("SELECT usd FROM ai_spend WHERE purpose='transcribe'").fetchone()[0], 0.001125)
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM ai_runs WHERE purpose='transcribe'").fetchone()[0], 3)
        # the post: transcript stored, searched, and due for scoring again with the transcript in the scorer's input
        x = corpus.db().execute("SELECT * FROM posts WHERE id=%s", (a,)).fetchone()
        self.assertEqual(x["transcript"], m["transcript"])
        self.assertIn("佢哋喺度讲嘢", x["hay"])  # the search copy (hk2s) holds the transcript
        due = [d for d in streams.due(50) if d["id"] == a]
        self.assertEqual(len(due), 1)
        self.assertRegex(streams.post_line(0, due[0]), r"影片逐字稿：\[00:00\] 第\d段")
        corpus.db().execute("UPDATE posts SET transcript=NULL WHERE id=%s", (a,))
        self.assertEqual(corpus.db().execute(f"SELECT {streams.HASH} FROM posts p WHERE id=%s", (a,)).fetchone()[0],
                         p["hash"])  # without a transcript the hash is the old title + text one: no mass rescoring

    def test_flapping_stop_tells_deyao_once(self):
        """live 2026-10-14: with the balance under USD 0.50, audio calls were refused while the scorer's small text
        calls still went through, so the stop cleared and came back every few seconds; each return is not a new
        message to Deyao (once per NOTIFY_EVERY)"""
        import ai, notify
        sent = []
        with mock.patch.object(notify, "deyao", lambda msg: sent.append(msg) or True):
            for _ in range(5):
                ai.limit_hit("This request requires at least $0.50 in balance for audio")
                ai.limit_clear()
        self.assertEqual(len(sent), 1)

    def test_runaway_answer_is_asked_again_then_left_out(self):
        """2026-10-14, live: one 56 s piece of a 30-minute HKET programme came back as 67,933 characters of English
        about Saturn's moon (the model ran away); the transcript must not carry it"""
        calls = []

        def answer(mp3, model=None):
            calls.append(1)
            return ("Iapetus, the outer moon of Saturn. " * 2000 if len(calls) <= 1 + media.RETRIES_LONG else "好正常嘅一句。"), 0.005
        w = os.path.join(self.tmp, "a.wav")
        tone_wav(w, [(20, True)])  # one piece for any model
        with mock.patch.object(media, "call_model", answer):
            pieces, secs, cost = media.transcribe_wav(w)
        self.assertEqual(pieces, [(0.0, media.LOST)])  # every try ran away: marked, not kept
        self.assertEqual(len(calls), 1 + media.RETRIES_LONG)
        n = []

        def flaky(mp3, model=None):
            n.append(1)
            return ("Saturn " * 5000 if len(n) == 1 else "好正常嘅一句。"), 0.005
        with mock.patch.object(media, "call_model", flaky):
            pieces, _, cost = media.transcribe_wav(w)
        self.assertEqual(pieces, [(0.0, "好正常嘅一句。")])
        self.assertAlmostEqual(cost, 0.01)  # both calls were paid
        self.assertTrue(media.runaway("x" * (media.MAX_CPS * 50 + 201), 50))
        self.assertFalse(media.runaway("正" * 400, 50))

    def test_several_clips_at_once(self):
        self.assertGreaterEqual(media.CLIPS, 3)
        a = post(URLS["now_video"], sid="now_local", source="NOW 新聞")
        b = post(URLS["icable_video"], sid="icable", source="有線新聞", published="2026-10-14T18:00:00+08:00")
        media.record(a, URLS["now_video"], page("now_video"))
        media.record(b, URLS["icable_video"], page("icable_video"))
        first = media.claim()
        second = media.claim()
        self.assertNotEqual(first["key"], second["key"])  # a clip in hand is not handed out again
        self.assertIsNone(media.claim())
        media.release(first["key"])
        self.assertEqual(media.claim()["key"], first["key"])

    def test_credit_refusal_is_a_safety_stop(self):
        import notify
        a = post(URLS["now_video"], sid="now_local", source="NOW 新聞")
        media.record(a, URLS["now_video"], page("now_video"))
        sent = []

        def fetch_audio(m, tmp):
            w = os.path.join(tmp, "a.wav")
            tone_wav(w, [(5, True)])
            return w
        with mock.patch.object(media, "fetch_audio", fetch_audio), \
                mock.patch("urllib.request.urlopen", FakeOpenRouter(refuse=True)), \
                mock.patch.object(notify, "deyao", lambda msg: sent.append(msg) or True), \
                mock.patch.object(media, "LIMIT_WAIT", 0):
            media.work_one()
            media.work_one()
        lim = corpus.kv_get("openrouter_limit")  # ai.py's safety stop: the monitor shows it
        self.assertIn("Insufficient credits", lim["message"])
        self.assertEqual(len(sent), 1)  # once per stop
        self.assertIn("OpenRouter", sent[0])
        self.assertEqual(corpus.db().execute("SELECT status, tries FROM media").fetchone(), ("queued", 0))
        with mock.patch.object(media, "fetch_audio", fetch_audio), mock.patch("urllib.request.urlopen", FakeOpenRouter()):
            media.work_one()
        self.assertFalse(corpus.kv_get("openrouter_limit"))
        self.assertEqual(corpus.db().execute("SELECT status FROM media").fetchone()[0], "done")


class YouTube(unittest.TestCase):
    """Deyao, 2026-10-14: every YouTube post (the YouTube sources and channels, SCMP's embeds) goes through the queue
    and shows its transcript, read from the video's subtitles (Deyao, 2026-10-16; tests/test_youtube_captions.py)"""

    def setUp(self):
        wipe()
        media._hosts.clear()

    def test_youtube_posts_are_queued_without_a_page_read(self):
        import cache_fetch
        ids = []
        for i, u in enumerate(("https://www.youtube.com/watch?v=uVLa0K2GCjE", "https://youtu.be/YP4F2SVf-BY",
                               "https://www.youtube.com/shorts/5qfEfpvriHo")):
            pid = post(u, sid="rthk_youtube", source="RTHK (YouTube)", published=media._now())
            corpus.db().execute("UPDATE posts SET seen=%s WHERE id=%s", ("2026-01-01T00:00:00+08:00", pid))
            ids.append(pid)
        bc = post("https://www.youtube.com/watch?v=zzzzzzzzzzz", sid="bc_icable", source="有線新聞", published=media._now())
        corpus.db().execute("UPDATE posts SET seen=%s WHERE id=%s", ("2026-01-01T00:00:00+08:00", bc))
        self.assertEqual(sorted(p for p, _ in media.scan_due()), sorted(ids))  # a broadcast episode is text already
        with mock.patch.object(cache_fetch, "get", side_effect=AssertionError("no page read for a YouTube post")):
            media.scan_once()
        got = [r[0] for r in corpus.db().execute("SELECT key FROM media ORDER BY key")]
        self.assertEqual(got, ["yt:5qfEfpvriHo", "yt:YP4F2SVf-BY", "yt:uVLa0K2GCjE"])
        # SCMP's embed of the same video is the same clip
        s = post(URLS["scmp_youtube_tts"], sid="scmp_news", source="南華早報")
        media.record(s, URLS["scmp_youtube_tts"], page("scmp_youtube_tts"))
        v = post("https://www.youtube.com/watch?v=_XZmr1ousHc", sid="yt_scmp", source="SCMP (YouTube)", published=media._now())
        media.record(v, "https://www.youtube.com/watch?v=_XZmr1ousHc", "")
        self.assertEqual(sorted(r[0] for r in corpus.db().execute("SELECT post_id FROM post_media WHERE key='yt:_XZmr1ousHc'")),
                         sorted([s, v]))

    def test_youtube_is_the_transcribers(self):
        """every YouTube clip is transcribed (its audio through the ISP route; tests/test_youtube_captions.py)"""
        a = post("https://www.youtube.com/watch?v=abcdefghijk", sid="rthk_youtube", source="RTHK (YouTube)")
        media.record(a, "https://www.youtube.com/watch?v=abcdefghijk", "")
        self.assertEqual([m["key"] for m in media.due(10)], ["yt:abcdefghijk"])

    def test_item_names_the_captions(self):
        a = post("https://www.youtube.com/watch?v=abcdefghijk", sid="rthk_youtube", source="RTHK (YouTube)")
        media.record(a, "https://www.youtube.com/watch?v=abcdefghijk", "")
        media.finish("yt:abcdefghijk", "[00:01] 字幕", 125, 0.0, "youtube-captions:zh-HK")
        d = view(a)
        self.assertEqual(d["media"][0]["model_name"], "YouTube 上載者字幕（zh-HK）")


def view(pid):
    """item.view as JSON (what the agent's tools and the MCP server get)"""
    return json.loads(json.dumps(item.view(pid), default=str))


def text(pid):
    """item.text as JSON (the full text, comments, transcripts and picture text)"""
    return json.loads(json.dumps(item.text(pid), default=str))


class ItemPage(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_clips_public_transcript_approved_only(self):
        a = post(URLS["rthk_audio_video"], sid="rthk_local", source="RTHK 香港電台")
        media.record(a, URLS["rthk_audio_video"], page("rthk_audio_video"))
        media.finish("https://newsstatic.rthk.hk/audios/mfile_1873950_1_20261014185443.mp3", "[00:00] 秘密逐字稿", 65.4,
                     0.006, "google/gemini-4-argon")
        d = view(a)
        self.assertEqual([(m["status"], m["duration"], m["chars"]) for m in d["media"]], [("done", 65, 13), ("queued", None, 0)])
        self.assertNotIn("秘密逐字稿", json.dumps(d, ensure_ascii=False))
        t = text(a)
        self.assertEqual(t["media"], {"https://newsstatic.rthk.hk/audios/mfile_1873950_1_20261014185443.mp3": "[00:00] 秘密逐字稿"})

    def test_ai_transcribed_label(self):
        """Deyao, 2026-10-15: "put transcript in the details page and label it as AI transcribed". Each model-made
        transcript carries 「AI 轉錄」 (the 「AI 標題」 badge's style, the model and time on hover/tap); the uploader's own
        captions keep 「YouTube 上載者字幕」 and no AI label"""
        corpus.kv_set("openrouter_models", {"at": 9e12, "models": {"qwen/qwen3-asr-1.7b": {
            "name": "Qwen: Qwen3 ASR 1.7B", "prompt": 0.0000075, "completion": 0}}})
        a = post("https://www.youtube.com/watch?v=abcdefghijk", sid="rthk_youtube", source="RTHK (YouTube)")
        media.record(a, "https://www.youtube.com/watch?v=abcdefghijk", "")
        b = post(URLS["rthk_audio_video"], sid="rthk_local", source="RTHK 香港電台")
        media.record(b, URLS["rthk_audio_video"], page("rthk_audio_video"))
        media.finish("yt:abcdefghijk", "[00:01] 字幕", 125, 0.0, "youtube-captions:zh-HK")
        media.finish("https://newsstatic.rthk.hk/audios/mfile_1873950_1_20261014185443.mp3", "[00:00] 逐字稿", 65.4,
                     0.0005, "qwen/qwen3-asr-1.7b")
        d = view(a)
        self.assertEqual([(m["by_ai"], m["model_name"]) for m in d["media"]], [(False, "YouTube 上載者字幕（zh-HK）")])
        d = view(b)
        self.assertEqual([(m["status"], m["by_ai"], m["model_name"]) for m in d["media"]],
                         [("done", True, "Qwen3 ASR 1.7B"), ("queued", None, None)])
        self.assertTrue(d["media"][0]["done_at"])
        f = "web-app/src/routes/-ItemPage.tsx"
        src = open(f).read()
        self.assertIn("影片逐字稿", src)
        self.assertIn("AI 轉錄", src, f)
        self.assertIn("by_ai", src, f)
        badge = open("web-app/src/lib/AiBadge.tsx").read()
        self.assertIn("<AiTranscribedBadge m={m} />", src)
        self.assertRegex(badge, r'(?s)className="ai-t".{0,200}?title=.{0,300}?>AI 轉錄</span>', f)
        self.assertIn("m.done_at", badge[badge.index("AiTranscribedBadge"):], f)

    def test_broadcast_rows_name_the_model(self):
        """a broadcast episode's text IS the transcript: its post records the model (text_via asr:<model>) and the item
        page names it as AI transcription"""
        import broadcast_fetch
        corpus.kv_set("openrouter_models", {"at": 9e12, "models": {"qwen/qwen3-asr-1.7b": {
            "name": "Qwen: Qwen3 ASR 1.7B", "prompt": 0.0000075, "completion": 0}}})
        ep = {"url": "https://www.rthk.hk/radio/radio1/programme/hktoday/episode/1", "title": "晨早新聞天地 2026-10-15",
              "published": datetime(2026, 10, 15, 6, 0, tzinfo=corpus.HKT), "media": "https://x/a.mp3"}
        prog = {"sid": "bc_rthk", "who": "香港電台", "name": "晨早新聞天地", "kind": "rthk_dated"}
        with mock.patch.object(broadcast_fetch, "transcribe", lambda e, k: ([(0, "[00:00] 早晨，今日嘅新聞。")], 60.0, "asr:" + media.MODEL)):
            broadcast_fetch.file_episode(prog, ep)
        x = corpus.db().execute("SELECT id, text_via FROM posts WHERE url=%s", (ep["url"],)).fetchone()
        self.assertEqual(x["text_via"], "asr:qwen/qwen3-asr-1.7b")
        d = view(x['id'])
        self.assertEqual(d["text_via_label"], "AI 轉錄（Qwen3 ASR 1.7B）")

    def test_pages_have_the_section(self):
        for f in ("web-app/src/routes/-ItemPage.tsx",):
            src = open(f).read()
            self.assertIn("影片逐字稿", src, f)
            self.assertIn("逐字稿排隊中", src, f)


class Deploy(unittest.TestCase):
    def test_broadcast_same_model(self):
        cj = open("deploy/apps/cronjobs.yaml").read()
        seg = cj[cj.index("  name: broadcast\n"):]
        self.assertIn("suspend: true", seg[:600])  # off with transcription (Deyao, 2026-10-20: cost)
        src = open("broadcast_fetch.py").read()
        self.assertIn("media.transcribe_wav", src)
        self.assertNotIn("sherpa", src + open("Dockerfile").read())

    def test_transcriber_deployment(self):
        y = open("deploy/apps/transcribe.yaml").read()
        self.assertIn("replicas: 1", y)
        self.assertIn("maxUnavailable: 0", y)
        self.assertIn("media.py, run", y)
        self.assertIn("transcribe.yaml", open("deploy/apps/kustomization.yaml").read())


if __name__ == "__main__":
    unittest.main()
