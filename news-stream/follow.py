#!/usr/bin/env python3
"""Link following (Deyao, 2026-10-20): what a post that reaches a stream links to is collected like any article.

Trigger: a post (not a comment) in stage follow's work (corpus.seen_cond) that scores at or above a stream's threshold after
stage 2 (table scores, streams.threshold), from any source: Reddit, Facebook, a forum, Telegram, a news article.

Its links: every URL in its title and text (where the collectors put a link post's target: Reddit's outside link,
Facebook's shared link, X's expanded t.co), and for a news-like post (fulltext_fetch.SOURCE_TYPES) the hyperlinks in
its article body, from one more read of its page (body_links: the paragraphs of the article, not navigation or
sidebars). Only the post's own links, never its comments'. One hop: an article collected here is followed only when it
reaches a stream itself.

Each link, in order:
  1. unwrapped (Facebook's l.php, Google's /url, l.instagram.com …: no request) and expanded when it is on a shortener
     (t.co, bit.ly, fb.me, lnkd.in …, SHORTENERS): one hop at a time with redirects off, so the destination itself is
     never requested here; a 200 interstitial (t.co's meta refresh, lnkd.in's page) gives its target;
  2. cleaned (clean: no fragment, no tracking parameters, corpus.TRACKING);
  3. skipped when it isn't an article (skip_reason: home page, section or tag page, picture/audio/video/file, a share
     button, a social platform's page, not http) and counted per reason and site on the monitor;
  4. a duplicate when the corpus has the article (by url, url_key: the existing dedupe);
  5. else, when a source of ours reads that site (outlet_for: the site of a watched source's feed or of its posts),
     handed to corpus.ingest as a new post of that outlet (its sid and name), then read in full right away by
     fulltext_now.fetch (the same reader every new article gets; the 30-minute job for hosts it leaves to that job),
     its title the page's own headline; scored and shown like any other post;
  6. else "unknown": listed on the monitor with its site, how many links pointed there and an example post, so
     Deyao sees which sites we're missing. When a source for that site is added, its listed links go through again.
A link that can't be expanded now (a shortener timing out) is tried again BACKOFF later, TRIES times in all.

Reddit's mirror bots (sources.resolve_mirrors, tikhub_fetch.reddit_mirror) use the same routing (route()) at
collection: the bot's post is never a post of its own; its article is a duplicate, a new post of its outlet, or listed.

Proxy routes: the collectors' (cache_fetch.get; 信報 / 香港討論區 through RESI_PROXY). Text only: media URLs are never
requested (nomedia).

Tables: follow_scan (one row per post followed: its links, how its body was read), follow_links (one row per link:
status pending | new | duplicate | unknown | skipped | failed, reason, the post it became or matched).

    python3 follow.py status          the monitor's figures (monitor.pipeline()["follow"])
    python3 follow.py once            follow the posts due now and print the counts
    python3 follow.py route URL…      what each link becomes (expands; writes nothing)
    python3 follow.py fix-skipped     links collected as articles that the skip rules now reject: skipped, and the
                                      posts link following made for them removed
    python3 follow.py fix-pointers    originals collected as new that are posts we had under another address of the
                                      story (corpus.STORY_KEYS): marked duplicate, the copy removed
"""
import html, json, re, sys, threading, time, urllib.parse
from datetime import datetime, timedelta

import corpus

HKT = corpus.HKT
TRIES = 4
BACKOFF = (300, 1800, 3 * 3600)
DAYS = 3          # posts seen this recently are looked at (a post is scored within hours; bounded scans)
IDLE = 10         # seconds between rounds when nothing was due
BATCH = 20        # posts per round
HOPS = 8          # shortener hops at most
TIMEOUT = 15
RECHECK = 3600    # seconds between looks at the listed sites (has a source for one been added?)
STOP = threading.Event()

SHORTENERS = {
    "t.co", "bit.ly", "bitly.com", "bit.do", "j.mp", "buff.ly", "dlvr.it", "fb.me", "goo.gl", "ift.tt", "is.gd",
    "v.gd", "lnkd.in", "ow.ly", "rebrand.ly", "shorturl.at", "t.ly", "tiny.cc", "tinyurl.com", "trib.al", "wp.me",
    "cutt.ly", "rb.gy", "reurl.cc", "pse.is", "lihi.cc", "lihi1.cc", "lihi1.com", "lihi.io", "ppt.cc", "s.id", "x.gd",
    "short.gy", "hubs.ly", "msft.it", "apple.co", "nyti.ms", "wapo.st", "reut.rs", "bloom.bg", "econ.st", "cnn.it",
    "bbc.in", "trib.in", "flip.it", "shar.es", "sc.mp", "tinyurl.hk", "urlz.fr", "linktr.ee/s", "spoti.fi", "amzn.to",
    "share.google", "search.app", "g.co", "aje.io", "ab.co", "abc7.la", "politi.co", "dailym.ai", "ind.pn", "gu.com",
    "theatln.tc", "on.ft.com", "on.wsj.com", "rd.gt", "ti.me", "yhoo.it", "zpr.io", "lin.ee", "i.mtr.cool",
    "qr.page", "qrcodes.pro",  # QR-code links: qr.page/g/<code> → 302 qrcodes.pro/<code> → a page whose redirect_link is the target
}
# links that are a button, not a page (sharing, sending)
SHARE = re.compile(r"^https?://(?:[^/]+\.)?(?:facebook\.com/(?:sharer|share\.php|dialog/(?:share|feed|send))|"
                   r"(?:twitter|x)\.com/(?:intent|share)|api\.whatsapp\.com/send|wa\.me/|(?:t|telegram)\.me/share|"
                   r"line\.me/R/msg|social-plugins\.line\.me|(?:www\.)?linkedin\.com/(?:shareArticle|sharing|cws/share)|"
                   r"(?:www\.)?reddit\.com/submit|pinterest\.com/pin/create|service\.weibo\.com/share|"
                   r"connect\.qq\.com/widget/shareqq|(?:www\.)?tumblr\.com/widgets/share|getpocket\.com/(?:edit|save)|"
                   r"mail\.google\.com/mail/\?.*view=cm|threads\.(?:net|com)/intent|"
                   r"(?:www\.)?google\.[a-z.]+/preferences/source)", re.I)  # Google's "preferred source" button
