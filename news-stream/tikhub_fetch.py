"""TikHub social-platform search for the YYC news stream (stdlib only).

Each adapter searches one platform for the NEWEST posts matching a keyword (newest-first sort where the
endpoint offers one) and returns normalised items:
  {"platform", "source", "title", "url", "published"}   (published: ISO 8601 in +08:00, or None)

Key: env TIKHUB_API (never printed), the YYC TikHub account (chendeyao.uk@gmail.com, key `yyc-news-stream`, scoped
to these route families). Billing: per successful request, prices in COST (from TikHub's get_all_endpoints_info,
2026-10-12); most search endpoints refuse the daily free credit, so the account needs a paid balance.

Scheduled run (CronJob tikhub, hourly): python3 tikhub_fetch.py --run
  every stream's social_queries (default: its news_queries) on every TikHub platform in SCHEDULE every 3 hours, plus
  Facebook post search through AnyAPI (fb_search.py) for streams with "facebook_search": true every hour (EVERY;
  Deyao, 2026-11-11: TikHub's hourly search was USD 4.79 a day); writes the document tikhub.json (corpus.file_put), which sources.tikhub_file reads. Every post a
  search returns (published within MAX_AGE) is kept, with no keyword filter: it goes into the corpus like any other
  post and the scorer (streams.py) decides which stream, if any, it belongs to (Deyao, 2026-10-14). Each item's
  section names the platform and the query. A query that fails or isn't due keeps its last posts in the file.
Run now (Deyao, by hand; results: infra/control.py social <STREAM_ID>): python3 tikhub_fetch.py --run --now <STREAM_ID>
  searches every query of that stream on every platform whether due or not. One run at a time (corpus.lock("tikhub"));
  each run is appended to the document social_runs.json with its request count and cost at list price.
CLI test: python3 tikhub_fetch.py <platform|all> <keyword>
Set TIKHUB_DUMP_DIR to save each raw response, TIKHUB_REPLAY_DIR to re-parse saved ones without requests.
"""
import json, os, re, sys, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

BASE = "https://api.tikhub.io"
UA = "yyc-news-stream/1"
TIMEOUT = 40
HKT = timezone(timedelta(hours=8))

PLATFORM_NAMES = {
    "tiktok": "TikTok", "twitter": "X", "youtube": "YouTube", "reddit": "Reddit", "threads": "Threads",
    "instagram": "Instagram", "facebook": "Facebook",
}
# USD per successful request (TikHub get_all_endpoints_info, 2026-10-12); free credit is refused where marked *
COST = {"tiktok": 0.001, "twitter": 0.001, "reddit": 0.001,  # X, Reddit *
        "youtube": 0.002, "threads": 0.002, "instagram": 0.002,  # *
        "facebook": 0.03}  # not TikHub: AnyAPI facebook.search_posts, USD 0.03 flat (its backup source up to 0.0661)
# Facebook: TikHub has no Facebook endpoints at all (2026-10-12), so the "facebook" adapter is AnyAPI (fb_search.py). Not covered:
# LinkedIn (job search only), Telegram (keyword search only inside one channel).


class TikHubError(Exception):
    pass


def platforms_on():
    """the platforms that may be searched now"""
    return list(ADAPTERS)


# The account's request rate is limited (HTTP 429 "Too Many Requests" when the scheduled run's 16 platform threads
# all fired at once, 2026-10-12): requests start at most every MIN_GAP seconds across all threads.
MIN_GAP = 0.3
_gap_lock = threading.Lock()
_last_start = [0.0]


def _pace():
    with _gap_lock:
        wait = _last_start[0] + MIN_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_start[0] = time.monotonic()


def call(path, params=None, body=None, tag=None):
    replay = os.environ.get("TIKHUB_REPLAY_DIR")  # parse saved responses offline (no requests)
    if replay:
        fn = os.path.join(replay, (tag or path.strip("/").replace("/", "_")) + ".json")
        if not os.path.exists(fn):
            raise TikHubError(f"no saved response {fn}")
        with open(fn, "rb") as f:
            return json.loads(f.read()).get("data")
    key = os.environ.get("TIKHUB_API")
    if not key:
        raise TikHubError("TIKHUB_API is not set")
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": "Bearer " + key, "User-Agent": UA, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    _pace()
    import costlog  # every request in paid_calls (the cost profile; TikHub bills the successful ones)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        costlog.record("tikhub", path, status=e.code, ok=False)
        msg = e.read()[:300].decode("utf-8", "replace")
        raise TikHubError(f"HTTP {e.code} {path}: {msg}") from None
    except Exception:
        costlog.record("tikhub", path, status=None, ok=False)
        raise
    costlog.record("tikhub", path)
    dump = os.environ.get("TIKHUB_DUMP_DIR")
    if dump:
        os.makedirs(dump, exist_ok=True)
        with open(os.path.join(dump, (tag or path.strip("/").replace("/", "_")) + ".json"), "wb") as f:
            f.write(raw)
    j = json.loads(raw)
    if j.get("code") not in (200, None):
        raise TikHubError(f"code {j.get('code')} {path}: {str(j.get('message'))[:200]}")
    return j.get("data")


