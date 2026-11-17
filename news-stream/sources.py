"""The YYC stream's sources: one tested parser per source (stdlib only). fetch_all() returns normalised items
{source, section, title, url (absolute), published (aware datetime or None)} and a per-source status.
Probed and tested 2026-10-12 (see the probe's notes in ~/artifacts/yyc/sources.json of that session).
成報, 亞洲週刊 and 明報月刊 come from browser_fetch.py (a real browser: Cloudflare managed challenge), and discuss.com.hk directly (bans datacenter networks; it
comes in through Google News site: search, 1-2 days late, and through discuss_search on a residential proxy). HKEJ refuses the Hetzner server's IP, so its pages go
through RESI_PROXY (IPRoyal HK residential, sub-user yycstream, YYC's one sub-user: resi.py).
"""
import gzip, html, http.client, json, re, socket, sys, time, urllib.error, urllib.parse, urllib.request, zlib, concurrent.futures as cf
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import corpus
import nomedia
import resi
import walls
import lihkg_api

HKT = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"


def refusal_reason(e):
    """the reason a server gave in a JSON error body (r.jina.ai: readableMessage / message), cut to 300 chars, or None
    (live 16 Oct 2026: the monitor showed FSTB LinkedIn's "403 Forbidden" while Jina's body said why)"""
    if not isinstance(e, urllib.error.HTTPError) or "json" not in (e.headers.get("Content-Type") or ""):
        return None
    try:
        raw = e.read(200000)
        if (e.headers.get("Content-Encoding") or "").lower() in ("gzip", "deflate"):  # r.jina.ai compresses it
            raw = zlib.decompress(raw, 47)  # 47 = zlib or gzip header, detected
        d = json.loads(raw[:20000].decode("utf-8", "ignore"))
    except Exception:
        return None
    why = (d.get("readableMessage") or d.get("message")) if isinstance(d, dict) else None
    return str(why)[:300] if why else None


RETRIES = 2  # more tries of a GET after a transient error (transient()); a timeout gets one
BACKOFF = (2, 6)  # seconds before each of them
_TRANSIENT_HTTP = (502, 503, 504)
_TRANSIENT_TEXT = re.compile(r"Tunnel connection failed: 50[234]|Connection reset|Connection refused|Remote end closed|"
                             r"RemoteDisconnected|Connection aborted|Temporary failure in name resolution|EOF occurred",
                             re.I)


def transient(e):
    """an error worth one more try in a moment: a dropped or refused connection, a proxy's or a server's 502/503/504
    (not a proof-of-work wall: fetch passes those), a timeout; never a 4xx or IPRoyal's 402 (resi.ProxyOut)"""
    if isinstance(e, resi.ProxyOut):
        return False
    if isinstance(e, urllib.error.HTTPError):
        return e.code in _TRANSIENT_HTTP
    if isinstance(e, (TimeoutError, socket.timeout)) or "timed out" in str(e):
        return "timeout"
    return isinstance(e, (ConnectionError, http.client.RemoteDisconnected, urllib.error.URLError, OSError)) and \
        bool(_TRANSIENT_TEXT.search(repr(e)))


def fetch(url, data=None, headers=None, proxy=None, timeout=30, charset=None, verify=True, isp=False, meta=None):
    """GET (POST with data) url → text, a GET tried again RETRIES times after a transient error (transient(): a dropped
    connection, a 502/503/504, a timeout once), BACKOFF seconds apart (tests/test_fetch_retry.py).
    One try: _fetch_once."""
    timeouts = 0
    for i in range(RETRIES + 1):
        try:
            return _fetch_once(url, data, headers, proxy, timeout, charset, verify, isp, meta)
        except Exception as e:
            kind = transient(e) if data is None else False
            if not kind or i == RETRIES or (kind == "timeout" and timeouts):
                raise
            timeouts += kind == "timeout"
            time.sleep(BACKOFF[min(i, len(BACKOFF) - 1)])


def _fetch_once(url, data=None, headers=None, proxy=None, timeout=30, charset=None, verify=True, isp=False, meta=None):
    """GET (POST with data) url → text. charset "auto": the body's own encoding (Content-Type, the XML declaration or a
    <meta charset>; Macau's e-papers are GB2312/Big5), else UTF-8; a codec name: that one; by default UTF-8, the declared one when the body
    is not UTF-8. verify=False: a site whose certificate chain is
    incomplete (the server leaves out its intermediate; browsers fetch it themselves): the TLS certificate isn't checked.
    isp=True: proxy is YYC's IPRoyal ISP IP (flat rate): its bytes go to resi.count_isp, not yycstream's per-GB count.
    meta: a dict that gets the answer's ETag and Last-Modified (for a conditional request next time; a 304 answer is
    the HTTPError 304 urllib raises)"""
    resi_px = proxy if not isp else None
    h = {"User-Agent": UA, "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8", "Accept-Encoding": "gzip, deflate"}
    h.update(headers or {})
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    if not verify:
        import ssl
        handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    opener = urllib.request.build_opener(*handlers)
    t0 = time.time()
    try:
        r = opener.open(urllib.request.Request(url, data=data, headers=h), timeout=timeout)
    except Exception as e:
        if resi_px:
            resi.check(e)  # IPRoyal out of traffic (402; a plain-http URL's 402 answer, confirmed): resi.ProxyOut
        if data is None and verify and isinstance(e, urllib.error.HTTPError) and e.code in (403, 429, 503) and \
                "json" not in (e.headers.get("Content-Type") or "") and walls.which(_error_body(e), e.code):
            return _walled(url, proxy, resi_px, headers, timeout)  # a proof-of-work wall (walls.py)
        why = refusal_reason(e)
        if why:
            raise urllib.error.HTTPError(e.url, e.code, why, e.headers, None) from e
        raise
    if resi_px:
        resi.ok(t0)
    if meta is not None:
        meta.update(etag=r.headers.get("ETag"), last_modified=r.headers.get("Last-Modified"))
    if nomedia.media_type(r.headers.get("Content-Type")):  # a picture / clip / font: its body is never read (text only)
        r.close()
        if resi_px:
            resi.count(resi.OVERHEAD, url)
        elif proxy:
            resi.count_isp(resi.OVERHEAD)
        raise nomedia.refuse(url)
    enc = r.headers.get("Content-Encoding", "")
    try:
        d = r.read()
    except http.client.IncompleteRead as e:
        # a server that cuts the body off (橙新聞's sitemaps stop at 64,454 bytes on about half the reads, on every
        # route): ask for the rest with Range, up to 5 more times; only for an uncompressed GET
        if enc or data is not None:
            raise
        d = e.partial
        for _ in range(5):
            rr = opener.open(urllib.request.Request(url, headers=dict(h, Range=f"bytes={len(d)}-")), timeout=timeout)
            if rr.status != 206:
                raise
            try:
                d += rr.read()
                break
            except http.client.IncompleteRead as e2:
                d += e2.partial
        else:
            raise
    if resi_px:
        resi.count(len(d) + resi.OVERHEAD, url)  # the compressed body as it crossed the proxy
    elif proxy:
        resi.count_isp(len(d) + resi.OVERHEAD)
    if enc == "gzip":
        d = gzip.decompress(d)
    elif enc == "deflate":
        d = zlib.decompress(d)
    if charset:  # "auto", or a site's charset option (what it says)
        enc_ = body_charset(d, r.headers.get("Content-Type")) if charset == "auto" else CHARSET_ALIASES.get(charset.lower(), charset)
        text = d.decode(enc_, "replace")
    else:
        try:
            text = d.decode("utf-8")
        except UnicodeDecodeError:  # not UTF-8: the encoding the page declares (香港賽馬資料庫 is Big5, 2026-10-20)
            text = d.decode(body_charset(d, r.headers.get("Content-Type")), "replace")
    if data is None and verify and walls.which(text, r.status):  # SiteGround's 202 stub is a "success" to urllib
        return _walled(url, proxy, resi_px, headers, timeout)
    return text


def _walled(url, proxy, resi_px, headers, timeout):
    """url read through its proof-of-work wall (walls.get), its bytes counted like fetch's own"""
    t = walls.get(url, proxy=proxy, headers=headers, timeout=timeout)
    if resi_px:
        resi.count(len(t.encode()) + 3 * resi.OVERHEAD, url)
    elif proxy:
        resi.count_isp(len(t.encode()) + 3 * resi.OVERHEAD)
    return t


def _error_body(e):
    """an HTTPError's body, decoded (a wall's page), "" when unreadable"""
    try:
        raw = e.read(200000)
        if (e.headers.get("Content-Encoding") or "").lower() in ("gzip", "deflate"):
            raw = zlib.decompress(raw, 47)
        return raw.decode("utf-8", "ignore")
    except Exception:
        return ""


CHARSET_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030", "big5": "big5hkscs", "x-big5": "big5hkscs"}


def body_charset(d, content_type=None):
    """the encoding a body declares (Content-Type charset, <?xml encoding>, <meta charset>), as a Python codec name
    (GB2312/GBK read as GB18030 and Big5 as Big5-HKSCS, their supersets); UTF-8 when none is declared or known"""
    m = re.search(r"charset=[\"']?([\w-]+)", content_type or "", re.I) or \
        re.search(rb"<\?xml[^>]*encoding=[\"']([\w-]+)", d[:300], re.I) or \
        re.search(rb"<meta[^>]*charset=[\"']?([\w-]+)", d[:4000], re.I)
    name = (m.group(1).decode("ascii", "ignore") if isinstance(m.group(1), bytes) else m.group(1)).lower() if m else "utf-8"
    name = CHARSET_ALIASES.get(name, name)
    try:
        "".encode(name)
    except LookupError:
        return "utf-8"
    return name


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def count(s):
    """'1,234' / '1.2K' / '1.2萬' / '3.4M views' / '觀看次數：1,234次' → int, else None"""
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([KkMm萬万億亿千]?)", str(s or ""))
    if not m:
        return None
    mult = {"k": 1e3, "K": 1e3, "千": 1e3, "m": 1e6, "M": 1e6, "萬": 1e4, "万": 1e4, "億": 1e8, "亿": 1e8}.get(m.group(2), 1)
    return int(float(m.group(1).replace(",", "")) * mult)


