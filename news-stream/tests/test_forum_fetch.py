#!/usr/bin/env python3
"""The catalogue forums' threads with their replies (forum_fetch.py, forum_threads.json; Deyao, 2026-10-20: collect
each thread's posts and replies, not only the listing's title), on a throwaway database (TEST_DATABASE_URL), no
network (the pages are fixtures saved from the forums themselves, text only):

1. forum_threads.json is valid: every forum's thread regexes match the URLs its listings file (real ones) and read
   the same in Postgres; the forums read are units of the two-pass scoring (twopass.UNIT_URLS and FETCHER_URLS), the
   ones that are off say why and are not units.
2. Each parser reads its page: Discuz's printable view (Discuz 6, 7 and X: 作者/時間, "N 小時前" with its title time,
   <br> or <br />), Discuz X and 7 thread pages (floor, author, time, body with nested tags, pages), the 提示信息 pages
   (no access), vBulletin's printthread, XenForo, NodeBB's API, schema.org DiscussionForumPosting, review33's frame,
   WordPress's REST post; quotes and pictures stay out of the text.
3. A run reads a new thread: the opening post becomes the thread post's text (the title too when the listing's was a
   placeholder: a sitemap slug), every reply a comment row (thread = the post's URL, its own author and time), the
   thread is captured for the two-pass scoring; the next read starts at the page of the first post not yet filed, a
   read with nothing new doubles the wait, one with new replies files only those; a thread behind a login is
   noaccess and still captured; a failing forum is a failing status line, not a failed run; the monitor's Forum
   replies card lists every forum (an off one with its reason).
4. eyny's proof of work (SHA-256 with leading zeros as cookies) is solved and its 18+ notice agreed to.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_forum_fetch.py
"""
import hashlib, json, os, re, sys, time, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, forum_fetch as ff, twopass  # noqa: E402

HKT = timezone(timedelta(hours=8))
FX = os.path.join(HERE, "tests", "fixtures", "forums")


def fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


# URLs the forums' listings filed (sites_fetch.read on each forum's sites.json entries, 2026-10-20) → the thread id
LISTED = {
    "post76": [("https://post76.hk/thread-362248-1-1.html", "362248")],
    "baby_kingdom": [("https://www.baby-kingdom.com/forum.php?mod=viewthread&tid=23859137", "23859137"),
                     ("https://www.baby-kingdom.com/forum.php?mod=viewthread&tid=23859137&extra=page%3D1%26orderby%3Ddateline", "23859137")],
    "edu_kingdom": [("https://www.edu-kingdom.com/thread-3887450-RonaldDahl(14本）", "3887450"),
                    ("https://www.edu-kingdom.com/forum.php?mod=redirect&tid=3887426&goto=lastpost", "3887426"),
                    ("https://www.edu-kingdom.com/forum.php?mod=viewthread&tid=3887395&extra=page%3D1%26orderby%3Ddateline", "3887395")],
    "jetsocome": [("https://www.jetsocome.com/forum.php?mod=viewthread&tid=199616&extra=", "199616")],
    "tvboxnow": [("https://www.tvboxnow.com/thread-5043229-1-1.html", "5043229")],
    "ds_hk": [("http://ds-hk.net/viewthread.php?tid=409800", "409800"), ("https://ds-hk.net/redirect.php?tid=409799&goto=lastpost", "409799")],
    "eyankit": [("http://forum.eyankit.com/viewthread.php?tid=100313", "100313")],
    "starphoto": [("http://starphoto.joinbbs.net/viewthread.php?tid=56153", "56153")],
    "s141": [("https://141-161.net/thread-4505146", "4505146")],
    "eyny": [("https://www05.eyny.com/archiver/tid-13000000-1.html", "13000000")],
    "geoexpat": [("https://geoexpat.com/forum/342/thread370455.html", "370455"), ("https://geoexpat.com/forum/342/thread370455-6.html", "370455"),
                 ("https://geoexpat.com/forum/showthread.php?t=370673&goto=newpost", "370673")],
    "u8hk": [("https://www.u8hk.com/topic/2724/施政報告2026-公屋輪候時間目標降至4.5年再壓到-3字頭", "2724")],
    "androidcentral": [("https://forums.androidcentral.com/threads/game-rumblorb-a-3d-marble-physics-puzzle.1091040/", "1091040")],
    "asiaxpat": [("https://hongkong.asiaxpat.com/forums/i-am-a-virtual-assistant-and-mandarin-tutor", "i-am-a-virtual-assistant-and-mandarin-tutor")],
    "review33": [("http://www.review33.com/chat/index.php?topic=64261020171545", "64261020171545")],
    "bpplatform": [("https://bpplatform.bastillepost.com/article/188804-%e6%b8%85%e6%b0%b4", "188804")],
    "av100fun": [("https://av100fun.com/viewtopic.php?f=7&t=336976", "336976"), ("https://av100fun.com/viewtopic.php?f=7&t=336976&sid=0547c1520abcb59d", "336976")],
}
NOT_THREADS = ["https://hongkong.asiaxpat.com/forums/general", "https://hongkong.asiaxpat.com/jobs",
               "https://www.dcfever.com/forum/list.php?f=12", "https://hk.ulifestyle.com.hk/topic/detail/20114476/x"]


