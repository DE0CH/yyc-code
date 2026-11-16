#!/usr/bin/env python3
"""WiseSearch searches as jobs (Deyao, 2026-10-20: "requests time out too fast"; "be conservative with searches").

    python3 tests/test_wisers_jobs.py

A real WisersOne search can take longer than any single request may (the page's 15 s, the tunnel's 100 s), so the relay
takes a search as a job: api/search answers within POLL_SECS (the result, or the job still running), api/job/<id> is
read again until it is done. Every search costs the account quota, so:
1. a 90 s search (the fake's slow90) works end to end, no request over 25 s; the same search asked again while it runs
   joins it, and once done it is answered from the saved response (no second Wisers search) unless fresh=1;
2. nothing repeats a search by itself: a connection that breaks after Wisers ran the search (drop…) and a search past
   WISERS_SEARCH_SECS (timeout) end as errors, counted once, never retried; a search Wisers refused because the session
   was over (a redirect to its login page, or its E40001 "no login record") was not run, so it runs once after the login;
3. the search log (api/log; searches.jsonl, the relay's own record): HKT time, who (the Access email), the reason, the
   query and options, the count, the time taken and the outcome, older lines and saved responses without a line
   included (backfill); today's and this month's searches (answers from memory not counted); /stats on the stats port.
"""
import json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_wisers import FakeWisers, Store, new_ns  # noqa: E402

W = FakeWisers(captcha=False, latency=0.3)
S = W.S


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def b64(d):
    import base64
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


TOKEN = f"{b64({'alg': 'RS256'})}.{b64({'email': 'michelle.yeoh@yyc.hk'})}.sig"  # no ACCESS_AUD here: the email is read as is


class Relay:
    def __init__(self, name, **env):
        self.tmp = tempfile.mkdtemp(prefix=f"wisers-jobs-{name}-")  # the relay's own log file only
        self.ns = new_ns(f"jobs-{name}")
        self.store = Store(self.ns)
        W.stale_state(self.ns)
        if name == "main":  # a log line in the old format (before 2026-10-20) and a saved response with no line at all
            self.store.append("searches.jsonl", json.dumps({"at": "2026-10-16T10:55:19", "keyword": "李家超", "page": 1, "cached": False,
                                                            "from": "2026-10-10", "to": "2026-10-16", "type": "all", "ok": True, "total": "39911",
                                                            "raw": "20261016-105519-李家超-1.json", "secs": 36.1}, ensure_ascii=False))
            for name_, kw, at in (("20261016-105519-李家超-1.json", "李家超", "2026-10-16T10:55:19+0000"),
                                  ("20261012-090000-銀河通用-1.json", "銀河通用", "2026-10-12T09:00:00+0000")):
                self.store.write_json(f"raw/{name_}", {"at": at, "request": {"path": "search/searchresult", "criteria": {
                    "keyword": kw, "dateRangePeriod": "custom", "dateFrom": "2026-10-10", "dateTo": "2026-10-16",
                    "startIdx": 1, "pubType": "print", "sortOrder": "DESC:date"}},
                    "response": {"g0": {"data": {"code": "50000", "data": {"items": [], "total": "7"}}}}})
        self.port, self.stats = free_port(), free_port()
        e = dict(os.environ, **W.env(self.ns), PYTHONUNBUFFERED="1", WISERS_PROBE_SECS="0", WISERS_LOGIN_GAP="0",
                 WISERS_STATS_PORT=str(self.stats), **env)
        self.log = open(os.path.join(self.tmp, "relay.log"), "w")  # noqa: SIM115
        self.p = subprocess.Popen([sys.executable, os.path.join(HERE, "wisers", "relay.py"), "--port", str(self.port),
                                   "--prefix", "/app/wisers/"], env=e, stdout=self.log, stderr=subprocess.STDOUT)
        t = time.time()
        while time.time() - t < 60:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz", timeout=2)
                break
            except Exception:
                time.sleep(0.2)

    def get(self, path, timeout=40):
        t0 = time.time()
        path = urllib.parse.quote(path, safe="/?&=%+:")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/app/wisers/api/{path}",
                                     headers={"Cf-Access-Jwt-Assertion": TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read()), time.time() - t0
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), time.time() - t0

    def run(self, qs, limit=400):
        """a search to its end, as the page does it: start, then read the job until it is done; every request's time"""
        code, d, secs = self.get("search?" + qs)
        times = [secs]
        t = time.time()
        while d.get("job", {}).get("state") == "running" and time.time() - t < limit:
            code, d, secs = self.get(f"job/{d['job']['id']}?wait=20")
            times.append(secs)
        return d, times

    def stop(self):
        self.p.terminate()
        self.p.wait(timeout=120)


R = None


def setUpModule():
    global R
    R = Relay("main", WISERS_SEARCH_SECS="150")


def tearDownModule():
    R.stop()


