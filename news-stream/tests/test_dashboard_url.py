#!/usr/bin/env python3
"""dashboard_url: every post, topic, author, theme, source and report the data APIs return links to its details page on
the monitoring site (Deyao, 2026-10-21: "the news monitor api/mcp should include link to the details page on the
dashboard"), built by one helper per language: dashboard_url.py (the AI server's /app/api/v1) and
web-app/src/lib/dashboard_url.ts (yyc-mcp, mcp-edge/src/tools.ts).

    python3 tests/test_dashboard_url.py                                          # the helpers (no database)
    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_dashboard_url.py # also the tools on seeded posts

1. The Python and TypeScript helpers give the same link for the same item (TS run by Node's type stripping).
2. The hosts follow flags.json: a section on only on the beta site (報告, 搜尋) links there, the rest to the main site.
3. No tool builds a site URL by hand: the site's hosts appear only in the helpers (and the old `item`/`urls` fields).
4. With a database: Python's posts_search, posts_get, topics_hot, authors_top, sources_top, stats_overview, trend,
   profiles_list and yyc-mcp's posts_search, post_get, analysis_posts, analysis_hot_topics, analysis_topic,
   analysis_authors, analysis_sources, analysis_themes, stream_items, streams_list carry dashboard_url; the
   descriptions tell the model to cite it.
5. yyc-mcp: a post is the same shape in every answer (Deyao, 2026-10-27: one abstraction for a post), whichever tool
   lists it (posts_search, analysis_posts, analysis_topic, analysis_person, stream_items, post_get's same_article): the
   same keys, never another field, each with its own page; and every listed thing with a page links it (a topic's
   authors and outlets, a person's outlets, terms, trend periods, the days a post was filed).
"""
import json, os, re, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import dashboard_url as dl  # noqa: E402

TS = os.path.join(HERE, "web-app", "src", "lib", "dashboard_url.ts")
MCP = os.path.join(HERE, "mcp-edge")

# yyc-mcp's post (mcp-edge/src/tools.ts post()): the post's own fields, then what a query says about it
POST_KEYS = {"id", "title", "url", "dashboard_url", "item", "source", "board", "author", "type", "region", "platform", "time",
             "date_only", "extract", "engagement", "score", "reason", "match", "sentiment", "labels", "theme", "stream",
             "category", "comment"}

# [ts function, args, python function, args, kwargs]
CASES = [
    ("postUrl", [123], dl.post_url, [123], {}),
    ("postByUrl", ["https://news.test/a?b=1&c=中 d~e*f(g)!'", "CEHK"], dl.post_by_url, ["https://news.test/a?b=1&c=中 d~e*f(g)!'", "CEHK"], {}),
    ("streamUrl", ["CEHK", "2026-10-21"], dl.stream_url, ["CEHK", "2026-10-21"], {}),
    ("streamUrl", [], dl.stream_url, [], {}),
    ("dashboardUrl", [{"p": "CEHK"}], dl.dashboard_url, [], {"p": "CEHK"}),
    ("topicUrl", [7, {"p": "CEHK", "from": "2026-10-01", "to": "2026-10-07", "sent": "negative", "q": "李家超 OR \"施政報告\""}],
     dl.topic_url, [7], {"p": "CEHK", "from_": "2026-10-01", "to": "2026-10-07", "sent": "negative", "q": "李家超 OR \"施政報告\""}),
    ("personUrl", ["Mr A+B / 陳", {"p": "FSHK", "from": "2026-07-01", "to": "2026-10-01"}],
     dl.person_url, ["Mr A+B / 陳"], {"p": "FSHK", "from_": "2026-07-01", "to": "2026-10-01"}),
    ("themeUrl", ["政策 & 民生", {"p": "HKIC"}], dl.theme_url, ["政策 & 民生"], {"p": "HKIC"}),
    ("sourceUrl", ["LIHKG討論區", {"p": "HKIC", "label": "crisis"}], dl.source_url, ["LIHKG討論區"], {"p": "HKIC", "label": "crisis"}),
    ("reportUrl", ["/reports/2026-10-21/CEHK/format1.html"], dl.report_url, ["/reports/2026-10-21/CEHK/format1.html"], {}),
    ("reportUrl", ["https://newswatch.yyc-internal.com/reports/2026-10-21/CEHK/report.pdf"], dl.report_url,
     ["https://newswatch.yyc-internal.com/reports/2026-10-21/CEHK/report.pdf"], {}),
    ("reportUrl", [], dl.report_url, [], {}),
    ("searchUrl", [{"q": "\"房屋\" AND FindIn(author: \"Mr A+B / 陳\")", "from": "2025-07-01", "to": "2026-10-01", "comments": 1}],
     dl.search_url, [], {"q": "\"房屋\" AND FindIn(author: \"Mr A+B / 陳\")", "from_": "2025-07-01", "to": "2026-10-01", "comments": 1}),
]


