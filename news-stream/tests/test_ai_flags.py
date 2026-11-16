#!/usr/bin/env python3
"""The website's user-initiated AI features are behind feature flags, and with those flags off no model call can be
triggered from the web (Deyao, 2026-10-14). On a throwaway database (TEST_DATABASE_URL), the real flags.json.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_ai_flags.py

1. The shipped flags.json has `ai` (the dashboard's persona AI 分析 and AI 每日摘要) off on both sites.
   (The 設定 page's AI actions are gone with the page: the site is read-only; tests/test_readonly.py.)
2. Every web entry point that calls a model answers 404 with its flag off, on both sites, and the model is never
   called (ai.chat / ai.chat_stream would record it). The removed 設定 AI routes answer 404 too.
"""
import io, json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ADMIN_DEV", None)
os.environ.pop("FLAGD_HOST", None)  # the in-process provider on the checkout's flags.json
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, flags, web  # noqa: E402

CALLS = []


def no_model(*a, **k):
    CALLS.append((a[1] if len(a) > 1 else k.get("purpose")))
    raise AssertionError("a model was called")


ai.chat = no_model
ai.chat_stream = no_model
web.access_user = lambda h: "a@yyc.hk"  # an approved account: only the flag stands in the way
HOSTS = ("newswatch.yyc-internal.com", flags.BETA_HOST)
USER_AI = {"ai": ["/app/ai/persona", "GET /api/ai/estimate?kind=persona"]}
# the AI 助理 and the on-demand summaries were removed (Deyao, 2026-10-28)
REMOVED_AI = ["/app/admin/api/suggest", "/app/admin/api/themes", "/app/admin/api/report", "/app/ai/summary", "/app/agent",
              "/app/agent/start", "/app/agent/delete"]


def call(method, path, body=None, host=HOSTS[0]):
    raw = json.dumps(body or {}).encode()
    p, _, q = path.partition("?")
    env = {"REQUEST_METHOD": method, "PATH_INFO": p, "QUERY_STRING": q, "RAW_URI": path, "HTTP_HOST": host,
           "wsgi.input": io.BytesIO(raw), "CONTENT_LENGTH": str(len(raw)), "CONTENT_TYPE": "application/json"}
    got = {}
    data = b"".join(app.application(env, lambda s, h: got.update(status=int(s.split()[0]))))
    return got["status"], data


class T(unittest.TestCase):
    def test_flags_off_on_both_sites(self):
        for host in HOSTS:
            for f in USER_AI:
                with self.subTest(host=host, flag=f):
                    from openfeature.evaluation_context import EvaluationContext
                    d = flags.client().get_boolean_details(f, True, EvaluationContext(attributes={"host": host}))
                    self.assertIsNone(d.error_code)  # really evaluated off, not off because the source failed
                    self.assertIs(d.value, False)
                    self.assertIs(flags.public(host)[f], False)  # what edge_config.py publishes for the app

    def test_entry_points_refuse_without_a_model_call(self):
        for host in HOSTS:
            for f, paths in list(USER_AI.items()) + [("removed", REMOVED_AI)]:
                for path in paths:
                    method, _, path = path.rpartition(" ")
                    with self.subTest(host=host, path=path):
                        code, _ = call(method or "POST", path, {"hint": "港投", "id": "x", "kind": "posts", "message": "hi"}, host)
                        self.assertEqual(code, 404)
        self.assertEqual(CALLS, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
