#!/usr/bin/env python3
"""The comment readers of social and video posts, called by social_twopass.py (a poller thread) for the two-pass
scoring only (twopass.py): once at capture and once for the second scoring at published + 30 min. Comments are filed
into the corpus as rows of their own (corpus.add_comments), where 搜尋 finds words inside them and the scorer reads them
with their post. Nothing re-checks a post's comments after its second scoring (Deyao, 2026-10-20: the `comments`
CronJob's schedule of re-checks up to 7 days, 2 h / 6 h / 24 h / 48 h apart, is gone; its Facebook reads alone were
~72 MB/h of IPRoyal yycstream), and only comments not yet in the corpus are added.

YouTube (sources.py's channels, sid yt_* / *_youtube): YouTube's own web API (youtubei/v1/next, the request the
  watch page makes): the first answer carries the comment count and the "newest first" token; the comment pages
  follow (20 a page) until a known comment. Sent directly (no proxy: YouTube answers the server's own IP). A comment's
  time is YouTube's relative "3 小時前", so it is exact to the hour for comments seen within a day, and to the day
  for older ones. Logged out, YouTube no longer sends replies to a comment (no reply continuation in the answer,
  checked 2026-10-13 with the WEB and MWEB clients), so only top-level comments come in.
Facebook (the posts fb_fetch.py collects, sid facebook): Facebook's own comment request
  (CommentListComponentsRootQuery, "newest first", 10 a page, then CommentsListComponentsPaginationQuery), sent with
  the logged-out session fb_fetch.py captures (fb-session.json), over RESI_PROXY (IPRoyal sub-user yycstream). The
  request names the post by its feedback id, base64("feedback:<post id>"): fb_fetch.py records each post's id (kv
  fbpid:<url>); for a post without one the id is read from the first ~48 KB of the post page (its canonical link; reels'
  canonical link carries the video id, whose feedback is empty, so reels need the id from fb_fetch). The two
  queries' doc ids and variables are FB_QUERIES (captured 2026-10-13); `capture` re-captures them from a browser
  into the document fb-comment-queries.json when Facebook changes them. Replies to a comment (Depth1CommentsListPagination
  Query, by the comment's feedback id and expansion token, oldest first, 10 a page) are filed under the same thread like
  comments: fetched for each comment read whose reply count grew; when the post's total grew by more than the new
  comments, the "most relevant" first page (the comments with replies) is read too, for replies to older comments.
Instagram + Threads (the posts ig_fetch.py collects, sid instagram): the logged-out post page carries the post's
  comments (Instagram: xig_polaris_media.comments_connection, the first page; Threads: the post's direct_replies only, not the page's relatedPosts),
  over RESI_PROXY (sub-user yycstream). The page shows ~15 comments;
  when the post counts more than the corpus holds, every comment page and every reply comes through TikHub
  (/instagram/v2/fetch_post_comments + fetch_comment_replies, 15 a page, USD 0.002 a call at list price, YYC's key).
Post ids for Facebook: fb_fetch.py records each post's id as kv fbpid:<url> when it files the post.

    python3 comments_fetch.py capture      re-capture Facebook's comment queries (browser)
State: corpus kv "cm:<post url>" = {checked, count, n, r (reply counts read), full_total, full_cursor}.
"""
import base64, json, os, re, sys, time, urllib.error, urllib.request, gzip
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode

import chrome_state
import corpus
import nomedia
import resi
import sources
import watchlist

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = corpus.DATA_DIR
HKT = timezone(timedelta(hours=8))
# comment pages per post and read (YouTube 20 a page, Facebook 10): Facebook reads a thread to its end (a first read cut
# at 30 pages never came back for the older comments: 唐生大地震 506 comments, 314 held)
PAGES_MAX = {"youtube": 30, "facebook": 1000}
TIKHUB = "https://api.tikhub.io"
TIKHUB_PAGES = 200  # Instagram comment pages (15 comments each) per post and read through TikHub


def now():
    return datetime.now(HKT)


def known(url):
    return corpus.db().execute("SELECT 1 FROM posts WHERE url=%s", (url,)).fetchone() is not None


def comment_url(thread_url, cid):
    return re.sub(r"#.*$", "", thread_url) + f"#c{cid}"