def ts_calls(calls):
    """[[fn, args]] → the TS helper's answers (Node strips the types; the helper has no imports)"""
    src = (f"import * as dl from {json.dumps(TS)};\n"
           "const calls = JSON.parse(process.argv[process.argv.length - 1]);\n"
           "process.stdout.write(JSON.stringify(calls.map(([f, a]) => dl[f](...a))));\n")
    with tempfile.NamedTemporaryFile("w", suffix=".mts", delete=False) as f:
        f.write(src)
    try:
        r = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", f.name, json.dumps(calls)],
                           capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(f.name)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout)


class Helpers(unittest.TestCase):
    def test_python_and_typescript_agree(self):
        got = ts_calls([[c[0], c[1]] for c in CASES])
        for (name, _, fn, args, kw), t in zip(CASES, got):
            self.assertEqual(fn(*args, **kw), t, name)

    def test_links(self):
        self.assertEqual(dl.post_url(5), "https://newswatch.yyc-internal.com/item/5")
        self.assertEqual(dl.topic_url(3, p="CEHK", from_="2026-10-01", to="2026-10-07"),
                         "https://newswatch.yyc-internal.com/dashboard?v=hot&p=CEHK&from=2026-10-01&to=2026-10-07&tid=3")
        self.assertEqual(dl.person_url("陳 大文", p="CEHK"),
                         "https://newswatch.yyc-internal.com/dashboard?v=persona&p=CEHK&a=%E9%99%B3+%E5%A4%A7%E6%96%87")
        self.assertEqual(dl.report_url("/reports/2026-10-21/CEHK/a.pdf"), "https://yyc-beta.deyaochen.com/reports/2026-10-21/CEHK/a.pdf")
        with self.assertRaises(ValueError):
            dl.dashboard_url(nope=1)

    def test_param_order_is_the_dashboards(self):
        page = open(os.path.join(HERE, "web-app", "src", "routes", "-DashboardPage.tsx"), encoding="utf-8").read()
        keys = json.loads("[" + re.search(r"export const KEYS = \[([^\]]*)\]", page).group(1) + "]")
        self.assertEqual(list(dl.DASH_KEYS), keys)
        ts = open(TS, encoding="utf-8").read()
        self.assertEqual(json.loads("[" + re.search(r"const DASH_KEYS = \[([^\]]*)\]", ts).group(1) + "]"), keys)
        page = open(os.path.join(HERE, "web-app", "src", "routes", "search.tsx"), encoding="utf-8").read()
        keys = json.loads("[" + re.search(r"export const KEYS = \[([^\]]*)\]", page).group(1) + "]")
        self.assertEqual(list(dl.SEARCH_KEYS), keys)
        self.assertEqual(json.loads("[" + re.search(r"const SEARCH_KEYS = \[([^\]]*)\]", ts).group(1) + "]"), keys)

    def test_hosts_follow_the_flags(self):
        """a section whose flag is on only for the beta site links there; 串流, item, 數據分析 have no flag"""
        f = json.load(open(os.path.join(HERE, "deploy", "apps", "flags", "flags.json")))["flags"]
        beta_only = sorted(k for k, v in f.items() if v.get("defaultVariant") == "off"
                           and dl.BETA.split("//")[1] in json.dumps(v.get("targeting") or {}))
        self.assertEqual(beta_only, sorted(dl.BETA_ONLY))
        ts = open(TS, encoding="utf-8").read()
        self.assertEqual(sorted(json.loads("[" + re.search(r"BETA_ONLY = \[([^\]]*)\]", ts).group(1) + "]")), beta_only)
        gates = open(os.path.join(HERE, "web-app", "src", "server", "gates.ts"), encoding="utf-8").read()
        for path in ("/item", "/dashboard"):
            self.assertNotIn(f'["{path}"', gates, f"{path} is behind a flag now: dashboard_url's host needs a look")

    def test_no_hand_built_site_urls(self):
        """the tools take every site link from the helper (the old `item` / `urls` fields keep their SITE)"""
        for path in ("mcp-edge/src/tools.ts", "tools/data.py", "tools/live.py"):
            src = open(os.path.join(HERE, path), encoding="utf-8").read()
            self.assertEqual(re.findall(r"yyc(?:-beta)?\.deyaochen\.com/(?:item|dashboard|reports)", src), [], path)
            self.assertNotRegex(src, r"\?v=(hot|persona|posts)", path)


