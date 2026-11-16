#!/usr/bin/env python3
"""Backfill: older articles of the outlets the stream reads, from each outlet's own public archive, into the corpus
(posts only: never stream_items, tags or alerts), so search reaches back before the stream started.

    python3 backfill.py OUTLET [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--max N] [--minutes M]
    python3 backfill.py all [--days N] [--max N] [--minutes M]     every outlet, in parallel (one thread per outlet)
    python3 backfill.py status                                    what each outlet has done (kv backfill:<outlet>)
    python3 backfill.py OUTLET --test DAY                          print what one unit gives (writes nothing)

Each outlet works backwards from --until (default: the day before the outlet's first live row in the corpus, and at
most today − RECENT_GAP days) to --since (default: today − DEFAULT_DAYS), one unit at a time (a day, a month's sitemap,
an article id or a listing page), and records in the corpus kv (`backfill:<outlet>`) where it got to, so the next
run carries on from there: a run is bounded by --max requests per outlet and --minutes. Rows use the same sid and
source names as the live sources (corpus.classify gives the same website), the article's real publish time, and its
text when the archive route gives it (HK01, now, TVB, 星島, SCMP, 晴報 pages); the rest get their text from
fulltext_fetch.py, which fills News rows without text.

Archive routes (probed 2026-10-13; depth = how far back the route goes):
  oncc        東方日報        orientaldaily.on.cc/section/sitemap/YYYYMMDD: the day's paper    ~200/day, back to 2012+
  mingpao     明報            news.mingpao.com/pns/x/section/YYYYMMDD/s000NN (11 sections)      ~110/day, from 2023-10-06
                              (Cloudflare refuses urllib/curl even on a residential IP; curl_cffi's Chrome fingerprint passes)
  rthk        RTHK 香港電台   news.rthk.hk/rthk/ch/news-archive.htm?archive_year=…             ~130/day, rolling 1 year
  tkp / wwp   大公報 / 文匯報 www.tkww.hk/epaper/sitemap/{tkp,wwp}/YYYYMMDD/news.xml           ~100-180/day, back to 2020+
  infogov     政府新聞公報    www.info.gov.hk/gia/general/YYYYMM/DDc.htm                        ~100/day, back to 1998
  hkej        信報            www1.hkej.com/dailynews/<section>?date=YYYY-MM-DD (via RESI_PROXY) ~250/day, rolling ~3 months
  scmp        南華早報        www.scmp.com/sitemap/archives/articles/YYYY_mon.xml + each page   ~110/day, back to 1991
  skypost     晴報            skypost.hk/store/prod/res/sitemap/sitemap_YYYYMM.xml + each page  ~2/day, back to 2016
  hk01        香港01          web-data.api.hk01.com/v2/articles/<id> (id walk, full text)       ~300 ids/day, back to ~2025-12
  stheadline  星島頭條        www.stheadline.com/realtime-news/<id> → 302 → page (id walk)      ~650 ids/day
  tvb         TVB 無綫新聞    news.tvb.com/tc/<id> → 301 → page (id walk)                       ~200 ids/day, back to ~2025-11
                              (ids below ~1,133,000 are 2012 items: the walk stops there)
  now         NOW 新聞        news.now.com/home/local/player?newsId=<id> (id walk, text included) ~120 ids/day, 2021+
  am730       am730           POST to the section page with the listing cursor                  rolling ~3 months
No archive route: 香港經濟日報 (article pages are rendered in the browser: the HTML has no title or text), 明報 instant news
(ol.mingpao.com: no date route found), 都市日報 (its monthly sitemaps cover only the last 3 months, as the live feed does).
Not probed yet: 香港商報, 881903, 有線, The Standard and the magazines.

Polite: one request at a time per outlet, at least PACE seconds apart; a Chrome TLS fingerprint (curl_cffi).
"""
import argparse, html, json, re, sys, threading, time, urllib.parse
from datetime import date, datetime, timedelta, timezone

import corpus
import resi

HKT = timezone(timedelta(hours=8))
DEFAULT_DAYS = 365      # how far back `all` goes by default (Deyao to confirm; decisions.md 2026-10-13)
RECENT_GAP = 31         # never backfill the last 31 days
PACE = 1.0              # seconds between two requests to one outlet
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"