# app-store and app-install links (onelink.to, Branch, Firebase dynamic links, the stores)
APPS = re.compile(r"(^|\.)(onelink\.to|app\.link|smart\.link|apps\.apple\.com|itunes\.apple\.com|play\.google\.com|"
                  r"appgallery\.huawei\.com|app\.adjust\.com|adj\.st|app\.appsflyer\.com|onelink\.me)$", re.I)
GOOGLE = re.compile(r"^(www\.)?google\.[a-z.]+$|^(maps|search|consent|accounts)\.google\.[a-z.]+$", re.I)
SOCIAL = re.compile(r"(^|\.)(facebook\.com|fb\.com|fb\.watch|instagram\.com|threads\.net|threads\.com|t\.me|telegram\.me|"
                    r"youtube\.com|youtu\.be|linkedin\.com|x\.com|twitter\.com|tiktok\.com|douyin\.com|linktr\.ee|"
                    r"whatsapp\.com|reddit\.com|redd\.it|weibo\.com|weibo\.cn|xiaohongshu\.com|xhslink\.com|"
                    r"bilibili\.com|b23\.tv|kuaishou\.com|zhihu\.com|weixin\.qq\.com|lemon8-app\.com|pinterest\.com|"
                    r"tumblr\.com|vimeo\.com|spotify\.com|podcasts\.apple\.com|"
                    r"discord\.gg|discord\.com|patreon\.com|imgur\.com|giphy\.com)$", re.I)
# an advert's or affiliate's tracking redirect (Deyao, 2026-10-20: get.surfshark.net on the list of sites no source
# reads: a VPN advert in a post, not a publication)
ADS = re.compile(r"(^|\.)(get\.surfshark\.net|go\.nordvpn\.net|nordvpn\.sjv\.io|click\.linksynergy\.com|"
                 r"shareasale\.com|awin1\.com|clickbank\.net|tkqlhce\.com|anrdoezrs\.net|jdoqocy\.com|dpbolvw\.net|"
                 r"go\.skimresources\.com|redirect\.viglink\.com|doubleclick\.net|googleadservices\.com|"
                 r"s\.click\.aliexpress\.com|s\.taobao\.com|uland\.taobao\.com|amzn\.to|bit\.ly/m/)$", re.I)
FILES = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|apk|exe|dmg|csv|epub)$", re.I)
HOME_PATH = re.compile(r"^/?((index|default|home)\.(html?|php|aspx?|shtml))?$|^/(zh|en|tc|sc|zh-hk|zh-tw|zh-cn|hk|cht|chs|"
                       r"zh_hk|zh_tw|big5|gb)/?$", re.I)
SECTION_PATH = re.compile(r"^/(?:[^/]+/)*(?:tag|tags|category|categories|topic|topics|author|authors|search|section|"
                          r"keyword|keywords|label|channel|columnist|profile|archive|live|video|videos|photos?|gallery|"
                          r"subscribe|login|register|about\w*|contact|privacy|terms|rss|feed|newsletter|app|apps|data|quote|quotes|"
                          r"stock|stocks|market-data|\w*outline\w*|desc)(?:[/._]|$)", re.I)  # crntt's newsOutline.jsp, desc.jsp  # invest.hket.com/data/HKG02888/: a quote page
ARTICLE_QUERY = re.compile(r"id$|^id|^p$|article|news|story|post|item|doc|content", re.I)  # ?id=123: an article on a home path
URL_RE = re.compile(r"https?://[^\s<>\"'）)」』，。、；！？\]]+")
TRAIL = ".,;:!?…'\"”’"


def _now():
    return datetime.now(HKT)


def iso(t):
    return t.isoformat(timespec="seconds")


def bare_host(url):
    h = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return re.sub(r"^(www\d?|m|amp|mobile)\.", "", h)


# ---- the links a post carries ----

def unwrap(url):
    """a redirect wrapper's target, read from its own query (no request): Facebook's l.php / flx warn, Google's /url,
    l.instagram.com, out.reddit.com, LinkedIn's redir, Tumblr's t.umblr.com, href.li → url unchanged otherwise"""
    for _ in range(3):
        u = urllib.parse.urlsplit(url)
        h, q = (u.hostname or "").lower(), dict(urllib.parse.parse_qsl(u.query))
        to = None
        if h.endswith("facebook.com") and u.path in ("/l.php", "/flx/warn/"):
            to = q.get("u")
        elif re.match(r"^(www\.)?google\.[a-z.]+$", h) and u.path == "/url":
            to = q.get("q") or q.get("url")
        elif re.match(r"^(www\.)?google\.[a-z.]+$", h) and u.path.startswith("/amp/s/"):  # Google's AMP viewer
            to = "https://" + u.path[len("/amp/s/"):] + ("?" + u.query if u.query else "")
        elif h in ("l.instagram.com", "l.threads.com", "l.threads.net"):
            to = q.get("u")
        elif h == "out.reddit.com":
            to = q.get("url")
        elif h.endswith("linkedin.com") and u.path.startswith("/redir/redirect"):
            to = q.get("url")
        elif h == "t.umblr.com":
            to = q.get("z")
        elif h == "href.li":
            to = urllib.parse.unquote(u.query)
        if not to or not to.startswith("http"):
            return url
        url = to
    return url


def text_links(title, text):
    """every URL in a post's text and title, unwrapped, in order, each once"""
    out = []
    for m in URL_RE.finditer(f"{text or ''}\n{title or ''}"):
        u = unwrap(m.group(0).rstrip(TRAIL))
        if u not in out:
            out.append(u)
    return out


