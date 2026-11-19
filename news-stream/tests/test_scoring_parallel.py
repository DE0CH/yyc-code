#!/usr/bin/env python3
"""The live scorer's model calls run in parallel (SCORE_THREADS), and each batch is filed the moment its own calls
are done (Deyao, 2026-10-23: "the pipeline time could be shortened"). After the 20 Oct 2026 restart (the cluster off
10.5 h, then Facebook/Instagram catching up at 3,300 posts an hour) the queue sat at 10,460 due posts for 30 min with
one call at a time (2,700-3,700 posts an hour), its oldest ageing out of MAX_AGE unscored. Rounds that waited for
their slowest batch before filing anything held a stage-2 answer a minute before it was filed.
TEST_DATABASE_URL, a throwaway database; no model call.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_scoring_parallel.py

1. run_once: the batches are scored at once; a fast batch is filed before a slow one finishes.
2. loop: a post that becomes due while a slow batch is in a call is scored and filed at once, without waiting for
   it; no post is in two calls at once; live posts and thread units alternate; a slow due() query (30 s on Neon when
   few posts are due) never holds up the filing.
3. loop drain (STOP): no new batch starts, the one in hand finishes and is filed, the loop returns.
4. OpenRouter's refusal: run_once raises it; loop pauses new batches (LIMIT_WAIT) and keeps running.
"""
import os, sys, threading, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import ai, streams  # noqa: E402

POSTS = [{"id": i, "url": f"https://example.com/{i}", "title": f"t{i}"} for i in range(50)]


def ok(batch):
    return {i: {"sentiment": None, "labels": [], "streams": {}} for i in range(len(batch))}


