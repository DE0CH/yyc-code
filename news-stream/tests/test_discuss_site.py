#!/usr/bin/env python3
"""The whole 香港討論區 site (Deyao, 2026-10-15: "capture everything just like LIHKG"), on a throwaway database
(TEST_DATABASE_URL), no network (a fake discuss.com.hk), the model stubbed:

Capture (replies_fetch.py discuss):
1. every board on the site (the archiver's forum index) is listed through its archiver pages (50 threads a page with
   their reply counts, latest reply first), paged on while most of a page changed; thread ids are sequential
   site-wide, so an id between the last one seen and the newest listed that no listing showed is read directly. The
   first listing of the site only records the reply counts (a baseline: a thread is read once it grows) and starts
   the id walk; threads newer than that are read at once, newest first.
2. a board whose first page didn't change is listed less often (its interval doubles up to BOARD_MAX_EVERY); one
   that changed is listed every run; the boards listed longest ago go first, and listing stops at half the run.
3. two-pass (twopass.py, as LIHKG): a thread's first full read marks it captured; a thread whose second scoring is
   due (post time + 30 min) is read again directly then (its listing count unchanged: marked re-read without a
   request).
4. polite: a pause of DISCUSS_GAP between requests; a walled IP (HTTP 202 reCAPTCHA / 403) moves to a new sticky
   session, at most DISCUSS_WALLS of them a page; a page still walled ends the run (27 Oct - 17 Nov 2026 every
   residential IP was walled: 8 sessions a page, ~0.7 GB of yycstream a day, no reply read). On the ISP IP
   (discuss_route) each request holds the ISP lock, its bytes count as the ISP IP's, and a wall ends the run at once.

Scoring (streams.py + twopass.py):
5. a 香港討論區 thread is one unit, the opening post and its replies (ai_spend purpose scoring_discuss); its reply
   rows are never scored on their own; scored again at post time + 30 min once re-read.
6. one item in 串流 and one Telegram message per thread and stream.
7. the item page lists every reply with the divider 「以上回覆已納入評分（time）」.

Search (sources.discuss_search, kept beside the whole site):
8. on the replies job's route (discuss_route; on yycstream a 403/202 moves to a new session), and one failing query
   doesn't lose the others.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_discuss_site.py
"""
import json, os, re, sys, tempfile, time, unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["RESI_PROXY"] = "http://u:p@proxy.test:12321"
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, item as itempage, replies_fetch, sources, streams, telegram_alerts, titles, twopass  # noqa: E402

HKT = corpus.HKT
STREAMS = app.STREAMS
CALLS = []
replies_fetch.DISCUSS_GAP = 0  # the tests don't wait between fake requests


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000):
    """every post scores CEHK 90 when 李家超 is in its block, else nothing"""
    CALLS.append((purpose, prompt))
    body = prompt.split("Posts:\n", 1)[1]
    out = []
    for b in re.split(r"(?m)^(?=\d+\. \[)", body):
        m = re.match(r"(\d+)\. ", b)
        if m:
            st = [{"id": "CEHK", "score": 90, "reason": "特首", "category": "言論及表態"}] if "李家超" in b else []
            out.append({"i": int(m.group(1)), "sentiment": "neutral", "labels": [], "streams": st})
    return {"posts": out}


import stage2  # noqa: E402
ai.jsonc = stage2.agree(fake_jsonc, CALLS)  # stage 2 answers as stage 1 (tests/stage2.py)
titles.ensure = lambda posts: None


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def url(tid):
    return f"https://www.discuss.com.hk/archiver/?tid-{tid}.html"


