#!/usr/bin/env python3
"""Capture lag of the news sites the poller reads (measured live 15 Oct 2026: first seen − published, posts published
after their source started). No network, no database.

- 橙新聞: its news and finance sitemaps are rebuilt every 6 hours (00, 06, 12, 18 HKT: Last-Modified 10:00 UTC at
  20:46 HKT, newest row 17:59), so its posts came in a median 147-160 min late. The site's own channel lists
  (api.orangenews.hk get_channel_data, a POST, what its section pages call) list a story within a minute: the
  港聞/兩岸/國際/產經/地產 channels are read every poll, under the same URL the sitemap later gives
  (/<channelPath>/<newsCode>/<title, each non-word character a '-'>.shtml; 88 of 88 matched on 15 Oct).
- The sources read every N seconds (sources.EVERY): a source was due only when a full N had passed since the poll
  that last read it, and polls start ~5.5 min apart, so EVERY 900 meant every third poll (16.5 min) and EVERY 300 could
  skip every other poll. A poll a little short of N counts (EVERY_SLACK).
- The sitemaps whose site lists a story within minutes (TVB's: 2.7 min median; on.cc, 香港01, am730, 星島, 明報,
  881903, HKET, 點新聞, 文匯, China Daily) are read every poll (EVERY entries removed): 30-190 KB a read.
- The poller waited POLL seconds AFTER each poll (poll time + 300 s); it now starts a poll every POLL seconds.

    python3 tests/test_capture_lag_news.py
"""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sources, sources_extra  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "capture_lag_news")


def spec(sid):
    return next(s for s in list(sources_extra.EXTRA) + list(sources.SOURCES) if s[0] == sid)


class OrangeNewsChannels(unittest.TestCase):
    def test_channel_list_gives_the_sitemaps_urls_and_times(self):
        body = open(os.path.join(FX, "orangenews_channel_119.json"), encoding="utf-8").read()
        calls = []

        def fake(url, data=None, headers=None, proxy=None, timeout=None, **k):
            calls.append((url, data))
            return body
        with mock.patch.object(sources, "fetch", side_effect=fake):
            items = sources.fetch_source(spec("orangenews_hk"))
        self.assertTrue(calls[0][0].startswith("https://api.orangenews.hk/app/bus/channel/common/get_channel_data"))
        self.assertIn(b"channelId", calls[0][1])  # a POST: the API answers GET with a 302
        self.assertIn(b"119", calls[0][1])
        by = {it["url"]: it for it in items}
        self.assertIn("https://www.orangenews.hk/hongkong/VY6qpaG/葵廣巴籍店員涉手指篤警脫襲警罪-官指警員口供與片段不符.shtml", by)
        self.assertIn("https://www.orangenews.hk/hongkong/VY5pgQP/長實嚴正聲明-李嘉誠去世-絕無其事--強烈譴責有自媒體揑造假新聞.shtml", by)
        it = by["https://www.orangenews.hk/hongkong/VY6qpaG/葵廣巴籍店員涉手指篤警脫襲警罪-官指警員口供與片段不符.shtml"]
        self.assertEqual(it["published"].astimezone(sources.HKT).isoformat()[:19], "2026-10-15T17:59:17")
        self.assertEqual(it["source"], "橙新聞")
        self.assertEqual(len(items), 4)

    def test_every_news_channel_read_every_poll(self):
        sids = {s[0] for s in sources_extra.EXTRA if s[0].startswith("orangenews_") and s[0] != "orangenews_finance"}
        self.assertEqual(sids, {"orangenews_hk", "orangenews_cn", "orangenews_intl", "orangenews_biz", "orangenews_property"})
        for sid in sids:
            self.assertNotIn(sid, sources.EVERY)


class DueWithSlack(unittest.TestCase):
    def test_a_poll_a_little_short_of_every_counts(self):
        sp = ("zz_test", "x", "https://example.com/", lambda t: [], False)
        read = []
        with mock.patch.object(sources, "SOURCES", [sp]), mock.patch.dict(sources.EVERY, {"zz_test": 900}), \
                mock.patch.object(sources, "fetch_source", side_effect=lambda s: read.append(s[0]) or []), \
                mock.patch.object(sources, "browser_file", side_effect=Exception), \
                mock.patch.object(sources, "instagram_file", side_effect=Exception), \
                mock.patch.object(sources, "tikhub_file", side_effect=Exception), \
                mock.patch.object(sources, "watch_file", side_effect=LookupError), \
                mock.patch.object(sources, "facebook_file", side_effect=Exception), \
                mock.patch.dict(sources._last, {}, clear=True), mock.patch.object(sources.time, "time") as t:
            t.return_value = 1000.0
            sources.fetch_all()
            t.return_value = 1000.0 + 3 * 299.5  # three polls ~300 s apart: 898.5 s, short of 900 by jitter
            sources.fetch_all()
        self.assertEqual(read, ["zz_test", "zz_test"])


class FastSitemapsEveryPoll(unittest.TestCase):
    def test_sites_that_list_within_minutes_are_read_every_poll(self):
        for sid in ("oncc_instant", "hk01_sitemap", "stheadline_sitemap", "am730_sitemap", "mingpao_ins_sitemap",
                    "tvb_sitemap", "cr881903_sitemap", "hket_sitemap", "dotdotnews", "chinadailyhk", "wenweipo_hk"):
            self.assertLessEqual(sources.EVERY.get(sid, 0), 300, sid)


class PollEveryPollSeconds(unittest.TestCase):
    def test_the_wait_counts_the_polls_own_time(self):
        import app
        clock = [0.0]
        waits = []

        def poll_once():
            clock[0] += 70  # a poll that took 70 s

        def wait(s):
            waits.append(s)
            clock[0] += s
            return len(waits) >= 2
        with mock.patch.object(app, "poll_once", poll_once), mock.patch.object(app.time, "monotonic", lambda: clock[0]), \
                mock.patch.object(app.STOP, "wait", side_effect=wait), \
                mock.patch.object(app.STOP, "is_set", side_effect=lambda: len(waits) >= 2):
            app.loop()
        self.assertEqual(waits, [app.POLL - 70, app.POLL - 70])


if __name__ == "__main__":
    unittest.main()
