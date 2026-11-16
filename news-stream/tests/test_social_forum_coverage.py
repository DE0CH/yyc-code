#!/usr/bin/env python3
"""Coverage of social search and forum replies (coverage audit, 2026-10-14), on a throwaway database
(TEST_DATABASE_URL), no network:

1. Social search runs each TikHub (platform, query) every 3 hours and each Facebook (AnyAPI) query every hour
   (tikhub_fetch.EVERY; Deyao, 2026-11-11: TikHub cost), a failed one again on the next hourly run. Lemon8 is not searched.
2. A provider's refusal for its balance (TikHub's HTTP 402 "Insufficient balance", AnyAPI's 402 / credit message) is a
   safety stop: kv social_limit records it per provider, Deyao gets one Discord message per episode (notify.deyao), the
   rest of that provider's queries in the run are not sent (their last posts stay), the other providers carry on, and
   the first successful search clears it. The monitor's pipeline shows it.
3. Forum replies: a thread the collectors filed keeps the board listing's CURRENT reply count. The collectors' count is
   the one from when the thread was filed, so it used to hide every later reply until the thread left the 2-day
   collected window (live 2026-10-14: 美股認真討論區 993 replies on LIHKG, 7 filed).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_social_forum_coverage.py
"""
import os, sys, time, unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, replies_fetch, tikhub_fetch  # noqa: E402

HKT = tikhub_fetch.HKT


def post(p, q, n=1):
    now = datetime.now(HKT).isoformat(timespec="seconds")
    return [{"platform": p, "source": f"{p}-a", "title": f"{q} {i}", "url": f"https://{p}.example/{q}/{i}",
             "published": now} for i in range(n)]


class Cadence(unittest.TestCase):
    def test_every(self):
        # the CronJob is hourly; a few minutes' slack so a run that starts late still finds the query due
        self.assertEqual(tikhub_fetch.EVERY["tikhub"], timedelta(hours=3) - timedelta(minutes=5))
        self.assertEqual(tikhub_fetch.EVERY["anyapi"], timedelta(hours=1) - timedelta(minutes=5))
        self.assertLessEqual(tikhub_fetch.RETRY, timedelta(hours=1))

    def test_no_lemon8(self):
        self.assertNotIn("lemon8", tikhub_fetch.ADAPTERS)
        self.assertNotIn("lemon8", tikhub_fetch.SCHEDULE)

    def test_due(self):
        """checked 1 h ago: the Facebook query runs again, the TikHub one waits; checked 3 h ago: both run"""
        plan = [("twitter", "甲"), ("facebook", "甲")]
        for hours, want in ((1, [("facebook", "甲")]), (3, plan)):
            at = (datetime.now(HKT) - timedelta(hours=hours)).isoformat(timespec="seconds")
            corpus.file_put("tikhub.json", {"checks": {f"{p}|{q}": {"ok": True, "items": 0, "error": None, "checked": at}
                                                       for p, q in plan}, "items": []})
            calls = []
            with mock.patch.object(tikhub_fetch, "queries", lambda only=None: plan), \
                    mock.patch.object(tikhub_fetch, "search", lambda p, q: calls.append((p, q)) or ([], None)), \
                    mock.patch.object(tikhub_fetch.time, "sleep", lambda s: None), mock.patch("notify.deyao", lambda t: True):
                tikhub_fetch.run()
            self.assertEqual(sorted(calls), sorted(want), f"checked {hours} h ago")


