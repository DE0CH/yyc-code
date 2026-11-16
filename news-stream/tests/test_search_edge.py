#!/usr/bin/env python3
"""The edge app's query dictionary (searchtext.publish_edge), on a throwaway database (TEST_DATABASE_URL; its tables are
dropped and made again). The Worker (web-app/src/server/search/) cuts and normalises query terms with what this writes.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_search_edge.py

1. publish_edge() writes table search_words (every word a query block can contain, lp = math.log(frequency)), kv
   search:opencc (the OpenCC configs and dictionaries) and kv search:edge (stamp, logtotal, Unicode tables); a second
   call with nothing changed writes nothing.
2. A dictionary change (a search_dict.txt word, a stream's query) reaches the edge by itself: the next publish has a new
   stamp, the Worker's tokens() cut the new word whole, and only the previous stamp's rows are kept beside the new ones.
3. The Worker's norm / variants / tokens / tsquery equal Python's on a sample of terms (tests/edge_parity.py searchtext
   runs thousands).
"""
import json, math, os, subprocess, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, searchtext  # noqa: E402

APP = os.path.join(HERE, "web-app")


def edge(fn, *args):
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql:///"):
        url = "postgres://claude:dev@127.0.0.1:5432/" + url.rsplit("/", 1)[1]
    r = subprocess.run(["npx", "tsx", "scripts/call.ts", fn, json.dumps(list(args))], cwd=APP, capture_output=True,
                       text=True, env=dict(os.environ, DATABASE_URL=url), timeout=600)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout)


def py(terms):
    out = []
    for t in terms:
        n = searchtext.norm(t)
        out.append({"term": t, "norm": n, "variants": searchtext.variants(t), "tokens": searchtext.tokens(n),
                    "tsquery": searchtext.tsquery(n), "tsquery_a": searchtext.tsquery(n, title_only=True)})
    return out


TERMS = ["選舉委員會", "李家超施政報告", "港投公司", "新資本投資者入境計劃", "東涌綫", "将军澳线", "佢哋已經做咗",
         "HSBC 滙豐", "Ｈｏｎｇ Ｋｏｎｇ", "iPhone17", "C++", "#港投", "裡面", "裏面", "COVID-19", "立法會選舉2025"]


class PublishEdge(unittest.TestCase):
    def test_publish_and_dictionary_change(self):
        corpus.pool()
        stamp = searchtext.publish_edge()
        meta = corpus.kv_get("search:edge")
        self.assertEqual(meta["stamp"], stamp)
        self.assertEqual(meta["version"], searchtext.VERSION)
        self.assertEqual(meta["logtotal"], math.log(searchtext.tokenizer().total))
        oc = corpus.kv_get("search:opencc")
        self.assertEqual(set(oc["configs"]), {"hk2s", "s2hk", "s2t"})
        self.assertIn("TSCharacters.ocd2", oc["dicts"])
        db = corpus.db()
        n = db.execute("SELECT count(*) FROM search_words WHERE v = %s", (stamp,)).fetchone()[0]
        self.assertGreater(n, 300000)
        freq = searchtext.tokenizer().FREQ
        for w, lp in db.execute("SELECT w, lp FROM search_words WHERE v = %s AND w = ANY(%s)", (stamp, ["香港", "选举", "港投"])):
            self.assertEqual(lp, math.log(freq[w]))  # bit for bit: the route sums on the Worker are Python's
        self.assertEqual(searchtext.publish_edge(), stamp)  # nothing changed: nothing written
        self.assertEqual(edge("search/searchtext.parity", TERMS), json.loads(json.dumps(py(TERMS))))

        # a dictionary edit: a new word (as search_dict.txt or a stream's query adds one) → a new stamp, published by itself
        word = "甲乙丙丁戊己庚"
        self.assertNotEqual(edge("search/searchtext.parity", [word])[0]["tokens"], [word])
        searchtext.tokenizer().add_word(word, 50000)
        old_version = searchtext.VERSION
        searchtext.VERSION = old_version + "x"
        try:
            stamp2 = searchtext.publish_edge()
        finally:
            searchtext.VERSION = old_version
        self.assertNotEqual(stamp2, stamp)
        self.assertEqual(corpus.kv_get("search:edge")["prev"], stamp)
        got = edge("search/searchtext.parity", [word, "香港" + word])
        self.assertEqual(got[0]["tokens"], [word])
        self.assertEqual(got, json.loads(json.dumps(py([word, "香港" + word]))))
        vs = {r[0] for r in db.execute("SELECT DISTINCT v FROM search_words")}
        self.assertEqual(vs, {stamp, stamp2})  # the previous stamp kept for requests in flight, older ones gone


if __name__ == "__main__":
    unittest.main()
