#!/usr/bin/env python3
"""One post's data (item.py: what the agent's posts_get tool and the MCP server read; the item page itself is the edge
app's, web-app/src/server/item.ts), on a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_item.py

1. Tag corrections made by hand (tags.corrected): such a row survives every writer (corpus.set_tags from the stream
   tagging, the profiles' tagging and the backfill; corpus.set_themes); an uncorrected row is still overwritten.
   Nothing on the website writes tags.
2. item.view: origin and times, an extract only (never the full text), engagement, the comment thread (from the post
   and from one of its comments), per-profile tags, matched profiles and streams, same-story coverage from other
   outlets; LookupError for a post that doesn't exist.
3. /app/login (the AI server): back to the item page it was sent from, never to another host.
4. item.text: the full text and full comments. No route edits tags: POST /app/item/tags answers 404 for everyone.
"""
import io, json, os, sys, unittest, urllib.parse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, item, web  # noqa: E402
from edge_call import edge  # noqa: E402

LONG = "施政報告提出多項房屋措施。" * 60  # 780 characters: more than the public extract


def call(path, method="GET", body=None):
    p, _, q = path.partition("?")
    raw = json.dumps(body or {}).encode()
    env = {"REQUEST_METHOD": method, "PATH_INFO": urllib.parse.unquote(p), "QUERY_STRING": q, "RAW_URI": path,
           "wsgi.input": io.BytesIO(raw), "CONTENT_LENGTH": str(len(raw)), "CONTENT_TYPE": "application/json"}
    got = {}
    data = b"".join(app.application(env, lambda s, h: got.update(status=int(s.split()[0]), headers=dict(h))))
    return got["status"], got["headers"], data


class Approved:
    """web.access_user answers an account (as the Access JWT would) inside the block"""
    def __enter__(self):
        self.old = web.ADMIN_DEV
        web.ADMIN_DEV = True

    def __exit__(self, *a):
        web.ADMIN_DEV = self.old