def body_links(page, base):
    """the hyperlinks in an HTML page's article body: inside the element marked articleBody, else inside the
    paragraphs of the largest paragraph cluster (cache_fetch.article_text's rule), never navigation, sidebars,
    headers or footers; relative links made absolute"""
    import lxml.html
    from collections import defaultdict
    try:
        doc = lxml.html.fromstring(page)
    except Exception:  # noqa: BLE001
        return []
    for bad in doc.xpath("//script|//style|//noscript|//nav|//footer|//header|//aside|//form|//iframe"):
        bad.drop_tree()
    scopes = doc.xpath('//*[@itemprop="articleBody"]')[:1]
    if not scopes:
        def cluster(roots):
            groups = defaultdict(int)
            for root in roots:
                for p in root.iter("p"):
                    groups[p.getparent()] += len(p.text_content().strip())
            return max(groups.items(), key=lambda kv: kv[1], default=(None, 0))
        best, n = cluster(doc.xpath("//article") or [doc])
        whole, wn = cluster([doc])
        if wn > 2 * n:
            best = whole
        scopes = [p for p in best.iterchildren("p")] if best is not None else []
    out = []
    for s in scopes:
        for a in s.iter("a"):
            href = (a.get("href") or "").strip()
            if not href or href.startswith("#"):
                continue
            u = unwrap(urllib.parse.urljoin(base, html.unescape(href)))
            if u not in out and u.split("#")[0] != base:
                out.append(u)
    return out


# ---- expanding, cleaning, judging ----

def _hop(url, browser=True):
    """one request to a shortener, redirects off → (status, Location, body); the body only of a 200 (an interstitial).
    browser: Chrome's TLS fingerprint (curl_cffi); False = a plain client"""
    from curl_cffi import requests
    r = requests.get(url, timeout=TIMEOUT, allow_redirects=False, headers={"Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"},
                     **({"impersonate": "chrome"} if browser else {}))
    return r.status_code, r.headers.get("location"), (r.text[:200000] if r.status_code == 200 else "")


def shortener(url):
    u = urllib.parse.urlsplit(url)
    h = (u.hostname or "").lower().removeprefix("www.")
    return h in SHORTENERS or f"{h}/{u.path.strip('/').split('/')[0]}" in SHORTENERS


# a shortener's own named target first (qrcodes.pro's footer also sets window.location to its maker's site)
INTERSTITIAL = (re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*id="redirect_link"', re.I),  # qrcodes.pro
                re.compile(r'<input[^>]+id="target"[^>]+value="(https?://[^"]+)"', re.I),  # reurl.cc (redirect.js)
                re.compile(r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\']*url=([^"\'>]+)', re.I),
                re.compile(r'data-tracking-control-name="external_url_click"[^>]*href="([^"]+)"', re.I),
                re.compile(r'href="([^"]+)"[^>]*data-tracking-control-name="external_url_click"', re.I),
                re.compile(r'location\.(?:replace|assign)\(\s*["\']([^"\']+)["\']', re.I),
                re.compile(r'(?:window\.)?location(?:\.href)?\s*=\s*["\'](https?://[^"\']+)["\']', re.I),
                re.compile(r"<title>\s*(https?://[^<\s]+)\s*</title>", re.I))


def _target(body):
    """the target an interstitial page names (INTERSTITIAL) → url or None"""
    return next((html.unescape(m.group(1)).strip() for rx in INTERSTITIAL if (m := rx.search(body or ""))), None)


def expand(url):
    """the address a shortener sends to (several shorteners in a row, wrappers in between); url itself when it isn't on
    a shortener. Raises when a shortener can't be read now (tried again later)"""
    url = unwrap(url)
    for _ in range(HOPS):
        if not shortener(url):
            return url
        status, loc, body = _hop(url)
        if status == 200 and not _target(body):
            # bit.ly shows a browser from the cluster's IP its preview page, whose target only its JavaScript knows; a
            # plain client gets the 301 (2026-10-21: bit.ly/IOMedForum)
            status, loc, body = _hop(url, browser=False)
        if 300 <= status < 400 and loc:
            nxt = urllib.parse.urljoin(url, loc)
        elif status == 200:
            nxt = _target(body)
            if not nxt:
                raise RuntimeError(f"{bare_host(url)} answered 200 with no target")
        else:
            raise RuntimeError(f"{bare_host(url)} answered HTTP {status}")
        if nxt == url:
            raise RuntimeError("redirect loop")
        url = unwrap(nxt)
    if shortener(url):
        raise RuntimeError("too many redirects")
    return url


# a site's other address → the one its pages state and its source reads (China Daily Hong Kong's old domain); and the
# hosts no browser opens (canonical.REHOST: www.hkcd.com.hk → www.hkcd.com, the same paths)
HOST_ALIASES = {"chinadailyasia.com": "www.chinadailyhk.com"}


def alias(url):
    """url on the address its site's source reads (HOST_ALIASES, canonical.REHOST; the same path) → url unchanged
    otherwise"""
    import canonical
    u = urllib.parse.urlsplit(url)
    h = bare_host(url)
    to = HOST_ALIASES.get(h) or canonical.REHOST.get(h)
    return urllib.parse.urlunsplit(("https", to, u.path, u.query, u.fragment)) if to else url


def clean(url):
    """the address without its fragment and tracking parameters (corpus.TRACKING), host lower-cased"""
    u = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True) if not corpus.TRACKING.match(k)]
    return urllib.parse.urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path or "/", urllib.parse.urlencode(q, safe=":/,"), ""))


# links Deyao decided to ignore (2026-10-21, on the "Sites no source reads (9)" list: "ignore all sites on the table"):
# forms, file shares, shop and account pages, a content farm, an event site's tagline, pages with no article text.
# Skipped as IGNORED, counted under Skipped; a whole host, or (host, path regex) for one kind of page on a site we read
IGNORED = "ignored by Deyao"
IGNORE_HOSTS = ("drive.google.com", "forms.cloud.microsoft", "docs.google.com", "gbb.shop", "mingtakfn.com",
                "geosirius.ifz.ru", "familyandwomensummit.hk")
IGNORE_PAGES = (
    ("elections.gov.hk", re.compile(r"^/ecss2026/(chi|eng)/(index|nomination2)\.html$")),  # home, candidate table
    ("bpahk.org", re.compile(r"微信公眾號")),  # 經民聯's "join our WeChat" page (its articles are read)
)


_off = {}


