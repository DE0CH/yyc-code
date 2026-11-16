#!/usr/bin/env python3
"""Every stream's exact score is stored, 0–19 too (Deyao, 2026-10-21: "my proof page should show me the exact score
even when below 20"). On a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again), both models
stubbed.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_all_scores.py

Covers:
  1. the prompts: stage 1 asks for a 0–100 score for each stream with SCORE_TOKENS (8,000) output; stage 2 asks for the
     checked pairs only; the stream definitions' hash (scored.defs) is the one from before, so no history is rescored;
  2. stage 1's 0–19 scores are stored with their reasons (CEHK 3 「無關」), scored.all_scores is set and kv
     scores_all_from is written once; nothing under the threshold is filed, tagged or sent;
  3. stage 2's answers for streams it was not asked about are dropped (its checked pair stands, the others keep
     stage 1's score and model);
  4. the staff item page (item.aiScores) shows a stage-1 score under the floor as before (no score: 「低於 20 分」), a
     stage-2 score under it as before (its number); the proof view (proof.entry) shows every exact score with
     stored = true, and a post scored before the change shows stored = false for the streams it has no row of;
  4b. the proof view also gets each stage's whole answer (scored.answers) and the tags kept;
  5. 串流's score line (scores.streamScores) shows no stage-1 score under the floor.
"""
import json, os, re, sys, tempfile, unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
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
CALLS = []   # (purpose, model, prompt, max_tokens)
MARK = re.compile(r"\b([A-Z_]+):h=(\d+)(?:,o=(\d+))?")
SCORED = [s["id"] for s in STREAMS]


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000, **kw):
    """stage 1 answers every scored stream: the marker's h= for a stream named in the post, else 3 「無關」. Stage 2
    answers the marker's o= for the named streams and, against its instructions, 7 for every other stream"""
    if not purpose.startswith("scoring"):
        return {"tags": []}
    CALLS.append((purpose, model, prompt, max_tokens))
    two = model == streams.CONFIRM_MODEL
    body = prompt.split("Posts:\n", 1)[1]
    out = []
    for i in re.findall(r"(?m)^(\d+)\. \[", body):
        block = body.split(f"\n{i}. ", 1)[-1] if i != "0" else body
        block = block.split(f"\n{int(i) + 1}. ", 1)[0]
        named = {sid: (o if two else h) for sid, h, o in MARK.findall(block)}
        st = [{"id": sid, "score": int(named[sid]), "reason": ("O" if two else "H") + named[sid], "category": None}
              if sid in named else {"id": sid, "score": 7 if two else 3, "reason": "無關"} for sid in SCORED]
        out.append({"i": int(i), "sentiment": "neutral", "labels": [], "streams": st})
    return {"posts": out}


ai.jsonc = fake_jsonc
ai.chat = lambda messages, purpose, **k: {"content": "hk"}
streams.WAIT_TEXT = timedelta(0)

TG = []
telegram_alerts.call = lambda method, **p: TG.append((method, p)) or ({"message_id": len(TG), "chat": {"id": 1}} if method == "sendMessage" else True)


def poll(items):
    sources.fetch_all = lambda extra: (list(items), {"x": {"ok": True, "items": len(items), "error": None}})
    sources.discuss_search = lambda qs: []
    app.after_poll = lambda g, f: insights.after_poll(g, f)
    app.threading = type("T", (), {"Thread": lambda target, args, daemon: type("X", (), {"start": lambda s: target(*args)})()})
    app.load_state()
    app.poll_once()


def post(url, title, text):
    return {"sid": "rthk", "source": "RTHK", "title": title, "url": url, "published": NOW, "text": text}


def pid(url):
    return corpus.ids_for_urls([url])[url]


def rows(url):
    return {r["stream"]: dict(r) for r in corpus.db().execute(
        "SELECT stream, score, reason, model, screen FROM scores WHERE post_id=%s", (pid(url),)).fetchall()}


