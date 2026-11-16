#!/usr/bin/env python3
"""Facebook pages for the YYC stream: Facebook's own data request for each page's timeline, sent directly.

How it works (measured 2026-10-12/13):
- When you scroll a page on facebook.com, the web app asks /api/graphql/ (ProfileCometTimelineFeedRefetchQuery) for more
  posts. This script sends that request itself with afterTime = the newest post already seen, and pages back with the
  answer's cursor (3 posts an answer) until Facebook says there are no more, so EVERY post of a followed page comes in,
  however busy the page. A page with nothing new answers ~0.3 KB (the request itself is ~5 KB, ~11 KB with headers);
  a new post costs ~20-30 KB, versus ~3 MB for a browser visit (Facebook's app code).
- Logged out, Facebook answers only residential IPs, so everything goes through RESI_PROXY (YYC's one IPRoyal
  sub-user `yycstream`, Hong Kong exits; resi.py); its bytes are counted under the site "facebook" for the monitor.
- The request needs the logged-out cookies and form tokens of a real browser session, captured on the SAME proxy
  (tokens from another IP are refused): `session`, only when Facebook refuses the one held (a browser capture is
  ~3 MB of Facebook's app code; Deyao, 2026-10-20). An empty timeline is a normal answer now, so each run first reads
  the busiest page's newest post without afterTime: no post there = refused, renew. During the run, three error
  answers renew it too, but a page that answers its Page node alone (no timeline: 9 of 223 pages logged out, 20 Oct;
  NoTimeline) is that page's error and never counts: those pages renewed the session on nearly every run (~12 MB/h).
  A session is kept however old while it is answered.
  "Rate limit exceeded" answers are retried on a new connection (another residential IP).
- It addresses pages by their internal profile id (文匯報 = 100063560007212, not the 193439447353622 in its URLs):
  `resolve` reads each new page's id once from its document (~230 KB) and stores it in the document fb-ids.json
  (a row's "id" in watchlist.json is used as is).
Rejected: Apify for polling (it bills every page per run even with no new post: USD 0.007, ~USD 120/month for 12 pages);
the IPRoyal ISP IP (Facebook refuses it); a smaller form (without __dyn/__csr the answers grow 2-3x).

    python3 fb_fetch.py poll         (CronJob facebook, every 15 min, under xvfb-run for the occasional session refresh)
    python3 fb_fetch.py backfill H   read every page back H hours once (posts already in the stream are skipped there)
    python3 fb_fetch.py session      capture a fresh session now (browser)
    python3 fb_fetch.py resolve      fill in missing profile ids (poll also runs it for new pages)
facebook.json: items = the posts of the last KEEP_H hours ("at": the run that found them); state[profile id] = {newest_ts, checked, gaps, new, ids (post ids read, last 3 days), deep (day of the last deep pass)}; wire = curl's byte
counts of the run (request + response headers + gzip body).
"""
import json, os, re, sys, time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode

import chrome_state
import corpus
import nomedia
import resi
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError, Timeout

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(HERE, "data"))
# documents in Postgres (corpus.file_get/file_put): fb-ids.json = profile ids resolved by `resolve` ({url: id or
# {"error": …}}), fb-session.json = the captured logged-out session, facebook.json = this job's output
HKT = timezone(timedelta(hours=8))
QUERY = "ProfileCometTimelineFeedRefetchQuery"
SEED = "https://www.facebook.com/wenweipo"  # busy and public: scrolling it always triggers the data request
FIRST_HOURS = 72   # a page seen for the first time is read back this far
MAX_REQ = 10       # timeline answers per page and run (3 posts each); more new posts than that wait as a gap
# a page that posted within the last 24 h is checked every ACTIVE_MIN minutes, the others every QUIET_MIN (Deyao,
# 2026-10-29: IPRoyal bandwidth; each check is a ~11 KB request even when nothing is new); the CronJob runs every 15 min
# and reads the pages that are due (every_minutes). Facebook files a post under its creation time,
# which can be older than a post already read (a video that finished processing, a scheduled post: 陳宗彝's 12:16 and
# 14:01 posts of 14 Oct appeared after his 12:40 one), so once an hour (OVERLAP_EVERY) a check reads back OVERLAP_H hours before the newest
# post read, and once a day (the first run of each HKT day per page) DEEP_H hours; a post id already read is never filed
# again (its pfbid permalink changes from read to read)
OVERLAP_H = 3
OVERLAP_EVERY = 60  # minutes: the overlap read is the bulk of the traffic (10 MB a run, 172 answers, measured 15 Oct), so
#                     one run an hour per page reads it; the runs between read from the newest post on
DEEP_H = 26
ACTIVE_MIN = 30
QUIET_MIN = 180
KEEP_H = 6         # facebook.json keeps the posts of the last KEEP_H hours (a poller that misses one run still gets them)
RUN_SECONDS = int(os.environ.get("FB_RUN_SECONDS", 300))  # pages not reached are read on the next run (the unit allows 400 s)