# ---------- helpers ----------

def clean(s, n=160):
    s = re.sub(r"<[^>]+>", "", str(s or ""))
    s = re.sub(r"&nbsp;|&#160;", " ", s)
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def iso(t):
    """epoch seconds/ms (int or digit string), RFC 2822, or ISO string -> ISO 8601 in +08:00, else None."""
    if t is None or t == "" or t == 0:
        return None
    try:
        if isinstance(t, (int, float)) or (isinstance(t, str) and t.strip().isdigit()):
            v = float(t)
            if v > 1e12:
                v /= 1000.0
            if v < 1e9:
                return None
            return datetime.fromtimestamp(int(v), HKT).isoformat()
        s = str(t).strip()
        try:
            d = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            d = None
        if d is None:
            try:
                d = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
            except ValueError:
                d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            return None
        return d.astimezone(HKT).replace(microsecond=0).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


REL_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000,
             "year": 31536000, "秒": 1, "分钟": 60, "分鐘": 60, "小时": 3600, "小時": 3600, "天": 86400, "周": 604800,
             "週": 604800, "个月": 2592000, "個月": 2592000, "年": 31536000}


def iso_relative(t):
    """'4 hours ago' / 'Streamed 3 weeks ago' / '3小时前' -> approximate ISO 8601 (the unit is the precision)."""
    m = re.search(r"(\d+)\s*(second|minute|hour|day|week|month|year|秒|分钟|分鐘|小时|小時|天|周|週|个月|個月|年)", str(t or ""))
    if not m:
        return None
    secs = int(m.group(1)) * REL_UNITS[m.group(2)]
    return datetime.fromtimestamp(int(time.time()) - secs, HKT).isoformat()


def walk(o):
    """Yield every dict inside o (depth-first)."""
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def num(v):
    """an engagement count: int, or a string like '1,234' / '1.2万' / '3.4K' / '1.2K views' → int, else None"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([KkMmBb萬万億亿千w]?)", str(v))
    if not m:
        return None
    mult = {"k": 1e3, "K": 1e3, "千": 1e3, "m": 1e6, "M": 1e6, "b": 1e9, "B": 1e9, "萬": 1e4, "万": 1e4, "w": 1e4,
            "億": 1e8, "亿": 1e8}.get(m.group(2), 1)
    return int(float(m.group(1).replace(",", "")) * mult)


def counts(d, **spec):
    """{name: count} from the post dict d; spec name=[dotted paths tried in order], e.g. likes=["statistics.digg_count"]"""
    out = {}
    for name, paths in spec.items():
        for path in paths:
            v = d
            for k in path.split("."):
                v = v.get(k) if isinstance(v, dict) else None
            n = num(v)
            if n is not None:
                out[name] = n
                break
    return out


def tiktok_clip(a, url):
    """a TikTok video's clip for the transcription queue (media.py): read from the post's page by yt-dlp, no proxy
    (the CDN's play_addr expires within a day; the page doesn't). A photo post (image_post_info) has none"""
    return [] if a.get("image_post_info") else [{"key": f"tt:{a['aweme_id']}", "url": url, "kind": "ytdlp"}]


# A post's text is kept whole up to corpus.TEXT_MAX (2026-10-20: every adapter but X cut it at 2000 characters with
# "…": 28 TikTok and 5 Facebook posts in 3 days, the platform's own text longer)
TEXT_MAX = 20000


def item(pid, source, title, url, published, text=None, engagement=None, images=None, clips=None, text_max=TEXT_MAX):
    """text: the post's full text (title is its first 160 characters); engagement: the counts the platform gives;
    images: the post's picture URLs (images.py), clips: its videos (media.clip dicts, the transcription queue)"""
    name = PLATFORM_NAMES[pid]
    out = {"platform": name, "source": f"{clean(source, 60) or '?'} ({name})", "title": clean(title),
           "url": url, "published": published}
    if clean(source, 60):
        out["author"] = clean(source, 60)
    full = clean(text if text is not None else title, text_max)
    if full and full != out["title"]:
        out["text"] = full
    if engagement:
        out["engagement"] = engagement
    if images:
        out["images"] = images
    if clips:
        out["clips"] = clips
    return out


def dedupe(items):
    seen, out = set(), []
    for it in items:
        if it["url"] and it["url"] not in seen and it["title"]:
            seen.add(it["url"])
            out.append(it)
    return out


def newest_first(items):
    return sorted(items, key=lambda i: i["published"] or "", reverse=True)


