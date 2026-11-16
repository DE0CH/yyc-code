#!/usr/bin/env python3
"""Pictures on posts scored for meaning (images.py), on a throwaway database (TEST_DATABASE_URL; its tables are dropped
and made again). No network and no model calls: downloads and OpenRouter are fakes.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_images.py

1. Finding pictures from saved real pages (tests/fixtures/media/*.html.gz): the lead image first, the story's own
   pictures, never the site's icons; a YouTube post's thumbnail from its id; the hand-labelled set's URLs exist.
2. One picture is scored once (sha256 of its bytes), whatever URLs and posts carry it; a picture too small to carry
   anything is not sent to the model.
3. The answer: {score 0–100, reason, text}, fenced JSON accepted, anything else asked again.
4. The threshold (settings image_threshold, default 50, infra/control.py set): the meaningful pictures' text joins the
   post's search text and the scorer's input (the post is due again); a new threshold redoes the posts.
5. The scorer gets each post's meaningful pictures attached (ai.jsonc images=).
6. The queue: 串流's items first, then the newest; failures counted, TRIES give a picture up; OpenRouter's credit stop
   is not a try.
7. One post's data (item.py): item.view lists the pictures (meaningful first in the page, reason, no text); item.text
   has the text; the edge app's ItemPage has the 圖片 section with 其他圖片（N）.
8. The monitor's pictures card; the Deployment.
9. Memory (2026-10-15: the Deployment was OOMKilled at 768Mi): a 60-megapixel JPEG is decoded at a reduced scale, and
   the pictures decoded at once stay inside a byte budget, so six workers never hold several full-size pictures.
10. Bad picture URLs (2026-10-15): HKET's content API JSON (escaped quotes in its HTML) gives the real pictures, a
   Qiniu thumbnail (?imageMogr2/…) resolves to its picture, an empty og/twitter image is not the page itself; a picture
   that can never work (HTTP 4xx, not an image) is given up at once for every post carrying it, a picture whose
   download kills the worker counts as a try, and `images.py repair` drops the bad rows saved before the fix.
"""
import gzip, io, json, os, subprocess, sys, textwrap, unittest, urllib.error, urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import flags_on  # noqa: E402,F401  picture analysis and transcription on (off since 2026-10-20: tests/test_pictures_transcription_off.py)
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, images, insights, item, streams  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)

MFIX = os.path.join(HERE, "tests", "fixtures", "media")
IFIX = os.path.join(HERE, "tests", "fixtures", "images")
URLS = json.load(open(os.path.join(MFIX, "pages.json")))
TABLE = open(os.path.join(IFIX, "table.jpg"), "rb").read()
LOGO = open(os.path.join(IFIX, "logo.jpg"), "rb").read()


def page(name):
    with gzip.open(os.path.join(MFIX, name + ".html.gz"), "rt") as f:
        return f.read()


def post(url, title="新聞", published="2026-10-14T10:00:00+08:00", text=None, sid="tvb", source="TVB 無綫新聞"):
    corpus.ingest([{"url": url, "title": title, "text": text, "sid": sid, "source": source, "published": published}])
    return corpus.db().execute("SELECT id FROM posts WHERE url=%s", (url,)).fetchone()[0]


def wipe():
    for t in ("post_images", "images", "image_scan", "scored", "scores", "stream_items", "posts", "ai_spend", "ai_runs",
              "settings"):
        corpus.db().execute(f"DELETE FROM {t}")
    corpus.db().execute("DELETE FROM kv WHERE k IN ('openrouter_limit', 'openrouter_limit_told')")
    images._inflight.clear()