class Parallel(unittest.TestCase):
    def setUp(self):
        streams.STOP.clear()
        self.filed, self.due, self.units = [], list(POSTS), []
        lock = threading.Lock()

        def file(st, batch, res, markets=None):
            with lock:
                self.filed.append(([p["id"] for p in batch], time.time()))
            for p in batch:  # filed = scored: no longer due
                if p in self.due:
                    self.due.remove(p)
                if p in self.units:
                    self.units.remove(p)
            return []  # streams.file → [items filed]
        self.patches = [mock.patch.object(streams, "settings", lambda: {}),
                        mock.patch.object(streams, "due", lambda limit, s=None: self.due[:limit]),
                        mock.patch.object(streams, "unit_due", lambda limit, s=None: self.units[:limit]),
                        mock.patch.object(streams.thin, "gate", lambda posts: posts),
                        mock.patch.object(streams.titles, "ensure", lambda batch, scored=(): None),
                        mock.patch.object(streams, "file", file)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        streams.STOP.set()
        for p in self.patches:
            p.stop()
        streams.STOP.clear()

    def test_1_run_once_files_each_batch_when_done(self):
        now, peak, lock = [0], [0], threading.Lock()

        def score(st, batch, purpose="scoring"):
            with lock:
                now[0] += 1
                peak[0] = max(peak[0], now[0])
            time.sleep(1.0 if batch[0]["id"] == 0 else 0.2)  # the first (newest) batch is slow
            with lock:
                now[0] -= 1
            return ok(batch)
        t0 = time.time()
        with mock.patch.object(streams, "score_posts", score):
            n = streams.run_once([])
        self.assertEqual(n, 50)
        self.assertGreater(peak[0], 1, "the batches' model calls overlap")
        self.assertLess(time.time() - t0, 1.8)
        self.assertEqual(sorted(i for b, _ in self.filed for i in b), list(range(50)))
        self.assertEqual(self.filed[-1][0], list(range(10)), "the slow batch is filed last")
        self.assertLess(self.filed[0][1] - t0, 0.6, "a fast batch is filed without waiting for the slow one")
        self.assertGreaterEqual(streams.SCORE_THREADS, 10)

    def test_2_loop_takes_new_posts_while_a_batch_is_in_a_call(self):
        self.due = [POSTS[0]]
        release, calls, inflight = threading.Event(), [], set()
        lock = threading.Lock()

        def score(st, batch, purpose="scoring"):
            ids = {p["id"] for p in batch}
            with lock:
                self.assertFalse(ids & inflight, "a post in two calls at once")
                inflight.update(ids)
                calls.append(sorted(ids))
            if 0 in ids:
                release.wait(10)  # a slow stage-2 call
            with lock:
                inflight.difference_update(ids)
            return ok(batch)
        with mock.patch.object(streams, "score_posts", score):
            t = threading.Thread(target=streams.loop, args=([],), daemon=True)
            t.start()
            time.sleep(0.5)
            self.due.append(POSTS[1])  # arrives while post 0 is in its call
            t0 = time.time()
            while not any(b == [1] for b, _ in self.filed) and time.time() - t0 < 5:
                time.sleep(0.05)
            self.assertIn([1], [b for b, _ in self.filed], "post 1 filed while post 0's call runs")
            self.assertLess(time.time() - t0, 2.5)
            self.assertNotIn([0], [b for b, _ in self.filed])
            release.set()
            t0 = time.time()
            while not any(b == [0] for b, _ in self.filed) and time.time() - t0 < 5:
                time.sleep(0.05)
            self.assertIn([0], [b for b, _ in self.filed])
            self.assertEqual(sum(c.count(0) for c in calls), 1, "post 0 scored once")
            streams.STOP.set()
            t.join(5)
            self.assertFalse(t.is_alive())

    def test_2b_plan_alternates_live_posts_and_units(self):
        self.units = [dict(p, id=100 + p["id"], url=f"https://lihkg.com/thread/{p['id']}") for p in POSTS[:3]]
        with mock.patch.object(streams, "char_batches", lambda ps: [[p] for p in ps]):
            jobs = streams.plan([], limit=30)
        self.assertEqual([purpose for _, purpose in jobs][:6],
                         ["scoring", streams.LIHKG_PURPOSE] * 3)
        jobs = streams.plan([], limit=30, busy={0, 1, 100}, slots=2)
        self.assertEqual(len(jobs), 2)
        self.assertNotIn(0, [p["id"] for b, _ in jobs for p in b])
        self.assertNotIn(100, [p["id"] for b, _ in jobs for p in b])

    def test_2c_a_slow_due_query_never_holds_up_filing(self):
        """due() takes 30 s on Neon when few posts are due: the planner runs it beside the filing"""
        self.due = [POSTS[0]]
        calls = [0]

        def due(limit, s=None):
            calls[0] += 1
            if calls[0] > 1:
                time.sleep(3)
            return self.due[:limit]

        def score(st, batch, purpose="scoring"):
            time.sleep(0.3)
            return ok(batch)
        with mock.patch.object(streams, "score_posts", score), mock.patch.object(streams, "due", due):
            t0 = time.time()
            t = threading.Thread(target=streams.loop, args=([],), daemon=True)
            t.start()
            while not self.filed and time.time() - t0 < 5:
                time.sleep(0.05)
            self.assertTrue(self.filed)
            self.assertLess(self.filed[0][1] - t0, 1.5, "filed while the next due() query still runs")
            streams.STOP.set()
            t.join(10)

    def test_3_drain_files_what_is_in_hand(self):
        self.due = [POSTS[0]]
        started = threading.Event()

        def score(st, batch, purpose="scoring"):
            started.set()
            time.sleep(0.5)
            return ok(batch)
        with mock.patch.object(streams, "score_posts", score):
            t = threading.Thread(target=streams.loop, args=([],), daemon=True)
            t.start()
            self.assertTrue(started.wait(5))
            self.due.append(POSTS[1])
            streams.STOP.set()
            t.join(5)
        self.assertFalse(t.is_alive(), "the loop returns after the drain")
        self.assertEqual([b for b, _ in self.filed], [[0]], "the batch in hand is filed, no new one started")

    def test_4_limit(self):
        def refuse(st, batch, purpose="scoring"):
            raise ai.LimitReached("Insufficient credits")
        with mock.patch.object(streams, "score_posts", refuse):
            with self.assertRaises(ai.LimitReached):
                streams.run_once([])
        calls = []

        def refuse_once(st, batch, purpose="scoring"):
            calls.append(time.time())
            raise ai.LimitReached("Insufficient credits")
        self.due = [POSTS[0]]
        with mock.patch.object(streams, "score_posts", refuse_once), mock.patch.object(streams, "LIMIT_WAIT", 1.5):
            t = threading.Thread(target=streams.loop, args=([],), daemon=True)
            t.start()
            time.sleep(2.5)
            streams.STOP.set()
            t.join(5)
        self.assertEqual(len(calls), 2, "one try, then the next only after LIMIT_WAIT")
        self.assertGreaterEqual(calls[1] - calls[0], 1.4)
        self.assertFalse(self.filed)


if __name__ == "__main__":
    unittest.main()
