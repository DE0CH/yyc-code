#!/usr/bin/env python3
"""A body that is only the title again is not the article's text: the item page never labels it 全文.

2026-10-20 (Deyao): Dimsum Daily's 「Hong Kong showcases strengths at APEC finance ministers' meeting as John Lee and
Paul Chan set out growth agenda」 (gnews_en_0, published 16:49) showed 內容 「全文」 「全文保存於 10月20日 16:57」 under
the headline sentence alone. Its page read had found nothing (text ""), then stage-2 scoring gave it an AI title
("retitled") and titles.shown fell back to the collected title for the text: the old title went out as 全文. The same
holds for a stored text that is the title plus a link or a few characters (an outlet's X post).

A social post or a comment is different: its collector gave its whole text, so a text equal to its title is that
whole text and is kept.

What the 內容 card says without text (Deyao, 2026-10-20, a 政府新聞處 YouTube post: "maybe full text collection is
still running, say that instead of the source dont' provide it, and don't say source don't provide it anyways, say we
are unable to fetch it"): item.view's text_state is a state the pipeline records, never a guess from age:
"full" (text), "due" (a reader will still read it: text_due), "failed" (read, nothing kept: cached_at), "whole" (a
social post or comment whose whole text is its title), "none" (no reader covers it). The page says 「正在收集內文」 for
due and 「未能取得內文。」 for failed / none, and never claims a source gives only a title.

Both implementations are checked: item.py (the agent, MCP) and the site's web-app/src/server/item.ts (edge()).

    TEST_DATABASE_URL=postgresql:///yyc_t_titleonly python3 tests/test_title_only_text.py
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, edge_config, item, titles  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)
from edge_call import edge  # noqa: E402

T = "2026-10-20T16:49+08:00"
HEAD = "Hong Kong showcases strengths at APEC finance ministers’ meeting as John Lee and Paul Chan set out growth agenda"
BODY = "Chief Executive John Lee said Hong Kong would use the meeting to show its strengths. " * 12


class TitleOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        items = [
            {"url": "https://www.dimsumdaily.hk/apec/", "title": HEAD, "text": "", "sid": "gnews_en_0",
             "source": "Dimsum Daily", "published": T},
            {"url": "https://www.dimsumdaily.hk/apec-2/", "title": HEAD, "sid": "gnews_en_0", "source": "Dimsum Daily",
             "text": HEAD + " https://www.dimsumdaily.hk/apec-2/", "published": T},
            {"url": "https://www.dimsumdaily.hk/apec-3/", "title": HEAD, "text": BODY, "sid": "gnews_en_0",
             "source": "Dimsum Daily", "published": T},
            {"url": "https://www.threads.com/@x/post/1", "title": "屈穎妍文章令我非常仇恨香港特區政府！", "sid": "tikhub_threads",
             "source": "x (Threads)", "published": T},
            {"url": "https://news.example.com/due/", "title": "未讀的新聞", "text": "", "sid": "rthk_local", "source": "RTHK",
             "published": T},
            {"url": "https://www.youtube.com/watch?v=f-Zvashrg0M", "title": "特首寄語學生弘揚紅軍長征精神", "text": "",
             "sid": "yt_LyP6K8lQ", "source": "政府新聞處 (YouTube)", "published": T},
            {"url": "https://www.discuss.com.hk/archiver/?tid-32327444.html", "title": "史卓豐以後唔駛指意黎香港",
             "text": "史卓豐以後唔駛指意黎香港", "sid": "discuss", "source": "香港討論區", "published": T},
            {"url": "https://news.google.com/rss/articles/CBMfixture1", "title": "A Google News link we never read", "text": "",
             "sid": "gnews_en_0", "source": "Google News", "published": T},
        ]
        corpus.ingest(items)
        cls.ids = [corpus.ids_for_urls([i["url"]])[i["url"]] for i in items]
        c = corpus.db()
        c.execute("UPDATE posts SET cached_at=%s WHERE id = ANY(%s)", ("2026-10-20T16:57:24+08:00", cls.ids[:3]))
        c.execute("UPDATE posts SET source_type='Social Media' WHERE id=%s", (cls.ids[3],))
        c.execute("UPDATE posts SET source_type='Video' WHERE id=%s", (cls.ids[5],))
        c.execute("UPDATE posts SET source_type='Forum' WHERE id=%s", (cls.ids[6],))  # a thread: its whole text is its title
        for pid in cls.ids[:3]:  # stage 2's new title, as on the live post
            titles.store({"id": pid}, "John Lee and Paul Chan set out Hong Kong growth agenda at APEC meeting",
                         "retitled", "anthropic/claude-opus-5.5")
        edge_config.publish()

    def check(self, view, text, real):
        if real:
            self.assertGreater(view["text_chars"], 500)
            self.assertIn("Chief Executive", text["text"])
        else:
            self.assertEqual(view["text_chars"], 0)
            self.assertEqual(text["text"], "")
            self.assertEqual(view["extract"], "")

    # the YouTube video: its description is still to be read (fulltext_fetch.will_read: sources.youtube_due); the
    # Google News link: no reader covers its host (cache_fetch.SKIP_HOSTS)
    STATES = ["failed", "failed", "full", "full", "due", "due", "full", "none"]

    def social(self, view, text):
        self.assertEqual(view["text_chars"], len("屈穎妍文章令我非常仇恨香港特區政府！"))  # its whole text, kept
        self.assertEqual(text["text"], "屈穎妍文章令我非常仇恨香港特區政府！")

    def test_python(self):
        for pid, real in zip(self.ids[:3], (False, False, True)):
            self.check(item.view(pid), item.text(pid), real)
        self.social(item.view(self.ids[3]), item.text(self.ids[3]))
        self.assertEqual([item.view(p)["text_state"] for p in self.ids], self.STATES)

    def test_site(self):
        for pid, real in zip(self.ids[:3], (False, False, True)):
            self.check(edge("item.view", pid), edge("item.text", pid), real)
        self.social(edge("item.view", self.ids[3]), edge("item.text", self.ids[3]))
        self.assertEqual([edge("item.view", p)["text_state"] for p in self.ids], self.STATES)

    def test_page_wording(self):
        """no page claims a source gives only a title; due and failed have their own lines"""
        src = os.path.join(HERE, "web-app", "src")
        for root, _, files in os.walk(src):
            for f in files:
                t = open(os.path.join(root, f), encoding="utf-8").read()
                self.assertFalse("只提供標題" in t, f)
        page = open(os.path.join(src, "routes", "-ItemPage.tsx"), encoding="utf-8").read()
        self.assertIn("正在收集內文", page)
        self.assertIn("未能取得內文。", page)

    def test_rule(self):
        self.assertTrue(titles.title_only("", HEAD))
        self.assertTrue(titles.title_only(HEAD + "  https://t.co/x", HEAD))
        self.assertTrue(titles.title_only("【" + HEAD + "】詳情", HEAD))
        self.assertTrue(titles.title_only("宏福苑聽證", "宏福苑聽證｜獨委會將交報告"))
        self.assertFalse(titles.title_only(BODY, HEAD))
        self.assertFalse(titles.title_only("特首李家超今日表示，政府會繼續推動房屋政策。", "李家超談房屋"))


if __name__ == "__main__":
    unittest.main()
