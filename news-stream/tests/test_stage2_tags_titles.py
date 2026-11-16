#!/usr/bin/env python3
"""Tags and titles from the stage-2 call (Deyao, 2026-10-20: "the tagging should also be done by Opus, for the ones
that pass the relevance threshold"; "the AI title should come from the same Opus stage-2 call too"; the title rule:
"the title differs too much from the content"). On a throwaway database (TEST_DATABASE_URL), both models and
Telegram stubbed.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_stage2_tags_titles.py

A post that reaches stage 2 (a pair Haiku scored at or above streams.SCREEN, or a stream the post is filed in) gets
from that one Opus call its sentiment, labels and, per checked stream, the score, reason and category, and Opus's
judgement of its title (streams.TITLE, one general instruction; Deyao, 2026-10-23): title_ok, else a title of Opus's
(kind "extracted" when it is the post's own opening headline, "written" when the post has no title of its own,
"retitled" when it has one, titles.own), stored with the model. scored.tag_model names the model the post's sentiment and labels are from.

Covers:
  1. a filed post: item, scored, tags row and the Telegram message carry Opus's sentiment, labels, category and
     reason (Haiku's differ); tag_model = Opus on the item and in scored;
  2. Opus's sentiment not one of ai.SENTIMENTS: none stored (never Haiku's), tag_model Opus;
  3. under the screen: Haiku's tags as before, tag_model Haiku;
  4. a filed post rescored with Haiku now under the screen: Opus scores its filed stream again, its tags Opus's;
  5. a social post whose title is the start of its text: Opus's written title stored (model Opus), no titles call;
  6. a news post with a generic title (「1100 新聞簡報」): Opus's title stored as "retitled", the post's own title kept;
  7. Opus says the title is fine: no AI title, checked on this content (no titles call later); 7b. Opus says
     "title_ok" for a post with no title of its own (shown with none): that counts for nothing, the titles call
     titles it (29 Oct 2026: 369 filed posts in 72 h were checked untitled that way);
  8. an extracted headline that is in the post's opening: kind "extracted";
  9. Opus gives no usable title for a post with no title of its own: the titles call (titles.ensure) titles it,
     since stage 2 reached it, before it is filed;
 10. a post that doesn't reach stage 2: titles as before (titles call), Opus never asked;
 11. the stage-2 prompt asks for the title judgement (streams.TITLE, nothing added per post); stage 1's does not;
 12. a post titled with its first paragraph: the model's judgement decides (no length rule in code); a title of
     Opus's is stored as "retitled", the post's own title kept;
 13. the model calls such a title fine: no AI title, checked on this content;
 14. such a title on a post that doesn't reach stage 2 stays as it is;
 15. a post with no title of its own (Facebook, Instagram, Threads, X, Telegram, TikTok…): the full text kept, the
     title the excerpt of its opening, title_from_body (corpus.ingest);
 16. the scorer reads such a post as it is: no title (its excerpt is not shown as one) and its whole text;
 17. tools/fix_body_titles.py corrects the stored posts from the restart cutoff on, without making them due;
 18. a comment row's title is its post's excerpt (and the tool corrects stored comment rows);
 19. the scorer's post line shows the post as it is: its own title (none for an excerpt, a placeholder or a link
     waiting for its headline, shown as streams.NO_TITLE) and its text whenever the text says more than the title;
 20. a "checked, none" from a stale read (the titles thread) never overwrites the title stage 2 stored meanwhile.
"""
import json, os, re, sys, tempfile, unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, insights, sources, streams, telegram_alerts, titles  # noqa: E402

STREAMS = app.STREAMS
NOW = datetime.now(app.HKT)
CALLS = []   # (purpose, model, prompt)
OPUS = streams.CONFIRM_MODEL

