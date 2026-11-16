#!/usr/bin/env python3
"""Scored streams (streams.py) end to end on a throwaway database (TEST_DATABASE_URL; its tables are dropped and made
again), the model stubbed (no OpenRouter call): a poll ingests every post (no keyword gate), the scorer files the
ones at or above each stream's threshold.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_streams.py

Covers: posts from before the switch are never scored; a post scored at or above the threshold becomes a stream item
(score, reason, category, post_id) and a relevant tag of the stream's profile, one under it doesn't; the CEHK
example (港府 without the CE scores 30) stays out; a threshold set with infra/thresholds.py applies to later posts and
Telegram skips queued items under it; 數據分析's stream profile is exactly the set scored at or above the current
threshold (every view and export), whatever the tags table held before (keyword-era rows are dropped by
streams.sync_tags), and a threshold change moves it at once; a post whose text arrives later is scored again and can enter then; comments
are scored only when switched on, with their thread's headline; posts older than 2 days aren't scored; one item per
headline; 串流's feed leaves scores out and /app/stream/scores gives them (the edge app's server functions, run in
Node by tests/edge_call.py; an item filed unscored shows its post's latest score from the scores table); Google News
outlets nobody watches are noted as source gaps. The history backfill scores the posts live scoring never takes
(before the switch, older than 2 days) into scores and the dashboard only, never 串流, Telegram or an alert; it
waits while live scoring has posts due, skips what is already scored on the current stream definitions, re-scores
on a definition change, runs with no reserve or projection, and stops only when OpenRouter refuses (key limit / credit).
"""
import io, json, os, re, sys, tempfile, unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ADMIN_DEV", None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, edge_config, insights, searchtext, sources, streams, telegram_alerts  # noqa: E402
from edge_call import edge  # noqa: E402
sys.path.insert(0, os.path.join(HERE, "infra"))
import thresholds  # noqa: E402

STREAMS = app.STREAMS
CALLS = []


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000):
    """the scorer's model: 李家超 → CEHK 90 (言論及表態), 港府 alone → CEHK 30, 金管局 → HKIC 70; others unrelated"""
    if purpose not in ("scoring", "scoring_backfill"):
        return {"tags": []}
    CALLS.append(prompt)
    posts = re.findall(r"(?m)^(\d+)\. \[[^\]]*\] (.*)$", prompt.split("Posts:\n", 1)[1])
    body = prompt.split("Posts:\n", 1)[1]
    out = []
    for i, line in posts:
        block = body.split(f"\n{i}. ", 1)[-1] if i != "0" else body
        block = block.split(f"\n{int(i) + 1}. ", 1)[0]
        st = []
        if "李家超" in block:
            st.append({"id": "CEHK", "score": 90, "reason": "特首本人發言", "category": "言論及表態"})
        elif "港府" in block:
            st.append({"id": "CEHK", "score": 30, "reason": "只涉港府，無特首", "category": "其他"})
        if "金管局" in block:
            st.append({"id": "HKIC", "score": 70, "reason": "涉及金管局", "category": "nope"})
        out.append({"i": int(i), "sentiment": "neutral", "labels": ["monitor", "bogus"], "streams": st})
    return {"posts": out}


import stage2  # noqa: E402
ai.jsonc = stage2.agree(fake_jsonc, CALLS)  # stage 2 answers as stage 1 (tests/stage2.py)
NOW = datetime.now(app.HKT)
streams.WAIT_TEXT = timedelta(0)  # these tests don't wait for page fetches, except test_8


def poll(items):
    sources.fetch_all = lambda extra: (list(items), {"x": {"ok": True, "items": len(items), "error": None}})
    sources.discuss_search = lambda qs: []
    app.after_poll = lambda g, f: insights.after_poll(g, f)
    app.threading = type("T", (), {"Thread": lambda target, args, daemon: type("X", (), {"start": lambda s: target(*args)})()})
    app.load_state()
    app.poll_once()


