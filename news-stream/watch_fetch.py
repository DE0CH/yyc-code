#!/usr/bin/env python3
"""Watch the accounts in watchlist.json: every new post of each account on YouTube, X and TikTok (Deyao, 2026-10-14).
Facebook, Instagram and Threads rows are
read by fb_fetch.py and ig_fetch.py (their proxied paths); see watchlist.py for the list, the groups and the safety stop.

How each platform is read (one request per account and check; every post since the newest one seen comes in):
  youtube    the channel's own RSS feed (youtube.com/feeds/videos.xml?channel_id=…, its 15 newest uploads, views and
             likes), no key, no proxy, every run (10 min); during YouTube's feed outages (404/500 for every channel)
             the channel's uploads playlist page instead (sources.youtube, exact times from the player answer).
             Channels sources.py already reads are left to it.
  x          TikHub /twitter/web/fetch_user_post_tweet (USD 0.001; ~20 posts, its cursor read on while every post
             is new); pure retweets are someone else's post and are skipped (quotes are kept, with the quoted text);
             t.co links expanded, an X Article's full text read once through the tweet detail (USD 0.001), photos
             and videos passed on (tikhub_fetch.x_tweet); every 30 min
  tiktok     TikHub /tiktok/app/v3/fetch_user_post_videos by username (USD 0.001; pinned videos first, then newest);
             every 30 min
Cadence: the CronJob runs every 10 min; YouTube accounts are checked on every run, X and TikTok accounts every 30 min
(EVERY_RUN). A check reads everything since the newest post seen, so a later check loses nothing. No comments are read here (Deyao, 2026-10-20: comments only where the two-pass scoring needs them, and
these platforms' posts are not two-pass units; twopass.py).

Writes the document watch.json (corpus.file_put): {fetched, ok, paused, accounts: {key: state}, items},
read by sources.watch_file (sid watch_<platform>). Every successful TikHub request is metered for the daily safety
stop (watchlist.add_tikhub); when watchlist.paused() says the day's stop is reached, nothing is fetched until the next
HKT day and the last posts stay in the file.

    python3 watch_fetch.py run                 one pass (CronJob `watch`, every 10 min)
    python3 watch_fetch.py test PLATFORM REF   print one account's posts (REF = handle / id), no state written
"""
import json, os, re, sys, time, urllib.error, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import tikhub_fetch as th
import watchlist

HKT = timezone(timedelta(hours=8))
UA = "yyc-news-stream/1"
RUN_SECONDS = int(os.environ.get("WATCH_RUN_SECONDS", 480))  # the CronJob runs every 10 min; the rest waits for the next run
FIRST_HOURS = 48  # an account seen for the first time: its posts of the last 2 days (the poller's window)
OVERLAP_H = 3  # each check files posts back to 3 h before the newest one seen (not yet in the file)
KEEP_HOURS = 48  # posts kept in watch.json after they were first found (the poller ingests them on its next poll)
YOUTUBE_MIN = 10  # RSS costs nothing: every run of the 10-min CronJob (15 with the 3-min slack meant every 20 min)
# X and TikTok accounts every 30 min (Deyao, 2026-11-11: every 10 min was 32 accounts × 144 checks a day = USD 4.61 a day
# at TikHub's USD 0.001, the largest TikHub item; 30 min is USD 1.54 a day)
EVERY_RUN = {"x": 30, "tiktok": 30}
MAX_PAGES = 5  # a check reads on with the cursor while every post on the page is new (X, TikTok)
# USD per successful request (TikHub get_all_endpoints_info, 2026-10-14)
PRICE = {"x": 0.001, "tiktok": 0.001}


def now():
    return datetime.now(HKT)


def iso(t):
    return th.iso(t)


def tik(path, params=None, body=None, price=0.0):
    """one TikHub request; a successful one is metered for the safety stop at its list price"""
    d = th.call(path, params, body, tag=path.strip("/").replace("/", "_"))
    if price and not os.environ.get("TIKHUB_REPLAY_DIR"):
        watchlist.add_tikhub(price)
    return d