# markers in a post: CEHK:h=60/言論及表態,o=85/施政及政策  SENT:h=neutral,o=negative  LAB:h=chance,o=issue+repute
# TITLE:ok  |  TITLE:written/標題  |  TITLE:extracted/標題  (Opus's title judgement; none: Opus leaves it out)
SC = re.compile(r"\b([A-Z_]+):h=(\d+)(?:/(\S+?))?(?:,o=(\d+)(?:/(\S+?))?)?(?=\s)")
SENT = re.compile(r"\bSENT:h=(\w+),o=(\w+)")
LAB = re.compile(r"\bLAB:h=([\w+]*),o=([\w+]*)")
TITLE = re.compile(r"\bTITLE:(ok|written|extracted|none)(?:/(\S+))?")


def blocks(prompt):
    body = prompt.split("Posts:\n", 1)[1]
    out = {}
    for i in re.findall(r"(?m)^(\d+)\. \[", body):
        b = body.split(f"\n{i}. ", 1)[-1] if i != "0" else body
        out[int(i)] = b.split(f"\n{int(i) + 1}. ", 1)[0]
    return out


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000, **kw):
    CALLS.append((purpose, model, prompt))
    if purpose == titles.PURPOSE:
        got = [{"i": int(i), "kind": "written", "title": "標題服務寫的標題內容足夠長"} for i in re.findall(r'<post i="(\d+)"', prompt)]
        return {"titles": got}
    if not purpose.startswith("scoring"):
        return {"tags": []}
    opus = model == OPUS
    out = []
    for i, b in blocks(prompt).items():
        st = []
        for sid, h, hc, o, oc in SC.findall(b + " "):
            if sid in ("SENT", "LAB", "TITLE"):
                continue
            v, cat = (o, oc) if opus else (h, hc)
            if v:
                st.append({"id": sid, "score": int(v), "reason": ("O" if opus else "H") + v, "category": cat or None})
        s = SENT.search(b)
        lab = LAB.search(b)
        x = {"i": i, "sentiment": (s.group(2) if opus else s.group(1)) if s else "neutral",
             "labels": [l for l in ((lab.group(2) if opus else lab.group(1)) if lab else "").split("+") if l],
             "streams": st}
        t = TITLE.search(b)
        if opus and t and t.group(1) != "none":
            if t.group(1) == "ok":
                x["title_ok"] = True
            else:
                x.update(title_ok=False, title_kind=t.group(1), title=t.group(2))
        out.append(x)
    return {"posts": out}


ai.jsonc = fake_jsonc
ai.chat = lambda messages, purpose, **k: {"content": "hk"}
streams.WAIT_TEXT = timedelta(0)

TG = []


def fake_call(method, **params):
    TG.append((method, params))
    return {"message_id": len(TG), "chat": {"id": params.get("chat_id")}} if method == "sendMessage" else True


telegram_alerts.call = fake_call
telegram_alerts.GAP = 0
TGS = [{"id": "CEHK", "telegram_chat": -1}]


def poll(items):
    sources.fetch_all = lambda extra: (list(items), {"x": {"ok": True, "items": len(items), "error": None}})
    sources.discuss_search = lambda qs: []
    app.after_poll = lambda g, f: insights.after_poll(g, f)
    app.threading = type("T", (), {"Thread": lambda target, args, daemon: type("X", (), {"start": lambda s: target(*args)})()})
    app.load_state()
    app.poll_once()


def news(url, title, text):
    return {"sid": "rthk", "source": "RTHK", "title": title, "url": url, "published": NOW, "text": text}


def social(url, text):
    """a Telegram channel post (its collector puts the start of its text in its title; not a two-pass unit)"""
    return {"sid": "tg_hk01", "source": "香港01 (Telegram)", "title": text[:160], "url": url, "published": NOW, "text": text}


def pid(url):
    return corpus.ids_for_urls([url])[url]


def filed(url, stream="CEHK"):
    r = corpus.db().execute("SELECT item FROM stream_items WHERE stream=%s AND url=%s", (stream, url)).fetchone()
    return r[0] if r else None