def thread_of(p):
    return {k: p[k] for k in ("url", "title", "sid", "source", "section", "published") if p.get(k) is not None}


def rel_time(t):
    """YouTube's '3 小時前' / '1 日前 (已編輯)' / '2 weeks ago' → ISO time"""
    t = str(t or "").replace("日前", "天前")
    d = sources.parse_relative(t)
    return d.isoformat(timespec="minutes") if d else None


# ---------------- YouTube ----------------

YT_UA = sources.UA
YT_CTX = {"client": {"clientName": "WEB", "clientVersion": "2.20261012.01.00", "hl": "zh-HK", "gl": "HK"}}


# YouTube answers some reads of the job's burst (~3,700 posts, 4 threads) 403 and the same video moments later (20 Oct
# 2026: 114 of 3,704): a 403 or 429 is tried again after a pause before it counts as an error
YT_RETRIES, YT_RETRY_WAIT = 2, 5.0


def yt_next(body):
    req = urllib.request.Request("https://www.youtube.com/youtubei/v1/next?prettyPrint=false",
                                 data=json.dumps(dict(body, context=YT_CTX)).encode(),
                                 headers={"User-Agent": YT_UA, "Content-Type": "application/json", "Accept-Encoding": "gzip",
                                          "Cookie": "SOCS=CAI", "Origin": "https://www.youtube.com"})
    for attempt in range(YT_RETRIES + 1):
        try:
            r = urllib.request.urlopen(req, timeout=30)
            break
        except urllib.error.HTTPError as e:
            if e.code not in (403, 429) or attempt == YT_RETRIES:
                raise
            time.sleep(YT_RETRY_WAIT * (attempt + 1))
    d = r.read()
    n = len(d)
    if r.headers.get("Content-Encoding") == "gzip":
        d = gzip.decompress(d)
    return json.loads(d), n


def walk(o, key):
    st = [o]
    while st:
        x = st.pop()
        if isinstance(x, dict):
            if key in x:
                yield x[key]
            st.extend(x.values())
        elif isinstance(x, list):
            st.extend(x)


def yt_comments_page(d):
    """→ ([comment], next token) from a youtubei/next answer for a comment section"""
    order = []
    for t in walk(d, "commentThreadRenderer"):  # the threads in page order
        cid = ((t.get("commentViewModel") or {}).get("commentViewModel") or {}).get("commentId")
        if cid:
            order.append(cid)
    by_id = {}
    for muts in walk(d, "mutations"):
        for m in muts if isinstance(muts, list) else []:
            p = (m.get("payload") or {}).get("commentEntityPayload")
            if not p:
                continue
            pr = p.get("properties") or {}
            if pr.get("replyLevel"):
                continue
            by_id[pr.get("commentId")] = {
                "id": pr.get("commentId"), "author": ((p.get("author") or {}).get("displayName") or "").lstrip("@") or None,
                "text": ((pr.get("content") or {}).get("content") or ""), "published": rel_time(pr.get("publishedTime")),
                "likes": sources.count((p.get("toolbar") or {}).get("likeCountNotliked")) or 0}
    token = None
    for eps in walk(d, "onResponseReceivedEndpoints"):
        for e in eps:
            for v in e.values():
                if isinstance(v, dict) and v.get("continuationItems"):
                    last = v["continuationItems"][-1].get("continuationItemRenderer") or {}
                    cmd = (last.get("continuationEndpoint") or {}).get("continuationCommand") or \
                          ((((last.get("button") or {}).get("buttonRenderer") or {}).get("command") or {}).get("continuationCommand")) or {}
                    token = cmd.get("token") or token
    return [by_id[c] for c in order if c in by_id], token