def post(url, text, published, engagement=None, pid=None, author=None, pinned=False, images=None, clips=None):
    out = {"url": url, "text": " ".join(str(text or "").split()), "published": published,
           "engagement": {k: v for k, v in (engagement or {}).items() if isinstance(v, int)}, "id": pid,
           "author": author, "pinned": pinned}
    if images:
        out["images"] = images
    if clips:
        out["clips"] = clips
    return out


# ---------- platforms: (row) → [post], newest first ----------

FEED_OUTAGE = (404, 500, 502, 503)  # YouTube's feed endpoint during its outages answers these for every channel


def youtube(row):
    """the channel's feed (exact times, descriptions, counts); while YouTube's feed endpoint is down (404/500 for every
    channel, from 19 Oct 2026 20:48 HKT), its uploads playlist page, the page sources.py reads every poll"""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={row['id']}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-HK,zh;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return youtube_feed(r.read())
    except urllib.error.HTTPError as e:
        if e.code not in FEED_OUTAGE:
            raise
    return youtube_uploads(row)


def youtube_uploads(row):
    """the channel's uploads playlist page (sources.youtube: titles, views, relative times), each video's description
    and, for a coarse time, its exact time from YouTube's player answer (sources.youtube_info: the page has neither); a
    row with no relative time is an old upload and left out"""
    import sources
    page = sources.fetch("https://www.youtube.com/playlist?list=UU" + row["id"][2:])
    rows = sources.youtube(page)
    info = sources.youtube_info(sources.youtube_recent(rows))
    exact = sources.youtube_exact(rows, info)
    out = []
    for it in rows:
        m = sources.YT_VID.search(it.get("url") or "")
        t = exact.get(it["url"]) or sources.parse_time(it.get("time"))
        if not m or not isinstance(t, datetime):
            continue
        desc = (info.get(it["url"]) or {}).get("text")
        out.append(post(it["url"], "\n".join(x for x in (it.get("title"), desc) if x),  # as the feed gives it
                        t.astimezone(HKT).isoformat(timespec="seconds"), it.get("engagement") or {}, m.group(1),
                        row.get("name")))
        out[-1]["title"] = it.get("title") or ""
    return out


def youtube_feed(xml):
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
          "media": "http://search.yahoo.com/mrss/"}
    out = []
    for e in ET.fromstring(xml).findall("a:entry", ns):
        vid = e.findtext("yt:videoId", namespaces=ns)
        title = e.findtext("a:title", namespaces=ns) or ""
        desc = e.findtext("media:group/media:description", namespaces=ns) or ""
        st = e.find("media:group/media:community/media:statistics", ns)
        rating = e.find("media:group/media:community/media:starRating", ns)
        eng = {"views": int(st.get("views")) if st is not None and (st.get("views") or "").isdigit() else None,
               "likes": int(rating.get("count")) if rating is not None and (rating.get("count") or "").isdigit() else None}
        link = (e.find("a:link", ns).get("href") if e.find("a:link", ns) is not None else None) or \
            f"https://www.youtube.com/watch?v={vid}"
        out.append(post(link, "\n".join(x for x in (title, desc) if x), iso(e.findtext("a:published", namespaces=ns)), eng, vid,
                        e.findtext("a:author/a:name", namespaces=ns)))
        out[-1]["title"] = title
    return out


def x(row, since=None):
    out, cursor = [], None
    for _ in range(MAX_PAGES):
        params = {"screen_name": (row.get("handle") or "").lstrip("@")} if not row.get("id") else {"rest_id": row["id"]}
        if cursor:
            params["cursor"] = cursor
        d = tik("/api/v1/twitter/web/fetch_user_post_tweet", params, price=PRICE["x"]) or {}
        page = x_posts(d, row)
        out += page
        cursor = d.get("next_cursor")
        if not cursor or not page or not since or min(p["published"] or "" for p in page) <= since:
            break
    return out


