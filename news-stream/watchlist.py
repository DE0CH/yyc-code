"""The accounts YYC's stream WATCHES directly, every post they publish (Deyao, 2026-10-14): watchlist.json, one row per
(account, platform), in four groups: media (outlets' official accounts), kol (voices that shape HK discussion),
politician (CE, officials, ExCo, LegCo, parties, departments), business (tycoons, companies, HKIC, Galbot).

    row = {"group", "name", "owner", "role", "platform", "handle", "url", "id", "why", "evidence", "since",
           optional: "inactive", "every_hours" (Instagram), "kind" (Facebook)}

Who fetches which platform:
  facebook                 fb_fetch.py   (Facebook's own timeline request over the IPRoyal sub-user yycstream)
  instagram, threads       ig_fetch.py   (the logged-out profile page over the sub-user yycstream)
  youtube                  watch_fetch.py (the channel's own RSS feed, no key, no proxy)
  x, tiktok                watch_fetch.py (TikHub, YYC's key yyc-news-stream)

The safety stop: USD 100 a day (Deyao, 2026-10-15; "just so I don't find a huge bill tomorrow morning"): the
spend the watching ADDS (rows with since >= WATCH_START, and every row watch_fetch.py reads) is metered per HKT day:
  tikhub  each successful TikHub request at its list price (TikHub bills exactly that per successful request)
  proxy   bytes on the wire through IPRoyal for those accounts (their profile checks and comment pages), at the per-GB
          price of YYC's last IPRoyal order
  model   OpenRouter's charged cost the watched posts add: the per-post AI (scoring, titles…) by their share of the
          posts first seen that day, and the transcription of the clips only watched posts carry (media.cost)
When the day's total reaches CAP_USD (env WATCH_CAP_USD, default 100), watching pauses until the next HKT day: the
jobs skip those accounts (YouTube too: its feed is free, but its videos are transcribed), kv watch_paused says so (the monitor shows it) and Deyao gets one Discord message. It is a
stop, not a plan: nothing is trimmed or sampled ahead of it. The accounts watched before 2026-10-14 (the Facebook pages
and Instagram accounts the stream already followed) are not part of it and keep running as they did.
"""
import json, os, re
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "watchlist.json")
HKT = timezone(timedelta(hours=8))
GROUPS = ("media", "kol", "politician", "business")
PLATFORMS = ("youtube", "facebook", "instagram", "threads", "x", "tiktok")
PLATFORM_NAMES = {"youtube": "YouTube", "facebook": "Facebook", "instagram": "Instagram", "threads": "Threads", "x": "X",
                  "tiktok": "TikTok"}
WATCH_START = "2026-10-14"  # rows added from this day on are the new watching the safety stop covers
IPROYAL_USD_PER_GB = 5.51  # YYC's last IPRoyal residential order 83044530 (2026-09-20): USD 55.10 for 10 GB


def cap_usd():
    return float(os.environ.get("WATCH_CAP_USD") or 100)


def load(path=None):
    with open(path or FILE) as f:
        return json.load(f)


def problems(rows):
    """→ [message] for rows that break the list's shape (tests/test_watch.py runs it on watchlist.json)"""
    out, seen = [], set()
    for i, r in enumerate(rows):
        where = f"row {i} ({r.get('name')!r}, {r.get('platform')})"
        for k in ("group", "name", "platform", "why", "since"):
            if not r.get(k):
                out.append(f"{where}: no {k}")
        if r.get("group") not in GROUPS:
            out.append(f"{where}: group {r.get('group')!r}")
        if r.get("platform") not in PLATFORMS:
            out.append(f"{where}: platform {r.get('platform')!r}")
        if not (r.get("handle") or r.get("id") or r.get("url")):
            out.append(f"{where}: no handle, id or url")
        if r.get("platform") == "youtube" and not re.fullmatch(r"UC[\w-]{22}", r.get("id") or ""):
            out.append(f"{where}: YouTube needs its channel id (UC…)")
        k = key(r)
        if k in seen:
            out.append(f"{where}: listed twice ({k})")
        seen.add(k)
    return out


def key(r):
    """one account on one platform: platform:<id or handle or url>, lower-case"""
    ref = r.get("id") or r.get("handle") or r.get("url") or ""
    if r.get("platform") == "facebook":
        ref = r.get("profile_id") or r.get("url") or ref
    return f"{r.get('platform')}:{str(ref).strip().lstrip('@').lower()}"


def is_new(r):
    return (r.get("since") or "") >= WATCH_START


def accounts(platform, rows=None):
    return [r for r in (load() if rows is None else rows) if r.get("platform") == platform]


def source_name(r):
    """the corpus `source` of a Facebook / Instagram / Threads row's posts (fb_fetch: the page name; ig_fetch: "<name>
    (Instagram)" / "<name> (Threads)")"""
    return r["name"] if r["platform"] == "facebook" else f"{r['name']} ({PLATFORM_NAMES[r['platform']]})"


