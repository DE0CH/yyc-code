#!/usr/bin/env python3
"""The whole LIHKG site (Deyao, 2026-10-14), on a throwaway database (TEST_DATABASE_URL), no network, the model stubbed:

Capture (replies_fetch.py):
1. LIHKG is listed through 最新 (cat 3, every board, latest reply first), each thread with its reply count and post
   time; a thread id between the last one seen and the newest listed that 最新 didn't show is read directly, and its
   board is listed from then on (a board 最新 hides).
   Threads are read newest first (the id walk's, then by post time), so a new thread isn't stuck behind an old
   thread's first full read.
2. A new thread is read at once; a thread whose reply count grew is read again when its last read is 25 min old, or
   when its post time + 30 min has passed since its last read (so the second scoring sees its replies).
3. The two-pass queue (twopass.py, shared by every source): a thread's first full read marks it captured; a run that
   reads every due thread marks the threads whose second scoring is due as re-read (twopass.fetched_all); one that
   leaves threads unread or is rate-limited doesn't. Each site has its own lock (LIHKG every 5 min beside
   香港討論區's longer run).

Scoring (streams.py + twopass.py): a LIHKG thread is scored as one unit, its opening post and its replies, against
every stream (ai_spend purpose scoring_lihkg), and its reply rows are not scored on their own:
4. at capture: once its replies are read (twopass.captured), or at once when no fetcher is reading it (a collector
   filed it); a thread mid-read waits;
5. once more at post time + 30 min, once its replies were re-read since (or 15 min after the mark without one), with
   the replies filed by then; never a third time. The later score is the current one; both are kept in
   score_passes;
6. a thread first scored 30 min or more after its post time is not scored again;
7. an old thread captured now (posted days ago) is scored at capture, on its seen time;
8. the replies read most recent first within REPLY_CHARS, and a call's threads stay within BATCH_CHARS.

By thread (Deyao, 2026-10-14):
9. a thread is one item in 串流 and one post in 數據分析; its replies are never scored or filed on their own, even
   with comment scoring on; a stream's Telegram channel gets ONE message per thread: at the first scoring when it
   passes, else at the 30-minute one when that passes, never a second;
10. the item page lists every reply fetched so far (no cap), each with its likes and dislikes, and marks which
    replies the latest scoring saw (a divider with its time; replies fetched later below it);
11. the mechanism is source-agnostic: another source's post URL pattern added to twopass.UNIT_URLS gets the same.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_lihkg_site.py
"""
import json, os, re, sys, tempfile, time, unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, item as itempage, replies_fetch, streams, telegram_alerts, titles, twopass  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)

HKT = corpus.HKT
STREAMS = app.STREAMS
CALLS = []


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000):
    """every post scores CEHK 90 when 李家超 is in its block, else nothing"""
    CALLS.append((purpose, prompt))
    body = prompt.split("Posts:\n", 1)[1]
    blocks = re.split(r"(?m)^(?=\d+\. \[)", body)
    out = []
    for b in blocks:
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


def listing_item(tid, created, replies, cat=5, last=None):
    return {"thread_id": tid, "cat_id": cat, "category": {"name": "時事台" if cat == 5 else f"台{cat}"}, "title": f"帖{tid}",
            "create_time": created, "last_reply_time": last or created + 60, "no_of_reply": replies}


def page(tid, created, n, cat=5, words="", start=1):
    return {"thread_id": tid, "cat_id": cat, "category": {"name": "時事台" if cat == 5 else f"台{cat}"},
            "title": f"帖{tid}", "create_time": created, "user_nickname": "樓主", "no_of_reply": n, "total_page": 1,
            "item_data": [{"msg_num": k, "msg": (f"第{k}樓 {words}" if k > 1 else f"開帖內容 {words}"),
                           "user_nickname": f"u{k}", "reply_time": created + 30 * k} for k in range(start, n + 1)]}


class Px:
    direct = True
    device = "d"

    def account(self):
        pass


def thread_row(tid, published, seen=None, text="開帖內容", sid="lihkg_current"):
    item = {"url": f"https://lihkg.com/thread/{tid}/page/1", "title": f"帖{tid} 李家超", "text": text, "sid": sid,
            "source": "LIHKG 時事台", "section": "時事台", "published": published}
    corpus.ingest([item], seen)
    return item


def replies(item, rng, published, words=""):
    corpus.add_comments(item, [{"id": k, "author": f"u{k}", "text": f"回覆{k} {words}", "published": published}
                               for k in rng])