def x_posts(d, row):
    """one page of an account's tweets (tikhub_fetch.x_tweet: text with links expanded, the X Article's full text,
    the quoted tweet, photos, videos); pure retweets are someone else's post and are skipped (quotes are kept)"""
    sn = ((d.get("user") or {}).get("profile") or (row.get("handle") or "").lstrip("@"))
    out = []
    for t in d.get("timeline") or []:
        tid, text = t.get("tweet_id") or t.get("id"), t.get("text") or ""
        if not tid or (text.startswith("RT @") and not t.get("quoted")):
            continue
        x = th.x_tweet(t, full_article=True)
        if x["retweet"]:
            continue
        out.append(post(f"https://x.com/{sn}/status/{tid}", x["text"], x["published"], x["engagement"], str(tid),
                        images=x["images"], clips=x["clips"]))
        out[-1]["title"] = x["title"]  # an X Article's own title; else the text's start without links
    return out


def tiktok(row, since=None):
    out, cursor = [], 0
    for _ in range(MAX_PAGES):
        d = tik("/api/v1/tiktok/app/v3/fetch_user_post_videos",
                {"unique_id": (row.get("handle") or "").lstrip("@"), "count": "20", "max_cursor": str(cursor)},
                price=PRICE["tiktok"]) or {}
        page = tiktok_posts(d, row)
        out += page
        cursor = d.get("max_cursor")
        fresh = [p for p in page if not p["pinned"]]
        if not d.get("has_more") or not cursor or not fresh or not since or min(p["published"] or "" for p in fresh) <= since:
            break
    return out


def tiktok_posts(d, row):
    out = []
    for a in d.get("aweme_list") or []:
        h = (a.get("author") or {}).get("unique_id") or (row.get("handle") or "").lstrip("@")
        url = f"https://www.tiktok.com/@{h}/video/{a['aweme_id']}"
        out.append(post(url, a.get("desc"), iso(a.get("create_time")),
                        th.counts(a, views=["statistics.play_count"], likes=["statistics.digg_count"],
                                  comments=["statistics.comment_count"], shares=["statistics.share_count"]),
                        str(a["aweme_id"]), pinned=bool(a.get("is_top")), clips=th.tiktok_clip(a, url)))
    return out


def dedupe(posts):
    seen, out = set(), []
    for p in posts:
        if p["url"] not in seen:
            seen.add(p["url"])
            out.append(p)
    return out


READ = {"youtube": youtube, "x": x, "tiktok": tiktok}


def every_min(row, st, now_ts):
    if row["platform"] == "youtube":
        return YOUTUBE_MIN
    return EVERY_RUN[row["platform"]]


def run():
    import corpus
    with corpus.lock("watch", wait=False) as got:
        if not got:
            print("watch: another run holds the lock")
            return
        _run()


def own_youtube():
    """channel ids sources.py already reads (each channel's uploads playlist UU… every poll): left to it"""
    import sources
    return {"UC" + m.group(1) for s in sources.SOURCES for m in [re.search(r"(?:/channel/UC|[?&]list=UU)([\w-]{22})", s[2])] if m}