class Config(unittest.TestCase):
    def test_valid_and_matching(self):
        forums = ff.load()
        for key, urls in LISTED.items():
            for url, tid in urls:
                f, g = ff.match(forums, url)
                self.assertEqual((f or {}).get("key"), key, url)
                self.assertEqual(g["tid"], tid, url)
        for url in NOT_THREADS:
            f, _ = ff.match({k: v for k, v in forums.items() if not v.get("off")}, url)
            self.assertIsNone(f, url)

    def test_units_read_the_same_in_postgres(self):
        forums = ff.load()
        units = twopass.forum_units()
        self.assertTrue(units)
        for u in units:
            self.assertIn(u, twopass.UNIT_URLS)
            self.assertIn(u, twopass.FETCHER_URLS)
        for key, urls in LISTED.items():
            if forums[key].get("off"):
                continue
            for url, _ in urls:
                py = any(re.match(u, url) for u in units)
                pg = corpus.db().execute("SELECT %s ~ ANY(%s)", (url, units)).fetchone()[0]
                self.assertTrue(py and pg, url)
        for k, f in forums.items():
            if f.get("off"):
                self.assertGreater(len(f["off"]), 20, k)  # the reason, measured
                for rx in f["thread"]:
                    self.assertNotIn(re.sub(r"\(\?P<\w+>", "(", rx), units)