def scored_at(tid):
    r = corpus.db().execute("""SELECT s.at FROM scored s JOIN posts p ON p.id = s.post_id WHERE p.url=%s""",
                            (f"https://lihkg.com/thread/{tid}/page/1",)).fetchone()
    return r and r[0]


class Capture(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'replies:%%'")

    def test_1_latest_listing_and_gaps(self):
        t0 = int(time.time())
        paths = []
        lists = {3: [listing_item(1001, t0 - 600, 3), listing_item(1004, t0 - 60, 1, cat=1)]}

        def fake(px, path, ref=None):
            paths.append(path)
            m = re.search(r"cat_id=(\d+)&page=(\d+)", path)
            if m:
                return {"items": lists.get(int(m.group(1)), []) if m.group(2) == "1" else []}
            tid = int(re.search(r"thread/(\d+)/page", path).group(1))
            if tid == 1003:
                raise RuntimeError("LIHKG thread/1003: 沒有相關文章")  # deleted
            return page(tid, t0 - 300, 2, cat=32 if tid == 1002 else (1 if tid == 1004 else 5))
        corpus.kv_set("replies:lihkg:max_id", 1001)
        with mock.patch.object(replies_fetch, "lihkg_json", fake), \
                mock.patch.object(replies_fetch, "Proxied", lambda site, direct=False: Px()):
            res = replies_fetch.run("lihkg", 3 * 3600)
        self.assertIn("thread/latest?cat_id=3&page=1&count=60&type=now", paths)
        self.assertFalse(any("thread/category?cat_id=5" in p for p in paths))  # no per-board lists any more
        read = [int(m.group(1)) for p in paths for m in [re.search(r"thread/(\d+)/page/1\?", p)] if m]
        self.assertEqual(read[:4], [1003, 1002, 1004, 1001])  # 1002, 1003: ids 最新 didn't show; then newest thread first
        self.assertEqual(corpus.kv_get("replies:lihkg:max_id"), 1004)
        self.assertEqual(corpus.kv_get("replies:lihkg:hidden_cats"), [32])  # 1002's board: listed from now on
        self.assertTrue(corpus.ids_for_urls(["https://lihkg.com/thread/1002/page/1"]))
        self.assertEqual(res["errors"], 1)
        paths.clear()
        with mock.patch.object(replies_fetch, "lihkg_json", fake), \
                mock.patch.object(replies_fetch, "Proxied", lambda site, direct=False: Px()):
            replies_fetch.run("lihkg", 3 * 3600)
        self.assertIn("thread/category?cat_id=32&page=1&count=60&type=now", paths)

    def test_2_when_a_thread_is_read_again(self):
        now = time.time()
        st = {"read": 3, "total": 3, "checked": now - 600, "at": now - 600}
        young = {"replies": 5, "created": now - 20 * 60}
        self.assertFalse(replies_fetch.lihkg_due(st, young, now))  # grew, read 10 min ago, mark not passed
        self.assertTrue(replies_fetch.lihkg_due(st, {"replies": 5, "created": now - 35 * 60}, now))  # mark passed since
        self.assertFalse(replies_fetch.lihkg_due(st, {"replies": 3, "created": now - 35 * 60}, now))  # no new reply
        self.assertTrue(replies_fetch.lihkg_due(dict(st, at=now - 26 * 60), {"replies": 5, "created": now - 3 * 86400}, now))
        self.assertFalse(replies_fetch.lihkg_due(st, {"replies": 5, "created": now - 3 * 86400}, now))
        self.assertTrue(replies_fetch.lihkg_due({"read": 0, "total": None, "checked": 0}, {"replies": 1}, now))
        self.assertTrue(replies_fetch.lihkg_due(dict(st, at=now - 26 * 60), 5, now))  # a bare count, old read
        self.assertFalse(replies_fetch.lihkg_due(st, 5, now))

    def test_3_two_pass_queue_and_locks(self):
        t0 = int(time.time())
        url = "https://lihkg.com/thread/2001/page/1"
        corpus.db().execute("DELETE FROM two_pass")

        def fake(px, path, ref=None):
            if "cat_id=" in path:
                return {"items": [listing_item(2001, t0 - 600, 2)] if "page=1&" in path else []}
            return page(2001, t0 - 600, 2)
        with mock.patch.object(replies_fetch, "lihkg_json", fake), \
                mock.patch.object(replies_fetch, "Proxied", lambda site, direct=False: Px()):
            replies_fetch.run("lihkg", 3 * 3600)
        r = corpus.db().execute("SELECT reading_at, captured_at FROM two_pass WHERE url=%s", (url,)).fetchone()
        self.assertTrue(r[0] and r[1])  # read in full at capture
        due = datetime.now(HKT) - timedelta(minutes=1)
        corpus.db().execute("UPDATE two_pass SET first_at=%s, due_at=%s WHERE url=%s", (due, due, url))
        other = "https://lihkg.com/thread/2002/page/1"  # due, not in this run's listing: no new reply since
        corpus.db().execute("INSERT INTO two_pass (url, first_at, due_at) VALUES (%s, %s, %s)", (other, due, due))
        self.assertEqual(sorted(twopass.due_for_fetch("https://lihkg.com/thread/%")), [url, other])

        def blocked(px, path, ref=None):
            raise replies_fetch.Blocked(path)
        with mock.patch.object(replies_fetch, "lihkg_json", blocked), \
                mock.patch.object(replies_fetch, "Proxied", lambda site, direct=False: Px()):
            replies_fetch.run("lihkg", 3 * 3600)
        self.assertEqual(len(twopass.due_for_fetch("https://lihkg.com/thread/%")), 2)  # rate-limited: not re-read
        with mock.patch.object(replies_fetch, "lihkg_json", fake), \
                mock.patch.object(replies_fetch, "Proxied", lambda site, direct=False: Px()):
            replies_fetch.run("lihkg", 3 * 3600)
        self.assertEqual(twopass.due_for_fetch("https://lihkg.com/thread/%"), [])
        names = []
        real = corpus.lock

        def lock(name, wait=True):
            names.append(name)
            return real(name, wait)
        with mock.patch.object(corpus, "lock", lock), mock.patch.object(replies_fetch, "run_sites", lambda a, s: None):
            replies_fetch.main(["lihkg"])
            replies_fetch.main(["discuss"])
        self.assertEqual(names, ["replies:lihkg", "replies:discuss"])


class Scoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.kv_set("scoring", {"from_id": 0, "comments": False, "since": "2026-10-14T10:00:00+08:00"})

    def setUp(self):
        CALLS.clear()
        corpus.db().execute("DELETE FROM posts")
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'replies:%%'")

    def run_at(self, t):
        with mock.patch.object(streams, "clock", Clock(t)):
            return streams.run_once(STREAMS)

    def test_4_first_pass_at_capture(self):
        t = datetime.now(HKT).replace(microsecond=0)
        pub = t - timedelta(minutes=5)
        item = thread_row(3001, pub.isoformat())
        twopass.reading(item["url"])  # mid-read
        self.assertEqual(self.run_at(t), 0)
        replies(item, range(2, 5), (pub + timedelta(minutes=2)).isoformat(), "支持")
        twopass.captured(item["url"])
        self.assertEqual(self.run_at(t), 1)  # the thread only: its reply rows are not scored on their own
        purpose, prompt = CALLS[-1]
        self.assertEqual(purpose, "scoring_lihkg")
        self.assertIn("回覆2 支持", prompt)
        self.assertIn("回覆4 支持", prompt)
        self.assertEqual(self.run_at(t + timedelta(minutes=10)), 0)  # before the mark: nothing
        # a collector's thread (no replies-job state): scored at once
        thread_row(3002, (t - timedelta(minutes=2)).isoformat())
        self.assertEqual(self.run_at(t), 1)

    def test_5_second_pass_after_the_mark(self):
        t = datetime.now(HKT).replace(microsecond=0)
        pub = t - timedelta(minutes=10)
        item = thread_row(3101, pub.isoformat())
        self.assertEqual(self.run_at(t), 1)
        first = scored_at(3101)
        replies(item, range(2, 4), (pub + timedelta(minutes=25)).isoformat(), "反對")
        mark = pub + timedelta(minutes=30)
        twopass.fetched(item["url"], mark - timedelta(minutes=1))  # a read before the mark doesn't count
        self.assertEqual(self.run_at(mark + timedelta(minutes=2)), 0)  # no re-read since the mark yet
        twopass.fetched(item["url"], mark + timedelta(minutes=1))
        self.assertEqual(self.run_at(mark + timedelta(minutes=2)), 1)
        self.assertIn("回覆3 反對", CALLS[-1][1])
        self.assertNotEqual(scored_at(3101), first)
        pid = corpus.ids_for_urls([item["url"]])[item["url"]]
        passes = corpus.db().execute("SELECT pass, comments FROM score_passes WHERE post_id=%s ORDER BY pass", (pid,)).fetchall()
        self.assertEqual([tuple(r) for r in passes], [(1, 0), (2, 2)])  # history: both; current: the later one
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM scores WHERE post_id=%s", (pid,)).fetchone()[0], 1)
        replies(item, range(4, 6), (pub + timedelta(minutes=40)).isoformat())
        corpus.db().execute("UPDATE posts SET text='改過' WHERE url=%s", (item["url"],))
        self.assertEqual(self.run_at(mark + timedelta(hours=3)), 0)  # never a third time
        # no replies run after the mark at all: scored 15 min after it anyway
        thread_row(3102, pub.isoformat())
        self.run_at(t)
        self.assertEqual(self.run_at(mark + timedelta(minutes=14)), 0)
        self.assertEqual(self.run_at(mark + timedelta(minutes=16)), 1)

    def test_6_first_capture_after_the_mark(self):
        t = datetime.now(HKT).replace(microsecond=0)
        thread_row(3201, (t - timedelta(minutes=45)).isoformat())
        self.assertEqual(self.run_at(t), 1)
        self.assertEqual(self.run_at(t + timedelta(minutes=30)), 0)

    def test_7_old_thread_captured_now(self):
        t = datetime.now(HKT).replace(microsecond=0)
        thread_row(3301, (t - timedelta(days=20)).isoformat(), seen=t.isoformat())
        self.assertEqual(self.run_at(t), 1)
        self.assertEqual(self.run_at(t + timedelta(hours=1)), 0)

    def test_8_reply_and_batch_budgets(self):
        t = datetime.now(HKT).replace(microsecond=0)
        with mock.patch.object(streams, "REPLY_CHARS", 200), mock.patch.object(streams, "BATCH_CHARS", 400):
            for tid in (3401, 3402, 3403):
                item = thread_row(tid, (t - timedelta(minutes=3)).isoformat())
                corpus.add_comments(item, [{"id": k, "author": "u", "text": f"樓{k:03d}" + "字" * 40,
                                            "published": (t - timedelta(minutes=3) + timedelta(seconds=k)).isoformat()}
                                           for k in range(2, 30)])
            self.assertEqual(self.run_at(t), 3)
        lihkg = [p for pu, p in CALLS if pu == "scoring_lihkg"]
        self.assertGreaterEqual(len(lihkg), 2)  # 3 threads × (title + ~200 reply chars) don't fit one 900-char call
        self.assertIn("樓029", lihkg[0])  # the newest replies are kept
        self.assertNotIn("樓002", lihkg[0])

    def test_9_one_item_and_one_message_per_thread(self):
        t = datetime.now(HKT).replace(microsecond=0)
        corpus.kv_set("telegram_sent", {"CEHK": corpus.db().execute("SELECT coalesce(max(id), 0) FROM stream_items").fetchone()[0]})
        sent = []
        pub = t - timedelta(minutes=5)
        a = thread_row(3501, pub.isoformat())  # passes at capture
        b = thread_row(3502, pub.isoformat())
        corpus.db().execute("UPDATE posts SET title='帖3502 天氣' WHERE url=%s", (b["url"],))  # not at capture
        b["title"] = "帖3502 天氣"
        corpus.db().execute("UPDATE posts SET first_run=NULL")  # not a new source's first posts (those aren't filed)
        self.assertEqual(self.run_at(t), 2)
        replies(a, range(2, 4), (pub + timedelta(minutes=10)).isoformat())
        replies(b, range(2, 4), (pub + timedelta(minutes=10)).isoformat(), "李家超")  # its replies make it pass
        twopass.fetched_all("https://lihkg.com/thread/%", pub + timedelta(minutes=31))
        self.assertEqual(self.run_at(pub + timedelta(minutes=32)), 2)
        rows = corpus.db().execute("""SELECT item->>'url' FROM stream_items WHERE stream='CEHK' AND item->>'url' LIKE
                                      'https://lihkg.com/thread/35%%' ORDER BY id""").fetchall()
        self.assertEqual([r[0] for r in rows], [a["url"], b["url"]])  # one item each, b's from the 30-minute scoring
        with mock.patch.object(telegram_alerts, "post", lambda chat, it, stream=None: sent.append(it["url"])), \
                mock.patch.object(telegram_alerts, "GAP", 0):
            telegram_alerts.send_due([{"id": "CEHK", "telegram_chat": -1}])
            telegram_alerts.send_due([{"id": "CEHK", "telegram_chat": -1}])
        self.assertEqual(sent, [a["url"], b["url"]])
        # comment scoring on: LIHKG replies still aren't scored or filed on their own
        corpus.kv_set("scoring", dict(corpus.kv_get("scoring"), comments=True))
        try:
            c = thread_row(3503, t.isoformat())
            replies(c, range(2, 4), t.isoformat(), "李家超")
            self.assertEqual(self.run_at(t + timedelta(minutes=1)), 1)
            n = corpus.db().execute("""SELECT count(*) FROM scored s JOIN posts p ON p.id = s.post_id
                                       WHERE p.url LIKE 'https://lihkg.com/thread/%%#c%%'""").fetchone()[0]
            self.assertEqual(n, 0)
        finally:
            corpus.kv_set("scoring", dict(corpus.kv_get("scoring"), comments=False))

    def test_10_item_page_lists_every_reply(self):
        t = datetime.now(HKT).replace(microsecond=0)
        it = thread_row(3601, t.isoformat())
        corpus.add_comments(it, [{"id": k, "author": "u", "text": f"回覆{k}", "published": (t + timedelta(seconds=k)).isoformat(),
                                  "likes": k, "dislikes": 1} for k in range(2, 252)])
        x = dict(corpus.db().execute("SELECT * FROM posts WHERE url=%s", (it["url"],)).fetchone())
        x["engagement"] = json.loads(x["engagement"]) if x.get("engagement") else {}
        th = itempage.thread(x)
        self.assertEqual(len(th["comments"]), 250)
        self.assertEqual((th["comments"][-1]["likes"], th["comments"][-1]["dislikes"]), (251, 1))
        corpus.add_comments(it, [{"id": 252, "author": "u", "text": "新回覆", "published": (t + timedelta(minutes=9)).isoformat()}])
        self.assertEqual(len(itempage.thread(x)["comments"]), 251)  # grows as more are fetched

    def test_10b_divider_at_what_the_latest_scoring_saw(self):
        t = datetime.now(HKT).replace(microsecond=0)
        it = thread_row(3602, (t - timedelta(minutes=3)).isoformat())
        replies(it, range(2, 4), (t - timedelta(minutes=2)).isoformat())
        self.run_at(t)
        replies(it, range(4, 6), (t + timedelta(minutes=1)).isoformat())  # fetched after the scoring
        x = dict(corpus.db().execute("SELECT * FROM posts WHERE url=%s", (it["url"],)).fetchone())
        x["engagement"] = json.loads(x["engagement"]) if x.get("engagement") else {}
        th = itempage.thread(x)
        self.assertEqual([c["scored"] for c in th["comments"]], [True, True, False, False])
        self.assertEqual(th["scored_at"], t.isoformat(timespec="seconds"))
        page = open(os.path.join(HERE, "web-app", "src", "routes", "-ItemPage.tsx"), encoding="utf-8").read()
        self.assertIn("以上${word}已納入評分", page)  # word = 回覆 on LIHKG and 香港討論區, else 留言
        self.assertRegex(page, r'/lihkg[^/]*/\.test\(D\.thread_url.*\? "回覆" : "留言"')

    def test_11_any_source_can_join(self):
        t = datetime.now(HKT).replace(microsecond=0)
        with mock.patch.object(twopass, "UNIT_URLS", twopass.UNIT_URLS + [r"^https://forum\.test/thread/[0-9]+$"]):
            # (Facebook, Instagram, Threads and YouTube posts are units already, read by social_twopass: test_social_twopass.py)
            fb = {"url": "https://forum.test/thread/77", "title": "李家超 帖文", "text": "內容", "sid": "forum_x",
                  "source": "Forum", "published": (t - timedelta(minutes=5)).isoformat()}
            corpus.ingest([fb])
            corpus.db().execute("UPDATE posts SET first_run=NULL")
            corpus.add_comments(fb, [{"id": 1, "author": "a", "text": "留言一", "published": t.isoformat()}])
            self.assertEqual(self.run_at(t), 1)  # the post only, with its comment; the comment row isn't scored alone
            self.assertIn("留言一", CALLS[-1][1])
            self.assertEqual(self.run_at(t + timedelta(minutes=26)), 0)
            twopass.fetched(fb["url"], t + timedelta(minutes=26))
            self.assertEqual(self.run_at(t + timedelta(minutes=27)), 1)


if __name__ == "__main__":
    unittest.main()