def done(items):
    return newest_first(dedupe(items))


# ---------- adapters ----------

def tiktok(kw):
    # app v3 offers no newest-first sort (0 relevance / 1 likes); restrict to the last week instead
    d = call("/api/v1/tiktok/app/v3/fetch_video_search_result",
             {"keyword": kw, "count": "20", "offset": "0", "sort_type": "0", "publish_time": "7", "region": "HK"},
             tag="tiktok")
    out = []
    for a in walk(d):
        if a.get("aweme_id") and "desc" in a and isinstance(a.get("author"), dict):
            h = a["author"].get("unique_id") or ""
            url = f"https://www.tiktok.com/@{h}/video/{a['aweme_id']}"
            out.append(item("tiktok", a["author"].get("nickname") or h, a.get("desc"), url, iso(a.get("create_time")),
                            engagement=counts(a, views=["statistics.play_count"], likes=["statistics.digg_count"],
                                              comments=["statistics.comment_count"], shares=["statistics.share_count"]),
                            clips=tiktok_clip(a, url)))
    return done(out)


def twitter(kw):
    d = call("/api/v1/twitter/web/fetch_search_timeline", {"keyword": kw, "search_type": "Latest"}, tag="twitter")
    out = []
    for t in (d or {}).get("timeline") or []:
        if not (t.get("tweet_id") or t.get("id_str")) or t.get("text") is None or not t.get("created_at"):
            continue
        x = x_tweet(t, full_article=True)
        name = x["author"] or x["handle"]
        out.append(item("twitter", f"@{x['handle']}" if x["handle"] and name == x["handle"] else name, x["title"],
                        x["url"], x["published"], text=x["text"], engagement=x["engagement"], images=x["images"],
                        clips=x["clips"]))
    return done(out)


# ---------- X (search, user timeline and tweet detail share one tweet shape) ----------
# Deyao, 2026-10-15: an X post (伊凡投資心得, HKIC) showed only "https://t.co/3DS8gH0wK7": it was an X Article whose
# title and text TikHub gives (`article`; its full text in the tweet detail) and nobody read. A tweet's content is its
# text with the t.co links expanded, the X Article it carries (〔連結內容：title〕text), the tweet it quotes
# (〔引用 name @handle〕text) or, for a retweet, the original (〔轉推 name @handle〕text); photos go to images.py and
# videos (the best mp4) to the transcription queue.
LINK_OPEN = "〔連結內容："  # links.BLOCK: what the post links to, as the linked page states it
X_DETAIL = "/api/v1/twitter/web/fetch_tweet_detail"
X_DETAIL_USD = 0.001  # TikHub get_all_endpoints_info, 2026-10-15
TCO = re.compile(r"\s*https?://t\.co/\w+")
TCO_LAST = re.compile(r"\s*https?://t\.co/\w+\s*$")  # a tweet's own photo/video link: the last thing in its text
_extra = {"usd": 0.0}  # detail requests made inside a search run (added to its cost)
_extra_lock = threading.Lock()


def link_block(title, body=""):
    """the 〔連結內容：title〕body block (links.py reads the title back)"""
    title = re.sub(r"\s+", " ", str(title or "").replace("〕", "］")).strip()
    return f"{LINK_OPEN}{title}〕{(body or '').strip()}" if title else ""


def x_expand(t):
    """a tweet's text with its t.co links expanded. A t.co link left is its own photo/video (X puts that one last) when
    the tweet has media: dropped; any other is kept (Deyao, 2026-10-20: a post's link is never lost; follow.py and
    links.py expand it)"""
    text = t.get("text") or ""
    for u in ((t.get("entities") or {}).get("urls") or []) + (t.get("urls") or []):
        if u.get("url") and u.get("expanded_url"):
            text = text.replace(u["url"], u["expanded_url"].split("#")[0])
    photos, clips = x_media(t)
    if photos or clips or ((t.get("entities") or {}).get("media")):
        text = TCO_LAST.sub("", text)
    return text.strip()


def x_who(t):
    """(name, handle) of a tweet's author, whichever shape the answer has"""
    u = t.get("user_info") or t.get("author") or t.get("user") or {}
    handle = u.get("screen_name") or t.get("screen_name") or ""
    return (u.get("name") or None), handle


def _label(t):
    name, handle = x_who(t)
    return " ".join(x for x in (name, f"@{handle}" if handle else None) if x)


def x_article(t, full=None):
    """the X Article block of a tweet ('' when none); full: the article's full text when it was read"""
    a = t.get("article")
    if not isinstance(a, dict) or not a.get("title"):
        return ""
    return link_block(a["title"], full or a.get("full_text") or a.get("preview_text") or "")