class Parsers(unittest.TestCase):
    def test_discuz_print(self):
        m, ps = ff.discuz_print(fx("discuz6_print.html"))
        self.assertEqual(m["title"], "[簽到] 每日一簽(20/10/2026）")
        self.assertEqual(len(ps), 9)
        self.assertEqual((ps[0]["floor"], ps[0]["author"]), (1, "天空"))
        self.assertEqual(ps[0]["published"], datetime(2026, 10, 20, 12, 3, tzinfo=HKT))
        self.assertTrue(ps[0]["text"].startswith("在這裡簽到得50ds幣"))
        self.assertEqual((ps[8]["author"], ps[8]["text"]), ("陳小蘋", "簽到~~"))
        m, ps = ff.discuz_print(fx("discuzx_print.html"))  # Discuz X, two-digit years, quotes
        self.assertEqual(len(ps), 58)
        self.assertEqual(ps[0]["published"], datetime(2026, 10, 11, 7, 26, tzinfo=HKT))
        self.assertNotIn("barque 發表於", ps[2]["text"])  # the quoted post is its own row
        self.assertTrue(ps[2]["text"].startswith("那700篇論文刊登在什麼 journal"))
        m, ps = ff.discuz_print(fx("discuz7_print_ago.html"))  # "1 小時前" with the time in its title
        self.assertEqual(ps[0]["published"], datetime(2026, 10, 20, 16, 31, tzinfo=HKT))
        self.assertNotIn("screen.width", ps[0]["text"])  # an <img onload="… >screen.width"> is no text
        self.assertNotIn("attachment.php", ps[0]["text"])
        m, ps = ff.discuz_print(fx("discuzx_page_jetso.html"))  # a forum that answers its printable view with the thread page
        self.assertTrue(m["paged"])
        self.assertEqual(len(ps), 7)
        m, ps = ff.discuz_print(fx("discuz6_print_br.html"))  # the header line ends in <br><br>
        self.assertTrue(ps[0]["text"].startswith("紅磡香港體育館在2026年2月舉行大型活動"))
        self.assertEqual(ps[0]["published"], datetime(2026, 2, 18, 3, 30, tzinfo=HKT))

    def test_discuz_pages(self):
        m, ps = ff.discuz(fx("discuzx_page_jetso.html"))
        self.assertEqual(m["title"], "大快活：APP會員 全單85折（至26/10）")
        self.assertEqual([p["floor"] for p in ps], list(range(1, 8)))
        self.assertEqual((ps[0]["author"], ps[1]["author"], ps[1]["text"]), ("tem", "LWY", "thanks"))
        self.assertIn("大快活APP「橙色搗蛋折」", ps[0]["text"])
        m, ps = ff.discuz(fx("discuzx_page_bk.html"))
        self.assertEqual(m["title"], "大家樂 : 原隻靚雞 半斤叉燒 外賣優惠")
        self.assertEqual((ps[1]["author"], ps[1]["text"], ps[1]["published"]), ("ttjl7", "多謝報料", datetime(2026, 10, 20, 17, 21, tzinfo=HKT)))
        m, ps = ff.discuz(fx("discuz7_page2.html"), 2, 15)  # Discuz 7
        self.assertEqual(ps[0]["floor"], 16)
        self.assertEqual(ps[0]["author"], "lhy92725")
        self.assertEqual(ps[0]["published"], datetime(2019, 9, 25, 10, 28, tzinfo=HKT))
        self.assertGreater(m["pages"], 2)
        m, ps = ff.discuz(fx("discuzx_page.html"))
        self.assertEqual((m["board"], ps[0]["author"]), ("電影討論", "小瑟"))
        with self.assertRaises(ff.NoAccess):
            ff.discuz(fx("discuz_noaccess.html"))

    def test_other_software(self):
        m, ps = ff.vbulletin_print(fx("vbulletin_print.html"), 1, 10)
        self.assertEqual((m["title"], m["pages"], len(ps)), ("New Huanggang Port", 6, 10))
        self.assertEqual((ps[0]["author"], ps[0]["published"]), ("nivek2046", datetime(2026, 6, 17, 21, 4, tzinfo=HKT)))
        self.assertIn("Shenzhen Metro Line 7", ps[0]["text"])  # the body sits in <blockquote class="restore">
        m, ps = ff.xenforo(fx("xenforo.html"))
        self.assertEqual((m["title"], ps[0]["author"], ps[0]["floor"]), ("[GAME] RUMBLORB: a 3D marble physics puzzle", "gyuforge", 1))
        self.assertIn("3D physics puzzle", ps[0]["text"])
        m, ps = ff.nodebb(fx("nodebb.json"))
        self.assertEqual((m["board"], ps[0]["author"], ps[0]["floor"]), ("公屋討論區", "香港公屋", 1))
        m, ps = ff.ldjson(fx("ldjson.html"))
        self.assertEqual([p["floor"] for p in ps], [1, 2, 3, 4])
        self.assertEqual((ps[3]["author"], ps[3]["text"]), ("faw", "98460774 WhatsApp"))
        m, ps = ff.review33(fx("review33.html"))
        self.assertEqual((m["pages"], ps[0]["author"], ps[0]["published"]), (1, "vitz", datetime(2026, 10, 20, 17, 15, tzinfo=HKT)))
        m, ps = ff.wp_post(fx("wp_post.json"))
        self.assertTrue(m["title"].startswith("清水灣鄉村俱樂部"))
        self.assertTrue(ps[0]["text"].startswith("清水灣鄉村俱樂部（「清水灣」）"))
        self.assertEqual(ff.wp_comments('{"code": "rest_cannot_read"}')[1], [])
        m, ps = ff.phpbb(fx("phpbb.html"), 1, 10)  # phpBB 3.0 prosilver (AV100fun, read in a real Chrome)
        self.assertEqual((m["title"], m["pages"], m["replies"], len(ps)), ("100FUN 仲有冇人睇架?", 1, 9, 10))
        self.assertEqual((ps[1]["author"], ps[1]["published"]), ("imcoke", datetime(2026, 9, 3, 0, 19, tzinfo=HKT)))
        self.assertTrue(ps[1]["text"].startswith("login 終於正常了"))

    def test_placeholders(self):
        self.assertTrue(ff.placeholder("forum.php", "https://www.baby-kingdom.com/forum.php?mod=viewthread&tid=1"))
        self.assertTrue(ff.placeholder("thread 3887450 RonaldDahl(14本）", "https://www.edu-kingdom.com/thread-3887450-RonaldDahl(14本）"))
        self.assertTrue(ff.placeholder("請問有2019-2020 N班下午班的同 ...", "https://www.edu-kingdom.com/forum.php?mod=redirect&tid=1"))
        self.assertFalse(ff.placeholder("Belkin Nintendo Switch 2 專用充電保護殼 簡評", "https://post76.hk/thread-362248-1-1.html"))
        self.assertEqual(ff.text_of("好味<br />12 小時前 上傳<br />"), "好味")

    def test_times(self):
        ref = datetime(2026, 10, 20, 12, 0, tzinfo=HKT)
        self.assertEqual(ff.when("26-10-20 16:51"), datetime(2026, 10, 20, 16, 51, tzinfo=HKT))
        self.assertEqual(ff.when("2019-9-25 10:28 PM"), datetime(2019, 9, 25, 22, 28, tzinfo=HKT))
        self.assertEqual(ff.when("17-06-2026, 09:04 PM", "dmy"), datetime(2026, 6, 17, 21, 4, tzinfo=HKT))
        self.assertEqual(ff.when("3 分鐘前", ref=ref), ref - timedelta(minutes=3))
        self.assertEqual(ff.when("昨天 08:15", ref=ref), datetime(2026, 10, 19, 8, 15, tzinfo=HKT))
        self.assertEqual(ff.when(1789544891635).year, 2026)