def scored(url):
    return dict(corpus.db().execute("SELECT * FROM scored WHERE post_id=%s", (pid(url),)).fetchone())


def post_row(url):
    return dict(corpus.db().execute("SELECT * FROM posts WHERE url=%s", (url,)).fetchone())


def purposes():
    return [c[0] for c in CALLS]


def start_sending():
    corpus.kv_set(telegram_alerts.KEY, {"CEHK": corpus.db().execute(
        "SELECT coalesce(max(id), 0) FROM stream_items WHERE stream='CEHK'").fetchone()[0]})
    TG.clear()


TEXT = "字" * 600


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.db()
        corpus.kv_set("scoring", {"since": NOW.isoformat(), "from_id": 0, "comments": False})
        corpus.kv_set("score_backfill", {"on": False, "posts": 0})
        poll([news("https://seed.test/0", "seed", "x" * 600),
              social("https://t.me/hk01/1", "種子帖文" * 40)])

    def setUp(self):
        CALLS.clear()

    def test_01_filed_post_has_opus_tags(self):
        start_sending()
        url = "https://news.test/tags-1"
        poll([news(url, "李家超公佈施政報告", "CEHK:h=60/言論及表態,o=85/施政及政策 SENT:h=neutral,o=negative "
                                       "LAB:h=chance,o=issue+repute TITLE:ok " + TEXT)])
        streams.run_once(STREAMS)
        it = filed(url)
        self.assertEqual((it["sentiment"], it["labels"], it["category"], it["reason"], it["model"], it["tag_model"]),
                         ("negative", ["issue", "repute"], "施政及政策", "O85", OPUS, OPUS))
        s = scored(url)
        self.assertEqual((s["sentiment"], json.loads(s["labels"]), s["tag_model"]), ("negative", ["issue", "repute"], OPUS))
        t = corpus.db().execute("SELECT sentiment, labels FROM tags WHERE profile='CEHK' AND post_id=%s", (pid(url),)).fetchone()
        self.assertEqual((t[0], json.loads(t[1])), ("negative", ["issue", "repute"]))
        r = corpus.db().execute("SELECT category, reason FROM scores WHERE stream='CEHK' AND post_id=%s", (pid(url),)).fetchone()
        self.assertEqual(tuple(r), ("施政及政策", "O85"))
        telegram_alerts.send_due(TGS)
        msgs = [p["text"] for m, p in TG if m == "sendMessage"]
        self.assertEqual(len(msgs), 1)
        self.assertIn("【施政及政策】", msgs[0])

    def test_02_opus_invalid_sentiment_is_not_haikus(self):
        url = "https://news.test/tags-2"
        poll([news(url, "李家超出席典禮", "CEHK:h=60,o=80 SENT:h=positive,o=bogus LAB:h=chance,o=issue TITLE:ok " + TEXT)])
        streams.run_once(STREAMS)
        it = filed(url)
        self.assertEqual((it["sentiment"], it["labels"], it["tag_model"]), (None, ["issue"], OPUS))
        self.assertEqual((scored(url)["sentiment"], scored(url)["tag_model"]), (None, OPUS))

    def test_03_under_the_screen_haiku_tags(self):
        url = "https://news.test/tags-3"
        poll([news(url, "政府資訊", "CEHK:h=10,o=90 SENT:h=positive,o=negative LAB:h=chance,o=issue " + TEXT)])
        streams.run_once(STREAMS)
        self.assertNotIn(OPUS, [c[1] for c in CALLS])
        s = scored(url)
        self.assertEqual((s["sentiment"], json.loads(s["labels"]), s["tag_model"]), ("positive", ["chance"], ai.MODEL))

    def test_04_filed_post_rescored_under_the_screen_gets_opus(self):
        url = "https://news.test/tags-4"
        poll([news(url, "李家超視察", "CEHK:h=60,o=85 SENT:h=neutral,o=negative TITLE:ok " + TEXT)])
        streams.run_once(STREAMS)
        self.assertIsNotNone(filed(url))
        with corpus.db() as c:  # its page read later: new content, Haiku now under the screen
            c.execute("UPDATE posts SET text=%s WHERE id=%s",
                      ("CEHK:h=10,o=70 SENT:h=positive,o=mixed LAB:h=chance,o=issue TITLE:ok " + TEXT + "新", pid(url)))
        CALLS.clear()
        streams.run_once(STREAMS)
        self.assertIn("scoring_verify", purposes())
        s = scored(url)
        self.assertEqual((s["sentiment"], json.loads(s["labels"]), s["tag_model"]), ("mixed", ["issue"], OPUS))
        r = corpus.db().execute("SELECT score, model, screen FROM scores WHERE stream='CEHK' AND post_id=%s", (pid(url),)).fetchone()
        self.assertEqual(tuple(r), (70, OPUS, 10))

    def test_05_social_post_written_title_from_stage2(self):
        url = "https://t.me/hk01/5"
        poll([social(url, "李家超今日出席活動並發表講話，提到施政方向。CEHK:h=60,o=85 TITLE:written/李家超出席活動談施政方向及未來工作重點 "
                          + TEXT)])
        streams.run_once(STREAMS)
        p = post_row(url)
        self.assertEqual((p["ai_title"], p["ai_title_kind"], p["ai_title_model"]),
                         ("李家超出席活動談施政方向及未來工作重點", "written", OPUS))  # no title of its own
        self.assertNotIn(titles.PURPOSE, purposes())
        self.assertEqual(p["ai_title_hash"], titles.content_hash(p))

    def test_06_generic_title_retitled(self):
        url = "https://news.test/tags-6"
        poll([news(url, "1100 新聞簡報", "CEHK:h=60,o=85 TITLE:written/亞太經合組織財長會議在港舉行李家超致辭 " + TEXT)])
        streams.run_once(STREAMS)
        p = post_row(url)
        self.assertEqual((p["ai_title"], p["ai_title_kind"], p["ai_title_model"], p["title"]),
                         ("亞太經合組織財長會議在港舉行李家超致辭", "retitled", OPUS, "1100 新聞簡報"))
        self.assertNotIn(titles.PURPOSE, purposes())

    def test_07_title_ok_no_ai_title(self):
        url = "https://news.test/tags-7"
        poll([news(url, "李家超宣布新措施", "CEHK:h=60,o=85 TITLE:ok " + TEXT)])
        streams.run_once(STREAMS)
        p = post_row(url)
        self.assertIsNone(p["ai_title"])
        self.assertEqual(p["ai_title_hash"], titles.content_hash(p))

    def test_08_extracted_headline(self):
        url = "https://t.me/hk01/8"
        head = "李家超：孫中山愛國思想激勵中華兒女"
        poll([social(url, head + "\n特首今日在紀念活動上致辭，提到國家發展。CEHK:h=60,o=85 TITLE:extracted/" + head + " " + TEXT)])
        streams.run_once(STREAMS)
        p = post_row(url)
        self.assertEqual((p["ai_title"], p["ai_title_kind"], p["ai_title_model"]), (head, "extracted", OPUS))

    def test_07b_title_ok_on_a_post_with_no_title(self):
        url = "https://t.me/hk01/7b"
        poll([social(url, "【全球大學校長及領袖高峰會2026】 今天我很高興出席。CEHK:h=60,o=85 TITLE:ok " + TEXT)])
        streams.run_once(STREAMS)
        self.assertIn(titles.PURPOSE, purposes())
        p = post_row(url)
        self.assertEqual((p["ai_title"], p["ai_title_kind"]), ("標題服務寫的標題內容足夠長", "written"))
        self.assertEqual(filed(url)["title"], p["title"])  # filed after the title: the site shows the post's AI title
        CALLS.clear()
        titles.run_once()
        self.assertNotIn(titles.PURPOSE, purposes())  # titled on this content: not due again

    def test_09_no_usable_title_titled_by_the_titles_call(self):
        """stage 2 gives no title for a post with no title of its own: the titles call titles it before filing"""
        url = "https://t.me/hk01/9"
        poll([social(url, "特首今日出席活動並發表講話，談及多項施政。CEHK:h=60,o=85 TITLE:none " + TEXT)])
        sent = []
        orig = titles.title_posts
        titles.title_posts = lambda posts, scored=(): (sent.append(filed(url)), orig(posts, scored))[1]
        try:
            streams.run_once(STREAMS)
        finally:
            titles.title_posts = orig
        self.assertIn(titles.PURPOSE, purposes())
        self.assertEqual(sent, [None])  # before it was filed
        p = post_row(url)
        self.assertEqual((p["ai_title"], p["title"]), ("標題服務寫的標題內容足夠長", "特首今日出席活動並發表講話，談及多項施政。"))

    def test_10_not_in_stage2_the_excerpt_shows(self):
        """a post with no title of its own that doesn't reach stage 2: the excerpt of its opening, no model call for
        a title (Deyao, 2026-10-20)"""
        url = "https://t.me/hk01/10"
        poll([social(url, "今日天氣晴朗，市民外出遊玩。CEHK:h=15,o=85 TITLE:written/不應出現 " + TEXT)])
        streams.run_once(STREAMS)
        self.assertNotIn(OPUS, [c[1] for c in CALLS])
        self.assertNotIn(titles.PURPOSE, purposes())
        p = post_row(url)
        self.assertEqual((p["ai_title"], p["title"]), (None, "今日天氣晴朗，市民外出遊玩。"))
        self.assertEqual(p["ai_title_hash"], titles.content_hash(p))  # checked: the titles thread leaves it

    def test_11_prompts(self):
        url = "https://news.test/tags-11"
        poll([news(url, "李家超會見訪客", "CEHK:h=60,o=85 TITLE:ok " + TEXT)])
        streams.run_once(STREAMS)
        haiku = [c[2] for c in CALLS if c[0] == "scoring"]
        opus = [c[2] for c in CALLS if c[0] == "scoring_verify"]
        self.assertTrue(haiku and opus)
        self.assertIn("title_ok", opus[0])
        self.assertNotIn("title_ok", haiku[0])
        head = opus[0].split("Posts:\n", 1)[0]
        self.assertIn(streams.TITLE, head)
        self.assertEqual(head.split(streams.TITLE, 1)[1].strip(), "")  # no per-post title lists after it

    LONG = ("【第六屆粵港澳大灣區高峰論壇】 我很高興出席第六屆粵港澳大灣區高峰論壇，和與會者就大灣區未來發展交流，"
            "並提出香港在2030年後，")

    def test_12_long_title_retitled_by_the_judgement(self):
        """Deyao, 2026-10-20: a post whose title is its whole first paragraph (行政長官 李家超's Facebook post): the
        stage-2 judgement (no length rule) gives it a title, stored as "retitled" with its own title kept"""
        url = "https://news.test/long-12"
        poll([news(url, self.LONG, "CEHK:h=60,o=85 TITLE:written/李家超出席大灣區高峰論壇談香港2030年後發展 " + TEXT)])
        streams.run_once(STREAMS)
        opus = [c[2] for c in CALLS if c[0] == "scoring_verify"]
        self.assertRegex(opus[0], r"(?m)^0\. \[[^\]\n]*\] " + re.escape(self.LONG) + "\n")  # its own title, as it is
        p = post_row(url)
        self.assertEqual((p["ai_title"], p["ai_title_kind"], p["ai_title_model"], p["title"]),
                         ("李家超出席大灣區高峰論壇談香港2030年後發展", "retitled", OPUS, self.LONG))

    def test_13_long_title_judged_ok_is_kept(self):
        """the model calls such a title fine: no AI title, checked on this content"""
        url = "https://news.test/long-13"
        poll([news(url, self.LONG, "CEHK:h=60,o=85 TITLE:ok " + TEXT)])
        streams.run_once(STREAMS)
        p = post_row(url)
        self.assertIsNone(p["ai_title"])
        self.assertEqual(p["ai_title_hash"], titles.content_hash(p))

    def test_14_long_title_under_the_screen_stays(self):
        url = "https://news.test/long-14"
        poll([news(url, self.LONG, "CEHK:h=15,o=85 TITLE:written/不應出現 " + TEXT)])
        streams.run_once(STREAMS)
        self.assertNotIn(OPUS, [c[1] for c in CALLS])
        self.assertIsNone(post_row(url)["ai_title"])


    FB = ("【第六屆粵港澳大灣區高峰論壇】 我很高興出席第六屆粵港澳大灣區高峰論壇，和與會者就大灣區未來發展交流，"
          "並提出香港在2030年後，繼續發揮優勢，與大灣區城市協同發展。\n今日的論壇聚集了多位嘉賓。" + "字" * 400)

    def test_15_no_title_of_its_own_at_ingest(self):
        """Deyao, 2026-10-20: Facebook posts have no title; the collector put the body in the title field. Stored:
        the full text as the text, the title a short excerpt of its opening, title_from_body"""
        url = "https://www.facebook.com/JohnLeeKaChiu/posts/15"
        corpus.ingest([{"sid": "facebook", "source": "李家超 John Lee Ka-chiu (Facebook)", "section": "Facebook",
                        "title": self.FB[:160], "url": url, "published": NOW.isoformat(), "text": self.FB}])
        p = post_row(url)
        self.assertTrue(p["title_from_body"])
        self.assertEqual(p["text"], self.FB)
        self.assertEqual(p["title"], "【第六屆粵港澳大灣區高峰論壇】 我很高興出席第六屆粵港澳大灣區高峰論壇，和與會者就大灣區…")
        corpus.ingest([{"sid": "facebook", "source": "x (Facebook)", "title": "只有標題的帖文，沒有內文欄位，標題就是全文的內容",
                        "url": url + "1", "published": NOW.isoformat()}])
        self.assertEqual(post_row(url + "1")["text"], "只有標題的帖文，沒有內文欄位，標題就是全文的內容")
        corpus.ingest([news("https://news.test/own-15", "李家超出席論壇", "李家超出席論壇。" * 40)])
        n = post_row("https://news.test/own-15")
        self.assertEqual((n["title"], n["title_from_body"]), ("李家超出席論壇", None))
        for sid, plat in (("tikhub_twitter", "X"), ("instagram", "Instagram"), ("tikhub_tiktok", "TikTok"),
                          ("tg_hk01", "Telegram"), ("tikhub_threads", "Threads"), ("tikhub_tiktok", "TikTok")):
            self.assertTrue(corpus.from_body(sid, plat, None, "y" * 20, "y" * 50), sid)
        self.assertFalse(corpus.from_body("tikhub_youtube", "YouTube", None, "t", "x"))
        self.assertFalse(corpus.from_body("facebook", "Facebook", "https://www.facebook.com/x", "t", "x"))  # a comment

    def test_16_scorer_reads_the_whole_text(self):
        """the scorer reads a post with no title of its own as it is: no title (its stored excerpt is not one) and its
        whole text (the 160-character title hid the rest of a Facebook post's text from the scorer)"""
        url = "https://t.me/hk01/16"
        poll([social(url, "特首出席活動。CEHK:h=60,o=85 TITLE:written/特首出席活動並談及香港未來發展方向 " + "長" * 300 + "尾段內容")])
        streams.run_once(STREAMS)
        haiku = [c[2] for c in CALLS if c[0] == "scoring"][0]
        self.assertIn("尾段內容", haiku)
        opus = [c[2] for c in CALLS if c[0] == "scoring_verify"][0]
        self.assertRegex(opus.split("Posts:\n", 1)[1], r"^0\. \[[^\]\n]*\] （無標題）\n   特首出席活動。")

    def test_17_stored_posts_corrected_from_the_cutoff(self):
        import tools.fix_body_titles as fix
        new, old = "https://www.facebook.com/fix/posts/1", "https://www.facebook.com/fix/posts/2"
        with corpus.db() as c:
            for url, seen in ((new, "2026-10-20T10:00:00+08:00"), (old, "2026-10-20T09:00:00+08:00")):
                c.execute("""INSERT INTO posts (url, sid, source, platform, source_type, title, text, seen, day)
                             VALUES (%s, 'facebook', 'x (Facebook)', 'Facebook', 'Social Media', %s, %s, %s, '2026-10-20')""",
                          (url, self.FB[:160], self.FB, seen))
            c.execute("INSERT INTO scored (post_id, hash, at) VALUES (%s, md5(%s || chr(10) || %s), 'x')",
                      (pid(new), self.FB[:160], self.FB))
            c.execute("INSERT INTO stream_items (day, stream, url, item) VALUES ('2026-10-20', 'CEHK', %s, %s)",
                      (new, corpus.J({"title": self.FB[:160], "url": new})))
        self.assertEqual(fix.run(), 1)
        p = post_row(new)
        self.assertEqual((p["title"], p["text"], p["title_from_body"]), (corpus.body_title(self.FB[:160], self.FB)[0], self.FB, True))
        self.assertEqual(scored(new)["hash"], titles.content_hash(p))  # not due again: no rescoring
        self.assertEqual(filed(new)["title"], p["title"])
        self.assertEqual(post_row(old)["title"], self.FB[:160])  # before the cutoff: left alone
        self.assertEqual(fix.run(), 0)

    def test_18_comments_carry_the_posts_excerpt(self):
        """a comment row's title is its post's: the excerpt for a post with no title of its own, not the post's
        first paragraph (Deyao, 2026-10-20: 李家超's Facebook post); tools/fix_body_titles.py corrects stored ones"""
        import tools.fix_body_titles as fix
        url = "https://www.facebook.com/johnleekachiu/posts/18"
        thread = {"sid": "facebook", "source": "李家超 (Facebook)", "section": "Facebook", "title": self.FB[:160],
                  "url": url, "published": NOW.isoformat(), "text": self.FB}
        corpus.add_comments(thread, [{"id": "1", "author": "a", "text": "支持特首", "published": NOW.isoformat()}])
        c = post_row(url + "#c1")
        self.assertEqual(c["title"], post_row(url)["title"])
        self.assertLess(len(c["title"]), 60)
        old = url + "#c2"  # stored before: the post's first paragraph as its title
        with corpus.db() as cx:
            cx.execute("""INSERT INTO posts (url, sid, source, platform, source_type, title, text, seen, day, thread)
                          VALUES (%s, 'facebook', 'x (Facebook)', 'Facebook', 'Social Media', %s, '舊留言', %s, '2026-10-20', %s)""",
                       (old, self.FB[:160], "2026-10-20T10:00:00+08:00", url))
        fix.run()
        self.assertEqual(post_row(old)["title"], post_row(url)["title"])

    def test_19_post_line_shows_the_post_as_it_is(self):
        line = lambda **p: streams.post_line(0, dict({"website": "W"}, **p))
        self.assertEqual(line(title="特首出席活動…", text="特首出席活動並發表講話", title_from_body=True),
                         "0. [W] （無標題）\n   特首出席活動並發表講話")
        self.assertEqual(line(title="(post without text)", text="一段內容"), "0. [W] （無標題）\n   一段內容")
        self.assertEqual(line(title=corpus.LINK_TITLE + "x.com", text="https://x.com/1"), "0. [W] （無標題）\n   https://x.com/1")
        self.assertEqual(line(title="8099523", text="何局長離任聲明"), "0. [W] （無標題）\n   何局長離任聲明")  # a thread id
        body = self.LONG + "繼續發揮優勢，與大灣區城市協同發展。"  # a title that is the text's opening: the rest is shown
        self.assertEqual(line(title=self.LONG, text=body), f"0. [W] {self.LONG}\n   {body}")
        self.assertEqual(line(title="李家超出席論壇", text="李家超出席論壇"), "0. [W] 李家超出席論壇")  # not repeated
        # a headline alone: its page's address (host + decoded path, no query) says where the outlet filed it
        sina = "https://portal.sina.com.hk/sports/liverpool/2026/11/03/1975661/%e5%95%9f%e7%a8%8b%e4%bc%8a%e6%96%af%e5%9d%a6%e5%a0%a1/?utm=x"
        self.assertEqual(line(title="啟程伊斯坦堡", text="啟程伊斯坦堡", url=sina),
                         "0. [W] 啟程伊斯坦堡\n   網址：portal.sina.com.hk/sports/liverpool/2026/11/03/1975661/啟程伊斯坦堡")
        self.assertEqual(line(title="啟程伊斯坦堡", text="", url="https://www.x.hk/a"), "0. [W] 啟程伊斯坦堡\n   網址：x.hk/a")
        self.assertEqual(line(title="李家超出席論壇", text="李家超今日出席論壇並致辭。", url=sina),
                         "0. [W] 李家超出席論壇\n   李家超今日出席論壇並致辭。")  # a text of its own: no address
        self.assertEqual(line(title="李家超出席論壇", text="李家超出席論壇", url=sina, transcript="今日論壇"),
                         "0. [W] 李家超出席論壇\n   影片逐字稿：今日論壇")  # a transcript says what it is about

    def test_19b_stage2_prompt_is_stage1s(self):
        # nothing tells stage 2 what stage 1 answered or why a post is in its batch (Deyao, 2026-11-03): the same
        # prompt, plus its own title task; the code keeps the checked pairs
        sts = streams.all_streams()
        posts = [{"website": "W", "title": "啟程伊斯坦堡", "text": "啟程伊斯坦堡"}]
        one, two = streams.score_prompt(sts, posts), streams.score_prompt(sts, posts, titles=True)
        self.assertEqual(two.replace("\n" + streams.TITLE, ""), one)
        seen = []
        orig = ai.jsonc
        ai.jsonc = lambda prompt, *a, **k: seen.append(str(prompt)) or {"posts": [{"i": 0, "streams": [
            {"id": "CEHK", "score": 5, "reason": "r"}, {"id": "POL", "score": 70, "reason": "r"}]}]}
        try:
            got = streams._ask(sts, posts, "scoring_verify", model=streams.CONFIRM_MODEL, checks={0: ["CEHK"]})
        finally:
            ai.jsonc = orig
        self.assertEqual(seen, [two])
        self.assertEqual(set(got[0]["streams"]), {"CEHK"})

    def test_20_stale_checked_none_keeps_stage2s_title(self):
        url = "https://t.me/hk01/20"
        poll([social(url, "潘焯鴻致函特首。CEHK:h=5,o=0 " + TEXT)])
        p = dict(corpus.db().execute(f"SELECT p.*, {titles.HASH} AS hash FROM posts p WHERE url=%s", (url,)).fetchone())
        stale = dict(p)  # the titles thread's read, before stage 2 stored its title
        titles.store(p, "潘焯鴻致函特首促恢復逃生承諾", "written", OPUS)
        titles.store(stale, None)
        self.assertEqual(post_row(url)["ai_title"], "潘焯鴻致函特首促恢復逃生承諾")
        self.assertEqual(stale["ai_title"], "潘焯鴻致函特首促恢復逃生承諾")
        titles.store(dict(stale), None, force=True)  # stage 2's own judgement on the post it just scored writes
        self.assertIsNone(post_row(url)["ai_title"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