def x_media(t):
    """(photo URLs, video clips) of a tweet"""
    m = t.get("media")
    if not isinstance(m, dict):
        return [], []
    photos = [p["media_url_https"] for p in m.get("photo") or [] if isinstance(p, dict) and p.get("media_url_https")]
    clips = []
    for v in m.get("video") or []:
        mp4 = [x for x in (v.get("variants") or []) if x.get("content_type") == "video/mp4" and x.get("url")]
        if not mp4:
            continue
        best = max(mp4, key=lambda x: x.get("bitrate") or 0)["url"]
        mid = re.search(r"/(?:amplify_video|ext_tw_video|tweet_video)(?:_thumb)?/(\d+)/", v.get("media_url_https") or best)
        clips.append({"key": "x:" + (mid.group(1) if mid else best.split("?")[0]), "url": best, "kind": "direct"})
    return photos, clips


def x_tweet(t, full_article=False):
    """one tweet (search / user timeline / tweet detail answer) → {id, url, handle, author, text, images, clips,
    engagement, published, article (its id or None), retweet}. full_article: read an X Article's full text through the
    tweet detail when the answer has only its preview (once per article: kv x_article:<id>)"""
    tid = str(t.get("tweet_id") or t.get("id_str") or t.get("id") or "")
    name, handle = x_who(t)
    rt = next((t[k] for k in ("retweeted_tweet", "retweeted_status", "retweeted") if isinstance(t.get(k), dict)), None)
    src = rt or t
    a = src.get("article") if isinstance(src.get("article"), dict) else None
    own = x_expand(src)
    if a and a.get("title"):  # the article itself replaces its link
        own = ARTICLE_LINK.sub("", own).strip()
    parts = []
    if rt:
        parts.append(f"〔轉推 {_label(rt)}〕{own}" if own else "")
    else:
        parts.append(own)
    full = None
    if a and a.get("title") and not a.get("full_text") and full_article:
        full = x_article_text(str(src.get("tweet_id") or src.get("id") or tid))
    title = None
    if a and a.get("title") and not own:  # an X Article: its own title and body (Deyao, 2026-10-15)
        title = clean(a["title"])
        body = (full or a.get("full_text") or a.get("preview_text") or "").strip()
        parts.append((f"〔轉推 {_label(rt)}〕" if rt else "") + body)
    else:
        parts.append(x_article(src, full))
    q = src.get("quoted")
    if isinstance(q, dict) and (q.get("text") or q.get("article")):
        parts.append(f"〔引用 {_label(q)}〕" + "\n".join(x for x in (x_expand(q), x_article(q)) if x))
    photos, clips = x_media(src)
    eng = counts(t, views=["views", "view_count"], likes=["favorites", "likes", "favorite_count"],
                 replies=["replies", "reply_count"], shares=["retweets", "retweet_count"], quotes=["quotes"],
                 bookmarks=["bookmarks"])
    text = "\n\n".join(p for p in parts if p).strip()
    return {"id": tid, "url": f"https://x.com/{handle or 'i'}/status/{tid}", "handle": handle, "author": name,
            "title": title or corpus_title(text), "text": text, "images": photos, "clips": clips, "engagement": eng,
            "published": iso(t.get("created_at")), "article": x_article_id(src) if a else None, "retweet": bool(rt)}


ARTICLE_LINK = re.compile(r"\s*https?://(?:www\.)?(?:x|twitter)\.com/i/article/\d+\S*")


def corpus_title(text):
    """a post's title from its text: its start, without links at either end (corpus.tidy_title)"""
    import corpus
    return clean(corpus.tidy_title(clean(text, 2000)))


def x_article_id(t):
    a = t.get("article") or {}
    m = re.search(r"x\.com/i/article/(\d+)", x_expand(t))
    return str(a.get("rest_id") or (m.group(1) if m else "") or "") or None


def x_detail(tid):
    """the tweet detail (USD 0.001, metered for the day's TikHub spend) → the answer's data"""
    d = call(X_DETAIL, {"tweet_id": tid}, tag=f"twitter_detail_{tid}")
    import watchlist
    watchlist.add_tikhub(X_DETAIL_USD)
    with _extra_lock:
        _extra["usd"] += X_DETAIL_USD
    return d or {}


def x_article_text(tid):
    """an X Article's full text through its tweet's detail, read once (kv x_article:<tweet id>) → str or None"""
    import corpus
    k = f"x_article:{tid}"
    got = corpus.kv_get(k)
    if got:
        return got.get("text")
    try:
        a = (x_detail(tid).get("article") or {})
    except Exception as e:  # noqa: BLE001: the preview stands
        print(f"x article {tid}: {e!r}"[:200], flush=True)
        return None
    text = a.get("full_text") or a.get("preview_text")
    if text:
        corpus.kv_set(k, {"text": text})
    return text