class Budget(Exception):
    pass


class Client:
    """one outlet's HTTP: curl_cffi with a Chrome fingerprint, paced, counted, bounded"""
    def __init__(self, max_req, deadline, proxy=None):
        self.s = resi.session(proxy)  # counts the proxied bytes per site (resi.py)
        self.max, self.deadline, self.n, self.last, self.bytes = max_req, deadline, 0, 0.0, 0

    def get(self, url, method="GET", data=None, headers=None, redirects=True):
        if self.n >= self.max or time.time() > self.deadline:
            raise Budget()
        wait = self.last + PACE - time.time()
        if wait > 0:
            time.sleep(wait)
        self.n += 1
        h = {"Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"}
        h.update(headers or {})
        for attempt in range(3):
            try:
                r = self.s.request(method, url, data=data, headers=h, timeout=40, allow_redirects=redirects)
                break
            except resi.ProxyOut:  # IPRoyal out of traffic (resi.py): the outlet stops, its cursor stays
                raise
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))
        self.last = time.time()
        self.bytes += len(r.content or b"")
        return r


# ---- page parsing ----

def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def ld_objects(page):
    out = []
    for s in re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            d = json.loads(s.strip())
        except ValueError:
            continue
        stack = [d]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                out.append(o)
                stack.extend(v for v in o.values() if isinstance(v, (dict, list)))
            elif isinstance(o, list):
                stack.extend(o)
    return out


def meta(page, prop):
    m = (re.search(r'<meta[^>]+(?:property|name|itemprop)=["\']%s["\'][^>]*content=["\']([^"\']*)' % re.escape(prop), page, re.I)
         or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name|itemprop)=["\']%s["\']' % re.escape(prop), page, re.I))
    return html.unescape(m.group(1)).strip() if m else None


def iso(t):
    """a date/time string → ISO in HKT (minutes), or None"""
    if not t:
        return None
    t = str(t).strip()
    if re.fullmatch(r"\d{10,13}", t):
        return datetime.fromtimestamp(int(t) / (1000 if len(t) == 13 else 1), HKT).isoformat(timespec="minutes")
    t = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", t.replace("Z", "+00:00"))
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?:\D+(\d{1,2}):(\d{2}))?", t)
        if not m:
            return None
        d = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4] or 0), int(m[5] or 0))
    if d.tzinfo is None:
        d = d.replace(tzinfo=HKT)
    return d.astimezone(HKT).isoformat(timespec="minutes")


def page_article(page):
    """→ (title, published ISO, text, author) from an article page's ld+json / meta tags"""
    title = pub = body = author = None
    for o in ld_objects(page):
        t = o.get("@type")
        t = " ".join(t) if isinstance(t, list) else str(t or "")
        if "Article" in t or "Posting" in t or o.get("articleBody"):
            title = title or o.get("headline")
            pub = pub or o.get("datePublished")
            if isinstance(o.get("articleBody"), str) and len(o["articleBody"]) > len(body or ""):
                body = o["articleBody"]
            a = o.get("author")
            a = a[0] if isinstance(a, list) and a else a
            author = author or (a.get("name") if isinstance(a, dict) else a if isinstance(a, str) else None)
    title = title or meta(page, "og:title") or clean((re.search(r"<title>(.*?)</title>", page, re.S) or [None, ""])[1])
    pub = pub or meta(page, "article:published_time") or meta(page, "datePublished") or meta(page, "pubdate")
    if len(body or "") < 600:  # some ld+json bodies are only a teaser: take the page's paragraphs when longer
        try:
            import cache_fetch
            alt = cache_fetch.article_text(page)
        except Exception:
            alt = ""
        if len(alt or "") > len(body or ""):
            body = alt
    return clean(title) or None, iso(pub), (html.unescape(body or "").strip() or None), author


def slug_title(url):
    s = urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1])
    return re.sub(r"[-_]+", " ", s).strip()


