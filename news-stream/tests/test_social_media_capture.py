#!/usr/bin/env python3
"""The photos and videos of Facebook, Instagram and Threads posts (Deyao, 2026-10-15: "our scrape comes up with almost
zero data (e.g. just a few hashtags or a url)"). In the 7 days to 15 Oct, 117 Facebook page posts, 59 Instagram/Threads
posts and 12 Facebook search posts had almost no words: photo and video posts whose caption is a few words and
hashtags (am730's 「好長情！ #魏浚笙 #am730」). The collectors passed no `images` / `clips`, so images.py never read their
pictures and media.py never transcribed their videos. On a throwaway database (TEST_DATABASE_URL; its tables are
dropped and made again), no network: real answers saved 15 Oct in fixtures/social_media/ (Facebook's timeline answer
for am730 and 李家超's page, Instagram profiles of am730 and CNA, TVB's Threads profile, TikHub's Instagram hashtag
answer, AnyAPI's Facebook search answer).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_social_media_capture.py

Covers:
  1. fb_fetch.posts: a photo post's photo, an album's photos (all of them, in order), a link share's preview image, a
     video's thumbnail and the video itself as a clip (fb:<video id>, its reel/permalink URL, kind proxy: media.py
     reads it with yt-dlp over the residential proxy); never a profile picture.
  2. fb_fetch.poll carries them into facebook.json, and ingest links them to images.py and media.py.
  3. ig_fetch.posts (Instagram profile): every post's picture (display_uri); a reel is a clip (ig:<code>,
     /reel/<code>/, proxy). ig_fetch.threads_posts: photos (largest candidate), carousels, videos (the mp4, direct).
     ig_fetch.main carries them into instagram.json.
  4. tikhub_fetch.instagram (hashtag search): the post's picture or every picture of a carousel; a video is a clip.
  5. fb_search / tikhub_fetch.facebook: the post's image and album preview pictures; a reel/video link is a clip; the
     title is never the author's name (posts were titled 楊濟榮, 方建平, Garett Lyu): "(post without text)" when the
     post has no text, as fb_fetch does.
"""
import gzip, io, json, os, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.setdefault("RESI_PROXY", "http://u:p@127.0.0.1:9")
os.environ.setdefault("TIKHUB_API", "test")
os.environ.setdefault("ANYAPI_API_KEY", "test")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, fb_fetch, fb_search, ig_fetch, sources, tikhub_fetch as th, watchlist  # noqa: E402

HKT = timezone(timedelta(hours=8))
FIX = os.path.join(HERE, "tests", "fixtures", "social_media")
TMP = tempfile.mkdtemp()


def fx(name):
    with gzip.open(os.path.join(FIX, name)) as f:
        return f.read()


def by_post(posts, pid):
    return next(p for p in posts if p["post_id"] == pid)


def path(u):
    return u.split("?")[0]


def has(urls, needle):
    return any(needle in u for u in urls)