def off_hosts():
    """the hosts of the catalogue sites left unread (sites_fetch.OFF_REGIONS; Deyao, 2026-10-28: "note them as ignored
    intentionally"), but a host a read source also has"""
    if "hosts" not in _off:
        try:
            import sites_fetch, sources
            from sources_extra import EXTRA
            sites = sites_fetch.load()
            on = {bare_host(s["url"]) for s in sites if not sites_fetch.off(s)}
            on |= {bare_host(sp[2]) for sp in list(sources.SOURCES) + list(EXTRA) if isinstance(sp[2], str)}
            _off["hosts"] = {bare_host(s["url"]) for s in sites if sites_fetch.off(s)} - on - {""}
        except Exception as e:  # noqa: BLE001: nothing is ignored for it then
            print("follow: off hosts:", repr(e)[:160], flush=True)
            return set()
    return _off["hosts"]


def ignored(url):
    """is this a link Deyao decided to ignore (IGNORE_HOSTS, IGNORE_PAGES, the unread regions' sites)?"""
    h = bare_host(url)
    if any(h == x or h.endswith("." + x) for x in IGNORE_HOSTS) or h in off_hosts():
        return True
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    return any((h == x or h.endswith("." + x)) and rx.search(path) for x, rx in IGNORE_PAGES)


def skip_reason(url):
    """why a link is not an article → "not_web" | "media" | "file" | "share" | "ad" | "app" | "google" | "social" |
    "home" | "section" | IGNORED, or None"""
    import nomedia
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return "not_web"
    if ignored(url):
        return IGNORED
    if nomedia.media_url(url):
        return "media"
    if FILES.search(u.path):
        return "file"
    if SHARE.search(url):
        return "share"
    if ADS.search(u.hostname.lower()):
        return "ad"
    if APPS.search(u.hostname.lower()):
        return "app"
    if GOOGLE.search(u.hostname.lower()):
        return "google"  # Google's own pages (a search, a map): /url and the AMP viewer are unwrapped before this
    if SOCIAL.search(u.hostname.lower()):
        return "social"
    path = re.sub(r"/+$", "", u.path) or "/"
    if HOME_PATH.match(u.path or "/") and not any(ARTICLE_QUERY.search(k) for k, _ in urllib.parse.parse_qsl(u.query)):
        return "home"  # also https://www.rthk.hk/?lang=zh-hant
    if SECTION_PATH.match(path):
        return "section"
    segs = [s for s in path.split("/") if s]
    if len(segs) <= 2 and not u.query and not re.search(r"\d", path) and len(re.split(r"[-_]", segs[-1])) < 3:
        return "section"  # /news, /news/hong-kong, /opinion: an article's address carries an id or a slug of words
    return None


# ---- which source reads a site ----

_outlets = {}
_post_hosts = {"at": 0, "hosts": {}}
_lock = threading.Lock()
NOT_OUTLETS = ("gnews", "col_", "tikhub", "watch_", "fb_", "ig_", "mirror", "bc_", "link_")  # link_: generic reader's


SHARED_SITES = {"blogspot.com", "wordpress.com", "medium.com", "substack.com", "github.io", "google.com", "feedburner.com",
                "googleusercontent.com", "amazonaws.com", "cloudfront.net", "wixsite.com", "weebly.com", "tumblr.com"}


def _feed_outlets():
    """{host: (sid, name)}: the hosts of every source's feed (sources.py, sources_extra, the catalogue sites.json),
    and each one's registrable domain (hk.crntt.com's feed reads www.crntt.com's articles), except shared hosting"""
    if not _outlets:
        import canonical, sites_fetch, sources
        from sources_extra import EXTRA
        feeds = [(sp[0], sp[1], sp[2]) for sp in list(sources.SOURCES) + list(EXTRA)]
        try:
            feeds += [(s["id"], s["name"], s["url"]) for s in sites_fetch.load_read()]
            # a list on one host whose stories sit on another, named by its "link" template (地球物理氣象局: the list on
            # cms.smg.gov.mo, the stories on www.smg.gov.mo/zh/news-detail/{id}): the template's host up to its first field
            feeds += [(s["id"], s["name"], s["link"].split("{")[0]) for s in sites_fetch.load_read()
                      if isinstance(s.get("link"), str) and s["link"].startswith("http") and "/" in s["link"].split("{")[0][8:]]
        except Exception as e:  # noqa: BLE001: the code's own sources still route
            print("follow: sites.json:", repr(e)[:160], flush=True)
        not_outlets = set()
        try:
            not_outlets = {s["id"] for s in sites_fetch.load() if s.get("outlet") is False}
        except Exception:  # noqa: BLE001: reported above
            pass
        for sid, name, feed in feeds:
            if not isinstance(feed, str) or sid.startswith(NOT_OUTLETS) or "{" in feed or sid in not_outlets:
                continue  # "outlet": false = a listing whose articles sit on another site (ECSS 2026 → info.gov.hk)
            h = bare_host(feed)
            if h and not SOCIAL.search(h):
                _outlets.setdefault(h, (sid, name))
        for h, o in list(_outlets.items()):
            s = canonical.site(h)
            if s not in SHARED_SITES and not SOCIAL.search(s):
                _outlets.setdefault(s, o)
    return _outlets


def _corpus_outlets():
    """{host: (sid, source)}: the sites a watched source's posts of the last 30 days sit on (its most common sid)"""
    with _lock:
        if time.time() - _post_hosts["at"] > 3600:
            since = (_now() - timedelta(days=30)).strftime("%Y-%m-%d")
            rows = corpus.db().execute(
                """SELECT split_part(url, '/', 3) AS h, sid, min(source) AS source, count(*) AS n FROM posts
                   WHERE day >= %s AND thread IS NULL AND sid IS NOT NULL GROUP BY 1, 2 ORDER BY n DESC""", (since,)).fetchall()
            hosts = {}
            for r in rows:
                h = bare_host("https://" + (r["h"] or "") + "/")
                if h and not r["sid"].startswith(NOT_OUTLETS) and not SOCIAL.search(h):
                    hosts.setdefault(h, (r["sid"], r["source"]))
            _post_hosts.update(at=time.time(), hosts=hosts)
        return _post_hosts["hosts"]


def outlet_for(url):
    """(sid, name) of the watched source that reads url's site (its feeds' host, else the host its posts sit on; a
    subdomain matches its parent) → None when no source of ours reads it"""
    h = bare_host(url)
    for table in (_feed_outlets(), _corpus_outlets()):
        x = h
        while x:
            if x in table:
                return table[x]
            x = x.partition(".")[2] if x.count(".") > 1 else ""
    return None