# ---- outlets ----
# each outlet: sid + source (as the live source), kind, and the functions below.
# day kind: unit(c, day) → [row]; month kind: urls(c, "YYYY-MM") → [url | row]; id kind: one(c, id) → row | None.

def oncc_day(c, d):
    ymd = d.strftime("%Y%m%d")
    page = c.get(f"https://orientaldaily.on.cc/section/sitemap/{ymd}").text
    rows = {}
    for path, title in re.findall(r"<a href=['\"](/content/[^'\"]*odn-%s-[0-9_]+[^'\"]*)['\"][^>]*>([^<]+)" % ymd, page):
        sec = urllib.parse.unquote(path.split("/")[2])
        rows.setdefault(path.split("/")[3], {"url": "https://orientaldaily.on.cc" + path, "title": clean(title),
                                              "published": f"{d.isoformat()}T05:00+08:00", "section": sec})
    return list(rows.values())


MINGPAO_SECTIONS = ["s00001", "s00002", "s00003", "s00004", "s00005", "s00012", "s00013", "s00014", "s00015", "s00016", "s00018"]


def mingpao_day(c, d):
    ymd = d.strftime("%Y%m%d")
    rows = {}
    for sec in MINGPAO_SECTIONS:
        page = c.get(f"https://news.mingpao.com/pns/x/section/{ymd}/{sec}").text
        for href, inner in re.findall(r'<a [^>]*?href="\.\./(pns/[^"/]+/article/%s/%s/\d+/[^"]*)"[^>]*>(.*?)</a>' % (ymd, sec), page, re.S):
            aid, title = href.split("/")[5], clean(inner)
            if title and (aid not in rows or rows[aid]["title"] == slug_title(rows[aid]["url"])):
                pass
            elif aid not in rows:
                title = slug_title(href)
            else:
                continue
            if title:
                rows[aid] = {"url": "https://news.mingpao.com/" + href, "title": clean(title),
                             "published": f"{d.isoformat()}T05:00+08:00", "section": urllib.parse.unquote(href.split("/")[1])}
    return list(rows.values())


def rthk_day(c, d):
    page = c.get("https://news.rthk.hk/rthk/ch/news-archive.htm?archive_year=%d&archive_month=%02d&archive_day=%02d&archive_cat=all"
                 % (d.year, d.month, d.day)).text
    out = []
    for href, title, t in re.findall(r"<a href='(/rthk/ch/component/k2/\d+-\d+\.htm)[^']*' title='([^']*)'>.*?"
                                     r"<span class='newsArchiveLiveTime'>([^<]*)</span>", page):
        m = re.match(r"(\d{4}-\d\d-\d\d) HKT (\d\d:\d\d)", t)
        out.append({"url": "https://news.rthk.hk" + href, "title": clean(title),
                    "published": f"{m[1]}T{m[2]}+08:00" if m else f"{d.isoformat()}T12:00+08:00"})
    return out


def tkww_day(paper):
    def f(c, d):
        x = c.get(f"https://www.tkww.hk/epaper/sitemap/{paper}/{d.strftime('%Y%m%d')}/news.xml").text
        out = []
        for loc, title in re.findall(r"<loc>([^<]+)</loc>.*?<news:title>(.*?)</news:title>", x, re.S):
            t = clean(re.sub(r"<!\[CDATA\[|\]\]>", "", title))
            if t:
                out.append({"url": loc.strip(), "title": t, "published": f"{d.isoformat()}T05:00+08:00"})
        return out
    return f


def infogov_day(c, d):
    r = c.get(f"https://www.info.gov.hk/gia/general/{d.strftime('%Y%m')}/{d.strftime('%d')}c.htm")
    raw = r.content
    page = raw.decode("utf-8") if b"charset=utf-8" in raw[:3000].lower() or d.year >= 2018 else raw.decode("big5hkscs", "replace")
    out = {}
    for href, title in re.findall(r'<a[^>]*href="((?:https?://www\.info\.gov\.hk)?/gia/general/\d{6}/\d\d/P\d{12,13}\.htm)"[^>]*>(.*?)</a>', page, re.S | re.I):
        url = urllib.parse.urljoin("https://www.info.gov.hk/", href).replace("http://", "https://")
        if clean(title):
            out.setdefault(url, {"url": url, "title": clean(title), "published": f"{d.isoformat()}T12:00+08:00"})
    return list(out.values())


