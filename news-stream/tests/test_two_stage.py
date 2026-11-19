#!/usr/bin/env python3
"""Two-stage scoring (Deyao, 2026-10-20: "a 40-score filter with Haiku, and then anything over that threshold we rate
again with Opus and see if it still passes. This saves cost and reduces false positives"). On a throwaway database
(TEST_DATABASE_URL; its tables are dropped and made again), both models and Telegram stubbed.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_two_stage.py

Stage 1: Haiku 5.5 (ai.MODEL) scores every post against every stream as before. Stage 2: every (post, stream) Haiku
scored at or above streams.SCREEN (20) is scored again by Opus 5.5 (streams.CONFIRM_MODEL) with the same prompt
(brief, rubric, text, comments), its calls under the stage-1 purpose + "_verify". Opus's score is the one compared
with the threshold and stored (scores.score, model = Opus); Haiku's is kept beside it (scores.screen,
screen_reason). Under SCREEN, Haiku's score stands (model = Haiku, no Opus call).

Covers:
  1. Haiku 45, Opus 30: not filed, no Telegram message; scores row 30 by Opus with screen 45; purpose scoring_verify;
  2. Haiku 60, Opus 85: filed with Opus's score, the item carrying screen 60 and the model; the Telegram message
     goes out once, after stage 2;
  3. Haiku 15: no Opus call; Haiku's 15 stands (model Haiku, no screen);
  4. Haiku 90, Opus leaves the stream out: Opus's answer is under the floor, not filed, the row says so (score 0 by
     Opus, screen 90);
  5. Opus's answer not JSON twice: the post is not scored or filed (it stays due) and is filed on the next round
     when Opus answers; Haiku's call then runs again;
  6. Opus is called only with the posts that have a pair to check, in calls of at most CONFIRM_BATCH posts, each
     post's streams to check listed;
  7. a LIHKG thread (two-pass unit): first scoring Haiku 45 / Opus 30 not filed, the 30-minute scoring Haiku 70 /
     Opus 80 filed; both passes keep the screen in score_passes; purposes scoring_lihkg + scoring_lihkg_verify;
  8. the history backfill: the same two stages (purpose scoring_backfill_verify), never filed;
  9. GALBOT is filed on Opus's score (Haiku 60 / Opus 30: not filed; Haiku 45 / Opus 70: filed);
 10. the item page's AI 分析 (item.aiScores): each stream's model (Opus) and its screening score and model;
 11. an answer with more text after its JSON object is read (the first object), not asked again;
 12. the stage-2 model is DeepSeek V4 Pro (Deyao, 2026-10-20: Opus too expensive at ~USD 31.55/day), one setting
     (streams.CONFIRM_MODEL, env CONFIRM_MODEL) with its OpenRouter options (streams.CONFIRM_OPTIONS: the provider,
     reasoning and max_tokens) sent on the call; Opus 5.5 stays selectable (env CONFIRM_MODEL=anthropic/claude-opus-5.5).
"""
import json, os, re, sys, tempfile, unittest
from unittest import mock
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, edge_config, insights, sources, streams, telegram_alerts  # noqa: E402
from edge_call import edge  # noqa: E402

STREAMS = app.STREAMS
NOW = datetime.now(app.HKT)
CALLS = []   # (purpose, model, prompt)
BROKEN = {"opus": 0}  # Opus calls left to answer with broken JSON

MARK = re.compile(r"\b([A-Z_]+):h=(\d+)(?:,o=(\d+))?")


ALL = []     # every model call's purpose (titles' too)
FAIL = {}    # post title -> the exception the stage-2 model raises for a call carrying it


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000, **kw):
    """markers in a post's block say what each model answers: "CEHK:h=45,o=30" → Haiku CEHK 45, Opus CEHK 30 (no o=:
    Opus leaves CEHK out); the last marker of a stream in the block counts (a thread's newest reply)"""
    ALL.append(purpose)
    if model == streams.CONFIRM_MODEL:
        for title, e in FAIL.items():
            if title in prompt.split("Posts:\n", 1)[-1]:
                raise e
    if not purpose.startswith("scoring"):
        return {"tags": []}
    CALLS.append((purpose, model, prompt))
    opus = model == streams.CONFIRM_MODEL
    if opus and BROKEN["opus"]:
        BROKEN["opus"] -= 1
        raise ValueError("not JSON")
    body = prompt.split("Posts:\n", 1)[1]
    out = []
    for i in re.findall(r"(?m)^(\d+)\. \[", body):
        block = body.split(f"\n{i}. ", 1)[-1] if i != "0" else body
        block = block.split(f"\n{int(i) + 1}. ", 1)[0]
        got = {}
        for sid, h, o in MARK.findall(block):
            got[sid] = o if opus else h
        st = [{"id": sid, "score": int(v), "reason": ("O" if opus else "H") + v, "category": None}
              for sid, v in got.items() if v]
        out.append({"i": int(i), "sentiment": "negative" if opus else "neutral", "labels": [], "streams": st})
    return {"posts": out}