class Jobs(unittest.TestCase):
    def test_1_slow_search(self):
        """a 90 s search: answered running at once, joined while running, done after 90 s; no request over 25 s"""
        n0 = S["searches"]
        t0 = time.time()
        code, d, secs = R.get("search?q=slow90+港股&period=7&reason=" + urllib.parse.quote("Claude test: slow search"))
        self.assertEqual(code, 200)
        self.assertLess(secs, 25)
        self.assertEqual(d["job"]["state"], "running", d)
        jid = d["job"]["id"]
        code, d2, _ = R.get("search?q=slow90+港股&period=7")  # the same search again (a reload): joins the job
        self.assertEqual(d2["job"]["id"], jid)
        times = []
        while d["job"]["state"] == "running" and time.time() - t0 < 200:
            code, d, secs = R.get(f"job/{jid}?wait=20")
            self.assertEqual(code, 200, d)
            self.assertIn("elapsed", d["job"])
            times.append(secs)
        self.assertEqual(d["job"]["state"], "ok", d)
        self.assertGreaterEqual(time.time() - t0, 89)
        self.assertLess(max(times), 25, times)
        self.assertEqual(len(d["result"]["items"]), 3)
        self.assertEqual(S["searches"] - n0, 1, "one Wisers search for both requests")
        # done: the same search is answered from the saved response, no new Wisers search
        code, d3, secs = R.get("search?q=slow90+港股&period=7")
        self.assertEqual(d3["job"]["state"], "ok")
        self.assertTrue(d3["job"]["cached"])
        self.assertLess(secs, 5)
        self.assertEqual(S["searches"] - n0, 1)

    def test_2_fresh(self):
        n0 = S["searches"]
        d, _ = R.run("q=fresh1&period=today")
        self.assertEqual(d["job"]["state"], "ok")
        self.assertFalse(d["job"]["cached"])
        d, _ = R.run("q=fresh1&period=today")
        self.assertTrue(d["job"]["cached"])
        self.assertEqual(S["searches"] - n0, 1)
        d, _ = R.run("q=fresh1&period=today&fresh=1")
        self.assertFalse(d["job"]["cached"])
        self.assertEqual(S["searches"] - n0, 2, "fresh=1: a new search")

    def test_3_broken_connection_is_not_retried(self):
        n0 = S["searches"]
        d, _ = R.run("q=drop1&period=today")
        self.assertEqual(d["job"]["state"], "err", d)
        self.assertEqual(d["failure"]["charged"], "maybe")
        time.sleep(3)
        self.assertEqual(S["searches"] - n0, 1, "Wisers ran it once; the relay did not run it again")
        d, _ = R.run("q=drop1&period=today")  # asked again by hand: a failed search is not kept, so it is a new search
        self.assertEqual(S["searches"] - n0, 2)

    def test_4_expired_session_runs_once_after_login(self):
        n0, l0 = S["searches"], S["logins"]
        d, _ = R.run("q=warm&period=today")  # the worker is signed in
        self.assertEqual(d["job"]["state"], "ok")
        n0 = S["searches"]
        S["valid"] = {"someone-else"}  # another login took the session: the API answers a redirect to the login page
        d, _ = R.run("q=after-expiry&period=today")
        self.assertEqual(d["job"]["state"], "ok", d)
        self.assertEqual(S["searches"] - n0, 1)
        self.assertGreater(S["logins"], l0)
        l1 = S["logins"]
        d, _ = R.run("q=relog1&period=today")  # Wisers answers E40001 (no login record): not run, so run after the login
        self.assertEqual(d["job"]["state"], "ok", d)
        self.assertEqual(S["searches"] - n0, 2)
        self.assertEqual(S["logins"], l1 + 1)

    def test_5_timeout_is_not_retried(self):
        r2 = Relay("short", WISERS_SEARCH_SECS="4")
        try:
            n0 = S["searches"]
            d, _ = r2.run("q=slow8&period=today")
            self.assertEqual(d["job"]["state"], "err", d)
            self.assertEqual(d["failure"]["outcome"], "timeout")
            self.assertEqual(d["failure"]["charged"], "maybe")
            time.sleep(6)
            self.assertEqual(S["searches"] - n0, 1, "the search Wisers finished after our timeout is not run again")
        finally:
            r2.stop()

    def test_6_log(self):
        R.run("q=log1&period=30&mt=web&author=記者甲&reason=" + urllib.parse.quote("Claude test: log"))
        R.run("q=log1&period=30&mt=web&author=記者甲")  # from memory
        code, d, _ = R.get("log")
        self.assertEqual(code, 200)
        rows = d["rows"]
        mine = [r for r in rows if r["keyword"] == "log1"]
        self.assertEqual(len(mine), 2, mine)
        first = [r for r in mine if not r["cached"]][0]
        self.assertEqual(first["who"], "michelle.yeoh@yyc.hk")
        self.assertEqual(first["reason"], "Claude test: log")
        self.assertEqual(first["outcome"], "ok")
        self.assertEqual(first["total"], "45")
        self.assertTrue(first["at"].endswith("+08:00"), first["at"])
        self.assertEqual(first["options"].get("pubType"), "web")
        self.assertNotIn("startIdx", first["options"], "defaults are left out")
        self.assertEqual(first["options"].get("author"), "記者甲")
        self.assertIn("secs", first)
        old = [r for r in rows if r["keyword"] == "李家超"]
        self.assertEqual(len(old), 1, "an old line and its saved response are one search")
        self.assertEqual(old[0]["at"], "2026-10-16T18:55:19+08:00", "old lines are UTC, shown in HKT")
        self.assertIsNone(old[0]["who"])
        raw_only = [r for r in rows if r["keyword"] == "銀河通用"]
        self.assertEqual(len(raw_only), 1, "a saved response with no log line is in the log")
        self.assertEqual(raw_only[0]["options"].get("pubType"), "print")
        failed = [r for r in rows if r["keyword"] == "drop1"]
        if failed:
            self.assertEqual(failed[0]["outcome"], "neterr")
        s = d["summary"]
        counted = [r for r in rows if not r["cached"] and r["at"][:10] == time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))]
        self.assertEqual(s["today"], len(counted))
        self.assertGreaterEqual(s["month"], s["today"])
        with urllib.request.urlopen(f"http://127.0.0.1:{R.stats}/stats", timeout=10) as r:
            st = json.loads(r.read())
        self.assertEqual(st["today"], s["today"])
        self.assertEqual(st["month"], s["month"])
        self.assertNotIn("rows", st, "the stats port gives counts only")


if __name__ == "__main__":
    unittest.main()
