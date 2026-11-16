#!/usr/bin/env python3
"""No budget-shaped decisions (Deyao, 2026-10-14: "budget is a safety stop, not what we should do in view of our
budget"), on a throwaway database (TEST_DATABASE_URL), no network:

1. OpenRouter refusing for the key's limit or the account's credit (HTTP 402, or a 403 that names the limit) is
   ai.LimitReached: recorded in kv openrouter_limit (the monitor shows it), Deyao is told once per episode
   (notify.deyao), and the first call that works again clears it. Other errors stay what they were.
2. The money caps are gone: fulltext's proxied pages a day and days back, the replies' proxy pacing, hourly 香港討論區
   runs, quiet-board gaps, the long-thread tail, the per-run discuss cap, broadcast's monthly YouTube MB, the paid
   tools' per-account budget.
3. Site protection stays: per-host gaps and overall request rates, LIHKG's per-run cap, TikHub's request gap,
   Telegram's per-channel gap.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_no_budget.py
"""
import io, json, os, sys, unittest, urllib.error
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["OPENROUTER_API"] = "test"
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, corpus, notify  # noqa: E402

SENT = []
notify.deyao = lambda text: SENT.append(text) or True  # sent


def http_error(code, body):
    return urllib.error.HTTPError("https://openrouter.ai/api/v1/chat/completions", code, "x", {},
                                  io.BytesIO(json.dumps(body).encode()))


class Ok:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return io.BytesIO(self.body)

    def __exit__(self, *a):
        return False


OK = {"choices": [{"message": {"role": "assistant", "content": "{}"}}], "usage": {"cost": 0.001}}


class Limit(unittest.TestCase):
    def setUp(self):
        SENT.clear()
        corpus.kv_set("openrouter_limit", None)
        corpus.kv_set("openrouter_limit_told", None)

    def test_402_is_a_hard_stop_alerted_once(self):
        err = {"error": {"code": 402, "message": "Insufficient credits"}}
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: (_ for _ in ()).throw(http_error(402, err))):
            for _ in range(3):
                with self.assertRaises(ai.LimitReached):
                    ai.chat([{"role": "user", "content": "x"}], "scoring")
        lim = corpus.kv_get("openrouter_limit")
        self.assertTrue(lim and lim.get("since"))
        self.assertIn("Insufficient credits", lim["message"])
        self.assertEqual(len(SENT), 1)  # once per episode
        self.assertIn("OpenRouter", SENT[0])
        with mock.patch("urllib.request.urlopen", return_value=Ok(OK)):
            ai.chat([{"role": "user", "content": "x"}], "scoring")
        self.assertFalse(corpus.kv_get("openrouter_limit"))  # cleared by the first call that works
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: (_ for _ in ()).throw(http_error(402, err))):
            with self.assertRaises(ai.LimitReached):
                ai.chat([{"role": "user", "content": "x"}], "scoring")
        self.assertEqual(len(SENT), 1)  # back within ai.NOTIFY_EVERY of the alert: the same stop flapping, not a new one
        corpus.kv_set("openrouter_limit", None)
        corpus.kv_set("openrouter_limit_told", (datetime.now(corpus.HKT) - ai.NOTIFY_EVERY - timedelta(minutes=1)).isoformat())
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: (_ for _ in ()).throw(http_error(402, err))):
            with self.assertRaises(ai.LimitReached):
                ai.chat([{"role": "user", "content": "x"}], "scoring")
        self.assertEqual(len(SENT), 2)  # a new episode alerts again

    def test_key_limit_403_and_other_errors(self):
        err = {"error": {"code": 403, "message": "Key limit exceeded (monthly limit). Manage it using https://openrouter.ai/settings/keys"}}
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: (_ for _ in ()).throw(http_error(403, err))):
            with self.assertRaises(ai.LimitReached):
                ai.jsonc("x", "scoring")
        self.assertEqual(len(SENT), 1)
        corpus.kv_set("openrouter_limit", None)
        other = {"error": {"code": 403, "message": "Input flagged by moderation"}}
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: (_ for _ in ()).throw(http_error(403, other))):
            with self.assertRaises(urllib.error.HTTPError) as e:
                ai.chat([{"role": "user", "content": "x"}], "scoring")
            self.assertNotIsInstance(e.exception, ai.LimitReached)
        self.assertFalse(corpus.kv_get("openrouter_limit"))

    def test_stream_limit_in_the_body(self):  # an error event inside the stream
        lines = [b'data: {"error": {"code": 402, "message": "Insufficient credits"}}\n']

        class S(Ok):
            def __enter__(self):
                return iter(lines)
        with mock.patch("urllib.request.urlopen", return_value=S({})):
            with self.assertRaises(ai.LimitReached):
                list(ai.chat_stream([{"role": "user", "content": "x"}], "summary"))
        self.assertEqual(len(SENT), 1)