TEXT = "字" * 600


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.db()
        corpus.kv_set("scoring", {"since": NOW.isoformat(), "from_id": 0, "comments": False})
        corpus.kv_set("score_backfill", {"on": False, "posts": 0})
        poll([post("https://seed.test/0", "seed", "x" * 600)])  # the source's first run (never filed)
        CALLS.clear()
        poll([post("https://n.test/low", "低分", "CEHK:h=12 " + TEXT),
              post("https://n.test/two", "覆核", "CEHK:h=60,o=15 HKIC:h=45,o=70 " + TEXT)])
        streams.run_once(STREAMS)

    def test_1_prompts_and_defs(self):
        one = [c for c in CALLS if c[1] is None]
        two = [c for c in CALLS if c[1] == streams.CONFIRM_MODEL]
        self.assertTrue(one and two)
        self.assertIn("for each topic above, a score from 0 to 100", one[0][2])
        self.assertNotIn("leave the stream out", one[0][2])
        self.assertEqual(one[0][3], streams.SCORE_TOKENS)
        self.assertGreaterEqual(streams.SCORE_TOKENS, 8000)
        # stage 2 reads stage 1's prompt (its title task added), nothing about stage 1's answer (Deyao, 2026-11-03)
        self.assertNotIn("Check:", two[0][2])
        head = lambda prompt: prompt.rsplit("\nPosts:\n", 1)[0]  # the batches differ: stage 2 has the checked post only
        self.assertEqual(head(two[0][2]).replace("\n" + streams.TITLE, ""), head(one[0][2]))
        # the hash the stored posts carry (scored.defs) is unchanged by the 21 Oct prompt change (and covers the briefs)
        import hashlib
        old = streams.RUBRIC.replace("0–19    unrelated", "0–19    unrelated (leave the stream out of your answer)")
        d = [[st["id"], st.get("name"), st.get("brief"), st.get("categories")]
             + ([b] if (b := streams.background(st["id"])) else []) for st in STREAMS]
        self.assertEqual(streams.defs_hash(STREAMS),
                         hashlib.md5(json.dumps([d, old, ai.MODEL], ensure_ascii=False).encode()).hexdigest()[:16])

    def test_2_low_scores_stored_nothing_filed(self):
        r = rows("https://n.test/low")
        self.assertEqual(set(r), set(SCORED))
        self.assertEqual((r["CEHK"]["score"], r["CEHK"]["reason"], r["CEHK"]["model"]), (12, "H12", ai.MODEL))
        self.assertEqual((r["FSHK"]["score"], r["FSHK"]["reason"]), (3, "無關"))
        self.assertTrue(corpus.db().execute("SELECT all_scores FROM scored WHERE post_id=%s", (pid("https://n.test/low"),)).fetchone()[0])
        self.assertTrue(corpus.kv_get("scores_all_from"))
        n = corpus.db().execute("SELECT count(*) FROM stream_items WHERE url LIKE 'https://n.test/%%' AND stream <> 'HKIC'").fetchone()[0]
        self.assertEqual(n, 0)
        tags = corpus.db().execute("SELECT profile FROM tags WHERE post_id = ANY(%s)",
                                   ([pid("https://n.test/low"), pid("https://n.test/two")],)).fetchall()
        self.assertEqual(sorted(t[0] for t in tags), ["HKIC"])

    def test_3_stage2_only_checked_pairs(self):
        r = rows("https://n.test/two")
        self.assertEqual((r["CEHK"]["score"], r["CEHK"]["model"], r["CEHK"]["screen"]), (15, streams.CONFIRM_MODEL, 60))
        self.assertEqual((r["HKIC"]["score"], r["HKIC"]["screen"]), (70, 45))
        self.assertEqual((r["FSHK"]["score"], r["FSHK"]["model"], r["FSHK"]["screen"]), (3, ai.MODEL, None))  # not stage 2's 7

    def test_4_staff_item_page_as_before_proof_exact(self):
        corpus.kv_set("edge:config", edge_config.build())
        staff = {x["stream"]: x for x in edge("item.aiScores", pid("https://n.test/two"))["streams"]}
        self.assertIsNone(staff["FSHK"]["score"])          # stage 1 under the floor: 「低於 20 分」 as before
        self.assertEqual(staff["CEHK"]["score"], 15)       # stage 2 under the floor: stored and shown before too
        self.assertNotIn("stored", staff["CEHK"])
        proof = edge("proof.entry", pid("https://n.test/two"))
        sc = {x["stream"]: x for x in proof["scores"]}
        self.assertEqual((sc["FSHK"]["score"], sc["FSHK"]["reason"], sc["FSHK"]["stored"]), (3, "無關", True))
        self.assertEqual(sc["CEHK"]["screen"]["score"], 60)
        self.assertTrue(proof["all_scores"])
        # every answer as the models gave it (scored.answers), both stages, and what was kept
        a = proof["answers"]
        self.assertEqual((a["stage1"]["model"], a["stage2"]["model"]), (ai.MODEL, streams.CONFIRM_MODEL))
        self.assertEqual(len(a["stage1"]["answer"]["streams"]), len(SCORED))
        self.assertEqual(a["stage2"]["answer"]["sentiment"], "neutral")
        self.assertEqual(proof["kept_tags"]["tag_model"], streams.CONFIRM_MODEL)
        self.assertTrue(proof["all_scores_from"])
        # a post scored before the change: no rows under 20, all_scores not set
        c = corpus.db()
        p = pid("https://n.test/low")
        c.execute("DELETE FROM scores WHERE post_id=%s AND score < 20", (p,))
        c.execute("UPDATE scored SET all_scores = NULL WHERE post_id=%s", (p,))
        old = edge("proof.entry", p)
        self.assertFalse(old["all_scores"])
        self.assertEqual({x["stream"]: x["stored"] for x in old["scores"]}["CEHK"], False)

    def test_5_stream_score_line(self):
        day = NOW.strftime("%Y-%m-%d")
        corpus.db().execute("""INSERT INTO stream_items (day, stream, url, item) VALUES (%s, 'FSHK', 'https://n.test/two',
                               %s) ON CONFLICT DO NOTHING""", (day, corpus.J({"seen": NOW.isoformat(timespec="seconds"), "title": "x"})))
        corpus.kv_set("edge:config", edge_config.build())
        got = edge("scores.streamScores", day)
        self.assertNotIn("FSHK|https://n.test/two", got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
