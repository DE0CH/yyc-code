#!/usr/bin/env python3
"""No post is titled with a URL (Deyao, 2026-10-15: "This is wrong", an X post in HKIC titled "https://t.co/…").
After the first fix (links.py) production still had 17 X posts whose title column was a URL, or began with one:
X Articles titled "https://x.com/i/article/<id> 〔連結內容：…" (the article's title and body sat in the text after
the link), outlets' link posts titled with the bare link they share (global.hk01.com), tweets whose stored text
opened with a t.co link, and comments carrying their parent's URL title; Threads and Facebook link posts the same.
A URL title also broke the stream's one-item-per-headline check (streams.norm_title: two X Articles whose ids share
their first digits looked like one headline). On a throwaway database (TEST_DATABASE_URL; its tables are dropped
and made again), no network: saved TikHub answers and pages in fixtures/x/, the model stubbed.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_url_titles.py

Covers:
  1. corpus.tidy_title: a title never starts or ends with a URL; a title that is only links becomes 〔連結〕<host>
     until links.py reads what it links to; the link stays in the post's text (so the post is held from scoring).
  2. An X Article is its own post: title = the article's title, text = its body (no x.com/i/article link, no
     〔連結內容〕 wrapper), from search, a watched account and the tweet detail; no AI title is asked for it (it has
     its own), and it is shown without an AI or linked mark.
  3. A link post resolved by links.py takes the linked page's title as its title.
  4. links.py fix-titles corrects the stored posts: X posts read again (tweet detail), resolved link posts retitled
     from their 〔連結內容〕 block, unresolved ones resolved, comments given their parent's title, stream items'
     stored titles updated (the page; Telegram messages are never edited) → before/after counts.
"""
import json, os, re, shutil, sys, tempfile, unittest
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
import ai, app, corpus, item, links, streams, tikhub_fetch as th, titles, watch_fetch, watchlist  # noqa: E402

HKT = app.HKT
NOW = datetime.now(HKT)
FIX = os.path.join(HERE, "tests", "fixtures", "x")
CALLS = []
ARTICLE_ID = "2110305899994558889"
ARTICLE_TITLE = "每人每年5万美元的外汇限额，有什么办法可以突破呢？"
ARTICLE_START = "合法合规的方法其实有一个"
URL_START = re.compile(r"^\s*https?://")