DB = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class Tools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["DATABASE_URL"] = DB
        os.environ["DATA_DIR"] = tempfile.mkdtemp()
        os.environ.pop("FLAGD_HOST", None)
        import psycopg
        with psycopg.connect(DB, autocommit=True) as c:
            c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        os.chdir(HERE)
        sys.path.insert(0, os.path.join(HERE, "tests"))
        global app, corpus, tools
        import ai, app, corpus, edge_config, insights, searchtext, sources, streams, stage2, tools  # noqa: F401
        corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)

        def fake_jsonc(prompt, purpose, model=None, max_tokens=4000):
            if purpose == "scoring":
                posts = re.findall(r"(?m)^(\d+)\. \[[^\]]*\] (.*)$", prompt.split("Posts:\n", 1)[1])
                return {"posts": [{"i": int(i), "sentiment": "negative", "labels": ["crisis"],
                                   "streams": [{"id": app.STREAMS[0]["id"], "score": 90, "reason": "測試"}]}
                                  for i, t in posts]}
            n = len(re.findall(r"(?m)^\d+\. \[", prompt))
            return {"tags": [{"i": i, "relevant": True, "sentiment": "negative", "labels": ["crisis"]} for i in range(n)]}
        ai.jsonc = stage2.agree(fake_jsonc, None)
        streams.settings()
        streams.WAIT_TEXT = streams.timedelta(0)
        now = app.datetime.now(app.HKT)
        got = [{"sid": "rthk", "source": "RTHK", "title": f"李家超 測試新聞 {i}", "author": "記者甲",
                "url": f"https://news.test/{i}", "published": now, "text": "內文"} for i in range(3)]
        sources.fetch_all = lambda extra: (list(got), {"rthk": {"ok": True, "items": len(got), "error": None}})
        sources.discuss_search = lambda qs: []
        app.after_poll = lambda g, f: insights.after_poll(g, f)
        app.threading = type("T", (), {"Thread": lambda target, args, daemon: type("X", (), {"start": lambda s: target(*args)})()})
        app.load_state()
        app.poll_once()
        streams.run_once(app.STREAMS)
        edge_config.publish()
        searchtext.publish_edge()  # analysis_hot_topics norms a topic's title with the search dictionary
        cls.stream = app.STREAMS[0]["id"]
        cls.pid = corpus.ids_for_urls(["https://news.test/0"])["https://news.test/0"]

    def py(self, name, args):
        return tools.call(name, args, {"who": "test@yyc.hk", "tables": []})

    def test_python_tools(self):
        s, pid = self.stream, self.pid
        r = self.py("posts_search", {"profile": s})
        self.assertTrue(r["posts"])
        for p in r["posts"]:
            self.assertEqual(p["dashboard_url"], dl.post_url(p["id"]))
            self.assertEqual(p["item"], f"/item/{p['id']}")  # unchanged
        r = self.py("posts_get", {"id": pid})
        self.assertEqual((r["dashboard_url"], r["item"]), (dl.post_url(pid), f"/item/{pid}"))
        r = self.py("topics_hot", {"profile": s})
        self.assertTrue(r["topics"])
        self.assertTrue(all(t["dashboard_url"].startswith(dl.MAIN + f"/dashboard?v=hot&p={s}&from=") and "&tid=" in t["dashboard_url"]
                            for t in r["topics"]), r["topics"])
        r = self.py("authors_top", {"profile": s})
        self.assertTrue(any(a.get("dashboard_url", "").startswith(dl.MAIN + f"/dashboard?v=persona&p={s}&") for a in r["authors"]), r)
        r = self.py("sources_top", {"profile": s})
        self.assertEqual(r["sources"][0]["dashboard_url"], dl.source_url("RTHK", p=s, from_=r["period"]["from"], to=r["period"]["to"]))
        r = self.py("stats_overview", {"profile": s})
        self.assertEqual(r["dashboard_url"], dl.dashboard_url(p=s, from_=r["period"]["from"], to=r["period"]["to"]))
        r = self.py("trend", {"profile": s, "bucket": "week"})
        self.assertIn("v=trend", r["dashboard_url"])
        self.assertIn("bucket=week", r["dashboard_url"])
        r = self.py("profiles_list", {})
        self.assertEqual(next(p for p in r["profiles"] if p["id"] == s)["dashboard_url"], dl.dashboard_url(p=s))
        for t in tools.tools():
            if t.name in ("posts_search", "posts_get", "topics_hot", "authors_top", "sources_top"):
                self.assertIn("dashboard_url", t.description, t.name)

    def test_mcp_edge_tools(self):
        for d in (MCP, os.path.join(HERE, "web-app")):  # the tools' modules import postgres from web-app's
            if not os.path.isdir(os.path.join(d, "node_modules")):
                self.skipTest(f"{d}/node_modules is not installed (npm ci)")
        s, pid = self.stream, self.pid
        calls = [["posts_search", {"stream": s, "query": "李家超"}], ["post_get", {"id": pid}],
                 ["analysis_posts", {"stream": s}], ["analysis_hot_topics", {"stream": s}],
                 ["analysis_authors", {"stream": s}], ["analysis_sources", {"stream": s}], ["analysis_themes", {"stream": s}],
                 ["stream_items", {"stream": s}], ["streams_list", {}], ["analysis_trend", {"stream": s, "bucket": "week"}],
                 ["analysis_terms", {"stream": s}], ["analysis_overview", {"stream": s}]]
        url = DB if not DB.startswith("postgresql:///") else "postgres://claude:dev@127.0.0.1:5432/" + DB.rsplit("/", 1)[1]
        r = subprocess.run(["npx", "tsx", "scripts/call.ts"], cwd=MCP, input=json.dumps(calls), capture_output=True,
                           text=True, env=dict(os.environ, DATABASE_URL=url), timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        search, post, aposts, hot, authors, srcs, themes, items, slist, trend, terms, overview = json.loads(r.stdout)
        for x in (search, post, aposts, hot, authors, srcs, themes, items, slist, trend, terms, overview):
            self.assertNotIn("error", x, x)
        self.assertTrue(search["posts"])
        for p in search["posts"]:
            self.assertEqual(p["dashboard_url"], dl.post_url(p["id"]))
            self.assertEqual(p["item"], f"https://newswatch.yyc-internal.com/item/{p['id']}")  # unchanged
        self.assertEqual(post["dashboard_url"], dl.post_url(pid))
        self.assertTrue(aposts["posts"] and all(p["dashboard_url"] == dl.post_url(p["id"]) for p in aposts["posts"]))
        self.assertEqual(aposts["dashboard_url"], dl.dashboard_url(v="posts", p=s, from_=aposts["from"], to=aposts["to"]))
        self.assertTrue(hot["topics"])
        t = hot["topics"][0]
        self.assertEqual(t["dashboard_url"], dl.topic_url(t["id"], p=s, from_=aposts["from"], to=aposts["to"]))
        self.assertTrue(any(a.get("dashboard_url", "").startswith(dl.MAIN + "/dashboard?v=persona&") for a in authors["authors"]), authors)
        self.assertEqual(srcs["sources"][0]["dashboard_url"], dl.source_url(srcs["sources"][0]["website"], p=s, from_=aposts["from"], to=aposts["to"]))
        self.assertEqual(themes["dashboard_url"], dl.dashboard_url(v="themes", p=s, from_=aposts["from"], to=aposts["to"]))
        self.assertEqual(items["dashboard_url"], dl.stream_url(s, items["day"]))
        for it in items["items"]:
            self.assertEqual(it["dashboard_url"], dl.post_url(it["id"]))
        # one shape for a post, whichever tool lists it
        for name, rows in (("posts_search", search["posts"]), ("analysis_posts", aposts["posts"]),
                           ("stream_items", items["items"]), ("same_article", post.get("same_article", []))):
            for p in rows:
                self.assertLessEqual(set(p), POST_KEYS, (name, set(p) - POST_KEYS))
                self.assertTrue({"id", "dashboard_url", "item"} <= set(p), (name, p))
                self.assertEqual(p["dashboard_url"], dl.post_url(p["id"]), name)
        self.assertEqual(hot["dashboard_url"], dl.dashboard_url(v="hot", p=s, from_=aposts["from"], to=aposts["to"]))
        # a hot term links to the posts holding that exact term: quoted, as the search box takes an exact phrase
        self.assertTrue(terms["terms"], terms)
        for x in terms["terms"]:
            self.assertEqual(x["dashboard_url"], dl.dashboard_url(v="posts", p=s, from_=aposts["from"], to=aposts["to"],
                                                                  q=f'"{x["term"]}"'), x)
        self.assertTrue(all("dashboard_url" in x for x in trend["trend"]))
        self.assertTrue(all("dashboard_url" in x for x in overview["trend"]))
        for f in post.get("filed_in", []):
            self.assertEqual(f["dashboard_url"], dl.stream_url(f["stream"], f["day"]))
        self.assertEqual(next(x for x in slist["streams"] if x["id"] == s)["dashboard_url"], dl.stream_url(s))
        self.assertIn("bucket=week", trend["dashboard_url"])
        # the topic's own page, and its posts
        r = subprocess.run(["npx", "tsx", "scripts/call.ts"], cwd=MCP, input=json.dumps([["analysis_topic", {"stream": s, "id": t["id"]}]]),
                           capture_output=True, text=True, env=dict(os.environ, DATABASE_URL=url), timeout=300)
        topic, = json.loads(r.stdout)
        self.assertEqual(topic["dashboard_url"], t["dashboard_url"])
        self.assertTrue(all(p["dashboard_url"] == dl.post_url(p["id"]) for p in topic["posts"]))
        self.assertTrue(all(set(p) <= POST_KEYS for p in topic["posts"]), [set(p) - POST_KEYS for p in topic["posts"]][:1])
        self.assertTrue(all("dashboard_url" in a for a in topic.get("top_authors", []) if a.get("author")))
        self.assertTrue(all("dashboard_url" in x for x in topic.get("top_sources", []) if x.get("website")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