HKEJ_SECTIONS = ["headline", "hknews", "finnews", "commentary", "cntw", "international", "property", "culture"]


def hkej_day(c, d):
    if not hasattr(c, "hkej_today"):  # every page also links today's paper in its side bar: leave those ids out
        ids = [int(x) for x in re.findall(r"/dailynews/[a-z_]+/article/(\d+)", c.get("https://www1.hkej.com/dailynews/index").text)]
        c.hkej_today = min(ids) if ids else 10 ** 9
    out = {}
    for sec in HKEJ_SECTIONS:
        r = c.get(f"https://www1.hkej.com/dailynews/{sec}?date={d.isoformat()}")
        if r.status_code == 404:
            continue
        for path, s2, aid, inner in re.findall(r"<a[^>]*href=['\"](/dailynews/([a-z_]+)/article/(\d+)/[^'\"]*)['\"][^>]*>(.*?)</a>", r.text, re.S):
            title = clean(re.sub(r"<!--.*?-->", "", inner, flags=re.S))
            if aid not in out and len(title) > 3 and s2 == sec and int(aid) < c.hkej_today:
                out[aid] = {"url": "https://www1.hkej.com" + path, "title": clean(title),
                            "published": f"{d.isoformat()}T05:00+08:00", "section": s2}
    return list(out.values())


MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def scmp_month(c, ym):
    y, m = ym.split("-")
    x = c.get(f"https://www.scmp.com/sitemap/archives/articles/{y}_{MONTHS[int(m) - 1]}.xml").text
    return [{"url": loc, "published": iso(lm)} for lm, loc in re.findall(r"<lastmod>([^<]+)</lastmod>\s*<loc>([^<]+)</loc>", x)]


def skypost_month(c, ym):
    x = c.get(f"https://skypost.hk/store/prod/res/sitemap/sitemap_{ym.replace('-', '')}.xml").text
    best = {}
    for loc in re.findall(r"<loc>([^<]+)</loc>", x):
        m = re.search(r"/article/(\d+)/(.*)", loc)
        if m and (m[1] not in best or len(loc) > len(best[m[1]])):  # listed with and without its slug: keep the slug
            best[m[1]] = loc
    return [{"url": u} for u in best.values()]


def hk01_one(c, i):
    r = c.get(f"https://web-data.api.hk01.com/v2/articles/{i}")
    if r.status_code != 200:
        return None
    d = r.json()
    if not d.get("title") or not d.get("publishTime"):
        return None
    text = []
    for b in d.get("blocks") or []:
        for t in b.get("htmlTokens") or []:
            for tok in (t if isinstance(t, list) else [t]):
                if isinstance(tok, dict) and tok.get("content"):
                    text.append(clean(tok["content"]))
        if b.get("text"):
            text.append(clean(b["text"]))
    return {"url": d.get("canonicalUrl") or f"https://www.hk01.com/x/{i}", "title": clean(d["title"]),
            "published": iso(d["publishTime"]), "text": "\n".join(t for t in text if t) or d.get("description"),
            "section": (d.get("mainCategory") or "") or None,
            "author": ", ".join(a.get("publishName", "") for a in d.get("authors") or [] if a.get("publishName")) or None}


def page_one(url_of, ok=None):
    """id walk over pages: url_of(id) → the page (redirects followed); ok(final url) filters out non-articles"""
    def f(c, i):
        r = c.get(url_of(i))
        if r.status_code != 200 or (ok and not ok(r.url)):
            return None
        title, pub, text, author = page_article(r.text)
        if not title or not pub:
            return None
        return {"url": r.url.split("?")[0] if "now.com" not in r.url else r.url, "title": title, "published": pub,
                "text": text, "author": author}
    return f


AM730_SECTIONS = ["本地", "國際", "中國", "財經", "娛樂", "生活", "體育", "健康", "科技"]