def youtube_post(p):
    """→ (new comment rows, bytes)"""
    vid = sources.YT_VID.search(p["url"]).group(1)  # watch?v=… or a Short's /shorts/…
    d, nbytes = yt_next({"videoId": vid})
    count, newest = None, None
    for panel in walk(d, "engagementPanelSectionListRenderer"):
        if panel.get("panelIdentifier") != "engagement-panel-comments-section":
            continue
        h = (panel.get("header") or {}).get("engagementPanelTitleHeaderRenderer") or {}
        count = sources.count("".join(r.get("text", "") for r in (h.get("contextualInfo") or {}).get("runs", []))) or 0
        items = ((h.get("menu") or {}).get("sortFilterSubMenuRenderer") or {}).get("subMenuItems") or []
        if len(items) >= 2:  # [熱門, 最新]
            newest = ((items[1].get("serviceEndpoint") or {}).get("continuationCommand") or {}).get("token")
    st = corpus.kv_get("cm:" + p["url"]) or {}
    if count is None or not newest or count == st.get("count"):
        corpus.kv_set("cm:" + p["url"], dict(st, checked=now().isoformat(timespec="seconds"), count=count))
        return 0, nbytes
    got, token = [], newest
    for _ in range(PAGES_MAX["youtube"]):
        d, n = yt_next({"continuation": token})
        nbytes += n
        page, token = yt_comments_page(d)
        fresh = [c for c in page if c["id"] and not known(comment_url(p["url"], c["id"]))]
        got += fresh
        if len(fresh) < len(page) or not token or not page:
            break
    new = corpus.add_comments(thread_of(p), got, total=count) if got else []
    corpus.kv_set("cm:" + p["url"], {"checked": now().isoformat(timespec="seconds"), "count": count,
                                      "n": (st.get("n") or 0) + len(new)})
    return len(new), nbytes


def watched_sources():
    return watchlist.new_sources()


# ---------------- Facebook ----------------

FB_QUERIES = {  # captured from a logged-out browser on 2026-10-13 (capture() refreshes them into fb-comment-queries.json)
    "root": {"name": "CommentListComponentsRootQuery", "doc_id": "38863170746661819",
             "variables": {"commentsIntentToken": "REVERSE_CHRONOLOGICAL_UNFILTERED_INTENT_V1", "feedLocation": "POST_PERMALINK_DIALOG",
                           "feedbackSource": 2, "focusCommentID": None, "scale": 1, "useDefaultActor": False, "id": None,
                           "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "AUTO_TRANSLATE",
                           "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
                           "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
                           "__relay_internal__pv__IsWorkUserrelayprovider": False}},
    "page": {"name": "CommentsListComponentsPaginationQuery", "doc_id": "40120064807592280",
             "variables": {"commentsAfterCount": -1, "commentsAfterCursor": None, "commentsBeforeCount": None, "commentsBeforeCursor": None,
                           "commentsIntentToken": "REVERSE_CHRONOLOGICAL_UNFILTERED_INTENT_V1", "feedLocation": "POST_PERMALINK_DIALOG",
                           "focusCommentID": None, "scale": 1, "targetDialect": None, "useDefaultActor": False, "id": None,
                           "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "AUTO_TRANSLATE",
                           "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
                           "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
                           "__relay_internal__pv__IsWorkUserrelayprovider": False}},
    "replies": {"name": "Depth1CommentsListPaginationQuery", "doc_id": "28879879234997632",
                "variables": {"clientKey": None, "expansionToken": None, "feedLocation": "POST_PERMALINK_DIALOG", "focusCommentID": None,
                              "repliesAfterCount": None, "repliesAfterCursor": None, "repliesBeforeCount": None, "repliesBeforeCursor": None,
                              "scale": 1, "useDefaultActor": False, "id": None,
                              "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "AUTO_TRANSLATE",
                              "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
                              "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
                              "__relay_internal__pv__IsWorkUserrelayprovider": False}},
}
def fb_queries():
    return dict(FB_QUERIES, **corpus.file_get("fb-comment-queries.json", {}))


