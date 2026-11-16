#!/usr/bin/env python3
"""The cluster's AI server (gunicorn app:application, web.py) is read-only and serves only the AI routes (Deyao,
2026-10-14: "no admin page, control plane is script from me manually"; the site's pages and reads are the edge app's,
web-app/).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_readonly.py

1. GET answers only /healthz, /app/login, /api/ai/estimate and /app/api/v1/*: every page and read the edge app
   serves (串流, /feed.json, the item page, 數據分析 and its /api reads and exports, 搜尋, 報告 and its files, the
   monitor, /app/me, /static, the icons) answers 404 here, for an approved account too, on the beta
   host where every flag is on.
2. Every path the server code names (each "/…" literal in app.py and web.py), the edge app's paths and the removed
   admin and edit paths: a POST answers 404 unless it is one of the AI routes (the persona button, the AI data API) or the proof view's flag (/app/proof/flag), whether the caller is an approved account or not.
3. PUT, PATCH, DELETE and OPTIONS answer 405 on every path.
4. The edge app's front end sends a POST/PUT/PATCH/DELETE only to the AI routes, and offers no 設定 or removed page.
Nothing here writes to the database: every request it sends is refused before any handler runs.
"""
import glob, io, json, os, re, sys, unittest, unittest.mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ.pop("ADMIN_DEV", None)
os.chdir(HERE)
import app, web  # noqa: E402

USER = {"who": None}
web.access_user = lambda h: USER["who"]

# the AI routes: asking a model or running the data API's tools, with a JSON body
KEPT_POST = (# the proof view's 標記為有誤 (Deyao, 2026-10-20: check each stored entry, flag the wrong ones): one row in
             # proof_flags per flag, approved accounts only (tests/test_proof.py); it edits no post, tag or setting
             "/app/proof/flag")
KEPT_POST_PREFIX = ("/app/ai/", "/app/api/v1/")  # the persona note; the AI data API (read tools)
# the Worker's own calls to its Durable Object (server.ts: POST https://hub/build tells the LiveHub this build's id so
# open pages update, web-app README "Updates reach open pages"): internal, no route of the site
INTERNAL = ("https://hub/",)
REMOVED = ["/app/admin", "/app/admin/", "/app/admin/social", "/app/admin/api/profile", "/app/admin/api/delete",
           "/app/admin/api/settings", "/app/admin/api/budget_reset", "/app/admin/api/suggest", "/app/admin/api/report",
           "/app/admin/api/themes", "/app/admin/api/social_run", "/app/admin/api/profiles", "/app/admin/api/preview",
           "/app/admin/api/websites", "/app/admin/api/social", "/app/admin/api/budgets", "/app/item/tags", "/settings"]
# what the edge app serves (edge/worker.js APP): none of it is the AI server's
APP_PATHS = ["/", "/?p=CEHK", "/feed.json", "/feed.json?date=2026-10-01", "/item/1", "/item?url=https://example.com/",
             "/post/1", "/dashboard", "/dashboard/", "/search", "/reports", "/reports/",
             "/reports/2026-10-01/CEHK/report.pdf", "/api/profiles", "/api/posts?p=CEHK",
             "/api/hot_topics?p=CEHK", "/api/topic?p=CEHK&id=1", "/api/themes?p=CEHK", "/api/persona?p=CEHK&author=x",
             "/api/item?id=1", "/api/flags", "/api/search?q=x", "/api/search/meta", "/api/export/posts.xlsx?p=CEHK",
             "/api/export/search.xlsx?q=x", "/api/reports", "/api/ai/daily?p=CEHK", "/api/ai/context?id=x",
             "/api/ai/spend", "/app/me", "/app/item/text?id=1", "/app/item/ai?id=1", "/app/stream/scores",
             "/app/monitor", "/app/monitor/data", "/app/proof", "/app/proof?sid=x", "/app/proof/data?catalogue=1",
             "/app/proof/entry?id=1", "/app/monitor/flags", "/static/shell.css", "/favicon.svg", "/favicon.ico",
             "/apple-touch-icon.png", "/version"]


def kept(path):
    return path in KEPT_POST or path.startswith(KEPT_POST_PREFIX)


def call(method, path, body=b"{}", who=None, host="yyc-beta.deyaochen.com"):
    USER["who"] = who
    p, _, q = path.partition("?")
    env = {"REQUEST_METHOD": method, "PATH_INFO": p, "QUERY_STRING": q, "RAW_URI": path, "HTTP_HOST": host,
           "wsgi.input": io.BytesIO(body), "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": "application/json"}
    got = {}
    b"".join(app.application(env, lambda s, h: got.update(status=int(s.split()[0]))))
    return got["status"]