def am730_pages(c, state):
    """am730's own listing, newest first, one section at a time from its cursor; → rows, done"""
    sec_i = state.get("sec", 0)
    if sec_i >= len(AM730_SECTIONS):
        return [], True
    sec = AM730_SECTIONS[sec_i]
    url = "https://www.am730.com.hk/" + urllib.parse.quote(sec)
    data = {"page": state.get("page", 2), "nextPageKey": state.get("key", "99999999"),
            "nextPageDate": state.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))}
    r = c.get(url, method="POST", data=data, headers={"X-Requested-With": "XMLHttpRequest"})
    try:
        d = r.json()["data"]
    except Exception:
        d = {}
    items, cur = d.get("data") or [], (d.get("pagination") or {}).get("nextPageCursor")
    rows = [{"url": "https://www.am730.com.hk" + it["url"], "title": clean(it.get("title")),
             "published": iso(it.get("publishDate")), "section": sec} for it in items if it.get("url") and it.get("title")]
    if cur and items:
        state.update(page=state.get("page", 2) + 1, key=cur["nextPageKey"], date=cur["nextPageDate"])
    else:
        state.update(sec=sec_i + 1, page=2, key="99999999")
        state.pop("date", None)
    return rows, False


OUTLETS = {
    "oncc": dict(sid="oncc", source="東方日報", kind="day", unit=oncc_day),
    "mingpao": dict(sid="mingpao_pns", source="明報", kind="day", unit=mingpao_day, min_day="2023-10-06"),
    "rthk": dict(sid="rthk_local", source="RTHK 香港電台", kind="day", unit=rthk_day, window=365),
    "tkp": dict(sid="takungpao_epaper", source="大公報", kind="day", unit=tkww_day("tkp")),
    "wwp": dict(sid="wenweipo", source="香港文匯報", kind="day", unit=tkww_day("wwp")),
    "infogov": dict(sid="infogov_press", source="政府新聞公報", kind="day", unit=infogov_day),
    "hkej": dict(sid="hkej_daily", source="信報", kind="day", unit=hkej_day, proxy=True, window=110),
    "scmp": dict(sid="scmp_hk", source="南華早報", kind="month", urls=scmp_month, fetch=True),
    "skypost": dict(sid="skypost", source="晴報", kind="month", urls=skypost_month, fetch=True, min_day="2016-01-01"),
    "hk01": dict(sid="hk01", source="香港01", kind="id", one=hk01_one, live_ids=r"hk01\.com/[^/]+/(\d+)"),
    "stheadline": dict(sid="stheadline", source="星島頭條", kind="id", live_ids=r"stheadline\.com/[^/]+/(\d+)",
                       one=page_one(lambda i: f"https://www.stheadline.com/realtime-news/{i}",
                                    lambda u: re.search(r"stheadline\.com/[a-z-]+/\d+", u))),
    "tvb": dict(sid="tvb", source="TVB 無綫新聞", kind="id", live_ids=r"news\.tvb\.com/tc/(\d+)",
                one=page_one(lambda i: f"https://news.tvb.com/tc/{i}", lambda u: "/tc/" in u)),
    "now": dict(sid="now_local", source="NOW 新聞", kind="id", live_ids=r"newsId=(\d+)",
                one=page_one(lambda i: f"https://news.now.com/home/local/player?newsId={i}")),
    "am730": dict(sid="am730", source="am730", kind="cursor", pages=am730_pages, window=120),
}


# ---- the run ----

def today():
    return datetime.now(HKT).date()


def first_live_day(o):
    """the first day of the outlet's live rows (backfilled rows have seen = published; live ones were seen later)"""
    return corpus.db().execute("SELECT min(day) FROM posts WHERE sid LIKE %s AND seen IS DISTINCT FROM published",
                               (o["sid"].split("_")[0] + "%",)).fetchone()[0]


def newest_live_id(o):
    ids = []
    for (u,) in corpus.db().execute("SELECT url FROM posts WHERE sid LIKE %s AND seen IS DISTINCT FROM published "
                                    "ORDER BY id DESC LIMIT 500", (o["sid"].split("_")[0] + "%",)):
        m = re.search(o["live_ids"], u)
        if m:
            ids.append(int(m[1]))
    return max(ids) if ids else None


