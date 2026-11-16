#!/usr/bin/env python3
"""Mirror posts (Deyao, 2026-10-20: "why is there a source from scmpauto subreddit but not scmp?"): bot accounts that
re-post an outlet's articles as Reddit link posts (r/SCMPauto, r/AutoNewspaper and the rest of the AutoNewspaper
network's r/<outlet>auto, posted by AutoNewsAdmin / AutoNewspaperAdmin) are pointers to the outlet's article, not posts
of their own. The original article wins: a mirror whose article is in the corpus is dropped; one whose article isn't
is collected as that article (its URL, the outlet's own source when we watch it). A person's Reddit link post stays a
Reddit post, with its link in the text, so it joins the linked article (posts.article_id).

Live case: r/SCMPauto re-posted SCMP's op-ed 「Tai Po fire: public accountability needed to honour those who died」
(scmp.com, 19 Oct 09:30) on 20 Oct 15:48; the Reddit copy (title only) was filed in 宏福苑 at 95 while SCMP's own copy
(full text, seen 19 Oct 09:33, before corpus.FLOOR) was never scored.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_mirror_posts.py
"""
import os, sys, tempfile, unittest, unittest.mock
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, sources, tikhub_fetch  # noqa: E402

HKT = corpus.HKT
OPED = "https://www.scmp.com/opinion/hong-kong-opinion/article/3371144/tai-po-fire-public-accountability-needed-honour-those-who-died"


def rd(pid, sub, author, title, url, self_post=False, sub_title=""):
    """a post as TikHub's Reddit search gives it (fields as seen live, 2026-10-20)"""
    return {"id": f"t3_{pid}", "createdAt": "2026-10-20T07:48:37.152000+0000", "postTitle": title, "url": url,
            "domain": url.split("/")[2].removeprefix("www."), "score": 1, "commentCount": 0,
            "authorInfo": {"name": author}, "permalink": f"/r/{sub}/comments/{pid}/x/", "isSelfPost": self_post,
            "postHint": "LINK" if not self_post else None, "subreddit": {"name": sub, "title": sub_title}}


LIVE = [
    rd("1xalzg8", "SCMPauto", "AutoNewsAdmin", "[Op-Ed] - Tai Po fire: public accountability needed to honour those who died",
       OPED, sub_title="South China Morning Post AutoNews"),
    rd("1xam1vb", "AutoNewspaper", "AutoNewspaperAdmin",
       "[Op-Ed] - Tai Po fire: public accountability needed to honour those who died | South China Morning Post", OPED,
       sub_title="AutoNewspaper: Constantly Updated Automated News"),
    rd("1xnyt01", "NYTauto", "AutoNewsAdmin", "[World] - Hong Kong fire inquiry opens",
       "https://www.nytimes.com/2026/10/20/world/asia/hong-kong-fire.html", sub_title="New York Times AutoNews"),
    rd("1xhk001", "HongKong", "someone", "SCMP: accountability for the Tai Po fire", OPED + "?utm_source=rss_feed"),
    rd("1xhk002", "HongKong", "someone", "Anyone else smell smoke in Tai Po?", "https://www.reddit.com/r/HongKong/comments/1xhk002/x/",
       self_post=True),
]


def search():
    with unittest.mock.patch.object(tikhub_fetch, "call", return_value={"data": LIVE}):
        return {i["url"]: i for i in tikhub_fetch.reddit("Tai Po fire")}


def tikhub_items(items):
    now = datetime.now(HKT).isoformat(timespec="seconds")
    corpus.file_put("tikhub.json", {"fetched": now, "ok": True, "error": None,
                                    "items": [dict(i, key="reddit|Tai Po fire", section="Reddit · Tai Po fire") for i in items],
                                    "checks": {"reddit|Tai Po fire": {"ok": True, "items": len(items), "error": None, "checked": now}}})
    got, _ = sources.tikhub_file()
    return got