class Extract(unittest.TestCase):
    def test_saved_pages(self):
        got = images.extract(URLS["tvb_video"], page("tvb_video"))
        self.assertTrue(got[0].startswith("https://pro-vod-cdn.tvb.com/9f22d17b181346ab8be36e5ef400380c/snapshots/"), got)  # og:image
        self.assertTrue(any(u.startswith("https://pro-img-cdn.tvb.com/uploads/image/") for u in got))
        st = images.extract(URLS["standard_video"], page("standard_video"))
        self.assertEqual(st[0], "https://image.hkstandard.com.hk/f/1200p0/0x0/100/none/9a6785c5dd0189762414772b32e84551/2026-09/smoke_0.jpg")
        self.assertGreaterEqual(len(st), 4)  # the story's own stills, not only the lead
        for name in URLS:
            for u in images.extract(URLS[name], page(name)):
                with self.subTest(name):
                    self.assertTrue(u.startswith("http"))
                    self.assertNotRegex(u.lower(), r"logo|icon|favicon|\.svg|\.gif")
                    self.assertLessEqual(len(images.extract(URLS[name], page(name))), images.MAX_IMAGES)

    def test_youtube_thumbnail(self):
        self.assertEqual(images.extract("https://www.youtube.com/watch?v=uVLa0K2GCjE", ""),
                         ["https://i.ytimg.com/vi/uVLa0K2GCjE/hqdefault.jpg"])

    def test_labelled_set(self):
        L = json.load(open(os.path.join(IFIX, "labels.json")))
        known = [r for r in L if r["label"] is not None]
        self.assertGreaterEqual(len(known), 40)
        self.assertGreaterEqual(sum(r["label"] for r in known), 15)  # enough meaningful ones to measure recall


