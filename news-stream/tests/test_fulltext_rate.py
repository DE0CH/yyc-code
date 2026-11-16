#!/usr/bin/env python3
"""The monitor's full-text rate per source (Deyao, 2026-10-20: "so many sources don't capture the full text"): for
every news, official, blog and forum source, the posts of the last 24 h, how many have the article's text, how many
were read and kept nothing or only the title again (titles.title_only), how many wait for their page; sorted worst
first, so a source that regresses shows at the top of the Full text card.

    TEST_DATABASE_URL=postgresql:///yyc_t_ftrate python3 tests/test_fulltext_rate.py
"""
import os, sys, unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, monitor  # noqa: E402

BODY = "獨立委員會預計本周內向特首提交報告，報告會稍後才公開。" * 20
PAGE = os.path.join(HERE, "web-app", "src", "routes", "-MonitorPage.tsx")


class Rate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t = (datetime.now(corpus.HKT) - timedelta(hours=1)).isoformat(timespec="minutes")
        old = (datetime.now(corpus.HKT) - timedelta(days=3)).isoformat(timespec="minutes")
        items = []
        for i in range(4):  # 集誌社: two kept text, one read with nothing, one only its title again
            items.append({"url": f"https://thecollectivehk.com/a{i}/", "title": f"宏福苑聽證第{i}篇", "sid": "gnews_15",
                          "source": "集誌社", "published": t, "text": BODY if i < 2 else ("" if i == 2 else f"宏福苑聽證第{i}篇")})
        items.append({"url": "https://thecollectivehk.com/p/", "title": "未讀", "sid": "gnews_15", "source": "集誌社",
                      "published": t, "text": ""})
        items.append({"url": "https://news.rthk.hk/x/", "title": "新聞", "sid": "rthk_local", "source": "RTHK",
                      "published": t, "text": BODY})
        items.append({"url": "https://news.rthk.hk/old/", "title": "舊聞", "sid": "rthk_local", "source": "RTHK",
                      "published": old, "text": ""})
        items.append({"url": "https://www.discuss.com.hk/archiver/?tid-32327444.html", "title": "史卓豐以後唔駛指意黎香港",
                      "text": "史卓豐以後唔駛指意黎香港", "sid": "discuss", "source": "香港討論區", "published": t})
        corpus.ingest(items)
        c = corpus.db()
        c.execute("UPDATE posts SET source_type='Forum' WHERE sid='discuss'")
        c.execute("UPDATE posts SET seen=%s WHERE url LIKE '%%/old/'", (old,))
        c.execute("UPDATE posts SET cached_at=%s WHERE url NOT LIKE '%%/p/'", (t,))

    def test_rates(self):
        rows = {r["sid"]: r for r in monitor.fulltext_sources()}
        g = rows["gnews_15"]
        self.assertEqual((g["posts"], g["full"], g["missing"], g["pending"]), (5, 2, 2, 1))
        self.assertEqual(g["rate"], 50)  # of the posts whose page was read
        self.assertEqual(g["source"], "集誌社")
        r = rows["rthk_local"]
        self.assertEqual((r["posts"], r["full"], r["rate"]), (1, 1, 100))  # the 3-day-old post is outside 24 h
        self.assertEqual([x["sid"] for x in monitor.fulltext_sources()][0], "gnews_15")  # worst first
        d = rows["discuss"]  # a forum thread whose opening post is only its title: that is its whole text
        self.assertEqual((d["full"], d["missing"], d["rate"]), (1, 0, 100))

    def test_in_pipeline_and_page(self):
        self.assertIn("sources", monitor.pipeline()["fulltext"])
        self.assertIn("ft.sources", open(PAGE).read())


if __name__ == "__main__":
    unittest.main()