class RedditAdapter(unittest.TestCase):
    def test_mirror_marked_with_its_article(self):
        got = search()
        for pid in ("1xalzg8", "1xam1vb", "1xnyt01"):
            it = next(i for u, i in got.items() if pid in u)
            self.assertTrue(it.get("mirror_of", "").startswith("https://www.nytimes.com/" if pid == "1xnyt01" else OPED), pid)

    def test_person_link_post_keeps_link_in_text(self):
        it = next(i for u, i in search().items() if "1xhk001" in u)
        self.assertNotIn("mirror_of", it)
        self.assertIn(OPED, it.get("text") or "")

    def test_self_post_has_no_link(self):
        it = next(i for u, i in search().items() if "1xhk002" in u)
        self.assertNotIn("mirror_of", it)
        self.assertNotIn("http", it.get("text") or "")


class Resolve(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("TRUNCATE posts, follow_links CASCADE")
        import follow  # a site no source reads: the generic reader finds no article on the page (no network)
        p = unittest.mock.patch.object(follow, "_generic_page", return_value="<html></html>")
        p.start()
        self.addCleanup(p.stop)

    def test_original_in_corpus_wins(self):
        orig = corpus.ingest([{"sid": "scmp_sitemap", "source": "南華早報", "url": OPED, "title": "Tai Po fire: public "
                               "accountability needed to honour those who died", "published": "2026-10-19T09:30+08:00"}])
        corpus.assign_articles(orig)
        got = tikhub_items(list(search().values()))
        urls = [i["url"] for i in got]
        self.assertFalse(any("SCMPauto" in u or "AutoNewspaper" in u for u in urls), urls)
        self.assertNotIn(OPED, urls)  # already collected: nothing new
        self.assertTrue(any("1xhk001" in u for u in urls))  # a person's post stays

    def test_original_missing_is_collected_as_the_outlet(self):
        got = tikhub_items(list(search().values()))
        scmp = [i for i in got if i["url"].split("?")[0] == OPED]
        self.assertEqual(len(scmp), 1, [i["url"] for i in got])  # two mirrors of one article → one post
        it = scmp[0]
        self.assertEqual(it["source"], "南華早報")
        self.assertTrue(it["sid"].startswith("scmp"), it["sid"])
        self.assertEqual(it["title"], "Tai Po fire: public accountability needed to honour those who died")
        self.assertIsNone(it["published"])  # the bot's re-post time is not the article's: the page gives it
        self.assertNotIn("mirror_of", it)
        ids = corpus.ingest(got)
        row = corpus.db().execute("SELECT website, platform, source_type FROM posts WHERE url=%s", (it["url"],)).fetchone()
        self.assertEqual((row["website"], row["source_type"]), ("南華早報", "News"))
        self.assertTrue(ids)

    def test_unwatched_outlet_listed_not_collected(self):
        """one routing for every link (follow.py, Deyao 2026-10-20: "no separate mechanism"): a site no source reads
        is listed on the monitor with an example, never collected as a post of a made-up source"""
        import follow
        got = tikhub_items(list(search().values()))
        self.assertFalse(any("nytimes.com" in i["url"] for i in got))
        site = next(s for s in follow.monitor()["unknown_sites"] if s["host"] == "nytimes.com")
        self.assertEqual(site["links"], 1)
        self.assertIn("NYTauto", site["example"]["post_url"])

    def test_mirror_outcomes_recorded_once(self):
        import follow
        orig = corpus.ingest([{"sid": "scmp_sitemap", "source": "南華早報", "url": OPED, "title": "Tai Po fire",
                               "published": "2026-10-19T09:30+08:00"}])
        corpus.assign_articles(orig)
        for _ in range(2):  # every poll reads tikhub.json again
            tikhub_items(list(search().values()))
        rows = corpus.db().execute("SELECT status, count(*) FROM follow_links WHERE post_id IS NULL GROUP BY 1").fetchall()
        self.assertEqual(dict(rows), {"duplicate": 2, "unknown": 1})  # two SCMP mirrors (two bot posts), the NYT one

    def test_person_link_post_joins_the_article(self):
        orig = corpus.ingest([{"sid": "scmp_sitemap", "source": "南華早報", "url": OPED, "title": "Tai Po fire",
                               "published": "2026-10-19T09:30+08:00"}])
        corpus.assign_articles(orig)
        got = [i for i in tikhub_items(list(search().values())) if "1xhk001" in i["url"]]
        ids = corpus.ingest(got)
        corpus.assign_articles(ids)
        self.assertEqual(corpus.db().execute("SELECT article_id FROM posts WHERE id=%s", (ids[0],)).fetchone()[0], orig[0])


if __name__ == "__main__":
    unittest.main()