class FB:
    def __init__(self):
        self.sess = corpus.file_get("fb-session.json")
        self.http = resi.session()  # counts its proxied bytes under "facebook" (resi.py)
        self.q = fb_queries()
        self.bytes = 0

    def gql(self, which, variables):
        qd = self.q[which]
        form = {k: v[0] for k, v in parse_qs(self.sess["post"]).items()}
        form.update(doc_id=qd["doc_id"], fb_api_req_friendly_name=qd["name"], variables=json.dumps(variables, separators=(",", ":")))
        h = {k: x for k, x in self.sess["headers"].items() if k.lower() in ("content-type", "x-asbd-id", "x-fb-lsd")}
        h.update({"x-fb-friendly-name": qd["name"], "User-Agent": self.sess["ua"], "Origin": "https://www.facebook.com",
                  "Referer": "https://www.facebook.com/", "Accept": "*/*", "Accept-Encoding": "gzip", "Sec-Fetch-Site": "same-origin",
                  "Sec-Fetch-Mode": "cors"})
        body = urlencode(form)
        r = self.http.post("https://www.facebook.com/api/graphql/", headers=h, cookies=self.sess["cookies"], timeout=40, data=body,
                           proxies={"https": resi.url("hk")})
        self.bytes += (r.request_size or 0) + (r.download_size or 0) + (r.header_size or 0)  # curl's own counts (gzip body)
        d = json.loads(r.content.decode("utf-8", "replace").split("\n")[0])
        node = (d.get("data") or {}).get("node")
        if not node:
            raise RuntimeError("no data: " + json.dumps(d.get("errors") or d, ensure_ascii=False)[:200])
        return node

    def post_id(self, url):
        """the post id fb_fetch recorded, else the numeric id in the post page's canonical link (first ~48 KB only)"""
        got = corpus.kv_get("fbpid:" + url)
        if got:
            return None if got == "-" else got
        m = re.search(r"/(?:posts|videos)/(\d{8,})|[?&]story_fbid=(\d{8,})", url)
        pid = m and (m.group(1) or m.group(2))
        if not pid and "/reel/" not in url:
            r = self.http.get(url, cookies=self.sess["cookies"], proxies={"https": resi.url("hk")}, timeout=40, stream=True,
                              headers={"User-Agent": self.sess["ua"], "Accept": "text/html", "Accept-Language": "zh-HK",
                                       "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
            buf = b""
            for ch in r.iter_content():
                buf += ch
                if len(buf) > 40000:
                    break
            r.close()
            n = (r.request_size or 0) + (r.header_size or 0) + len(buf) // 5  # body: gzip ~1/5 of the html
            self.bytes += n
            resi.count(n, "facebook")  # a stream=True read: counted here, not by the session
            m = re.search(r'<link rel="canonical" href="[^"]*?/(?:posts|videos)/(?:[^"/]*/)?(\d{8,})/?"', buf.decode("utf-8", "replace"))
            pid = m and m.group(1)
        if pid or "/reel/" not in url:  # "-": the page has no id (login wall): don't read it again
            corpus.kv_set("fbpid:" + url, pid or "-")
        return pid


def fb_comment(n):
    f = n.get("feedback") or {}
    return {"id": n.get("legacy_fbid"), "author": (n.get("author") or {}).get("name"),
            "text": (n.get("body") or {}).get("text") or "",
            "published": datetime.fromtimestamp(n["created_time"], HKT).isoformat(timespec="minutes") if n.get("created_time") else None,
            "likes": sources.count((f.get("reactors") or {}).get("count_reduced")) or 0,
            # for its replies: the comment's own feedback id, the token the replies request needs, the reply count
            "fid": f.get("id"), "tok": (f.get("expansion_info") or {}).get("expansion_token"),
            "replies": (f.get("replies_fields") or {}).get("total_count") or 0}


def fb_parse(node):
    cm = ((node.get("comment_rendering_instance_for_feed_location") or {}).get("comments")) or {}
    out = [fb_comment(e.get("node") or {}) for e in cm.get("edges") or [] if not (e.get("node") or {}).get("depth")]
    pi = cm.get("page_info") or {}
    return out, cm.get("total_count"), (pi.get("end_cursor") if pi.get("has_next_page") else None)


def fb_replies(fb, c, end=float("inf"), over=lambda: False):
    """every reply to comment c (oldest first, 10 a page) → (replies, read to the end); stops at `end` or when
    over() (the read's request cap)"""
    out, cursor = [], None
    for _ in range(PAGES_MAX["facebook"]):
        if time.time() > end or over():
            return out, False
        node = fb.gql("replies", dict(fb.q["replies"]["variables"], id=c["fid"], expansionToken=c["tok"], repliesAfterCursor=cursor))
        rc = node.get("replies_connection") or {}
        out += [fb_comment(e.get("node") or {}) for e in rc.get("edges") or []]
        pi = rc.get("page_info") or {}
        cursor = pi.get("end_cursor") if pi.get("has_next_page") else None
        if not cursor or not rc.get("edges"):
            return out, True
    return out, True


def facebook_post(fb, p, end=float("inf"), pages=None):
    """one Facebook post's comments → new rows; a newly watched page's traffic counts for the daily safety stop.
    The read stops at `end` (a busy thread's full read took 18 min: Skyposthk, 1,774 comments, 20 Oct 2026). With
    `pages` (social_twopass.py's reads for the scoring) it makes at most that many requests, newest comments first,
    and never the full pass over every older page"""
    before = fb.bytes
    try:
        return _facebook_post(fb, p, end, pages)
    finally:
        if p.get("source") in watched_sources():
            watchlist.add_proxy_bytes(fb.bytes - before)


def _facebook_post(fb, p, end=float("inf"), pages=None):
    """newest first back to the last known comment; when the post counts more than we hold, a full pass over every
    comment page. Each page's comments and their new replies are taken before the next page, so a read cut at `end`
    keeps everything before its cursor: the post stays due (not marked checked), the cursor is saved (`full_cursor`)
    and the next check's full pass starts there"""
    pid = fb.post_id(p["url"])
    st = corpus.kv_get("cm:" + p["url"]) or {}
    if not pid:
        corpus.kv_set("cm:" + p["url"], dict(st, checked=now().isoformat(timespec="seconds"), error="no post id"))
        return 0
    fid = base64.b64encode(f"feedback:{pid}".encode()).decode()
    v = dict(fb.q["root"]["variables"], id=fid)
    got, ids, done = [], set(), set()
    nrep = dict(st.get("r") or {})
    cut = [False]
    asked = [0]

    def over():
        """the request cap is reached (counts the request about to be made)"""
        if pages is not None and asked[0] >= pages:
            return True
        asked[0] += 1
        return False

    def fetch(cursor):
        if time.time() > end or over():
            cut[0] = True
            return None
        if cursor is None:
            return fb_parse(fb.gql("root", v))
        return fb_parse(fb.gql("page", dict(fb.q["page"]["variables"], id=fid, commentsAfterCursor=cursor)))

    def take(page):
        """a page's new comments and the replies of its comments whose reply count grew → (whole page taken, how many
        of its comments were new); not whole when cut at `end`"""
        fresh = [c for c in page if c["id"] and c["id"] not in ids and not known(comment_url(p["url"], c["id"]))]
        ids.update(c["id"] for c in fresh)
        got.extend(fresh)
        for c in page:
            if c["id"] in done or not c["replies"] or not c["fid"] or not c["tok"] or c["replies"] <= nrep.get(str(c["id"]), 0):
                continue
            if time.time() > end:
                cut[0] = True
                return False, len(fresh)
            done.add(c["id"])
            try:
                rs, whole = fb_replies(fb, c, end, over)
            except resi.ProxyOut:
                raise  # a pause: the whole post waits (not marked checked)
            except Exception as e:  # a reply page that fails is tried again at the next check
                print("facebook replies", p["url"], c["id"], repr(e)[:160], file=sys.stderr)
                continue
            for r in rs:
                if r["id"] and r["id"] not in ids and not known(comment_url(p["url"], r["id"])):
                    ids.add(r["id"])
                    got.append(r)
            if not whole:  # cut: this comment's replies again at the next check
                cut[0] = True
                return False, len(fresh)
            nrep[str(c["id"])] = c["replies"]
        return True, len(fresh)

    def walk_pages(start, newest):
        """pages from cursor `start` (None = the first page) → the cursor of the first page not wholly taken (where a
        cut read carries on; None when it is the first page or the thread was read to its end) and the post's total.
        newest: stop at the first page holding a known comment (the newest-first check)"""
        resume, total = start, None
        res = fetch(start)
        for _ in range(PAGES_MAX["facebook"]):
            if not res:
                return resume, total
            page, t, cursor = res
            total = t if total is None else total
            whole, nfresh = take(page)
            if not whole:
                return resume, total
            resume = cursor
            if (newest and nfresh < len(page)) or not cursor or not page:
                return None, total
            res = fetch(cursor)
        return None, total

    resume, total = walk_pages(None, True)
    full = False
    if cut[0]:
        resume = st.get("full_cursor") or resume  # a full pass already under way carries on where it stopped
    elif pages is None and total is not None and total > held(p["url"]) + len(got) and total != st.get("full_total"):
        # the post counts more comments and replies than we hold (replies to older comments, a read cut short): read
        # every comment page, so every comment and every comment's reply count is seen (once per new total), from
        # where the last cut read stopped
        full = True
        try:
            resume, _t = walk_pages(st.get("full_cursor"), False)
        except RuntimeError:  # the saved cursor no longer answers: from the first page
            if not st.get("full_cursor"):
                raise
            resume, _t = walk_pages(None, False)
    new = corpus.add_comments(thread_of(p), got, total=total) if (got or total is not None) else []
    out = {"checked": now().isoformat(timespec="seconds"), "count": total if total is not None else st.get("count"), "n": (st.get("n") or 0) + len(new), "r": nrep,
           "full_total": total if (full and not cut[0]) else st.get("full_total")}
    if cut[0]:  # still due: the rest at the next check, from `full_cursor`
        out.update(checked=st.get("checked"), full_cursor=resume, cut=now().isoformat(timespec="seconds"))
        if not out["checked"]:
            del out["checked"]
    corpus.kv_set("cm:" + p["url"], out)
    return len(new)


def held(url):
    """comment and reply rows the corpus holds for a thread"""
    return corpus.db().execute("SELECT count(*) FROM posts WHERE thread=%s", (re.sub(r"#.*$", "", url),)).fetchone()[0]


def capture():
    """re-capture the three comment queries from a logged-out browser on RESI_PROXY: open a busy post, open a comment's
    replies ("查看全部N則回覆", in the default "most relevant" order), then sort the comments newest first and scroll,
    keeping the requests' doc ids and variables → fb-comment-queries.json"""
    from patchright.sync_api import sync_playwright
    row = corpus.db().execute("""SELECT url FROM posts WHERE sid='facebook' AND thread IS NULL AND url LIKE '%%/posts/%%' AND day >= %s
                                 ORDER BY (engagement::jsonb->>'comments')::numeric DESC NULLS LAST LIMIT 1""",
                              ((now() - timedelta(days=2)).strftime("%Y-%m-%d"),)).fetchone()
    proxy = resi.playwright("hk")
    got = {}
    with sync_playwright() as pw:
        ctx = chrome_state.launch(pw, "fb-comments-profile", channel="chrome", headless=False, no_viewport=True,
                                  proxy=proxy, locale="zh-HK")
        counted = resi.count_browser(ctx, "facebook")
        try:
            tab = ctx.pages[0] if ctx.pages else ctx.new_page()

            def on_request(r):
                if "graphql" in r.url and r.method == "POST":
                    q = {k: v[0] for k, v in parse_qs(r.post_data or "").items()}
                    for key, qd in FB_QUERIES.items():
                        if q.get("fb_api_req_friendly_name") == qd["name"] and key not in got:
                            got[key] = {"name": qd["name"], "doc_id": q["doc_id"], "variables": json.loads(q["variables"])}
            tab.on("request", on_request)
            try:
                tab.goto(row["url"], wait_until="domcontentloaded", timeout=90000)
            except Exception as e:
                resi.check_browser(e)  # IPRoyal out of traffic → resi.ProxyOut (a pause), else as before
                raise
            time.sleep(8)
            tab.keyboard.press("Escape")
            tab.mouse.wheel(0, 1500)
            time.sleep(2)
            for _ in range(4):  # a comment's replies (busy posts have some among the most relevant comments)
                link = tab.get_by_text(re.compile(r"查看.*則回覆|View.*repl", re.I))
                if link.count():
                    link.first.click(timeout=8000)
                    time.sleep(4)
                    break
                tab.mouse.move(500, 550)
                tab.mouse.wheel(0, 1200)
                time.sleep(2)
            for txt in ["最相關", "由新到舊"]:
                tab.get_by_text(txt, exact=True).first.click(timeout=8000)
                time.sleep(4)
            for _ in range(4):
                if "page" in got:
                    break
                tab.mouse.move(500, 550)
                tab.mouse.wheel(0, 2500)
                time.sleep(3)
        finally:
            counted()
            chrome_state.close(ctx)
    if set(got) != set(FB_QUERIES):
        raise RuntimeError(f"captured only {sorted(got)}")
    for g in got.values():
        g["variables"] = {k: (None if k in ("id", "commentsAfterCursor", "expansionToken", "repliesAfterCursor") else v)
                          for k, v in g["variables"].items()}
        if "commentsIntentToken" in g["variables"]:  # newest first, whatever order the page was in when it asked
            g["variables"]["commentsIntentToken"] = "REVERSE_CHRONOLOGICAL_UNFILTERED_INTENT_V1"
    corpus.file_put("fb-comment-queries.json", got)
    print("captured", {k: v["doc_id"] for k, v in got.items()})


# ---------------- Instagram + Threads ----------------

def ig_comments(html, post_url):
    """→ (comments, total) from a logged-out Instagram post page or Threads post page"""
    threads = "threads." in post_url
    code = re.search(r"/(?:p|reel|post)/([\w-]+)", post_url).group(1)
    out, total = {}, None
    for blob in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', html, re.S):
        if ("comments_connection" not in blob and '"comment_count"' not in blob) if not threads else ('"taken_at"' not in blob):
            continue
        try:
            st = [json.loads(blob)]
        except ValueError:
            continue
        while st:
            o = st.pop()
            if isinstance(o, dict):
                if not threads and o.get("code") == code and isinstance(o.get("comment_count"), int):
                    total = o["comment_count"]
                if not threads and isinstance(o.get("xig_polaris_media"), dict):
                    m = o["xig_polaris_media"]
                    total = m.get("comment_count") if isinstance(m.get("comment_count"), int) else total
                    for e in ((m.get("comments_connection") or {}).get("edges") or []):
                        n = e.get("node") or {}
                        if n.get("pk") and n.get("text"):
                            out[n["pk"]] = {"id": n["pk"], "author": (n.get("user") or {}).get("username"), "text": n["text"],
                                            "published": datetime.fromtimestamp(n["created_at"], HKT).isoformat(timespec="minutes") if n.get("created_at") else None,
                                            "likes": n.get("comment_like_count") if isinstance(n.get("comment_like_count"), int) else None}
                if threads and isinstance(o.get("taken_at"), int) and o.get("code") == code:
                    tp = o.get("text_post_app_info") or {}
                    total = tp.get("direct_reply_count") if isinstance(tp.get("direct_reply_count"), int) else total
                if threads and isinstance((o.get("text_post_app_info") or {}).get("direct_replies"), (dict, list)):
                    # the replies are only those under direct_replies (in its own script, beside a copy of the post
                    # without its code): the rest of the page is Threads' relatedPosts (other people's posts) and the
                    # author's self_thread
                    rs = [o["text_post_app_info"]["direct_replies"]]
                    while rs:
                        r = rs.pop()
                        if isinstance(r, dict):
                            if (isinstance(r.get("taken_at"), int) and r.get("code") and (r.get("caption") or {}).get("text")
                                    and (r.get("text_post_app_info") or {}).get("is_reply")):
                                out[r["code"]] = {"id": r.get("pk") or r["code"], "author": (r.get("user") or {}).get("username"),
                                                  "text": r["caption"]["text"], "published": datetime.fromtimestamp(r["taken_at"], HKT).isoformat(timespec="minutes"),
                                                  "likes": r.get("like_count") if isinstance(r.get("like_count"), int) else None}
                            rs.extend(r.values())
                        elif isinstance(r, list):
                            rs.extend(r)
                if threads and "relatedPosts" in o:
                    o = {k: v for k, v in o.items() if k != "relatedPosts"}
                st.extend(o.values())
            elif isinstance(o, list):
                st.extend(o)
    return list(out.values()), total


def tikhub(path, params):
    """one TikHub API call (YYC's key, TIKHUB_API) → its "data"; TikHub answers 429 when calls fire together"""
    import costlog
    from curl_cffi import requests
    import tikhub_fetch
    for attempt in range(3):
        tikhub_fetch._pace()  # the Instagram workers share the account's request rate
        r = requests.get(TIKHUB + path, params=params, timeout=90,
                         headers={"Authorization": "Bearer " + os.environ["TIKHUB_API"], "Accept": "application/json"})
        costlog.record("tikhub", path, status=r.status_code, ok=r.status_code == 200)  # the cost profile
        if r.status_code == 200:
            return r.json().get("data") or {}
        time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"TikHub {path} HTTP {r.status_code}: {r.text[:120]}")


def th_comment(c):
    return {"id": str(c.get("id") or c.get("pk") or ""), "text": c.get("text"),
            "author": (c.get("user") or {}).get("username") if isinstance(c.get("user"), dict) else None,
            "published": datetime.fromtimestamp(c["created_at"], HKT).isoformat(timespec="minutes") if c.get("created_at") else None,
            "likes": c.get("comment_like_count") if isinstance(c.get("comment_like_count"), int) else None}


def th_items(d):
    return ((d.get("data") or {}).get("items") or d.get("items") or []) if isinstance(d, dict) else []


def ig_every_comment(post_url, st):
    """every comment and reply of an Instagram post through TikHub (the logged-out page shows ~15): comment pages of 15
    (/instagram/v2/fetch_post_comments), then the replies (/fetch_comment_replies) of each comment whose reply count
    is above what was read before → (comments, {comment id: reply count read}, TikHub calls)"""
    code = re.search(r"/(?:p|reel)/([\w-]+)", post_url).group(1)
    out, nrep, calls, tok = {}, dict(st.get("r") or {}), 0, None
    for _ in range(TIKHUB_PAGES):
        d = tikhub("/api/v1/instagram/v2/fetch_post_comments", dict({"code_or_url": code, "sort_by": "recent"},
                                                                   **({"pagination_token": tok} if tok else {})))
        calls += 1
        items = th_items(d)
        for c in items:
            cm = th_comment(c)
            if cm["id"]:
                out[cm["id"]] = cm
                n = c.get("child_comment_count") or 0
                if n > nrep.get(cm["id"], 0):
                    rtok = None
                    for _ in range(TIKHUB_PAGES):
                        rd = tikhub("/api/v1/instagram/v2/fetch_comment_replies", dict({"code_or_url": code, "comment_id": cm["id"]},
                                                                                     **({"pagination_token": rtok} if rtok else {})))
                        calls += 1
                        for r in th_items(rd):
                            rc = th_comment(r)
                            if rc["id"]:
                                out[rc["id"]] = rc
                        rtok = rd.get("pagination_token") if th_items(rd) else None
                        if not rtok:
                            break
                    nrep[cm["id"]] = n
        tok = d.get("pagination_token") if items else None
        if not tok:
            break
    return list(out.values()), nrep, calls


def instagram_post(http, p):
    """one Instagram/Threads post's comments (the logged-out page; every comment through TikHub when it counts more)
    → new rows; raises on a login wall or an error answer"""
    hdr = {"Accept": "text/html", "Accept-Language": "zh-HK,zh;q=0.9", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    url = p["url"].replace("://www.threads.net/", "://www.threads.com/")
    try:
        r = http.get(url, proxies={"https": resi.url("hk")}, timeout=40, headers=hdr)
    except resi.ProxyOut:
        raise  # out of traffic: never retried into the 402
    except Exception:  # the residential proxy refuses a connection now and then: one retry
        r = http.get(url, proxies={"https": resi.url("hk")}, timeout=40, headers=hdr)
    watched = p.get("source") in watched_sources()
    if watched:  # a newly watched account (watchlist.py): its traffic counts for the daily safety stop
        watchlist.add_proxy_bytes(len(r.content) // 5)
    if "/accounts/login" in str(r.url) or r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} {str(r.url)[:80]}")
    got, total = ig_comments(r.text, p["url"])
    st = corpus.kv_get("cm:" + p["url"]) or {}
    new = corpus.add_comments(thread_of(p), got, total=total) if (got or total is not None) else []
    nrep = st.get("r") or {}
    if "instagram.com" in p["url"] and total and total > held(p["url"]):  # more than the page shows: every comment
        more, nrep, calls = ig_every_comment(p["url"], st)
        if watched:
            watchlist.add_tikhub(calls * 0.002)  # /instagram/v2/fetch_post_comments and …_replies, USD 0.002 a call
        new += corpus.add_comments(thread_of(p), more, total=total)
    corpus.kv_set("cm:" + p["url"], {"checked": now().isoformat(timespec="seconds"), "count": total,
                                      "n": (st.get("n") or 0) + len(new), "r": nrep})
    return len(new)


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    {"capture": capture}[sys.argv[1]]()
