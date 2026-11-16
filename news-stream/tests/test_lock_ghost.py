#!/usr/bin/env python3
"""Ghost lock sessions (2026-10-20): at 14:09 UTC the database connections of pods on several nodes went silent (no
RST reached Neon), so Neon kept their sessions, and with them their session advisory locks, for nine hours. whatsapp
and transcribe waited on their own ghosts ("lock lost to another copy"), tikhub on a ghost 'tikhub', and the sites,
forums and transcribe ISP reads on a ghost 'yyc youtube'. So every lock connection asks the SERVER to end it when idle
(idle_session_timeout; Neon enforces it, tested 2026-10-20), and every holder sends a heartbeat that keeps it busy;
a vanished holder's lock is free within corpus.LOCK_IDLE seconds. tcp_user_timeout makes a send on a dead path fail
in 30 s, not after TCP's 16-minute retransmission (the keepers noticed only at 14:25).

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_lock_ghost.py
"""
import os, sys, threading, time, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import corpus  # noqa: E402


def free(name):
    with corpus.lock(name, wait=False) as got:
        return got


class GhostLocks(unittest.TestCase):
    def setUp(self):
        self.saved = corpus.LOCK_IDLE, corpus.LOCK_BEAT
        corpus.LOCK_IDLE = 2

    def tearDown(self):
        corpus.LOCK_IDLE, corpus.LOCK_BEAT = self.saved

    def hold(self, cm, seconds):
        """hold cm in another thread for `seconds` → (thread, event set once it is held)"""
        held = threading.Event()

        def run():
            try:
                with cm:
                    held.set()
                    time.sleep(seconds)
            except Exception:  # noqa: BLE001  the server ended the ghost's session: its block's end may raise
                pass
        t = threading.Thread(target=run, daemon=True)
        t.start()
        self.assertTrue(held.wait(10))
        return t

    def test_a_silent_holder_loses_the_lock(self):
        corpus.LOCK_BEAT = 1000  # a ghost: the session exists, its client never speaks again
        self.hold(corpus.lock("t-ghost"), 8)
        self.assertFalse(free("t-ghost"))
        time.sleep(3.5)
        self.assertTrue(free("t-ghost"), "the server ends an idle lock session after LOCK_IDLE seconds")

    def test_a_live_holder_keeps_it(self):
        corpus.LOCK_BEAT = 0.5
        t = self.hold(corpus.lock("t-live"), 5)
        for _ in range(4):
            time.sleep(1)
            self.assertFalse(free("t-live"), "the heartbeat keeps a working holder's session")
        t.join(10)
        self.assertTrue(free("t-live"))

    def test_the_isp_and_youtube_locks_are_corpus_locks(self):
        import media, sites_fetch
        corpus.LOCK_BEAT = 1000
        self.hold(sites_fetch.isp_lock(), 8)
        self.assertFalse(free("yyc youtube"))
        time.sleep(3.5)
        self.assertTrue(free("yyc youtube"))
        self.hold(media.youtube_lock(), 8)
        self.assertFalse(free("yyc youtube"))
        time.sleep(3.5)
        self.assertTrue(free("yyc youtube"))

    def test_a_lock_wait_is_bounded(self):
        """a job never waits past its deadline for the ISP route: isp_lock gives up after ISP_WAIT seconds (the page is
        retried by a later run), and a corpus.lock with a timeout raises LockTimeout"""
        import sites_fetch
        corpus.LOCK_BEAT = 0.5
        self.hold(corpus.lock("yyc youtube"), 6)
        t = time.monotonic()
        with self.assertRaises(corpus.LockTimeout):
            with corpus.lock("yyc youtube", timeout=1):
                pass
        self.assertLess(time.monotonic() - t, 3)
        saved, sites_fetch.ISP_WAIT = sites_fetch.ISP_WAIT, 1
        try:
            with self.assertRaises(corpus.LockTimeout):
                with sites_fetch.isp_lock():
                    pass
        finally:
            sites_fetch.ISP_WAIT = saved

    def test_singleton_lock_connections_end_when_idle(self):
        import app, podlock
        for c in (app._lock_connect(), podlock.Lock("t", threading.Event())._connect(), corpus.lock_connect("t")):
            with c:
                self.assertEqual(c.execute("SHOW idle_session_timeout").fetchone()[0], "2s")
                self.assertEqual(c.info.get_parameters().get("tcp_user_timeout"), "30000")

    def test_pool_connections_time_out_sends(self):
        with corpus.pool().connection() as c:
            self.assertEqual(c.info.get_parameters().get("tcp_user_timeout"), "30000")


if __name__ == "__main__":
    unittest.main()