MARKET = []


def fake_chat(messages, purpose, **k):
    MARKET.append(purpose)
    return {"content": "hk"}


REAL_JSONC = ai.jsonc
REAL_CHAT = ai.chat
ai.jsonc = fake_jsonc
ai.chat = fake_chat
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


def post(url, title, text, sid="rthk", published=None):
    return {"sid": sid, "source": "RTHK", "title": title, "url": url, "published": published or NOW, "text": text}


def pid(url):
    return corpus.ids_for_urls([url])[url]


def filed(url, stream="CEHK"):
    r = corpus.db().execute("SELECT item FROM stream_items WHERE stream=%s AND url=%s", (stream, url)).fetchone()
    return r[0] if r else None


def row(url, stream="CEHK"):
    r = corpus.db().execute("SELECT score, reason, model, screen, screen_reason FROM scores WHERE stream=%s AND post_id=%s",
                            (stream, pid(url))).fetchone()
    return dict(r) if r else None


def sends():
    return [p for m, p in TG if m == "sendMessage"]


def start_sending():
    corpus.kv_set(telegram_alerts.KEY, {"CEHK": corpus.db().execute(
        "SELECT coalesce(max(id), 0) FROM stream_items WHERE stream='CEHK'").fetchone()[0]})
    TG.clear()


def opus_calls():
    return [c for c in CALLS if c[1] == streams.CONFIRM_MODEL]