class Eyny(unittest.TestCase):
    def test_pow_and_agree(self):
        page = fx("eyny_pow.html")
        ck = ff.pow_cookies(page)
        pre = re.search(r'document\.cookie = "(\w+)_n="', page).group(1)
        ch, ts = ck[pre + "_ch"], ck[pre + "_ts"]
        self.assertTrue(hashlib.sha256(f"{ch}|{ts}|{ck[pre + '_n']}".encode()).hexdigest().startswith("0000"))
        f = ff.load()["eyny"]
        cl = ff.Client(f)
        cl.gap = 0
        seen = []
        answers = [(200, page), (200, fx("eyny_agree.html")), (200, fx("discuz6_print.html"))]

        def fetch(url, data=None):
            seen.append((url, data, dict(cl.cookies)))
            return answers.pop(0)
        with mock.patch.object(cl, "fetch", side_effect=fetch), mock.patch("time.sleep"):
            text = cl.get("https://www05.eyny.com/forum.php?mod=viewthread&action=printable&tid=1")
        self.assertIn("每日一簽", text)
        self.assertIn(pre + "_n", seen[1][2])  # the second request carries the solved cookies
        self.assertEqual(seen[2][1], {"agree": "yes"})  # the 18+ notice: agreed with a POST


def add_post(url, title, sid="hk_test_forum", seen=None):
    seen = seen or datetime.now(HKT).isoformat(timespec="seconds")
    corpus.ingest([{"sid": sid, "source": "測試論壇", "title": title, "url": url, "published": None, "section": None}], seen)
    return corpus.db().execute("SELECT id FROM posts WHERE url=%s", (url,)).fetchone()[0]


class Fallback(unittest.TestCase):
    def test_isp_refused_moves_to_hk(self):
        """親子王國 goes over the flat-rate ISP IP; when IPRoyal refuses the host there (tunnel 403) the run moves to
        yycstream HK"""
        f = ff.load()["baby_kingdom"]
        self.assertEqual((f["route"], f["fallback"]), ("isp", "hk"))
        cl = ff.Client(f)
        cl.gap = 0
        routes = []

        def fetch(url, data=None):
            routes.append(cl.route)
            if cl.route == "isp":
                raise OSError("Tunnel connection failed: 403 Forbidden")
            return 200, "ok page"
        with mock.patch.object(cl, "fetch", side_effect=fetch), mock.patch("time.sleep"):
            self.assertEqual(cl.get("https://www.baby-kingdom.com/forum.php?mod=viewthread&tid=1"), "ok page")
        self.assertEqual(routes, ["isp"] * ff.ISP_REFUSED + ["hk"])  # waits first: another client on the ISP IP answers the same