class Facebook(unittest.TestCase):
    def test_1_video_photo_album_share(self):
        vid = fb_fetch.posts(fx("fb_johnleekachiu_video.ndjson.gz"))
        reel = by_post(vid, "1096104226499775")
        self.assertEqual(reel["clips"], [{"key": "fb:1741902233707231", "url": "https://www.facebook.com/reel/1741902233707231/",
                                          "kind": "ytdlp"}])
        self.assertEqual(len(reel["images"]), 1)  # the video's thumbnail
        self.assertIn("846280883_1998623584163679", reel["images"][0])
        photo = by_post(vid, "1097074233069441")
        self.assertEqual(photo["clips"], [])
        self.assertEqual(len(photo["images"]), 1)
        self.assertIn("847832799_1374938794427043", photo["images"][0])

        album = by_post(fb_fetch.posts(fx("fb_johnleekachiu_album.ndjson.gz")), "1093928826717315")
        self.assertEqual([path(u).rsplit("/", 1)[1].split("_")[0] for u in album["images"]],
                         ["842611581", "844008261", "842611588", "843474523", "845747087"])

        am = fb_fetch.posts(fx("fb_am730_photo_share.ndjson.gz"))
        self.assertTrue(has(by_post(am, "1612521400915456")["images"], "850792447_28745591525108995"))
        share = by_post(am, "1612388484262081")["images"]
        self.assertEqual(len(share), 1, share)  # the preview image once (large and flexible copies are one picture)
        self.assertIn("841804835_29252947384392967", share[0])
        for p in vid + am:
            self.assertFalse(has(p["images"], "t39.30808-1/"), "a profile picture was taken as a post's photo")
            self.assertEqual(len(p["images"]), len({path(u) for u in p["images"]}))

    def test_1b_link_share_carries_its_link(self):
        """a link share's own article URL (attachment target external_url) joins the text when the caption lacks it, so
        links.py can read a short caption's article and the post groups with the article (link_key); thin.py, 15 Oct"""
        am = by_post(fb_fetch.posts(fx("fb_am730_photo_share.ndjson.gz")), "1612388484262081")
        self.assertIn("https://www.am730.com.hk/", am["text"])
        self.assertIn("1057584", am["text"])
        self.assertTrue(am["text"].startswith("第55屆香港藝術節"))
        photo = by_post(fb_fetch.posts(fx("fb_am730_photo_share.ndjson.gz")), "1612490084251921")
        self.assertNotIn("http", photo["text"])  # a photo post has no link to add

    def test_2_poll_and_ingest(self):
        body = fx("fb_johnleekachiu_video.ndjson.gz")

        class Resp:
            def __init__(self, b):
                self.content, self.status_code = b, 200
                self.request_size, self.download_size, self.header_size = 5000, len(b), 300

        class Http:
            n = 0

            def __init__(self, *a, **k):
                pass

            def post(self, url, data=None, **k):  # every answer: the saved one, no further page
                Http.n += 1
                return Resp(body.replace(b'"has_next_page":true', b'"has_next_page":false'))
        corpus.file_put("fb-session.json", {"post": "variables=" + json.dumps({"count": 3}), "headers": {}, "cookies": {},
                                            "ua": "x", "captured": datetime.now(HKT).isoformat(timespec="seconds")})
        corpus.file_put("fb-ids.json", {"https://www.facebook.com/johnleekachiu": "100083006243795"})
        corpus.file_put("facebook.json", {})
        with open(os.path.join(TMP, "fb.json"), "w") as f:
            json.dump([{"group": "gov", "name": "李家超", "platform": "facebook", "url": "https://www.facebook.com/johnleekachiu",
                        "why": "test", "since": "2026-10-12"}], f)
        with mock.patch.object(watchlist, "FILE", os.path.join(TMP, "fb.json")), mock.patch("curl_cffi.requests.Session", Http), \
                mock.patch.object(fb_fetch, "capture_session", side_effect=AssertionError("no browser in tests")), \
                mock.patch.object(fb_fetch, "FIRST_HOURS", 24 * 365):
            fb_fetch.poll()
        items = corpus.file_get("facebook.json")["items"]
        reel = next(i for i in items if i["post_id"] == "1096104226499775")
        self.assertEqual([c["key"] for c in reel["clips"]], ["fb:1741902233707231"])
        self.assertTrue(reel["images"])
        with mock.patch.object(sources, "parse_time", lambda s: s):
            got, _ = sources.facebook_file()
        corpus.ingest([dict(g, published=datetime.now(HKT).isoformat(timespec="seconds")) for g in got])
        pid = corpus.db().execute("SELECT id FROM posts WHERE url=%s", (reel["url"],)).fetchone()["id"]
        m = corpus.db().execute("SELECT m.key, m.kind, m.status FROM post_media pm JOIN media m ON m.key=pm.key WHERE pm.post_id=%s",
                                (pid,)).fetchone()
        self.assertEqual((m["key"], m["kind"], m["status"]), ("fb:1741902233707231", "ytdlp", "queued"))
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM post_images WHERE post_id=%s", (pid,)).fetchone()[0], 1)


def ig_codes(posts):
    return {p["url"].rstrip("/").rsplit("/", 1)[1]: p for p in posts}