def readable(sid, name, url):
    """can the pipeline read a post of this outlet from its URL? Not a forum's or a social platform's posts, which
    only their own jobs read (LIHKG, 香港討論區: the replies job walks every thread) → None or why not"""
    import fulltext_fetch
    st = corpus.classify({"sid": sid, "source": name, "url": url})["source_type"]
    return None if st in fulltext_fetch.SOURCE_TYPES else f"{st} posts: read only by their own job"


def have(url):
    """the id of the corpus post that is this article (its url or url_key: the existing dedupe) → int or None"""
    k = corpus.url_key(url)
    r = corpus.db().execute("SELECT id FROM posts WHERE url = %s OR url_key = %s ORDER BY id LIMIT 1", (url, k)).fetchone()
    return r[0] if r else None


# ---- the generic reader: a site no source reads (Deyao, 2026-10-21: "add these sources") ----
# A link to a site no source of ours reads is read once, as a single page; it becomes a post (sid link_<site>, its
# name the page's og:site_name) only when the page is an article: article text (cache_fetch.article_text) of at least
# GENERIC_MIN letters, and no sign of a form, a shop or a file share (not_article). Anything else stays on the
# monitor's list with the reason, for Deyao to decide (Deyao, 2026-10-21: "if some sources don't fit … don't force it,
# bring it up to me and let me decide"): nothing is ignored or read specially without his word.
GENERIC = "link_"
GENERIC_MIN = 200
# hosts of document, form and file services: their pages are not articles (Google Drive folders, Forms)
DOC_SERVICES = re.compile(r"(^|\.)(docs\.google\.com|drive\.google\.com|forms\.gle|forms\.cloud\.microsoft|"
                          r"forms\.office\.com|onedrive\.live\.com|1drv\.ms|sharepoint\.com|dropbox\.com|box\.com|"
                          r"wetransfer\.com|we\.tl|mega\.nz|jotform\.com|typeform\.com|surveymonkey\.com|"
                          r"eventbrite\.[a-z.]+|notion\.site)$", re.I)
SHOP = re.compile(r'"@type"\s*:\s*"Product"|og:type"\s+content="product|加入購物車|add to cart|立即購買|buy now', re.I)
FORM_FIELD = re.compile(r'<input\b[^>]*type=["\']?(?:text|email|tel|number|date)\b', re.I)
# sites listed for Deyao's decision: the generic reader leaves them on the list (not ignored: they stay visible)
HOLD = {}


def not_article(url, page):
    """why a page is not an article the generic reader turns into a post → "document or form service" | "form" |
    "shop page" | "no article text", or None"""
    import cache_fetch
    if DOC_SERVICES.search(bare_host(url)):
        return "document or form service"
    if "FB_PUBLIC_LOAD_DATA_" in page or len(FORM_FIELD.findall(page)) >= 5:
        return "form"
    if SHOP.search(page):
        return "shop page"
    if len(re.sub(r"\W", "", cache_fetch.article_text(page))) < GENERIC_MIN:
        return "no article text"
    return None


def generic_outlet(url, page):
    """(sid, name) of a site no source reads: link_<its registrable domain>, named by the page's og:site_name"""
    import canonical
    site = canonical.site(bare_host(url))
    m = re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']{2,80})["\']', page, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']{2,80})["\'][^>]+property=["\']og:site_name["\']', page, re.I)
    name = html.unescape(m.group(1)).strip() if m else site
    return GENERIC + re.sub(r"[^a-z0-9]+", "_", site.lower()).strip("_"), name


def _generic_page(url):
    """the page of a site no source reads (cache_fetch.get: the collectors' route, cached)"""
    import cache_fetch
    return cache_fetch.get(url)


def generic(url):
    """read a page of a site no source reads → ((sid, name), None) when it is an article, else (None, why). Raises
    when the page can't be read now (tried again later, TRIES times)"""
    h = bare_host(url)
    held = next((why for x, why in HOLD.items() if h == x or h.endswith("." + x)), None)
    if held:
        return None, held
    page = _generic_page(url)
    why = not_article(url, page or "")
    return (None, why) if why else (generic_outlet(url, page), None)


def route(link):
    """what a link becomes → {"url" (expanded, cleaned), "host", "status": new | duplicate | unknown | skipped,
    "reason", "target_id" (a duplicate's post), "outlet" ((sid, name) for new)}. Raises when it can't be expanded now
    (or, for a site no source reads, its page can't be read now)"""
    final = alias(clean(expand(link)))
    out = {"url": final, "host": bare_host(final), "reason": None, "target_id": None, "outlet": None}
    why = skip_reason(final)
    if why:
        return dict(out, status="skipped", reason=why)
    pid = have(final)
    if pid:
        return dict(out, status="duplicate", target_id=pid)
    o = outlet_for(final)
    if not o:
        o, why = generic(final)
        return dict(out, status="new", outlet=o) if o else dict(out, status="unknown", reason=why)
    why = readable(*o, final)
    if why:
        return dict(out, status="unknown", reason=why)
    return dict(out, status="new", outlet=o)


# ---- collecting a new article ----

def collect(r, title=None):
    """corpus.ingest the routed link as a post of its outlet, then its page read now by fulltext_now.fetch (the
    reader every new article gets; enqueue for a host only the 30-minute job reads); its title the page's own
    headline → (status, post id): "new", or "duplicate" when another writer stored it meanwhile"""
    import cache_fetch, fulltext_fetch, fulltext_now
    sid, name = r["outlet"]
    placeholder = corpus.LINK_TITLE + r["host"]
    ids = corpus.ingest([{"sid": sid, "source": name, "section": None, "title": title or placeholder, "url": r["url"],
                          "published": None, "max_age_days": None,
                          # a generic post is one linked page, never a backlog; a watched source's first post stays one
                          # (its sid's first read is its backlog: the post followed first would make the rest look new)
                          "followed": sid.startswith(GENERIC)}])
    if not ids:
        return "duplicate", have(r["url"])
    pid = ids[0]
    row = dict(corpus.db().execute("SELECT id, url, sid, thread, source_type, text, cached_at, published, date_only "
                                   "FROM posts WHERE id=%s", (pid,)).fetchone())
    if not fulltext_now.wanted(row):
        fulltext_now.enqueue([pid])
    else:
        fulltext_now.fetch(pid, r["url"], sid)
    if not title:
        t = fulltext_fetch.page_title(cache_fetch.last_page() or "", r["url"]) if fulltext_now.wanted(row) else ""
        if not t:
            try:
                import links
                t = links.page_info(r["url"])["title"]
            except Exception:  # noqa: BLE001: the 〔連結〕 title stays; the post is scored on its text
                t = ""
        if t:
            corpus.db().execute("UPDATE posts SET title=%s WHERE id=%s AND title=%s", (t[:500], pid, placeholder))
    if sid.startswith(GENERIC):
        generic_fix(pid, cache_fetch.last_page() or "")
    return "new", pid