def youtube(kw):
    # sort_by=upload_date is ignored (relevance order comes back), so filter to this week; times are relative
    # ("5 hours ago"), converted at the unit's precision.
    d = call("/api/v1/youtube/web_v2/get_general_search_v2",
             {"keyword": kw, "upload_date": "this_week", "sort_by": "upload_date", "type": "video"}, tag="youtube")
    out = []
    for v in walk(d):
        vid = v.get("video_id") or v.get("videoId")
        if vid and v.get("title"):
            out.append(item("youtube", v.get("author") or v.get("channel_name") or v.get("channel_title"),
                            v.get("title"), f"https://www.youtube.com/watch?v={vid}",
                            iso_relative(v.get("published_time") or v.get("publish_time")),
                            text="\n".join(clean(x, TEXT_MAX) for x in (v.get("title"), v.get("description") or v.get("description_snippet"))
                                           if isinstance(x, str) and x) or None,
                            engagement=counts(v, views=["view_count", "views", "number_of_views", "viewCount"])))
    return done(youtube_described(out))


def youtube_described(out):
    """the search answer has only a snippet of the description (description_snippet, cut with "..."; read as
    "description", 68 of 71 posts had no text, 2026-10-20): each video's whole
    description from YouTube's own player answer (sources.youtube_info: free, no proxy, once per video). A video the
    player doesn't answer for keeps the snippet; replayed answers (TIKHUB_REPLAY_DIR) read nothing."""
    if os.environ.get("TIKHUB_REPLAY_DIR"):
        return out
    try:
        import sources
        info = sources.youtube_info(out)
    except Exception as e:  # noqa: BLE001: no database (the CLI test) or no answer: the snippets stand
        print(f"youtube descriptions: {e!r}"[:200], flush=True)
        return out
    for it in out:
        desc = (info.get(it["url"]) or {}).get("text")
        if desc:
            full = clean(it["title"] + "\n" + desc, TEXT_MAX)
            if full != it["title"]:
                it["text"] = full
    return out


def reddit_body(p):
    """a Reddit post's own text: content.markdown (with need_format the answer gives content as {"markdown": …}; read
    only as a string, 19 of 20 posts were stored with no text, 2026-10-20), or None"""
    c = p.get("content")
    body = c.get("markdown") if isinstance(c, dict) else c if isinstance(c, str) else None
    return body if body and body.strip() else None


# Reddit's mirror bots (Deyao, 2026-10-20: "why is there a source from scmpauto subreddit but not scmp?"): the
# AutoNewspaper network re-posts outlets' articles as link posts, r/AutoNewspaper and one r/<outlet>auto per outlet
# (r/SCMPauto "South China Morning Post AutoNews", r/NYTauto…), posted by these accounts. Such a post is a pointer to
# the article (item "mirror_of"; sources.resolve_mirrors collects the article itself), never a post of its own.
MIRROR_AUTHORS = {"autonewsadmin", "autonewspaperadmin"}
MIRROR_SUB = re.compile(r"^(autonewspaper|\w+auto)$", re.I)
NOT_ARTICLE_HOSTS = re.compile(r"(^|\.)(reddit\.com|redd\.it|redditmedia\.com|imgur\.com|gfycat\.com)$", re.I)


def reddit_link(p):
    """the outside page a Reddit link post points to, or None (a self post, a picture or video on Reddit)"""
    url = p.get("url") if isinstance(p.get("url"), str) else None
    if p.get("isSelfPost") or not url or not url.startswith("http"):
        return None
    host = urllib.parse.urlsplit(url).netloc.lower().split(":")[0]
    return None if NOT_ARTICLE_HOSTS.search(host) else url


def reddit_mirror(sub, sub_title, author):
    return ((author or "").lower() in MIRROR_AUTHORS
            or bool(MIRROR_SUB.match(sub or "") and re.search(r"autonews", sub_title or "", re.I)))


def reddit(kw):
    d = call("/api/v1/reddit/app/fetch_dynamic_search",
             {"query": kw, "search_type": "post", "sort": "NEW", "need_format": "true"}, tag="reddit")
    out = []
    for p in walk(d):
        if p.get("postTitle") and (p.get("permalink") or p.get("url")):
            link = p.get("permalink") or p.get("url")
            if link.startswith("/"):
                link = "https://www.reddit.com" + link
            sr = p.get("subreddit") if isinstance(p.get("subreddit"), dict) else {"name": p.get("subreddit")}
            sub = sr.get("name")
            au = (p.get("authorInfo") or {}).get("name") if isinstance(p.get("authorInfo"), dict) else p.get("author")
            outside = reddit_link(p)
            # the link in the text: corpus.link_key puts the post in the linked article (posts.article_id)
            text = "\n".join(clean(x, TEXT_MAX) for x in (p.get("postTitle"), reddit_body(p), outside) if x) or None
            it = item("reddit", f"r/{sub}" if sub else au, p.get("postTitle"), link, iso(p.get("createdAt")),
                      text=text, engagement=counts(p, likes=["score", "upvoteCount"], comments=["commentCount", "numComments"]))
            if outside and reddit_mirror(sub, sr.get("title"), au):
                it["mirror_of"] = outside
            out.append(it)
    return done(out)


