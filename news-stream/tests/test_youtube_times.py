#!/usr/bin/env python3
"""YouTube capture times (capture-lag audit, 2026-10-15), on a throwaway database (TEST_DATABASE_URL), no network
(a real RTHK uploads playlist page and a real InnerTube player answer in fixtures/youtube/):

1. A channel's uploads playlist gives only relative times ("4 分鐘前", "3 小時前", "2 日前"). A video first read while
   it shows hours or days got published = now − that many hours/days: up to a day off ("1 日前" filed 24 h early or
   late), so 首次收錄 looked a day after 發佈 (yt_*: lags of exactly 1442, 2882, 20162 min). Such a video that is not in
   the corpus yet gets its exact time from YouTube's player answer (microformat publishDate, ~8 KB, no proxy), once
   per video (kv cache); minute-fresh rows keep their relative time (exact to the minute). Every new video's player
   answer is read anyway, for its description (tests/test_fulltext_social.py).
2. `sources.py youtube-dates` corrects the stored rows whose published came from such a coarse time.
3. Watched YouTube channels (watch_fetch.py, RSS, free) are read on every 10-minute run: a 15-minute cadence with the
   3-minute slack made every channel wait 20 minutes (all 215 checked in one run, 1 in the next).
4. Comments of a YouTube Short (/shorts/<id>, the watched channels' Shorts) are read: the video id came only from
   ?v=, so every Short failed (~200 errors per comments run, 15 Oct).

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_youtube_times.py
"""
import gzip, json, os, sys, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, sources  # noqa: E402

FIX = os.path.join(HERE, "tests", "fixtures", "youtube")
HKT = timezone(timedelta(hours=8))
RTHK = next(s for s in sources.SOURCES if s[0] == "rthk_youtube")


def page():
    return gzip.open(os.path.join(FIX, "rthk_uploads.html.gz")).read().decode()


class PlayerTime(unittest.TestCase):
    def test_publish_date_from_player_answer(self):
        with open(os.path.join(FIX, "player_Dv2_7coivdI.json")) as f:
            d = json.load(f)
        # publishDate 2026-10-11T20:40:17-07:00 = 12 Oct 11:40 HKT (stored as 13 Oct 09:37 from "2 日前")
        self.assertEqual(sources.youtube_player_time(d), datetime(2026, 10, 12, 11, 40, 17, tzinfo=HKT))
        self.assertIsNone(sources.youtube_player_time({"playabilityStatus": {"status": "ERROR"}}))


class Playlist(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM posts")
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'ytinfo:%%'")

    def run_source(self, exact):
        calls = []

        def fake(vid):
            calls.append(vid)
            return {"published": exact, "text": ""} if exact else None

        with mock.patch.object(sources, "fetch", return_value=page()), \
                mock.patch.object(sources, "youtube_player", side_effect=fake):
            return sources.fetch_source(RTHK), calls

    def test_coarse_rows_get_the_exact_time(self):
        when = datetime(2026, 10, 14, 9, 30, 5, tzinfo=HKT)
        got, calls = self.run_source(when)
        rows = sources.youtube(page())
        coarse = [r for r in rows if not sources.youtube_fine(r["time"])]
        fine = [r for r in rows if sources.youtube_fine(r["time"])]
        self.assertTrue(coarse and fine)
        self.assertEqual(len(calls), min(len(rows), sources.YT_INFO_MAX))  # one player read per new video
        by = {g["url"]: g for g in got}
        read = set(calls)
        for r in coarse:
            if r["url"][-11:] in read:
                self.assertEqual(by[r["url"]]["published"], when, r["time"])
        for r in fine:
            self.assertLess(datetime.now(HKT) - by[r["url"]]["published"], timedelta(hours=1), r["time"])

    def test_known_and_cached_videos_are_not_read_again(self):
        with mock.patch.object(sources, "YT_INFO_MAX", 1000):
            self.run_source(datetime(2026, 10, 14, 9, 30, tzinfo=HKT))  # caches every video's answer
            _, calls = self.run_source(datetime(2026, 10, 14, 9, 30, tzinfo=HKT))
            self.assertEqual(calls, [])
            corpus.db().execute("DELETE FROM kv WHERE k LIKE 'ytinfo:%%'")
            rows = sources.youtube_recent(sources.youtube(page()))
            corpus.ingest([{"sid": "rthk_youtube", "source": "RTHK", "url": rows[0]["url"], "title": rows[0]["title"],
                            "text": "its description", "published": "2026-10-14T09:30+08:00"}])
            _, calls = self.run_source(datetime(2026, 10, 14, 9, 30, tzinfo=HKT))
        self.assertEqual(len(calls), len(rows) - 1)  # the one stored with its text is not read

    def test_unanswered_player_keeps_the_relative_time(self):
        got, calls = self.run_source(None)
        self.assertTrue(calls)
        self.assertTrue(all(g["published"] for g in got))


class FixStored(unittest.TestCase):
    def test_youtube_dates_corrects_coarse_rows(self):
        corpus.db().execute("DELETE FROM posts")
        corpus.ingest([
            {"sid": "yt_SB9yB62A", "source": "LION", "url": "https://www.youtube.com/watch?v=Ao4fiwZoyX8", "title": "a",
             "published": "2026-10-01T06:32+08:00"},  # "2 週前" at 06:34 on 15 Oct: really 25 Sep 13:00
            {"sid": "yt_SB9yB62A", "source": "LION", "url": "https://www.youtube.com/watch?v=freshfresh1", "title": "b",
             "published": None},
        ], seen="2026-10-15T06:34:04+08:00")
        corpus.db().execute("UPDATE posts SET published='2026-10-15T06:30+08:00' WHERE url LIKE '%%freshfresh1'")
        exact = {"Ao4fiwZoyX8": datetime(2026, 9, 24, 22, 0, 32, tzinfo=timezone(timedelta(hours=-7)))}
        with mock.patch.object(sources, "youtube_player", side_effect=lambda v: {"published": exact.get(v), "text": ""}) as m:
            n = sources.youtube_fix_dates()
        self.assertEqual(n, 1)
        self.assertEqual([c.args[0] for c in m.call_args_list], ["Ao4fiwZoyX8"])  # a 4-minute lag is exact already
        r = corpus.db().execute("SELECT published, day FROM posts WHERE url LIKE '%%Ao4fiwZoyX8'").fetchone()
        self.assertEqual(r["published"], "2026-09-25T13:00+08:00")
        self.assertEqual(r["day"], "2026-09-25")


class WatchCadence(unittest.TestCase):
    def test_youtube_channel_due_every_run(self):
        import watch_fetch
        st = {"checked": (datetime.now(HKT) - timedelta(minutes=10)).isoformat(timespec="seconds")}
        gap = watch_fetch.every_min({"platform": "youtube"}, st, 0)
        self.assertLessEqual(gap - 3, 10 - 2)  # the CronJob's 10 minutes (minus jitter) is enough to be due again


class ShortsComments(unittest.TestCase):
    def test_short_url_reads_its_comments(self):
        import comments_fetch
        seen = []

        def fake_next(body):
            seen.append(body)
            return {}, 10

        with mock.patch.object(comments_fetch, "yt_next", side_effect=fake_next):
            comments_fetch.youtube_post({"url": "https://www.youtube.com/shorts/LUQrzowipTQ", "title": "t"})
        self.assertEqual(seen[0], {"videoId": "LUQrzowipTQ"})


if __name__ == "__main__":
    unittest.main()
