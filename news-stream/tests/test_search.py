#!/usr/bin/env python3
"""The 搜尋 section and the search index, on a throwaway database (TEST_DATABASE_URL; its tables are dropped and made
again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_search.py

1. Index fuzz: random posts and random queries (CJK, Latin, spaces, quotes, backslashes, colons, one-character terms,
   AND/OR/NOT/NEARBY/FindIn/TopicSearchOnly): the matches with query.prefilter (the GIN lookup on posts.grams) equal
   the matches without it, and rows without grams (written before the index, as migrate_to_postgres.py leaves them)
   are still found; corpus.reindex() fills them.
2. The edge app's 搜尋 (web-app/src/server/search/, run in Node by tests/edge_call.py on this database, with the
   dictionary searchtext.publish_edge writes): filters (type, site, region, dates, titles only, comment rows),
   sorting, paging, facets, the extract (never the full text), errors; the meta (types and sites); the Excel export
   (no full text in it either).
3. The split (web-app/src/server/search/split.ts): a query of words alone sorted by date answers as one statement with
   the same page, total, facets and export as the one query (one and two terms, a Latin term with and without
   post_subs, type/site/region filters, oldest first, a page past the end, no match).
4. yyc-mcp's posts_search (mcp-edge/scripts/call.ts on this database): when the word matches fill the page it skips the
   substring-only part (total counts word matches, total_exact false); otherwise it returns the substring-only rows.
"""
import json, os, random, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, corpus, query, searchtext  # noqa: E402
from edge_call import edge  # noqa: E402
from urllib.parse import parse_qs  # noqa: E402

EXTRACT = 160  # web-app/src/server/search/search.ts


def ids(q, start="2000-01-01", end="2100-01-01"):
    return set(corpus.match_ids(query.parse(q), start, end))


ALPHA = list("港投公司滙豐銀行施政報告房屋土地") + list("abcHSB") + [" ", "'", "\\", ":", "&", "|", "!", "(", "*", '"']