class Answer(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(images.parse('```json\n{"score": 92, "reason": "表格", "text": "手動"}\n```'),
                         {"score": 92, "reason": "表格", "text": "手動"})
        self.assertEqual(images.parse('{"score": 140, "reason": "", "text": null}')["score"], 100)
        with self.assertRaises(ValueError):
            images.parse("這是一張圖")


def fake_chat(score_by_size):
    calls = []

    def chat(messages, purpose, model=None, json_mode=False, **kw):
        calls.append((purpose, model, messages))
        img = messages[0]["content"][1]["image_url"]["url"]
        return {"content": json.dumps(score_by_size(len(img)), ensure_ascii=False)}
    return chat, calls


class Queue(unittest.TestCase):
    def setUp(self):
        wipe()

    def score(self, downloads, answer):
        chat, calls = fake_chat(answer)
        with mock.patch.object(images, "download", lambda u: downloads[u]), mock.patch.object(ai, "chat", chat):
            while images.work_one():
                pass
        return calls

    def test_one_picture_scored_once_and_feeds_search_and_scoring(self):
        a = post("https://news.tvb.com/tc/1", text="消防條例")
        b = post("https://news.now.com/x?newsId=2", sid="now_local", source="NOW 新聞")
        corpus.db().execute("INSERT INTO post_images (post_id, url, pos) VALUES (%s,'https://a/t.jpg',0), (%s,'https://b/t2.jpg',0),"
                            " (%s,'https://a/logo.jpg',1)", (a, b, a))
        corpus.kv_set("scoring", {"from_id": 0, "since": "2026-01-01"})
        h0 = corpus.db().execute(f"SELECT {streams.HASH} FROM posts p WHERE id=%s", (a,)).fetchone()[0]
        calls = self.score({"https://a/t.jpg": TABLE, "https://b/t2.jpg": TABLE, "https://a/logo.jpg": LOGO},
                           lambda n: {"score": 95, "reason": "消防設備對照表", "text": "物聯網火警偵測系統"} if n > 70000
                           else {"score": 3, "reason": "標誌", "text": "港鐵"})
        self.assertEqual(len(calls), 2)  # the table once (two URLs, two posts), the logo once
        self.assertEqual({c[0] for c in calls}, {"images"})
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM images").fetchone()[0], 2)
        for pid in (a, b):
            x = corpus.db().execute("SELECT image_text, hay FROM posts WHERE id=%s", (pid,)).fetchone()
            self.assertEqual(x["image_text"], "物聯網火警偵測系統")  # the logo is under the threshold
            self.assertIn("物联网火警侦测系统", x["hay"])  # searched (the search copy is Simplified)
        p = dict(corpus.db().execute(f"SELECT p.*, {streams.HASH} AS hash FROM posts p WHERE id=%s", (a,)).fetchone())
        self.assertNotEqual(p["hash"], h0)  # rescored
        self.assertIn("圖片文字：物聯網火警偵測系統", streams.post_line(0, p))
        # the threshold moves: the posts are redone
        insights.save_settings({"image_threshold": 99})
        images.refresh_all()
        self.assertIsNone(corpus.db().execute("SELECT image_text FROM posts WHERE id=%s", (a,)).fetchone()[0])
        insights.save_settings({"image_threshold": 50})
        images.refresh_all()
        # the scorer reads the meaningful picture itself
        with mock.patch.object(images, "download", lambda u: TABLE):
            pics = images.for_scoring([p])
        self.assertEqual([c for c, _ in pics], ["帖子 0 的圖片："])
        self.assertTrue(pics[0][1][:2] == b"\xff\xd8")
        sent = []
        with mock.patch.object(ai, "chat", lambda msgs, *a, **k: sent.append(msgs) or {"content": '{"posts": []}'}), \
                mock.patch.object(images, "download", lambda u: TABLE):
            streams.score_posts(insights.streams(), [p])
        kinds = [c["type"] for c in sent[0][0]["content"]]
        self.assertEqual(kinds, ["text", "text", "text", "image_url"])  # the cached rubric + topics, the rest, the caption, the picture

    def test_small_pictures_not_sent(self):
        from PIL import Image
        b = io.BytesIO()
        Image.new("RGB", (60, 60), "white").save(b, "PNG")
        a = post("https://news.tvb.com/tc/3")
        corpus.db().execute("INSERT INTO post_images (post_id, url, pos) VALUES (%s,'https://a/s.png',0)", (a,))
        calls = self.score({"https://a/s.png": b.getvalue()}, lambda n: {"score": 99, "reason": "", "text": ""})
        self.assertEqual(calls, [])
        self.assertEqual(corpus.db().execute("SELECT score FROM images").fetchone()[0], 0)

    def test_order_failures_and_the_credit_stop(self):
        old = post("https://news.tvb.com/tc/old", published="2026-10-10T10:00:00+08:00")
        new = post("https://news.tvb.com/tc/new", published="2026-10-14T10:00:00+08:00")
        kept = post("https://news.tvb.com/tc/kept", published="2026-10-01T10:00:00+08:00")
        corpus.db().execute("INSERT INTO stream_items (day, stream, url, item) VALUES ('2026-10-01','CEHK',%s,'{}')",
                            ("https://news.tvb.com/tc/kept",))
        for pid, u in ((old, "https://x/old.jpg"), (new, "https://x/new.jpg"), (kept, "https://x/kept.jpg")):
            corpus.db().execute("INSERT INTO post_images (post_id, url, pos) VALUES (%s,%s,0)", (pid, u))
        self.assertEqual([r["url"] for r in images.due(5)], ["https://x/kept.jpg", "https://x/new.jpg", "https://x/old.jpg"])
        with mock.patch.object(images, "download", side_effect=RuntimeError("HTTP 404")):
            for _ in range(images.TRIES * 3):
                images.work_one()
        self.assertEqual(images.due(5), [])
        corpus.db().execute("UPDATE post_images SET tries=0, error=NULL")

        def refuse(*a, **k):
            raise ai.LimitReached("Insufficient credits")
        with mock.patch.object(images, "download", lambda u: TABLE), mock.patch.object(ai, "chat", refuse), \
                mock.patch.object(images.STOP, "wait", lambda s: None):
            images.work_one()
        self.assertEqual(corpus.db().execute("SELECT max(tries) FROM post_images").fetchone()[0], 0)  # not a try

    def test_record_and_scan(self):
        a = post(URLS["standard_video"], sid="thestandard", source="英文虎報 The Standard")
        urls = images.record(a, URLS["standard_video"], page("standard_video"))
        self.assertEqual([r[0] for r in corpus.db().execute("SELECT url FROM post_images WHERE post_id=%s ORDER BY pos", (a,))], urls)
        y = post("https://www.youtube.com/watch?v=uVLa0K2GCjE", sid="rthk_youtube", source="RTHK (YouTube)",
                 published=images._now())
        corpus.db().execute("UPDATE posts SET seen='2026-01-01T00:00:00+08:00', source_type='Video' WHERE id=%s", (y,))
        self.assertIn((y, "https://www.youtube.com/watch?v=uVLa0K2GCjE"), images.scan_due())
        self.assertNotIn(a, [p for p, _ in images.scan_due()])  # looked at once
        images.scan_one(y, "https://www.youtube.com/watch?v=uVLa0K2GCjE")
        self.assertEqual(corpus.db().execute("SELECT url FROM post_images WHERE post_id=%s", (y,)).fetchone()[0],
                         "https://i.ytimg.com/vi/uVLa0K2GCjE/hqdefault.jpg")


def fixture(name):
    with gzip.open(os.path.join(IFIX, name), "rt") as f:
        return f.read()


HKET = "https://ps.hket.com/article/4209096/私宅呎租高位整固"
ORANGE = "https://www.orangenews.hk/finance/1467331/英聯股份-2026年前三季度淨利潤同比增長22-96--25-22.shtml"


class BadUrls(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_hket_content_api(self):
        got = images.extract(HKET, fixture("hket_4209096_api.json.gz"))
        self.assertEqual(got[0], "https://static06.hket.com/res/v3/image/content/4205000/4209096/honn20260804001_2500x2500_1_1024.jpg")
        for n in (6, 7, 8, 9, 10):
            self.assertIn(f"https://static06.hket.com/res/v3/image/inline-content/0/252/{n}_1024.png", got)
        self.assertIn("https://static06.hket.com/res/v3/image/content/4205000/4209096/P141026MTR_1024.png", got)
        for u in got:
            self.assertTrue(u.startswith("https://static06.hket.com/"), u)
        self.assertEqual(len(got), len(set(got)))

    def test_escaped_quotes(self):
        page = r'<html><body><div><p>一段很長的新聞內容，足夠成為正文。</p><p>第二段很長的新聞內容，也是正文。</p>' \
               r'<img src=\"https://static06.hket.com/res/v3/image/inline-content/0/252/7_1024.png\"/></div></body></html>'
        self.assertEqual(images.extract("https://ps.hket.com/article/1/", page),
                         ["https://static06.hket.com/res/v3/image/inline-content/0/252/7_1024.png"])

    def test_orangenews_without_a_picture(self):
        got = images.extract(ORANGE, fixture("orangenews_1467331_noimage.html.gz"))
        for u in got:
            self.assertNotEqual(urllib.parse.urlsplit(u).path, urllib.parse.urlsplit(ORANGE).path, u)  # not the page itself
            self.assertNotIn(".shtml", u)

    def test_qiniu_thumbnail_is_its_picture(self):
        page = ('<html><head><meta property="og:image" content="https://cdn.orangenews.hk/images/2026/10/15/a.png">'
                '<meta name="twitter:image" content="https://cdn.orangenews.hk/images/2026/10/15/a.png?imageMogr2/thumbnail/x320">'
                '</head><body></body></html>')
        self.assertEqual(images.extract(ORANGE, page), ["https://cdn.orangenews.hk/images/2026/10/15/a.png"])

    def test_scan_reads_the_hket_api(self):
        pid = post(HKET, sid="hket_ps", source="經濟日報 地產站")
        asked = []

        def get(url, **kw):
            asked.append(url)
            return fixture("hket_4209096_api.json.gz") if "data02.hket.com" in url else "<html><body><div id=root></div></body></html>"
        import cache_fetch
        with mock.patch.object(cache_fetch, "get", get), mock.patch("media.seen_page", lambda *a, **k: None):
            images.scan_one(pid, HKET)
        self.assertTrue(any("data02.hket.com/content?id=4209096" in u for u in asked), asked)
        urls = [r[0] for r in corpus.db().execute("SELECT url FROM post_images WHERE post_id=%s ORDER BY pos", (pid,))]
        self.assertGreaterEqual(len(urls), 6)
        self.assertTrue(all(u.startswith("https://static06.hket.com/") for u in urls), urls)

    def test_permanent_failure_given_up_for_every_post(self):
        a, b = post("https://news.tvb.com/tc/a1"), post("https://news.tvb.com/tc/b1")
        bad, odd, slow = "https://x/400.jpg", "https://x/page.shtml", "https://x/slow.jpg"
        for pid in (a, b):
            for u in (bad, odd, slow):
                corpus.db().execute("INSERT INTO post_images (post_id, url, pos) VALUES (%s,%s,0)", (pid, u))

        def download(u):
            if u == bad:
                raise urllib.error.HTTPError(u, 400, "Bad Request", {}, None)
            if u == odd:
                return b"<!doctype html><html><body>not a picture</body></html>"
            raise TimeoutError("timed out")
        with mock.patch.object(images, "download", download):
            for _ in range(3):
                images.work_one()
        tries = {r[0]: r[1] for r in corpus.db().execute("SELECT url, min(tries) FROM post_images GROUP BY url")}
        self.assertGreaterEqual(tries[bad], images.TRIES)  # one request, then never again, on either post
        self.assertGreaterEqual(tries[odd], images.TRIES)
        self.assertEqual(tries[slow], 1)  # a timeout is worth trying again
        c = post("https://news.tvb.com/tc/c1")
        images.link(c, [bad, odd])  # a new post carrying a picture already given up: not fetched again
        self.assertEqual([r["url"] for r in images.due(10)], [slow, slow])

    def test_a_crash_mid_picture_counts_as_a_try(self):
        a = post("https://news.tvb.com/tc/crash")
        corpus.db().execute("INSERT INTO post_images (post_id, url, pos) VALUES (%s,'https://x/huge.jpg',0)", (a,))
        seen = []

        def score_one(row):  # the OOM kill: nothing after this line runs in the real worker
            seen.append(corpus.db().execute("SELECT tries FROM post_images WHERE url=%s", (row["url"],)).fetchone()[0])
            return "h"
        with mock.patch.object(images, "score_one", score_one):
            images.work_one()
        self.assertEqual(seen, [1])

    def test_repair_drops_the_bad_rows(self):
        h = post("https://ps.hket.com/article/4209021/x", sid="hket_ps", source="經濟日報 地產站")
        o = post(ORANGE, sid="orangenews", source="橙新聞")
        good = post("https://news.tvb.com/tc/good")
        corpus.db().execute("INSERT INTO post_images (post_id, url, pos, tries) VALUES "
                            "(%s, %s, 0, 3), (%s, %s, 0, 3), (%s, 'https://x/fine.jpg', 0, 0)",
                            (h, 'https://ps.hket.com/article/4209021/\\"https:/static06.hket.com/res/v3/image/inline-content/0/252/7_1024.png\\"',
                             o, ORANGE + "?imageMogr2/thumbnail/x320", good))
        for pid in (h, o, good):
            corpus.db().execute("INSERT INTO image_scan (post_id, at, found) VALUES (%s, %s, 1)", (pid, images._now()))
        self.assertEqual(images.repair(), 2)
        self.assertEqual([r[0] for r in corpus.db().execute("SELECT url FROM post_images")], ["https://x/fine.jpg"])
        self.assertEqual([r[0] for r in corpus.db().execute("SELECT post_id FROM image_scan")], [good])  # looked at again


MEASURE = textwrap.dedent("""
    import io, os, resource, sys, threading
    sys.path.insert(0, sys.argv[1])
    import images
    from PIL import Image
    kind, n = sys.argv[2], int(sys.argv[3])
    b = io.BytesIO()
    if kind == "jpeg":  # a 9504x6336 newspaper photo (文匯報, 2026-10-14) is 60 megapixels
        im = Image.new("RGB", (9504, 6336), "white"); im.paste((200, 30, 30), (0, 0, 4000, 3000)); im.save(b, "JPEG", quality=85)
    else:
        im = Image.new("RGBA", (4000, 4000), (255, 255, 255, 0)); im.paste((20, 20, 200, 255), (0, 0, 2000, 2000)); im.save(b, "PNG", compress_level=1)
    data = b.getvalue(); del im, b
    base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    out = []
    ts = [threading.Thread(target=lambda: out.append(images.prepare(data))) for _ in range(n)]
    [t.start() for t in ts]; [t.join() for t in ts]
    h, jpeg, w, ht = out[0]
    side = max(Image.open(io.BytesIO(jpeg)).size)
    print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024, base // 1024, w, ht, side)
""")


class Memory(unittest.TestCase):
    def peak(self, kind, n):
        r = subprocess.run([sys.executable, "-c", MEASURE, HERE, kind, str(n)], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, DATABASE_URL="postgresql:///unused"))
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return [int(x) for x in r.stdout.split()]

    def test_huge_jpeg_decoded_at_a_reduced_scale(self):
        peak, base, w, ht, side = self.peak("jpeg", images.WORKERS)
        self.assertEqual((w, ht), (9504, 6336))  # the picture's own size is recorded
        self.assertEqual(side, images.MAX_SIDE)  # and the model still gets the full 1024 px
        self.assertLess(peak - base, 200, f"six 60-megapixel JPEGs at once took {peak - base} MB")  # was ~2,200 MB

    def test_decoding_stays_inside_the_budget(self):
        peak, base, w, ht, side = self.peak("png", images.WORKERS)
        self.assertEqual((w, ht, side), (4000, 4000, images.MAX_SIDE))
        self.assertLess(peak - base, images.DECODE_BUDGET // 2**20 + 150, f"six 16-megapixel PNGs at once took {peak - base} MB")


def view(pid):
    """item.view as JSON (what the agent's tools and the MCP server get)"""
    return json.loads(json.dumps(item.view(pid), default=str))


def text(pid):
    """item.text as JSON (the full text, comments, transcripts and picture text)"""
    return json.loads(json.dumps(item.text(pid), default=str))


class ItemPage(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_pictures_public_text_approved(self):
        a = post("https://news.tvb.com/tc/9")
        corpus.db().execute("INSERT INTO images (hash, url, width, height, score, reason, text, model, at) VALUES "
                            "('h1','https://a/1.jpg',800,600,92,'對照表','秘密圖文','m','2026-10-14'),"
                            "('h2','https://a/2.jpg',800,600,5,'人像','','m','2026-10-14')")
        corpus.db().execute("INSERT INTO post_images (post_id, url, pos, hash) VALUES (%s,'https://a/2.jpg',0,'h2'),"
                            " (%s,'https://a/1.jpg',1,'h1'), (%s,'https://a/3.jpg',2,NULL)", (a, a, a))
        d = view(a)
        items = d["pictures"]["items"]
        self.assertEqual([(i["status"], i["meaningful"]) for i in items], [("scored", False), ("scored", True), ("queued", False)])
        self.assertEqual(items[1]["reason"], "對照表")
        self.assertNotIn("秘密圖文", json.dumps(d, ensure_ascii=False))
        t = text(a)
        self.assertEqual(t["pictures"], {"h1": "秘密圖文"})

    def test_pages(self):
        for f in ("web-app/src/routes/-ItemPage.tsx",):  # the TanStack Start route item.$id.tsx renders it
            src = open(f).read()
            for want in ("其他圖片（", "圖中文字", 'id="pictures"'):
                self.assertIn(want, src, f)


class Plumbing(unittest.TestCase):
    def test_monitor_and_deploy(self):
        import monitor
        self.assertIn("pictures", monitor.pipeline())
        self.assertIn("ai_spend images", open("web-app/src/routes/-MonitorPage.tsx").read())
        y = open("deploy/apps/images.yaml").read()
        self.assertIn("images.py, run", y)
        self.assertIn("maxUnavailable: 0", y)
        self.assertIn('MALLOC_MMAP_THRESHOLD_, value: "1048576"', y)  # freed picture buffers go back (Memory)
        self.assertIn("images.yaml", open("deploy/apps/kustomization.yaml").read())
        self.assertIn("image_threshold", open("infra/control.py").read())


if __name__ == "__main__":
    unittest.main()