class NoSilentFallback(unittest.TestCase):
    def test_monitor_shows_the_limit(self):
        import monitor
        corpus.kv_set("openrouter_limit", {"since": "2026-10-14T20:00:00+08:00", "message": "Key limit exceeded",
                                           "notified": True})
        try:
            p = monitor.pipeline()
        except Exception as e:  # the pipeline needs tables the poller makes; the limit is read first
            self.skipTest(repr(e)[:100])
        self.assertEqual(p["ai_limit"]["message"], "Key limit exceeded")
        self.assertNotIn("budget", p["history_scoring"])
        with open(os.path.join(HERE, "web-app", "src", "routes", "-MonitorPage.tsx"), encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("waiting for budget", html)
        self.assertIn("OpenRouter limit", html)
        corpus.kv_set("openrouter_limit", None)


class CapsGone(unittest.TestCase):
    def test_fulltext_reads_every_proxied_page(self):
        import fulltext_fetch, cache_fetch
        for name in ("PROXY_PAGES_DAY", "PROXY_DAYS"):
            self.assertFalse(hasattr(fulltext_fetch, name), name)
        h = cache_fetch.PROXY_HOSTS[0]
        todo = [(i, f"https://{h}/article/{i}", "x", "2026-01-01") for i in range(400)]  # old and many
        with mock.patch.dict(os.environ, {"RESI_PROXY": "socks5://u:p@h:1"}):
            by_host, skipped = fulltext_fetch.plan(todo)
        self.assertEqual((sum(map(len, by_host.values())), skipped), (400, 0))
        with mock.patch.dict(os.environ, {"RESI_PROXY": ""}):
            by_host, skipped = fulltext_fetch.plan(todo)
        self.assertEqual((sum(map(len, by_host.values())), skipped), (0, 400))  # no proxy configured: can't read them

    def test_replies_unpaced(self):
        import replies_fetch
        for name in ("OutOfBudget", "DISCUSS_MB_MONTH", "LIHKG_MB_MONTH", "DISCUSS_RUN_EVERY", "DISCUSS_EVERY",
                     "DISCUSS_TAIL", "DISCUSS_MAX_PAGES"):
            self.assertFalse(hasattr(replies_fetch, name), name)
        self.assertFalse(hasattr(replies_fetch.Proxied, "allowance"))
        corpus.kv_set("proxy:bytes:discuss:total", 10 ** 12)  # a terabyte used: requests still go out
        os.environ["RESI_PROXY"] = "http://u:p@h:1"
        px = replies_fetch.Proxied("discuss")
        def fetch(url, headers):
            px.bytes += 1000
            return 200, "<html>ok</html>"
        px.fetch = fetch
        self.assertEqual(px.get("https://www.discuss.com.hk/x"), "<html>ok</html>")
        px.account()
        self.assertGreater(corpus.kv_get("proxy:bytes:discuss:total"), 10 ** 12)  # still counted (monitor)
        corpus.kv_set("replies:discuss:ran_at", __import__("time").time())  # ran a minute ago: runs again anyway
        with mock.patch.object(replies_fetch, "Proxied", side_effect=RuntimeError("reached the run")):
            with self.assertRaises(RuntimeError):
                replies_fetch.run("discuss", 3600)

    def test_broadcast_no_monthly_mb(self):
        src = open(os.path.join(HERE, "broadcast_fetch.py"), encoding="utf-8").read()
        self.assertNotIn("YT_MB_MONTH", src)

    def test_paid_tools_have_no_budget(self):
        import tools
        self.assertFalse(os.path.exists(os.path.join(HERE, "tools", "budget.py")))
        self.assertFalse(hasattr(tools, "budget_limit"))
        from tools import spend
        self.assertFalse(hasattr(spend, "DEFAULT_LIMIT"))
        self.assertFalse(hasattr(spend, "reset"))

    def test_streams_no_reserve(self):
        import streams
        for name in ("budget", "RESERVE_MARGIN", "BACKFILL_WAIT", "live_rate"):
            self.assertFalse(hasattr(streams, name), name)


class SiteProtectionStays(unittest.TestCase):
    def test_rate_limits(self):
        import cache_fetch, fulltext_fetch, replies_fetch, telegram_alerts, tikhub_fetch
        self.assertGreater(fulltext_fetch.HOST_GAP, 0)
        self.assertGreater(fulltext_fetch.RATE, 0)
        self.assertGreater(cache_fetch.RATE, 0)
        self.assertGreater(replies_fetch.LIHKG_MAX_PAGES, 0)
        self.assertGreater(tikhub_fetch.MIN_GAP, 0)
        self.assertGreater(telegram_alerts.GAP, 0)


if __name__ == "__main__":
    unittest.main()
