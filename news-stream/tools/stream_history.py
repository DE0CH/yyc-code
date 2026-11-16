#!/usr/bin/env python3
"""Populate ONE stream with the whole corpus's history, on Deyao's word for that stream (first: 宏福苑 WFC, 2026-10-14:
"score all existing posts, file them into 串流 under their own day, send them all to its Telegram channel, oldest
first"). Other streams are never touched, and the general history backfill (streams.py backfill_*) stays as it is.

  1. score: every non-comment post (every date), oldest id first, in streams.BATCH batches against this stream only
     (same model, rubric and prompt as live scoring, only this stream listed), WORKERS model calls at a time, into
     `scores` (this stream's row only) and this stream's profile tags (at or above its threshold). Posts the live
     scorer already scored with the current stream definitions (scored.defs) are skipped: their score is there.
     The table `scored` is not written (it records an all-streams score). Spend: ai_spend purpose `scoring_history`.
  2. file: every post scored at or above the threshold becomes a stream item under its own publication day (HKT),
     oldest first, deduped the usual way (one item per (day, url), and one per normalised headline within a day of
     another item of the stream).
  3. Telegram: kv telegram_sent[STREAM] is set to the id before the first item filed, so the sender
     (telegram_alerts.py, which goes by stream_items.id) posts every filed item, oldest first, at its usual pace,
     resumably and once, as soon as the stream has a `telegram_chat`.

Resumable: kv `stream_history:<STREAM>` keeps the last post id scored and the phase; a rerun carries on. --max-usd
stops the scoring when its own spend reaches that figure.

    python3 tools/stream_history.py WFC estimate          posts to score and the projected cost
    python3 tools/stream_history.py WFC run [--max-usd N] score, file, arm Telegram
    python3 tools/stream_history.py WFC status
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import corpus, streams as sc  # noqa: E402

PURPOSE = "scoring_history"
WORKERS = 8
CHUNK = sc.BATCH * WORKERS * 4
RETRIES = 2


def stream(sid):
    with open(os.path.join(os.path.dirname(os.path.abspath(sc.__file__)), "streams.json")) as f:
        st = json.load(f)
    one = next(s for s in st if s["id"] == sid)
    return st, one


def state(sid):
    return corpus.kv_get(f"stream_history:{sid}") or {"after": 0, "phase": "score", "scored": 0, "usd": 0.0}


def save(sid, s):
    s["at"] = datetime.now(sc.HKT).isoformat(timespec="seconds")
    corpus.kv_set(f"stream_history:{sid}", s)


def todo_sql(count=False):
    sel = "count(*)" if count else f"p.*, {sc.HASH} AS hash"
    return f"""SELECT {sel} FROM posts p LEFT JOIN scored s ON s.post_id = p.id
               WHERE p.thread IS NULL AND p.id > %s AND (s.post_id IS NULL OR s.defs IS DISTINCT FROM %s)"""


def spent():
    r = corpus.db().execute("SELECT coalesce(sum(usd), 0) FROM ai_spend WHERE purpose=%s", (PURPOSE,)).fetchone()
    return float(r[0])


def estimate(sid):
    allst, _ = stream(sid)
    n = corpus.db().execute(todo_sql(True), (state(sid)["after"], sc.defs_hash(allst))).fetchone()[0]
    day = datetime.now(sc.HKT).strftime("%Y-%m-%d")
    usd = corpus.db().execute("SELECT coalesce(sum(usd), 0) FROM ai_spend WHERE purpose='scoring' AND day=%s", (day,)).fetchone()[0]
    done = corpus.db().execute("SELECT count(*) FROM scored WHERE at >= %s", (day,)).fetchone()[0]
    per = float(usd) / done if done else 0.0005
    return {"posts": n, "usd_per_post_live_all_streams": round(per, 6), "projected_usd_upper": round(n * per, 2)}


def halves(st, batch):
    """score a batch as two halves → one result keyed by the batch's own indexes ({} when both fail)"""
    h = max(1, len(batch) // 2)
    out = {}
    for off in (0, h):
        part = batch[off:off + h] if off == 0 else batch[h:]
        if part:
            out.update({off + i: v for i, v in sc.score_posts([st], part, PURPOSE).items()})
    return out


def score(sid, max_usd):
    allst, st = stream(sid)
    defs = sc.defs_hash(allst)
    s = state(sid)
    base = spent() - s.get("usd", 0.0)  # spend under this purpose from before this run's own
    t = sc.threshold(sid)
    while True:
        posts = [dict(r) for r in corpus.db().execute(todo_sql() + " ORDER BY p.id LIMIT %s", (s["after"], defs, CHUNK))]
        if not posts:
            break
        batches = [posts[k:k + sc.BATCH] for k in range(0, len(posts), sc.BATCH)]
        with ThreadPoolExecutor(WORKERS) as ex:
            results = list(ex.map(lambda b: sc.score_posts([st], b, PURPOSE), batches))
            for _ in range(RETRIES):  # a batch whose answer wasn't JSON twice: again, then in halves
                bad = [k for k, r in enumerate(results) if not r]
                if not bad:
                    break
                again = list(ex.map(lambda k: halves(st, batches[k]), bad))
                for k, r in zip(bad, again):
                    results[k] = r
        c = corpus.db()
        with c:
            for b, res in zip(batches, results):
                for i, p in enumerate(b):
                    got = (res.get(i) or {}).get("streams", {}).get(sid)
                    c.execute("DELETE FROM scores WHERE stream=%s AND post_id=%s", (sid, p["id"]))
                    if not got:
                        continue
                    c.execute("INSERT INTO scores (stream, post_id, score, reason, category, scored_at) VALUES (%s,%s,%s,%s,%s,%s)",
                              (sid, p["id"], got["score"], got["reason"], got["category"],
                               datetime.now(sc.HKT).isoformat(timespec="seconds")))
                    if got["score"] >= t:
                        corpus.set_tags(sid, [(p["id"], True, res[i]["sentiment"], res[i]["labels"])])
        missed = sum(len(b) for b, r in zip(batches, results) if not r)
        s.update(after=posts[-1]["id"], scored=s["scored"] + len(posts) - missed, usd=round(spent() - base, 4))
        s["missed"] = s.get("missed", 0) + missed
        save(sid, s)
        print(f"score: up to id {s['after']}, {s['scored']} posts, USD {s['usd']}", flush=True)
        if max_usd is not None and s["usd"] >= max_usd:
            print(f"score: stopped at the --max-usd {max_usd}", flush=True)
            return False
    s["phase"] = "file"
    save(sid, s)
    return True


def file(sid):
    """the posts scored at or above the threshold → stream items under their publication day, oldest first"""
    _, st = stream(sid)
    t = sc.threshold(sid)
    c = corpus.db()
    rows = [dict(r) for r in c.execute("""SELECT p.*, s.score, s.reason, s.category, sd.sentiment AS sd_sent,
                                                 sd.labels AS sd_labels, tg.sentiment AS tg_sent, tg.labels AS tg_labels
                                          FROM scores s JOIN posts p ON p.id = s.post_id
                                          LEFT JOIN scored sd ON sd.post_id = p.id
                                          LEFT JOIN tags tg ON tg.profile = s.stream AND tg.post_id = p.id
                                          WHERE s.stream=%s AND s.score >= %s AND p.thread IS NULL
                                          ORDER BY coalesce(p.published, p.seen), p.id""", (sid, t))]
    have = {}  # normalised headline -> [days] of the stream's items
    for r in c.execute("SELECT day, item->>'title' FROM stream_items WHERE stream=%s", (sid,)):
        have.setdefault(sc.norm_title(r[1]), []).append(r[0])
    first, n, per_day = None, 0, {}
    with c:
        for p in rows:
            day = sc.item_day(p)
            key = sc.norm_title(p.get("title"))
            d0 = datetime.strptime(day, "%Y-%m-%d")
            if key and any(abs((datetime.strptime(d, "%Y-%m-%d") - d0).days) <= 1 for d in have.get(key, [])):
                continue
            labels = p.get("tg_labels") or p.get("sd_labels") or []
            if isinstance(labels, str):
                labels = json.loads(labels)
            tag = {"sentiment": p.get("tg_sent") or p.get("sd_sent"), "labels": labels}
            when = datetime.fromisoformat(p.get("published") or p["seen"]).astimezone(sc.HKT)
            it = sc.stream_item(p, sid, {"score": p["score"], "reason": p["reason"], "category": p["category"]}, tag, when)
            it["history"] = True
            got = c.execute("""INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s)
                               ON CONFLICT (day, stream, url) DO NOTHING RETURNING id""",
                            (day, sid, p["url"], corpus.J(it))).fetchone()
            if got:
                first = first or got[0]
                n += 1
                per_day[day] = per_day.get(day, 0) + 1
                have.setdefault(key, []).append(day)
    s = state(sid)
    pd = s.get("per_day") or {}
    for d, k in per_day.items():
        pd[d] = pd.get(d, 0) + k
    s.update(phase="done", filed=s.get("filed", 0) + n, per_day=dict(sorted(pd.items())))
    lo = c.execute("SELECT min(id) FROM stream_items WHERE stream=%s", (sid,)).fetchone()[0]
    if lo:  # Telegram: every item of the stream, from its first (the sender goes by id and never re-sends)
        sent = corpus.kv_get("telegram_sent", {}) or {}
        sent[sid] = min(lo - 1, sent.get(sid, lo - 1))
        corpus.kv_set("telegram_sent", sent)
        s["telegram_from"] = sent[sid]
    save(sid, s)
    print(f"file: {n} items filed", flush=True)
    return per_day


if __name__ == "__main__":
    sid, cmd = sys.argv[1], sys.argv[2]
    if cmd == "estimate":
        print(json.dumps(estimate(sid)))
    elif cmd == "status":
        print(json.dumps(state(sid), ensure_ascii=False))
    elif cmd == "run":
        mx = float(sys.argv[sys.argv.index("--max-usd") + 1]) if "--max-usd" in sys.argv else None
        if state(sid)["phase"] == "score" and not score(sid, mx):
            sys.exit(1)
        if state(sid)["phase"] == "file":
            print(json.dumps(file(sid), ensure_ascii=False, sort_keys=True))
        print(json.dumps(state(sid), ensure_ascii=False))
