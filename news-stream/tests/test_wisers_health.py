#!/usr/bin/env python3
"""The WiseSearch relay's health check and background logins (Deyao, 2026-11-05: staff saw WiseSearch fail while the
relay logged in again on their calls).

    python3 tests/test_wisers_health.py

The fake WisersOne (tests/fake_wisers.py) runs locally; its login page long-polls like Wisers' (its network is never
idle). The relay's real browser worker and its health-check thread (prober) run against it, with a fake solver.
1. the first health check opens the session (a login: the saved one has expired) through the long-polling login page;
2. the session is ended between calls (another login): the next health check finds it and logs in in the background,
   so the staff member's next call needs no login; events.jsonl has the expiry (how Wisers said so) and both logins;
3. a login that fails (wrong password) leaves the relay logged out; the health check retries in the background and,
   once the password works again, holds the session with no staff call;
4. the stats port's health block: the last check (ok, seconds), failures of the last 15 minutes, logins of the hour;
5. getHotPicks' empty answer (no code, no data) is an empty list, not an error.
"""
import json, os, sys, threading, time, unittest, urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_wisers import SOLVED, FakeWisers, new_ns  # noqa: E402

NS = new_ns("health")
W = FakeWisers(captcha=True)
S = W.S
os.environ.update(W.env(NS))
W.stale_state(NS)
sys.path.insert(0, os.path.join(HERE, "wisers"))
import relay  # noqa: E402

relay.configure(NS)
relay.PROBE_SECS = 3
relay.LOGIN_GAP = 2
relay.exit_ip = lambda: "203.0.113.9"  # no outside lookup in a test
threading.Thread(target=relay.browser_worker, kwargs={"solver": lambda png: SOLVED}, daemon=True).start()
threading.Thread(target=relay.prober, daemon=True).start()


def until(cond, secs=90):
    t = time.time()
    while not cond() and time.time() - t < secs:
        time.sleep(0.3)
    return cond()


def events(kind):
    time.sleep(0.5)  # the lines with an address are written by a thread of their own
    return [e for e in relay.events_log() if e.get("event") == kind]