class RunDeadline(unittest.TestCase):
    """a request never outlives the run (forums-29875744, 21 Oct 01:05: the 600 s Job deadline passed though the run's
    budget is 240 s: a page's tries, each waiting for the ISP lock, ran on): past the run's deadline the client stops
    trying, and its ISP lock wait is never longer than the time left"""

    def test_no_try_past_the_deadline(self):
        cl = ff.Client(ff.load()["baby_kingdom"])
        cl.gap, cl.deadline = 0, time.time() - 1
        with mock.patch.object(cl, "fetch", side_effect=AssertionError("no request after the deadline")):
            with self.assertRaises(ff.Blocked):
                cl.get("https://www.baby-kingdom.com/forum.php?mod=viewthread&tid=1")

    def test_isp_wait_within_the_time_left(self):
        import sites_fetch
        cl = ff.Client(ff.load()["baby_kingdom"])
        cl.deadline = time.time() + 5
        with mock.patch.object(sites_fetch, "isp_lock", side_effect=lambda wait=None: wait) as lk:
            got = cl._isp()
        self.assertLessEqual(got, 5)
        self.assertEqual(lk.call_count, 1)

    def test_forum_run_gives_the_client_its_deadline(self):
        f = ff.load()["baby_kingdom"]
        seen = []
        real = ff.Client.__init__

        def init(self, forum):
            real(self, forum)
            seen.append(self)
        with mock.patch.object(ff.Client, "__init__", init), mock.patch.object(ff, "candidates", return_value={}):
            ff.forum_run(f, [], 12345.0)
        self.assertEqual(seen[0].deadline, 12345.0)