def every_minutes(recent, now_ts):
    """minutes between checks of a page from its posts' times (epoch seconds): ACTIVE_MIN when one is within 24 h"""
    return ACTIVE_MIN if any(t > now_ts - 86400 for t in recent or ()) else QUIET_MIN


def due(st, now_ts):
    """is the page's check due this run? (never checked, or its interval has passed; 2 minutes' slack for the
    CronJob's start times)"""
    if not st.get("checked"):
        return True
    age = now_ts - datetime.fromisoformat(st["checked"]).timestamp()
    return age >= (every_minutes(st.get("recent"), now_ts) - 2) * 60


def capture_session():
    """a browser on RESI_PROXY scrolls one page until the app asks for more posts; keep that request's form + cookies"""
    from patchright.sync_api import sync_playwright
    proxy = resi.playwright("hk")
    cap = {}
    with sync_playwright() as p:
        ctx = chrome_state.launch(p, "fb-profile", channel="chrome", headless=False, no_viewport=True, proxy=proxy,
                                  locale="zh-HK")
        counted = resi.count_browser(ctx, "facebook")
        try:
            tab = ctx.pages[0] if ctx.pages else ctx.new_page()

            def on_request(r):
                if "graphql" in r.url and r.method == "POST" and QUERY in (r.post_data or "") and "post" not in cap:
                    cap["post"], cap["headers"] = r.post_data, dict(r.headers)
            tab.on("request", on_request)
            try:
                tab.goto(SEED, wait_until="domcontentloaded", timeout=90000)
            except Exception as e:
                resi.check_browser(e)  # IPRoyal out of traffic → resi.ProxyOut (a pause), else as before
                raise
            time.sleep(6)
            for _ in range(10):
                if "post" in cap:
                    break
                tab.keyboard.press("Escape")
                tab.mouse.move(700, 450)
                tab.mouse.wheel(0, 2500)
                time.sleep(2.5)
            cap["cookies"] = {c["name"]: c["value"] for c in ctx.cookies() if "facebook.com" in c["domain"]}
            cap["ua"] = tab.evaluate("navigator.userAgent")
        finally:
            counted()
            chrome_state.close(ctx)
    if "post" not in cap:
        raise RuntimeError("the page never asked for more posts (login wall on this IP?)")
    cap["captured"] = datetime.now(HKT).isoformat(timespec="seconds")
    corpus.file_put("fb-session.json", cap)
    return cap


def load_pages():
    """the watched Facebook pages (watchlist.json) with their profile ids (the row's id, else resolved into
    fb-ids.json); new: a page added on or after watchlist.WATCH_START (its traffic counts for the daily safety stop)"""
    import watchlist
    ids = corpus.file_get("fb-ids.json", {})
    pages = []
    for p in watchlist.accounts("facebook"):
        if p.get("inactive"):
            continue
        got = p.get("id") or ids.get(p["url"])
        pages.append(dict(p, profile_id=got if isinstance(got, str) else None, new=watchlist.is_new(p),
                          resolve_error=got.get("error") if isinstance(got, dict) else None))
    return pages, ids