def probe(o, c, i):
    """the publish day of article id i, or of the nearest valid id just below it → (id, day) or (i, None)"""
    for j in range(i, i - 8, -1):
        row = o["one"](c, j)
        if row and row.get("published"):
            return j, row["published"][:10]
    return i, None


def seek(o, c, start, until):
    """the highest id published on or before `until`: gallop down from `start` in growing steps, then bisect"""
    hi, step, lo = start, 200, None
    while lo is None:
        j, d = probe(o, c, hi - step)
        if d is not None and d <= until.isoformat():
            lo = j
        else:
            hi, step = hi - step, step * 2
        if hi - step <= 0:
            return max(hi - step, 1)
    while hi - lo > 30:
        mid = (lo + hi) // 2
        j, d = probe(o, c, mid)
        if d is not None and d <= until.isoformat():
            lo = mid
        else:
            hi = mid
    return hi


def save(name, o, rows):
    """→ number of new posts"""
    items = []
    for r in rows:
        if not r or not r.get("url") or not r.get("title") or not r.get("published"):
            continue
        items.append({"url": r["url"], "title": r["title"][:500], "published": r["published"], "seen": r["published"],
                      "sid": o["sid"], "source": o["source"], "section": r.get("section"), "text": r.get("text"),
                      "author": r.get("author")})
    return len(corpus.ingest(items)) if items else 0