def threads(kw):
    # search_recent (newest) failed upstream on every try (HTTP 400, not billed); search_posts (relevance) is the fallback
    try:
        d = call("/api/v1/threads/web/search_recent", {"query": kw}, tag="threads_recent")
    except TikHubError:
        d = call("/api/v1/threads/web/search_posts", {"query": kw}, tag="threads_posts")
    out = []
    for p in walk(d):
        if p.get("code") and isinstance(p.get("user"), dict) and p.get("taken_at"):
            un = p["user"].get("username") or ""
            cap = p.get("caption") or {}
            pics, clips = meta_media(p, p["code"], threads=True)  # a video post is transcribed (media.py)
            out.append(item("threads", un, cap.get("text") if isinstance(cap, dict) else cap,
                            f"https://www.threads.com/@{un}/post/{p['code']}", iso(p.get("taken_at")),
                            engagement=counts(p, likes=["like_count"], replies=["text_post_app_info.direct_reply_count"],
                                              shares=["text_post_app_info.repost_count"]), images=pics, clips=clips))
    return done(out)


def _best(cands):
    """the largest picture of an Instagram/Threads candidates list (image_versions2.candidates / image_versions.items)"""
    cands = [c for c in cands or [] if isinstance(c, dict) and c.get("url")]
    return max(cands, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0))["url"] if cands else None


def _picture(o):
    iv = o.get("image_versions2") or o.get("image_versions") or {}
    if isinstance(iv, dict):
        u = _best(iv.get("candidates") or iv.get("items"))
        if u:
            return u
    return o.get("display_uri") or o.get("display_url") or o.get("thumbnail_url")


def meta_media(o, code, threads=False):
    """(picture URLs, video clips) of an Instagram or Threads post dict (a profile page's node, TikHub's media dict, a
    Threads post; Deyao, 2026-10-15: photo and video posts with a caption of a few hashtags were scored on nothing):
    the post's picture, or every picture of a carousel; a video's cover and the video as a clip. Instagram: ig:<code>,
    its /reel/ page, kind ytdlp (media.py reads it with yt-dlp from the server, the proxy only as a fallback; the profile page carries no
    video URL). Threads: the mp4 itself (video_versions, kind direct), threads:<code>[:n]"""
    images, clips = [], []
    kids = [k for k in o.get("carousel_media") or [] if isinstance(k, dict)]
    for i, k in enumerate(kids or [o]):
        u = _picture(k)
        if u and u not in images:
            images.append(u)
        video = k.get("video_versions") or k.get("is_video") is True or k.get("media_type") == 2
        if not video:
            continue
        if threads:
            mp4 = _best(k.get("video_versions"))
            if mp4:
                clips.append({"key": f"threads:{code}" + (f":{i}" if kids else ""), "url": mp4, "kind": "direct"})
        elif not any(c["key"] == "ig:" + code for c in clips):
            clips.append({"key": "ig:" + code, "url": f"https://www.instagram.com/reel/{code}/", "kind": "ytdlp"})
    return images, clips


def instagram(kw):
    # hashtag feed, newest first (v2 feed_type=recent, USD 0.002, carries usernames). v1 fetch_hashtag_posts
    # (USD 0.001) mixes top + recent posts but gives only the owner's numeric id; v3 costs USD 0.008.
    tag = re.sub(r"[\W_]+", "", kw).lower()
    if not tag:
        return []
    d = call("/api/v1/instagram/v2/fetch_hashtag_posts", {"keyword": tag, "feed_type": "recent"}, tag="instagram")
    out = []
    for p in walk(d):
        if p.get("code") and p.get("taken_at") and isinstance(p.get("user"), dict):
            cap = p.get("caption") or {}
            pics, clips = meta_media(p, p["code"])
            out.append(item("instagram", p["user"].get("full_name") or p["user"].get("username"),
                            (cap.get("text") if isinstance(cap, dict) else cap) or "(post without caption)",
                            f"https://www.instagram.com/p/{p['code']}/", iso(p.get("taken_at")), images=pics, clips=clips,
                            engagement=counts(p, views=["play_count", "view_count", "ig_play_count"], likes=["like_count"],
                                              comments=["comment_count"])))
    return done(out)


def facebook(kw):
    """Facebook post search through AnyAPI, not TikHub (TikHub has no Facebook endpoints); see fb_search.py"""
    import fb_search
    # the title is the post's text, never its author's name (posts were titled 楊濟榮, 方建平: Deyao, 2026-10-15)
    out = [item("facebook", p["author"], p["text"] or "(post without text)", p["url"], iso(p["created"]), text=p["text"],
                engagement=p["engagement"], images=p.get("images"), clips=p.get("clips")) for p in fb_search.search(kw)]
    return done(out)


