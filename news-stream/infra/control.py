#!/usr/bin/env python3
"""Deyao's control plane for the YYC site: profiles, settings, paid-tool spend. The website is read-only; there is
no admin page (Deyao, 2026-10-14: "no admin page, control plane is script from me manually"). Stream thresholds and
scoring are infra/thresholds.py; reports and themes are reports.py.

    python3 infra/control.py                       every profile (= a stream of streams.json), the general settings
                                                   and today's AI spend
    python3 infra/control.py show ID               one profile as JSON. What a profile covers is its stream's brief
                                                   in streams.json; its posts are the ones the scorer rated at or
                                                   above the threshold (infra/thresholds.py)
    python3 infra/control.py set KEY VALUE         a general setting: report_hour (0-23, HKT), image_threshold
                                                   (0-100: a picture scored at or above it is meaningful, images.py;
                                                   the posts' picture text is redone at once)
    python3 infra/control.py tool-spend            every account's paid-tool spend (data API / MCP paid tools; no limit)
    python3 infra/control.py websites              every website in the corpus with its type, region and post count
    python3 infra/control.py social [STREAM]       social-platform search: each platform's last check, the run log and
                                                   the newest posts (default GALBOT). Run now: tikhub_fetch.py --run --now STREAM

Build a report now:  python3 reports.py profile ID --day YYYY-MM-DD;  themes:  python3 reports.py themes ID

Runs wherever DATABASE_URL reaches the database; on the YYC cluster:
    SECRETS=~/.yyc-cluster news-stream/deploy/cluster/run.sh -- python /app/infra/control.py …   (a pod of its own, never the poller's)
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import corpus, insights  # noqa: E402

SETTING_TYPES = {"report_hour": int, "image_threshold": int}


def out(x):
    print(json.dumps(x, ensure_ascii=False, indent=1, default=str))


def overview():
    import ai
    for p in insights.profiles():
        print(f"{p['id']:10} {p.get('title') or p['name']}")
    print("settings:", json.dumps(insights.settings(), ensure_ascii=False))
    print("AI spend today:", json.dumps(ai.spend(), ensure_ascii=False, default=str))


def social(sid):
    import tikhub_fetch as tf
    streams = tf.streams()
    if not any(s["id"] == sid for s in streams):
        sys.exit(f"no such stream; streams: {', '.join(s['id'] for s in streams)}")
    plan = tf.queries(sid)
    keys = {f"{p}|{q}" for p, q in plan}
    d = corpus.file_get("tikhub.json", {"checks": {}, "items": [], "fetched": None})
    print(f"{sid}: {len(plan)} requests per run, USD {sum(tf.COST.get(p, 0) for p, _ in plan):.4f} at list price;"
          f" running now: {corpus.locked('tikhub')}; file written {d.get('fetched')}")
    for p, q in plan:
        c = d["checks"].get(f"{p}|{q}") or {}
        print(f"  {tf.PLATFORM_NAMES.get(p, p):10} {q[:30]:30} {json.dumps(c, ensure_ascii=False, default=str)[:120]}")
    runs = [r for r in corpus.file_get("social_runs.json", []) if r["trigger"] in ("daily", f"button {sid}")]
    print("runs (newest first):")
    for r in runs[-30:][::-1]:
        print("  " + json.dumps(r, ensure_ascii=False, default=str)[:200])
    items = sorted((i for i in d["items"] if i.get("key") in keys), key=lambda i: i.get("published") or "", reverse=True)
    print(f"posts: {len(items)} (newest 30)")
    for i in items[:30]:
        print(f"  {(i.get('published') or '')[:16]}  {i.get('platform', '')}  {(i.get('title') or '')[:70]}\n      {i.get('url')}")


def main(a):
    if not a:
        return overview()
    cmd, rest = a[0], a[1:]
    if cmd == "show" and len(rest) == 1:
        p = insights.profile(rest[0])
        return out(p) if p else sys.exit("no such profile")
    if cmd == "set" and len(rest) == 2 and rest[0] in SETTING_TYPES:
        got = insights.save_settings({rest[0]: SETTING_TYPES[rest[0]](rest[1])})
        if rest[0] == "image_threshold":
            import images
            got["posts_redone"] = images.refresh_all()
        return out(got)
    if cmd == "tool-spend" and not rest:
        from tools import spend
        return out({"spend": spend.everyone()})
    if cmd == "websites" and not rest:
        for r in corpus.db().execute("SELECT website, source_type, region, count(*) n FROM posts GROUP BY 1,2,3 ORDER BY n DESC, 1, 2, 3"):
            print(f"{r['n']:8}  {r['website']}  {r['source_type']}  {r['region']}")
        return
    if cmd == "social" and len(rest) <= 1:
        return social(rest[0] if rest else "GALBOT")
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