class Item(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        T = "2026-10-12T10:00+08:00"
        items = [
            {"url": "https://news.mingpao.com/a1", "title": "特首宣布施政報告房屋新措施", "text": LONG, "sid": "mingpao_x",
             "source": "明報", "section": "港聞", "author": "記者甲", "published": T, "engagement": {"likes": 12, "shares": 3}},
            {"url": "https://hk.on.cc/b1", "title": "特首宣布施政報告房屋新措施！", "text": "東方的報道", "sid": "oncc",
             "source": "東方日報", "published": "2026-10-12T11:00+08:00"},
            {"url": "https://news.mingpao.com/a2", "title": "特首宣布施政報告房屋新措施（多圖）", "text": "明報另一篇", "sid": "mingpao_x",
             "source": "明報", "published": "2026-10-12T12:00+08:00"},
            {"url": "https://www.rthk.hk/c1", "title": "天文台：明日有雨", "text": "天氣", "sid": "rthk", "source": "RTHK 香港電台",
             "published": "2026-10-12T09:00+08:00"},
            {"url": "https://news.mingpao.com/old", "title": "特首宣布施政報告房屋新措施", "text": "", "sid": "mingpao_x",
             "source": "明報", "published": "2026-09-01T09:00+08:00"},
        ]
        corpus.ingest(items)
        cls.ids = corpus.ids_for_urls(i["url"] for i in items)
        cls.a1 = cls.ids["https://news.mingpao.com/a1"]
        corpus.add_comments(items[0], [{"id": "x1", "author": "讀者一", "text": "支持" + "長" * 300, "published": "2026-10-12T10:30+08:00", "likes": 4, "dislikes": 2},
                                       {"id": "x2", "author": "讀者二", "text": "反對", "published": "2026-10-12T10:40+08:00"}], total=9)
        cls.c2 = corpus.ids_for_urls(["https://news.mingpao.com/a1#cx2"])["https://news.mingpao.com/a1#cx2"]
        corpus.set_tags("CEHK", [(cls.a1, True, "neutral", ["monitor"])])
        corpus.set_tags("FSHK", [(cls.a1, False, "neutral", [])])
        corpus.db().execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s)",
                            ("2026-10-12", "CEHK", items[0]["url"], corpus.J(dict(items[0], stream="CEHK", sentiment="neutral", labels=["monitor"]))))
        rthk = cls.ids["https://www.rthk.hk/c1"]
        corpus.set_tags("CEHK", [(rthk, True, "positive", [])])
        cls.rthk = rthk
        # 數據分析's CEHK = the posts scored at or above its threshold (50); FSHK's 30 keeps a1 out of FSHK
        corpus.db().executemany("INSERT INTO scores (stream, post_id, score, reason, category, scored_at) VALUES (%s,%s,%s,'t',NULL,%s)",
                                [("CEHK", cls.a1, 90, T), ("CEHK", rthk, 60, T), ("FSHK", cls.a1, 30, T)])

    # 1
    def test_corrected_rows_survive_every_writer(self):
        b = corpus.ids_for_urls(["https://hk.on.cc/b1"])["https://hk.on.cc/b1"]
        corpus.set_tags("HKIC", [(b, True, "neutral", ["monitor"])])
        corpus.db().execute("""UPDATE tags SET relevant=0, sentiment='negative', labels='["crisis"]', theme='人手主題',
                               corrected=TRUE, corrected_by='a@b.c', corrected_at='2026-10-13T10:00:00+08:00'
                               WHERE profile='HKIC' AND post_id=%s""", (b,))
        corpus.set_tags("HKIC", [(b, True, "positive", ["praise"])])
        corpus.set_themes("HKIC", [(b, "AI 主題")])
        r = corpus.db().execute("SELECT * FROM tags WHERE profile='HKIC' AND post_id=%s", (b,)).fetchone()
        self.assertEqual((r["relevant"], r["sentiment"], json.loads(r["labels"]), r["theme"], r["corrected"], r["corrected_by"]),
                         (0, "negative", ["crisis"], "人手主題", True, "a@b.c"))
        corpus.set_tags("POL", [(b, True, "neutral", [])])  # an uncorrected row is still written over
        corpus.set_tags("POL", [(b, True, "positive", ["praise"])])
        corpus.set_themes("POL", [(b, "主題")])
        r = corpus.db().execute("SELECT * FROM tags WHERE profile='POL' AND post_id=%s", (b,)).fetchone()
        self.assertEqual((r["sentiment"], json.loads(r["labels"]), r["theme"], r["corrected"]), ("positive", ["praise"], "主題", None))

    # 2
    def test_view(self):
        d = item.view(self.a1)
        self.assertEqual((d["website"], d["board"], d["author"], d["source_type"], d["region"], d["sid"], d["day"]),
                         ("明報", "港聞", "記者甲", "News", "HK", "mingpao_x", "2026-10-12"))
        self.assertEqual(d["published"], "2026-10-12T10:00+08:00")
        self.assertTrue(d["seen"])
        self.assertLessEqual(len(d["extract"]), 201)
        self.assertEqual(d["text_chars"], len(LONG))
        self.assertNotIn(LONG, json.dumps(d, ensure_ascii=False))
        self.assertNotIn("text", d)
        self.assertEqual(d["engagement"], {"likes": 12, "shares": 3, "comments": 9})
        th = d["thread"]
        self.assertEqual((th["total"], th["stated"], [c["author"] for c in th["comments"]]), (2, 9, ["讀者一", "讀者二"]))
        self.assertTrue(th["comments"][0]["cut"])
        self.assertLessEqual(len(th["comments"][0]["text"]), 201)
        self.assertEqual((th["comments"][0]["likes"], th["comments"][0]["dislikes"]), (4, 2))
        self.assertEqual((th["comments"][1]["likes"], th["comments"][1]["dislikes"]), (None, None))
        self.assertEqual(d["matched"], ["CEHK"])
        self.assertEqual({t["profile"]: (t["relevant"], t["sentiment"]) for t in d["tags"]},
                         {"CEHK": (True, "neutral"), "FSHK": (False, "neutral")})
        self.assertEqual(d["streams"], [{"stream": "CEHK", "name": "CEHK", "day": "2026-10-12", "category": None}])
        cov = {x["website"] for x in d["coverage"]}
        self.assertIn("東方日報", cov)  # the same story from another outlet
        self.assertNotIn("明報", cov)    # the same outlet's other copy is not "other coverage"
        self.assertFalse(any(x["title"].startswith("天文台") for x in d["coverage"]))
        self.assertFalse(d["comment"])
        # a comment row: its thread with the parent
        c = item.view(self.c2)
        self.assertTrue(c["comment"])
        self.assertEqual(c["thread"]["parent"]["id"], self.a1)
        self.assertEqual(c["thread"]["total"], 2)
        self.assertEqual(c["coverage"], [])
        with self.assertRaises(LookupError):
            item.view(99999999)

    # 3
    def test_login(self):
        s, h, _ = call(f"/app/login?next=/item/{self.a1}")
        self.assertEqual((s, h["Location"]), (302, f"/item/{self.a1}"))
        s, h, _ = call("/app/login?next=https://evil.example/")
        self.assertEqual(h["Location"], "/dashboard")

    # 4
    def test_text(self):
        body = {"id": self.a1, "profile": "CEHK", "relevant": True, "sentiment": "negative", "labels": ["crisis"], "theme": "房屋"}
        self.assertEqual(call("/app/item/tags", "POST", body)[0], 404)
        d = item.text(self.a1)
        self.assertEqual(d["text"], LONG)
        self.assertEqual(len(d["comments"][self.ids_c1()]), 302)
        with Approved():
            self.assertEqual(call("/app/item/tags", "POST", body)[0], 404)  # no edits, approved or not

    def ids_c1(self):
        return corpus.ids_for_urls(["https://news.mingpao.com/a1#cx1"])["https://news.mingpao.com/a1#cx1"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