ADAPTERS = {
    "tiktok": tiktok, "twitter": twitter, "youtube": youtube, "reddit": reddit, "threads": threads,
    "instagram": instagram, "facebook": facebook,
}


def search(platform, keyword):
    """Run one adapter; returns (items, error_or_None) and never raises."""
    fn = ADAPTERS.get(platform)
    if not fn:
        return [], f"unknown platform {platform}"
    for attempt in (1, 2):
        try:
            return fn(keyword), None
        except Exception as e:  # noqa: BLE001 — one platform's failure must not stop the others
            err = f"{type(e).__name__}: {str(e)[:300]}"
            # HTTP 400 = TikHub's upstream scrape failed (not billed); it often works on a second try (Threads)
            # 429 = the account's rate limit (not billed): wait and try once more
            if attempt == 1 and ("HTTP 400" in err or "HTTP 429" in err):
                time.sleep(3)
                continue
            return [], err


# ---------- scheduled run ----------
HERE = os.path.dirname(os.path.abspath(__file__))
# How often each (platform, query) is searched, per provider; the 5 minutes' slack keeps a query due when a run starts
# late. TikHub every 3 hours (Deyao, 2026-11-11: hourly was USD 4.79 a day); Facebook (AnyAPI) on every hourly run.
EVERY = {"tikhub": timedelta(hours=3) - timedelta(minutes=5), "anyapi": timedelta(hours=1) - timedelta(minutes=5)}
RETRY = timedelta(hours=1) - timedelta(minutes=5)  # a failed check (unbilled) is tried again on the next run
# A provider's refusal for its balance (TikHub: HTTP 402 "Insufficient balance"; AnyAPI: 402 / credits) is a safety
# stop (YYC CLAUDE.md "Budgets are safety stops"): kv social_limit, one Discord message per episode, that provider's
# other queries are not sent in the run, the first answer clears it.
LIMIT_RE = re.compile(r"HTTP 402|insufficient|balance|credit", re.I)


def provider(platform):
    return "anyapi" if platform == "facebook" else "tikhub"


PROVIDER_NAMES = {"tikhub": "TikHub (chendeyao.uk@gmail.com, key yyc-news-stream)", "anyapi": "AnyAPI (YYC's account)"}


def limit_update(stopped, answered, now):
    """record the providers that refused for their balance, notify Deyao once per episode, clear the ones that answered"""
    import corpus, notify
    lim = dict(corpus.kv_get("social_limit") or {})
    for prov, msg in stopped.items():
        cur = lim.get(prov) or {"since": now, "notified": False}
        cur.update(message=msg[:300], at=now)
        if not cur.get("notified"):
            print(f"social search: {prov} requests failing for its balance:", msg[:200], flush=True)
            cur["notified"] = notify.deyao(
                f"Waiting on you: {PROVIDER_NAMES[prov]} requests for YYC's social search are failing with "
                f"\"{msg[:250]}\" since {cur['since'][:16].replace('T', ' ')} HKT (the account's balance), so its "
                "platforms bring in no new posts. Top up the account; the hourly run tries again and carries on by itself.")
        lim[prov] = cur
    for prov in answered - set(stopped):
        if lim.pop(prov, None):
            print(f"social search: {prov} answers again: limit cleared", flush=True)
    corpus.kv_set("social_limit", lim or None)
SCHEDULE = ["tiktok", "twitter", "youtube", "reddit", "threads", "instagram", "facebook"]
MAX_AGE = timedelta(days=3)


def streams():
    with open(os.path.join(HERE, "streams.json")) as f:
        return json.load(f)


def queries(only=None):
    """→ [(platform, query)]: every stream's social_queries (default news_queries) on every TikHub platform, and on
    Facebook for streams with facebook_search. Every post a search returns is kept; the scorer (streams.py) decides
    which stream, if any, it belongs to"""
    out = []
    every = streams()
    on = set(platforms_on())
    for s in every:
        if only and s["id"] != only:
            continue
        for q in s.get("social_queries", s.get("news_queries", [])):
            for p in SCHEDULE:
                if p not in on:
                    continue
                if p == "facebook" and not s.get("facebook_search"):
                    continue
                if (p, q) not in out:
                    out.append((p, q))
    return out


def run(now_stream=None):
    import corpus
    with corpus.lock("tikhub"):  # the timer's run and a button run never write tikhub.json at once
        _run(now_stream)