HAN = re.compile(r"[\u4e00-\u9fff]")


def generic_fix(pid, page):
    """a generic post after its page was read: the page's one <h1> as its title when the title doesn't hold it (VA.gov's
    <title> is the site's name), and Hong Kong as its region when its text is Chinese on a non-.cn host (經民聯's .org)"""
    r = corpus.db().execute("SELECT title, text, region, url FROM posts WHERE id=%s", (pid,)).fetchone()
    if not r:
        return
    h1 = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
          for x in re.findall(r"<h1\b[^>]*>(.*?)</h1>", page, re.S | re.I)]
    title = r["title"] or ""
    if len(h1) == 1 and len(h1[0]) >= 6 and h1[0] not in title and not title.startswith(corpus.LINK_TITLE):
        corpus.db().execute("UPDATE posts SET title=%s WHERE id=%s", (h1[0][:500], pid))
    text = r["text"] or ""
    if r["region"] == "Global" and len(HAN.findall(text)) >= max(50, len(text) * 0.3) and not bare_host(r["url"]).endswith(".cn"):
        corpus.db().execute("UPDATE posts SET region='HK' WHERE id=%s", (pid,))


def pointer(pid, origin, link):
    """an aggregator's post (cache_fetch.AGGREGATOR_HOSTS, Deyao 2026-10-21: headline4hk.com copies 星島, NOW, on.cc …
    with a 「原文連結」): its original recorded as the post's link and routed now, whether or not the post reaches a
    stream (route: a duplicate of the corpus's post, a new post of its outlet collected and read, or an unknown site
    listed) → (status, target post id or None, the routed URL)"""
    c = corpus.db()
    row = c.execute("""INSERT INTO follow_links (post_id, origin, url, status, at, tries) VALUES (%s,%s,%s,'pending',%s,0)
                       ON CONFLICT (origin, url) DO UPDATE SET post_id = EXCLUDED.post_id
                       RETURNING id, url, status, target_id, final_url""", (pid, origin, link[:4000], iso(_now()))).fetchone()
    if row["status"] not in ("new", "duplicate") or not row["target_id"]:
        one({"id": row["id"], "url": row["url"]})
        row = c.execute("SELECT status, target_id, final_url FROM follow_links WHERE id=%s", (row["id"],)).fetchone()
    return row["status"], row["target_id"], row["final_url"] or link


def fix_pointers():
    """aggregator originals collected as new posts that are posts we had under another address of the same story (an
    older post with the same url_key: corpus.STORY_KEYS) → the link marked duplicate of that post, the copy removed
    → copies removed"""
    import cache_fetch
    c = corpus.db()
    rows = c.execute("""SELECT f.id, f.target_id, p.url_key, p.seen FROM follow_links f JOIN posts p ON p.id = f.target_id
                        WHERE f.status = 'new' AND f.post_id IS NOT NULL""").fetchall()
    gone = 0
    for r in rows:
        if not r["url_key"]:
            continue
        old = c.execute("SELECT id FROM posts WHERE url_key = %s AND id < %s ORDER BY id LIMIT 1",
                        (r["url_key"], r["target_id"])).fetchone()
        if not old:
            continue
        with c:
            c.execute("UPDATE follow_links SET status='duplicate', target_id=%s WHERE target_id=%s", (old["id"], r["target_id"]))
            gone += c.execute("DELETE FROM posts WHERE id=%s", (r["target_id"],)).rowcount
    return gone


# ---- the trigger ----

def _thresholds():
    import streams
    ids = [st["id"] for st in streams.all_streams()]
    return [(i, streams.threshold(i)) for i in ids], streams.DEFAULT_THRESHOLD


def due(limit=BATCH):
    """posts that reached a stream and are not followed yet, newest first"""
    pairs, default = _thresholds()
    since = max(datetime.fromisoformat(corpus.cutoff("follow")), _now() - timedelta(days=DAYS))
    vals = ",".join(["(%s, %s)"] * len(pairs)) or "(NULL, NULL)"
    args = [x for p in pairs for x in p] + [default, iso(since), limit]
    # driven from scores: each stream's rows at or above its threshold (index-only once scores_stream_score is built), then those posts' rows.
    # From posts first (the posts_seen range, a scores probe per post) it read ~624k buffers a call (2026-10-29).
    # Every stream in scores takes part (found by a skip scan of scores' key); one streams.json lacks has the default
    return [dict(r) for r in corpus.db().execute(
        f"""WITH RECURSIVE ss(stream) AS (SELECT min(stream) FROM scores
              UNION ALL SELECT (SELECT min(stream) FROM scores WHERE stream > ss.stream) FROM ss WHERE ss.stream IS NOT NULL),
            listed(stream, th) AS (VALUES {vals}),
            th AS (SELECT ss.stream, coalesce(l.th::int, %s) AS th FROM ss LEFT JOIN listed l ON l.stream = ss.stream
                   WHERE ss.stream IS NOT NULL),
            hit AS MATERIALIZED (SELECT DISTINCT s.post_id FROM th
              JOIN LATERAL (SELECT post_id FROM scores s WHERE s.stream = th.stream AND s.score >= th.th) s ON true),
            c AS MATERIALIZED (SELECT q.id FROM hit JOIN posts q ON q.id = hit.post_id
              WHERE q.seen >= %s AND q.thread IS NULL
                AND NOT EXISTS (SELECT 1 FROM follow_scan f WHERE f.post_id = q.id))
            SELECT p.id, p.url, p.title, p.text, p.source_type, p.sid FROM c JOIN posts p ON p.id = c.id
            WHERE {corpus.seen_cond("p", "follow")}
            ORDER BY p.id DESC LIMIT %s""", args)]


