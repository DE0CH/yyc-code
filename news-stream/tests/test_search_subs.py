#!/usr/bin/env python3
"""post_subs (corpus.SCHEMA subs_of and its triggers, query._sub; Deyao, 2026-10-27): a Latin term's substring matches
come from a GIN prefix lookup on the suffixes of each post's a-z/0-9 runs, with the same answer as the substring test
on hay. On a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_search_subs.py

1. For many Latin terms (inside longer words, hashtags, digits, case, full-width, runs longer than SUB_LEN, terms
   longer than SUB_LEN, one character) the posts query.to_sql finds are exactly the posts whose hay holds the term
   or whose words match it (strpos on hay, the old test): before kv search:subs and after, with a post that has no
   post_subs row and one whose subs is NULL; corpus.fill_subs() writes the missing row and sets the flag.
2. subs_of: the suffixes cut to SUB_LEN, NULL over a tsvector's size; the triggers follow inserts, hay changes and
   deletes, and leave other updates alone.
3. The plan of a Latin term uses posts_grams before the flag and post_subs_subs after.
4. The TypeScript port (web-app/src/server/search/query.ts) builds the same SQL and arguments in both states.
"""
import json, os, subprocess, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, corpus, query, searchtext  # noqa: E402,F401

LONG = "x" * 40 + "needle" + "y" * 40  # a run longer than SUB_LEN
POSTS = [
    ("plain", "Public housing supply", "The housing bureau said supply will rise."),
    ("inside", "#publichousing protest", "HousingAuthority reply; housings, rehousing2026"),
    ("caps", "TRUMP tariffs", "Trump's tariffs and trumpets"),
    ("fullwidth", "ＴＲＵＭＰ年報", ""),
    ("digits", "Budget 2026/27", "HK$12345678 and covid-19 cases"),
    ("chinese", "特朗普關稅", "房屋供應"),
    ("long", "token", LONG),
    ("mixed", "5G網絡housing測試", "iphone17pro發布"),
    ("comment_like", "", "lol trumpism"),
]
TERMS = ["housing", "using", "ousi", "trump", "trumps", "tariff", "2026", "26", "345", "covid", "19", "needle",
         "x" * 30 + "ne", "x" * 33, "h", "g", "5g", "iphone17", "17pro", "Trump", "ＴＲＵＭＰ", "housingauthority",
         "rehousing2026", "zzz", "y" * 32]


def found(q, only=None):
    where, params = query.to_sql(query.parse(q))
    sql = f"SELECT p.url FROM posts p WHERE {where}" + (f" AND {only}" if only else "")
    return {r["url"].rsplit("/", 1)[1] for r in corpus.db().execute(sql, params)}


def truth(q):
    """the old test: a word match or the term (normalised or as typed) a substring of hay"""
    w = searchtext.tsquery(searchtext.norm(q))
    rows = corpus.db().execute("SELECT url FROM posts p WHERE (%s::tsquery IS NOT NULL AND coalesce(p.words @@ %s::tsquery, FALSE))"
                               " OR strpos(p.hay, %s) > 0 OR strpos(p.hay, %s) > 0", (w, w, searchtext.norm(q), q))
    return {r["url"].rsplit("/", 1)[1] for r in rows}


def ready(on):
    """kv search:subs (query.subs_ready), and the 30 s cache forgotten"""
    corpus.kv_set(query.SUBS_KEY, on)
    query._ready["at"] = 0.0


