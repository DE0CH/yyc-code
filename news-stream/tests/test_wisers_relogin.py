#!/usr/bin/env python3
"""The WiseSearch relay logs in again by itself when its Wisers session expires (Deyao, 2026-10-16: "Login expired").

    python3 tests/test_wisers_relogin.py

A fake WisersOne runs locally: a "home" server (/wevo/home, /wevo/api: a valid session cookie or a 302 to the login
page, as Wisers does) and a separate "login" server (Wisers' login form with the same field classes, an image captcha,
one session per user: a login revokes every earlier session). The relay's real browser worker (Playwright Chromium)
runs against it through WISERS_HOME / WISERS_LOGIN_URL, with a fake captcha solver.
1. a saved session that has expired: the first search logs in, succeeds, and the new session is saved (state.json in the store);
2. the session is ended between searches (another login): the search's API call is redirected to the login page, the
   worker logs in and retries, and the search succeeds;
3. it is ended again within LOGIN_GAP: no second login, the page says the account was just logged in again;
4. wrong credentials: three captcha tries, then the EXPIRED message, and the session's state is "logged_out";
5. a session saved by hand while logged out (as login.py does) is opened by the idle worker: "held" again with no login
   and no search; the next search after the gap works.
The session's state (held / logged_out, the last login, the last call Wisers answered) is in the store's session.json,
served on the stats port for the monitor (Deyao, 2026-10-21).
"""
import json, os, sys, threading, time, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_wisers import SOLVED, FakeWisers, Store, new_ns  # noqa: E402

NS = new_ns("relogin")
ST = Store(NS)
W = FakeWisers(captcha=True)
S = W.S
STATE = "state.json"
os.environ.update(W.env(NS))
W.stale_state(NS)  # a saved session Wisers no longer accepts
sys.path.insert(0, os.path.join(HERE, "wisers"))
import relay  # noqa: E402

relay.configure(NS)
threading.Thread(target=relay.browser_worker, kwargs={"solver": lambda png: SOLVED}, daemon=True).start()


LAST = []


def run(keyword):
    crit, _ = relay.query.criteria({"q": [keyword], "period": ["today"]})
    j = relay.wait_job(relay.start_search(crit, 1, reason="test"), 300)
    LAST[:] = [j]
    return ("ok",) if j["state"] == "ok" else ("err", j["error"])


class Relogin(unittest.TestCase):
    def test_no_solver_key_fails_cleanly(self):
        """no TWOCAPTCHA_API: the solver raises a clear error before any request (no other solver is tried)"""
        import login
        from unittest import mock
        with mock.patch.dict(os.environ, {}), mock.patch.object(login.urllib.request, "urlopen", side_effect=AssertionError):
            os.environ.pop("TWOCAPTCHA_API", None)
            with self.assertRaisesRegex(RuntimeError, "TWOCAPTCHA_API not set"):
                login.solve(b"png")

    def test_all(self):
        relay.LOGIN_GAP = 0
        r = run("one")
        self.assertEqual(r[0], "ok", r)
        self.assertEqual(S["logins"], 1)
        saved = {c["name"]: c["value"] for c in ST.read_json(STATE)["cookies"]}
        self.assertIn(saved.get("sess"), S["valid"], "the new session is saved")
        st = relay.session_state()
        self.assertEqual(st["state"], "held", st)
        self.assertTrue(st["last_ok"] and st["last_login"]["ok"], st)

        S["valid"] = {"someone-else"}  # another login took the session
        r = run("two")
        self.assertEqual(r[0], "ok", r)
        self.assertEqual(S["logins"], 2)

        relay.LOGIN_GAP = 3600
        S["valid"] = {"someone-else-again"}
        r = run("three")
        self.assertEqual(r, ("err", relay.RECENT))
        self.assertEqual(S["logins"], 2, "no login inside LOGIN_GAP")

        relay.LOGIN_GAP = 0
        os.environ["WISERS_PASSWORD"] = "wrong"
        tries = S["tries"]
        r = run("four")
        self.assertEqual(r, ("err", relay.EXPIRED))
        self.assertEqual(S["tries"] - tries, 3)
        self.assertEqual((LAST[0]["outcome"], LAST[0]["charged"]), ("logged_out", "no"), "not sent to Wisers: no quota")
        st = relay.session_state()
        self.assertEqual(st["state"], "logged_out", st)
        self.assertFalse(st["last_login"]["ok"], st)

        # Deyao takes it back by hand (login.py saves a fresh session): the idle worker opens it, no login, no search
        os.environ["WISERS_PASSWORD"] = "pw-ok"
        relay.LOGIN_GAP = 3600
        S["valid"] = {"by-hand"}
        n, logins = S["searches"], S["logins"]
        ST.write_json(STATE, {"cookies": [{"name": "sess", "value": "by-hand", "domain": "127.0.0.1", "path": "/", "expires": -1,
                                           "httpOnly": False, "secure": False, "sameSite": "Lax"}], "origins": []})
        t = time.time()
        while relay.session_state()["state"] != "held" and time.time() - t < 60:
            time.sleep(0.5)
        self.assertEqual(relay.session_state()["state"], "held")
        self.assertEqual((S["searches"], S["logins"]), (n, logins), "picked up without a login or a search")
        r = run("five")
        self.assertEqual(r[0], "ok", r)
        self.assertEqual(S["logins"], logins)


if __name__ == "__main__":
    unittest.main()