def _run():
    import corpus
    t0, t = time.time(), now()
    now_ts = int(t.timestamp())
    deadline = t0 + RUN_SECONDS
    rows = [r for r in watchlist.load() if r["platform"] in READ and not r.get("inactive")]
    prev = corpus.file_get("watch.json", {"accounts": {}, "items": []})
    paused = watchlist.paused()
    skip_yt = own_youtube()
    state, items, stopped, answered = {}, [], {}, set()
    keep_from = (t - timedelta(hours=KEEP_HOURS)).isoformat(timespec="seconds")
    old_items = {}
    for it in prev.get("items") or []:
        if (it.get("found") or "") >= keep_from:
            old_items.setdefault(it["account"], []).append(it)
    rows.sort(key=lambda r: (prev["accounts"].get(watchlist.key(r)) or {}).get("checked") or "")
    for r in rows:
        k = watchlist.key(r)
        st = dict(prev["accounts"].get(k) or {}, name=r["name"], platform=r["platform"], group=r["group"])
        mine = {i["url"]: i for i in old_items.get(k, [])}  # this account's posts still kept in the file
        if r["platform"] == "youtube" and r["id"] in skip_yt:
            st.update(ok=True, off="read by sources.py")
        elif paused:  # YouTube's feed is free, but its videos are transcribed: it waits too
            st.update(off="safety stop (watchlist.paused)", off_by="safety stop")
        elif "tikhub" in stopped and r["platform"] != "youtube":
            st.pop("off_by", None)  # out of balance is a failure, not a choice
            st.update(off="TikHub out of balance: requests failing")
        elif time.time() > deadline or (st.get("checked") and now() - datetime.fromisoformat(st["checked"])
                                        < timedelta(minutes=every_min(r, st, now_ts) - 3)):
            pass  # not due (or out of time this run): keep its state
        else:
            st.pop("off", None)
            st.pop("off_by", None)
            since = st.get("newest")
            try:
                got = READ[r["platform"]](r) if r["platform"] == "youtube" else READ[r["platform"]](r, since)
                if r["platform"] != "youtube":
                    answered.add("tikhub")
                stamp = t.isoformat(timespec="seconds")
                # back OVERLAP_H before the newest post seen: a post that shows up late (behind a newer one, or with
                # the same second) is still filed; the ones already in the file are known by URL
                floor = (datetime.fromisoformat(since) - timedelta(hours=OVERLAP_H)).isoformat(timespec="seconds") \
                    if since else (t - timedelta(hours=FIRST_HOURS)).isoformat(timespec="seconds")
                new = []
                for g in got:
                    if g["url"] in mine:  # seen before: its counts and text refresh
                        mine[g["url"]].update(engagement=g["engagement"] or mine[g["url"]].get("engagement"),
                                              text=g["text"][:20000] or mine[g["url"]].get("text"))
                    elif g["published"] and g["published"] > floor:
                        new.append(g)
                        mine[g["url"]] = {
                            "account": k, "platform": r["platform"], "author": r["name"],
                            "source": f"{r['name']} ({watchlist.PLATFORM_NAMES[r['platform']]})",
                            "title": (g.get("title") or g["text"])[:160] or "(post without text)", "url": g["url"],
                            "published": g["published"], "text": g["text"][:20000], "engagement": g["engagement"],
                            "found": stamp, **{k: g[k] for k in ("images", "clips") if g.get(k)}}
                ts = [int(datetime.fromisoformat(g["published"]).timestamp()) for g in new]
                st.update(ok=True, error=None, checked=stamp, new=len(new),
                          newest=max([since or ""] + [g["published"] for g in new]) or None,
                          recent=sorted({x for x in st.get("recent", []) + ts if x > now_ts - 3 * 86400}))
            except Exception as e:  # noqa: BLE001: one account's failure never stops the others
                err = f"{type(e).__name__}: {str(e)[:240]}"
                if r["platform"] != "youtube" and "HTTP 402" in err:
                    stopped["tikhub"] = err
                st.update(ok=False, error=err, checked=t.isoformat(timespec="seconds"))
        items += list(mine.values())
        state[k] = st
    if stopped or answered:
        th.limit_update(stopped, answered, t.isoformat(timespec="seconds"))
    ok = sum(1 for v in state.values() if v.get("ok"))
    seen = set()
    items = [i for i in items if not (i["url"] in seen or seen.add(i["url"]))]
    out = {"fetched": now().isoformat(timespec="seconds"), "ok": ok > 0, "error": None if ok else "no account answered",
           "paused": corpus.kv_get("watch_paused") if paused else None, "accounts": state,
           "items": items, "spend": watchlist.spend(), "seconds": round(time.time() - t0)}
    corpus.file_put("watch.json", out)
    checked = sum(1 for v in state.values() if v.get("checked") == t.isoformat(timespec="seconds"))
    print(f"watch: {checked} accounts checked, {ok}/{len(state)} ok, {len(items)} posts in the file, "
          f"spend today {out['spend']}" + (" PAUSED (safety stop)" if paused else ""))


def main(argv):
    if argv[:1] == ["run"]:
        run()
        return 0
    if argv[:1] == ["test"] and len(argv) == 3:
        plat, ref = argv[1], argv[2]
        row = {"platform": plat, "handle": ref, "id": ref if plat not in ("x", "tiktok") else None, "name": ref}
        for g in READ[plat](row) if plat == "youtube" else READ[plat](row, None):
            print(g["published"], g["url"], json.dumps(g["engagement"]), g["text"][:80].replace("\n", " "))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