def fx(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return json.loads(f.read())["data"]


def page(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000, **kw):
    CALLS.append((purpose, prompt))
    if purpose == "titles":
        body = prompt.split("Posts:\n", 1)[1]
        return {"titles": [{"i": int(i), "kind": "written", "title": "模型寫的標題測試"}
                           for i in re.findall(r'(?m)^<post i="(\d+)"', body)]}
    return {}


import stage2  # noqa: E402
ai.jsonc = stage2.agree(fake_jsonc, CALLS)  # stage 2 answers as stage 1 (tests/stage2.py)
titles.SETTLE = timedelta(seconds=-60)
streams.WAIT_TEXT = timedelta(0)
streams.FIRST_WAIT = timedelta(seconds=-60)  # a social post (a unit) is not waiting for its comments here
streams.settings()


def replay(**files):
    d = tempfile.mkdtemp()
    for tag, name in files.items():
        shutil.copy(os.path.join(FIX, name), os.path.join(d, tag + ".json"))
    return mock.patch.dict(os.environ, {"TIKHUB_REPLAY_DIR": d})


def row(url):
    r = corpus.db().execute("SELECT * FROM posts WHERE url=%s", (url,)).fetchone()
    return dict(r) if r else None


def no_spend():
    return mock.patch.object(watchlist, "add_tikhub", lambda usd: None)


def due_ids():
    return [p["id"] for p in streams.due(500)] + [p["id"] for p in streams.unit_due(500)]


def url_titled():
    return corpus.db().execute("SELECT count(*) FROM posts WHERE title ~ '^\\s*https?://'").fetchone()[0]


class Tidy(unittest.TestCase):
    def test_tidy_title(self):
        t = corpus.tidy_title
        self.assertEqual(t("https://global.hk01.com/a/60399747"), "〔連結〕global.hk01.com")
        self.assertEqual(t("  https://youtu.be/SBeTwR4nSeI?si=AQ 　"), "〔連結〕youtu.be")
        self.assertEqual(t("https://t.co/O3lIrGP0B6 フィジカルAIの現在地：現実世界へ進出するAI"),
                         "フィジカルAIの現在地：現実世界へ進出するAI")
        self.assertEqual(t("https://x.com/i/article/2110561859241680896 〔連結內容：AI時代，孩子更需要提問能力〕十月"),
                         "〔連結內容：AI時代，孩子更需要提問能力〕十月")
        self.assertEqual(t("新影片喔 https://youtu.be/q--KWIW-LUQ?si=D2Y"), "新影片喔")
        self.assertEqual(t("李家超今日出席 https://bit.ly/x 立法會答問會"), "李家超今日出席 https://bit.ly/x 立法會答問會")
        self.assertEqual(t("李家超今日出席立法會答問會"), "李家超今日出席立法會答問會")
        self.assertEqual(t(""), "")

    def test_bare_link_post_ingested_without_url_title_and_held(self):
        url = "https://www.threads.net/@hk01_hk/post/DefupBojwOb"
        link = "https://www.hk01.com/sns/article/60399111?utm_medium=Social&utm_source=threads"
        corpus.ingest([{"sid": "instagram", "source": "香港01 (Threads)", "section": "hk01_hk", "title": link,
                        "url": url, "published": NOW.isoformat(timespec="seconds"), "author": "hk01_hk"}])
        corpus.db().execute("UPDATE posts SET first_run = FALSE")
        r = row(url)
        self.assertEqual(r["title"], "〔連結〕www.hk01.com")
        self.assertEqual(r["text"], link)  # the link is kept: it is what links.py reads
        self.assertNotIn(r["id"], due_ids())  # held: nothing to score yet
        with mock.patch.object(links, "fetch_page", lambda u: page("page_hk01_global.html")):
            links.run_once()
        r = row(url)
        self.assertEqual(r["title"], "华裔富商邓丕兵被揭柬国电骗大王　捐款建警局　陈志被捕前出逃")
        self.assertIn(r["id"], due_ids())

    def test_set_content_tidies(self):
        url = "https://www.facebook.com/x/posts/1"
        corpus.ingest([{"sid": "facebook", "source": "x (Facebook)", "title": "一個帖", "url": url,
                        "published": NOW.isoformat(timespec="seconds"), "text": "一個帖子的內容"}])
        corpus.set_content(row(url)["id"], "https://youtu.be/abc", "https://youtu.be/abc")
        self.assertEqual(row(url)["title"], "〔連結〕youtu.be")


class XArticle(unittest.TestCase):
    def check(self, x):
        self.assertEqual(x["title"], ARTICLE_TITLE)
        self.assertTrue(x["text"].startswith(ARTICLE_START), x["text"][:80])
        self.assertNotIn("x.com/i/article", x["text"])
        self.assertNotIn("〔連結內容", x["text"])

    def test_detail(self):
        x = th.x_tweet(fx("detail_article.json"))
        self.check(x)
        self.assertIn("移民转移：条件与重要限制", x["text"])  # the whole body

    def test_timeline_preview(self):
        t = next(t for t in fx("user_yifaninvest.json")["timeline"] if t["tweet_id"] == ARTICLE_ID)
        self.check(th.x_tweet(t))

    def test_plain_tweet_title_is_its_text(self):
        t = next(t for t in fx("search.json")["timeline"] if t["tweet_id"] == "1854819694173470812")
        x = th.x_tweet(t)
        self.assertFalse(URL_START.match(x["title"]))
        self.assertTrue(" ".join(x["text"].split()).startswith(x["title"].rstrip("…")))

    def test_link_tweet_title(self):
        x = th.x_tweet(fx("detail_link.json"))
        self.assertEqual(x["text"], "https://global.hk01.com/a/60399375")  # links.py reads it
        self.assertEqual(x["title"], "〔連結〕global.hk01.com")

    def test_watch_and_search_titles(self):
        with replay(api_v1_twitter_web_fetch_user_post_tweet="user_yifaninvest.json",
                    **{"twitter_detail_" + ARTICLE_ID: "detail_article.json"}), no_spend():
            got = watch_fetch.x_posts(fx("user_yifaninvest.json"), {"handle": "yifaninvest"})
        p = next(p for p in got if p["url"].endswith(ARTICLE_ID))
        self.assertEqual(p["title"], ARTICLE_TITLE)
        self.assertTrue(p["text"].startswith(ARTICLE_START))
        with replay(twitter="search.json"):
            items = th.twitter("香港投資管理有限公司")
        self.assertFalse([i for i in items if URL_START.match(i["title"])])

    def test_article_post_shown_with_its_own_title(self):
        url = f"https://x.com/yifaninvest/status/{ARTICLE_ID}"
        x = th.x_tweet(fx("detail_article.json"))
        corpus.ingest([{"sid": "tikhub_twitter", "source": "伊凡投資心得 (X)", "section": "X · 港投",
                        "title": x["title"], "text": x["text"], "url": url,
                        "published": NOW.isoformat(timespec="seconds"), "author": "伊凡投資心得"}])
        CALLS.clear()
        titles.title_posts([dict(row(url), hash=titles.content_hash(row(url)))])
        self.assertFalse([p for k, p in CALLS if k == "titles"])  # no model call: it has its own title
        v = item.view(row(url)["id"])
        self.assertEqual(v["title"], ARTICLE_TITLE)
        self.assertFalse(v.get("title_by_ai"))
        self.assertFalse(v.get("title_linked"))

    def test_stored_t_co_post_resolved_to_article(self):
        """the reported post as first stored: title the t.co URL, no text; links.py reads it through the detail"""
        url = f"https://x.com/someone/status/{ARTICLE_ID}"
        corpus.ingest([{"sid": "tikhub_twitter", "source": "伊凡投資心得 (X)", "section": "X · 港投",
                        "title": "https://t.co/3DS8gH0wK7", "url": url, "published": NOW.isoformat(timespec="seconds")}])
        r = row(url)
        self.assertFalse(URL_START.match(r["title"]))
        self.assertNotIn(r["id"], due_ids())
        with replay(**{"twitter_detail_" + ARTICLE_ID: "detail_article.json"}), no_spend():
            links.run_once()
        r = row(url)
        self.assertEqual(r["title"], ARTICLE_TITLE)
        self.assertTrue(r["text"].startswith(ARTICLE_START))


class FixTitles(unittest.TestCase):
    def test_fix_stored_posts(self):
        when = NOW.isoformat(timespec="seconds")
        art = f"https://x.com/yifaninvest/status/{ARTICLE_ID}"
        old_art = f"https://x.com/i/article/2096065673881604096  〔連結內容：{ARTICLE_TITLE}〕合法合规的方法其实有一个"
        hk01 = "https://x.com/hk01official/status/2110365256505843752"
        hk01_text = "https://global.hk01.com/a/60399747  〔連結內容：女KOL受访自爆曾任柬埔寨园区赌场荷官〕自称曾在东南亚"
        thr = "https://www.threads.net/@am730hk/post/DeeBeULE9lF"
        thr_link = "https://www.am730.com.hk/article/1058419"
        rows = [
            {"sid": "tikhub_twitter", "source": "伊凡投資心得 (X)", "url": art, "title": old_art[:160], "text": old_art},
            {"sid": "watch_x", "source": "香港01 (X)", "url": hk01, "title": "https://global.hk01.com/a/60399747",
             "text": hk01_text},
            {"sid": "instagram", "source": "am730 (Threads)", "url": thr, "title": thr_link, "text": thr_link},
        ]
        # stored as the old code stored them (raw SQL: ingest would tidy them now)
        c = corpus.db()
        for r in rows:
            c.execute("""INSERT INTO posts (url, sid, source, platform, title, text, published, seen, day)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (r["url"], r["sid"], r["source"], "X" if "x.com" in r["url"] else "Threads", r["title"],
                       r["text"], when, when, NOW.strftime("%Y-%m-%d")))
        c.execute("""INSERT INTO posts (url, sid, source, platform, title, text, published, seen, day, thread)
                     VALUES (%s,'instagram','am730 (Threads)','Threads',%s,'包租婆？',%s,%s,%s,%s)""",
                  (thr + "#c1", thr_link, when, when, NOW.strftime("%Y-%m-%d"), thr))
        c.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s,'HKIC',%s,%s)",
                  (NOW.strftime("%Y-%m-%d"), hk01, corpus.J({"title": "https://global.hk01.com/a/60399747",
                                                            "url": hk01, "post_id": row(hk01)["id"]})))
        before = url_titled()
        self.assertGreaterEqual(before, 4)
        with replay(**{"twitter_detail_" + ARTICLE_ID: "detail_article.json"}), no_spend(), \
                mock.patch.object(links, "fetch_page", lambda u: page("page_am730.html")):
            got = links.fix_titles()
        self.assertEqual((got["before"], got["after"]), (before, 0))
        self.assertEqual(url_titled(), 0)
        r = row(art)
        self.assertEqual(r["title"], ARTICLE_TITLE)
        self.assertTrue(r["text"].startswith(ARTICLE_START))
        self.assertEqual(row(hk01)["title"], "女KOL受访自爆曾任柬埔寨园区赌场荷官")
        self.assertEqual(row(thr)["title"], "伍富橋宣布BB早產一個月 太太梁兆楹榮升媽咪母子平安 | am730")
        self.assertEqual(row(thr + "#c1")["title"], row(thr)["title"])  # a comment carries its parent's title
        it = c.execute("SELECT item FROM stream_items WHERE url=%s", (hk01,)).fetchone()[0]
        self.assertEqual(it["title"], "女KOL受访自爆曾任柬埔寨园区赌场荷官")


if __name__ == "__main__":
    unittest.main(verbosity=2)
