#!/usr/bin/env python3
"""chrome_state.py: a Chrome profile's cookies live in Postgres (kv "chrome:<name>"), so a browser job runs on any node
with a fresh folder (Deyao, 2026-11-08: state in the application, no Kubernetes volume).

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_chrome_state.py
"""
import os, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import chrome_state, corpus  # noqa: E402

LATER = time.time() + 86400


def cookie(name, expires=LATER):
    return {"name": name, "value": "v", "domain": ".example.com", "path": "/", "expires": expires,
            "httpOnly": False, "secure": True, "sameSite": "Lax"}


class Ctx:
    def __init__(self, folder, jar):
        self.folder, self.jar, self.closed = folder, jar, False

    def cookies(self):
        return list(self.jar)

    def add_cookies(self, cs):
        self.jar.extend(cs)

    def route(self, *a, **k):  # nomedia.block
        pass

    def close(self):
        self.closed = True


class Playwright:
    """p.chromium.launch_persistent_context(folder) → a context with an empty cookie jar, as Chrome on a fresh folder"""
    def __init__(self):
        self.chromium, self.launched = self, []

    def launch_persistent_context(self, folder, **kw):
        ctx = Ctx(folder, [])
        self.launched.append(ctx)
        return ctx


class ChromeState(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'chrome:%%'")

    def test_cookies_survive_a_run_on_a_fresh_folder(self):
        p = Playwright()
        ctx = chrome_state.launch(p, "browser-profile", channel="chrome")
        self.assertEqual(ctx.cookies(), [])
        ctx.jar += [cookie("cf_clearance"), cookie("gone", time.time() - 10), cookie("session", -1)]
        folder = ctx.folder
        chrome_state.close(ctx)
        self.assertTrue(ctx.closed)
        self.assertFalse(os.path.exists(folder), "the temporary folder stays behind")
        self.assertEqual([c["name"] for c in corpus.kv_get("chrome:browser-profile")], ["cf_clearance"],
                         "expired and session cookies are dropped, as Chrome drops them on a restart")
        again = chrome_state.launch(p, "browser-profile", channel="chrome")
        self.assertNotEqual(again.folder, folder)
        self.assertEqual([c["name"] for c in again.cookies()], ["cf_clearance"])
        chrome_state.close(again)

    def test_a_reader_never_writes(self):
        corpus.kv_set("chrome:browser-profile", [cookie("cf_clearance")])
        ctx = chrome_state.launch(Playwright(), "browser-profile", save=False)
        ctx.jar.append(cookie("fulltext-only"))
        chrome_state.close(ctx)
        self.assertEqual([c["name"] for c in corpus.kv_get("chrome:browser-profile")], ["cf_clearance"])

    def test_a_crashed_browser_keeps_the_saved_cookies(self):
        corpus.kv_set("chrome:browser-profile-sites", [cookie("kept")])
        ctx = chrome_state.launch(Playwright(), "browser-profile-sites")

        def dead():
            raise RuntimeError("Target page, context or browser has been closed")
        ctx.cookies = dead
        chrome_state.close(ctx)
        self.assertTrue(ctx.closed)
        self.assertEqual([c["name"] for c in corpus.kv_get("chrome:browser-profile-sites")], ["kept"])


if __name__ == "__main__":
    unittest.main()