class Instagram(unittest.TestCase):
    def test_3_profile_posts(self):
        am = ig_fetch.posts(fx("ig_am730_profile.html.gz").decode())
        self.assertEqual(len(am), 12)
        for p in am:
            self.assertEqual(len(p["images"]), 1, p["url"])
            self.assertTrue(p["images"][0].startswith("https://scontent"), p["images"])
            self.assertEqual(p["clips"], [])
        cna = ig_codes(ig_fetch.posts(fx("ig_channelnewsasia_profile.html.gz").decode()))
        self.assertEqual(cna["DegRkXyiVs3"]["clips"], [{"key": "ig:DegRkXyiVs3", "url": "https://www.instagram.com/reel/DegRkXyiVs3/",
                                                       "kind": "ytdlp"}])
        self.assertEqual(len(cna["DegRkXyiVs3"]["images"]), 1)  # the reel's cover
        self.assertEqual(cna["DegIvPeGaPH"]["clips"], [])  # a carousel of photos
        self.assertEqual(sum(1 for p in cna.values() if p["clips"]), 10)

    def test_4_threads_posts(self):
        got = ig_codes(ig_fetch.threads_posts(fx("threads_tvbnews_profile.html.gz").decode(), "tvbnews.official"))
        v = got["DeByOltEUNe"]
        self.assertEqual(len(v["clips"]), 1)
        self.assertEqual((v["clips"][0]["key"], v["clips"][0]["kind"]), ("threads:DeByOltEUNe", "direct"))
        self.assertTrue(v["clips"][0]["url"].startswith("https://scontent-hkg1-1.cdninstagram.com/o1/v/t16/"))
        self.assertEqual(len(v["images"]), 1)
        p = got["Ded5iqGCcdn"]
        self.assertEqual(p["clips"], [])
        self.assertEqual(len(p["images"]), 1)
        self.assertIn("847679759_1807", p["images"][0])
        self.assertEqual(got["DegRNtGiYuX"]["images"], [])  # a text post

    def test_5_main_carries_media(self):
        acct = [{"group": "media", "name": "CNA", "platform": "instagram", "handle": "channelnewsasia", "why": "test", "since": "2026-10-12"}]
        html = fx("ig_channelnewsasia_profile.html.gz").decode()

        class R:
            text, url, status_code = html, "https://www.instagram.com/channelnewsasia/", 200
        with open(os.path.join(TMP, "ig.json"), "w") as f:
            json.dump(acct, f)
        corpus.file_put("instagram.json", {"accounts": {}, "items": []})
        with mock.patch.object(ig_fetch, "fetch", lambda *a: (R(), html)), mock.patch.object(watchlist, "FILE", os.path.join(TMP, "ig.json")), \
                mock.patch.object(ig_fetch, "gap_posts", lambda u, n: ([], 0)), mock.patch("curl_cffi.requests.Session", lambda *a, **k: None):
            ig_fetch.main()
        items = ig_codes(corpus.file_get("instagram.json")["items"])
        self.assertEqual([c["key"] for c in items["DegRkXyiVs3"]["clips"]], ["ig:DegRkXyiVs3"])
        self.assertEqual(len(items["DegIvPeGaPH"]["images"]), 1)
        it = sources.extras("instagram", items["DegRkXyiVs3"])
        self.assertTrue(it["images"] and it["clips"])

    def test_6_tikhub_hashtag(self):
        d = json.loads(fx("tikhub_ig_hashtag.json.gz"))
        with mock.patch.object(th, "call", lambda *a, **k: d):
            got = ig_codes(th.instagram("hongkong"))
        self.assertEqual(len(got["DegUVrdkdI3"]["images"]), 6)  # a carousel: every picture
        self.assertEqual(len(got["DegUUP2v5Zs"]["images"]), 1)
        self.assertNotIn("clips", got["DegUUP2v5Zs"])
        vid = dict(d["data"]["items"][0], code="VIDEO123456", media_type=2, is_video=True)
        with mock.patch.object(th, "call", lambda *a, **k: {"data": {"items": [vid]}}):
            v = th.instagram("hongkong")[0]
        self.assertEqual(v["clips"], [{"key": "ig:VIDEO123456", "url": "https://www.instagram.com/reel/VIDEO123456/", "kind": "ytdlp"}])


class FacebookSearch(unittest.TestCase):
    def answer(self, mutate=None):
        d = json.loads(fx("anyapi_fb_search.json.gz"))
        if mutate:
            mutate(d["output"]["data"]["items"])

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return mock.patch("urllib.request.urlopen", lambda *a, **k: R(json.dumps(d).encode()))

    def test_7_images_and_title(self):
        with self.answer():
            got = {p["url"]: p for p in fb_search.search("李家超")}
        ww = next(p for u, p in got.items() if "/wenweipo/" in u)
        self.assertEqual(len(ww["images"]), 1)
        self.assertIn("846087291_1750444800417503", ww["images"][0])
        rmw = next(p for u, p in got.items() if "/rmwhk/" in u)
        self.assertEqual(len(rmw["images"]), 4)  # the album preview's pictures

        def blank(items):
            items[0]["text"] = ""
            items[1]["url"] = "https://www.facebook.com/reel/1409164301324456/"
        with self.answer(blank), mock.patch.object(th, "call", side_effect=AssertionError("AnyAPI, not TikHub")):
            out = th.facebook("李家超")
        empty = next(i for i in out if "/wenweipo/" in i["url"])
        self.assertEqual(empty["title"], "(post without text)")
        self.assertNotEqual(empty["title"], "香港文匯報")
        reel = next(i for i in out if "/reel/" in i["url"])
        self.assertEqual(reel["clips"], [{"key": "fb:1409164301324456", "url": "https://www.facebook.com/reel/1409164301324456/",
                                         "kind": "ytdlp"}])
        for i in out:
            self.assertNotEqual(i["title"], i.get("author"), "a post titled with its author's name")


if __name__ == "__main__":
    unittest.main(verbosity=2)