def new_sources(rows=None):
    """the corpus `source` names of the newly watched Facebook / Instagram / Threads accounts; a name an account
    watched before also uses stays out (its posts can't be told apart, so they are not counted as new)"""
    rows = load() if rows is None else rows
    rows = [r for r in rows if r["platform"] in ("facebook", "instagram", "threads")]
    old = {source_name(r) for r in rows if not is_new(r)}
    return {source_name(r) for r in rows if is_new(r)} - old


# ---- the daily safety stop ----

def today():
    return datetime.now(HKT).strftime("%Y-%m-%d")


def add_tikhub(usd, day=None):
    import corpus
    return float(corpus.kv_add(f"watch:tikhub_usd:{day or today()}", round(usd, 6)))


def add_proxy_bytes(n, day=None):
    import corpus
    return int(corpus.kv_add(f"watch:proxy_bytes:{day or today()}", int(n)))


PER_POST_AI = ("scoring", "scoring_verify", "titles", "market", "images", "tagging_streams")  # background AI done once per post


def model_usd(day):
    """the watched posts' share of the day's OpenRouter charged cost: per-post AI (PER_POST_AI) by their share of the
    posts first seen that day; transcription by the charged cost of the clips that only watched posts carry"""
    import corpus
    c = corpus.db()
    start = datetime.fromisoformat(day + "T00:00:00+08:00")
    a, b = start.isoformat(timespec="seconds"), (start + timedelta(days=1)).isoformat(timespec="seconds")
    watched = "(sid LIKE 'watch\\_%%' OR (sid IN ('facebook', 'instagram') AND source = ANY(%s)))"
    src = sorted(new_sources())
    per_post = float(c.execute("SELECT coalesce(sum(usd), 0) FROM ai_spend WHERE day=%s AND purpose = ANY(%s)",
                               (day, list(PER_POST_AI))).fetchone()[0])
    share = 0.0
    if per_post:
        r = c.execute(f"SELECT count(*) FILTER (WHERE {watched}), count(*) FROM posts WHERE seen >= %s AND seen < %s",
                      (src, a, b)).fetchone()
        share = per_post * r[0] / r[1] if r[1] else 0.0
    clips = float(c.execute(f"""SELECT coalesce(sum(m.cost), 0) FROM media m WHERE m.status = 'done' AND m.done_at >= %s
                                AND m.done_at < %s AND EXISTS (SELECT 1 FROM post_media pm JOIN posts p ON p.id = pm.post_id
                                                               WHERE pm.key = m.key)
                                AND NOT EXISTS (SELECT 1 FROM post_media pm JOIN posts p ON p.id = pm.post_id
                                                WHERE pm.key = m.key AND NOT {watched})""", (a, b, src)).fetchone()[0])
    return share + clips


def spend(day=None):
    """{tikhub, proxy, proxy_bytes, model, total, cap} USD for an HKT day"""
    import corpus
    day = day or today()
    tik = float(corpus.kv_get(f"watch:tikhub_usd:{day}") or 0)
    pb = int(float(corpus.kv_get(f"watch:proxy_bytes:{day}") or 0))
    proxy = pb / 1e9 * IPROYAL_USD_PER_GB
    try:
        model = model_usd(day)
    except Exception as e:  # noqa: BLE001: the model share is the smallest part; never let it stop the check
        print("watch spend: model share:", repr(e)[:160], flush=True)
        model = 0.0
    return {"day": day, "tikhub": round(tik, 4), "proxy": round(proxy, 4), "proxy_bytes": pb, "model": round(model, 4),
            "total": round(tik + proxy + model, 4), "cap": cap_usd()}


def paused():
    """True while the day's watching spend is at or over the cap (records the pause and tells Deyao once a day).
    A new HKT day clears it."""
    import corpus, notify
    s = spend()
    cur = corpus.kv_get("watch_paused")
    if s["total"] < s["cap"]:
        if cur:
            corpus.kv_set("watch_paused", None)
        return False
    if not cur or cur.get("day") != s["day"]:
        cur = {"day": s["day"], "since": datetime.now(HKT).isoformat(timespec="seconds"), "spend": s, "notified": False}
    if not cur.get("notified"):
        cur["notified"] = notify.deyao(
            f"YYC account watching reached its daily safety stop: USD {s['total']:.2f} of USD {s['cap']:.2f} on {s['day']} "
            f"(TikHub {s['tikhub']:.2f}, proxy {s['proxy']:.2f}, model share {s['model']:.2f}). Watching is paused until "
            "00:00 HKT and carries on by itself then; keyword search and the other sources keep running. To raise the stop, "
            "set WATCH_CAP_USD.")
    cur["spend"] = s
    corpus.kv_set("watch_paused", cur)
    return True