def _page(url):
    """one more read of a news post's page for its body's links (cache_fetch.get: the collectors' route, 信報 and
    香港討論區 through RESI_PROXY)"""
    import cache_fetch, resi
    proxy = resi.url("hk") if cache_fetch.host(url).endswith(cache_fetch.PROXY_HOSTS) else None
    return cache_fetch.get(url, proxy=proxy)


def page_reason(url):
    """why a news post's page is not read again for its links (a solver, a browser or a special reader) → None"""
    import cache_fetch, hkgolden, paywall
    h = cache_fetch.host(url)
    if h.endswith(cache_fetch.SOLVER_HOSTS + paywall.DIRECT_HOSTS):
        return "not read: paid solver / page data"
    if h.endswith(cache_fetch.BROWSER_HOSTS + cache_fetch.SKIP_HOSTS) or cache_fetch.hket_api(url) \
            or hkgolden.thread_id(url) or h.endswith("lihkg.com"):
        return "not read: browser or API reader"
    return None


def take(p):
    """record a post's links (pending) → how many"""
    import fulltext_fetch
    found = text_links(p["title"], p["text"])
    body = None
    if p.get("source_type") in fulltext_fetch.SOURCE_TYPES:
        body = page_reason(p["url"])
        if not body:
            import resi
            try:
                found += [u for u in body_links(_page(p["url"]), p["url"]) if u not in found]
                body = "page"
            except resi.ProxyOut:  # IPRoyal out of traffic: the body's links are not read (its text's are followed)
                body = "not read: proxy out of traffic"
            except Exception as e:  # noqa: BLE001: its text's links are still followed
                body = f"failed: {type(e).__name__}: {e}"[:200]
    own = {corpus.url_key(p["url"])}
    found = [u for u in found if corpus.url_key(u) not in own]
    at = iso(_now())
    c = corpus.db()
    with c:
        for u in found:
            c.execute("""INSERT INTO follow_links (post_id, origin, url, status, at, tries) VALUES (%s,%s,%s,'pending',%s,0)
                         ON CONFLICT (origin, url) DO NOTHING""", (p["id"], p["url"], u[:4000], at))
        c.execute("INSERT INTO follow_scan (post_id, at, links, body) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                  (p["id"], at, len(found), body))
    return len(found)


def record(row_id, r=None, err=None, counted=True):
    c = corpus.db()
    if r is not None:
        c.execute("""UPDATE follow_links SET status=%s, reason=%s, final_url=%s, host=%s, target_id=%s, done_at=%s,
                     next_try=NULL, error=NULL WHERE id=%s""",
                  (r["status"], r["reason"], r["url"][:4000], r["host"], r["target_id"], iso(_now()), row_id))
        return
    t = c.execute("SELECT tries FROM follow_links WHERE id=%s", (row_id,)).fetchone()[0] + (1 if counted else 0)
    failed = t >= TRIES
    nxt = None if failed else iso(_now() + timedelta(seconds=BACKOFF[min(max(t, 1), len(BACKOFF)) - 1]))
    c.execute("""UPDATE follow_links SET status=%s, tries=%s, next_try=%s, error=%s, done_at=%s WHERE id=%s""",
              ("failed" if failed else "pending", t, nxt, (err or "")[:300], iso(_now()) if failed else None, row_id))


def one(row):
    """route one pending link and act on it (collect a new article)"""
    import resi
    try:
        r = route(row["url"])
        if r["status"] == "new":
            r["status"], r["target_id"] = collect(r)
        record(row["id"], r)
        return r["status"]
    except Exception as e:  # noqa: BLE001: tried again later, TRIES times
        record(row["id"], err=f"{type(e).__name__}: {e}", counted=not isinstance(e, resi.ProxyOut))
        print("follow:", row["url"][:100], repr(e)[:160], flush=True)
        return "error"


_recheck = {"at": 0}


def recheck():
    """listed links that may go through now: a site a source of ours reads now (one was added), a held site Deyao has
    released (HOLD), and the links listed before the generic reader (reason NULL, 2026-10-21) go through again; a
    listed link the skip rules now reject leaves the list as skipped. A page the generic reader found no article on
    stays listed (the page doesn't change; Deyao decides)"""
    if time.time() - _recheck["at"] < RECHECK:
        return 0
    _recheck["at"] = time.time()
    _post_hosts["at"] = 0  # the feeds' sites are the code's (a new source = a deploy = a fresh process)
    c = corpus.db()
    for r in c.execute("SELECT id, final_url FROM follow_links WHERE status='unknown'").fetchall():
        why = skip_reason(r["final_url"] or "")
        if why:  # the skip rules grew (an advert's link: get.surfshark.net): off the list, counted as skipped
            c.execute("UPDATE follow_links SET status='skipped', reason=%s WHERE id=%s", (why, r["id"]))
    rows = c.execute("SELECT DISTINCT host, reason FROM follow_links WHERE status='unknown'").fetchall()
    held = set(HOLD.values())
    again = [(r["host"], r["reason"]) for r in rows
             if r["reason"] is None or (r["reason"] in held and not any(
                 r["host"] == x or (r["host"] or "").endswith("." + x) for x in HOLD))
             or (not (r["reason"] or "").endswith("own job") and outlet_for(alias("https://" + (r["host"] or "") + "/")))]
    n = 0
    for h, why in again:
        n += c.execute("""UPDATE follow_links SET status='pending', next_try=NULL, tries=0 WHERE status='unknown'
                          AND host IS NOT DISTINCT FROM %s AND reason IS NOT DISTINCT FROM %s""", (h, why)).rowcount
    return n


def run_once(limit=BATCH):
    """follow the posts due (record their links), then route every pending link whose turn it is → posts followed"""
    posts = due(limit)
    for p in posts:
        if STOP.is_set():  # the poller's drain: the rest are due again for the next poller
            return 0
        take(p)
    recheck()
    rows = corpus.db().execute("""SELECT id, url FROM follow_links WHERE status='pending'
                                  AND (next_try IS NULL OR next_try <= %s) ORDER BY id LIMIT %s""",
                               (iso(_now()), limit * 10)).fetchall()
    for r in rows:
        if STOP.is_set():
            break
        one(dict(r))
    return len(posts)


def loop():
    while not STOP.is_set():
        try:
            with corpus.stage("follow"):  # a restart starts from now (corpus.STAGES): the backlog waits
                n = run_once()
        except Exception as e:  # noqa: BLE001
            print("follow: round failed:", repr(e)[:300], flush=True)
            n = 0
        if not n:
            STOP.wait(IDLE)


def start(stop=None):
    """the follower thread (the poller's; stop = the scorer's drain event)"""
    global STOP
    STOP = stop or STOP
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


# ---- Reddit's mirror bots (sources.resolve_mirrors) ----

def mirror(origin, link):
    """a mirror bot's article (route) → the route, the outcome recorded as a link of the bot's post (post_id NULL: the
    bot's post is never stored). "new" is collected by the poll that brought the bot's post (sources.resolve_mirrors)"""
    try:
        r = route(link)
    except Exception as e:  # noqa: BLE001
        print("follow: mirror", link[:100], repr(e)[:160], flush=True)
        return None
    c = corpus.db()
    c.execute("""INSERT INTO follow_links (post_id, origin, url, final_url, host, status, reason, target_id, at, done_at, tries)
                 VALUES (NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,0) ON CONFLICT (origin, url) DO NOTHING""",
              (origin, link[:4000], r["url"][:4000], r["host"], r["status"], r["reason"], r["target_id"],
               iso(_now()), iso(_now())))
    return r


def fix_skipped():
    """links collected as new articles that skip_reason now calls not articles (its rules grew): marked skipped, and
    the post collected for each removed when link following made it (seen between the link's record and its outcome)
    → (links, posts removed)"""
    c = corpus.db()
    rows = c.execute("""SELECT f.id, f.final_url, f.at, f.done_at, f.target_id, p.seen FROM follow_links f
                        LEFT JOIN posts p ON p.id = f.target_id WHERE f.status = 'new' AND f.post_id IS NOT NULL""").fetchall()
    n = gone = 0
    for r in rows:
        why = skip_reason(r["final_url"] or "")
        if not why:
            continue
        with c:
            c.execute("UPDATE follow_links SET status='skipped', reason=%s, target_id=NULL WHERE id=%s", (why, r["id"]))
            if r["target_id"] and r["seen"] and r["at"] <= r["seen"] <= (r["done_at"] or r["seen"]):
                gone += c.execute("DELETE FROM posts WHERE id=%s", (r["target_id"],)).rowcount
        n += 1
    return n, gone


# ---- the monitor ----

def monitor():
    """the monitor's figures: last 24 h and all-time counts (posts followed, links, per status), the sites no source
    reads (host, links, posts, last, an example post), the skipped links per reason (with their top sites), recent
    failures"""
    c = corpus.db()
    day = iso(_now() - timedelta(hours=24))

    def counts(since):
        r = c.execute("""SELECT count(*) AS links, count(*) FILTER (WHERE status='new') AS new,
                                count(*) FILTER (WHERE status='duplicate') AS duplicate,
                                count(*) FILTER (WHERE status='unknown') AS unknown,
                                count(*) FILTER (WHERE status='skipped') AS skipped,
                                count(*) FILTER (WHERE status='failed') AS failed,
                                count(*) FILTER (WHERE status='pending') AS pending,
                                count(*) FILTER (WHERE post_id IS NULL) AS from_mirrors
                         FROM follow_links WHERE at >= %s""", (since,)).fetchone()
        d = dict(r)
        d["posts"] = c.execute("SELECT count(*) FROM follow_scan WHERE at >= %s", (since,)).fetchone()[0]
        return d
    sites = []
    for r in c.execute("""SELECT host, count(*) AS links, count(DISTINCT origin) AS posts, max(at) AS last,
                                 (array_agg(id ORDER BY id DESC))[1] AS example_id, min(reason) AS reason
                          FROM follow_links WHERE status='unknown' GROUP BY host ORDER BY 2 DESC, 4 DESC LIMIT 100"""):
        ex = c.execute("""SELECT f.url, f.final_url, f.origin, p.id, p.title, p.source FROM follow_links f
                          LEFT JOIN posts p ON p.id = f.post_id WHERE f.id=%s""", (r["example_id"],)).fetchone()
        sites.append({"host": r["host"], "links": r["links"], "posts": r["posts"], "last": r["last"], "reason": r["reason"],
                      "example": {"link": ex["final_url"] or ex["url"], "post_id": ex["id"], "post_url": ex["origin"],
                                  "title": ex["title"], "source": ex["source"]}})
    skipped = []
    for r in c.execute("""SELECT reason, count(*) AS links FROM follow_links WHERE status='skipped' AND at >= %s
                          GROUP BY reason ORDER BY 2 DESC""", (day,)):
        top = c.execute("""SELECT host, count(*) AS n, max(final_url) AS example FROM follow_links
                           WHERE status='skipped' AND reason=%s AND at >= %s GROUP BY host ORDER BY 2 DESC LIMIT 5""",
                        (r["reason"], day)).fetchall()
        skipped.append({"reason": r["reason"], "links": r["links"], "hosts": [dict(x) for x in top]})
    failed = [dict(r) for r in c.execute("""SELECT url, error, done_at FROM follow_links WHERE status='failed'
                                            ORDER BY done_at DESC NULLS LAST LIMIT 10""")]
    last = c.execute("SELECT max(at) FROM follow_scan").fetchone()[0]
    return {"last_24h": counts(day), "all": counts("0"), "unknown_sites": sites, "skipped": skipped, "failed": failed,
            "last_followed": last}


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["status"]:
        print(json.dumps(monitor(), ensure_ascii=False, indent=1, default=str))
    elif cmd == ["fix-pointers"]:
        print("copies removed:", fix_pointers())
    elif cmd == ["fix-skipped"]:
        print("links marked skipped, posts removed:", fix_skipped())
    elif cmd == ["once"]:
        print(run_once(), "post(s) followed")
    elif cmd == ["route"]:
        for u in sys.argv[2:]:
            r = route(u)
            print(u, "→", r["status"], r["url"], r["reason"] or "", r["target_id"] or "", r["outlet"] or "")
    else:
        sys.exit(__doc__)