class Health(unittest.TestCase):
    def test_hotpicks_empty_is_ok(self):
        d, _ = relay.interpret("CommonService", ["ok", 200, "", json.dumps({"g0": {"data": {"data": {}, "path": "getHotPicks"}}})], 5,
                               "getHotPicks")
        self.assertEqual(d.get("data"), {})
        with self.assertRaises(RuntimeError):  # any other path: still an error
            relay.interpret("CommonService", ["ok", 200, "", json.dumps({"g0": {"data": {"data": {}}}})], 5, "getWipPackage")
        with self.assertRaises(relay.Expired) as e:
            relay.interpret("CommonService", ["ok", 200, "", json.dumps({"g0": {"data": {"code": "E40001", "message": "no login record"}}})], 5)
        self.assertIn("E40001 no login record", str(e.exception))
        with self.assertRaises(relay.Expired):  # 2026-11-06: an over session's calls answered 401, empty, no redirect
            relay.interpret("CommonService", ["ok", 401, "https://vertical-new-search.wisersone.com/wevo/api", ""], 5)

    def test_all(self):
        # 1. the first check logs in (the saved session is stale), through the long-polling login page
        self.assertTrue(until(lambda: (relay.session_state().get("probe") or {}).get("ok")), relay.session_state())
        self.assertEqual(S["logins"], 1)
        self.assertGreater(S["polls"], 0, "the fake login page long-polls")
        self.assertEqual(relay.session_state()["state"], "held")

        # 2. another login ends the session: the health check logs in again by itself, before any staff call
        S["valid"] = {"someone-else"}
        self.assertTrue(until(lambda: S["logins"] == 2), S["logins"])
        self.assertTrue(until(lambda: relay.session_state()["state"] == "held" and (relay.session_state().get("probe") or {}).get("ok")))
        n = S["logins"]
        self.assertEqual(relay.lookup("search/keywords", {"keyword": "施政"}, "staff@yyc.hk") is not None, True)
        self.assertEqual(S["logins"], n, "the staff call needed no login")
        exp = events("expired")
        self.assertTrue(exp and exp[-1]["how"].startswith("getUserCustomConfig: a redirect to the login page"), exp)
        self.assertIsNotNone(exp[-1]["idle_secs"])
        self.assertEqual(exp[-1]["exit_ip"], "203.0.113.9")
        lg = events("login")
        self.assertEqual([e["ok"] for e in lg], [True, True], lg)

        # 2b. the session ends and Wisers answers the calls HTTP 401 (no redirect): an expiry too, logged in again
        S["api_401"] = True
        S["valid"] = {"someone-else-401"}
        self.assertTrue(until(lambda: S["logins"] == 3), S["logins"])
        self.assertTrue(until(lambda: relay.session_state()["state"] == "held" and (relay.session_state().get("probe") or {}).get("ok")))
        exp = events("expired")
        self.assertIn("HTTP 401", exp[-1]["how"], exp)
        S["api_401"] = False

        # 3. the login fails: logged out, retried in the background; works again once the password does
        os.environ["WISERS_PASSWORD"] = "wrong"
        S["valid"] = {"someone-else-again"}
        self.assertTrue(until(lambda: relay.session_state()["state"] == "logged_out"), relay.session_state())
        self.assertTrue(until(lambda: len([e for e in events("login") if not e["ok"]]) >= 2, 120), "retried in the background")
        os.environ["WISERS_PASSWORD"] = "pw-ok"
        self.assertTrue(until(lambda: relay.session_state()["state"] == "held", 120), relay.session_state())
        self.assertTrue(until(lambda: (relay.session_state().get("probe") or {}).get("ok")))

        # 4. the stats port's health block
        relay.call_line("search/keywords", who="staff@yyc.hk", outcome="logged_out", error=relay.EXPIRED)
        srv = relay.ThreadingHTTPServer(("127.0.0.1", 0), relay.Stats)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        st = json.load(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/stats", timeout=10))
        h = st["health"]
        self.assertTrue(h["probe"]["ok"] and h["probe"]["secs"] is not None, h)
        self.assertEqual(h["failures"].get("logged_out"), 1, h)
        self.assertEqual(h["recent_failures"][0]["who"], "staff@yyc.hk")
        self.assertGreaterEqual(h["logins_1h"], 5)
        self.assertGreaterEqual(h["failed_logins_1h"], 2)
        self.assertEqual(h["expiries_1h"], 4, "the retries while logged out are no expiries")
        self.assertEqual(h["probe_secs"], 3)
        srv.shutdown()

        # 5. a check that hangs (2026-11-11: one in 10-25 timed out at 60 s, the next answered): its event says where it
        # hung (the page's own network trace), it counts as one failure in a row, and the check that answers next is
        # an event too
        relay.PROBE_TIMEOUT = 3
        S["config_hang"] = 8
        self.assertTrue(until(lambda: (relay.session_state().get("probe") or {}).get("ok") is False, 60), relay.session_state())
        pr = relay.session_state()["probe"]
        self.assertEqual((pr["outcome"], pr["fails"]), ("timeout", 1), pr)
        self.assertTrue(until(lambda: (relay.session_state().get("probe") or {}).get("ok"), 60))
        self.assertEqual(relay.session_state()["probe"]["fails"], 0)
        ev = [e for e in events("probe") if e.get("path") == "getUserCustomConfig"]
        bad = [e for e in ev if not e["ok"]][-1]
        self.assertEqual((bad["outcome"], bad["fails"]), ("timeout", 1), bad)
        api = bad["net"]["api"]
        self.assertTrue(api and api[-1]["url"].endswith("/wevo/api") and api[-1]["method"] == "POST", bad["net"])
        self.assertNotIn("headers", api[-1], "no answer arrived: the trace shows the request sent and never answered")
        self.assertIn("failed", api[-1], "the page's abort")
        good = ev[-1]
        self.assertEqual((good["ok"], good["after_fails"]), (True, 1), good)
        relay.PROBE_TIMEOUT = 60


if __name__ == "__main__":
    unittest.main()