class Run(unittest.TestCase):
    def setUp(self):
        ff.schema()
        corpus.db().execute("DELETE FROM forum_threads")
        corpus.db().execute("DELETE FROM posts")
        corpus.db().execute("DELETE FROM two_pass")
        self.forums = ff.load()

    def serve(self, pages):
        """Client.fetch answering from {url substring: text}"""
        calls = []

        def fetch(cl, url, data=None):
            calls.append(url)
            for k, v in pages.items():
                if k in url:
                    return (200, v) if isinstance(v, str) else v
            return 404, ""
        return calls, mock.patch.object(ff.Client, "fetch", fetch)

    def test_thread_read_then_followed(self):
        # the fixture's posts carry the dates they were saved with (October 2026), and a thread quiet for FOLLOW_DAYS is
        # no longer followed: lifted here, so the thread stays followed whatever today's date (this test began failing a
        # week after the fixture was saved)
        p = mock.patch.object(ff, "FOLLOW_DAYS", 36500)
        p.start()
        self.addCleanup(p.stop)
        url = "https://www.edu-kingdom.com/thread-3887426-AI與數學"
        pid = add_post(url, "thread 3887426 AI與數學")
        page = fx("discuzx_print.html")
        calls, patch = self.serve({"action=printable&tid=3887426": page})
        with patch, mock.patch.object(ff, "GAP", 0), mock.patch("sites_fetch.isp_lock") as lk:
            lk.return_value.__enter__ = lambda s: None
            lk.return_value.__exit__ = lambda s, *a: None
            res = ff.run(["edu_kingdom"], self.forums)
        self.assertEqual(res["edu_kingdom"]["threads"], 1, res)
        row = corpus.db().execute("SELECT * FROM posts WHERE id=%s", (pid,)).fetchone()
        self.assertEqual(row["title"], "AI 與數學")  # the sitemap's slug title replaced by the thread's own
        self.assertTrue(row["text"].startswith("AI短短一個月內成功解決了超過 300 道"))
        self.assertEqual(row["author"], "barque")
        rows = corpus.db().execute("SELECT url, author, published, text FROM posts WHERE thread=%s ORDER BY id", (url,)).fetchall()
        self.assertEqual(len(rows), 57)
        self.assertEqual(rows[0]["url"], url + "#c2")
        self.assertEqual(rows[1]["author"], "ANChan59")
        tp = corpus.db().execute("SELECT captured_at FROM two_pass WHERE url=%s", (url,)).fetchone()
        self.assertIsNotNone(tp["captured_at"])
        st = ff.state("edu_kingdom", "3887426")
        self.assertEqual((st["status"], st["read"], st["every"]), ("follow", 58, ff.REREAD_MIN))
        self.assertIn(url, [r[0] for r in corpus.db().execute(f"SELECT url FROM posts p WHERE {twopass.unit_cond('p')}")])
        # not due yet: nothing read
        calls.clear()
        with patch, mock.patch("sites_fetch.isp_lock") as lk:
            lk.return_value.__enter__ = lambda s: None
            lk.return_value.__exit__ = lambda s, *a: None
            ff.run(["edu_kingdom"], self.forums)
            self.assertEqual(calls, [])
            # due: the same page again: nothing new, the wait doubles
            corpus.db().execute("UPDATE forum_threads SET next_at = now() - interval '1 minute'")
            ff.run(["edu_kingdom"], self.forums)
            st = ff.state("edu_kingdom", "3887426")
            self.assertEqual((st["read"], st["every"]), (58, 2 * ff.REREAD_MIN))
            n = corpus.db().execute("SELECT count(*) FROM posts WHERE thread=%s", (url,)).fetchone()[0]
            self.assertEqual(n, 57)
            # a new reply: only it is filed, the wait starts over
            end = '<hr noshade size="2" width="100%" color="BORDERCOLOR">'
            more = page.replace(end, '<hr noshade size="2" width="100%" color="#808080">\n<b>作者: </b>newbie&nbsp; &nbsp; '
                                '<b>時間: </b>26-10-20 11:00<br /><br />\n新的回覆<br />' + end)
            calls2, patch2 = self.serve({"action=printable&tid=3887426": more})
            corpus.db().execute("UPDATE forum_threads SET next_at = now() - interval '1 minute'")
        with patch2, mock.patch("sites_fetch.isp_lock") as lk:
            lk.return_value.__enter__ = lambda s: None
            lk.return_value.__exit__ = lambda s, *a: None
            res = ff.run(["edu_kingdom"], self.forums)
        self.assertEqual(res["edu_kingdom"]["new_replies"], 1)
        st = ff.state("edu_kingdom", "3887426")
        self.assertEqual((st["read"], st["every"]), (59, ff.REREAD_MIN))
        last = corpus.db().execute("SELECT author, text FROM posts WHERE url=%s", (url + "#c59",)).fetchone()
        self.assertEqual((last["author"], last["text"]), ("newbie", "新的回覆"))

    def test_paged_from_the_first_unread(self):
        url = "https://www.jetsocome.com/forum.php?mod=viewthread&tid=199614&extra="
        add_post(url, "大快活：APP會員 全單85折（至26/10）")
        f = dict(self.forums["jetsocome"])
        f.pop("first")
        calls, patch = self.serve({"tid=199614&page=1": fx("discuzx_page_jetso.html")})
        with patch, mock.patch.dict(self.forums, {"jetsocome": f}):
            ff.run(["jetsocome"], self.forums)
            st = ff.state("jetsocome", "199614")
            self.assertEqual(st["read"], 7)
            self.assertEqual(sum("page=1" in c for c in calls), 1)
            corpus.db().execute("UPDATE forum_threads SET next_at = now() - interval '1 minute'")
            ff.run(["jetsocome"], self.forums)
        self.assertTrue(calls[-1].endswith("page=1"))  # post 8 is on page 1 (15 a page)

    def test_noaccess_and_failing(self):
        url = "https://www.baby-kingdom.com/forum.php?mod=viewthread&tid=23859137"
        add_post(url, "forum.php")
        url2 = "https://post76.hk/thread-362248-1-1.html"
        add_post(url2, "Belkin Nintendo Switch 2 專用充電保護殼 簡評")
        calls, patch = self.serve({"tid=23859137": fx("discuz_noaccess.html"), "tid=362248": (403, "")})
        with patch, mock.patch.dict(os.environ, {"ISP_PROXY": "http://u:p@isp:1"}):
            res = ff.run(["baby_kingdom", "post76"], self.forums)
        self.assertEqual(ff.state("baby_kingdom", "23859137")["status"], "noaccess")
        self.assertIsNotNone(corpus.db().execute("SELECT captured_at FROM two_pass WHERE url=%s", (url,)).fetchone()[0])
        self.assertFalse(res["post76"]["ok"])
        self.assertIn("403", res["post76"]["error"])
        self.assertEqual(ff.state("post76", "362248")["status"], "error")
        import monitor
        with mock.patch.object(ff, "load", return_value=self.forums):
            ff.run(["dcfever"], self.forums)
            card = {k: v for k, v in ff.statuses(self.forums).items() if v}
        self.assertIn("off", card["dcfever"])
        self.assertTrue(hasattr(monitor, "pipeline"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