class Site:
    """a fake discuss.com.hk: boards {fid: name}, threads {tid: {fid, title, posts: [(author, text, time)], bump}};
    a board lists its threads by latest reply (bump), 50 to an archiver page"""

    def __init__(self):
        self.boards, self.threads, self.requests, self.tick = {}, {}, [], 0
        self.gated, self.agreed = {}, set()  # boards behind the 18+ gambling notice {fid: catid}; catids agreed

    def thread(self, tid, fid, title, n, start=None, words=""):
        start = start or datetime.now(HKT) - timedelta(minutes=5)
        posts = [("樓主", f"開帖內容 {words}", start)] + [(f"u{k}", f"回覆{k} {words}", start + timedelta(seconds=k))
                                                        for k in range(2, n + 2)]
        self.tick += 1
        self.threads[tid] = {"fid": fid, "title": title, "posts": posts, "bump": self.tick}

    def reply(self, tid, n=1, words=""):
        t = self.threads[tid]
        for _ in range(n):
            k = len(t["posts"]) + 1
            t["posts"].append((f"u{k}", f"回覆{k} {words}", datetime.now(HKT)))
        self.tick += 1
        t["bump"] = self.tick

    def get(self, u):
        self.requests.append(u)
        if u == "https://www.discuss.com.hk/archiver/":
            return "<ul>" + "".join(f'<li><a href="archiver/?fid-{f}.html">{n}</a></li>' for f, n in self.boards.items()) + "</ul>"
        m = re.match(r"https://www\.discuss\.com\.hk/archiver/\?fid-(\d+)(?:-page-(\d+))?\.html$", u)
        if m:
            fid, page = int(m.group(1)), int(m.group(2) or 1)
            rows = sorted((t for t in self.threads.items() if t[1]["fid"] == fid), key=lambda t: -t[1]["bump"])
            rows = rows[(page - 1) * 50:page * 50]
            return "<ul>" + "".join(f'<li><a href="archiver/?tid-{tid}.html">{t["title"]}</a> <em>({len(t["posts"]) - 1} 篇回覆)</em></li>'
                                    for tid, t in rows) + "</ul>"
        m = re.match(r"https://www\.discuss\.com\.hk/viewthread\.php\?action=printable&tid=(\d+)$", u)
        if m:
            t = self.threads.get(int(m.group(1)))
            if not t:
                return "<html>指定的主題不存在</html>"
            cat = self.gated.get(t["fid"])
            if cat and cat not in self.agreed:
                return (f'<form action="agreement18.php" method="post"><input type="hidden" name="catid" value="{cat}">'
                        '<input type="hidden" name="adult" value="agreed"><button>我知道了</button></form>')
            hr = '<hr noshade size="2" width="100%" color="#808080">'
            segs = "".join(f'{hr}<b>作者: </b> {a} &nbsp; <b>時間: </b> {w.strftime("%Y-%m-%d %H:%M")}<br /><br />{x}'
                           for a, x, w in t["posts"][:100])
            return f'<b>標題: </b>{t["title"]}<b>{segs}<hr noshade size="2" width="100%" color="#000000">'
        m = re.match(r"https://m\.discuss\.com\.hk/index\.php\?action=thread&tid=(\d+)&page=(\d+)$", u)
        if m:  # the mobile thread page: 15 posts a page
            t, page = self.threads[int(m.group(1))], int(m.group(2))
            last = (len(t["posts"]) + 14) // 15
            posts = "".join(f'<div class="post" id="post_pid{k}"><div data-floor="{k}"></div><a class="host-name" href="#">{a}</a>'
                            f'<div class="info-date">{w.strftime("%Y-%m-%d %H:%M")}</div><div class="post-content">{x}</div>'
                            '<div class="post-tool"></div></div>'
                            for k, (a, x, w) in enumerate(t["posts"], 1) if (page - 1) * 15 < k <= page * 15)
            return (f'<meta property="og:title" content="{t["title"]} - 香港討論區 discuss.com.hk">'
                    f'<a class="thread-lastpage-btn" href="x?page={last}">尾頁</a>'
                    f'<a href="post.php?action=post&amp;do=reply&amp;fid={t["fid"]}">回覆</a>{posts}')
        raise AssertionError("unexpected request " + u)


class Px:
    """replies_fetch.Proxied over the fake site"""
    direct = False

    def __init__(self, site):
        self.site, self.bytes = site, 0

    def get(self, u, headers=None, walled=None):
        return self.site.get(u)

    def post(self, u, data, walled=None):
        self.site.requests.append(u)
        assert u == "https://www.discuss.com.hk/agreement18.php" and data["adult"] == "agreed"
        self.site.agreed.add(int(data["catid"]))
        return "ok"

    def account(self):
        pass


def run(site):
    with mock.patch.object(replies_fetch, "Proxied", lambda name, direct=False, isp=False: Px(site)):
        return replies_fetch.run("discuss", 3 * 3600)


def listed(site, fid):
    return [u for u in site.requests if re.search(rf"\?fid-{fid}(-page-\d+)?\.html$", u)]