def server_paths():
    """every path literal in the server code, e.g. "/app/item/text", "/api/", "/reports" """
    out = set(REMOVED) | {p.partition("?")[0] for p in APP_PATHS}
    for f in ("app.py", "web.py"):
        with open(os.path.join(HERE, f), encoding="utf-8") as fh:
            src = fh.read()
        for m in re.finditer(r'''["'](/[A-Za-z0-9_./-]*)''', src):
            p = m.group(1)
            if p.startswith("//") or "." in p.split("/")[-1] and not p.endswith((".json", ".xlsx")):
                continue
            out.add(p)
            out.add(p.rstrip("/") + "/x")  # anything under a prefix route
    return sorted(out)


def flags_file():
    import flags
    with open(flags.FILE) as fh:
        return json.load(fh)


class Routes(unittest.TestCase):
    def test_post_only_to_kept_routes(self):
        paths = server_paths()
        self.assertGreater(len(paths), 50)
        for who in (None, "a@yyc.hk"):
            for p in paths:
                if kept(p):
                    continue
                with self.subTest(path=p, who=who):
                    self.assertEqual(call("POST", p, b'{"id": "x", "name": "x", "query": "x"}', who=who), 404)

    def test_other_methods_refused(self):
        for p in server_paths():
            for m in ("PUT", "PATCH", "DELETE", "OPTIONS"):
                with self.subTest(path=p, method=m):
                    self.assertEqual(call(m, p, who="a@yyc.hk"), 405)

    def test_removed_pages_are_gone(self):
        for p in REMOVED:
            with self.subTest(path=p):
                self.assertEqual(call("GET", p, who="a@yyc.hk"), 404)

    def test_get_only_the_ai_routes(self):
        for who in (None, "a@yyc.hk"):
            for p in APP_PATHS:
                with self.subTest(path=p, who=who):
                    self.assertEqual(call("GET", p, b"", who=who), 404)
        self.assertEqual(call("GET", "/healthz", b""), 200)
        self.assertEqual(call("GET", "/app/login?next=/dashboard", b"", who="a@yyc.hk"), 302)
        self.assertEqual(call("GET", "/app/api/v1/openapi.json", b""), 200)
        self.assertEqual(call("GET", "/app/api/v1/profiles_list", b""), 403)  # approved accounts only
        self.assertEqual(call("GET", "/api/ai/estimate?kind=persona", b""), 404)  # the `ai` flag is off on both sites
        with unittest.mock.patch.object(web.flags, "on", lambda flag, host: True):  # on: the estimate answers
            self.assertEqual(call("GET", "/api/ai/estimate?kind=nothing", b""), 400)


class FrontEnd(unittest.TestCase):
    SOURCES = glob.glob(os.path.join(HERE, "web-app", "src", "**", "*.ts*"), recursive=True)

    def test_no_writes_but_kept(self):
        self.assertGreater(len(self.SOURCES), 10)
        for f in self.SOURCES:
            with open(f, encoding="utf-8") as fh:
                src = fh.read()
            for m in re.finditer(r'''fetch\(\s*[`"']([^`"'?$]*)[^)]*?method:\s*["'](POST|PUT|PATCH|DELETE)''', src):
                with self.subTest(file=os.path.relpath(f, HERE), url=m.group(1)):
                    self.assertTrue(kept(m.group(1)) or m.group(1).startswith(INTERNAL), m.group(0)[:120])
            self.assertNotRegex(src, r'method\s*=\s*["\']?post', os.path.relpath(f, HERE))  # no <form method=post>
            for bad in ("/app/admin", "/app/item/tags", ">修正<", "設定\", \"/"):  # (已人手修正 shows a stored flag)
                with self.subTest(file=os.path.relpath(f, HERE), text=bad):
                    self.assertNotIn(bad, src)
            with self.subTest(file=os.path.relpath(f, HERE), text="儲存"):  # a save button; 已儲存的回應 (WiseSearch's
                self.assertNotRegex(src, r"(?<!已)儲存")                     # saved responses, read-only) is a list

    def test_flags_have_no_settings_section(self):
        import flags
        self.assertNotIn("settings", flags.FLAGS)
        self.assertNotIn("settings", flags_file()["flags"])


if __name__ == "__main__":
    unittest.main()
