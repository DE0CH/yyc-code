#!/usr/bin/env python3
"""A post's AI analysis, on a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_item_ai.py

1. item.view (public: the agent's tools, the MCP server): the scorer's sentiment and labels (`scoring`), each kept
   stream's category, the labels' Chinese names and meanings (vocab.label_info), the post's article (the other posts
   with its article_id); never a score, a reason or a threshold.
2. The item page's AI analysis for approved accounts (/app/item/ai: the edge app's item.aiScores, run in Node by
   tests/edge_call.py): every stream with its score, reason, category, threshold and whether it passed, the stream it
   was kept in, and why a post was not scored.
"""
import json, os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, edge_config, insights, item, streams  # noqa: E402
from edge_call import edge  # noqa: E402

ai.model_name = lambda m: "Claude Haiku 5.5"  # no OpenRouter call in tests


def view(pid):
    return json.loads(json.dumps(item.view(pid), default=str))


class ItemAI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.kv_set("scoring", {"from_id": 0, "comments": False})
        corpus.kv_set("stream_thresholds", {"FSHK": 70})
        T = insights.now().isoformat(timespec="minutes")  # fresh: older posts are "old" to the scorer
        cls.day = T[:10]
        items = [
            {"url": "https://news.mingpao.com/s1", "title": "特首出席立法會答問會", "text": "特首今日出席答問會。" * 30,
             "sid": "mingpao_x", "source": "明報", "published": T},
            {"url": "https://hk.on.cc/s2", "title": "特首出席立法會答問會（東方）", "text": "特首今日出席答問會。" * 30,
             "sid": "oncc", "source": "東方日報", "published": T},
            {"url": "https://www.rthk.hk/n1", "title": "天文台：明日有雨", "text": "天氣", "sid": "rthk",
             "source": "RTHK 香港電台", "published": T},
        ]
        corpus.ingest(items)
        ids = corpus.ids_for_urls(i["url"] for i in items)
        cls.s1, cls.s2, cls.n1 = ids[items[0]["url"]], ids[items[1]["url"]], ids[items[2]["url"]]
        db = corpus.db()
        db.execute("UPDATE posts SET article_id=%s WHERE id = ANY(%s)", (cls.s1, [cls.s1, cls.s2]))
        db.execute("""INSERT INTO scored (post_id, hash, sentiment, labels, at)
                      SELECT id, md5(coalesce(title, '') || chr(10) || coalesce(text, '')), 'negative', '["issue","monitor"]',
                             '2026-10-14T10:05+08:00' FROM posts WHERE id=%s""", (cls.s1,))
        for st, sc, reason, cat in (("CEHK", 92, "特首主講答問會", "立法會"), ("FSHK", 65, "財爺列席", None),
                                    ("POL", 25, "政壇花絮略提", None)):
            db.execute("INSERT INTO scores (stream, post_id, score, reason, category, scored_at) VALUES (%s,%s,%s,%s,%s,%s)",
                       (st, cls.s1, sc, reason, cat, "2026-10-14T10:05+08:00"))
        db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s)",
                   (cls.day, "CEHK", items[0]["url"],
                    corpus.J(dict(items[0], stream="CEHK", score=92, reason="特首主講答問會", category="立法會",
                                 seen=cls.day + "T10:06:00+08:00"))))  # filed (and sent) that day: 串流's day
        corpus.kv_set("openrouter_models", {"at": 9e12, "models": {ai.MODEL: {"name": "Anthropic: Claude Haiku 5.5"}}})
        corpus.kv_set("edge:config", edge_config.build())

    # 1
    def test_public_fields(self):
        d = view(self.s1)
        self.assertEqual((d["scoring"]["sentiment"], d["scoring"]["labels"], d["scoring"]["rescoring"]),
                         ("negative", ["issue", "monitor"], False))
        self.assertEqual(d["streams"], [{"stream": "CEHK", "name": "CEHK", "day": self.day, "category": "立法會"}])
        self.assertEqual(d["vocab"]["label_info"]["issue"]["zh"], "議題")
        self.assertTrue(d["vocab"]["label_info"]["monitor"]["meaning"])
        self.assertEqual(set(d["vocab"]["label_info"]), set(ai.LABELS))
        self.assertEqual(d["article"]["id"], self.s1)
        self.assertEqual([p["id"] for p in d["article"]["posts"]], [self.s2])
        blob = json.dumps(d, ensure_ascii=False)
        for secret in ("特首主講答問會", "財爺列席", '"score"', '"threshold"', '"reason"'):
            self.assertNotIn(secret, blob)  # scores and reasons are for approved accounts only
        n = view(self.n1)
        self.assertEqual((n["scoring"], n["not_scored"], n["article"]), (None, "due", None))

    # 2
    def test_approved_scores(self):
        d = edge("item.aiScores", self.s1)
        self.assertEqual((d["scored_at"], d["not_scored"], d["floor"], d["model"]),
                         ("2026-10-14T10:05+08:00", None, 20, "Claude Haiku 5.5"))
        by = {x["stream"]: x for x in d["streams"]}
        self.assertEqual(set(by), {s["id"] for s in insights.streams()})  # every scored stream, scored or not
        self.assertEqual([x["stream"] for x in d["streams"][:3]], ["CEHK", "FSHK", "POL"])  # highest first
        c = by["CEHK"]
        self.assertEqual((c["score"], c["reason"], c["category"], c["threshold"], c["passed"], c["kept"]),
                         (92, "特首主講答問會", "立法會", 50, True, {"day": self.day, "score": 92}))
        self.assertEqual((by["FSHK"]["score"], by["FSHK"]["threshold"], by["FSHK"]["passed"], by["FSHK"]["kept"]),
                         (65, 70, False, None))
        self.assertEqual((by["HKIC"]["score"], by["HKIC"]["passed"]), (None, False))  # below the floor
        n = edge("item.aiScores", self.n1)
        self.assertEqual((n["scored_at"], n["not_scored"]), (None, "due"))
        self.assertTrue(all(x["score"] is None for x in n["streams"]))
        corpus.kv_set("scoring", {"from_id": self.n1, "comments": False})
        self.assertEqual(edge("item.aiScores", self.n1)["not_scored"], "before")
        corpus.kv_set("scoring", {"from_id": 0, "comments": False})
        with self.assertRaises(RuntimeError):  # no such post
            edge("item.aiScores", 99999999)


if __name__ == "__main__":
    unittest.main(verbosity=2)