class IndexFuzz(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rnd = random.Random(7)
        items = []
        for i in range(400):
            t = "".join(rnd.choice(ALPHA) for _ in range(rnd.randint(1, 12))).strip() or "x"
            body = "".join(rnd.choice(ALPHA) for _ in range(rnd.randint(0, 60)))
            items.append({"url": f"https://f/{i}", "title": t, "text": body, "author": rnd.choice([None, "陳", "ab c"]),
                          "source": rnd.choice(["明報", "RTHK"]), "sid": "fuzz", "published": "2020-01-01T10:00+08:00"})
        corpus.ingest(items)
        thread = items[0]
        corpus.add_comments(thread, [{"id": "c1", "text": "港投 'quote' \\ back", "author": "x"}])
        cls.rnd = rnd

    def term(self):
        r = self.rnd
        w = "".join(r.choice([c for c in ALPHA if c not in ' "()']) for _ in range(r.randint(1, 3)))
        if w.upper() in ("AND", "OR", "NOT", "NEAR", "NEARBY"):
            w = "港" + w
        return f'"{w}"' if r.random() < .4 else w

    def query(self, depth=0):
        r = self.rnd
        k = r.random()
        if depth > 2 or k < .35:
            return self.term()
        if k < .5:
            return f"{self.query(depth + 1)} AND {self.query(depth + 1)}"
        if k < .65:
            return f"({self.query(depth + 1)} {self.query(depth + 1)})"
        if k < .75:
            return f"{self.query(depth + 1)} NOT {self.term()}"
        if k < .85:
            return f"{self.term()} NEARBY/{r.randint(0, 8)} {self.term()}"
        if k < .93:
            return f"FindIn({r.choice(['title', 'content', 'author', 'source'])}: {self.term()})"
        return f"TopicSearchOnly {self.term()}"

    def test_same_matches_with_and_without_the_index(self):
        real = query.prefilter
        checked = narrowed = 0
        for _ in range(600):
            q = self.query()
            try:
                parsed = query.parse(q)
            except query.QueryError:
                continue
            with_index = ids(q)
            query.prefilter = lambda q: None
            try:
                without = ids(q)
            finally:
                query.prefilter = real
            self.assertEqual(with_index, without, q)
            checked += 1
            narrowed += bool(real(parsed))
        self.assertGreater(checked, 400)
        self.assertGreater(narrowed, 200)  # most queries do use the index

    def test_rows_without_grams(self):
        corpus.db().execute("UPDATE posts SET grams=NULL WHERE id IN (SELECT id FROM posts ORDER BY id LIMIT 50)")
        q = "港投 OR 'a OR \\\\ OR 施政"
        real = query.prefilter
        query.prefilter = lambda q: None
        try:
            want = ids(q)
        finally:
            query.prefilter = real
        self.assertEqual(ids(q), want)
        corpus.db().execute("UPDATE posts SET search_v=NULL WHERE grams IS NULL")
        self.assertEqual(corpus.reindex(batch=7, pause=0), 50)
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM posts WHERE grams IS NULL").fetchone()[0], 0)
        self.assertEqual(ids(q), want)

    def test_index_is_used(self):
        where, args = query.to_sql(query.parse('港投公司 AND "滙豐"'))
        with corpus.db() as c:  # a small table is read whole anyway: ask the planner what it does on a large one
            c.execute("SET LOCAL enable_seqscan = off")
            plan = "\n".join(r[0] for r in c.execute("EXPLAIN SELECT p.id FROM posts p WHERE " + where, args))
        self.assertIn("posts_grams", plan)


LONG = "立法會今日討論房屋政策。" * 40 + "港投公司宣布投資半導體企業，涉及金額十億元。" + "其後議員再談土地供應。" * 40


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        posts = [
            ("a1", "港投公司投資半導體", LONG, "記者甲", "明報", "mingpao_ins_all", "2026-10-10T09:00+08:00"),
            ("a2", "港投公司年報出爐", "年報內容", None, "香港01", "hk01", "2026-10-11T09:00+08:00"),
            ("a3", "政府公布施政報告", "施政報告提到港投公司", None, "政府新聞公報", "infogov_press", "2026-10-12T09:00+08:00"),
            ("a4", "無關新聞", "天氣", None, "明報", "mingpao_ins_all", "2026-10-12T10:00+08:00"),
            ("a5", "舊新聞 港投公司", "", None, "明報", "mingpao_ins_all", "2025-01-05T10:00+08:00"),
            ("a6", "討論港投", "港投好唔好", "user", "LIHKG討論區", "lihkg", "2026-10-12T11:00+08:00"),
            ("a7", "Trump tariffs", "trumpets played", None, "明報", "mingpao_ins_all", "2026-10-09T09:00+08:00"),
            ("a8", "trumpism rises", "", None, "香港01", "hk01", "2026-10-09T10:00+08:00"),
        ]
        items = [{"url": f"https://s/{u}", "title": t, "text": x, "author": a, "source": s, "sid": sid, "published": p}
                 for u, t, x, a, s, sid, p in posts]
        corpus.ingest(items)
        corpus.add_comments(items[5], [{"id": "r1", "text": "港投公司係咪蝕錢", "author": "u2", "published": "2026-10-12T12:00+08:00"}])
        searchtext.publish_edge()

    def get(self, qs):
        """the search of a query string → (status, JSON) as /api/search answers it"""
        d = edge("search/search.runMany", [parse_qs(qs)])[0]
        return (d["status"], d) if set(d) == {"status", "error"} else (200, d)

    def test_search(self):
        code, d = self.get("q=港投公司&from=2026-10-01&to=2026-10-13")
        self.assertEqual(code, 200)
        self.assertEqual([p["url"] for p in d["posts"]], ["https://s/a3", "https://s/a2", "https://s/a1"])  # newest first
        self.assertEqual(d["total"], 3)
        self.assertEqual(d["facets"]["types"], {"News": 2, "Official": 1})
        a1 = d["posts"][2]
        self.assertNotIn("text", a1)
        self.assertIn("港投公司", a1["extract"])
        self.assertLessEqual(len(a1["extract"]), EXTRACT + 2)
        self.assertTrue(a1["extract"].startswith("…") and a1["extract"].endswith("…"))
        self.assertNotIn(LONG[:300], json.dumps(d, ensure_ascii=False))
        # comment rows only on request
        code, d = self.get("q=港投公司&from=2026-10-01&to=2026-10-13&comments=1")
        self.assertEqual(d["total"], 4)
        self.assertTrue(any(p["comment"] for p in d["posts"]))
        # filters
        self.assertEqual(self.get("q=港投公司&from=2026-10-01&to=2026-10-13&type=Official")[1]["total"], 1)
        self.assertEqual(self.get("q=港投公司&from=2026-10-01&to=2026-10-13&site=明報&site=香港01")[1]["total"], 2)
        self.assertEqual(self.get("q=港投公司&from=2026-10-01&to=2026-10-13&site=明報,香港01")[1]["total"], 2)
        self.assertEqual(self.get("q=港投公司&from=2026-10-01&to=2026-10-13&title=1")[1]["total"], 2)
        self.assertEqual(self.get("q=港投公司&from=2025-01-01&to=2026-10-13")[1]["total"], 4)  # dates
        self.assertEqual(self.get("q=港投公司&from=2025-01-01&to=2026-10-13&sort=oldest")[1]["posts"][0]["url"], "https://s/a5")
        rel = self.get("q=港投公司 半導體&from=2025-01-01&to=2026-10-13&sort=relevance")[1]["posts"]
        self.assertEqual(rel[0]["url"], "https://s/a1")
        # paging
        p2 = self.get("q=港投公司&from=2025-01-01&to=2026-10-13&n=2&page=2")[1]
        self.assertEqual((p2["total"], len(p2["posts"])), (4, 2))
        # no query: everything in the period
        self.assertEqual(self.get("from=2026-10-12&to=2026-10-12")[1]["total"], 3)
        # errors
        self.assertEqual(self.get("q=(港投&from=2026-10-01")[0], 400)
        self.assertEqual(self.get("q=x&from=2026-1-1")[0], 400)
        self.assertEqual(self.get("q=x&sort=bad")[0], 400)

    def test_meta_and_export(self):
        m = edge("search/search.meta")
        news = next(t for t in m["types"] if t["type"] == "News")
        self.assertEqual(news["zh"], "新聞")
        self.assertIn("明報", [s["site"] for s in news["sites"]])
        out = tempfile.mkdtemp()
        try:
            name = edge("search/search.exportFile", parse_qs("q=港投公司&from=2025-01-01&to=2026-10-13"), out + "/s.xlsx")
            self.assertTrue(name.endswith(".xlsx"))
            from openpyxl import load_workbook
            ws = load_workbook(out + "/s.xlsx").active
        finally:
            shutil.rmtree(out)
        cells = [str(c.value or "") for row in ws.iter_rows() for c in row]
        self.assertIn("港投公司投資半導體", cells)
        self.assertIn("https://s/a1", cells)
        self.assertFalse(any(len(c) > 400 for c in cells))  # extracts only

    SPLIT = ["q=港投公司", "q=港投", "q=港投公司 施政報告", "q=trump", "q=trump 港投", "q=港投公司&type=News",
             "q=港投公司&type=News,Official", "q=港投公司&site=明報", "q=港投公司&region=HK", "q=港投公司&sort=oldest",
             "q=trump&sort=oldest", "q=港投公司&n=2&page=9", "q=港投公司&n=1&page=2", "q=冇呢個詞", "q=港投公司&comments=1",
             "q=港投公司&from=2026-10-12&to=2026-10-12"]

    def test_split_same_answer(self):
        """the split statement and the one query on the same searches: page, total, facets, export"""
        qs = [parse_qs(q if "from=" in q else q + "&from=2025-01-01&to=2026-10-13") for q in self.SPLIT]
        for on in (False, True):
            subs(on)
            self.assertTrue(all(edge("search/search.splits", q) for q in qs))
            split, one = edge("search/search.runMany", qs, True), edge("search/search.runMany", qs, False)
            for q, a, b in zip(self.SPLIT, split, one):
                self.assertEqual(a, b, (on, q))
            self.assertEqual({p["match"] for d in split for p in d["posts"]}, {"word", "substring"})  # both parts ran
            self.assertEqual(split[self.SPLIT.index("q=trump")]["total"], 2)
            self.assertEqual(split[self.SPLIT.index("q=冇呢個詞")]["total"], 0)
            for q in qs[:4] + qs[9:11]:
                self.assertEqual(edge("search/search.exportRows", q, True)["rows"],
                                 edge("search/search.exportRows", q, False)["rows"], q)
        # the one query still answers what the split can't: AND, NOT, relevance, titles only, no query
        self.assertFalse(any(edge("search/search.splits", parse_qs(q)) for q in
                             ("q=港投 AND 公司", "q=港投 NOT 年報", "q=港投&sort=relevance", "q=港投&title=1", "from=2026-10-12")))

    def test_posts_search_skips_substrings(self):
        """yyc-mcp's posts_search: the word matches first, the substring-only part only when they don't fill the page"""
        period = {"from": "2026-10-01", "to": "2026-10-13"}
        calls = [["posts_search", {"query": "trump", "page_size": 1, **period}],
                 ["posts_search", {"query": "trump", "page_size": 5, **period}],
                 ["posts_search", {"query": "trump", "page_size": 1, "page": 2, **period}]]
        r = subprocess.run(["npx", "tsx", "scripts/call.ts"], cwd=os.path.join(HERE, "mcp-edge"), input=json.dumps(calls),
                           capture_output=True, text=True, env=dict(os.environ), timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        full, short, rest = json.loads(r.stdout)
        # one word match fills a page of one: the substring part is skipped, total counts the word matches
        self.assertEqual((full["total"], full.get("total_exact")), (1, False), full)
        self.assertEqual([p["match"] for p in full["posts"]], ["word"])
        self.assertIn("longer words are not counted", full["more"])
        # a page the word matches don't fill: the substring-only rows too, and the exact total
        self.assertEqual((short["total"], short.get("total_exact")), (2, None), short)
        self.assertEqual([p["match"] for p in short["posts"]], ["word", "substring"])
        self.assertEqual(([p["match"] for p in rest["posts"]], rest.get("total_exact")), (["substring"], None), rest)


def subs(on):
    """kv search:subs: a Latin term's substring matches from post_subs (on) or the grams and text test (off)"""
    corpus.kv_set(query.SUBS_KEY, on)


if __name__ == "__main__":
    unittest.main(verbosity=2)