def read(site):
    return [int(m.group(1)) for u in site.requests for m in [re.search(r"printable&tid=(\d+)$", u)] if m]


class Capture(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'replies:%%'")
        corpus.db().execute("DELETE FROM two_pass")
        corpus.db().execute("DELETE FROM posts")

    def test_1_every_board_baseline_then_new_threads_and_gaps(self):
        s = Site()
        s.boards = {54: "香港及世界新聞", 62: "吹水閒聊"}
        s.thread(900, 54, "舊帖", 3)
        s.thread(901, 62, "舊帖二", 2)
        run(s)
        self.assertTrue(listed(s, 54) and listed(s, 62))  # every board of the forum index
        self.assertEqual(read(s), [])  # the first listing is a baseline
        self.assertEqual(corpus.kv_get("replies:discuss:max_id"), 901)
        s.requests.clear()
        s.thread(902, 54, "新帖", 1)
        s.thread(903, 99, "隱藏版新帖", 0)  # a board not in the index: only the id walk finds it
        s.thread(904, 62, "最新帖", 2)
        s.reply(900, 2)  # an old thread grows
        res = run(s)
        self.assertEqual(read(s)[:3], [904, 903, 902])  # new threads at once, newest first
        self.assertIn(900, read(s))  # the old thread, once it grew: read in full (its opening post too)
        self.assertNotIn(901, read(s))
        self.assertEqual(corpus.kv_get("replies:discuss:max_id"), 904)
        got = corpus.ids_for_urls([url(t) for t in (900, 902, 903, 904)])
        self.assertEqual(len(got), 4)
        n = corpus.db().execute("SELECT count(*) FROM posts WHERE thread=%s", (url(900),)).fetchone()[0]
        self.assertEqual(n, 5)  # all 5 replies of the old thread
        self.assertEqual(res["errors"], 0)
        s.requests.clear()
        run(s)
        self.assertEqual(read(s), [])  # nothing grew: nothing read

    def test_1b_gambling_notice(self):
        """the betting boards' threads (about 1 in 8 new thread ids, 2026-10-15) sit behind an 18+ notice: the job
        agrees to it (its form, once per session) and reads the thread"""
        s = Site()
        s.boards = {18: "香港賽馬"}
        s.gated = {18: 152}
        s.thread(900, 18, "舊帖", 1)
        run(s)
        s.thread(901, 18, "新帖 賽馬", 2)
        run(s)
        self.assertIn("https://www.discuss.com.hk/agreement18.php", s.requests)
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM posts WHERE thread=%s", (url(901),)).fetchone()[0], 2)

    def test_2_quiet_boards_listed_less_often(self):
        s = Site()
        s.boards = {54: "香港及世界新聞", 62: "吹水閒聊"}
        s.thread(900, 54, "帖", 1)
        s.thread(901, 62, "帖", 1)
        run(s)
        clock = [time.time()]
        with mock.patch.object(replies_fetch.time, "time", lambda: clock[0]):
            counts = []
            for k in range(8):  # 8 runs 5 min apart; board 54 busy, board 62 quiet
                clock[0] += 300
                s.requests.clear()
                s.reply(900)
                run(s)
                counts.append((len(listed(s, 54)) > 0, len(listed(s, 62)) > 0))
        self.assertTrue(all(a for a, b in counts))
        self.assertLess(sum(b for a, b in counts), 6)  # the quiet board backs off
        s.requests.clear()
        s.reply(901)
        with mock.patch.object(replies_fetch.time, "time", lambda: clock[0] + 2 * replies_fetch.BOARD_MAX_EVERY):
            run(s)  # listed again within BOARD_MAX_EVERY: its new reply is read
        self.assertIn(901, read(s))

    def test_2b_listing_takes_half_the_run(self):
        s = Site()
        s.boards = {54: "香港及世界新聞", 62: "吹水閒聊", 57: "金融財經投資區"}
        corpus.kv_set("replies:discuss:board:62", {"every": 1, "since": 0, "at": 1, "sig": "x"})
        corpus.kv_set("replies:discuss:board:57", {"every": 1, "since": 0, "at": 2, "sig": "x"})
        corpus.kv_set("replies:discuss:board:54", {"every": 1, "since": 0, "at": 3, "sig": "x"})
        clock = iter(range(10 ** 6))
        with mock.patch.object(replies_fetch, "RUN_SECONDS", 4), \
                mock.patch.object(replies_fetch.time, "time", lambda: 1000 + next(clock)):
            run(s)
        boards = [int(re.search(r"fid-(\d+)", u).group(1)) for u in s.requests if "?fid-" in u]
        self.assertEqual(boards[0], 62)  # listed longest ago first
        self.assertNotIn(54, boards)  # past half the run: waits for the next one

    def test_2c_busy_boards_before_overdue_quiet_ones(self):
        """capture lag (2026-10-15): the boards were listed strictly longest-ago first, so with 449 boards every board,
        the busy ones too, came round once in 48-71 min and their new replies waited that long (median reply lag 32 min,
        31 % over an hour). A board is listed by how overdue it is against its own interval: a busy board (every run)
        listed 10 min ago goes before a quiet board (every 12 runs) listed 61 min ago"""
        s = Site()
        s.boards = {54: "香港及世界新聞", 62: "吹水閒聊", 57: "金融財經投資區"}
        t0 = 10 ** 6
        corpus.kv_set("replies:discuss:board:62", {"every": 12, "since": 11, "at": t0 - 3660, "sig": "x"})
        corpus.kv_set("replies:discuss:board:57", {"every": 12, "since": 11, "at": t0 - 3700, "sig": "x"})
        corpus.kv_set("replies:discuss:board:54", {"every": 1, "since": 0, "at": t0 - 600, "sig": "x"})
        clock = iter(range(10 ** 6))
        with mock.patch.object(replies_fetch, "RUN_SECONDS", 4), \
                mock.patch.object(replies_fetch.time, "time", lambda: t0 + next(clock)):
            run(s)
        boards = [int(re.search(r"fid-(\d+)", u).group(1)) for u in s.requests if "?fid-" in u]
        self.assertEqual(boards[0], 54)

    def test_2d_a_long_thread_stops_at_the_run_deadline(self):
        """a thread's first read of hundreds of pages ran past the run's time (RUN_SECONDS) and the CronJob's deadline
        killed the run (Failed jobs 2026-10-15, its listings and id walk lost): a thread is read up to the run's
        deadline and carries on in the next run"""
        s = Site()
        s.boards = {54: "香港及世界新聞"}
        s.thread(900, 54, "舊帖", 1)
        run(s)
        s.thread(950, 54, "長帖", 400)
        clock = [10 ** 6]

        def tick():
            clock[0] += 1
            return clock[0]
        with mock.patch.object(replies_fetch, "RUN_SECONDS", 20), mock.patch.object(replies_fetch.time, "time", tick):
            start = clock[0]
            run(s)
            self.assertLess(clock[0] - start, 40)  # stopped near the deadline, not after all 20 pages
            n = corpus.db().execute("SELECT count(*) FROM posts WHERE thread=%s", (url(950),)).fetchone()[0]
            self.assertLess(n, 400)
            for _ in range(10):
                run(s)
        n = corpus.db().execute("SELECT count(*) FROM posts WHERE thread=%s", (url(950),)).fetchone()[0]
        self.assertEqual(n, 400)  # every reply over the later runs

    def test_3_paging(self):
        s = Site()
        s.boards = {54: "香港及世界新聞"}
        for t in range(1000, 1120):
            s.thread(t, 54, f"帖{t}", 1)
        run(s)
        s.requests.clear()
        for t in range(1000, 1005):
            s.reply(t)
        run(s)
        self.assertEqual(len(listed(s, 54)), 1)  # page 1 mostly unchanged: no paging
        s.requests.clear()
        for t in range(1000, 1080):
            s.reply(t)
        run(s)
        self.assertGreaterEqual(len(listed(s, 54)), 2)  # page 1 all changed: page on
        self.assertEqual(sorted(set(read(s))), list(range(1000, 1080)))

    def test_4_two_pass(self):
        s = Site()
        s.boards = {54: "香港及世界新聞"}
        s.thread(900, 54, "舊帖", 1)
        run(s)
        s.thread(950, 54, "新帖", 1)
        s.requests.clear()
        run(s)
        r = corpus.db().execute("SELECT reading_at, captured_at FROM two_pass WHERE url=%s", (url(950),)).fetchone()
        self.assertTrue(r and r[0] and r[1])  # read in full at capture
        due = datetime.now(HKT) - timedelta(minutes=1)
        corpus.db().execute("UPDATE two_pass SET first_at=%s, due_at=%s WHERE url=%s", (due, due, url(950)))
        s.requests.clear()
        s.threads[950]["posts"].append(("u3", "回覆3", datetime.now(HKT)))  # a reply the listing doesn't show yet
        run(s)
        self.assertIn(950, read(s))  # read directly for its second scoring
        self.assertEqual(twopass.due_for_fetch("https://www.discuss.com.hk/%"), [])
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM posts WHERE thread=%s", (url(950),)).fetchone()[0], 2)
        s.thread(960, 54, "再新帖", 1)
        s.requests.clear()
        run(s)
        corpus.db().execute("UPDATE two_pass SET first_at=%s, due_at=%s WHERE url=%s", (due, due, url(960)))
        s.requests.clear()
        run(s)  # listed with the same count: no new reply, marked re-read without reading it
        self.assertNotIn(960, read(s))
        self.assertEqual(twopass.due_for_fetch("https://www.discuss.com.hk/%"), [])

    def test_5_polite_and_sticky(self):
        self.assertGreaterEqual(replies_fetch.DISCUSS_GAP_DEFAULT, 0.5)
        px = replies_fetch.Proxied("discuss")
        answers = [(403, "Forbidden"), (202, '請確認身份 g-recaptcha'), (200, "<html>ok</html>")]
        sessions = []

        def fetch(u, h):
            sessions.append(px.proxy())
            return answers.pop(0)
        px.fetch = fetch
        slept = []
        with mock.patch.object(replies_fetch, "DISCUSS_GAP", 0.7), \
                mock.patch.object(replies_fetch.time, "sleep", lambda s: slept.append(s)):
            self.assertEqual(px.get("https://www.discuss.com.hk/x", walled=replies_fetch.discuss_walled), "<html>ok</html>")
        self.assertEqual(len(set(sessions)), 3)  # a new sticky session after each wall
        self.assertIn(0.7, slept)
        # a thread id that doesn't exist (the id walk meets deleted ones) answers 404: an answer, not a wall
        answers[:] = [(404, "<html>not found</html>")]
        sessions.clear()
        with mock.patch.object(replies_fetch.time, "sleep", lambda s: None):
            self.assertEqual(px.get("https://www.discuss.com.hk/viewthread.php?action=printable&tid=1",
                                    walled=replies_fetch.discuss_walled), "<html>not found</html>")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(replies_fetch.discuss_printable("<html>not found</html>"), (None, []))
        px.bytes = 1234
        px.account()  # proxy bytes by HKT hour too (kv proxy:bytes:discuss:<YYYY-MM-DDTHH>), beside day/month/total
        self.assertEqual(corpus.kv_get("proxy:bytes:discuss:" + replies_fetch.now().strftime("%Y-%m-%dT%H")), 1234)


    def test_5e_walled_everywhere_stops_the_run(self):
        """every residential IP walled: a page tries DISCUSS_WALLS sessions, then the run stops (no other board or
        thread asked) and the threads wait"""
        px = replies_fetch.Proxied("discuss")
        tries = []

        def fetch(u, h):
            tries.append(px.proxy())
            return 202, "請確認身份 g-recaptcha"
        px.fetch = fetch
        with mock.patch.object(replies_fetch.time, "sleep", lambda s: None):
            with self.assertRaises(replies_fetch.Wall):
                px.get("https://www.discuss.com.hk/archiver/?fid-54.html", walled=replies_fetch.discuss_walled)
        self.assertEqual(len(tries), replies_fetch.DISCUSS_WALLS)
        self.assertEqual(len(set(tries)), replies_fetch.DISCUSS_WALLS)
        calls = []

        class Walled(Px):
            def get(self, u, headers=None, walled=None):
                calls.append(u)
                raise replies_fetch.Wall(u)
        corpus.kv_set("replies:discuss:boards", {"at": time.time(), "boards": {"54": "a", "62": "b", "70": "c"}})
        corpus.kv_set("replies:discuss:pending", {"900": {"replies": 3}, "901": {"replies": 4}})
        with mock.patch.object(replies_fetch, "Proxied", lambda name, direct=False, isp=False: Walled(Site())), \
                mock.patch.object(replies_fetch, "discuss_route", lambda: "hk"):
            res = replies_fetch.run("discuss", 3600)
        self.assertEqual(len(calls), 1)  # the first board's wall: nothing else asked
        self.assertTrue(res["blocked"])
        self.assertEqual(set(corpus.kv_get("replies:discuss:pending")), {"900", "901"})

    def test_5f_isp_route(self):
        """the ISP IP (discuss_route "isp"): every request through ISP_PROXY under the ISP lock ('yyc youtube', one
        client at a time), bytes counted as the ISP IP's (not yycstream's), a wall raises at once (one fixed IP)"""
        held = []

        class Lock:
            def __enter__(self):
                held.append(True)

            def __exit__(self, *a):
                held.append(False)
        made = []

        class Sess:
            def __init__(self, **kw):
                made.append(self)
                self.answers = [(200, "<html>ok</html>"), (202, "請確認身份 g-recaptcha")]

            def get(self, u, headers=None, proxies=None, timeout=None, allow_redirects=True):
                assert held and held[-1], "a request outside the ISP lock"
                assert proxies["https"] == "http://u:p@isp.test:12323"
                st, tx = self.answers.pop(0)
                return mock.Mock(status_code=st, text=tx, content=tx.encode(), download_size=1000, headers={})
        import sites_fetch
        from curl_cffi import requests as cr
        day = replies_fetch.now().strftime("%Y-%m-%d")
        resi_before = corpus.kv_get(f"proxy:bytes:discuss:{day}", 0)
        isp_before = corpus.kv_get(f"isp:bytes:{day}", 0)
        with mock.patch.dict(os.environ, {"ISP_PROXY": "http://u:p@isp.test:12323"}), \
                mock.patch.object(cr, "Session", Sess), mock.patch.object(sites_fetch, "isp_lock", lambda: Lock()), \
                mock.patch.object(replies_fetch.time, "sleep", lambda s: None):
            px = replies_fetch.Proxied("discuss", isp=True)
            self.assertEqual(px.get("https://www.discuss.com.hk/a", walled=replies_fetch.discuss_walled), "<html>ok</html>")
            with self.assertRaises(replies_fetch.Blocked):
                px.get("https://www.discuss.com.hk/b", walled=replies_fetch.discuss_walled)
            px.account()
        self.assertEqual(len(made), 1)
        self.assertEqual(held, [True, False, True, False])
        self.assertEqual(corpus.kv_get(f"proxy:bytes:discuss:{day}", 0), resi_before)
        self.assertEqual(corpus.kv_get(f"isp:bytes:{day}", 0) - isp_before, 2 * (1000 + 800))

    def test_5c_one_connection_for_the_run(self):
        """each request opened a new connection through the proxy (CONNECT + TLS: 1.25 s a request measured in the
        cluster, 2026-10-15, against 0.43 s on a kept-alive connection): requests share one session, its cookies (the
        18+ notice) too, and a new sticky IP gets a new one"""
        made = []

        class Sess:
            def __init__(self, **kw):
                self.calls = []
                made.append(self)

            def get(self, u, headers=None, proxies=None, timeout=None, allow_redirects=True):
                self.calls.append(("get", u, proxies))
                return mock.Mock(status_code=200, text="<html>ok</html>", content=b"<html>ok</html>", download_size=15,
                                 headers={})

            def post(self, u, data=None, headers=None, proxies=None, timeout=None, allow_redirects=True):
                self.calls.append(("post", u, proxies))
                return mock.Mock(status_code=302, text="", content=b"", download_size=0, headers={})
        from curl_cffi import requests as cr
        with mock.patch.object(cr, "Session", Sess), mock.patch.object(replies_fetch.time, "sleep", lambda s: None):
            px = replies_fetch.Proxied("discuss")
            px.get("https://www.discuss.com.hk/a")
            px.post("https://www.discuss.com.hk/agreement18.php", {"catid": 1, "adult": "agreed"})
            px.get("https://www.discuss.com.hk/b", headers={"User-Agent": replies_fetch.MUA})
            self.assertEqual(len(made), 1)
            self.assertEqual([c[0] for c in made[0].calls], ["get", "post", "get"])
            self.assertTrue(all(c[2] and c[2]["https"] == px.proxy() for c in made[0].calls))
            px.rotate()
            px.get("https://www.discuss.com.hk/c")
            self.assertEqual(len(made), 2)

    def test_5d_proxy_out_through_curl(self):
        """the kept-alive session is curl's: IPRoyal's 402 reads 'CONNECT tunnel failed, response 402' there (LIHKG's
        proxied path already went through curl and never saw it): still a stop, not a retry"""
        px = replies_fetch.Proxied("discuss")
        tries = []

        def fetch(u, h):
            tries.append(u)
            raise ConnectionError("Failed to perform, curl: (7) CONNECT tunnel failed, response 402. See https://curl.se/")
        px.fetch = fetch
        with mock.patch.object(replies_fetch.time, "sleep", lambda s: None):
            with self.assertRaises(replies_fetch.ProxyOut):
                px.get("https://www.discuss.com.hk/x")
        self.assertEqual(len(tries), 1)

    def test_5b_proxy_out_of_traffic_stops_the_run(self):
        """IPRoyal answers 402 Payment Required when the sub-user's traffic is used up: the run stops at once (no
        retries, no new sessions) and its status says so (the monitor shows it; 2026-10-15 yycstream ran out)"""
        px = replies_fetch.Proxied("discuss")
        tries = []

        def fetch(u, h):
            tries.append(u)
            raise OSError("Tunnel connection failed: 402 Payment Required")
        px.fetch = fetch
        with mock.patch.object(replies_fetch.time, "sleep", lambda s: None):
            with self.assertRaises(replies_fetch.ProxyOut):
                px.get("https://www.discuss.com.hk/archiver/")
        self.assertEqual(len(tries), 1)
        s = Site()
        s.boards = {54: "香港及世界新聞", 62: "吹水閒聊"}
        calls = []

        class Out(Px):
            def get(self, u, headers=None, walled=None):
                calls.append(u)
                raise replies_fetch.ProxyOut("402")
        corpus.kv_set("replies:discuss:boards", {"at": time.time(), "boards": {"54": "a", "62": "b"}})
        with mock.patch.object(replies_fetch, "Proxied", lambda name, direct=False, isp=False: Out(s)):
            res = replies_fetch.run("discuss", 3600)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res["proxy_out"], True)
        self.assertIsNone(corpus.kv_get(replies_fetch.lihkg_api.K_BLOCKED))


class Scoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.kv_set("scoring", {"from_id": 0, "comments": False, "since": "2026-10-14T10:00:00+08:00"})

    def setUp(self):
        CALLS.clear()
        corpus.db().execute("DELETE FROM posts")
        corpus.db().execute("DELETE FROM two_pass")
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'replies:%%'")

    def run_at(self, t):
        with mock.patch.object(streams, "clock", Clock(t)):
            return streams.run_once(STREAMS)

    def thread_row(self, tid, published, title=None):
        item = {"url": url(tid), "title": title or f"帖{tid} 李家超", "text": "開帖內容", "sid": "discuss",
                "source": "香港討論區", "section": "香港及世界新聞", "published": published}
        corpus.ingest([item])
        corpus.db().execute("UPDATE posts SET first_run=NULL")
        return item

    def replies(self, item, rng, published, words=""):
        corpus.add_comments(item, [{"id": k, "author": f"u{k}", "text": f"回覆{k} {words}", "published": published}
                                   for k in rng])

    def test_6_unit_two_passes(self):
        t = datetime.now(HKT).replace(microsecond=0)
        pub = t - timedelta(minutes=5)
        item = self.thread_row(4001, pub.isoformat())
        twopass.reading(item["url"])
        self.assertEqual(self.run_at(t), 0)  # mid-read: waits
        self.replies(item, range(2, 4), (pub + timedelta(minutes=1)).isoformat(), "支持")
        twopass.captured(item["url"])
        self.assertEqual(self.run_at(t), 1)  # the thread only
        purpose, prompt = CALLS[-1]
        self.assertEqual(purpose, "scoring_discuss")
        self.assertIn("回覆3 支持", prompt)
        self.replies(item, range(4, 5), (pub + timedelta(minutes=20)).isoformat(), "反對")
        mark = pub + timedelta(minutes=30)
        twopass.fetched(item["url"], mark + timedelta(minutes=1))
        self.assertEqual(self.run_at(mark + timedelta(minutes=2)), 1)
        self.assertIn("回覆4 反對", CALLS[-1][1])
        self.assertEqual(self.run_at(mark + timedelta(hours=2)), 0)  # never a third time

    def test_7_one_item_one_message_and_replies_not_alone(self):
        t = datetime.now(HKT).replace(microsecond=0)
        corpus.kv_set("telegram_sent", {"CEHK": corpus.db().execute("SELECT coalesce(max(id), 0) FROM stream_items").fetchone()[0]})
        pub = t - timedelta(minutes=5)
        a = self.thread_row(4101, pub.isoformat())
        self.assertEqual(self.run_at(t), 1)
        self.replies(a, range(2, 4), (pub + timedelta(minutes=10)).isoformat(), "李家超")
        twopass.fetched_all("https://www.discuss.com.hk/%", pub + timedelta(minutes=31))
        self.assertEqual(self.run_at(pub + timedelta(minutes=32)), 1)
        rows = corpus.db().execute("SELECT count(*) FROM stream_items WHERE stream='CEHK' AND item->>'url'=%s", (a["url"],)).fetchone()[0]
        self.assertEqual(rows, 1)
        sent = []
        with mock.patch.object(telegram_alerts, "post", lambda chat, it, stream=None: sent.append(it["url"])), \
                mock.patch.object(telegram_alerts, "GAP", 0):
            telegram_alerts.send_due([{"id": "CEHK", "telegram_chat": -1}])
            telegram_alerts.send_due([{"id": "CEHK", "telegram_chat": -1}])
        self.assertEqual(sent, [a["url"]])
        corpus.kv_set("scoring", dict(corpus.kv_get("scoring"), comments=True))
        try:
            c = self.thread_row(4102, t.isoformat())
            self.replies(c, range(2, 4), t.isoformat(), "李家超")
            self.run_at(t + timedelta(minutes=1))
            n = corpus.db().execute("""SELECT count(*) FROM scored s JOIN posts p ON p.id = s.post_id
                                       WHERE p.url LIKE 'https://www.discuss.com.hk/archiver/%%#c%%'""").fetchone()[0]
            self.assertEqual(n, 0)
        finally:
            corpus.kv_set("scoring", dict(corpus.kv_get("scoring"), comments=False))

    def test_8_item_page_divider(self):
        t = datetime.now(HKT).replace(microsecond=0)
        it = self.thread_row(4201, (t - timedelta(minutes=3)).isoformat())
        self.replies(it, range(2, 4), (t - timedelta(minutes=2)).isoformat())
        self.run_at(t)
        self.replies(it, range(4, 6), (t + timedelta(minutes=1)).isoformat())
        x = dict(corpus.db().execute("SELECT * FROM posts WHERE url=%s", (it["url"],)).fetchone())
        x["engagement"] = json.loads(x["engagement"]) if x.get("engagement") else {}
        th = itempage.thread(x)
        self.assertEqual([c["scored"] for c in th["comments"]], [True, True, False, False])
        page = open(os.path.join(HERE, "web-app", "src", "routes", "-ItemPage.tsx"), encoding="utf-8").read()
        m = re.search(r"/([^/]*lihkg[^/]*)/\.test\(D\.thread_url", page)
        self.assertTrue(m and "discuss" in m.group(1), "the 回覆 divider covers 香港討論區")


class Search(unittest.TestCase):
    def test_9_search_sticky_and_per_query(self):
        page = '<div class="result"><a href="https://m.discuss.com.hk/index.php?action=thread&tid=5001">李家超出席活動</a></div>'
        calls = []

        made = []

        class P:
            def __init__(self, site, direct=False, isp=False):
                self.site = site
                made.append((direct, isp))

            def get(self, u, headers=None, walled=None):
                calls.append(u)
                if "srchtxt=%E7%89%B9%E9%A6%96" in u:  # 特首: walled on every session
                    raise replies_fetch.Wall(u)
                return page

            def account(self):
                pass
        with mock.patch.object(replies_fetch, "Proxied", P), mock.patch.object(replies_fetch, "discuss_route", lambda: "isp"):
            got = sources.discuss_search(["特首", "李家超"])
        self.assertEqual(made, [(False, True)])  # the replies job's route (the ISP IP here)
        self.assertEqual(len(calls), 2)
        self.assertEqual([g["url"] for g in got], [url(5001)])


if __name__ == "__main__":
    unittest.main()