def run_outlet(name, since=None, until=None, max_req=2000, minutes=50, log=print):
    o = OUTLETS[name]
    key = "backfill:" + name
    st = corpus.kv_get(key) or {}
    t = today()
    lim = t - timedelta(days=RECENT_GAP)
    fl = first_live_day(o)
    until = until or min(lim, (date.fromisoformat(fl) - timedelta(days=1)) if fl else lim)
    since = since or (t - timedelta(days=DEFAULT_DAYS))
    if o.get("min_day"):
        since = max(since, date.fromisoformat(o["min_day"]))
    if o.get("window"):
        since = max(since, t - timedelta(days=o["window"] - 2))
    proxy = resi.url("hk") if o.get("proxy") else None
    if o.get("proxy") and not proxy:
        log(f"{name}: RESI_PROXY not set, skipped")
        return {"outlet": name, "error": "RESI_PROXY not set"}
    c = Client(max_req, time.time() + minutes * 60, proxy)
    new = units = 0
    try:
        if o["kind"] == "day":
            d = date.fromisoformat(st["next"]) if st.get("next") else until
            d = min(d, until)
            while d >= since:
                rows = o["unit"](c, d)
                new += save(name, o, rows)
                units += 1
                d -= timedelta(days=1)
                st.update(next=d.isoformat(), at=datetime.now(HKT).isoformat(timespec="seconds"))
                st["rows"] = st.get("rows", 0) + len(rows)
                corpus.kv_set(key, st)
            st["done_to"] = since.isoformat()
        elif o["kind"] == "month":
            ym = st.get("next") or until.strftime("%Y-%m")
            stop = since.strftime("%Y-%m")
            while ym >= stop:
                todo = st.get("todo")
                if todo is None:
                    todo = o["urls"](c, ym)
                    known = {u for (u,) in corpus.db().execute("SELECT url FROM posts WHERE url = ANY(%s)", ([x["url"] for x in todo],))}
                    todo = [x for x in todo if x["url"] not in known and (not x.get("published")
                            or since.isoformat() <= x["published"][:10] <= until.isoformat())]
                    st["todo"] = todo
                    corpus.kv_set(key, st)
                while todo:
                    x = todo[0]
                    r = c.get(x["url"])
                    if r.status_code == 200:
                        title, pub, text, author = page_article(r.text)
                        pub = pub or x.get("published")
                        if not pub or not since.isoformat() <= pub[:10] <= until.isoformat():
                            pub = None  # outside the window (the sitemap gave no date): not saved
                        new += save(name, o, [{"url": x["url"], "title": title or slug_title(x["url"]),
                                               "published": pub, "text": text, "author": author}])
                    todo.pop(0)
                    units += 1
                    if units % 20 == 0:
                        corpus.kv_set(key, st)
                y, m = map(int, ym.split("-"))
                ym = f"{y - (m == 1)}-{12 if m == 1 else m - 1:02d}"
                st.update(next=ym, todo=None, at=datetime.now(HKT).isoformat(timespec="seconds"))
                corpus.kv_set(key, st)
        elif o["kind"] == "id":
            i = st.get("next")
            if not i:
                start = newest_live_id(o)
                if not start:
                    raise RuntimeError("no live id to start from")
                i = seek(o, c, start, until)
                st["next"] = i
                corpus.kv_set(key, st)
            misses = st.get("misses", 0)
            while True:
                row = o["one"](c, i)
                units += 1
                i -= 1
                if row and row["published"][:10] > until.isoformat():
                    pass  # newer than the backfill window (the live sources have it)
                elif row and row["published"][:10] < since.isoformat():
                    misses += 1  # ids are not strictly in time order: stop after a run of older ones
                    if misses >= 50:
                        st["done_to"] = since.isoformat()
                        break
                elif row:
                    misses = 0
                    new += save(name, o, [row])
                st.update(next=i, misses=misses, at=datetime.now(HKT).isoformat(timespec="seconds"),
                          last_day=(row or {}).get("published", st.get("last_day", ""))[:10] or st.get("last_day"))
                if units % 20 == 0:
                    corpus.kv_set(key, st)
            corpus.kv_set(key, st)
        elif o["kind"] == "cursor":
            cs = st.get("cursor") or {}
            while True:
                rows, done = o["pages"](c, cs)
                units += 1
                rows = [r for r in rows if r.get("published") and since.isoformat() <= r["published"][:10] <= until.isoformat()]
                new += save(name, o, rows)
                st.update(cursor=cs, at=datetime.now(HKT).isoformat(timespec="seconds"))
                corpus.kv_set(key, st)
                if done:
                    st["done_to"] = since.isoformat()
                    break
    except Budget:
        pass
    except Exception as e:
        st["error"] = repr(e)[:200]
        log(f"{name}: {e!r}")
    st["new"] = st.get("new", 0) + new
    st["bytes"] = st.get("bytes", 0) + c.bytes
    st["since"], st["until"] = since.isoformat(), until.isoformat()
    corpus.kv_set(key, st)
    out = {"outlet": name, "units": units, "requests": c.n, "new": new, "kb": c.bytes // 1024, "next": st.get("next"),
           "done_to": st.get("done_to")}
    log(json.dumps(out, ensure_ascii=False))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outlet")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--days", type=int)
    ap.add_argument("--max", type=int, default=2000)
    ap.add_argument("--minutes", type=float, default=50)
    ap.add_argument("--test")
    a = ap.parse_args()
    if a.outlet == "status":
        for n in OUTLETS:
            print(n, json.dumps({k: v for k, v in (corpus.kv_get("backfill:" + n) or {}).items() if k != "todo"}, ensure_ascii=False))
        return
    if a.test:
        o = OUTLETS[a.outlet]
        c = Client(50, time.time() + 300, resi.url("hk") if o.get("proxy") else None)
        rows = (o["unit"](c, date.fromisoformat(a.test)) if o["kind"] == "day" else o["urls"](c, a.test) if o["kind"] == "month"
                else [o["one"](c, int(a.test))] if o["kind"] == "id" else o["pages"](c, {})[0])
        for r in rows[:8]:
            print(json.dumps({k: (v[:80] if isinstance(v, str) else v) for k, v in (r or {}).items()}, ensure_ascii=False))
        print(f"{len(rows)} rows, {c.n} requests, {c.bytes // 1024} KB")
        return
    since = date.fromisoformat(a.since) if a.since else (today() - timedelta(days=a.days) if a.days else None)
    until = date.fromisoformat(a.until) if a.until else None
    names = list(OUTLETS) if a.outlet == "all" else a.outlet.split(",")
    ths = [threading.Thread(target=run_outlet, args=(n, since, until, a.max, a.minutes)) for n in names]
    for t in ths:
        t.start()
    for t in ths:
        t.join()


if __name__ == "__main__":
    main()