TEXT = "字" * 600


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.db()
        corpus.kv_set("scoring", {"since": NOW.isoformat(), "from_id": 0, "comments": False})
        corpus.kv_set("score_backfill", {"on": False, "posts": 0})
        poll([post("https://seed.test/0", "seed", "x" * 600),  # the sources' first run (never filed)
              post("https://lihkg.com/thread/9900000/page/1", "seed", "x" * 600, sid="lihkg")])

    def setUp(self):
        CALLS.clear()

    def test_01_haiku_passes_opus_rejects(self):
        start_sending()
        url = "https://news.test/two-1"
        poll([post(url, "港府記者會", "CEHK:h=45,o=30 " + TEXT)])
        streams.run_once(STREAMS)
        self.assertIsNone(filed(url))
        self.assertEqual(row(url), {"score": 30, "reason": "O30", "model": streams.CONFIRM_MODEL, "screen": 45,
                                    "screen_reason": "H45"})
        self.assertEqual([c[0] for c in CALLS], ["scoring", "scoring_verify"])
        self.assertEqual(CALLS[0][1] or ai.MODEL, ai.MODEL)
        telegram_alerts.send_due(TGS)
        self.assertEqual(sends(), [])

    def test_02_haiku_passes_opus_confirms(self):
        start_sending()
        url = "https://news.test/two-2"
        poll([post(url, "李家超出席論壇", "CEHK:h=60,o=85 " + TEXT)])
        streams.run_once(STREAMS)
        it = filed(url)
        self.assertIsNotNone(it)
        self.assertEqual((it["score"], it["reason"], it["screen"], it["model"]), (85, "O85", 60, streams.CONFIRM_MODEL))
        self.assertEqual(row(url)["score"], 85)
        telegram_alerts.send_due(TGS)
        self.assertEqual(len(sends()), 1)
        self.assertIn("李家超出席論壇", sends()[0]["text"])
        self.assertEqual(corpus.db().execute("SELECT sentiment FROM scored WHERE post_id=%s", (pid(url),)).fetchone()[0],
                         "negative")  # the final verdict's sentiment and labels (Opus's)

    def test_03_under_the_screen_haiku_stands(self):
        url = "https://news.test/two-3"
        poll([post(url, "港府資訊", "CEHK:h=15,o=99 " + TEXT)])
        streams.run_once(STREAMS)
        self.assertEqual(opus_calls(), [])
        self.assertEqual(row(url), {"score": 15, "reason": "H15", "model": ai.MODEL, "screen": None, "screen_reason": None})
        self.assertIsNone(filed(url))

    def test_04_opus_leaves_the_stream_out(self):
        url = "https://news.test/two-4"
        poll([post(url, "特首？", "CEHK:h=90 " + TEXT)])
        streams.run_once(STREAMS)
        self.assertIsNone(filed(url))
        r = row(url)
        self.assertEqual((r["score"], r["model"], r["screen"]), (0, streams.CONFIRM_MODEL, 90))

    def test_05_opus_fails_post_stays_due(self):
        url = "https://news.test/two-5"
        poll([post(url, "李家超視察", "CEHK:h=70,o=75 " + TEXT)])
        BROKEN["opus"] = 2
        streams.run_once(STREAMS)
        self.assertIsNone(filed(url))
        self.assertIsNone(corpus.db().execute("SELECT 1 FROM scored WHERE post_id=%s", (pid(url),)).fetchone())
        self.assertIsNone(row(url))
        CALLS.clear()
        streams.run_once(STREAMS)
        self.assertEqual(filed(url)["score"], 75)
        self.assertEqual([c[0] for c in CALLS], ["scoring", "scoring_verify"])

    def test_06_only_posts_to_check_go_to_opus_in_batches(self):
        n = streams.CONFIRM_BATCH + 2
        items = [post(f"https://news.test/b{k}", f"標題{k}", ("CEHK:h=50,o=60 FSHK:h=41,o=20 " if k % 2 else "CEHK:h=10 ") + TEXT)
                 for k in range(2 * n)]
        poll(items)
        while streams.run_once(STREAMS):
            pass
        oc = opus_calls()
        sent = [p for c in oc for p in re.findall(r"(?m)^\d+\. \[[^\]]*\] (標題\d+)", c[2].split("Posts:\n", 1)[1])]
        self.assertEqual(sorted(sent), sorted(f"標題{k}" for k in range(2 * n) if k % 2))
        self.assertTrue(all(len(re.findall(r"(?m)^\d+\. \[", c[2].split("Posts:\n", 1)[1])) <= streams.CONFIRM_BATCH
                            for c in oc))
        self.assertIn("CEHK", oc[0][2].split("Posts:\n", 1)[0].split("Check", 1)[-1])
        self.assertEqual(row("https://news.test/b1", "FSHK")["screen"], 41)
        self.assertEqual(row("https://news.test/b1", "FSHK")["score"], 20)
        self.assertEqual(row("https://news.test/b0")["model"], ai.MODEL)

    def test_07_lihkg_thread_both_passes(self):
        start_sending()
        url = "https://lihkg.com/thread/9900001/page/1"
        pub = NOW - timedelta(minutes=5)
        poll([post(url, "李家超講話", "CEHK:h=45,o=30 " + TEXT, sid="lihkg", published=pub)])
        streams.run_once(STREAMS)
        self.assertIsNone(filed(url))
        self.assertEqual([c[0] for c in CALLS], ["scoring_lihkg", "scoring_lihkg_verify"])
        corpus.add_comments({"url": url, "title": "李家超講話", "sid": "lihkg", "source": "LIHKG"},
                            [{"id": "r1", "text": "CEHK:h=70,o=80 特首今日", "published": NOW.isoformat()}])
        with corpus.db() as c:
            c.execute("UPDATE two_pass SET due_at=%s, fetched_at=%s WHERE url=%s",
                      (NOW - timedelta(minutes=1), NOW, url))
        CALLS.clear()
        streams.run_once(STREAMS)
        self.assertEqual(filed(url)["score"], 80)
        passes = corpus.db().execute("SELECT pass, result FROM score_passes WHERE post_id=%s ORDER BY pass",
                                     (pid(url),)).fetchall()
        self.assertEqual([(p, r["streams"]["CEHK"]["score"], r["streams"]["CEHK"]["screen"]) for p, r in passes],
                         [(1, 30, 45), (2, 80, 70)])

    def test_08_backfill_two_stages_never_filed(self):
        url = "https://news.test/old-1"
        old = NOW - timedelta(days=5)
        poll([post(url, "李家超舊聞", "CEHK:h=50,o=55 " + TEXT, published=old)])
        corpus.kv_set("score_backfill", {"on": True, "posts": 0})
        try:
            while streams.due(1):
                streams.run_once(STREAMS)
            CALLS.clear()
            for _ in range(5):
                if streams.backfill_once(STREAMS) == "done":
                    break
        finally:
            corpus.kv_set("score_backfill", {"on": False, "posts": 0})
        self.assertIn(("scoring_backfill", "scoring_backfill_verify"),
                      {(a[0], b[0]) for a, b in zip(CALLS, CALLS[1:])})
        r = row(url)
        self.assertEqual((r["score"], r["screen"], r["model"]), (55, 50, streams.CONFIRM_MODEL))
        self.assertIsNone(filed(url))

    def test_09_galbot_filed_on_the_final_score(self):
        url = "https://news.test/galbot-1"
        poll([post(url, "銀河通用", "GALBOT:h=60,o=30 " + TEXT)])
        streams.run_once(STREAMS)
        self.assertEqual(row(url, "GALBOT")["score"], 30)
        self.assertIsNone(filed(url, "GALBOT"))
        url2 = "https://news.test/galbot-2"
        poll([post(url2, "銀河通用機械人", "GALBOT:h=45,o=70 " + TEXT)])
        streams.run_once(STREAMS)
        self.assertEqual(filed(url2, "GALBOT")["score"], 70)

    def test_10_item_page_shows_model_and_screen(self):
        url = "https://news.test/two-2"
        corpus.kv_set("openrouter_models", {"at": 9e12, "models": {
            ai.MODEL: {"name": "Anthropic: Claude Haiku 5.5"},
            streams.CONFIRM_MODEL: {"name": "Anthropic: Claude Opus 5.5"}}})
        corpus.kv_set("edge:config", edge_config.build())
        d = edge("item.aiScores", pid(url))
        c = {x["stream"]: x for x in d["streams"]}["CEHK"]
        self.assertEqual((c["score"], c["model"]), (85, "Claude Opus 5.5"))
        self.assertEqual(c["screen"], {"score": 60, "reason": "H60", "model": "Claude Haiku 5.5"})
        d3 = edge("item.aiScores", pid("https://news.test/two-3"))
        c3 = {x["stream"]: x for x in d3["streams"]}["CEHK"]
        # under the screen (20) is under the floor too: the item page shows it as 「低於 20 分」, no number (item.hiddenLow)
        self.assertEqual((c3["score"], c3["model"], c3["screen"]), (None, None, None))


    def test_11_json_followed_by_more_text(self):
        """Opus 5.5 sometimes writes a second block after the JSON (3 of 109 calls in the 20 Oct trial: "Extra
        data"): the first JSON object is the answer, no second call"""
        calls = []
        ai.chat = lambda messages, purpose, **k: calls.append(1) or {
            "content": '{"posts": [{"i": 0, "streams": []}]}\n\n{"note": "done"}'}
        try:
            self.assertEqual(REAL_JSONC("p", "scoring_verify", model=streams.CONFIRM_MODEL), {"posts": [{"i": 0, "streams": []}]})
            self.assertEqual(len(calls), 1)
        finally:
            ai.chat = fake_chat

    def test_12_stage2_model_is_deepseek_flash_with_its_options(self):
        """Deyao, 2026-10-28: "run stage 2 on deepseek v4.1 flash with thinking set as low" (OpenRouter refuses Google
        and Anthropic models to YYC's account); DeepSeek V4 Pro kept selectable (env CONFIRM_MODEL); open weights only"""
        self.assertEqual(streams.CONFIRM_MODEL, "deepseek/deepseek-v4.1-flash")
        self.assertEqual(set(streams.CONFIRM_OPTIONS), {streams.DEEPSEEK_FLASH, streams.DEEPSEEK})
        self.assertFalse(any(m.startswith(("anthropic/", "google/", "openai/")) for m in streams.CONFIRM_OPTIONS))
        opts = streams.CONFIRM_OPTIONS[streams.CONFIRM_MODEL]
        self.assertEqual(opts["reasoning"], {"effort": "low"})
        # open weights: never a mainland provider's endpoint (their filter sits in front of the model)
        self.assertEqual(opts["provider"]["ignore"], ["streamlake", "alibaba", "siliconflow", "baidu"])
        self.assertEqual(streams.SCORE_OPTIONS, {"reasoning": {"enabled": False}})  # stage 1: no thinking
        self.assertEqual((opts["max_tokens"], opts["timeout"]), (32000, 600))
        ds = streams.CONFIRM_OPTIONS[streams.DEEPSEEK]
        self.assertEqual(streams.DEEPSEEK, "deepseek/deepseek-v4-pro-0813")
        # DeepSeek's own endpoint is barred by the account's no-training data policy (OpenRouter's 404, 20 Oct): the
        # cheapest endpoint that is not an fp4 quantisation
        # StreamLake and Alibaba run mainland content filters (censorship check, 20 Oct: canned refusals, answers cut
        # off, 2 of 5 batches blocked): never routed to, even when Ionstream is down
        self.assertEqual(ds["provider"], {"quantizations": ["fp8", "fp16", "bf16", "fp32", "unknown"], "sort": "price",
                                          "ignore": ["streamlake", "alibaba"]})
        # thinking asked for explicitly: Ionstream answered the first calls without it (0 reasoning tokens) unless asked
        self.assertEqual(ds["reasoning"], {"effort": "high"})
        # its thinking runs long (live, 20 Oct: up to 13,089 output tokens and 177 s for one call): room for both
        self.assertEqual((ds["max_tokens"], ds["timeout"]), (32000, 600))
        seen = []
        ai.jsonc = lambda prompt, purpose, **k: seen.append(k) or {"posts": [{"i": 0, "streams": []}]}
        try:
            streams._ask(STREAMS, [post("https://x.test/1", "t", "x")], "scoring_verify",
                         model=streams.CONFIRM_MODEL, checks={0: ["CEHK"]})
        finally:
            ai.jsonc = fake_jsonc
        self.assertEqual(seen[0]["model"], "deepseek/deepseek-v4.1-flash")
        self.assertEqual((seen[0]["max_tokens"], seen[0].get("provider"), seen[0]["reasoning"]),
                         (32000, opts["provider"], {"effort": "low"}))
        # the options reach OpenRouter's request body
        bodies = []

        class R:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def read(s):
                return json.dumps({"id": "g", "choices": [{"message": {"content": '{"posts": []}'}}],
                                   "usage": {"cost": 0}}).encode()
        real_open, real_chat = ai._open, ai.chat
        timeouts = []
        ai._open = lambda req, timeout: timeouts.append(timeout) or bodies.append(json.loads(req.data)) or R()
        ai.chat = REAL_CHAT
        key = mock.patch.dict(os.environ, {"OPENROUTER_API": "test"})  # the request's header; _open is the fake above
        key.start()
        try:
            REAL_JSONC("p", "scoring_verify", model=streams.CONFIRM_MODEL, max_tokens=opts["max_tokens"],
                       provider=opts.get("provider"), reasoning=opts.get("reasoning"), timeout=opts["timeout"])
            REAL_JSONC("p", "scoring")  # any other call: the 180 s it always had
        finally:
            ai._open, ai.chat = real_open, real_chat
            key.stop()
        self.assertEqual((bodies[0]["model"], bodies[0].get("provider"), bodies[0]["max_tokens"], bodies[0]["reasoning"]),
                         ("deepseek/deepseek-v4.1-flash", opts["provider"], 32000, {"effort": "low"}))
        self.assertEqual(timeouts, [600, 180])
        self.assertEqual(seen[0]["timeout"], 600)

    def test_13_a_stage2_provider_error_fails_only_its_batch(self):
        """a provider error on one stage-2 call (an outage, a filter error) leaves that call's posts due; the other
        batches of the round are scored and filed; OpenRouter's limit still stops the round (LimitReached)"""
        bad, good = "https://news.test/err-1", "https://news.test/err-2"
        FAIL["供應商出錯"] = RuntimeError("{'message': 'Provider returned error', 'code': 502}")
        real_batch = streams.CONFIRM_BATCH
        streams.CONFIRM_BATCH = 1
        try:
            poll([post(bad, "供應商出錯", "CEHK:h=70,o=80 " + TEXT), post(good, "李家超出席典禮", "CEHK:h=70,o=80 " + TEXT)])
            streams.run_once(STREAMS)
            self.assertIsNone(corpus.db().execute("SELECT 1 FROM scored WHERE post_id=%s", (pid(bad),)).fetchone())
            self.assertIsNone(filed(bad))
            self.assertEqual(filed(good)["score"], 80)
            FAIL.clear()
            streams.run_once(STREAMS)  # the next round: it answers, the post is scored and filed
            self.assertEqual(filed(bad)["score"], 80)
            FAIL["供應商出錯二"] = ai.LimitReached("Key limit exceeded")
            poll([post("https://news.test/err-3", "供應商出錯二", "CEHK:h=70,o=80 " + TEXT)])
            with self.assertRaises(ai.LimitReached):
                streams.run_once(STREAMS)
        finally:
            FAIL.clear()
            streams.CONFIRM_BATCH = real_batch

    def test_14_stage2_calls_only_the_two_models(self):
        """scoring a post through both stages calls stage 1 and stage 2 and nothing else (no model judges stage 2's
        answer; titles' own calls aside)"""
        ALL.clear()
        poll([post("https://news.test/only-2", "李家超視察工地", "CEHK:h=70,o=75 " + TEXT)])
        streams.run_once(STREAMS)
        self.assertEqual([p for p in ALL if not p.startswith("titles")], ["scoring", "scoring_verify"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