def _run(now_stream):
    import corpus
    from concurrent.futures import ThreadPoolExecutor
    prev = corpus.file_get("tikhub.json", {"checks": {}, "items": []})
    now = datetime.now(HKT)
    forced = set(queries(now_stream)) if now_stream else set()
    if now_stream and not forced:
        raise SystemExit(f"no stream {now_stream} (or it has no queries)")
    plan = queries()
    by_platform = {}
    for p, q in plan:
        by_platform.setdefault(p, []).append(q)

    stopped, answered, stop_lock = {}, set(), threading.Lock()

    def platform_run(p):
        checks, items, calls, billed = {}, [], 0, 0
        prov = provider(p)
        for q in by_platform.get(p, []):
            key = f"{p}|{q}"
            last = prev["checks"].get(key)
            if forced:  # a button run: only that stream's queries
                due = (p, q) in forced
            else:
                due = not last or now - datetime.fromisoformat(last["checked"]) >= (EVERY[prov] if last["ok"] else RETRY)
            if not due and not last:
                continue
            with stop_lock:
                if prov in stopped:  # the provider refused for its balance this run: the query keeps its last posts
                    due = False
                    if not last:
                        continue
            if not due:
                checks[key] = last  # not due: keep its last result and posts
                items += [i for i in prev["items"] if i.get("key") == key]
                continue
            if calls:
                time.sleep(1.1)
            calls += 1
            got, err = search(p, q)
            if err and LIMIT_RE.search(err):
                with stop_lock:
                    stopped.setdefault(prov, err)
            elif not err:
                with stop_lock:
                    answered.add(prov)
            if err:  # keep the last posts until a check succeeds
                checks[key] = {"ok": False, "items": 0, "error": err[:200], "checked": now.isoformat(timespec="seconds")}
                items += [i for i in prev["items"] if i.get("key") == key]
                continue
            billed += 1  # TikHub and AnyAPI bill successful requests
            fresh = [g for g in got if g["url"] and (not g["published"] or
                                                      now - datetime.fromisoformat(g["published"]) <= MAX_AGE)]
            checks[key] = {"ok": True, "items": len(fresh), "error": None, "checked": now.isoformat(timespec="seconds"),
                           "found": len(got)}
            items += [dict(g, section=f"{g['platform']} · {q}", key=key, found=now.isoformat(timespec="seconds"))
                      for g in fresh]
        return checks, items, calls, billed

    checks, items, calls, cost = {}, [], {}, 0.0
    with _extra_lock:
        _extra["usd"] = 0.0
    with ThreadPoolExecutor(len(SCHEDULE)) as ex:
        for p, (c, i, n, b) in zip(SCHEDULE, ex.map(platform_run, SCHEDULE)):
            checks.update(c)
            items += i
            if n:
                calls[p] = n
                cost += b * COST.get(p, 0)
    if _extra["usd"]:  # X Articles' full text (tweet detail) read during the run
        calls["twitter_detail"] = round(_extra["usd"] / X_DETAIL_USD)
        cost += _extra["usd"]
    limit_update(stopped, answered, now.isoformat(timespec="seconds"))
    ok = sum(1 for v in checks.values() if v["ok"])
    out = {"fetched": now.isoformat(timespec="seconds"), "ok": ok > 0,
           "error": None if ok else next((v["error"] for v in checks.values() if v["error"]), "no query answered"),
           "checks": checks, "items": items}
    corpus.file_put("tikhub.json", out)
    if calls:  # the run log (infra/control.py social) (cost = successful requests x list price; AnyAPI's backup can cost more)
        runs = corpus.file_get("social_runs.json", [])
        failed_now = sorted({k.split("|")[0] for k, v in checks.items() if not v["ok"] and v["checked"] == out["fetched"]})
        runs.append({"at": out["fetched"], "trigger": f"button {now_stream}" if now_stream else "hourly",
                     "requests": calls, "cost_usd": round(cost, 4), "failed": failed_now})
        corpus.file_put("social_runs.json", runs[-500:])
    failed = sorted({k.split("|")[0] for k, v in checks.items() if not v["ok"]})
    print(f"tikhub: {sum(calls.values())} requests (USD {cost:.3f} at list price), {ok}/{len(checks)} queries ok, "
          f"{len(items)} posts; failing: {failed}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--run"]:
        run(argv[2] if argv[1:2] == ["--now"] and len(argv) > 2 else None)
        return 0
    if len(argv) < 2:
        print("usage: tikhub_fetch.py --run [--now STREAM] | <platform|all> <keyword>\nplatforms: " + " ".join(ADAPTERS))
        return 2
    plats = list(ADAPTERS) if argv[0] == "all" else argv[0].split(",")
    kw = " ".join(argv[1:])
    for i, p in enumerate(plats):
        if i:
            time.sleep(1.1)  # some routes are limited to 1 request/s
        items, err = search(p, kw)
        print(f"{p} ({PLATFORM_NAMES.get(p, '?')}): {len(items)} items" + (f"  ERROR {err}" if err else ""))
        for it in items[:2]:
            print(f"   {it['published']}  {it['source']}  {it['title'][:80]}\n      {it['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
