#!/usr/bin/env python3
"""AI titles for posts with no real title (Deyao, 2026-10-14): a social post's text was used as its title (long,
cut off, unreadable in 串流, Telegram and 數據分析). The pipeline writes a short title with AI (titles.py), marked as
AI-written everywhere it is shown; the post's own text stays stored, intact, and is shown as the body. On a throwaway
database (TEST_DATABASE_URL; its tables are dropped and made again), the model stubbed.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_ai_titles.py

Covers:
  - titles.needs(): text-as-title platforms (Facebook, Instagram, Threads, Telegram, X, 微博, 抖音, 快手, TikTok…) need
    one; a post short enough to read as it is, news, forums, YouTube, comments and a real platform title do not; a
    title that is the cut-off start of the text, or no title at all, does.
  - the live scorer writes the AI title before it files the post, so the stream item and its Telegram message carry
    it: posts.ai_title + ai_title_model + ai_title_at; the original title column is untouched.
  - display: 串流 and 搜尋 (the edge app's, run in Node by tests/edge_call.py), item.view and 數據分析's post lists
    (corpus.posts) show the AI title as the title with title_by_ai, the original in original_title, and the post's
    full content as the text.
  - Telegram: 〔AI標題〕 before the AI title; a news headline has no mark.
  - Chinese titles are Traditional Chinese (Simplified converted, s2hk); the prompt (one general instruction) asks
    for a title in the post's language.
  - a text change makes the title due again; a post that needs none is checked once, not on every round.
  - the title backfill takes the last 7 days and every post in 串流, not older posts outside it.
  - 搜尋 finds a post by words of its AI title.
  - a post that opens with its own headline (a news outlet's Telegram channel or Facebook page: RTHK即時新聞's
    "李家超：孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥" then the body) has that headline surfaced word for word
    (ai_title_kind "extracted"): shown as the title with NO AI mark (site or Telegram), the body without it; a headline
    the model calls extracted but that isn't in the post word for word counts as written (marked). Emoji inside a
    headline don't break the match; 《》 in a headline stay.
  - a batch whose answer isn't JSON is asked again post by post.
  - since 2026-10-20 (Deyao) a post with no title of its own (corpus.from_body) is titled by stage-2 scoring
    (streams.confirm_title; stage2_titles stands in for it here): the titles thread leaves it to the scorer until it
    is scored on its content, then checks it without a model call when stage 2 never reached it, and titles it when
    stage 2 reached it but left it untitled, or when it is in 串流 (titles.recheck titles such posts checked before).
  - a news, forum or blog post whose collected title has no word in it (香港高登's sitemap slug 「8099523」) needs a
    title the same way; one with a real title does not.
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
import ai, app, corpus, item, searchtext, streams, telegram_alerts, titles, twopass  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)
from edge_call import edge  # noqa: E402

HKT = app.HKT
NOW = datetime.now(HKT)
CALLS = []
TITLES = {}  # a word in the post → the title the fake model writes


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000):
    CALLS.append((purpose, prompt))
    if purpose == "titles":
        body = prompt.split("Posts:\n", 1)[1]
        out = []
        for m in re.finditer(r"(?ms)^<post i=\"(\d+)\"[^>]*>(.*?)</post>", body):
            t = next((v for k, v in TITLES.items() if k in m.group(2)), "預設標題測試一下")
            kind, t = t if isinstance(t, tuple) else ("written", t)
            out.append({"i": int(m.group(1)), "kind": kind, "title": t})
        return {"titles": out}
    if purpose not in ("scoring", "scoring_backfill", "scoring_units"):  # scoring_units: social posts with their comments
        return {}
    body = prompt.split("Posts:\n", 1)[1]
    out = []
    for i in re.findall(r"(?m)^(\d+)\. \[", body):
        block = body.split(f"\n{i}. ", 1)[-1] if i != "0" else body
        block = block.split(f"\n{int(i) + 1}. ", 1)[0]
        st = [{"id": "CEHK", "score": 90, "reason": "特首", "category": "言論及表態"}] if "李家超" in block else []
        out.append({"i": int(i), "sentiment": "neutral", "labels": [], "streams": st})
    return {"posts": out}


import stage2  # noqa: E402
ai.jsonc = stage2.agree(fake_jsonc, CALLS)  # stage 2 answers as stage 1 (tests/stage2.py)


def with_stage2_titles(jsonc):
    """stage 2 also gives a title (streams.TITLE) for a post shown with no title (one with no title of its own,
    streams.post_line), from TITLES"""
    def f(prompt, purpose, *a, **k):
        res = jsonc(prompt, purpose, *a, **k)
        if purpose.endswith("_verify"):
            body = prompt.split("Posts:\n", 1)[1]
            for x in res.get("posts", []):
                block = body.split(f"{x['i']}. ", 1)[-1].split(f"\n{x['i'] + 1}. ", 1)[0]
                t = (next((v for kk, v in TITLES.items() if kk in block), None)
                     if re.match(r"\[[^\]\n]*\] " + streams.NO_TITLE + "\n", block) else None)
                if t:
                    kind, t = t if isinstance(t, tuple) else ("written", t)
                    x.update(title_ok=False, title_kind=kind, title=t)
        return res
    return f


def stage2_titles(urls):
    """what stage 2 stores for posts with no title of their own (streams.confirm_title), the titles module's own
    headline checks (generate: extracted word for word, else written) standing in for the stage-2 call"""
    rows = [dict(r) for r in corpus.db().execute(f"SELECT p.*, {titles.HASH} AS hash FROM posts p WHERE url = ANY(%s)", (urls,))]
    for i, (t, kind) in titles.generate(rows).items():
        titles.store(rows[i], t, kind, streams.CONFIRM_MODEL)
streams.WAIT_TEXT = timedelta(0)
streams.settings()  # from_id = 0: every post below is new
titles.SETTLE = timedelta(seconds=-60)  # the thread takes posts the live scorer has not scored yet too
LONG_FB = ("【#港聞小編】李家超今日出席立法會答問會，回應議員就房屋、經濟及大埔宏福苑火災善後的提問，"
           "他表示政府會在年底前公布新一輪資助方案，並會檢討樓宇維修監管制度，確保同類事故不再發生。" * 2)


def fb(url, text, **kw):
    return dict({"sid": "facebook", "source": "香港01 (Facebook)", "section": "Facebook", "title": text[:160],
                 "url": url, "published": NOW.isoformat(timespec="seconds"), "text": text}, **kw)


def scored_on(url, stage2=False):
    """the scorer's record of the post on its current content (scored; stage2: stage 2's answer in it)"""
    ans = {"stage1": {"model": ai.MODEL, "answer": {}}}
    if stage2:
        ans["stage2"] = {"model": streams.CONFIRM_MODEL, "answer": {"title_ok": True}}
    corpus.db().execute(f"""INSERT INTO scored (post_id, hash, at, answers) SELECT p.id, {streams.HASH}, 'x', %s FROM posts p
                            WHERE p.url = %s ON CONFLICT (post_id) DO UPDATE SET hash = excluded.hash, answers = excluded.answers""",
                        (corpus.J(ans), url))


def row(url):
    return dict(corpus.db().execute("SELECT * FROM posts WHERE url=%s", (url,)).fetchone())


class Needs(unittest.TestCase):
    def p(self, **kw):
        return dict({"thread": None, "source_type": "Social Media", "sid": "facebook", "platform": "Facebook",
                     "title": LONG_FB[:160], "text": LONG_FB}, **kw)

    def test_text_as_title_platforms(self):
        self.assertTrue(titles.needs(self.p()))
        for sid, plat in (("instagram", "Instagram"), ("instagram", "Threads"), ("tg_mingpao", "Telegram"),
                          ("tikhub_twitter", "X"), ("tikhub_threads", "Threads"),
                          ("tikhub_tiktok", "TikTok"), ("tikhub_facebook", "facebook")):
            self.assertTrue(titles.needs(self.p(sid=sid, platform=plat)), sid)

    def test_text_only_post(self):  # tikhub keeps no text when the whole post fits in the title
        self.assertTrue(titles.needs(self.p(sid="tikhub_tiktok", platform="TikTok", source_type="Video",
                                            title=LONG_FB[:159] + "…", text=None)))

    def test_short_post_reads_as_is(self):
        self.assertFalse(titles.needs(self.p(title="“好运豹到” 🤎🐆 揪get一件豹纹外套", text=None)))

    def test_real_titles(self):
        self.assertFalse(titles.needs(self.p(source_type="News", sid="mingpao", platform="明報")))
        self.assertFalse(titles.needs(self.p(source_type="Forum", sid="lihkg", platform="LIHKG討論區")))
        self.assertFalse(titles.needs(self.p(source_type="Video", sid="tikhub_youtube", platform="YouTube",
                                             title="屈穎妍評宏福苑事件", text="屈穎妍評宏福苑事件 " + LONG_FB)))
        self.assertFalse(titles.needs(self.p(sid="tikhub_reddit", platform="Reddit",
                                             title="香港特首是什么级别？", text="香港特首是什么级别？ " + LONG_FB)))
        self.assertFalse(titles.needs(self.p(thread="https://facebook.com/x")))  # a comment: its thread is its subject

    def test_wordless_title_outside_social(self):
        forum = self.p(source_type="Forum", sid="hk_forum_hkgolden", platform="香港高登", title="8099523",
                       text="雖然唔關佢事，但依然主動承擔，背負百幾條人命，每日痛苦地活下去")
        self.assertTrue(titles.needs(forum))
        self.assertTrue(titles.by_scorer(forum))
        self.assertEqual(titles.own(forum), "")
        self.assertFalse(titles.needs(dict(forum, title="何局長離任")))
        self.assertFalse(titles.needs(dict(forum, text=None)))

    def test_facebook_page_name_with_brackets(self):
        """a Facebook page named 「胡‧說樓市 (胡偉雄)」: the brackets are its owner, not a platform"""
        c = corpus.classify({"sid": "facebook", "source": "胡‧說樓市 (胡偉雄)"})
        self.assertEqual((c["platform"], c["website"], c["author"]), ("Facebook", "Facebook", "胡‧說樓市 (胡偉雄)"))
        c = corpus.classify({"sid": "instagram", "source": "100毛 (Threads)"})
        self.assertEqual((c["platform"], c["author"]), ("Threads", "100毛"))

    def test_title_cut_from_text(self):
        self.assertTrue(titles.needs(self.p(sid="tikhub_reddit", platform="Reddit", title=LONG_FB[:159] + "…")))
        self.assertTrue(titles.needs(self.p(sid="tikhub_reddit", platform="Reddit", title="", text=LONG_FB)))


class Pipeline(unittest.TestCase):
    def setUp(self):
        CALLS.clear()

    def test_live_flow(self):
        TITLES.clear()
        TITLES["立法會答問會"] = "李家超立法会答问：年底前公布宏福苑资助方案"  # Simplified: stored as Traditional
        url = "https://www.facebook.com/hk01/posts/1"
        news = "https://news.rthk.hk/1"
        corpus.ingest([fb(url, LONG_FB),
                       {"sid": "rthk", "source": "RTHK", "title": "李家超出席立法會答問會", "url": news,
                        "published": NOW.isoformat(timespec="seconds"), "text": "李家超今日出席立法會答問會。" * 30}])
        corpus.db().execute("UPDATE posts SET first_run = FALSE")  # sources already watched: their posts are filed
        twopass.captured(url)  # a Facebook post is scored with its comments once social_twopass.py has read them
        ai.jsonc = with_stage2_titles(stage2.agree(fake_jsonc, CALLS))
        try:
            streams.run_once(app.STREAMS)
        finally:
            ai.jsonc = stage2.agree(fake_jsonc, CALLS)
        r = row(url)
        self.assertEqual(r["ai_title"], "李家超立法會答問：年底前公佈宏福苑資助方案")  # HK usage: 公佈
        self.assertEqual(r["ai_title_kind"], "written")
        self.assertEqual(r["ai_title_model"], streams.CONFIRM_MODEL)  # from the stage-2 call (streams.confirm_title)
        self.assertTrue(r["ai_title_at"])
        self.assertEqual((r["title"], r["text"], r["title_from_body"]), (corpus.excerpt(LONG_FB), LONG_FB, True))
        self.assertIsNone(row(news)["ai_title"])
        self.assertEqual([p for k, p in CALLS if k == "titles"], [])  # no titles call of its own

        # 串流
        feed = edge("feed.archiveDay", r["day"])  # the edge app's 串流 (tests/edge_call.py)
        it = next(x for x in feed if x["url"] == url)
        self.assertEqual(it["title"], r["ai_title"])
        self.assertTrue(it["title_by_ai"])
        self.assertEqual(it["original_title"], corpus.excerpt(LONG_FB))
        self.assertEqual(it["text"], LONG_FB[:2000])
        n = next(x for x in feed if x["url"] == news)
        self.assertEqual(n["title"], "李家超出席立法會答問會")
        self.assertFalse(n.get("title_by_ai"))

        # Telegram
        sent = []
        telegram_alerts.post = lambda chat, it, sid=None: sent.append(telegram_alerts.text(it, sid))
        telegram_alerts.GAP = 0
        corpus.kv_set("telegram_sent", {"CEHK": 0})
        telegram_alerts.send_due([{"id": "CEHK", "telegram_chat": -1}])
        ai_msg = next(m for m in sent if "hk01/posts/1" in m)
        self.assertIn("〔AI標題〕李家超立法會答問：年底前公佈宏福苑資助方案\n", ai_msg)
        self.assertNotIn("港聞小編", ai_msg)  # the cut-off text is not the headline any more
        news_msg = next(m for m in sent if "rthk" in m)
        self.assertNotIn("AI標題", news_msg)

        # item page
        v = item.view(r["id"])
        self.assertEqual(v["title"], r["ai_title"])
        self.assertTrue(v["title_by_ai"])
        self.assertEqual(v["original_title"], corpus.excerpt(LONG_FB))
        self.assertTrue(v["extract"].startswith("【#港聞小編】李家超今日"))
        self.assertEqual(v["ai_title_model"], streams.CONFIRM_MODEL)

        # 數據分析
        d = corpus.posts([r["id"]], "CEHK")[0]
        self.assertEqual((d["title"], d["title_by_ai"], d["text"]), (r["ai_title"], True, LONG_FB))

        # 搜尋: words of the AI title find it
        searchtext.publish_edge()  # the dictionary the edge app's 搜尋 cuts queries with
        got = edge("search/search.run", {"q": ["資助方案"], "from": [r["day"]], "to": [r["day"]]})
        hit = next(x for x in got["posts"] if x["id"] == r["id"])
        self.assertEqual(hit["title"], r["ai_title"])
        self.assertTrue(hit["title_by_ai"])
        self.assertTrue(hit["extract"])

    def test_text_change_and_checked_once(self):
        TITLES.clear()
        TITLES["九龍城"] = "九龍城舊區重建諮詢展開"
        url = "https://www.facebook.com/x/posts/2"
        short = "https://www.facebook.com/x/posts/3"
        text = "九龍城舊區重建今日展開第一輪諮詢，市建局表示會聽取居民意見，" * 3
        corpus.ingest([fb(url, text), fb(short, "早晨，今日天氣好好")])
        titles.run_once()
        # Deyao, 2026-10-20: a post with no title of its own is titled by stage-2 scoring; the titles thread leaves
        # it to the scorer until it is scored on its content
        self.assertEqual([k for k, _ in CALLS], [])
        self.assertIsNone(row(url)["ai_title_hash"])
        scored_on(url)
        scored_on(short)
        titles.run_once()  # scored by stage 1 only: checked (no model call), it shows the excerpt of its opening
        self.assertEqual([k for k, _ in CALLS], [])
        self.assertEqual((row(url)["ai_title"], row(url)["title"]), (None, corpus.excerpt(text)))
        self.assertEqual(row(url)["ai_title_hash"], titles.content_hash(row(url)))
        self.assertIsNone(row(short)["ai_title"])
        CALLS.clear()
        titles.run_once()
        self.assertEqual(CALLS, [])  # nothing due: both were checked once
        corpus.set_text(row(url)["id"], text + "補充：諮詢期延長兩星期。")
        titles.run_once()  # changed: the scorer's again until it is scored on its new content
        old = row(url)["ai_title_hash"]
        self.assertNotEqual(old, titles.content_hash(row(url)))
        scored_on(url)
        titles.run_once()  # checked again, still no model call
        self.assertEqual(CALLS, [])
        self.assertEqual(row(url)["ai_title_hash"], titles.content_hash(row(url)))

    def test_stage2_reached_untitled(self):
        """stage 2 scored a post with no title of its own but left it untitled (title_ok on a post shown with none,
        29 Oct 2026): the thread titles it; one checked that way before is titled by titles.recheck"""
        TITLES.clear()
        TITLES["大學校長"] = ("extracted", "全球大學校長及領袖高峰會2026")
        TITLES["紡織商會"] = "黃偉綸出席香港紡織商會國慶酒會"
        a, b = "https://www.facebook.com/s2/posts/1", "https://www.facebook.com/s2/posts/2"
        ta = "【全球大學校長及領袖高峰會2026】 今天我很高興出席全球大學校長及領袖高峰會2026，並歡迎來自世界各地的大學校長。" * 2
        tb = "我今晚出席了香港紡織商會慶祝中華人民共和國77周年國慶暨第十五屆會董就職典禮，與業界朋友交流。" * 2
        corpus.ingest([fb(a, ta), fb(b, tb)])
        scored_on(a, stage2=True)
        titles.run_once()
        self.assertEqual((row(a)["ai_title"], row(a)["ai_title_kind"]), ("全球大學校長及領袖高峰會2026", "extracted"))
        self.assertEqual(row(a)["ai_title_model"], ai.MODEL)
        # b: checked with no title by the old code (stage 2's title_ok stored as "checked")
        corpus.db().execute(f"UPDATE posts p SET ai_title_hash = {titles.HASH} WHERE url=%s", (b,))
        scored_on(b, stage2=True)
        self.assertEqual(titles.recheck(dry=True), 1)
        self.assertEqual(row(b)["ai_title_hash"], titles.content_hash(row(b)))  # a dry run writes nothing
        self.assertEqual(titles.recheck(), 1)
        self.assertEqual((row(b)["ai_title"], row(b)["ai_title_kind"]), ("黃偉綸出席香港紡織商會國慶酒會", "written"))
        self.assertEqual(titles.recheck(), 0)  # titled: nothing to make due

    def test_backfill_window(self):
        TITLES.clear()
        TITLES["舊帖"] = "八日前的舊帖標題"
        TITLES["串流舊"] = "串流中的舊帖標題"
        old = (NOW - timedelta(days=8)).isoformat(timespec="seconds")
        a, b = "https://www.facebook.com/o/posts/1", "https://www.facebook.com/o/posts/2"
        corpus.ingest([fb(a, "舊帖：" + LONG_FB, published=old), fb(b, "串流舊：" + LONG_FB, published=old)])
        corpus.db().execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, 'CEHK', %s, %s)",
                            (old[:10], b, corpus.J({"title": "x", "url": b, "seen": old})))
        while titles.run_once():
            pass
        self.assertIsNone(row(a)["ai_title"])  # older than 7 days and not in 串流
        self.assertIsNone(row(a)["ai_title_hash"])  # not taken
        self.assertEqual(row(b)["ai_title"], "串流中的舊帖標題")  # in 串流: titled, though it has no title of its own
        self.assertEqual(row(b)["ai_title_hash"], titles.content_hash(row(b)))

    def test_prompt_language_and_validation(self):
        p = {"id": 1, "title": "x", "text": "The Beijing-based robotics firm Galbot has opened three stores. " * 4,
             "platform": "X", "website": "X"}
        prompt = titles.prompt([p])
        self.assertIn("in the post's language", " ".join(prompt.split()))
        self.assertIn("Traditional Chinese with Hong Kong usage", prompt)
        self.assertEqual(titles.clean("「李家超出席答問會」", "李家超今日出席"), "李家超出席答問會")
        self.assertEqual(titles.clean("Galbot opens three stores abroad.", "Galbot has opened"), "Galbot opens three stores abroad")
        self.assertEqual(titles.clean("香港立法會前主席范徐麗泰：同源同心，守望相助", "x"),
                         "香港立法會前主席范徐麗泰：同源同心，守望相助")  # Traditional already: 范 stays 范
        self.assertIsNone(titles.clean("", "x"))
        self.assertIsNone(titles.clean("長" * 80, "長"))  # far over the length: not a title


RTHK = ("李家超：孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥\n\n"
        "行政長官李家超表示，特區政府重視愛國主義教育，全力弘揚以愛國愛港為核心、和「一國兩制」方針相適應的主流價值觀，"
        "上月公布的《香港第一個五年規劃》明確提出，培育德才兼備、愛國愛港、具文化自信、正確價值觀以及世界視野的下一代。")
ROAD = ("【中九龍繞道12.6全線通車 同日起收費8元】 政府公布，自12月6日中九龍繞道全線通車起，同日開始收費8元。 "
        "中九龍繞道由已通車的中九龍繞道（油麻地段隧道）及計劃在今年12月6日通車的中九龍繞道（九龍灣段隧道）組成。")
HOTEL = ("【#港聞小編】相約酒店開房 港男如廁後現金不翼而飛 27歲日本女涉企圖盜竊被捕 尖沙咀發生企圖盜竊案。"
         "一名本地男子昨日（13日）傍晚相約一名透過通訊軟件結識的女子到酒店房間，期間男子入場如廁，赫然發現銀包內的現金不翼而飛。")
ROADS = ("【郭芙蓉｜傳媒訪問｜引入新科技協助監察道路工程安排 檢視現行守則🔎 提升道路安全🛣️】 近年在高速公路上，不時發生"
         "涉及工程車的交通意外🛻，造成人命傷亡。感謝 #HOYTV #一線搜查 節目的訪問🎤，讓芙蓉分享如何提升進行道路工程時的安全。")
PRIME = ("【Prime】《體育救港》第258集︰跑步團復出慢跑 節目連結︰https://www.passiontimes.hk/prog/114/258 "
         "Prime節目重溫只限付費訂戶，如想隨時重溫所有熱血時報節目，請即訂購PassionPrime")
LIFT = ("【📢葵青區「三駕馬車」加強宣傳安全使用扶手電梯及升降機資訊 🛗🚶‍♂️🚶‍♀️ 📢】 ⚠️近月喺葵青區曾經發生長者使用扶手電梯時跌倒"
        "受傷嘅意外，情況令人關注。")
DIARY = ("係宏福苑火災第320日。 今日又解鎖新技能——去揀樓。 唔係值得高興嘅事。但總算，日後有個固定居所，為老咗嘅我，"
         "都唔使再執嘢搬嚟搬去。 呢個「新技能」，無人想學。")
MINGPAO = ("茶百道受監管機構調查停牌 遭剔出恒生綜合指數\n恒指公司宣布，因為茶百道（2555）受監管機構調查而停牌，其公司股份"
           "將會從恒生綜合指數、分類指數包括恒生綜合行業指數 - 必需性消費、恒生綜合小型股指數及恒生綜合中小型股指數中剔除。")


class Headlines(unittest.TestCase):
    """real posts of 12–14 Oct 2026: six open with their own headline, one (a resident's diary on Threads) has none"""

    def test_extracted_checked_word_for_word(self):
        self.assertEqual(titles.extracted("李家超：孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥", RTHK),
                         "李家超：孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥")
        self.assertEqual(titles.extracted("【中九龍繞道12.6全線通車 同日起收費8元】", ROAD), "中九龍繞道12.6全線通車 同日起收費8元")
        self.assertEqual(titles.extracted("相約酒店開房 港男如廁後現金不翼而飛 27歲日本女涉企圖盜竊被捕", HOTEL),
                         "相約酒店開房 港男如廁後現金不翼而飛 27歲日本女涉企圖盜竊被捕")
        # the model drops the emoji inside the headline: still the post's own
        self.assertEqual(titles.extracted("郭芙蓉｜傳媒訪問｜引入新科技協助監察道路工程安排 檢視現行守則 提升道路安全", ROADS),
                         "郭芙蓉｜傳媒訪問｜引入新科技協助監察道路工程安排 檢視現行守則 提升道路安全")
        self.assertEqual(titles.extracted("《體育救港》第258集︰跑步團復出慢跑", PRIME), "《體育救港》第258集︰跑步團復出慢跑")
        self.assertEqual(titles.extracted("📢葵青區「三駕馬車」加強宣傳安全使用扶手電梯及升降機資訊 🛗🚶‍♂️🚶‍♀️ 📢", LIFT),
                         "葵青區「三駕馬車」加強宣傳安全使用扶手電梯及升降機資訊")  # emoji left out
        self.assertIsNone(titles.extracted("李家超談孫中山愛國思想", RTHK))  # rewritten: not the post's own
        self.assertIsNone(titles.extracted("一名本地男子昨日（13日）傍晚相約", HOTEL))  # from the body, not its start

    def test_body_without_headline(self):
        self.assertTrue(titles.strip_headline(RTHK, "李家超：孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥").startswith("行政長官李家超表示"))
        self.assertTrue(titles.strip_headline(ROAD, "中九龍繞道12.6全線通車 同日起收費8元").startswith("政府公布"))
        self.assertTrue(titles.strip_headline(HOTEL, "相約酒店開房 港男如廁後現金不翼而飛 27歲日本女涉企圖盜竊被捕").startswith("尖沙咀發生"))
        self.assertTrue(titles.strip_headline(ROADS, "郭芙蓉｜傳媒訪問｜引入新科技協助監察道路工程安排 檢視現行守則 提升道路安全")
                        .startswith("近年在高速公路上"))
        self.assertEqual(titles.strip_headline(DIARY, "宏福苑居民記錄揀樓心情"), DIARY)

    def test_prompt_asks_to_surface_the_posts_headline(self):
        pr = titles.prompt([{"title": RTHK[:160], "text": RTHK, "platform": "Telegram"}])
        self.assertIn("extracted", pr)
        self.assertIn("李家超：孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥\n", pr)  # its line break is kept

    def test_not_json_asks_post_by_post(self):
        calls = []

        def broken(prompt, purpose, model=None, max_tokens=4000):
            calls.append(prompt.count("<post i="))
            if prompt.count("<post i=") > 1:
                raise ValueError("Expecting ',' delimiter")
            return {"titles": [{"i": 0, "kind": "written", "title": "宏福苑居民記錄火災後揀樓心情"}]}
        ai.jsonc = broken
        try:
            got = titles.generate([{"text": DIARY}, {"text": DIARY + "。"}])
        finally:
            ai.jsonc = stage2.agree(fake_jsonc, CALLS)
        self.assertEqual(calls, [2, 1, 1])
        self.assertEqual(got, {0: ("宏福苑居民記錄火災後揀樓心情", "written"), 1: ("宏福苑居民記錄火災後揀樓心情", "written")})

    def test_flow(self):
        TITLES.clear()
        TITLES["孫中山"] = ("extracted", "李家超：孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥")
        TITLES["中九龍繞道"] = ("extracted", "中九龍繞道12.6全線通車 同日起收費8元")
        TITLES["揀樓"] = ("written", "宏福苑居民記錄火災後第320日揀樓心情")
        TITLES["茶百道"] = ("extracted", "茶百道停牌被剔出恒指")  # not word for word: written
        rows = [{"sid": "tg_rthk", "source": "RTHK即時新聞 (Telegram)", "title": re.sub(r"\s+", "", RTHK)[:200],
                 "url": "https://t.me/rthk_new_c/1", "published": NOW.isoformat(timespec="seconds"), "text": RTHK},
                fb("https://www.facebook.com/passiontimes/posts/9", ROAD),
                {"sid": "tikhub_threads", "source": "hfc_resident (Threads)", "title": DIARY[:160],
                 "url": "https://www.threads.net/@r/post/1", "published": NOW.isoformat(timespec="seconds"), "text": DIARY},
                {"sid": "tg_mingpao", "source": "明報卽時新聞 (Telegram)", "title": MINGPAO.replace("\n", "")[:200],
                 "url": "https://t.me/mingpao/2", "published": NOW.isoformat(timespec="seconds"), "text": MINGPAO}]
        corpus.ingest(rows)
        stage2_titles([x["url"] for x in rows])
        r = row("https://t.me/rthk_new_c/1")
        self.assertEqual((r["ai_title"], r["ai_title_kind"]), ("李家超：孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥", "extracted"))
        self.assertEqual(row("https://www.facebook.com/passiontimes/posts/9")["ai_title_kind"], "extracted")
        self.assertEqual(row("https://www.threads.net/@r/post/1")["ai_title_kind"], "written")
        self.assertEqual(row("https://t.me/mingpao/2")["ai_title_kind"], "written")
        self.assertEqual(r["text"], RTHK)  # the stored post is untouched

        d = corpus.posts([r["id"]])[0]
        self.assertEqual(d["title"], "李家超：孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥")
        self.assertFalse(d["title_by_ai"])
        self.assertTrue(d["title_extracted"])
        self.assertTrue(d["text"].startswith("行政長官李家超表示"))
        v = item.view(r["id"])
        self.assertFalse(v["title_by_ai"])
        self.assertTrue(v["title_extracted"])
        self.assertTrue(v["extract"].startswith("行政長官李家超表示"))
        self.assertTrue(item.text(r["id"])["text"].startswith("行政長官李家超表示"))

        for u in ("https://t.me/rthk_new_c/1", "https://www.threads.net/@r/post/1"):
            corpus.db().execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, 'WFC', %s, %s)",
                                (NOW.strftime("%Y-%m-%d"), u, corpus.J({"title": "x", "url": u, "source": "s", "seen": NOW.isoformat(timespec="seconds")})))
        feed = {x["url"]: x for x in edge("feed.archiveDay", NOW.strftime("%Y-%m-%d"))}
        it = feed["https://t.me/rthk_new_c/1"]
        self.assertEqual((it["title"], it["title_by_ai"], it["title_extracted"]),
                         ("李家超：孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥", False, True))
        self.assertTrue(it["text"].startswith("行政長官李家超表示"))
        self.assertTrue(feed["https://www.threads.net/@r/post/1"]["title_by_ai"])

        sent = []
        telegram_alerts.post = lambda chat, it, sid=None: sent.append(telegram_alerts.text(it, sid))
        telegram_alerts.GAP = 0
        corpus.kv_set("telegram_sent", {"WFC": 0})
        telegram_alerts.send_due([{"id": "WFC", "telegram_chat": -1}])
        rthk = next(m for m in sent if "rthk_new_c" in m)
        self.assertTrue(rthk.startswith("李家超：孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥\n"), rthk)
        self.assertNotIn("AI標題", rthk)
        self.assertTrue(next(m for m in sent if "threads.net" in m).startswith("〔AI標題〕宏福苑居民記錄火災後第320日揀樓心情\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
