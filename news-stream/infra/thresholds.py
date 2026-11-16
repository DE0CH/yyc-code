#!/usr/bin/env python3
"""Deyao's control of the scored streams (streams.py); there is no admin page for this (Deyao, 2026-10-14).

    python3 infra/thresholds.py                   thresholds, scoring settings, and per stream how the last 3 days'
                                                  scores spread over the rubric's bands
    python3 infra/thresholds.py set CEHK 60       a stream's threshold (0–100; default 50). 數據分析 applies it at once
                                                  to every scored post (its profile = the posts scored at or above
                                                  it; the tags are synced); the stream page and Telegram apply it to
                                                  posts scored from now on (Telegram skips queued items under it),
                                                  and items already on the stream page stay
    python3 infra/thresholds.py preview CEHK 60   the last 2 days' posts that 60 would add or drop, with their scores
                                                  and the model's reasons (nothing changes)
    python3 infra/thresholds.py comments on|off   score comment/forum-reply rows too (each with its thread's headline)
    python3 infra/thresholds.py gaps              outlets Google News showed that no watched source covers
    python3 infra/thresholds.py cost [DAYS]       OpenRouter's charged cost of scoring per day (default 7 days)
    python3 infra/thresholds.py backfill          the history backfill (streams.py backfill_*): posts left, its cost so
                                                  far and per post, the projected rest, the key's balance (OpenRouter's
                                                  figure), and its state (`limit` = OpenRouter refused for the key's
                                                  limit or credit)
    python3 infra/thresholds.py backfill on|off   switch it (off by default: a manual run of the scoring backlog, infra/backlog.py);
                                                  it never sends to 串流 or Telegram

Runs wherever DATABASE_URL reaches the database; on the YYC cluster:
    SECRETS=~/.yyc-cluster news-stream/deploy/cluster/run.sh -- python /app/infra/thresholds.py …   (a pod of its own, never the poller's)
"""
import os, sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json  # noqa: E402

import corpus, streams  # noqa: E402

BANDS = [(90, 101), (60, 90), (50, 60), (40, 50), (30, 40), (20, 30)]
STREAMS = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "streams.json")))


def since(days):
    return (datetime.now(corpus.HKT) - timedelta(days=days)).isoformat(timespec="seconds")


def show():
    s = streams.settings()
    print(f"scoring new posts after id {s['from_id']} (since {s.get('since')}); comments: {'on' if s.get('comments') else 'off'}")
    print(f"{'stream':10} {'threshold':>9}  " + "  ".join(f"{a}-{b - 1 if b <= 100 else 100}".rjust(7) for a, b in BANDS))
    for st in STREAMS:
        rows = corpus.db().execute("SELECT score FROM scores WHERE stream=%s AND scored_at >= %s", (st["id"], since(3))).fetchall()
        sc = [r[0] for r in rows]
        print(f"{st['id']:10} {streams.threshold(st['id']):>9}  " + "  ".join(str(sum(a <= x < b for x in sc)).rjust(7) for a, b in BANDS))


def preview(sid, new):
    cur = streams.threshold(sid)
    lo, hi = sorted((cur, new))
    rows = corpus.db().execute("""SELECT s.score, s.reason, p.title, p.website, p.url FROM scores s JOIN posts p ON p.id=s.post_id
                                  WHERE s.stream=%s AND s.scored_at >= %s AND s.score >= %s AND s.score < %s
                                  ORDER BY s.score DESC, s.post_id DESC""", (sid, since(2), lo, hi)).fetchall()
    verb = "add" if new < cur else "drop"
    print(f"{sid}: {cur} → {new} would {verb} {len(rows)} post(s) of the last 2 days")
    for score, reason, title, site, url in rows:
        print(f"  {score:3}  {title[:70]}  [{site}]  — {reason}\n       {url}")


def gaps():
    g = corpus.kv_get("source_gaps", {}) or {}
    print(f"{len(g)} outlet(s) seen on Google News that no watched source covers (most seen first):")
    for h, v in sorted(g.items(), key=lambda kv: -kv[1]["count"]):
        print(f"  {v['count']:4}  {h}  ({v.get('name')})  {v['first'][:10]} – {v.get('last', '')[:10]}\n        e.g. {v['examples'][-1]}")


def cost(days):
    rows = corpus.db().execute("SELECT day, usd FROM ai_spend WHERE purpose='scoring' AND day >= %s ORDER BY day",
                               ((datetime.now(corpus.HKT) - timedelta(days=days)).strftime("%Y-%m-%d"),)).fetchall()
    for day, usd in rows:
        print(f"  {day}  USD {usd:.4f}")
    print(f"total USD {sum(r[1] for r in rows):.4f} over {len(rows)} day(s) (OpenRouter's charged cost)")


def backfill():
    b = streams.backfill_settings()
    left = streams.backfill_left(STREAMS)
    usd = corpus.db().execute("SELECT coalesce(sum(usd), 0) FROM ai_spend WHERE purpose = ANY(%s)",
                           ([streams.BACKFILL_PURPOSE, streams.BACKFILL_PURPOSE + streams.VERIFY],)).fetchone()[0]
    pp = streams.per_post()
    print(f"backfill {'on' if b.get('on', True) else 'off'}, state {b.get('state')} at {b.get('at')}")
    print(f"  scored {b.get('posts', 0)} post(s) for USD {usd:.4f} (OpenRouter's charged cost); USD {pp:.6f} per post")
    print(f"  left {left} post(s): projected USD {left * pp:.4f}")
    try:
        k = streams.key_info()
        print(f"  key: USD {k.get('limit_remaining')} left of its USD {k.get('limit')} limit (OpenRouter's figure)")
    except Exception as e:
        print("  key: OpenRouter's figure unavailable:", repr(e)[:120])
    lim = corpus.kv_get("openrouter_limit")
    if lim:
        print(f"  OpenRouter refused since {lim.get('since')}: {lim.get('message')}")


def main(a):
    ids = {st["id"] for st in STREAMS}
    if not a:
        return show()
    if a[0] == "set" and len(a) == 3 and a[1] in ids and a[2].isdigit() and 0 <= int(a[2]) <= 100:
        t = streams.thresholds()
        t[a[1]] = int(a[2])
        corpus.kv_set("stream_thresholds", t)
        print(f"{a[1]} threshold = {a[2]}")
        return print("tags synced (dropped, added):", streams.sync_tags(STREAMS))
    if a[0] == "preview" and len(a) == 3 and a[1] in ids and a[2].isdigit():
        return preview(a[1], int(a[2]))
    if a[0] == "comments" and a[1:] in (["on"], ["off"]):
        s = streams.settings()
        s["comments"] = a[1] == "on"
        corpus.kv_set("scoring", s)
        return print("comment scoring", a[1])
    if a[0] == "backfill" and a[1:] in ([], ["on"], ["off"]):
        if a[1:]:
            b = streams.backfill_settings()
            b["on"] = a[1] == "on"
            corpus.kv_set("score_backfill", b)
            print("backfill", a[1])
        return backfill()
    if a[0] == "gaps":
        return gaps()
    if a[0] == "cost":
        return cost(int(a[1]) if a[1:] and a[1].isdigit() else 7)
    sys.exit(__doc__ + f"\nstreams: {', '.join(sorted(ids))}")


if __name__ == "__main__":
    main(sys.argv[1:])