class SafetyStop(unittest.TestCase):
    def setUp(self):
        corpus.kv_set("social_limit", None)
        corpus.file_put("tikhub.json", {"checks": {}, "items": []})

    def run_with(self, answer):
        calls, sent = [], []

        def search(p, q):
            calls.append((p, q))
            return answer(p, q)
        plan = [("twitter", "甲"), ("twitter", "乙"), ("tiktok", "甲"), ("facebook", "甲"), ("facebook", "乙")]
        with mock.patch.object(tikhub_fetch, "queries", lambda only=None: plan), \
                mock.patch.object(tikhub_fetch, "search", search), mock.patch.object(tikhub_fetch.time, "sleep", lambda s: None), \
                mock.patch("notify.deyao", lambda text: sent.append(text) or True):
            tikhub_fetch._run(None)
        return calls, sent

    def test_stop_notify_once_and_clear(self):
        broke = "TikHubError: HTTP 402 /api/v1/twitter/web/fetch_search_timeline: {\"detail\":{\"code\":402,\"message\":\"Insufficient balance, this endpoint requires payment"
        def ans(p, q):
            if p in ("twitter", "tiktok"):
                return [], broke
            return post(p, q), None
        calls, sent = self.run_with(ans)
        # TikHub refused once: its other queries in that run are not sent; AnyAPI (facebook) carries on
        # (platforms run in parallel threads: at most the one request in flight on each when the refusal came)
        self.assertLessEqual(sum(1 for p, _ in calls if p == "twitter"), 1, calls)  # X's second query never sent
        self.assertLessEqual(sum(1 for p, _ in calls if p == "tiktok"), 1, calls)
        self.assertGreaterEqual(sum(1 for p, _ in calls if p in ("twitter", "tiktok")), 1, calls)
        self.assertEqual(sum(1 for p, _ in calls if p == "facebook"), 2)
        lim = corpus.kv_get("social_limit")
        self.assertIn("tikhub", lim)
        self.assertNotIn("anyapi", lim)
        self.assertIn("Insufficient balance", lim["tikhub"]["message"])
        self.assertEqual(len(sent), 1)
        self.assertIn("TikHub", sent[0])
        # the next run: still refused → no second message
        corpus.file_put("tikhub.json", {"checks": {}, "items": []})
        _, sent2 = self.run_with(ans)
        self.assertEqual(sent2, [])
        self.assertTrue(corpus.kv_get("social_limit")["tikhub"]["notified"])
        # monitor shows it
        import monitor
        self.assertIn("tikhub", monitor.social_limit() or {})
        # recharged: the first answer clears it
        corpus.file_put("tikhub.json", {"checks": {}, "items": []})
        self.run_with(lambda p, q: (post(p, q), None))
        self.assertFalse((corpus.kv_get("social_limit") or {}).get("tikhub"))

    def test_anyapi_credit(self):
        def ans(p, q):
            if p == "facebook":
                return [], "AnyAPIError: HTTP 402: {\"error\":\"insufficient_credits\",\"message\":\"Not enough credits\"}"
            return post(p, q), None
        calls, sent = self.run_with(ans)
        self.assertEqual(sum(1 for p, _ in calls if p == "facebook"), 1)
        self.assertIn("anyapi", corpus.kv_get("social_limit"))
        self.assertEqual(len(sent), 1)
        self.assertIn("AnyAPI", sent[0])

    def test_other_errors_are_not_a_stop(self):
        calls, sent = self.run_with(lambda p, q: ([], "TikHubError: HTTP 400 /api/v1/x: upstream failed"))
        self.assertEqual(len(calls), 5)
        self.assertFalse(corpus.kv_get("social_limit"))
        self.assertEqual(sent, [])


class Px:
    direct = True

    def account(self):
        pass


class CollectedThreadsKeepTheListingsCount(unittest.TestCase):
    def test_lihkg(self):
        tid = 4167976
        corpus.ingest([{"sid": "lihkg_cat15", "source": "LIHKG 財經台", "title": "美股認真討論區", "url":
                        f"https://lihkg.com/thread/{tid}/page/1", "published": datetime.now(HKT),
                        "engagement": {"replies": 7}}])
        replies_fetch.save_state("lihkg", tid, {"read": 7, "total": 7, "checked": time.time() - 3600})
        seen = {}

        def reader(px, t, known, allowed):
            seen[t] = known
            return 0, 1
        site = (10, {15: None}, lambda px, b, floor: ({tid: 993}, 1), reader, replies_fetch.lihkg_collected)
        with mock.patch.dict(replies_fetch.SITES, {"lihkg": lambda: site}), \
                mock.patch.object(replies_fetch, "Proxied", lambda *a, **k: Px()):
            replies_fetch.run("lihkg", 3600)
        self.assertEqual(seen.get(tid), 993)

    def test_discuss(self):
        tid = 31000001
        corpus.ingest([{"sid": "discuss", "source": "香港討論區", "title": "帖", "url":
                        f"https://www.discuss.com.hk/viewthread.php?tid={tid}", "published": datetime.now(HKT),
                        "engagement": {"replies": 3, "views": 10}}])
        seen = {}

        def reader(px, t, info, allowed):
            seen[t] = info
            return 0, 1
        listing = {tid: {"title": "帖", "replies": 250, "views": 900, "author": "a"}}
        site = (None, {54: "香港及世界新聞討論"}, lambda px, b, floor: (listing, 1), reader, replies_fetch.discuss_collected)
        with mock.patch.dict(replies_fetch.SITES, {"discuss": lambda: site}), \
                mock.patch.object(replies_fetch, "Proxied", lambda *a, **k: Px()):
            replies_fetch.run("discuss", 3600)
        self.assertEqual(seen[tid]["replies"], 250)
        self.assertEqual(seen[tid]["views"], 900)


if __name__ == "__main__":
    unittest.main()
