#!/usr/bin/env python3
"""One Galbot stream (Deyao, 2026-11-11: "there's only one galbot channel"): streams.json has GALBOT alone, with the
Telegram channel and WhatsApp group Galbot 香港 had, and streams.fold_galbot_split() folds what Galbot 香港 and
Galbot 內地 (GALBOT_HK / GALBOT_CN) held into GALBOT once, on a throwaway database (TEST_DATABASE_URL):

  - their stream items become GALBOT's (item.stream too); a (day, url) GALBOT already holds stays GALBOT's alone;
  - their score rows (copies of GALBOT's) go; GALBOT's stay;
  - a hand-corrected tag moves to GALBOT; their other tags go (the tag sync rebuilds GALBOT's members);
  - their report files become GALBOT's;
  - GALBOT's Telegram and WhatsApp cursors become the furthest of the three, so nothing sent is sent again, and the
    children's keys leave the kv maps;
  - it runs once (kv migrated:galbot_one_stream) and a second call changes nothing.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_galbot_fold.py
"""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402
with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as _c:
    _c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
import corpus, streams  # noqa: E402

DAY = "2026-11-10"


def post(url):
    corpus.ingest([{"url": url, "title": "銀河通用 " + url, "sid": "hk01", "source": "香港01", "published": None}])
    return corpus.ids_for_urls([url])[url]


def item(stream, url, day=DAY):
    return corpus.db().execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s) RETURNING id",
                               (day, stream, url, corpus.J({"stream": stream, "url": url, "title": url}))).fetchone()[0]


class Fold(unittest.TestCase):
    def test_0_streams_json(self):
        with open(os.path.join(HERE, "streams.json")) as f:
            st = {s["id"]: s for s in json.load(f)}
        self.assertNotIn("GALBOT_HK", st)
        self.assertNotIn("GALBOT_CN", st)
        g = st["GALBOT"]
        self.assertEqual((g["telegram_chat"], g["whatsapp_group"]), (-1004317458957, "120363412057377420@g.us"))
        for k in ("split", "parent", "market", "flag"):
            self.assertNotIn(k, g)

    def test_1_fold(self):
        c = corpus.db()
        urls = [f"https://news.test/g{i}" for i in range(5)]
        p = [post(u) for u in urls]
        old = item("GALBOT", urls[0], "2026-10-01")  # from before the split
        hk1, hk2 = item("GALBOT_HK", urls[1]), item("GALBOT_HK", urls[2])
        cn = item("GALBOT_CN", urls[3])
        item("GALBOT", urls[4])
        item("GALBOT_HK", urls[4])  # the same (day, url) as GALBOT's own: GALBOT's stays
        for sid in ("GALBOT", "GALBOT_HK"):
            c.execute("INSERT INTO scores (stream, post_id, score, reason) VALUES (%s, %s, 80, 'r')", (sid, p[1]))
        corpus.set_tags("GALBOT_HK", [(p[1], True, "neutral", []), (p[2], True, "neutral", [])])
        c.execute("UPDATE tags SET corrected = TRUE WHERE profile = 'GALBOT_HK' AND post_id = %s", (p[2],))
        c.execute("INSERT INTO report_files (day, profile, name, ctype, body) VALUES (%s, 'GALBOT_HK', 'report.pdf', "
                  "'application/pdf', 'x')", (DAY,))
        corpus.kv_set("telegram_sent", {"CEHK": 7, "GALBOT": old, "GALBOT_HK": hk2, "GALBOT_CN": cn})
        corpus.kv_set("whatsapp_sent", {"CEHK": 7, "GALBOT": old, "GALBOT_HK": hk1})
        corpus.kv_set("telegram_sent_at", {"GALBOT_HK": "2026-11-10T10:00:00+08:00"})

        moved = streams.fold_galbot_split()
        self.assertEqual(moved, 3)  # hk1, hk2, cn (the duplicate of GALBOT's own is dropped)
        rows = c.execute("SELECT stream, url, item->>'stream' FROM stream_items ORDER BY id").fetchall()
        self.assertEqual({r[0] for r in rows}, {"GALBOT"})
        self.assertEqual({r[2] for r in rows}, {"GALBOT"})
        self.assertEqual(sorted(r[1] for r in rows), sorted(urls))
        self.assertEqual(c.execute("SELECT stream FROM scores WHERE post_id = %s", (p[1],)).fetchall(), [("GALBOT",)])
        self.assertEqual(c.execute("SELECT profile, post_id FROM tags ORDER BY post_id").fetchall(), [("GALBOT", p[2])])
        self.assertEqual(c.execute("SELECT profile FROM report_files").fetchall(), [("GALBOT",)])
        self.assertEqual(corpus.kv_get("telegram_sent"), {"CEHK": 7, "GALBOT": max(hk2, cn)})
        self.assertEqual(corpus.kv_get("whatsapp_sent"), {"CEHK": 7, "GALBOT": hk1})
        self.assertEqual(corpus.kv_get("telegram_sent_at"), {"GALBOT": "2026-11-10T10:00:00+08:00"})

        # once: a later GALBOT_HK row (none can appear, the writer is gone) is left alone
        self.assertIsNone(streams.fold_galbot_split())
        self.assertTrue(corpus.kv_get("migrated:galbot_one_stream"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