def resolve():
    """profile ids for pages without one: the page's own document (one plain request on RESI_PROXY with the session's
    cookies, ~230 KB) names it as "userID"/"pageID" """
    pages, ids = load_pages()
    todo = [p for p in pages if not p["profile_id"] and not p["resolve_error"]]
    if not todo:
        return
    try:
        sess = corpus.file_get("fb-session.json")
    except LookupError:
        sess = capture_session()
    http = resi.session()
    for p in todo:
        try:
            r = http.get(p["url"], cookies=sess["cookies"], proxies={"https": resi.url("hk")}, timeout=40,
                         headers={"User-Agent": sess["ua"], "Accept": "text/html", "Accept-Language": "zh-HK",
                                  "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
            found = re.findall(r'"userID":"(\d{6,})"', r.text) + re.findall(r'"pageID":"(\d{6,})"', r.text)
            ids[p["url"]] = found[0] if found else {
                "error": "no id in the page (" + ("login wall" if "/login" in str(r.url) else f"HTTP {r.status_code}") + ")"}
        except resi.ProxyOut:
            raise  # a pause, not an unresolvable page
        except Exception as e:
            ids[p["url"]] = {"error": repr(e)[:120]}
    corpus.file_put("fb-ids.json", ids)
    print(f"resolved {sum(1 for p in todo if isinstance(ids.get(p['url']), str))}/{len(todo)} pages")


def posts(body):
    """the answer is one JSON document per line; each story carries creation_time, its permalink
    (comet_sections.timestamp.story.url) and its text (the first message with "text")"""
    out = []
    for line in body.decode("utf-8", "replace").split("\n"):
        try:
            d = json.loads(line) if line.strip() else None
        except ValueError:
            d = None
        node = ((d or {}).get("data") or {}).get("node") or {}
        nodes = [e.get("node") or {} for e in ((node.get("timeline_list_feed_units") or {}).get("edges") or [])] or [node]
        for n in nodes:
            if "creation_time" not in n:
                continue
            ts = ((n.get("comet_sections") or {}).get("timestamp") or {}).get("story") or {}
            texts = re.findall(r'"message": ?\{"text": ?"((?:[^"\\]|\\.)*)"', json.dumps(n.get("comet_sections") or {}, ensure_ascii=False))
            text = json.loads('"' + texts[0] + '"') if texts else ""
            images, clips = media(n)
            text = " ".join(text.split())
            link = shared_link(n)
            if link and link.split("?")[0] not in text:
                text = (text + "\n" + link).strip()  # links.py reads a short caption's article; link_key groups it
            out.append({"url": ts.get("url") or f"https://www.facebook.com/{n.get('post_id')}", "text": text,
                        "images": images, "clips": clips,
                        "post_id": n.get("post_id"),  # comments_fetch.py names the post's comments by it
                        "ts": n["creation_time"],
                        "published": datetime.fromtimestamp(n["creation_time"], HKT).isoformat(timespec="minutes"),
                        "engagement": engagement(n)})
    return out


def shared_link(n):
    """a link share's article URL: content.story.attachments[…].target.external_url (the post's text often lacks it:
    am730's shares are a caption, the link only in the attachment) → str or None"""
    story = ((n.get("comet_sections") or {}).get("content") or {}).get("story") or {}
    m = re.search(r'"external_url": ?"((?:[^"\\]|\\.)*)"', json.dumps(story.get("attachments") or [], ensure_ascii=False))
    if not m:
        return None
    u = json.loads('"' + m.group(1) + '"')
    return u if re.match(r"https?://", u) and "facebook.com" not in u else None


_PHOTO_KEYS = ("photo_image", "viewer_image", "image", "large_share_image", "flexible_height_share_image")
_SKIP = ("feedback", "owner", "action_links", "comet_footer_renderer", "video_player_scrubber_preview_renderer", "actors",
         "context_layout", "comet_sections")


def media(n):
    """(photo URLs, video clips) of a story (Deyao, 2026-10-15: photo and video posts with a few words of caption were
    scored on those words alone): from the story's attachments (content.story.attachments and a shared post's
    attached_story): a photo post's photo, every photo of an album (all_subattachments), a link share's preview
    image, a video's thumbnail and the video itself as a clip (fb:<video id>, its reel/permalink URL, kind ytdlp:
    media.py reads it with yt-dlp from the server, the proxy only as a fallback). Never comments, profile pictures or the page's own
    photo: only what sits under the story's attachments."""
    story = ((n.get("comet_sections") or {}).get("content") or {}).get("story") or {}
    images, clips, seen = [], [], set()

    def pic(o):
        for k in _PHOTO_KEYS:
            u = (o.get(k) or {}).get("uri") if isinstance(o.get(k), dict) else None
            if u:
                return u
        return None

    def add_image(u):
        if u and u.split("?")[0] not in seen:
            seen.add(u.split("?")[0])
            images.append(u)

    def visit(o, inside):
        if isinstance(o, list):
            for x in o:
                visit(x, inside)
            return
        if not isinstance(o, dict):
            return
        t = o.get("__typename")
        if inside and t == "Video" and (o.get("videoId") or o.get("id")):
            vid = str(o.get("videoId") or o["id"])
            url = o.get("permalink_url") or o.get("url")
            have = next((c for c in clips if c["key"] == "fb:" + vid), None)
            if not have:  # the same video sits there several times, not all with its link (attachments[].media: id only)
                clips.append({"key": "fb:" + vid, "url": url or f"https://www.facebook.com/watch/?v={vid}", "kind": "ytdlp"})
            elif url:
                have["url"] = url
            thumb = (o.get("preferred_thumbnail") or {}).get("image") or o.get("thumbnailImage") or o.get("image") or {}
            add_image(thumb.get("uri") if isinstance(thumb, dict) else None)
            return
        if inside and t in ("Photo", "GenericAttachmentMedia"):
            add_image(pic(o))
            return
        for k, v in o.items():
            if k in _SKIP:
                continue
            visit(v, inside or k in ("attachments", "attached_story"))

    visit({k: v for k, v in story.items() if k in ("attachments", "attached_story")}, False)
    return images, clips


def engagement(n):
    """the story's like/comment/share/view counts: comet_ufi_summary_and_actions_renderer.feedback (reaction_count.count,
    comment_rendering_instance.comments.total_count, share_count.count, video_view_count) — only the ones it gives"""
    stack = [n]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if isinstance(o.get("comet_ufi_summary_and_actions_renderer"), dict):
                f = o["comet_ufi_summary_and_actions_renderer"].get("feedback") or {}
                got = {"likes": (f.get("reaction_count") or {}).get("count"),
                       "comments": ((f.get("comment_rendering_instance") or {}).get("comments") or {}).get("total_count"),
                       "shares": (f.get("share_count") or {}).get("count"), "views": f.get("video_view_count")}
                return {k: v for k, v in got.items() if isinstance(v, int)}
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return {}


def page_info(body):
    """(end_cursor, has_next_page) of the timeline itself: the "…timeline_list_feed_units$page_info" line of the answer
    (stories carry page_infos of their own, for their comments)"""
    for line in body.decode("utf-8", "replace").split("\n"):
        if "page_info" not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        pi = (d.get("data") or {}).get("page_info") if str(d.get("label", "")).endswith("timeline_list_feed_units$page_info") else \
            (((d.get("data") or {}).get("node") or {}).get("timeline_list_feed_units") or {}).get("page_info")
        if isinstance(pi, dict):
            return pi.get("end_cursor"), bool(pi.get("has_next_page"))
    return None, False


class NoTimeline(RuntimeError):
    """the page answered its Page node alone: no public timeline logged out (that page's error, not the session's)"""


def answered(body):
    """True when Facebook answered with the page's timeline (possibly empty), False for an error answer (rate limit,
    invalid cursor, stale session: no timeline_list_feed_units object)"""
    try:
        d = json.loads(body.decode("utf-8", "replace").split("\n")[0])
    except ValueError:
        return False
    return isinstance((((d.get("data") or {}).get("node") or {}).get("timeline_list_feed_units")), dict)


def bare_page(body, pid):
    """True when the answer is the Page node alone (data.node = {__typename: Page, id: pid}, nothing else)"""
    try:
        node = (json.loads(body.decode("utf-8", "replace").split("\n")[0]).get("data") or {}).get("node")
    except (ValueError, AttributeError):
        return False
    return isinstance(node, dict) and str(node.get("id")) == str(pid) and set(node) <= {"__typename", "id"}


def corpus_times(source, since_ts):
    """creation times of a page's posts in the corpus since since_ts (to start a page's posting rate)"""
    try:
        rows = corpus.db().execute("SELECT published FROM posts WHERE sid='facebook' AND thread IS NULL AND source=%s AND published >= %s",
                                   (source, datetime.fromtimestamp(since_ts, HKT).isoformat(timespec="minutes"))).fetchall()
        return sorted(int(datetime.fromisoformat(r[0]).timestamp()) for r in rows if r[0])
    except Exception:
        return []


def corpus_ids(source, since_ts):
    """post ids of a page's posts in the corpus since since_ts (fbpid:<url> in kv, written by _poll as it files them)"""
    try:
        rows = corpus.db().execute("SELECT k.v FROM posts p JOIN kv k ON k.k = 'fbpid:' || p.url WHERE p.sid='facebook' AND "
                                   "p.thread IS NULL AND p.source=%s AND p.published >= %s",
                                   (source, datetime.fromtimestamp(since_ts, HKT).isoformat(timespec="minutes"))).fetchall()
        return {str(r[0]): since_ts for r in rows if r[0] and r[0] != "-"}
    except Exception:
        return {}


def poll(since_hours=None):
    """_poll; IPRoyal out of traffic (resi.ProxyOut, at any request) is a pause: facebook.json keeps every page's
    place and the last posts as they were (nothing advances), says off 'proxy_out', and the next run reads on"""
    try:
        return _poll(since_hours)
    except resi.ProxyOut as e:
        d = corpus.file_get("facebook.json", {})
        d.update(fetched=datetime.now(HKT).isoformat(timespec="seconds"), ok=False, off=resi.STATUS, error=repr(e)[:200])
        d.setdefault("items", [])
        d.setdefault("state", {})
        corpus.file_put("facebook.json", d)
        print("facebook: proxy out of traffic, paused:", repr(e)[:160], flush=True)


def _poll(since_hours=None):
    """every post of each followed page since the last one seen: the timeline request with afterTime = the newest post's
    time, paged back with its cursor (3 posts an answer) until Facebook says there are no more. A page with nothing new
    answers ~0.3 KB (plus the ~5 KB request); a page is checked when due (every_minutes); each new post costs
    ~20 KB. A page seen for the first time (or `backfill H`) is read back FIRST_HOURS (H) hours. A page with more than
    MAX_REQ answers' worth of new posts in one run keeps the rest as a gap (afterTime..beforeTime), read on later runs."""
    if any(not p["profile_id"] and not p["resolve_error"] for p in load_pages()[0]):
        resolve()
    pages, done = [], set()
    for p in load_pages()[0]:  # one check per profile id: a page can be listed under a numeric id and its vanity name
        if p["profile_id"] and p["profile_id"] not in done:
            done.add(p["profile_id"])
            pages.append(p)
    try:
        sess = corpus.file_get("fb-session.json")
    except LookupError:
        sess = capture_session()
    prev_doc = corpus.file_get("facebook.json", {})
    prev, prev_items = prev_doc.get("state", {}), prev_doc.get("items", [])
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    http = [resi.session()]  # counts its bytes under "facebook" (resi.py)
    wire = {"up": 0, "down": 0, "headers": 0, "requests": 0}

    def ask(pid, after=None, before=None, cursor=None, count=3):
        """one timeline answer → (posts, next cursor or None); raises on an error answer"""
        q = {k: v[0] for k, v in parse_qs(sess["post"]).items()}
        v = json.loads(q["variables"])
        v.update(id=pid, cursor=cursor, count=count, afterTime=after, beforeTime=before)
        v = {k: x for k, x in v.items() if x is not None}
        h = {k: x for k, x in sess["headers"].items() if k.lower() in ("content-type", "x-asbd-id", "x-fb-friendly-name", "x-fb-lsd")}
        h.update({"User-Agent": sess["ua"], "Origin": "https://www.facebook.com", "Referer": "https://www.facebook.com/",
                  "Accept": "*/*", "Accept-Encoding": "gzip", "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"})
        data = urlencode(dict(q, variables=json.dumps(v, separators=(",", ":"))))
        for attempt in range(3):
            try:
                r = http[0].post("https://www.facebook.com/api/graphql/", headers=h, cookies=sess["cookies"], timeout=40,
                                 data=data, proxies={"https": resi.url("hk")})
            except (Timeout, CurlConnectionError):
                # one residential exit that never answers (2026-11-07 15:15 UTC: the run's first request timed out
                # after 40 s and the run failed): a new connection = another residential IP
                if attempt == 2:
                    raise
                http[0] = resi.session()
                continue
            wire["requests"] += 1
            wire["up"] += r.request_size or 0
            wire["down"] += r.download_size or 0
            wire["headers"] += r.header_size or 0
            if b"Rate limit exceeded" in r.content[:300] and attempt < 2:
                http[0] = resi.session()  # a new connection = another residential IP
                continue
            break
        if not answered(r.content):
            raise (NoTimeline if bare_page(r.content, pid) else RuntimeError)(
                "no timeline in the answer: " + r.content[:160].decode("utf-8", "replace"))
        cur, more = page_info(r.content)
        return posts(r.content), (cur if more else None)

    def read(pid, after, before=None):
        """posts in (after, before), newest first, at most MAX_REQ answers → (posts, oldest time read if cut short)"""
        got, cursor = [], None
        for _ in range(MAX_REQ):
            page, cursor = ask(pid, after, before, cursor)
            got += page
            if not cursor or not page:
                return got, None
        return got, min(g["ts"] for g in got)

    now_ts = int(time.time())
    deadline = time.time() + RUN_SECONDS
    # pages checked longest ago first, so a run cut short by the deadline is followed by the ones it skipped
    pages.sort(key=lambda pg: (prev.get(pg["profile_id"]) or {}).get("checked") or "")
    items, state, refreshed, failed = [], {}, False, 0
    # a page with nothing new answers an empty timeline, as a stale session might: so first the busiest page's newest
    # post without afterTime (~35 KB); no post there = renew the session
    busiest = max(pages, key=lambda pg: (prev.get(pg["profile_id"]) or {}).get("newest") or "")
    try:
        alive = bool(ask(busiest["profile_id"], count=1)[0])
    except RuntimeError:
        alive = False
    if not alive:
        sess, refreshed = capture_session(), True
    import watchlist
    paused = watchlist.paused() if any(pg["new"] for pg in pages) else False
    for pg in pages:
        pid = pg["profile_id"]
        st = dict(prev.get(pid) or {}, name=pg["name"])
        if pg["new"] and paused:  # the watching's daily safety stop (watchlist.py): new pages wait for the next day
            state[pid] = dict(st, off="safety stop")
            continue
        before = wire["up"] + wire["down"] + wire["headers"]
        if "recent" not in st:  # the first run with this field: the page's posts of the last 3 days from the corpus
            st["recent"] = corpus_times(pg["name"], now_ts - 3 * 86400)
        if "since" not in st and not (st.get("newest_ts") or st.get("newest")):
            st["since"] = now_ts  # the page's first read: posts published before now are its backlog (first_run)
        if "ids" not in st:  # post ids already filed (last 3 days), so a re-read never files a post twice
            st["ids"] = corpus_ids(pg["name"], now_ts - 3 * 86400)
        if time.time() > deadline or not (since_hours or due(st, now_ts)):
            state[pid] = st
            continue
        newest_ts = st.get("newest_ts") or (int(datetime.fromisoformat(st["newest"]).timestamp()) if st.get("newest") else None)
        overlap = now_ts - (st.get("overlap") or 0) >= OVERLAP_EVERY * 60 - 300
        after = (newest_ts - OVERLAP_H * 3600 if overlap else newest_ts + 1) if newest_ts else now_ts - FIRST_HOURS * 3600
        deep = st.get("deep") != today
        if deep:
            after = min(after, now_ts - DEEP_H * 3600)
        if since_hours:
            after = min(after, now_ts - int(since_hours * 3600))
        gaps = [g for g in st.get("gaps", []) if g[0] < g[1]]
        try:
            try:
                got, cut = read(pid, after)
            except NoTimeline:
                raise  # this page's own answer: the session is fine
            except RuntimeError:
                failed += 1
                if failed >= 3 and not refreshed:  # several pages answering errors = the session went stale
                    sess, refreshed = capture_session(), True
                    got, cut = read(pid, after)
                else:
                    raise
            if cut:  # more new posts than one run reads: the older part waits as a gap
                gaps.append([after, cut])
            if gaps and not cut:  # one gap per run, oldest last
                g = gaps.pop()
                more, gcut = read(pid, g[0], g[1])
                got += more
                if gcut:
                    gaps.append([g[0], gcut])
            merged = []  # overlapping windows (a backfill over an older gap) are read once
            for g in sorted(gaps):
                if merged and g[0] <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], g[1])
                else:
                    merged.append(list(g))
            gaps = merged
            if got:
                newest_ts = max([newest_ts or 0] + [g["ts"] for g in got])
            ids = {k: v for k, v in (st.get("ids") or {}).items() if v > now_ts - 3 * 86400}
            fresh, dup = [], set()
            for g in got:
                key = str(g["post_id"] or g["url"])
                if key not in ids and key not in dup:
                    dup.add(key)
                    fresh.append(g)
            ids.update({str(g["post_id"] or g["url"]): g["ts"] for g in fresh})
            for g in fresh:  # the comment reads at capture name the post by it (comments_fetch.FB.post_id)
                if g["post_id"]:
                    corpus.kv_set("fbpid:" + g["url"], str(g["post_id"]))
            st.update(ok=True, checked=datetime.now(HKT).isoformat(timespec="seconds"), newest_ts=newest_ts,
                      newest=datetime.fromtimestamp(newest_ts, HKT).isoformat(timespec="minutes") if newest_ts else None,
                      gaps=gaps, new=len(fresh), ids=ids, deep=today if deep else st.get("deep"),
                      overlap=now_ts if overlap or deep else st.get("overlap"),
                      recent=sorted({t for t in st.get("recent", []) + [g["ts"] for g in fresh] if t > now_ts - 3 * 86400}))
            st.pop("error", None)
            items += [{"source": pg["name"], "title": g["text"][:160] or "(post without text)", "url": g["url"],
                       "published": g["published"], "text": g["text"], "engagement": g["engagement"],
                       "post_id": g["post_id"], "at": now_ts,
                       **({"first_run": True} if st.get("since") and g["ts"] < st["since"] else {}),
                       **{k: g[k] for k in ("images", "clips") if g.get(k)}} for g in fresh]
        except resi.ProxyOut:
            raise  # the whole run pauses (poll): no page's place moves
        except Exception as e:  # checked too: a page that errors (NoTimeline) waits its interval like the others
            st.update(ok=False, error=repr(e)[:160], checked=datetime.now(HKT).isoformat(timespec="seconds"))
        if pg["new"]:
            watchlist.add_proxy_bytes(wire["up"] + wire["down"] + wire["headers"] - before)
        st.pop("off", None)
        state[pid] = st
    ok = sum(1 for v in state.values() if v.get("ok"))
    seen = set()
    new_n = len(items)
    kept = [it for it in prev_items if (it.get("at") or 0) > now_ts - KEEP_H * 3600]
    items = [it for it in items + kept if not (it["url"] in seen or seen.add(it["url"]))]
    out = {"fetched": datetime.now(HKT).isoformat(timespec="seconds"), "ok": ok > 0,
           "error": None if ok else "no page answered", "state": state, "items": items, "wire": wire}
    corpus.file_put("facebook.json", out)
    kb = (wire["up"] + wire["down"] + wire["headers"]) / 1024
    print(f"facebook: {ok}/{len(pages)} pages answered, {new_n} new posts, {wire['requests']} requests, "
          f"{kb:.0f} KB on the wire" + (", session refreshed" if refreshed else ""))


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    if sys.argv[1] == "backfill":  # read every page back this many hours (once, e.g. after adding pages)
        poll(since_hours=float(sys.argv[2]))
    else:
        {"poll": poll, "session": capture_session, "resolve": resolve}[sys.argv[1]]()