def paragraphs(s):
    """HTML → plain text with paragraph breaks kept"""
    s = re.sub(r"<(script|style)\b.*?</\1>", "", s or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</(p|div|li|h\d|tr|blockquote)>", "\n", s, flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return "\n".join(l for l in (re.sub(r"[ \t\u3000\xa0]+", " ", x).strip() for x in s.split("\n")) if l)


def cdata(s):
    return re.sub(r"^\s*<!\[CDATA\[|\]\]>\s*$", "", s or "")


def rss(text):
    out = []
    for it in re.findall(r"<item\b.*?>(.*?)</item>", text, re.S):
        g = lambda k: (re.search(r"<%s\b[^>]*>(.*?)</%s>" % (k, k), it, re.S) or [None, ""])[1]
        t = re.sub(r"^<!\[CDATA\[|\]\]>$", "", g("title").strip())
        l = re.sub(r"^<!\[CDATA\[|\]\]>$", "", g("link").strip())
        src = re.search(r'<source url="([^"]+)">([^<]+)', it)
        au = re.search(r"<(?:dc:creator|author)\b[^>]*>(.*?)</(?:dc:creator|author)>", it, re.S)
        out.append({"title": clean(t), "url": html.unescape(l), "time": re.sub(r"^<!\[CDATA\[|\]\]>$", "", g("pubDate").strip()),
                    "section": clean(g("category")) or (html.unescape(src.group(2)) if src else ""),
                    "author": clean(cdata(au.group(1))) if au else None, "desc": paragraphs(cdata(g("description"))),
                    # the whole article (WordPress <content:encoded>) is the post's text when longer than the
                    # <description> excerpt and not itself cut (集誌社, 2026-10-20: a 1,394-character excerpt
                    # ending 「[…]」, the article 3,414)
                    "text": _whole(paragraphs(cdata(g("content:encoded"))), paragraphs(cdata(g("description"))))})
    return out


CUT_END = re.compile(r"(\[(…|&#8230;|\.\.\.)\]|…|\.\.\.|read more|continue reading|閱讀全文|繼續閱讀|全文)\W*$", re.I)


def _whole(content, desc):
    """a feed item's <content:encoded> when it is the whole article (longer than the description, not cut), else None"""
    return content if content and len(content) > len(desc) and not CUT_END.search(content[-40:]) else None


def rx(pattern, flags=re.S):
    def p(text):
        out = []
        for m in re.finditer(pattern, text, flags):
            d = {k: clean(v) if k == "title" else (v or "") for k, v in m.groupdict().items()}
            out.append(d)
        return out
    return p


def hk01(text):
    return [{"title": i["data"]["title"], "url": i["data"]["publishUrl"], "time": i["data"]["publishTime"],
             "section": i["data"].get("mainCategory", ""),
             "author": ", ".join(a["publishName"] for a in i["data"].get("authors") or [] if a.get("publishName")) or None,
             "engagement": {"comments": i["data"]["commentCount"]} if isinstance(i["data"].get("commentCount"), int) else None}
            for i in json.loads(text)["items"] if i.get("type") == 1]


def tvb(text):
    out = []
    for z in json.loads(text)["data"]["zones"]:
        for n in z.get("news") or []:
            out.append({"title": n["title"], "url": n["share_url"], "time": n["publish_at"], "section": n.get("category") or z["navigation_name"]})
    return out


def now(text):
    return [{"title": x["title"], "url": "https://news.now.com/home/local/player?newsId=" + x["newsId"],
             "time": x["publishDate"] // 1000, "section": x["categoryName"]} for x in json.loads(text)]


def now_sports(text):
    """Now Sports' own API (news.now.com/home/sports lists it): headline, summary, sport"""
    return [{"title": x.get("headlineChi") or "", "url": "https://news.now.com/home/sports/player?newsId=" + str(x["newsId"]),
             "time": x["publishDate"] // 1000, "section": x.get("sportType") or "體育", "text": x.get("summaryChi") or None}
            for x in json.loads(text)]


def metroradio(text):
    """新城's home and section pages; a section page adds CategoryName to the link, which is dropped so a story has one
    URL whichever page lists it"""
    return [{"title": t, "url": f"/news/details.aspx?NewsId={i}&amp;page=0", "time": i}
            for i, t in re.findall(r'<a id="[^"]*lnkTitle[^"]*" href="/news/details\.aspx\?(?:CategoryName=[^&"]*&amp;)?'
                                   r'NewsId=(\d{14})[^"]*">([^<]+)</a>', text)]


def etnet_latest(text):
    """經濟通's whole latest-news list (categorized_news.php, 40 a page, ~450 stories a day). Links use the editor's-picks
    RSS form (home_categorized_news_detail.php?newsid=ETN3<year's last digit><MMDD><n>) so a story both list it once"""
    out = []
    for nid, title, t in re.findall(r'category=latest&newsid=(\d{9,})"[^>]*><h2 class="h4">([^<]+)</h2>.*?'
                                    r'<p class="time">(\d\d/\d\d/\d{4} \d\d:\d\d)</p>', text, re.S):
        d, m, rest = t.split("/", 2)
        out.append({"title": title.strip(), "time": f"{rest[:4]}-{m}-{d}T{rest[5:]}+08:00",
                    "url": f"http://www.etnet.com.hk/www/tc/news/home_categorized_news_detail.php?newsid=ETN3{nid[3]}{nid[4:]}"})
    return out


def cr881(text):
    s = text[text.find("VueApp.main(") + 12:]
    d, _ = json.JSONDecoder().raw_decode(s)
    return [{"title": x["title"], "url": "https://www.881903.com/news/%s/%s" % (x["article_column"]["uri_code"], x["item_id"]),
             "time": x["display_ts"], "section": x["article_column"]["name"]} for x in d["news"]["content"]]


def youtube(text):
    d = json.loads(re.search(r"var ytInitialData = (\{.*?\});</script>", text, re.S).group(1))
    out = []

    def walk(o):
        if isinstance(o, dict):
            if "lockupViewModel" in o:
                l = o["lockupViewModel"]; m = l["metadata"]["lockupMetadataViewModel"]
                rows = [p["text"]["content"] for r in m.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", []) for p in r.get("metadataParts", [])]
                views = next((count(r) for r in rows[:-1] if count(r) is not None), None)  # ['35 次觀看', '24 分鐘前']
                out.append({"title": m["title"]["content"], "url": "https://www.youtube.com/watch?v=" + l["contentId"], "time": rows[-1] if rows else "", "section": "",
                            "engagement": {"views": views} if views is not None else None})
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    return out


# A channel's uploads playlist gives only titles and relative times. Two things come from YouTube's player answer
# (~8 KB, no proxy; it answers from the cluster's IP even when the video itself is "UNPLAYABLE" there), read once per
# video (kv ytinfo:<id>) for every video the corpus doesn't hold with its text yet (youtube_info):
# - the description (videoDetails.shortDescription, the whole description despite its name): a video's post text.
#   The playlist page has none, so yt_* / rthk_youtube posts had only their title (2026-10-20: 92 of 99 empty since
#   09:15), and so did tikhub_youtube (TikHub's search gives at most a snippet: 68 of 71 empty) and watch_youtube
#   during YouTube's feed outages (its uploads-page fallback);
# - the exact time: "4 分鐘前" is exact to the minute, but "3 小時前" / "2 日前" / "2 週前" only to the hour, day or
#   week, so a video first read while it shows one of those was stored up to a day (a week) off (capture-lag audit,
#   15 Oct: yt_* lags of exactly 1442, 2882, 20162 min); such a row takes the player's publishDate (youtube_exact).
YT_FINE = re.compile(r"\d+\s*(?:秒|分鐘|second|minute)", re.I)
YT_PLAYER = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
YT_CLIENT = {"clientName": "WEB", "clientVersion": "2.20251010.00.00", "hl": "zh-HK"}
YT_VID = re.compile(r"(?:[?&]v=|/shorts/|youtu\.be/)([\w-]{11})")
YT_INFO_MAX = 40  # player reads per call (a listing, a search answer): the rest of a backlog waits for the next poll
YT_INFO_DAYS = 3  # an uploads page lists ~100 videos back over weeks; the poller keeps 2 days (app.MAX_AGE)


def youtube_recent(rows):
    """the uploads-page rows whose relative time is within YT_INFO_DAYS (the ones the stream keeps)"""
    since = datetime.now(HKT) - timedelta(days=YT_INFO_DAYS)
    return [r for r in rows if isinstance(parse_time(r.get("time")), datetime) and parse_time(r.get("time")) >= since]


def youtube_fine(t):
    """is this relative time exact to the minute ("N 分鐘前", "N 秒前")?"""
    return bool(YT_FINE.search(t or ""))


def youtube_player_time(d):
    """a player answer → the video's publish time (aware, HKT), or None"""
    m = ((d or {}).get("microformat") or {}).get("playerMicroformatRenderer") or {}
    t = m.get("publishDate") or m.get("uploadDate")
    try:
        return datetime.fromisoformat(t).astimezone(HKT) if t else None
    except ValueError:
        return None


def youtube_player_text(d):
    """a player answer → the video's whole description ("" when it has none), or None when the answer has no
    videoDetails (an error answer)"""
    vd = (d or {}).get("videoDetails")
    if not isinstance(vd, dict):
        return None
    return (vd.get("shortDescription") or "").strip()


def youtube_player(vid):
    """one video's player answer → {"published": aware datetime or None, "text": description or None}, or None when
    YouTube didn't answer"""
    try:
        req = urllib.request.Request(YT_PLAYER, data=json.dumps({"videoId": vid, "context": {"client": YT_CLIENT}}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
    except Exception:  # noqa: BLE001: the listing's own data stands
        return None
    return {"published": youtube_player_time(d), "text": youtube_player_text(d)}


def youtube_info(rows):
    """{url: {"published": aware datetime or None, "text": description or None}} for the rows (dicts with a YouTube
    url, newest first) whose video the corpus doesn't hold with a text: the player answer, once per video (kv
    ytinfo:<id>, kept also when the description is empty), at most YT_INFO_MAX new reads per call"""
    vids = {}
    for r in rows:
        m = YT_VID.search(r.get("url") or "")
        if m:
            vids.setdefault(r["url"], m.group(1))
    if not vids:
        return {}
    c = corpus.db()
    have = {x[0] for x in c.execute("SELECT url FROM posts WHERE url = ANY(%s) AND coalesce(btrim(text), '') <> ''",
                                    (list(vids),))}
    todo = {u: v for u, v in vids.items() if u not in have}
    cached = {x[0][7:]: x[1] for x in c.execute("SELECT k, v FROM kv WHERE k = ANY(%s)",
                                                (["ytinfo:" + v for v in todo.values()],))}
    out, reads = {}, 0
    for u, v in todo.items():
        got = cached.get(v)
        if got is None:
            if reads >= YT_INFO_MAX:
                continue
            reads += 1
            d = youtube_player(v)
            if d is None:
                continue
            got = {"published": d["published"].isoformat() if d["published"] else None, "text": d["text"]}
            corpus.kv_set("ytinfo:" + v, got)
        out[u] = {"published": datetime.fromisoformat(got["published"]) if got.get("published") else None,
                  "text": got.get("text") or None}
    return out


def youtube_exact(rows, info=None):
    """{url: exact publish time} for the rows whose relative time is coarse (info: youtube_info's answer for them)"""
    info = youtube_info(rows) if info is None else info
    return {r["url"]: info[r["url"]]["published"] for r in rows
            if r.get("url") in info and info[r["url"]]["published"] and r.get("time") and not youtube_fine(r["time"])
            and parse_relative(r["time"])}


def youtube_fix_dates():
    """correct the stored YouTube channel posts whose publish time came from a coarse relative time (first seen 55+
    minutes after it): → how many changed"""
    rows = corpus.db().execute("""SELECT id, url FROM posts WHERE thread IS NULL AND (sid LIKE 'yt\\_%%' OR sid = 'rthk_youtube')
                                  AND published ~ '^\\d{4}-' AND date_only IS NOT TRUE
                                  AND seen::timestamptz - published::timestamptz >= interval '55 minutes' ORDER BY id""").fetchall()
    n = 0
    for r in rows:
        m = YT_VID.search(r["url"])
        d = (youtube_player(m.group(1)) or {}).get("published") if m else None
        if d and corpus.set_published(r["id"], d.astimezone(HKT).isoformat(timespec="minutes"), replace=True):
            n += 1
    return n


# the YouTube video posts whose text is the video's description (a regex the item page's willRead uses too)
YT_TEXT_SID = r"^(yt_.*|rthk_youtube|tikhub_youtube|watch_youtube)$"


def youtube_due(x):
    """will youtube_fill_text still read this post's description? (a YouTube video post, not a comment, no text,
    never tried): fulltext_fetch.will_read"""
    return (x.get("cached_at") is None and not x.get("thread") and not (x.get("text") or "").strip()
            and bool(re.match(YT_TEXT_SID, x.get("sid") or "")) and bool(YT_VID.search(x.get("url") or "")))


def youtube_fill_text(seconds=None):
    """the stored YouTube video posts without a text and never tried (youtube_due), newest first: each gets its
    description from the player answer (youtube_info's kv cache first; tikhub_youtube and watch_youtube posts as
    title + description, like their collectors), "" when the video has none, which marks it tried (cached_at). A
    video the player doesn't answer for waits for the next run. Runs in the `fulltext` job (at most `seconds`).
    → how many were filled with a text"""
    end = time.monotonic() + seconds if seconds else None
    rows = corpus.db().execute(f"""SELECT id, url, sid, title FROM posts WHERE thread IS NULL AND sid ~ %s
                                  AND coalesce(btrim(text), '') = '' AND cached_at IS NULL
                                  AND {corpus.seen_cond("posts", "fulltext")} ORDER BY id DESC""",
                               (YT_TEXT_SID,)).fetchall()
    n = 0
    for i in range(0, len(rows), YT_INFO_MAX):
        if end and time.monotonic() > end:
            break
        chunk = [r for r in rows[i:i + YT_INFO_MAX] if YT_VID.search(r["url"])]
        info = youtube_info([{"url": r["url"]} for r in chunk])
        for r in chunk:
            got = info.get(r["url"])
            if got is None:
                continue
            desc = got["text"] or ""
            own = r["sid"].startswith("yt_") or r["sid"] == "rthk_youtube"
            corpus.set_text(r["id"], desc if own or not desc else (r["title"] or "") + "\n" + desc)
            n += bool(desc)
    return n


def telegram(text):
    out = []
    for blk in re.split(r'(?=<div class="tgme_widget_message_wrap)', text)[1:]:
        p = re.search(r'data-post="([^"]+)"', blk); t = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', blk, re.S)
        d = re.search(r'<time datetime="([^"]+)"', blk)
        v = re.search(r'<span class="tgme_widget_message_views">([^<]+)', blk)
        if p and t:
            links = re.findall(r'href="(https?://[^"]+)"', t.group(1))
            body = paragraphs(t.group(1))
            # every link of the message stays in its text (a word linking an article loses its href in paragraphs():
            # follow.py follows them once the post reaches a stream)
            extra = [html.unescape(l) for l in dict.fromkeys(links) if html.unescape(l) not in body]
            out.append({"title": clean(t.group(1))[:200], "url": links[0] if links else "https://t.me/" + p.group(1), "time": d.group(1) if d else "", "section": "",
                        "text": "\n".join([body] + extra).strip(), "engagement": {"views": count(v.group(1))} if v and count(v.group(1)) is not None else None})
    return out[::-1]


# ---- HTML patterns (all tested 2026-10-12) ----
P = {
    "oncc": rx(r"<option value='(?P<url>/content/(?P<section>[^/']+)/odn-(?P<time>\d{8})[^']*)'>(?P<title>[^<]+)"),
    "hkej_daily": rx(r"<option value='(?P<url>/dailynews/(?P<section>[a-z_]+)/article/\d+/[^']*)'>(?P<title>[^<]+)</option>"),
    "hkej_instant": rx(r'<h3><a href="(?P<url>/instantnews/[a-z_]+/article/\d+/[^"]*)">(?P<title>[^<]+)</a></h3>(?:(?!<h3>).)*?<span class="hkej_toc_top2_2014"><a href="[^"]*">(?P<section>[^<]+)</a></span><span class="hkej_toc_top2_timeStamp_2014">(?P<time>[0-9:]+)</span>'),
    "wwp": rx(r'<a href="(?P<url>https://www\.wenweipo\.com/a/(?P<time>\d{6}/\d{2})/[^"]+\.html)" title="(?P<title>[^"]+)"'),
    "tkp": rx(r'href="(?P<url>https://epaper\.tkww\.hk/a/(?P<time>\d{6}/\d{2})/[^"]+\.html)" title="(?P<title>[^"]+)"'),
    "tkww": rx(r'href="(?P<url>https://www\.tkww\.hk/a/(?P<time>\d{6}/\d{2})/[^"]+\.html)" title="(?P<title>[^"]+)"'),
    "hkcd": rx(r'<a href="(?P<url>/hkcdweb/content/(?P<time>\d{4}/\d{2}/\d{2})/content_\d+\.html)"[^>]*>(?P<title>[^<]{4,})'),
    "am730": rx(r'<a href="(?P<url>/(?P<section>[^/"?]+)/(?P<time>\d{6,})/[^"]*)"[^>]*>(?P<title>[^<]{4,})</a>'),
    "skypost": rx(r"<h6 class='card-tag'><a [^>]*>(?P<section>[^<]+)</a></h6>\s*<h5 class='card-title'><a href='(?P<url>https://skypost\.hk/article/(?P<time>\d+)/)'>(?P<title>[^<]+)"),
    "passion": rx(r'<a[^>]*href="(?P<url>(?:https://www\.passiontimes\.hk)?/article/(?P<time>\d\d-\d\d-\d{4})/\d+)"[^>]*>(?:\s|<(?!/a)[^>]*>)*(?P<title>[^<\s][^<]{5,})'),
    "hket": rx(r'<div class="listing-title">\s*<a\s+href="(?P<url>[^"]+/article/(?P<time>\d+)[^"]*)"[^>]*>(?P<title>.*?)</a>'),
    "fso_speech": rx(r'<tr class="fs"><td><a href="(?P<url>[^"]+)"[^>]*>(?P<title>[^<]+)</a> \((?P<time>[\d.]+)\)'),
    "fstb_speech": rx(r"<div class='year'><span[^>]*>(?P<y>\d{4})年</span><span[^>]*>(?P<time>\d+月\d+日)</span></div>\s*<div class='title'><a href='(?P<url>[^']+)'>(?P<title>[^<]+)"),
}


def lihkg(text):
    """LIHKG's own API (api_v2/thread/category): newest threads with exact create_time"""
    d = json.loads(text)
    return [{"title": x.get("title") or "", "url": f"https://lihkg.com/thread/{x['thread_id']}/page/1",
             "time": x.get("create_time"), "section": (x.get("category") or {}).get("name") or "時事台",
             "author": x.get("user_nickname") or None,
             "engagement": {k: x[f] for k, f in (("replies", "no_of_reply"), ("likes", "like_count"), ("dislikes", "dislike_count"))
                            if isinstance(x.get(f), int)}}
            for x in ((d.get("response") or {}).get("items") or [])]


# news.gov.hk's category feeds, Chinese and English alike (every name the site has; others answer 404): an article
# outside top stories is only in its category's feed (coverage audit 2026-10-14)
NEWSGOV_CATEGORIES = ("finance", "school_work", "health", "environment", "law_order", "infrastructure", "admin",
                      "nationalsecurity", "clarification")
SOURCES = [
    # id, name, url, parser, needs PROXY
    ("rthk_local", "RTHK 香港電台", "https://rthk.hk/rthk/news/rss/c_expressnews_clocal.xml", rss, False),
    ("rthk_finance", "RTHK 香港電台", "https://rthk.hk/rthk/news/rss/c_expressnews_cfinance.xml", rss, False),
    ("rthk_greaterchina", "RTHK 香港電台", "https://rthk.hk/rthk/news/rss/c_expressnews_greaterchina.xml", rss, False),
    ("rthk_international", "RTHK 香港電台", "https://rthk.hk/rthk/news/rss/c_expressnews_cinternational.xml", rss, False),
    ("rthk_sport", "RTHK 香港電台", "https://rthk.hk/rthk/news/rss/c_expressnews_csport.xml", rss, False),
    # RTHK English (2026-10-14): every English express-news feed, ~35 stories a day; the <description> is the story
    ("rthk_en_local", "RTHK English", "https://rthk.hk/rthk/news/rss/e_expressnews_elocal.xml", rss, False),
    ("rthk_en_finance", "RTHK English", "https://rthk.hk/rthk/news/rss/e_expressnews_efinance.xml", rss, False),
    ("rthk_en_greaterchina", "RTHK English", "https://rthk.hk/rthk/news/rss/e_expressnews_egreaterchina.xml", rss, False),
    ("rthk_en_international", "RTHK English", "https://rthk.hk/rthk/news/rss/e_expressnews_einternational.xml", rss, False),
    ("rthk_en_sport", "RTHK English", "https://rthk.hk/rthk/news/rss/e_expressnews_esport.xml", rss, False),
    ("newsgov_top", "政府新聞網", "https://www.news.gov.hk/tc/common/html/topstories.rss.xml", rss, False),
] + [("newsgov_" + c, "政府新聞網", f"https://www.news.gov.hk/tc/categories/{c}/html/articlelist.rss.xml", rss, False)
     for c in NEWSGOV_CATEGORIES] + [
    # news.gov.hk English: top stories and every category feed
    ("newsgov_en_top", "news.gov.hk English", "https://www.news.gov.hk/en/common/html/topstories.rss.xml", rss, False),
] + [("newsgov_en_" + c, "news.gov.hk English", f"https://www.news.gov.hk/en/categories/{c}/html/articlelist.rss.xml", rss, False)
     for c in NEWSGOV_CATEGORIES] + [
    ("infogov_press", "政府新聞公報", "https://www.info.gov.hk/gia/rss/general_zh.xml", rss, False),
    ("infogov_press_en", "Government press releases (English)", "https://www.info.gov.hk/gia/rss/general_en.xml", rss, False),
    ("oncc", "東方日報", "https://orientaldaily.on.cc/", P["oncc"], False),
    ("hkej_daily_headline", "信報", "https://www1.hkej.com/dailynews/headline", P["hkej_daily"], True),
    ("hkej_daily_hknews", "信報", "https://www1.hkej.com/dailynews/hknews", P["hkej_daily"], True),
    ("hkej_daily_commentary", "信報", "https://www1.hkej.com/dailynews/commentary", P["hkej_daily"], True),
    ("hkej_daily_finnews", "信報", "https://www1.hkej.com/dailynews/finnews", P["hkej_daily"], True),
    ("hkej_instant", "信報", "https://www2.hkej.com/instantnews", P["hkej_instant"], True),
    ("mingpao_pns_yaowen", "明報", "https://news.mingpao.com/rss/pns/s00001.xml", rss, False),
    ("mingpao_pns_ganwen", "明報", "https://news.mingpao.com/rss/pns/s00002.xml", rss, False),
    ("mingpao_pns_editorial", "明報", "https://news.mingpao.com/rss/pns/s00003.xml", rss, False),
    ("mingpao_pns_economy", "明報", "https://news.mingpao.com/rss/pns/s00004.xml", rss, False),
    ("mingpao_pns_views", "明報", "https://news.mingpao.com/rss/pns/s00012.xml", rss, False),
    ("mingpao_pns_china", "明報", "https://news.mingpao.com/rss/pns/s00013.xml", rss, False),
    ("mingpao_ins_all", "明報", "https://news.mingpao.com/rss/ins/all.xml", rss, False),
    ("hk01", "香港01", "https://web-data.api.hk01.com/v2/feed/category/0?bucketId=00000", hk01, False),
    ("stheadline", "星島頭條", "https://www.stheadline.com/rss", rss, False),
    ("hket_inews", "香港經濟日報", "https://inews.hket.com/", P["hket"], False),
    ("wenweipo", "香港文匯報", "https://www.wenweipo.com/", P["wwp"], False),
    ("takungpao_epaper", "大公報", "https://www.takungpao.com/", P["tkp"], False),
    ("tkww", "大公文匯網", "https://www.tkww.hk/", P["tkww"], False),
    ("hkcd", "香港商報", "https://www.hkcd.com/", P["hkcd"], False),
    ("am730", "am730", "https://www.am730.com.hk/", P["am730"], False),
    ("skypost", "晴報", "https://skypost.hk/", P["skypost"], False),
    ("metrohk", "都市日報", "https://metrohk.com.hk/feed.xml", rss, False),
    ("scmp_hk", "南華早報", "https://www.scmp.com/rss/2/feed", rss, False),
    ("cr881903", "商業電台 881903", "https://www.881903.com/news/recent", cr881, False),
    ("metroradio", "新城電台", "https://www.metroradio.com.hk/news/", metroradio, False),
] + [("metroradio_" + sec, "新城電台", f"https://www.metroradio.com.hk/news/{sec}.aspx", metroradio, False)
     for sec in ("local", "china", "international", "finance", "eng")] + [
    ("now_local", "NOW 新聞", "https://newsapi1.now.com/pccw-news-api/api/getNewsListv2?category=119&pageNo=1&pageSize=20", now, False),
    ("now_finance", "NOW 新聞", "https://newsapi1.now.com/pccw-news-api/api/getNewsListv2?category=121&pageNo=1&pageSize=20", now, False),
    ("now_intl", "NOW 新聞", "https://newsapi1.now.com/pccw-news-api/api/getNewsListv2?category=120&pageNo=1&pageSize=20", now, False),
    ("now_ent", "NOW 新聞", "https://newsapi1.now.com/pccw-news-api/api/getNewsListv2?category=500&pageNo=1&pageSize=20", now, False),
    ("now_life", "NOW 新聞", "https://newsapi1.now.com/pccw-news-api/api/getNewsListv2?category=501&pageNo=1&pageSize=20", now, False),
    ("now_tech", "NOW 新聞", "https://newsapi1.now.com/pccw-news-api/api/getNewsListv2?category=502&pageNo=1&pageSize=20", now, False),
    ("now_sports", "NOW 新聞", "https://sportsapi.now.com/api/getNewsList?pageSize=20&pageNo=1&searchTagsKey=allSportsSearchTags", now_sports, False),
    ("now_telegram", "NOW 新聞", "https://t.me/s/nowtv_news", telegram, False),
    ("tvb", "TVB 無綫新聞", "https://news.tvb.com/app/public/homepage/pc?lang=zh_hk&limit=10", tvb, False),
    ("icable", "有線新聞", "https://www.i-cable.com/feed", rss, False),
    ("etnet_editor", "經濟通", "https://www.etnet.com.hk/www/tc/news/rss.php?section=editor&fid=538467", rss, False),
    # every story of the day (~450; busiest hour ~60), two pages of 40 each poll
    ("etnet_latest", "經濟通", "https://www.etnet.com.hk/www/tc/news/categorized_news.php?page=1", etnet_latest, False),
    ("etnet_latest_p2", "經濟通", "https://www.etnet.com.hk/www/tc/news/categorized_news.php?page=2", etnet_latest, False),
    ("passiontimes", "熱血時報", "https://www.passiontimes.hk/", P["passion"], False),
    ("rthk_youtube", "RTHK 香港電台 (YouTube)", "https://www.youtube.com/playlist?list=UU6of7UYhctnYmqABjUqzuxw", youtube, False),
    ("fso_blog", "財政司司長辦公室", "https://www.fso.gov.hk/rss/fsblog_rss_t.xml", rss, False),
    ("fso_speech", "財政司司長辦公室", "https://www.fso.gov.hk/chi/speech.htm", P["fso_speech"], False),
    ("fstb_speech", "財經事務及庫務局", "https://www.fstb.gov.hk/tc/news/speech/index.php", P["fstb_speech"], False),
    ("hkma_press", "金管局", "https://www.hkma.gov.hk/chi/other-information/rss/rss_press-release.xml", rss, False),
    ("hkma_speech", "金管局", "https://www.hkma.gov.hk/chi/other-information/rss/rss_speeches.xml", rss, False),
    ("sfc_press", "證監會", "https://www.sfc.hk/TC/RSS-Feeds/Press-releases", rss, False),
    ("gnews_discuss", "香港討論區 (via Google News)", "https://news.google.com/rss/search?q=site:discuss.com.hk+when:2d&hl=zh-HK&gl=HK&ceid=HK:zh-Hant", rss, False),
    # K-Matrix parity (2026-10-12): the YouTube channels and Telegram channels its alerts link, and LIHKG
    ("yt_5JfM27CQ", "無綫新聞 TVB NEWS Official (YouTube)", "https://www.youtube.com/playlist?list=UU_ifDTtFAcsj-wJ5JfM27CQ", youtube, False),
    ("yt_LyP6K8lQ", "政府新聞處 Information Services Department, HKSARG (YouTube)", "https://www.youtube.com/playlist?list=UUVdt-K3UythSHVYLyP6K8lQ", youtube, False),
    ("yt_82TO_zRw", "memehongkong (YouTube)", "https://www.youtube.com/playlist?list=UUu_YquoQYKR3GpP82TO-zRw", youtube, False),
    ("yt_mtGqDPzQ", "香港商報 (YouTube)", "https://www.youtube.com/playlist?list=UUw6-y9jGJbfw6jLmtGqDPzQ", youtube, False),
    ("yt_zXwy_SGg", "香港01 (YouTube)", "https://www.youtube.com/playlist?list=UUTxyBu9VWUq5LXezXwy_SGg", youtube, False),
    ("yt_GCV4tYCA", "星島頭條 (YouTube)", "https://www.youtube.com/playlist?list=UUSyH9MRwMn-kdq5GCV4tYCA", youtube, False),
    ("yt_0Cl_kTTg", "通視 (YouTube)", "https://www.youtube.com/playlist?list=UU2P4BOQlye13d9l0Cl-kTTg", youtube, False),
    ("yt__f9BTUkA", "葛珮帆EQ (YouTube)", "https://www.youtube.com/playlist?list=UUBNVE4F5PSRYcA__f9BTUkA", youtube, False),
    ("yt_alK1BjCQ", "升旗易日報 (YouTube)", "https://www.youtube.com/playlist?list=UUIKhrdByqxGYzRPalK1BjCQ", youtube, False),
    ("yt_aKx_BoRw", "TVB (official) (YouTube)", "https://www.youtube.com/playlist?list=UUD2SNRlEjxJODlwaKx-BoRw", youtube, False),
    ("yt__3Bi0a2Q", "dotdotnews點新聞 (YouTube)", "https://www.youtube.com/playlist?list=UUcrnNcl12onXCeS_3Bi0a2Q", youtube, False),
    ("yt_A_xE2flA", "華記正能量Alex Yeung channel (YouTube)", "https://www.youtube.com/playlist?list=UUjQiROcVtb0ZqPpA-xE2flA", youtube, False),
    ("yt_SB9yB62A", "LION哥的投資世界 | LION GOR'S INVESTMENT WORLD (YouTube)", "https://www.youtube.com/playlist?list=UUgyTp-zlMz6D-6BSB9yB62A", youtube, False),
    ("tg_rthk", "RTHK即時新聞 (Telegram)", "https://t.me/s/rthk_new_c", telegram, False),
    ("tg_icable", "有線新聞 (Telegram)", "https://t.me/s/icable_news", telegram, False),
    ("tg_mingpao", "明報卽時新聞 (Telegram)", "https://t.me/s/mingpao_news", telegram, False),
    ("lihkg_current", "LIHKG 時事台", "https://lihkg.com/api_v2/thread/category?cat_id=5&page=1&count=60&type=now", lihkg, False),
]


# 香港討論區 links a thread in many forms: viewthread.php?tid=N (&page=2, &extra=…), the mobile
# index.php?action=thread&tid=N, redirect.php?tid=N&goto=lastpost, the printable view. Google News (gnews_discuss) links
# any of them, so each page of a thread was its own post, and none had a text: the full-text readers skip forum posts and
# the replies job files the thread under its archiver address (2026-10-20: 583 of 590 gnews_discuss posts empty, while
# the same threads' archiver posts held the opening post). A thread link becomes that archiver address (discuss_url).
DISCUSS_THREAD = re.compile(r"^https?://(?:(?:www|m)\.)?discuss\.com\.hk/(?:viewthread|index|redirect|archiver/?)(?:\.php)?\?"
                            r"(?:[^#]*?[?&])?tid[-=](\d+)", re.I)


def discuss_url(url):
    """a 香港討論區 thread link in any form → the thread's archiver address (where replies_fetch files it), else None"""
    m = DISCUSS_THREAD.match(url or "")
    if not m or re.search(r"#c\d", url) or (re.search(r"[?&]action=(?!thread|printable)", url) and "viewthread" not in url):
        return None
    return f"https://www.discuss.com.hk/archiver/?tid-{m.group(1)}.html"


def discuss_fill_text():
    """the stored gnews_discuss posts without a text (filed under Google News's links before discuss_url) get their
    thread's opening post from the archiver post the replies job filed: → how many filled"""
    c = corpus.db()
    rows = c.execute("""SELECT id, url FROM posts WHERE sid = 'gnews_discuss' AND thread IS NULL
                        AND coalesce(btrim(text), '') = ''""").fetchall()
    n = 0
    for r in rows:
        a = discuss_url(r["url"])
        got = c.execute("SELECT text, author FROM posts WHERE url = %s AND coalesce(btrim(text), '') <> ''",
                        (a,)).fetchone() if a else None
        if got:
            corpus.set_text(r["id"], got["text"], author=got["author"])
            n += 1
    return n


DISCUSS_RECENT_TIDS = 1800  # discuss.com.hk thread ids rise ~850 a day: keep threads within ~2 days of the newest


def discuss_search(queries):
    """香港討論區: its mobile site's keyword search, newest first, one page per query, through RESI_PROXY (discuss.com.hk
    bans datacenter networks; a Hong Kong residential IP from IPRoyal passes). The results carry no dates, so threads
    older than ~2 days (by thread id, which rises steadily) are dropped. Links use the archiver form K-Matrix posts.
    Requests go through replies_fetch.Proxied on the replies job's route (replies_fetch.discuss_route: the server's
    IP, the ISP IP, else the replies job's sticky IPRoyal session: an IP the site walls (HTTP 202 reCAPTCHA, 403: 15
    Oct 2026 13:29-14:00 HKT every run failed on one 403 from a rotating IP) moves to a new session); a query that
    still fails is skipped (the rest are kept), and only all of them failing is a failure."""
    import replies_fetch
    route = replies_fetch.discuss_route()
    # its bytes count under the site "discuss" (or the ISP IP's), with the replies job's
    px = replies_fetch.Proxied("discuss", direct=route == "direct", **({"isp": True} if route == "isp" else {}))
    found, extra, failed = {}, {}, []
    for q in queries:
        try:
            text = px.get("https://m.discuss.com.hk/index.php?action=search&do=submit&orderby=newest&srchtxt=" + urllib.parse.quote(q),
                          headers={"User-Agent": replies_fetch.MUA}, walled=replies_fetch.discuss_walled)
        except resi.ProxyOut:
            raise  # IPRoyal out of traffic: the other queries would meet the same 402
        except Exception as e:
            failed.append(f"{q}: {e!r}"[:120])
            continue
        finally:
            px.account()
        for tid, title in re.findall(r'href="https://m\.discuss\.com\.hk/index\.php\?action=thread&tid=(\d+)[^"]*"[^>]*>([^<]{4,})</a>', text):
            found[int(tid)] = clean(title).lstrip("\u200b")
        for blk in text.split('<div class="result">')[1:]:  # each result: the thread's author and 回覆/查看 counts
            tid = re.search(r"action=thread&tid=(\d+)", blk)
            au = re.search(r'action=viewpro[^>]*>([^<]+)</a>', blk)
            rv = re.search(r"回覆：([\d,]+)\s*/\s*查看：([\d,]+)", blk)
            if tid:
                extra[int(tid.group(1))] = {"author": clean(au.group(1)) if au else None,
                                            "engagement": {"replies": count(rv.group(1)), "views": count(rv.group(2))} if rv else None}
    if failed and len(failed) == len(queries):
        raise RuntimeError("every query failed: " + "; ".join(failed[:3]))
    if not found:
        return []
    newest = max(found)
    return [dict({"sid": "discuss", "source": "香港討論區", "section": None, "title": t, "published": None,
                  "url": f"https://www.discuss.com.hk/archiver/?tid-{tid}.html"}, **{k: v for k, v in extra.get(tid, {}).items() if v})
            for tid, t in sorted(found.items()) if tid >= newest - DISCUSS_RECENT_TIDS]


# magazines publish weekly or monthly: their latest issue counts as current (days; everything else: the app's 2 days)
MAX_AGE_DAYS = {"yzzk": 10, "mpmonthly": 40, "mpweekly": 10, "citymagazine": 40, "jdonline_hk": 10, "jdonline_fin": 10,
                "imoney_paper": 10, "edigest": 10, "capital": 10}


def browser_file(doc="browser.json", line="browser"):
    """成報 / 亞洲週刊 / 明報月刊 / 獨立媒體 from the document browser_fetch.py (a real browser, every 30 min) writes (corpus.file_get) → (items, status);
    a file older than 2 h counts as a failure for every site and for the job itself (status "browser")"""
    d = corpus.file_get(doc)
    stale = datetime.now(HKT) - datetime.fromisoformat(d["fetched"]) > timedelta(hours=2)
    items, status = [], {}
    for sid, site in d["sites"].items():
        if site.get("off") and not stale:  # a site on yycstream while IPRoyal is out of traffic: paused, not failing
            status[sid] = {"ok": False, "items": 0, "error": site.get("error"), "off": site["off"]}
            continue
        if stale or not site["ok"]:
            status[sid] = {"ok": False, "items": 0, "error": f"{doc} stale (fetched {d['fetched']})" if stale else site["error"]}
            continue
        status[sid] = {"ok": True, "items": len(site["items"]), "error": None}
        for it in site["items"]:
            items.append(dict({"sid": sid, "source": site["name"], "section": it.get("section"), "title": it["title"],
                               "url": it["url"], "published": parse_time(it.get("published")), "max_age_days": MAX_AGE_DAYS.get(sid)},
                              **extras(sid, it)))
    status[line] = {"ok": not stale, "items": len(items),  # the job's own line ("browser job" on the monitor)
                    "error": f"{doc} stale (fetched {d['fetched']})" if stale else None}
    return items, status


def instagram_file():
    """Instagram accounts from the file ig_fetch.py (hourly) writes → (items, status); older than 3 h = failed"""
    d = corpus.file_get("instagram.json")
    if datetime.now(HKT) - datetime.fromisoformat(d["fetched"]) > timedelta(hours=3):
        return [], {"instagram": {"ok": False, "items": 0, "error": f"instagram.json stale (fetched {d['fetched']})"}}
    items = [dict({"sid": "instagram", "source": it["source"], "section": "Instagram", "title": it["title"], "url": it["url"],
                   "published": parse_time(it["published"]), "max_age_days": None}, **extras("instagram", it)) for it in d["items"]]
    st = {"ok": d["ok"], "items": len(items), "error": d.get("error")}
    if d.get("off"):  # the proxy out of traffic (resi.py): paused, not failing
        st["off"] = d["off"]
    return items, {"instagram": st}


def tikhub_file():
    """Social-platform keyword search from the file tikhub_fetch.py (yyc-tikhub timer, hourly) writes → (items, status);
    one status line per platform; a file older than 4 h = every platform failed"""
    d = corpus.file_get("tikhub.json")
    stale = datetime.now(HKT) - datetime.fromisoformat(d["fetched"]) > timedelta(hours=4)
    status = {}
    for key, c in d["checks"].items():
        sid = "tikhub_" + key.split("|")[0]
        st = status.setdefault(sid, {"ok": False, "items": 0, "error": None})
        st["ok"] = st["ok"] or (c["ok"] and not stale)
        st["items"] += c["items"]
        if not c["ok"] and not st["error"]:
            st["error"] = c["error"]
    for st in status.values():
        if stale:
            st["error"] = f"tikhub.json stale (fetched {d['fetched']})"
        elif st["ok"]:
            st["error"] = None
    # the file itself (also clears an error left from a poll before the file existed)
    status["tikhub"] = {"ok": not stale, "items": 0 if stale else len(d["items"]),
                        "error": f"tikhub.json stale (fetched {d['fetched']})" if stale else None}
    if stale:
        return [], status
    items = [dict({"sid": "tikhub_" + it["key"].split("|")[0], "source": it["source"], "section": it["section"],
                   "title": it["title"], "url": it["url"], "published": parse_time(it["published"]), "max_age_days": None},
                  **extras("tikhub", it), **{k: it[k] for k in ("mirror_of",) if it.get(k)})
             for it in d["items"]]
    return resolve_mirrors(items), status


# ---- mirror posts (Deyao, 2026-10-20): a bot's re-post of an outlet's article (tikhub_fetch.reddit_mirror) ----

MIRROR_PREFIX = re.compile(r"^\s*\[[^\]]{1,40}\]\s*[-–—:]\s*")  # AutoNewspaper's "[Op-Ed] - ", "[World] - "
MIRROR_SUFFIX = re.compile(r"\s+\|\s+([^|]{3,80})$")  # r/AutoNewspaper's " | South China Morning Post"


def resolve_mirrors(items):
    """the original article wins over its mirror: an item with "mirror_of" goes through link following's routing
    (follow.mirror, the same as every followed link, recorded there): an article in the corpus (by url / url_key) or
    earlier in this batch drops it; one of an outlet we read becomes the article itself: its URL, the outlet's sid
    and name, the headline without the bot's "[Section] - " / " | <outlet>", and no publish time (a bot re-posts old
    articles as new: the page gives the time); a site no source reads, or a link that isn't an article, is listed on
    the monitor (follow.monitor) and the bot's post dropped. Other items pass unchanged"""
    if not any(it.get("mirror_of") for it in items):
        return items
    import follow
    out, taken = [], set()
    for it in items:
        link = it.get("mirror_of")
        if not link:
            out.append(it)
            continue
        r = follow.mirror(it["url"], link)
        if not r or r["status"] != "new" or corpus.url_key(r["url"]) in taken:
            continue
        taken.add(corpus.url_key(r["url"]))
        title = MIRROR_PREFIX.sub("", it["title"] or "")
        m = MIRROR_SUFFIX.search(title)
        if m:
            title = title[:m.start()].strip()
        sid, name = r["outlet"]
        out.append({"sid": sid, "source": name, "section": None, "title": title, "url": r["url"], "published": None,
                    "max_age_days": None})
    return out


def facebook_file():
    """Facebook pages from the file fb_apify.py (Apify, every 30 min) writes → (items, status); older than 2 h = failed"""
    d = corpus.file_get("facebook.json")
    if datetime.now(HKT) - datetime.fromisoformat(d["fetched"]) > timedelta(hours=2):
        return [], {"facebook": {"ok": False, "items": 0, "error": f"facebook.json stale (fetched {d['fetched']})"}}
    items = [dict({"sid": "facebook", "source": it["source"], "section": "Facebook", "title": it["title"], "url": it["url"],
                   "published": parse_time(it["published"]), "max_age_days": None}, **extras("facebook", it)) for it in d["items"]]
    st = {"ok": d["ok"], "items": len(items), "error": d.get("error")}
    if d.get("off"):  # the proxy out of traffic (resi.py): paused, not failing
        st["off"] = d["off"]
    return items, {"facebook": st}


def sites_file():
    """The catalogue sites (sites.json: Macau's papers and websites, Hong Kong's magazines, forums, websites and blogs)
    from the document sites_fetch.py (every 10 min) writes → (items, status): one status line per site (its id) and
    the job's own line "sites"; a file older than 1 h = every site failed"""
    d = corpus.file_get("sites.json")
    stale = datetime.now(HKT) - datetime.fromisoformat(d["fetched"]) > timedelta(hours=1)
    why = f"sites.json stale (fetched {d['fetched']})"
    status = {}
    for sid, st in (d.get("sites") or {}).items():
        if st.get("off"):
            status[sid] = {"ok": False, "items": 0, "error": st.get("error"), "off": st["off"]}
        else:
            status[sid] = {"ok": bool(st.get("ok")) and not stale, "items": st.get("items") or 0,
                           "error": why if stale else st.get("error")}
    status["sites"] = {"ok": not stale, "items": 0 if stale else len(d.get("items") or []), "error": why if stale else None}
    if stale:
        return [], status
    names = {sid: st.get("name") or sid for sid, st in (d.get("sites") or {}).items()}
    items = [dict({"sid": it["sid"], "source": names.get(it["sid"], it["sid"]), "section": it.get("section") or None,
                   "title": it["title"], "url": it["url"], "published": parse_time(it.get("published")),
                   "max_age_days": None}, **extras(it["sid"], it, names.get(it["sid"]))) for it in d.get("items") or []]
    return items, status


def watch_file():
    """The watched accounts' posts (watchlist.json; YouTube, X, TikTok) from the document
    watch_fetch.py (every 10 min) writes → (items, status): one status line per platform (watch_<platform>); a file
    older than 1 h = every platform failed. A platform whose every account is off on purpose (watch_fetch's off_by: the
    safety stop) is OFF, {"ok": None, "off": reason}: not a failing source
    (Deyao, 2026-10-15); once its accounts are read again it is judged as before."""
    d = corpus.file_get("watch.json")
    stale = datetime.now(HKT) - datetime.fromisoformat(d["fetched"]) > timedelta(hours=1)
    status, off = {}, {}
    for st in d["accounts"].values():
        sid = "watch_" + st["platform"]
        s = status.setdefault(sid, {"ok": False, "items": 0, "error": None, "accounts": 0})
        s["accounts"] += 1
        off.setdefault(sid, []).append(st.get("off_by"))
        if st.get("ok") and not stale:
            s["ok"] = True
        elif st.get("off") and not s["error"]:
            s["error"] = st["off"]
        elif st.get("ok") is False and not s["error"]:
            s["error"] = f"{st.get('name')}: {st.get('error')}"
    for sid, why in off.items():
        if all(why) and not status[sid]["ok"]:
            status[sid].update(ok=None, off=why[0], error=None)
    for s in status.values():
        if s.get("off"):
            continue
        if stale:
            s["error"] = f"watch.json stale (fetched {d['fetched']})"
        elif s["ok"]:
            s["error"] = None
    if stale:
        return [], status
    items = []
    for it in d["items"]:
        sid = "watch_" + it["platform"]
        status[sid]["items"] += 1
        items.append(dict({"sid": sid, "source": it["source"], "section": it["author"], "title": it["title"],
                           "url": it["url"], "published": parse_time(it["published"]), "max_age_days": None},
                          **extras("watch", it)))
    return items, status


GNEWS_EDITIONS = {"zh": "hl=zh-HK&gl=HK&ceid=HK:zh-Hant", "en": "hl=en-HK&gl=HK&ceid=HK:en"}


def gnews_queries(queries, edition="zh"):
    """Google News search, one feed per query: the streams' news_queries on the zh-HK edition (gnews_N), their
    news_queries_en on the English edition en-HK (gnews_en_N), where English-only outlets surface"""
    prefix = "gnews_" if edition == "zh" else f"gnews_{edition}_"
    return [(prefix + str(i), "Google News", "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) +
             "+when:1d&" + GNEWS_EDITIONS[edition], rss, False) for i, q in enumerate(queries)]


DATE_FORMATS = [  # date-only shapes from the HTML sources' URLs and lists (a corpus.DateOnly: the day, no time)
    (r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", "ymdHMS"),  # 新城 NewsId
    (r"(\d{4})(\d{2})(\d{2})", "ymd"),                            # 東方 odn-YYYYMMDD
    (r"(\d{4})(\d{2})/(\d{2})", "ymd"),                           # 文匯/大公 YYYYMM/DD
    (r"(\d{4})/(\d{2})/(\d{2})", "ymd"),                          # 商報 YYYY/MM/DD
    (r"(\d{2})-(\d{2})-(\d{4})", "mdy"),                          # 熱血時報 MM-DD-YYYY (/article/10-14-2026/…)
    (r"(\d{1,2})\.(\d{1,2})\.(\d{4})", "dmy"),                  # FSO speeches D.M.YYYY
    (r"(\d{4})年(\d{1,2})月(\d{1,2})日", "ymd"),                   # FSTB YYYY年M月D日
    (r"(\d{4})\s+年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", "ymd"),        # 2026 年 10 月 16 日 (HKSI)
    (r"(\d{1,2})-(\d{1,2}),\s*(\d{4})", "mdy"),                  # 10-16,2026 (珠海學院)
    (r"(\d{1,2})/(\d{1,2})/(\d{4})", "dmy"),                     # 20/10/2026: Hong Kong's day/month order
    (r"(\d{4})/(\d{1,2})/(\d{1,2})", "ymd"),                     # 2026/10/20
    (r"(\d{4})\.(\d{1,2})\.(\d{1,2})", "ymd"),                   # 2026.10.20
    (r"(\d{1,2})-(\d{1,2})-(\d{4})", "dmy"),                     # 20-10-2026 (熱血時報's MM-DD-YYYY URLs are matched above)
]
MONTHS = {m.lower(): i for i, m in enumerate("January February March April May June July August September October November "
                                                "December".split(), 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9
WORD_DATE = [  # the catalogue's English pages (2026-10-20): "6 Oct 2026", "19 August 2026", "October 20, 2026"
    (re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})"), "dmy"),
    (re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})"), "mdy"),
]


def parse_date_only(t):
    for rx_, order in WORD_DATE:
        m = rx_.fullmatch(t)
        if m:
            g = m.groups()
            d, mon, y = (g[0], g[1], g[2]) if order == "dmy" else (g[1], g[0], g[2])
            mn = MONTHS.get(mon.lower())
            if mn:
                try:
                    return corpus.DateOnly(int(y), mn, int(d), tzinfo=HKT)
                except ValueError:
                    return None
    for rx_, order in DATE_FORMATS:
        m = re.fullmatch(rx_, t)
        if m:
            g = dict(zip(order, m.groups())) if order != "ymdHMS" else dict(zip("ymdHMS", m.groups()))
            try:
                if order != "ymdHMS":
                    return corpus.DateOnly(int(g["y"]), int(g["m"]), int(g["d"]), tzinfo=HKT)
                return datetime(int(g["y"]), int(g["m"]), int(g["d"]), int(g["H"]), int(g["M"]), int(g["S"]), tzinfo=HKT)
            except ValueError:
                continue  # not a date in this order (20-10-2026 is no MM-DD): the next shape
    if re.fullmatch(r"\d{1,2}:\d{2}", t):  # HKEJ instant: today's HH:MM
        h, mi = map(int, t.split(":"))
        now = datetime.now(HKT)
        d = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        return d - timedelta(days=1) if d > now + timedelta(minutes=5) else d
    return None


REL_UNITS = {"second": 1, "秒": 1, "minute": 60, "分鐘": 60, "hour": 3600, "小時": 3600, "day": 86400, "天": 86400,
             "日": 86400, "week": 604800, "週": 604800, "周": 604800, "星期": 604800, "month": 2592000, "個月": 2592000, "year": 31536000, "年": 31536000}


def parse_relative(t):
    """'3 hours ago' / '3小時前' / 'Streamed 2 days ago' → aware datetime"""
    m = re.search(r"(\d+)\s*(second|minute|hour|day|week|month|year|秒|分鐘|小時|天|日|星期|週|周|個月|年)", t)
    if not m or not re.search(r"ago|前", t):
        return None
    return datetime.now(HKT) - timedelta(seconds=int(m.group(1)) * REL_UNITS[m.group(2)])


# a stated time of exactly midnight (to the second) is a day without a time: 金管局's and 政府新聞網's RSS
# ("Wed, 14 Oct 2026 00:00:00 +0800" on every item), 東方日報's sitemap ("T00:00+08:00"), the 大公/文匯 e-papers'
# sitemaps ("T00:00:00Z": the day in UTC), 信報's and 金管局's pages. A post really published at 00:00:00 is shown as
# its day, ordered at the time it was first seen (Claude's inference: rare next to the 37 sources that give only a day)
MIDNIGHT = re.compile(r"(?:T|\s)00:00(?::00(?:\.0+)?)?\s*(?:Z|[+-]\d{2}:?\d{2}|GMT|UTC|HKT)?\s*$", re.I)
MONTHS3 = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
JAVA_DATE = re.compile(r"[A-Z][a-z]{2} ([A-Z][a-z]{2}) (\d{1,2}) (\d{2}):(\d{2}):(\d{2}) (?:HKT|CST) (\d{4})")  # 文匯's JSON-LD


def parse_time(t):
    """the many time shapes the sources give → aware datetime, a corpus.DateOnly when the source gives only the day
    (a date with no time, or a time of exactly midnight: MIDNIGHT), or None when only an article ID is known"""
    if t in (None, ""):
        return None
    d = _parse_time(t)
    if d is None or isinstance(d, corpus.DateOnly) or not isinstance(t, str):
        return d
    t = t.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t) or MIDNIGHT.search(t):
        return corpus.DateOnly.of(d)  # the day as the source wrote it (UTC midnight → that UTC day)
    return d


def _parse_time(t):
    if (m := JAVA_DATE.fullmatch(str(t).strip())) and m.group(1) in MONTHS3:
        return datetime(int(m.group(6)), MONTHS3[m.group(1)], int(m.group(2)), int(m.group(3)), int(m.group(4)),
                        int(m.group(5)), tzinfo=HKT)
    if isinstance(t, str) and (d := parse_relative(t)):
        return d
    if isinstance(t, str) and (d := parse_date_only(t.strip())):
        return d
    if isinstance(t, (int, float)) or (isinstance(t, str) and t.isdigit() and len(t) in (10, 13)):
        t = int(t)
        return datetime.fromtimestamp(t / 1000 if t > 1e12 else t, timezone.utc)
    t = str(t).strip()
    try:
        d = parsedate_to_datetime(t)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)  # "-0000" / "+00:00" (FinanceAsia) come back naive
    except Exception:
        pass
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=HKT)
    except Exception:
        return None


# LIHKG's API: through the one LIHKG client every reader shares (lihkg_api.py: the web app's device headers, one
# pacing, one 429 back-off with the residential fallback). Without the device headers LIHKG answers the cluster IP
# with 429 (2026-10-20)
LIHKG_API = {"lihkg_current"}
READERS = {}  # sid → fn(url) → (text, route): a source with its own routes (sources_extra.linkedin_read)
ROUTE_USED = {}  # sid → the route its reader took on its last read (fetch_all's status "route")


# sources read only up to N bytes: a huge sitemap that lists newest first (Young Post's articles sitemap is 9 MB)
POST_DATA = {}  # sid → the form body of a source read by POST (橙新聞's channel lists answer GET with a 302)
HEAD_BYTES = {"youngpost_sitemap": 200_000}


def fetch_head(url, n, headers=None, timeout=30):
    """the first n bytes of url (uncompressed, decoded; a cut-off last entry is the parser's to skip)"""
    h = {"User-Agent": UA, "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return r.read(n).decode("utf-8", "ignore")


def fetch_source(spec):
    sid, name, url, parser, proxy = spec
    url = url.replace("{now}", str(int(time.time())))  # a cache-busting query for feeds a CDN serves stale (Mingtiandi)
    if sid in LIHKG_API:
        text = lihkg_api.client().get(url)
    elif sid in READERS:
        text, ROUTE_USED[sid] = READERS[sid](url)
    elif sid in HEAD_BYTES:
        text = fetch_head(url, HEAD_BYTES[sid], headers=HEADERS.get(sid))
    elif sid in POST_DATA:
        text = fetch(url, data=POST_DATA[sid], headers=HEADERS.get(sid))
    else:
        text = fetch(url, headers=HEADERS.get(sid), proxy=resi.url() if proxy else None)
    out = []
    rows = list(parser(text))
    info = youtube_info(youtube_recent(rows)) if parser is youtube else {}
    exact = youtube_exact(rows, info) if parser is youtube else {}
    for it in rows:  # a video's post text is its description (the playlist page has none)
        if info.get(it.get("url"), {}).get("text"):
            it["text"] = info[it["url"]]["text"]
    for it in rows:
        title, link = (it.get("title") or "").strip(), (it.get("url") or "").strip()
        link = link.split('"')[0].strip()  # 明報's RSS: <link>…" target="blank</link> (a URL never holds a quote)
        if not title or not link:
            continue
        source, section = name, (it.get("section") or "").strip() or None
        if sid.startswith("gnews") and section:  # Google News: the section field holds the publisher
            source, section = section, None
            title = re.sub(r"\s+-\s+" + re.escape(source) + r"$", "", title)
            title = re.sub(r"^" + re.escape(source) + r"\s*[-－:：|｜]\s*", "", title)  # "香港政府新聞網- …"
        t = it.get("time")
        if it.get("y") and t and "年" not in str(t):  # FSTB: the year comes separately
            t = f"{it['y']}年{t}"
        if parser is youtube and not parse_time(it.get("time")):
            continue  # YouTube rows without a relative time are old uploads: never let them through undated
        out.append(dict({"sid": sid, "source": source, "section": section, "title": title, "url": urllib.parse.urljoin(url, link),
                         "published": exact.get(link) or parse_time(t), "max_age_days": MAX_AGE_DAYS.get(sid)}, **extras(sid, it, source)))
    return out


FULL_TEXT_RSS = ("rthk_", "newsgov_", "infogov_", "fso_blog")  # feeds whose <description> is the whole story
# paywalled outlets whose feed summary is the free text: kept as the post's text until a page read or its
# archive.today copy (paywall.py) gives more (fulltext_fetch.save keeps the longer one)
SUMMARY_RSS = ("ft_", "mingtiandi")


DESK = re.compile(r"editor|編輯|資訊|新聞部|newsroom|新聞網", re.I)  # bylines that name a desk or the outlet, not a person


def extras(sid, it, source=None):
    """the author, text, engagement counts, photos and videos a parser found (only the keys it really gave)"""
    out = {}
    au = it.get("author")
    if au and not DESK.search(au) and not (source and (source in au or au in source)):
        out["author"] = au
    text = it.get("text") or (it.get("desc") if sid.startswith(FULL_TEXT_RSS + SUMMARY_RSS) else None)
    if text:
        out["text"] = text
    eng = {k: v for k, v in (it.get("engagement") or {}).items() if isinstance(v, int)}
    if eng:
        out["engagement"] = eng
    for k in ("images", "clips"):  # the post's own photos (images.py) and videos (media.py), when the platform gives them
        if it.get(k):
            out[k] = it[k]
    if it.get("first_run"):  # published before its collector started watching the account: backlog (corpus.ingest)
        out["first_run"] = True
    return out


# sources fetched less often than every poll (seconds): the daily paper's pages change once a day, and the proxied
# ones cost per GB
EVERY = {"fstb_linkedin": 3600, "hkej_daily_headline": 1800, "hkej_daily_hknews": 1800, "hkej_daily_commentary": 1800, "hkej_daily_finnews": 1800,
         "hkej_instant": 600}
_last = {}
EVERY_SLACK = 60  # a poll this much short of EVERY counts as due (polls start ~300 s apart: EVERY 900 was every 4th poll)
HEADERS = {}  # per source


def scheduled(extra=()):
    """the sources fetch_all reads on a schedule (EVERY): the only ones whose last status a poll carries forward while
    they aren't due. Every other line (the job files' sites and platforms, a reader's own error line) is written by
    each poll afresh, so a line no reader writes any more is dropped, not carried (live 2026-10-15: "browser" kept a
    08:13 Postgres error for hours after browser.json read fine again)"""
    return {sp[0] for sp in list(extra) + list(SOURCES)}


ROUTE_KEEP = 6 * 3600  # seconds a source read over yycstream after its direct read failed stays there, then direct again
_route = {}  # sid → time its direct read failed and yycstream read it (this process; the poller is long-lived)


def escalates(spec):
    """a plain direct GET that may be retried over yycstream hk when it fails (Deyao, 2026-10-21 "fix the failing
    sources": a WARP exit can get 403 from 熱血時報 and others that another exit reads): not a proxied source,
    not LIHKG's API, a head-only or POST read, and never YouTube (YouTube never touches yycstream)"""
    sid, url, proxy = spec[0], spec[2], spec[4]
    return not proxy and sid not in LIHKG_API and sid not in READERS and sid not in HEAD_BYTES and sid not in POST_DATA \
        and "youtube.com" not in (urllib.parse.urlsplit(url).hostname or "") and resi.configured()


def fetch_all(extra=()):
    """→ (items, status): every source due (EVERY) in parallel, each retried once (a plain direct read over yycstream
    hk, kept there for ROUTE_KEEP: escalates()); plus 成報 from its browser job's file"""
    def one(spec):
        sid = spec[0]
        try:
            if time.time() - _route.get(sid, 0) < ROUTE_KEEP and escalates(spec):
                try:
                    return sid, fetch_source(spec[:4] + (True,)), None
                except resi.ProxyOut:
                    raise
                except Exception:
                    _route.pop(sid, None)  # yycstream fails too now: direct again
            try:
                return sid, fetch_source(spec), None
            except resi.ProxyOut:
                raise  # never retried into IPRoyal's 402
            except Exception as e:
                if not escalates(spec):
                    return sid, fetch_source(spec), None
                try:
                    got = fetch_source(spec[:4] + (True,))
                except resi.ProxyOut:  # yycstream out of traffic: the direct read's failure is what stands
                    return sid, [], repr(e)[:200]
                _route[sid] = time.time()
                return sid, got, None
        except resi.ProxyOut as e:
            return sid, [], e
        except Exception as e:
            return sid, [], repr(e)[:200]
    items, status = [], {}
    now = time.time()
    due = [sp for sp in list(extra) + list(SOURCES) if  # extra first: a column's own label wins the URL dedupe
           now - _last.get(sp[0], 0) >= EVERY.get(sp[0], 0) - EVERY_SLACK]
    for sp in due:
        _last[sp[0]] = now
    with cf.ThreadPoolExecutor(12) as ex:
        for sid, got, err in ex.map(one, due):
            if isinstance(err, resi.ProxyOut):  # a pause, not a failure: off until the proxy has traffic again
                status[sid] = {"ok": False, "items": 0, "error": repr(err)[:200], "off": resi.STATUS}
                continue
            status[sid] = {"ok": err is None, "items": len(got), "error": err}
            if err is None and time.time() - _route.get(sid, 0) < ROUTE_KEEP:
                status[sid]["route"] = "yycstream"
            if err is None and sid in ROUTE_USED:
                status[sid]["route"] = ROUTE_USED[sid]
            items += got
    try:
        got, st = browser_file()
        status.update(st)
        items += got
    except Exception as e:
        status["browser"] = {"ok": False, "items": 0, "error": repr(e)[:200]}
    try:
        got, st = browser_file("browser-sites.json", "sites_browser")  # the catalogue's browser sites (sites.json)
        status.update(st)
        items += got
    except LookupError:
        pass  # the sites-browser job has not run yet
    except Exception as e:
        status["sites_browser"] = {"ok": False, "items": 0, "error": repr(e)[:200]}
    try:
        got, st = instagram_file()
        status.update(st)
        items += got
    except Exception as e:
        status["instagram"] = {"ok": False, "items": 0, "error": repr(e)[:200]}
    try:
        got, st = tikhub_file()
        status.update(st)
        items += got
    except Exception as e:
        status["tikhub"] = {"ok": False, "items": 0, "error": repr(e)[:200]}
    try:
        got, st = watch_file()
        status.update(st)
        items += got
    except LookupError:
        pass  # watch_fetch.py has not run yet
    except Exception as e:
        status["watch"] = {"ok": False, "items": 0, "error": repr(e)[:200]}
    try:
        got, st = sites_file()
        status.update(st)
        items += got
    except LookupError:
        pass  # sites_fetch.py has not run yet
    except Exception as e:
        status["sites"] = {"ok": False, "items": 0, "error": repr(e)[:200]}
    try:
        got, st = facebook_file()
        status.update(st)
        items += got
    except Exception as e:
        status["facebook"] = {"ok": False, "items": 0, "error": repr(e)[:200]}
    return items, status


if __name__ == "__main__":
    if sys.argv[1:] == ["youtube-dates"]:  # correct the stored coarse YouTube times (capture-lag audit, 15 Oct)
        print("youtube-dates:", youtube_fix_dates(), "posts corrected")
    elif sys.argv[1:] == ["youtube-text"]:  # descriptions for the stored YouTube posts without text (2026-10-20)
        print("youtube-text:", youtube_fill_text(), "posts filled")
    elif sys.argv[1:] == ["discuss-text"]:  # opening posts for the stored gnews_discuss threads (2026-10-20)
        print("discuss-text:", discuss_fill_text(), "posts filled")
    else:
        sys.exit("usage: sources.py youtube-dates | youtube-text | discuss-text")