class Subs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.ingest([{"url": f"https://s/{k}", "title": t, "text": x, "source": "test", "sid": "test_src",
                        "published": "2026-10-20T09:00+08:00"} for k, t, x in POSTS])

    def test_1_same_answer_as_the_substring_test(self):
        db = corpus.db()
        self.assertEqual(db.execute("SELECT count(*) AS n FROM posts p WHERE NOT EXISTS (SELECT 1 FROM post_subs s "
                                    "WHERE s.post_id = p.id)").fetchone()["n"], 0)  # the insert trigger
        ids = corpus.ids_for_urls(["https://s/inside", "https://s/caps"])
        for on in (False, True):  # before fill_subs has finished (grams narrow) and after (post_subs alone)
            ready(on)
            for t in TERMS:
                self.assertEqual(found(f'"{t}"'), truth(t), (on, t))
            # a post whose subs is NULL (a text over a tsvector's size), and before the flag one with no row yet
            if not on:
                db.execute("DELETE FROM post_subs WHERE post_id = %s", (ids["https://s/inside"],))
            db.execute("UPDATE post_subs SET subs = NULL WHERE post_id = %s", (ids["https://s/caps"],))
            for t in TERMS:
                self.assertEqual(found(f'"{t}"'), truth(t), (on, t, "subs NULL / no row"))
            self.assertEqual(corpus.fill_subs(pause=0), 0 if on else 1)
            self.assertIs(corpus.kv_get(query.SUBS_KEY), True)
            for t in TERMS:
                self.assertEqual(found(f'"{t}"'), truth(t), (on, t, "after fill_subs"))
            db.execute("UPDATE post_subs SET subs = subs_of(p.hay) FROM posts p WHERE p.id = post_id")

    def test_2_subs_of_and_triggers(self):
        db = corpus.db()
        one = lambda sql, *a: db.execute(sql, a).fetchone()["v"]  # noqa: E731
        self.assertEqual(one("SELECT subs_of('ab1 c-d' || chr(10) || 'ab1')::text AS v"), "'1' 'ab1' 'b1' 'c' 'd'")
        self.assertEqual(one("SELECT max(length(x)) AS v FROM unnest(tsvector_to_array(subs_of(%s))) x", LONG), searchtext.SUB_LEN)
        self.assertIsNone(one("SELECT subs_of(string_agg('w' || lpad(i::text, 7, '0'), ' ')) AS v FROM generate_series(1, 150000) i"))
        self.assertEqual(one("SELECT subs_of('') AS v"), "")
        pid = corpus.ids_for_urls(["https://s/plain"])["https://s/plain"]
        has = lambda t: one("SELECT subs @@ %s::tsquery AS v FROM post_subs WHERE post_id = %s", t + ":*", pid)  # noqa: E731
        self.assertTrue(has("housing"))
        db.execute(f"UPDATE posts SET {corpus.SEARCH_SET} WHERE id = %s", corpus.search_cols("Zebra crossing", "", None, "test") + (pid,))
        self.assertFalse(has("housing"))
        self.assertTrue(has("ebra"))
        db.execute("UPDATE posts SET engagement = '{}' WHERE id = %s", (pid,))  # no hay change: the row stays
        self.assertTrue(has("ebra"))
        db.execute(f"UPDATE posts SET {corpus.SEARCH_SET} WHERE id = %s",
                   corpus.search_cols("Public housing supply", "The housing bureau said supply will rise.", None, "test") + (pid,))
        tmp = corpus.ingest([{"url": "https://s/tmp", "title": "gone soon", "text": "", "source": "test", "sid": "test_src",
                              "published": "2026-10-20T09:00+08:00"}])[0]
        self.assertEqual(one("SELECT count(*) AS v FROM post_subs WHERE post_id = %s", tmp), 1)
        db.execute("DELETE FROM posts WHERE id = %s", (tmp,))
        self.assertEqual(one("SELECT count(*) AS v FROM post_subs WHERE post_id = %s", tmp), 0)

    def test_3_plan(self):
        for on, want, other in ((False, "posts_grams", "post_subs_subs"), (True, "post_subs_subs", "posts_grams")):
            ready(on)
            with corpus.db() as c:
                c.execute("SET LOCAL enable_seqscan = off")
                where, params = query.to_sql(query.parse("housing"))
                plan = "\n".join(r["QUERY PLAN"] for r in c.execute(f"EXPLAIN SELECT p.id FROM posts p WHERE {where}", params))
            self.assertIn(want, plan, (on, plan))
            self.assertNotIn(other, plan, (on, plan))

    def test_4_typescript_builds_the_same_sql(self):
        searchtext.publish_edge(force=True)
        qs = ["housing", "Trump 特朗普", '"public housing"', "housing AND NOT trump", "FindIn(author: trump)",
              "housing NEARBY/5 supply", "x" * 40, "2026", "TopicSearchOnly housing", "ＴＲＵＭＰ", "李家超"]
        for on in (False, True):
            ready(on)
            py = [list(query.to_sql(query.parse(q))) for q in qs]
            r = subprocess.run(["npx", "tsx", "scripts/call.ts", "--batch"], cwd=os.path.join(HERE, "web-app"),
                               input=json.dumps([["search/query.sqlFor", [q]] for q in qs]), capture_output=True, text=True,
                               env=dict(os.environ), timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            for q, a, b in zip(qs, py, json.loads(r.stdout)):
                self.assertEqual(a, b, (on, q))


if __name__ == "__main__":
    unittest.main()