def post(i, title, text="內文", **kw):
    return dict({"sid": "rthk", "source": "RTHK", "title": title, "url": f"https://news.test/{i}", "published": NOW,
                 "text": text}, **kw)


def items(stream):
    return [r[0] for r in corpus.db().execute("SELECT item FROM stream_items WHERE stream=%s ORDER BY id", (stream,))]


def site(fn, *args):
    """what the site serves (the edge app's server functions, in Node by tests/edge_call.py), after the poller's
    publish of the site's config (edge_config.py) and of the search dictionary (a hot topic's all-posts count norms
    the title with it)"""
    corpus.kv_set("edge:config", edge_config.build())
    searchtext.publish_edge()
    return edge(fn, *args)


class Scored(unittest.TestCase):
    def test_1_switch_and_threshold(self):
        poll([post(0, "舊新聞 李家超")])  # ingested before the scorer first ran
        self.assertEqual(streams.settings()["from_id"], corpus.db().execute("SELECT max(id) FROM posts").fetchone()[0])
        poll([post(1, "李家超出席論壇發言"), post(2, "Save Lily：港府不援手將提司法覆核"), post(3, "金管局公布數據"),
              post(4, "天氣：明日有雨"), post(5, "老新聞 李家超", published=NOW - timedelta(days=3))])
        n = streams.run_once(STREAMS)
        self.assertEqual(n, 4)  # 0 is from before the switch, 5 is too old
        ce = items("CEHK")
        self.assertEqual([x["title"] for x in ce], ["李家超出席論壇發言"])  # the Lily story (30) stays out
        self.assertEqual((ce[0]["score"], ce[0]["category"], ce[0]["reason"]), (90, "言論及表態", "特首本人發言"))
        self.assertEqual(ce[0]["labels"], ["monitor"])
        self.assertEqual(ce[0]["post_id"], corpus.ids_for_urls(["https://news.test/1"])["https://news.test/1"])
        hk = items("HKIC")
        self.assertEqual((hk[0]["score"], hk[0]["category"]), (70, None))  # a category the stream doesn't list: dropped
        self.assertEqual(corpus.db().execute("SELECT score FROM scores s JOIN posts p ON p.id=s.post_id WHERE p.url=%s",
                                             ("https://news.test/2",)).fetchone()[0], 30)  # stored, below the threshold
        tagged = insights.members("CEHK", insights.day_add(insights.today(), -1), insights.today())
        self.assertEqual(tagged, [ce[0]["post_id"]])  # the dashboard's stream profile follows the scores
        self.assertEqual(streams.run_once(STREAMS), 0)  # nothing twice
        self.assertIn("<categories>施政及政策", CALLS[0])

        out = io.StringIO()
        sys.stdout, old = out, sys.stdout
        try:
            thresholds.main(["preview", "CEHK", "25"])  # nothing changes
            thresholds.main([])
        finally:
            sys.stdout = old
        self.assertIn("50 → 25 would add 1 post", out.getvalue())
        self.assertIn("Save Lily", out.getvalue())
        self.assertEqual(streams.threshold("CEHK"), 50)
        thresholds.main(["set", "CEHK", "25"])
        self.assertEqual(streams.threshold("CEHK"), 25)
        poll([post(6, "港府宣布新措施")])
        streams.run_once(STREAMS)
        self.assertIn("港府宣布新措施", [x["title"] for x in items("CEHK")])  # 30 >= 25 now
        thresholds.main(["set", "CEHK", "50"])

    def test_2_text_arrives_later(self):
        poll([post(10, "論壇今日舉行", text="")])
        streams.run_once(STREAMS)
        self.assertNotIn("論壇今日舉行", [x["title"] for x in items("CEHK")])
        with corpus.db() as c:  # the article's full text comes in (fulltext_fetch.py)
            c.execute("UPDATE posts SET text=%s WHERE url=%s", ("行政長官李家超在論壇致辭", "https://news.test/10"))
        self.assertEqual(streams.run_once(STREAMS), 1)
        self.assertIn("論壇今日舉行", [x["title"] for x in items("CEHK")])

    def test_3_comments_only_when_on(self):
        poll([post(20, "李家超回應", text="佢又講大話", thread="https://news.test/thread", url="https://news.test/c1")])
        self.assertEqual(streams.run_once(STREAMS), 0)
        thresholds.main(["comments", "on"])
        before = len(CALLS)
        self.assertEqual(streams.run_once(STREAMS), 1)
        self.assertIn("留言（帖子：李家超回應）：佢又講大話", CALLS[before])
        self.assertNotIn("https://news.test/c1", [x["url"] for x in items("CEHK")])  # comments: dashboard, not the page
        pid = corpus.ids_for_urls(["https://news.test/c1"])["https://news.test/c1"]
        self.assertIn(pid, insights.members("CEHK", insights.day_add(insights.today(), -1), insights.today()))
        thresholds.main(["comments", "off"])

    def test_4_one_item_per_headline(self):
        poll([post(30, "李家超訪京 - 明報"), post(31, "李家超訪京 | 星島")])
        streams.run_once(STREAMS)
        self.assertEqual(sum(x["title"].startswith("李家超訪京") for x in items("CEHK")), 1)

    def test_5_public_feed_and_approved_scores(self):
        feed = site("feed.feed")
        ce = [x for x in feed["items"] if x["stream"] == "CEHK"]
        self.assertTrue(ce)
        self.assertFalse(any("score" in x or "reason" in x for x in feed["items"]))
        self.assertEqual(ce[0]["category"], "言論及表態")  # categories are public (each 串流 card shows its label)
        sc = {"scores": site("scores.streamScores", insights.today())}  # /app/stream/scores (approved accounts)
        self.assertEqual(sc["scores"]["CEHK|https://news.test/1"], {"score": 90, "reason": "特首本人發言"})

    def test_5b_unscored_item_falls_back_to_scores_table(self):
        """an item filed by the keyword flow carries no score; /app/stream/scores shows the post's latest score from
        the scores table once (backfill) scoring reaches it, and nothing for it before that"""
        poll([post(160, "舊流程收錄的新聞")])
        url, day = "https://news.test/160", insights.today()
        pid = corpus.ids_for_urls([url])[url]
        with corpus.db() as c:
            c.execute("DELETE FROM scores WHERE post_id=%s", (pid,))
            c.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, 'CEHK', %s, %s)",
                      (day, url, json.dumps({"title": "舊流程收錄的新聞", "url": url,  # every item carries seen
                                              "seen": NOW.isoformat(timespec="seconds")})))  # (串流's day, 8781a6f)
        try:
            sc = site("scores.streamScores", day)
            self.assertNotIn(f"CEHK|{url}", sc)  # pending: not scored yet
            self.assertEqual(sc["CEHK|https://news.test/1"], {"score": 90, "reason": "特首本人發言"})  # own score wins
            with corpus.db() as c:
                c.execute("INSERT INTO scores (stream, post_id, score, reason, scored_at) VALUES ('CEHK', %s, 77, '補評分', %s)",
                          (pid, day))
            sc = site("scores.streamScores", day)
            self.assertEqual(sc[f"CEHK|{url}"], {"score": 77, "reason": "補評分", "from": "scores"})
        finally:
            with corpus.db() as c:
                c.execute("DELETE FROM stream_items WHERE url=%s", (url,))
                c.execute("DELETE FROM scores WHERE post_id=%s", (pid,))

    def test_6_telegram_sends_what_is_filed(self):
        """the channel is 串流's copy (Deyao, 2026-10-15: filed ⇒ member, nothing recalled): a threshold raised after
        filing doesn't hold an item back"""
        sent = []
        telegram_alerts.post = lambda chat, it, sid=None: sent.append(it["title"])
        st = [{"id": "CEHK", "telegram_chat": -1}]
        corpus.kv_set(telegram_alerts.KEY, {"CEHK": 0})
        telegram_alerts.GAP = 0
        thresholds.main(["set", "CEHK", "95"])
        telegram_alerts.send_due(st)
        self.assertEqual(sent, [x["title"] for x in items("CEHK")])
        thresholds.main(["set", "CEHK", "50"])

    def test_7_source_gaps(self):
        streams._watched["at"] = 0
        poll([post(40, "某新聞", sid="gnews_0", url="https://www.unwatched.hk/a/1", section="未知報"),
              post(41, "另一新聞", sid="gnews_1", url="https://news.test/zz")])  # news.test is watched (rthk posts)
        g = corpus.kv_get("source_gaps")
        self.assertEqual(list(g), ["unwatched.hk"])
        self.assertEqual((g["unwatched.hk"]["count"], g["unwatched.hk"]["name"]), (1, "未知報"))


    def test_8_scored_after_the_page_fetch(self):
        import fulltext_now
        streams.WAIT_TEXT = timedelta(minutes=2)
        with fulltext_now.Q.mutex:
            fulltext_now.Q.queue.clear()  # the earlier tests' posts
        try:
            poll([post(50, "記者會今日舉行", text="短"), post(51, "信報：記者會", text="短", url="https://www.hkej.com/x/1"),
                  post(52, "長文", text="李家超" + "字" * 600)])
            got = {p["url"] for p in streams.due(50)}
            self.assertNotIn("https://news.test/50", got)  # waits for its page
            self.assertNotIn("https://www.hkej.com/x/1", got)  # 信報: waits for the 30-minute job's read too
            self.assertIn("https://news.test/52", got)  # the feed carried the article
            pid = corpus.ids_for_urls(["https://news.test/50"])["https://news.test/50"]
            self.assertEqual([x[0] for x in list(fulltext_now.Q.queue)], [pid])  # queued at ingest; 51/52 aren't
            fulltext_now.cache_fetch.fetch_one = lambda url, meta=None: ("行政長官李家超在記者會上宣布新政策。" * 5, None, None)
            fulltext_now.fetch(*fulltext_now.Q.get_nowait())
            self.assertIn("https://news.test/50", {p["url"] for p in streams.due(50)})
            before = len(CALLS)
            streams.run_once(STREAMS)
            self.assertIn("行政長官李家超在記者會上宣布新政策", "".join(CALLS[before:]))  # the model read the full page
            self.assertIn("記者會今日舉行", [x["title"] for x in items("CEHK")])
        finally:
            streams.WAIT_TEXT = timedelta(0)

    def test_9_dashboard_is_the_scored_set(self):
        day = insights.today()
        poll([post(60, "李家超今日見記者"), post(61, "港府公布新政策"), post(62, "政府新聞：天氣")])
        streams.run_once(STREAMS)
        ids = corpus.ids_for_urls([f"https://news.test/{i}" for i in (60, 61, 62)])
        p60, p61, p62 = (ids[f"https://news.test/{i}"] for i in (60, 61, 62))
        # a keyword-era row (政府 was a CEHK keyword) and a custom profile's leftovers: not the scorer's set
        corpus.set_tags("CEHK", [(p62, True, "neutral", [])])
        corpus.set_tags("OLDKW", [(p60, True, "neutral", [])])

        def scored_set(t):  # the members: scored at or above t, or filed in CEHK (filed ⇒ member, Deyao 2026-10-15)
            return {r[0] for r in corpus.db().execute(
                """SELECT s.post_id FROM scores s JOIN posts p ON p.id=s.post_id WHERE s.stream='CEHK' AND s.score >= %s
                   AND p.day=%s UNION SELECT p.id FROM stream_items si JOIN posts p ON p.url=si.url
                   WHERE si.stream='CEHK' AND p.day=%s""", (t, day, day))}

        def dashboard():
            q = {"p": "CEHK", "from": day, "to": day}
            posts, hot, summ, ex = site("dashboard.apiBatch", [["posts", dict(q, n="200")], ["hot_topics", dict(q, top="500")],
                                                              ["summary", q]]) + \
                [edge("dashboard-export-node.exportFile", "posts", q)]
            self.assertEqual(posts["status"], 200)
            posts, hot, summ = posts["body"], hot["body"], summ["body"]
            got = {x["id"] for x in posts["posts"]}
            self.assertEqual(posts["total"], len(got))
            self.assertEqual(summ["metrics"]["posts"], len(got))  # 摘要 counts the same posts
            self.assertEqual(sum(t["matched_post_count"] for t in hot["topics"]), len(got))  # 熱門話題 too
            from openpyxl import load_workbook
            with open(ex["path"], "rb") as fh:  # 匯出 Excel
                data = fh.read()
            cells = [str(c) for row in load_workbook(io.BytesIO(data))["Post Listing"].iter_rows(values_only=True) for c in row]
            urls = {r[0]: r[1] for r in corpus.db().execute("SELECT id, url FROM posts WHERE id = ANY(%s)", (list(got),))}
            self.assertEqual({c for c in cells if c.startswith("https://news.test/")} & set(urls.values()), set(urls.values()))
            return got

        self.assertEqual(dashboard(), scored_set(50))
        self.assertIn(p60, dashboard())
        self.assertNotIn(p62, dashboard())  # the stale tag row doesn't make it a member
        prof = edge_config.build()  # the profiles the site lists
        self.assertEqual({x["id"] for x in prof["profiles"]}, {s["id"] for s in STREAMS})
        self.assertNotIn("query", prof["profiles"][0])

        streams.sync_tags(STREAMS)  # what the poller does at every start
        tagged = {r[0] for r in corpus.db().execute("SELECT post_id FROM tags t JOIN posts p ON p.id=t.post_id WHERE profile='CEHK' AND p.day=%s", (day,))}
        self.assertEqual(tagged, scored_set(50))  # the tags agree: the keyword-era row is gone
        self.assertFalse(corpus.db().execute("SELECT 1 FROM tags WHERE profile='OLDKW'").fetchone())

        thresholds.main(["set", "CEHK", "25"])  # 港府 alone scores 30: in at once on 數據分析, its tags too
        try:
            self.assertEqual(dashboard(), scored_set(25))
            self.assertIn(p61, dashboard())
            tagged = {r[0] for r in corpus.db().execute("SELECT post_id FROM tags t JOIN posts p ON p.id=t.post_id WHERE profile='CEHK' AND p.day=%s", (day,))}
            self.assertEqual(tagged, scored_set(25))
        finally:
            thresholds.main(["set", "CEHK", "50"])
        self.assertEqual(dashboard(), scored_set(50))

    def test_9z_backfill_history(self):
        corpus.kv_set("score_backfill", {"on": True, "posts": 0})  # off by default: switched on by hand (a manual run)
        sent = []
        telegram_alerts.post = lambda chat, it, sid=None: sent.append(it["title"])
        telegram_alerts.GAP = 0
        tg = [{"id": "CEHK", "telegram_chat": -1}, {"id": "HKIC", "telegram_chat": -2}]
        corpus.kv_set(telegram_alerts.KEY, {x["id"]: corpus.db().execute(
            "SELECT coalesce(max(id), 0) FROM stream_items WHERE stream=%s", (x["id"],)).fetchone()[0] for x in tg})
        poll([post(70, "十日前 李家超施政報告", published=NOW - timedelta(days=10)),
              post(71, "金管局舊聞", published=NOW - timedelta(days=40))])
        old = corpus.ids_for_urls([f"https://news.test/{i}" for i in (0, 5, 70, 71)])
        poll([post(72, "李家超今晚記者會")])  # live: due now
        n_items = corpus.db().execute("SELECT count(*) FROM stream_items").fetchone()[0]

        before = len(CALLS)
        self.assertEqual(streams.backfill_once(STREAMS), "live")  # live scoring has a post due: the backfill waits
        self.assertEqual(len(CALLS), before)
        self.assertEqual(streams.run_once(STREAMS), 1)
        self.assertIn("李家超今晚記者會", [x["title"] for x in items("CEHK")])  # live filed it as usual
        n_items += 1

        due = [p["url"] for p in streams.backfill_due(50, STREAMS)]
        self.assertEqual(due, ["https://news.test/0", "https://news.test/5", "https://news.test/70",
                               "https://news.test/71"])  # newest first (0: before the switch); not 72, live's own
        streams.BATCH, batch = 50, streams.BATCH
        try:
            self.assertEqual(streams.backfill_once(STREAMS), "scored")
        finally:
            streams.BATCH = batch
        self.assertEqual(CALLS[-1].count("十日前 李家超施政報告"), 1)
        sc = {r[0]: r[1] for r in corpus.db().execute(
            "SELECT post_id, score FROM scores WHERE stream='CEHK' AND post_id = ANY(%s)", (list(old.values()),))}
        self.assertEqual(sc, {old["https://news.test/0"]: 90, old["https://news.test/5"]: 90, old["https://news.test/70"]: 90})
        members = insights.members("CEHK", "2000-01-01", insights.today())
        self.assertTrue({old["https://news.test/0"], old["https://news.test/70"]} <= set(members))  # 數據分析 has the history
        self.assertIn(old["https://news.test/71"], insights.members("HKIC", "2000-01-01", insights.today()))
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM stream_items").fetchone()[0], n_items)  # 串流 unchanged
        telegram_alerts.send_due(tg)
        self.assertEqual(sent, ["李家超今晚記者會"])  # Telegram: only the live item

        before = len(CALLS)
        self.assertEqual(streams.backfill_once(STREAMS), "done")  # idempotent: nothing scored twice
        self.assertEqual(len(CALLS), before)
        self.assertEqual(streams.backfill_settings()["state"], "done")

        changed = [dict(x) for x in STREAMS]
        changed[0]["brief"] = (changed[0].get("brief") or "") + " (edited)"
        self.assertTrue(streams.backfill_due(1, changed))  # a definition change makes the history due again

        corpus.db().execute("DELETE FROM scored WHERE post_id=%s", (old["https://news.test/70"],))
        self.assertFalse(hasattr(streams, "budget"))  # no reserve: nothing projected decides whether it runs
        real = ai.jsonc

        def refused(prompt, purpose, model=None, max_tokens=4000, **opts):
            raise ai.LimitReached("Key limit exceeded (monthly limit)")
        ai.jsonc = refused
        try:
            self.assertEqual(streams.backfill_once(STREAMS), "limit")  # OpenRouter's own refusal stops it
        finally:
            ai.jsonc = real
        self.assertEqual(len(CALLS), before)
        self.assertEqual(streams.backfill_settings()["state"], "limit")
        self.assertEqual(streams.backfill_settings()["left"], 1)
        # OpenRouter answers again while live scoring has posts due: the backfill waits for them, and its state says
        # so (16 Oct 2026: the monitor showed "OpenRouter limit, checked 5.0 h ago" for hours after the limit cleared)
        poll([post(73, "李家超再見記者")])
        self.assertEqual(streams.backfill_once(STREAMS), "live")
        self.assertEqual(streams.backfill_settings()["state"], "live")
        self.assertIsNone(streams.backfill_settings().get("error"))
        self.assertEqual(streams.run_once(STREAMS), 1)
        self.assertEqual(streams.backfill_once(STREAMS), "scored")  # works again: carries on
        self.assertEqual(streams.backfill_settings()["state"], "running")
        thresholds.main(["backfill", "off"])
        self.assertEqual(streams.backfill_once(STREAMS), "off")
        thresholds.main(["backfill", "on"])


if __name__ == "__main__":
    unittest.main()
