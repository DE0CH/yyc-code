"""Extra sources for the YYC stream (K-Matrix media-list parity): The Standard, 香港仔, 鳳凰網, the magazines, FSTB's
LinkedIn page and the political columns. Same contract as sources.py: SOURCES-style tuples (id, name, url, parser,
needs_proxy); a parser takes the fetched text and returns [{title, url, time, section}]. Stdlib only.
Merge: `from sources_extra import EXTRA` and pass it to fetch_all(EXTRA) (or append it to SOURCES).
Probed and tested 2026-10-12 (sources_extra_check.py); what could not be reached is in sources_extra-failed.md.

Notes
- A few parsers make follow-up requests themselves (香港仔 pages, HKET column article dates, 香港01 column tag feed).
  They go direct; only the entry's main page goes through RESI_PROXY when it is marked needs_proxy (sources.py).
- The column entries (明報·Emily, 明報·聞風筆動) read the same Ming Pao RSS as mingpao_pns_ganwen / _views; they
  only keep that column's items, so the stream shows the column name. If the app dedupes by URL keeping the first
  copy, put EXTRA before SOURCES so the column label wins.
- 香港01·政壇諸事町 and 星島·Kelly Online keep only titles carrying the column name.
"""
import html, json, os, re, time, urllib.error, urllib.parse, concurrent.futures as cf
from datetime import datetime, timedelta, timezone
from sources import *  # noqa: F401,F403  (fetch, clean, rss, rx, parse_time, HKT, UA)
import resi
from sources import fetch, clean, rss, rx, parse_time, parse_relative, HKT, P


def _iso(dt):
    return dt.isoformat() if dt else ""


# ---- The Standard 英文虎報: Google News sitemap (titles + publication dates; the dates are HKT labelled +00:00) ----
# every section (world, entertainment, sports, social-buzz… too: coverage audit 2026-10-14, ~30 stories a day were left out)


def standard_sitemap(text):
    out = []
    for u in re.findall(r"<url>(.*?)</url>", text, re.S):
        loc = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", u)
        t = re.search(r"<news:title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</news:title>", u, re.S)
        d = re.search(r"<news:publication_date>([^<]+)</news:publication_date>", u)
        if not (loc and t):
            continue
        sec = urllib.parse.urlparse(loc.group(1)).path.strip("/").split("/")[0]
        tm = re.sub(r"(Z|[+-]00:00)$", "+08:00", d.group(1).strip()) if d else ""
        out.append({"title": clean(t.group(1)), "url": loc.group(1), "time": tm, "section": sec})
    return out


# ---- 香港仔 (free paper, 大公文匯 group, lionrockdaily.com e-paper API): latest issue's pages → their stories ----
LIONROCK = "https://www.lionrockdaily.com/epaper/api/v1/book/"


def lionrock(text):
    pages = json.loads(text)["data"] or []

    def stories(p):
        d = json.loads(fetch(LIONROCK + "listSectionNews?bookId=hker&sectionId=" + p["uuid"]))
        return [{"title": s["title"], "url": s.get("detailUrl") or
                 "https://www.lionrockdaily.com/epaper/view/newsDetail/%s.html" % s["uuid"],
                 "time": s.get("publishTime") or s.get("publishDate", ""), "section": p.get("sectionName", "")}
                for s in d.get("data") or [] if (s.get("title") or "").strip()]
    out = []
    with cf.ThreadPoolExecutor(8) as ex:
        for got in ex.map(stories, pages):
            out += got
    return out


# ---- 鳳凰網 港澳 channel (news.ifeng.com shanklist page embeds its list as `var allData = {...}`) ----
def ifeng(text):
    d = json.loads(re.search(r"var allData = (\{.*?\});\s*var ", text, re.S).group(1))
    return [{"title": x["title"], "url": x["url"], "time": x.get("newsTime", ""), "section": "港澳"}
            for x in d.get("newsstream") or [] if x.get("title") and x.get("url")]


# ---- Capital 資本雜誌: its Strapi backend, newest first ----
def capital(text):
    return [{"title": a["attributes"]["title"], "url": "https://www.capital-hk.com/" + a["attributes"]["slug"],
             "time": a["attributes"].get("publishedAt", ""), "section": ""} for a in json.loads(text)["data"]]


# ---- 紫荊 (bau.com.hk): ids are snowflakes from 2015-01-01 HKT → creation time (stories publish within ~1 day) ----
BAU_EPOCH_MS = 1420041600000


def bauhinia(text):
    out, seen = [], set()
    for url, sid, title in re.findall(r'<a href="(https://bau\.com\.hk/web/article/(\d+)/web/content_\d+\.html)"[^>]*title="([^"]+)"', text):
        if sid in seen:
            continue
        seen.add(sid)
        ts = ((int(sid) >> 22) + BAU_EPOCH_MS) // 1000
        out.append({"title": clean(title), "url": url, "time": ts, "section": ""})
    return out


# ---- 號外 City Magazine: home page "hot" list; the upload path of each cover image carries the date ----
def citymag(text):
    out = []
    for m in re.finditer(r'<a href="(?P<url>[a-z]+/\d+)" class="hot-item-link">(?P<body>.*?)</a>', text, re.S):
        b = m.group("body")
        d = re.search(r'/upload/(\d\d)/(\d\d)/(\d\d)/', b)
        left = re.search(r'hot-item-text-left">([^<]*)<', b)
        title = re.search(r'hot-item-text-title">([^<]*)<', b)
        t = " ".join(x for x in [clean(left.group(1)) if left else "", clean(title.group(1)) if title else ""] if x)
        if t:
            out.append({"title": t, "url": m.group("url"), "section": m.group("url").split("/")[0],
                        "time": "20%s年%s月%s日" % d.groups() if d else ""})
    return out


# ---- 經濟導報 (jdonline.com.hk) column lists: <a href=content_N.html title=…><h6/><i>YYYY-MM-DD</i> ----
JD = rx(r'<a href="(?P<url>content_\d+\.html)" title="(?P<title>[^"]+)" class="item"\s*target="_blank">\s*<h6>[^<]*</h6>\s*<i[^>]*>(?P<time>\d{4}-\d{2}-\d{2})</i>')


# ---- HKET 投資頻道 / iMoney (imoney.hket.com now redirects to invest.hket.com): sitemap, title from the URL slug ----
def hket_sitemap(text):
    out = []
    for u in re.findall(r"<url>(.*?)</url>", text, re.S):
        loc = re.search(r"<loc>\s*([^<\s]+)", u)
        lm = re.search(r"<lastmod>([^<]+)</lastmod>", u)
        if loc and "/article/" in loc.group(1):
            slug = urllib.parse.unquote(loc.group(1).rstrip("/").rsplit("/", 1)[-1])
            if not slug.isdigit():
                out.append({"title": slug.strip(), "url": loc.group(1), "time": lm.group(1) if lm else "", "section": ""})
    return out


# ---- HKET listing pages (paper.hket.com srapNNN, specialist.hket.com sras103/有政戲, iMoney paper section) ----
# The list shows only "今日" on the newest item, so each article's date is read from its page (cached by URL).
_HKET_DATES = {}


def _hket_date(url, proxy=None):
    if url not in _HKET_DATES:
        m = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(\+\d{2}(?::?\d{2})?)?', fetch(url, proxy=proxy))
        _HKET_DATES[url] = (m.group(1) + "+08:00") if m else ""
    return _HKET_DATES[url]


def hket_listing(limit=6, strip_prefix=""):
    def p(text):
        items = []
        for m in re.finditer(r'<div class="listing-title">\s*<a\s+href="([^"]+)"[^>]*>(.*?)</a>', text, re.S):
            url = urllib.parse.urljoin("https://paper.hket.com/", m.group(1).split("?")[0])
            title = clean(m.group(2))
            if strip_prefix:
                title = re.sub(r"^" + re.escape(strip_prefix) + r"\s*-\s*", "", title)
            if url not in [i["url"] for i in items]:
                items.append({"title": title, "url": url, "section": ""})
        items = items[:limit]
        with cf.ThreadPoolExecutor(6) as ex:
            for it, d in zip(items, ex.map(lambda i: _hket_date(i["url"]), items)):
                it["time"] = d
        return items
    return p


# iMoney's paper section links to invest.hket.com pages (a JS app, no date in the HTML): dates from its paper sitemap
def imoney_listing(text):
    items = []
    for m in re.finditer(r'<div class="listing-title">\s*<a\s+href="([^"]+)"[^>]*>(.*?)</a>', text, re.S):
        url = urllib.parse.urljoin("https://paper.hket.com/", m.group(1).split("?")[0])
        if url not in [i["url"] for i in items]:
            items.append({"title": clean(m.group(2)), "url": url, "section": "", "time": ""})
    dates = {}
    for u in re.findall(r"<url>(.*?)</url>", fetch("https://invest.hket.com/paper.xml"), re.S):
        a, lm = re.search(r"/article/(\d+)", u), re.search(r"<lastmod>([^<]+)</lastmod>", u)
        if a and lm:
            dates[a.group(1)] = lm.group(1)
    for it in items:
        a = re.search(r"/article/(\d+)", it["url"])
        it["time"] = dates.get(a.group(1), "") if a else ""
    return items


# ---- 信報 columns via its full-text search by author, kept to one column label ----
def hkej_column(column):
    def p(text):
        out = []
        for blk in re.split(r'<div class="result">', text)[1:]:
            a = re.search(r'<h3><a href="([^"]+)" title="([^"]*)"', blk)
            ts = re.search(r'<span class="timeStamp">([^<]+)</span>', blk)
            if a and column in blk:
                out.append({"title": clean(a.group(2)), "url": a.group(1), "time": ts.group(1) if ts else "", "section": column})
        return out
    return p


# ---- RSS filtered to one column (明報 【Emily】 in 港聞, 聞風筆動 in 觀點) ----
def rss_title(pattern):
    def p(text):
        return [i for i in rss(text) if re.search(pattern, i["title"])]
    return p


# ---- Google News (site: query) kept to recent items: for magazines behind a Cloudflare challenge ----
def rss_recent(days):
    def p(text):
        cut = datetime.now(timezone.utc) - timedelta(days=days)
        return [i for i in rss(text) if (d := parse_time(i["time"])) and d >= cut
                and len(re.split(r"\s+-\s+", i["title"])[0].strip()) >= 6]  # drops section pages ("家電 - 亞洲週刊")
    return p


def gnews(q):
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=zh-HK&gl=HK&ceid=HK:zh-Hant"


# ---- FSTB LinkedIn: the public company page (no login) carries its recent posts as JSON-LD SocialMediaPosting ----
def linkedin_company(text):
    out = []
    for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.S):
        try:
            d = json.loads(s)
        except ValueError:
            continue
        for g in (d.get("@graph") or [d]) if isinstance(d, dict) else []:
            if g.get("@type") != "SocialMediaPosting":
                continue
            body = (g.get("headline") or g.get("text") or "").strip()
            first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
            first = re.sub(r"^【(.*?)】.*$", r"\1", first) if first.startswith("【") else first
            out.append({"title": first[:200], "url": g.get("mainEntityOfPage") or g.get("url", ""),
                        "time": g.get("datePublished", ""), "section": ""})
    return out


LINKEDIN_ISP_WAIT = 60  # seconds the read waits for the ISP IP (one client at a time, lock 'yyc youtube'), then skips it
LINKEDIN_JINA_TRIES = 3  # r.jina.ai reads, each over a fresh yycstream sticky session (a new residential exit)
JINA_LOG = "linkedin:jina"  # kv: Jina's last 100 answers (time, session, status, error name) for the per-IP question


def linkedin_read(url):
    """the public company page's HTML → (text, route). Measured from the cluster 22 Oct 2026 (~/artifacts/fstb-linkedin
    of that session): every node's WARP exit (104.28.207.215) gets the logged-out page with its 10 JSON-LD posts (40/40
    reads, curl_cffi Chrome; urllib, a mobile UA and hk.linkedin.com too); the ISP IP 81.181.135.61 reads it as well;
    IPRoyal yycstream refuses linkedin.com (CONNECT 403); r.jina.ai answers WARP 401 (bad IP reputation) and a
    residential exit 200, and blocked anonymous linkedin.com reads for hours on 16 Oct (403 AbuseAlleviationError).
    LinkedIn answers some datacenter IPs 999 (its bot wall; earlier WARP exits got it), so the routes in order:
    1. direct with Chrome's TLS fingerprint; 2. YYC's ISP IP (flat rate, under the ISP lock; skipped when the lock is
    busy for LINKEDIN_ISP_WAIT); 3. r.jina.ai keyless, LINKEDIN_JINA_TRIES times, each over a fresh yycstream exit
    (Deyao, 2026-10-22), every answer logged in kv JINA_LOG. A read counts only with at least one post parsed (an
    authwall page is a 200 with none). All fail → RuntimeError naming each route's answer"""
    from curl_cffi import requests as cr
    import corpus
    errs = []

    def ok(r):
        return r.status_code == 200 and linkedin_company(r.text)

    try:
        r = cr.get(url, impersonate="chrome", timeout=30)
        if ok(r):
            return r.text, "direct"
        errs.append(f"direct {r.status_code}")
    except Exception as e:
        errs.append(f"direct {type(e).__name__}")
    isp = os.environ.get("ISP_PROXY")
    if isp:
        try:
            with corpus.lock("yyc youtube", app_name="yyc-linkedin-isp", timeout=LINKEDIN_ISP_WAIT):
                r = cr.get(url, impersonate="chrome", timeout=30, proxies={"http": isp, "https": isp})
            resi.count_isp(len(r.content) + 2000)
            if ok(r):
                return r.text, "isp"
            errs.append(f"isp {r.status_code}")
        except Exception as e:
            errs.append(f"isp {type(e).__name__}")
    if resi.configured():
        for _ in range(LINKEDIN_JINA_TRIES):
            sess = resi.session_id()
            rec = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "session": sess}
            try:
                text = fetch("https://r.jina.ai/" + url, headers={"X-Return-Format": "html", "User-Agent": "yyc-news-stream/1"},
                             proxy=resi.url("hk", sess), timeout=60)  # r.jina.ai 403s browser UAs
                rec["status"] = 200
                if linkedin_company(text):
                    _jina_log(corpus, rec)
                    return text, "jina"
                errs.append("jina 200 no posts")
            except resi.ProxyOut:
                raise
            except urllib.error.HTTPError as e:
                rec["status"] = e.code
                rec["error"] = str(e.reason)[:200]  # Jina's own reason (sources.refusal_reason): AbuseAlleviationError: …
                errs.append(f"jina {e.code}")
            except Exception as e:
                rec["status"], rec["error"] = None, type(e).__name__
                errs.append(f"jina {type(e).__name__}")
            _jina_log(corpus, rec)
    raise RuntimeError("LinkedIn: " + "; ".join(errs))


def _jina_log(corpus, rec):
    try:
        corpus.kv_set(JINA_LOG, ([rec] + list(corpus.kv_get(JINA_LOG) or []))[:100])
    except Exception:
        pass


# ---- 星島 Kelly Online: stheadline's search endpoint (JSON), titles ending in "Kelly Online" ----
def stheadline_column(name):
    def p(text):
        out = []
        for blk in json.loads(text):
            for x in (blk.get("data") or []) if isinstance(blk, dict) else []:
                t = clean(x.get("title", ""))
                if re.search(re.escape(name).replace(r"\ ", r"\s*"), t + " " + x.get("url", ""), re.I):
                    t = re.sub(r"\s*[｜︱|:：]\s*" + re.escape(name).replace(r"\ ", r"\s*") + r"\s*$", "", t, flags=re.I).strip()
                    t = re.sub(r"^\s*" + re.escape(name).replace(r"\ ", r"\s*") + r"\s*[｜︱|:：]\s*", "", t, flags=re.I).strip()
                    out.append({"title": t, "url": "https://www.stheadline.com" + x["url"], "time": x.get("updated_at", ""),
                                "section": (x.get("channel_rect") or {}).get("name", "")})
        return out
    return p


# ---- 香港01 columns: every item of the column's tag feed, plus items of the 政情 category feed whose title names the
# column (not every piece is tagged; not every tagged piece names the column in its title) ----
HK01_API = "https://web-data.api.hk01.com/v2/feed/"


def hk01_column(name, extra_feed):
    def items(text):
        return [{"title": i["data"]["title"], "url": i["data"]["publishUrl"], "time": i["data"]["publishTime"],
                 "section": i["data"].get("mainCategory", "")} for i in json.loads(text).get("items", []) if i.get("type") == 1]

    def p(text):
        got = [dict(it, tagged=True) for it in items(text)]  # the column's tag feed: every item is the column's
        if extra_feed:
            try:
                got += items(fetch(HK01_API + extra_feed))
            except Exception:
                pass
        seen, out = set(), []
        for it in got:
            if (it.pop("tagged", False) or name in it["title"]) and it["url"] not in seen:
                seen.add(it["url"])
                it["title"] = re.sub(r"\s*[｜|]\s*" + re.escape(name) + r"\s*$", "", it["title"])
                out.append(it)
        return out
    return p


# ---- more political columns (2026-10-14, the boss's 政治花絮 benchmark; survey: ~/artifacts/boss-feedback/pol-columns.md
# of that session) ----
def am730_column(name):
    """am730's column list page (/本地/<column>): <h6 class="newsitem-title"><a href="/本地/<id>/<slug>">title｜<name></a>
    with a relative time ("12分鐘前", "3小時前", "4日前", "1星期前", "3個月前", "1年前") next to it"""
    def p(text):
        out, now = [], datetime.now(HKT)
        for m in re.finditer(r'<h6 class="newsitem-title">\s*<a href="(/[^"]+/\d+/[^"]*)">(.*?)</a>(.*?)</li>', text, re.S):
            title = clean(m.group(2))
            if name.lower() not in title.lower():
                continue
            title = re.sub(r"\s*[｜︱|]\s*" + re.escape(name) + r"\s*$|^\s*" + re.escape(name) + r"\s*[｜︱|]\s*", "", title, flags=re.I)
            ago = re.search(r'newsitem-time">\s*([^<]*?)\s*<', m.group(3))  # "12分鐘前" … "1星期前", "3個月前", "1年前"
            when = parse_relative(ago.group(1)) if ago else None  # unread: None, and the page's datePublished fills it
            out.append({"title": title.strip(), "url": urllib.parse.urljoin("https://www.am730.com.hk/", m.group(1)),
                        "time": _iso(when), "section": name})
        return out
    return p


def epoch_author(prefix):
    """大紀元 (HK) author page: <a href="/news/YYYY-MM-DD/ID" aria-label="【column】author｜title">, kept to one column"""
    def p(text):
        out, seen = [], set()
        for m in re.finditer(r'<a href="(/news/(\d{4}-\d{2}-\d{2})/\d+)" aria-label="([^"]+)"', text):
            label = html.unescape(m.group(3))
            if not label.startswith(prefix) or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            title = re.sub(r"^" + re.escape(prefix) + r"[^｜|]*[｜|]\s*", "", label).strip()
            out.append({"title": title, "url": "https://hk.epochtimes.com" + m.group(1), "time": m.group(2) + "T00:00:00+08:00",
                        "section": prefix.strip("【】")})
        return out
    return p


def column_sitemap(suffix, section):
    """a Google News sitemap kept to one column's URLs (東周刊 政官莊: slugs ending in -政官莊)"""
    def p(text):
        out = news_sitemap(keep=re.escape(suffix) + r"/?$")(text)
        for it in out:
            it["title"] = re.sub(r"\s*[｜︱|]\s*" + re.escape(section) + r"\s*$", "", it["title"])
            it["section"] = section
        return out
    return p


def epaper_column(prefix):
    """the 大公文匯 e-paper's last two daily sitemaps (as wwp_epaper reads them), kept to titles starting with prefix"""
    def p(text):
        got = sitemap_children(news_sitemap(), last=2, match=r"/news\.xml$")(text)
        return [dict(it, title=it["title"][len(prefix):].strip(), section=prefix.strip("【】"))
                for it in got if it["title"].startswith(prefix)]
    return p


def column_section(inner, section):
    """another parser's items with the column as their section (a whole feed that is one column)"""
    def p(text):
        return [dict(it, section=section) for it in inner(text)]
    return p


EXTRA = [
    # id, name, url, parser, needs PROXY
    ("thestandard", "英文虎報 The Standard", "https://www.thestandard.com.hk/sitemap.xml", standard_sitemap, False),
    ("hongkongjai", "香港仔", LIONROCK + "sections?bookId=hker&publishDate=&page=0&size=1000&categoryName=", lionrock, False),
    ("ifeng_hkmo", "鳳凰網", "https://news.ifeng.com/shanklist/3-35200-/", ifeng, False),
    # magazines
    ("citymagazine", "號外 City Magazine", "https://www.citymagazine.hk/", citymag, True),
    ("asiatimes", "亞洲時報 Asia Times", "https://asiatimes.com/feed/", rss, False),
    ("bauhinia", "紫荊", "https://bau.com.hk/", bauhinia, False),
    ("capital", "資本雜誌 Capital", "https://prd-backend.capital-hk.com/api/articles?sort=publishedAt:desc"
     "&pagination%5Blimit%5D=30&fields%5B0%5D=title&fields%5B1%5D=slug&fields%5B2%5D=publishedAt", capital, False),
    ("edigest", "經濟一週", "https://www.edigest.hk/feed/", rss, False),
    ("jdonline_hk", "經濟導報", "https://www.jdonline.com.hk/column_10007_1.html", JD, False),
    ("jdonline_fin", "經濟導報", "https://www.jdonline.com.hk/column_11_1.html", JD, False),
    ("imoney_paper", "iMoney 智富", "https://paper.hket.com/srap016/iMoney", imoney_listing, False),
    ("imoney_invest", "iMoney 智富 (HKET 投資頻道)", "https://invest.hket.com/instantnews.xml", hket_sitemap, False),
    ("mpweekly", "明週", "https://www.mpweekly.com/culture/feed", rss, False),
    # government social
    # read by linkedin_read (sources.READERS): direct, then the ISP IP, then r.jina.ai over fresh yycstream exits
    ("fstb_linkedin", "財經事務及庫務局 LinkedIn", "https://www.linkedin.com/company/fstbhk", linkedin_company, False),
    # political columns
    ("col_mp_emily", "明報·Emily", "https://news.mingpao.com/rss/pns/s00002.xml", rss_title(r"^【Emily】"), False),
    ("col_mp_wenfeng", "明報·聞風筆動", "https://news.mingpao.com/rss/pns/s00012.xml", rss_title(r"^聞風筆動"), False),
    ("col_hkej_gaotianyou", "信報·新聞點評", "https://search.hkej.com/template/fulltextsearch/php/search.php?author="
     + urllib.parse.quote("高天佑"), hkej_column("新聞點評"), True),
    ("col_hkej_yujinxian", "信報·香港脈搏", "https://search.hkej.com/template/fulltextsearch/php/search.php?author="
     + urllib.parse.quote("余錦賢"), hkej_column("香港脈搏"), True),
    ("col_hket_youzhengxi", "經濟日報·有政戲", "https://specialist.hket.com/sras103/" + urllib.parse.quote("有政戲"),
     hket_listing(6, "有政戲"), False),
    ("col_hket_zhengqing", "經濟日報·政情動向", "https://paper.hket.com/srap026/?dis=" + urllib.parse.quote("更多專欄文章")
     + "&srcid=001&col=" + urllib.parse.quote("政情動向") + "&aut=&org=", hket_listing(6), False),
    ("col_st_kelly", "星島·Kelly Online", "https://www.stheadline.com/search_post?q=" + urllib.parse.quote("Kelly Online")
     + "&type=1", stheadline_column("Kelly Online"), False),
    ("col_hk01_zhengtan", "香港01·政壇諸事町", HK01_API + "tag/17643?bucketId=00000",
     hk01_column("政壇諸事町", "category/310?bucketId=00000"), False),
    ("col_hk01_zhengquan", "香港01·政圈風聲", HK01_API + "tag/13925?bucketId=00000",
     hk01_column("政圈風聲", "category/310?bucketId=00000"), False),
    ("col_hk01_zhizhishuo", "香港01·政・直說", HK01_API + "tag/54282?bucketId=00000", hk01_column("政・直說", None), False),
    ("col_st_daqipan", "星島·大棋盤", "https://www.stheadline.com/search_post?q=" + urllib.parse.quote("大棋盤") + "&type=1",
     stheadline_column("大棋盤"), False),
    ("col_am730_ray", "am730·Ray Online", "https://www.am730.com.hk/" + urllib.parse.quote("本地") + "/Ray%20Online",
     am730_column("Ray Online"), False),
    ("col_eastweek_zhengguan", "東周刊·政官莊", "https://eastweek.stheadline.com/sitemap.xml", column_sitemap("政官莊", "政官莊"), False),
    ("col_epoch_jiema", "大紀元·解碼新香港", "https://hk.epochtimes.com/category/" + urllib.parse.quote("專欄") + "/"
     + urllib.parse.quote("林兆彬"), epoch_author("【解碼新香港】"), False),
    ("col_wwp_huaxu", "文匯報·花絮", "https://www.tkww.hk/epaper/sitemap/wwp/sitemapIndex.xml",
     epaper_column("【花絮】"), False),
    ("col_tsangyoksing", "曾鈺成", "https://tsangyoksing.hk/feed/", column_section(rss, "曾鈺成"), False),
    ("col_realsteel", "王慧麟·真如鐵", "https://hkwanmundo.substack.com/feed", column_section(rss, "真如鐵"), False),
    ("col_hkfeature", "誌 HK Feature", "https://hkfeature.substack.com/feed", rss, False),
    # a weekly newsletter digest of HK politics and courts: a source, not a column (not in 政治花絮)
    ("thecollective", "集誌社 The Collective", "https://thecollectivehongkong.substack.com/feed", rss, False),
    # 集誌社's own site (2026-10-20: its articles reached us only through Google News): its WordPress feed, the whole
    # article in <content:encoded>; a SiteGround site, its robot challenge passed by walls.py
    ("thecollectivehk", "集誌社", "https://thecollectivehk.com/feed/", rss, False),
]


# ---- Wider coverage (2026-10-13, the WisersOne alternative): every article of the HK papers, magazines and news
# sites, mostly from the publishers' Google News sitemaps (<news:title> + <news:publication_date>, the last ~2 days
# of every section). Probed from a Jarvis 2 session and from the old Hetzner server on 2026-10-13. ----

def _seg(url, i):
    """the i-th path segment of a URL, percent-decoded ('' when missing)"""
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    return urllib.parse.unquote(parts[i]) if len(parts) > i else ""


def slug_title(url, strip_id=True):
    """a title from the URL's last segment (sitemaps without <news:title>): '16964778-中歐-global-emba' → '中歐 global emba'.
    fulltext_fetch.py replaces it with the page's own title when it reads the article."""
    s = urllib.parse.unquote(urllib.parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    s = re.sub(r"\.(s?html?)$", "", s)
    if strip_id:
        s = re.sub(r"^\d+-", "", s)
    return re.sub(r"[-_]+", " ", s).strip()


def news_sitemap(section_seg=None, sections=None, keep=None, slug=False, host_sections=None):
    """a sitemap (<urlset>): title from <news:title> (else the URL slug when slug=True), time from
    <news:publication_date> (else <lastmod>), section from path segment section_seg (mapped through `sections` when
    given) or from the host's first label (host_sections: {label: section}), only URLs matching the regex `keep`"""
    def p(text):
        out = []
        for u in re.findall(r"<url>(.*?)</url>", text, re.S):
            loc = re.search(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\s\]]+)", u)
            if not loc:
                continue
            url = html.unescape(loc.group(1))
            if keep and not re.search(keep, url):
                continue
            t = re.search(r"<(news|n):title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</\1:title>", u, re.S)  # SCMP: n:
            title = clean(t.group(2)) if t else (slug_title(url) if slug else "")
            if not title:
                continue
            d = re.search(r"<(?:news|n):publication_date>\s*([^<\s]+)", u) or re.search(r"<lastmod>\s*([^<\s]+)", u)
            sec = _seg(url, section_seg) if section_seg is not None else ""
            if host_sections:
                sec = host_sections.get((urllib.parse.urlparse(url).hostname or "").split(".")[0], "")
            out.append({"title": title, "url": url, "time": d.group(1) if d else "",
                        "section": (sections or {}).get(sec, sec)})
        return out
    return p


def video_sitemap(section_seg=None):
    """a video sitemap (<video:video>): title from <video:title>, time from <video:publication_date>, the post's text
    from <video:description>, section from path segment section_seg; a cut-off last entry (a head read) is skipped"""
    def p(text):
        out = []
        for u in re.findall(r"<url>(.*?)</url>", text, re.S):
            loc = re.search(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\s\]]+)", u)
            t = re.search(r"<video:title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</video:title>", u, re.S)
            if not loc or not t or not clean(t.group(1)):
                continue
            url = html.unescape(loc.group(1))
            d = re.search(r"<video:publication_date>\s*([^<\s]+)", u)
            desc = re.search(r"<video:description>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</video:description>", u, re.S)
            out.append({"title": clean(t.group(1)), "url": url, "time": d.group(1) if d else "",
                        "text": html.unescape(desc.group(1)).strip() if desc else "",
                        "section": _seg(url, section_seg) if section_seg is not None else ""})
        return out
    return p


def sitemap_children(inner, first=None, last=None, match=None):
    """a sitemap index: fetch the first (newest-first indexes) or last (oldest-first) `n` child sitemaps whose URL
    matches `match`, and parse each with `inner`"""
    def p(text):
        locs = [html.unescape(x) for x in re.findall(r"<sitemap>\s*<loc>\s*([^<\s]+)", text)]
        if match:
            locs = [x for x in locs if re.search(match, x)]
        locs = locs[:first] if first else locs[-last:]
        out = []
        for x in locs:
            out += inner(fetch(x))
        return out
    return p


def orangenews_channel(text):
    """橙新聞's own channel list (api.orangenews.hk get_channel_data, the JSON its section pages load): the newest
    stories of one channel, each under the URL its sitemaps give later (/<channelPath>/<newsCode>/<title with every
    non-word character a '-'>.shtml), time = releaseDate (HKT). Its sitemaps are rebuilt every 6 hours, this list at once"""
    out = []
    for x in (json.loads(text).get("data") or {}).get("records") or []:
        title, code, path = (x.get("title") or "").strip(), x.get("newsCode"), x.get("channelPath")
        if not (title and code and path):
            continue
        slug = re.sub(r"[^\w]", "-", title).strip("-")
        t = (x.get("releaseDate") or "").strip()
        out.append({"title": title, "url": f"https://www.orangenews.hk/{path}/{code}/{slug}.shtml",
                    "time": t.replace(" ", "T") + "+08:00" if t else "", "section": x.get("channelName") or ""})
    return out


ORANGENEWS_API = "https://api.orangenews.hk/app/bus/channel/common/get_channel_data"
ORANGENEWS_CHANNELS = {"orangenews_hk": 119, "orangenews_cn": 121, "orangenews_intl": 120, "orangenews_biz": 117,
                       "orangenews_property": 127}  # 港聞, 兩岸, 國際, 產經, 地產 (channel ids from the section pages)


def anchors(href, base, min_title=6, section=None):
    """<a href=…>title</a> links whose href matches the regex `href` (the time from its named group `time` when it
    has one); the title from the link text or its title attribute; first copy of each URL. A link that wraps a card
    (a heading, then a summary and a time: Now 財經's 「<h3>headline</h3><div class="leading">…」) takes its heading's
    text: the whole link text glued the summary onto the headline"""
    rxh = re.compile(href)

    def p(text):
        out, seen = [], set()
        for m in re.finditer(r'<a\b([^>]*)>(.*?)</a>', text, re.S):
            h = re.search(r'href="([^"]+)"', m.group(1))
            if not h:
                continue
            hm = rxh.search(html.unescape(h.group(1)))
            if not hm:
                continue
            url = urllib.parse.urljoin(base, html.unescape(h.group(1))).split("#")[0]
            ta = re.search(r'title="([^"]+)"', m.group(1))
            hd = re.search(r"<h([1-6])\b[^>]*>(.*?)</h\1>", m.group(2), re.S)
            head = clean(hd.group(2)) if hd else ""
            title = (head if len(head) >= min_title else "") or clean(m.group(2)) or (clean(ta.group(1)) if ta else "")
            if len(title) < min_title or url in seen:
                continue
            seen.add(url)
            gd = hm.groupdict()
            out.append({"title": title, "url": url, "time": gd.get("time") or "", "section": gd.get("section") or section or ""})
        return out
    return p


def bastille_sitemap(text):
    """巴士的報: the month's post sitemaps from the index (`text`; gen-sitemap/post/YYYY-MM, newest month first; the
    month before too on the first day of a month), every post. The "today" sitemap is paged and starts over each UTC
    day, so posts fell off it. No <news:title>: the title comes from the slug. <lastmod> is UTC written as +08:00
    (14 Oct 17:28 "+08:00" is the page's 15 Oct 01:28 HKT): read as UTC."""
    locs = [html.unescape(x) for x in re.findall(r"<sitemap>\s*<loc>\s*([^<\s]+)", text) if "/gen-sitemap/post/" in x]
    locs = locs[:2] if datetime.now(HKT).day == 1 else locs[:1]
    out = []
    for x in locs:
        for it in news_sitemap(slug=True, keep=r"/hongkong/article/\d+")(fetch(x)):
            it["time"] = re.sub(r"\+08:00$", "+00:00", it["time"])
            out.append(it)
    return recent_rows(out)


WALK_RECHECK = (600, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 24 * 3600)  # an id with no article yet: looked at again
WALK_GIVE_UP = 3 * 86400   # an id still without a published article 3 days after it was first tried is dropped
WALK_PER_RUN = 600         # new ids read per poll at most; a longer backlog (the first run) goes on next poll
WALK_KEEP = 6 * 3600       # an article found is offered again on every run for 6 h: a poll lost before its corpus write
                           # (the poller restarted) doesn't lose it (the corpus takes a URL once)


def id_walk(key, newest, read, back, threads):
    """every article id in turn, from the last one read up to `newest`, each through read(id) → item | None (no
    published article under that id yet); a story the sitemaps and feeds leave out still comes in. Ids with no
    article are tried again on WALK_RECHECK and dropped after WALK_GIVE_UP. State in kv `key`: {next, pending: {id:
    [first tried, last tried, tries]}}. A network error leaves the id where it was."""
    import corpus
    st = corpus.kv_get(key) or {}
    now = time.time()
    nxt = int(st.get("next") or newest - back)
    pending = {int(k): v for k, v in (st.get("pending") or {}).items()}
    due = [i for i, (first, last, n) in pending.items() if now - last >= WALK_RECHECK[min(n - 1, len(WALK_RECHECK) - 1)]]
    new = list(range(nxt, min(newest, nxt + WALK_PER_RUN - 1) + 1))
    out, done = [], {}
    with cf.ThreadPoolExecutor(threads) as ex:
        futs = {ex.submit(read, i): i for i in sorted(set(new) | set(due))}
        for f, i in futs.items():
            try:
                done[i] = f.result()
            except Exception:
                continue
            if done[i] is not None:
                out.append(done[i])
    for i, d in done.items():
        if d is not None:
            pending.pop(i, None)
        else:
            first, last, n = pending.get(i, [now, now, 0])
            pending[i] = [first, now, n + 1]
    for i in [i for i, v in pending.items() if now - v[0] >= WALK_GIVE_UP]:
        pending.pop(i)
    unread = [i for i in new if i not in done]
    recent = {k: v for k, v in (st.get("recent") or {}).items() if now - v[0] < WALK_KEEP}
    for it in out:
        recent[it["url"]] = [now, dict(it, text=(it.get("text") or "")[:3000])]
    found = {it["url"] for it in out}
    out += [v[1] for k, v in recent.items() if k not in found]
    corpus.kv_set(key, {"next": unread[0] if unread else (new[-1] + 1 if new else nxt),
                        "pending": {str(i): v for i, v in pending.items()}, "recent": recent})
    return out


HK01_ARTICLE = "https://web-data.api.hk01.com/v2/articles/%d"
HK01_WALK_BACK = 2500  # ids the first run reads back (~2 days: 香港01 hands out ~1,200 ids a day)
HK01_THREADS = 6       # 香港01's API answered 4,901 requests at 6 at a time (~20 a second) with no 429 (2026-10-14)


def hk01_read(i):
    try:
        d = json.loads(fetch(HK01_ARTICLE % i))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    if not (d.get("title") and d.get("publishTime") and d.get("publishUrl")):
        return None
    body = []
    for b in d.get("blocks") or []:
        if b.get("blockType") in ("text", "summary"):
            for line in b.get("htmlTokens") or []:
                t = "".join(x.get("content", "") for x in (line if isinstance(line, list) else [line]) if isinstance(x, dict)).strip()
                if t:
                    body.append(clean(t))
    return {"title": d["title"], "url": d["publishUrl"], "time": d["publishTime"], "section": d.get("mainCategory") or "",
            "text": "\n".join(body) or d.get("description"),
            "author": ", ".join(a.get("publishName", "") for a in d.get("authors") or [] if a.get("publishName")) or None}


def hk01_walk(text):
    """香港01: every article id up to the newest in the category feed (`text`) through the article API (title, time,
    channel, authors, the whole text): the sitemap and the feeds leave out some stories (9 of 730 on 13–14 Oct)"""
    newest = max([int(i["data"]["articleId"]) for i in json.loads(text).get("items") or []
                  if i.get("type") == 1 and str(i.get("data", {}).get("articleId", "")).isdigit()] or [0])
    return id_walk("hk01_walk", newest, hk01_read, HK01_WALK_BACK, HK01_THREADS) if newest else []


STHEADLINE_WALK_BACK = 2500  # ids (~2.5 days: ~1,000 a day)
STHEADLINE_THREADS = 4       # 2,601 pages at 4 at a time: one 502, no 429 (2026-10-14)


def stheadline_read(i):
    """/realtime-news/<id> redirects to the article in any channel; no article = a "404" page (status 200)"""
    import cache_fetch
    page = fetch(f"https://www.stheadline.com/realtime-news/{i}")
    m = re.search(r'<link rel="canonical" href="(https://www\.stheadline\.com/[a-z0-9-]+/\d+/[^"]*)"', page)
    t = re.search(r'<meta property="og:title"\s+content="([^"]+)"', page) or re.search(r"<title>([^<]+)</title>", page)
    when = cache_fetch.page_date(page)
    if not (m and t and when):
        return None
    url = urllib.parse.unquote(html.unescape(m.group(1)))
    return {"title": clean(t.group(1)), "url": url, "time": when, "section": _seg(url, 0),
            "text": cache_fetch.article_text(page)}


def stheadline_walk(text):
    """星島頭條: every article id up to the newest in its RSS (`text`), each page read once (title, time, channel, the
    whole text): the sitemap leaves out the 星島日報 paper (daily-*) and opinion (66 of 363 stories on 13 Oct)"""
    newest = max([int(x) for x in re.findall(r"stheadline\.com/[a-z0-9-]+/(\d{6,})", text)] or [0])
    return id_walk("stheadline_walk", newest, stheadline_read, STHEADLINE_WALK_BACK, STHEADLINE_THREADS) if newest else []


MONTH_SITEMAP_DAYS = 3  # a month sitemap read keeps the rows of the 3 days before its newest row (the first read took all)


def recent_rows(items):
    """the rows modified in the MONTH_SITEMAP_DAYS before the newest one (by their time); undated rows kept"""
    ts = [(parse_time(it["time"]), it) for it in items]
    newest = max([t for t, _ in ts if t] or [None]) if any(t for t, _ in ts) else None
    if not newest:
        return items
    return [it for t, it in ts if not t or t >= newest - timedelta(days=MONTH_SITEMAP_DAYS)]


def metrohk_sitemap(text):
    """都市日報: every post of the month from its article sitemaps (the RSS holds the newest 30, and the print edition
    lands ~90 posts within minutes around 03:30). The index lists sitemap-articles-YYYY-MM.xml newest month first;
    the month before is read too on the first day of a month. No titles: the slug without its id suffix
    ('…-2e0590') until fulltext_fetch.py reads the page's own; the section is the first path segment."""
    locs = [html.unescape(x) for x in re.findall(r"<sitemap>\s*<loc>\s*([^<\s]+)", text) if "sitemap-articles-" in x]
    locs = locs[:2] if datetime.now(HKT).day == 1 else locs[:1]
    out = []
    for x in locs:
        for u in re.findall(r"<url>(.*?)</url>", fetch(x), re.S):
            loc = re.search(r"<loc>\s*([^<\s]+)", u)
            if not loc:
                continue
            url = html.unescape(loc.group(1))
            slug = urllib.parse.unquote(urllib.parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
            title = re.sub(r"[-_]+", " ", re.sub(r"-[0-9a-f]{6}$", "", slug)).strip()
            lm = re.search(r"<lastmod>\s*([^<\s]+)", u)
            if title:
                out.append({"title": title, "url": url, "time": lm.group(1) if lm else "", "section": _seg(url, 0)})
    return recent_rows(out)


ONCC_SECTIONS = {"news": "港澳", "finance": "財經", "intnews": "兩岸國際", "commentary": "評論", "entertainment": "娛樂",
                 "sport": "體育", "cnnews": "中國", "lifestyle": "生活", "weather": "天氣"}


def oncc_sitemap(text):
    """on.cc's news sitemap: every story of every section (instant news, weather, the racing site /bknrac/: 馬經)"""
    out = news_sitemap(3, ONCC_SECTIONS, keep=r"on\.cc/[a-z]+/bkn/cnt/")(text)
    for it in out:
        if "/bknrac/" in it["url"]:
            it["section"] = "馬經"
    return out
HKET_SITES = {"news": "新聞", "inews": "即時新聞", "invest": "投資", "china": "中國", "ps": "地產", "paper": "報章",
              "wealth": "理財", "topick": "TOPick", "specialist": "專家觀點", "knowledge": "知識", "topschool": "教育"}
WWP = r"/a/(?P<time>\d{6}/\d{2})/[A-Za-z0-9]+\.html$"   # 文匯 / 點新聞 (the 大公文匯 CMS): /a/YYYYMM/DD/AP….html

COVERAGE = [
    # 東方: on.cc instant news (every section) and the Oriental Daily paper (every page), both news sitemaps
    ("oncc_instant", "東方日報 on.cc", "https://hk.on.cc/sitemap.xml", oncc_sitemap, False),
    ("oncc_daily", "東方日報", "https://orientaldaily.on.cc/sitemap.xml", news_sitemap(1), False),
    # 香港01: the newest articles of every channel (by publication time)
    ("hk01_sitemap", "香港01", "https://www.hk01.com/sitemap.xml", news_sitemap(0), False),
    ("hk01_walk", "香港01", "https://web-data.api.hk01.com/v2/feed/category/0?bucketId=00000", hk01_walk, False),
    # 星島 / 頭條: every channel of stheadline.com
    ("stheadline_sitemap", "星島頭條", "https://www.stheadline.com/sitemap-index.xml", news_sitemap(0), False),
    ("stheadline_walk", "星島頭條", "https://www.stheadline.com/rss", stheadline_walk, False),
    ("am730_sitemap", "am730", "https://www.am730.com.hk/sitemap.xml", news_sitemap(0), False),
    # 明報: the day's instant-news and daily-paper sitemaps (newest day first in each index; today and yesterday)
    ("mingpao_ins_sitemap", "明報", "https://news.mingpao.com/sitemap/ins.xml",
     sitemap_children(news_sitemap(1), first=2, match=r"/\d{8}/ins\.xml$"), False),
    ("mingpao_pns_sitemap", "明報", "https://news.mingpao.com/sitemap/pns.xml",
     sitemap_children(news_sitemap(1), first=2, match=r"/\d{8}/pns\.xml$"), False),
    # 經濟日報: every HKET site (news, inews, invest, china, ps, paper, wealth, topick, specialist …)
    ("hket_sitemap", "香港經濟日報", "https://www.hket.com/sitemap.xml", news_sitemap(host_sections=HKET_SITES), False),
    # 大公報 / 文匯報 / 香港仔 print editions: the e-paper's daily sitemaps (oldest day first in each index)
    ("tkp_epaper", "大公報", "https://www.tkww.hk/epaper/sitemap/tkp/sitemapIndex.xml",
     sitemap_children(news_sitemap(), last=2, match=r"/news\.xml$"), False),
    ("wwp_epaper", "香港文匯報", "https://www.tkww.hk/epaper/sitemap/wwp/sitemapIndex.xml",
     sitemap_children(news_sitemap(), last=2, match=r"/news\.xml$"), False),
    # 文匯網 sections
    ("wenweipo_hk", "香港文匯報", "https://www.wenweipo.com/hongkong", anchors(WWP, "https://www.wenweipo.com/", section="香港"), False),
    ("wenweipo_inland", "香港文匯報", "https://www.wenweipo.com/inland", anchors(WWP, "https://www.wenweipo.com/", section="內地"), False),
    ("wenweipo_world", "香港文匯報", "https://www.wenweipo.com/world", anchors(WWP, "https://www.wenweipo.com/", section="國際"), False),
    ("wenweipo_business", "香港文匯報", "https://www.wenweipo.com/business", anchors(WWP, "https://www.wenweipo.com/", section="財經"), False),
    ("wenweipo_comment", "香港文匯報", "https://www.wenweipo.com/comment", anchors(WWP, "https://www.wenweipo.com/", section="評論"), False),
    # 今日文匯 = the print edition: some print articles are listed only here, not in the online sections
    ("wenweipo_today", "香港文匯報", "https://www.wenweipo.com/todaywenwei", anchors(WWP, "https://www.wenweipo.com/", section="今日文匯"), False),
    # 南華早報: the news sections' RSS (Hong Kong is sources.py's scmp_hk)
    ("scmp_news", "南華早報", "https://www.scmp.com/rss/91/feed", rss, False),
    # every section (sport, tech, lifestyle, magazines… have no feed we read): the Google News sitemap, 2 days, ~110 a day
    ("scmp_sitemap", "南華早報", "https://www.scmp.com/sitemap/google-news.xml", news_sitemap(0), False),
    ("scmp_china", "南華早報", "https://www.scmp.com/rss/4/feed", rss, False),
    ("scmp_business", "南華早報", "https://www.scmp.com/rss/92/feed", rss, False),
    ("scmp_world", "南華早報", "https://www.scmp.com/rss/5/feed", rss, False),
    ("scmp_opinion", "南華早報", "https://www.scmp.com/rss/93/feed", rss, False),
    # its videos and photo galleries are in none of the feeds above (SCMP coverage audit, 2026-10-20): the head of the
    # two sitemaps that list them newest first (sources.HEAD_BYTES)
    ("scmp_video", "南華早報", "https://www.scmp.com/sitemap/video-format.xml", video_sitemap(1), False),
    ("scmp_photos", "南華早報", "https://www.scmp.com/sitemap/galleries.xml", news_sitemap(1, slug=True), False),
    # 信報 daily paper: the other sections (through RESI_PROXY like sources.py's hkej_daily_*)
    ("hkej_daily_cntw", "信報", "https://www1.hkej.com/dailynews/cntw", P["hkej_daily"], True),
    ("hkej_daily_international", "信報", "https://www1.hkej.com/dailynews/international", P["hkej_daily"], True),
    ("hkej_daily_views", "信報", "https://www1.hkej.com/dailynews/views", P["hkej_daily"], True),
    ("hkej_daily_property", "信報", "https://www1.hkej.com/dailynews/property", P["hkej_daily"], True),
    ("hkej_daily_culture", "信報", "https://www1.hkej.com/dailynews/culture", P["hkej_daily"], True),
    # broadcasters' full news lists
    ("tvb_sitemap", "TVB 無綫新聞", "https://news.tvb.com/sitemap.xml", news_sitemap(keep=r"news\.tvb\.com/tc/\d+"), False),
    ("cr881903_sitemap", "商業電台 881903", "https://www.881903.com/sitemap-google-news.xml", news_sitemap(1), False),
    # other HK news sites
    ("orangenews", "橙新聞", "https://www.orangenews.hk/sitemap/news_latest.xml", news_sitemap(0), False),
    ("orangenews_finance", "橙新聞", "https://www.orangenews.hk/sitemap/finance_latest.xml", news_sitemap(0), False),
    *[(sid, "橙新聞", ORANGENEWS_API, orangenews_channel, False) for sid in ORANGENEWS_CHANNELS],
    ("dotdotnews", "點新聞", "https://www.dotdotnews.com/", anchors(r"dotdotnews\.com" + WWP, "https://www.dotdotnews.com/"), False),
    ("bastillepost", "巴士的報", "https://www.bastillepost.com/hongkong/sitemap/generate/index", bastille_sitemap, False),
    ("metrohk_sitemap", "都市日報", "https://metrohk.com.hk/sitemap.xml", metrohk_sitemap, False),
    ("hkfp", "香港自由新聞 HKFP", "https://hongkongfp.com/feed/", rss, False),
    ("thewitness", "法庭線 The Witness", "https://thewitnesshk.com/feed/", rss, False),
    # 獨立媒體 (inmediahk) is read by browser_fetch.py: Cloudflare refuses every plain client here (403)
    ("chinadailyhk", "中國日報香港版 China Daily HK", "https://www.chinadailyhk.com/",
     anchors(r"^/article/\d+", "https://www.chinadailyhk.com/", min_title=10), False),
    ("yahoo_hk", "Yahoo 新聞", "https://hk.news.yahoo.com/rss/hong-kong", rss, False),
]


# ---- Hong Kong English sources (an audit found them missing, 2026-10-14; probed 2026-10-14). RTHK English, news.gov.hk
# English and the English press releases are in sources.py next to their Chinese feeds. ----

def hkbusiness_rss(text):
    """Hong Kong Business: its RSS <title> holds the article's dek, not its headline; fulltext_fetch.py replaces it
    with the page's own og:title (SLUG_TITLES). The feed (50 newest) replaces the paged Drupal sitemap, whose 23
    pages (~8 MB) carry no titles and no order."""
    return rss(text)


def youngpost(text):
    """Young Post (SCMP's youth paper, now youngpostclub.com): its Next.js page data lists each article as
    articleId, headLine, …, publishDate, …, entityId[, urlAlias]; the link comes from urlAlias or the page's <a href>
    with that id. Its articles sitemap is 9 MB without ranges, so the news pages are read instead."""
    y = text.replace('\\"', '"')
    hrefs = {m.group(1): m.group(0) for m in re.finditer(r"/yp/[a-z0-9/-]*?article/(\d+)/[a-z0-9-]+", y)}
    out, seen = [], set()
    for blk in y.split('"articleId":')[1:]:
        h = re.search(r'"headLine":"((?:[^"\\]|\\.)*)"', blk)
        d = re.search(r'"publishDate":"([^"]+)"', blk)
        e = re.search(r'"entityId":"(\d+)"', blk)
        if not (h and e) or e.group(1) in seen:
            continue
        u = re.search(r'"entityId":"\d+","urlAlias":"([^"]+)"', blk)
        path = u.group(1) if u else hrefs.get(e.group(1))
        if not path:
            continue
        seen.add(e.group(1))
        sec = re.search(r'"currentSectionName":"([^"]*)"', blk)
        title = json.loads('"' + h.group(1) + '"') if "\\" in h.group(1) else h.group(1)
        out.append({"title": clean(title), "url": "https://www.youngpostclub.com" + path, "time": d.group(1) if d else "",
                    "section": sec.group(1) if sec else ""})
    return out


BLOOMBERG_RSS = ("markets", "politics", "technology", "economics", "industries", "business", "wealth", "bview")


def bloomberg(text):
    """Bloomberg: every headline of its Google News sitemap (all of them, ~300 a day; the scorer decides), plus the
    one-line summary its section RSS feeds carry for some stories (the only text: no article pages are read; paywall,
    and Bloomberg's terms forbid scraping)"""
    items = news_sitemap(1)(text)
    summary = {}
    for feed in BLOOMBERG_RSS:
        try:
            for it in rss(fetch(f"https://feeds.bloomberg.com/{feed}/news.rss")):
                if it["desc"] and len(it["desc"]) > 20:
                    summary.setdefault(it["url"].split("?")[0], (it["desc"], feed))
        except Exception:
            pass  # a feed that fails (wealth is often empty) leaves its stories without a summary
    for it in items:
        s = summary.get(it["url"].split("?")[0])
        if s:
            it["text"] = s[0]
    return items


REUTERS_PAGES = ("&from=50",)  # the news sitemap's second page: 50 stories each, ~700 a day, so 100 = ~3 hours


REUTERS_FULL = "https://www.reuters.com/arc/outboundfeeds/sitemap/?outputType=xml"  # every story incl. Breakingviews


def reuters_fetch(url):
    """one more Reuters sitemap page, through the source's DataDome session (paywall.reuters_page)"""
    import paywall
    return paywall.reuters_page(url)


def reuters(text):
    """Reuters: headlines from its Google News sitemap (arc outboundfeeds, paged with from=N), plus the stories
    only its full sitemap lists (Breakingviews: never in the news sitemap; coverage audit 2026-10-14): the newest 200 by
    lastmod, title from the URL slug until the page's own replaces it (SLUG_TITLES), time = lastmod when the URL's date
    (…-YYYY-MM-DD/) is that day, else the URL's date (an old story updated is not news). Every page is read with the
    solved DataDome session (READERS reuters, reuters_fetch: the sitemaps answer 401 to plain clients since 30 Oct 2026)"""
    out = news_sitemap(0)(text)
    for q in REUTERS_PAGES:
        try:
            out += news_sitemap(0)(reuters_fetch("https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml" + q))
        except Exception:
            pass
    have = {x["url"] for x in out}
    for q in ("", "&from=100"):
        try:
            full = news_sitemap(0, slug=True)(reuters_fetch(REUTERS_FULL + q))
        except Exception:
            continue
        for it in full:
            if it["url"] in have:
                continue
            have.add(it["url"])
            m = re.search(r"-(\d{4}-\d{2}-\d{2})/?$", it["url"])
            it["title"] = slug_title(re.sub(r"-\d{4}-\d{2}-\d{2}/?$", "", it["url"]))
            it["title"] = it["title"][:1].upper() + it["title"][1:]
            if m and not it["time"].startswith(m.group(1)):
                it["time"] = m.group(1)
            out.append(it)
    return out


def rss_unescaped(text):
    """RSS whose titles are escaped twice (FinanceAsia: Malaysia&amp;#39;s)"""
    return [dict(it, title=html.unescape(it["title"])) for it in rss(text)]


def mingtiandi_rss(text):
    """Mingtiandi: RSS whose summary ends in 'Read More>>' and WordPress's 'The post … appeared first on' line"""
    out = rss(text)
    for it in out:
        it["desc"] = re.sub(r"\s*(\.\.\.|…)?\s*Read More>>.*$", "…", it["desc"], flags=re.S).strip()
    return out


ENGLISH = [
    ("dimsumdaily", "Dimsum Daily", "https://www.dimsumdaily.hk/feed/", rss, False),  # 6 newest items: every poll (5 min)
    ("hkbusiness", "Hong Kong Business", "https://hongkongbusiness.hk/rss.xml", hkbusiness_rss, False),
    ("dotdotnews_en", "DotDotNews", "https://english.dotdotnews.com/",
     anchors(r"english\.dotdotnews\.com" + WWP, "https://english.dotdotnews.com/"), False),
    ("nikkei_asia", "Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar", rss, False),  # no dates in the feed
    ("ft_hk", "Financial Times", "https://www.ft.com/hong-kong?format=rss", rss, False),  # paywall: headline + summary
    # Mingtiandi's CDN serves /feed/ a week stale; a fresh query string gets the live feed
    ("mingtiandi", "Mingtiandi", "https://www.mingtiandi.com/feed/?t={now}", mingtiandi_rss, False),
    ("financeasia", "FinanceAsia", "https://www.financeasia.com/rss/latest", rss_unescaped, False),
    ("asiafinancial", "Asia Financial", "https://www.asiafinancial.com/feed", rss, False),
    ("reuters", "Reuters", "https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml", reuters, False),
    ("bloomberg", "Bloomberg", "https://www.bloomberg.com/sitemaps/news/latest.xml", bloomberg, False),
    ("youngpost_hk", "Young Post", "https://www.youngpostclub.com/yp/news/hong-kong", youngpost, False),
    ("youngpost_news", "Young Post", "https://www.youngpostclub.com/yp/news", youngpost, False),
    # every story (Spark, learning zone…): the articles sitemap lists newest first; only its first 200 KB is read
    # (sources.HEAD_BYTES; ~700 stories, weeks back), titles from the slug until the page's own replaces them
    ("youngpost_sitemap", "Young Post", "https://www.youngpostclub.com/sitemap/articles.xml", news_sitemap(0, slug=True), False),
]
GLOBAL_SIDS = {"nikkei_asia", "ft_hk", "mingtiandi", "reuters", "bloomberg"}  # = corpus.GLOBAL_SIDS (region Global)

SLUG_TITLES = {"bastillepost", "hkbusiness", "reuters", "youngpost_sitemap", "metrohk_sitemap", "scmp_photos"}  # titles not the headline: fulltext_fetch.py takes the page's own title
EXTRA += COVERAGE + ENGLISH

# sitemaps and pages read less often than every poll (the rest, e.g. on.cc, 香港01, 星島, am730, 明報, TVB, 881903, HKET,
# 點新聞, 文匯 港聞, China Daily: 30-190 KB a read, sites that list a story within minutes, are read every poll: the
# capture-lag audit of 15 Oct 2026, tests/test_capture_lag_news.py)
import sources as _s  # noqa: E402
_s.EVERY.update({"oncc_daily": 3600, "mingpao_pns_sitemap": 3600,
                 "tkp_epaper": 3600, "wwp_epaper": 3600,
                 "orangenews": 900, "orangenews_finance": 1800, "bastillepost": 900, "metrohk_sitemap": 1800,
                 "wenweipo_inland": 1800, "wenweipo_world": 1800, "wenweipo_business": 1800, "wenweipo_comment": 1800, "wenweipo_today": 1800,
                 "hkej_daily_cntw": 1800, "hkej_daily_international": 1800,
                 "hkej_daily_views": 1800, "hkej_daily_property": 1800, "hkej_daily_culture": 1800,
                 # the political columns added 2026-10-14 publish a few times a week at most
                 "col_st_daqipan": 1800, "col_am730_ray": 1800, "col_eastweek_zhengguan": 1800,
                 "col_hk01_zhizhishuo": 3600, "col_epoch_jiema": 3600, "col_wwp_huaxu": 3600, "col_tsangyoksing": 3600,
                 "col_realsteel": 3600, "col_hkfeature": 3600, "thecollective": 3600, "thecollectivehk": 900,
                 # English sources (2026-10-14): Bloomberg and Reuters every 10 min (the newest page of their sitemaps
                 # spans ~1.5 h), the weekly-paced outlets every 30 min, Young Post (Crawl-delay 10) hourly
                 "bloomberg": 600, "reuters": 600, "hkbusiness": 900, "dotdotnews_en": 900, "nikkei_asia": 900,
                 "ft_hk": 1800, "mingtiandi": 1800, "financeasia": 1800, "asiafinancial": 1800,
                 "youngpost_hk": 3600, "youngpost_news": 3600, "youngpost_sitemap": 3600,
                 "scmp_sitemap": 600, "scmp_video": 900, "scmp_photos": 900})
# video-format.xml is 6.4 MB (20,000 videos, ~1 KB each), galleries.xml 157 KB: both newest first, a few new a day
_s.HEAD_BYTES.update({"scmp_video": 60_000, "scmp_photos": 20_000})
# 橙新聞 cuts its gzip responses short (IncompleteRead); uncompressed it answers in full
_s.READERS["fstb_linkedin"] = linkedin_read


def reuters_read(url):
    """Reuters' sitemaps behind DataDome (since 30 Oct 2026: 401 to every plain client) → paywall.reuters_read"""
    import paywall
    return paywall.reuters_read(url)


_s.READERS["reuters"] = reuters_read
_s.HEADERS.update({"orangenews": {"Accept-Encoding": "identity"}, "orangenews_finance": {"Accept-Encoding": "identity"}})
_s.POST_DATA.update({sid: urllib.parse.urlencode({"limit": "30", "handlerName": "contentPageListHandler", "page": "1",
                                                  "params": json.dumps({"channelId": str(ch), "channelType": "3", "isPc": "1"})}).encode()
                     for sid, ch in ORANGENEWS_CHANNELS.items()})


# ---- sites.json "kind": "parser" (2026-10-23, the source-gap hosts Google News showed: Deyao "add the real gaps"):
# a site whose list takes more than one request or a step the generic kinds lack. Each parser: (the text of the
# site's url, the site, get) → [{title, url, time, section, author?, text?}]; get(url) reads another page by the
# site's route (sites_fetch.read's reader: direct, proxy or Chrome). Tests: tests/test_gap_walls.py ----

FLAMINGWHEELS = "https://www.flamingwheels.online/"


def flamingwheels(text, site=None, get=None):
    """香港風火時報: the list its pages POST for (nowall_ct_news_select.do, newest first, the whole story in each row);
    a story's page is /m/<YYYYMMDD>/<id>.htm (the day of its createDate, Hong Kong time)"""
    out = []
    for x in (json.loads(text) or {}).get("list") or []:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2})(:\d{2})?", x.get("createDate") or "")
        if not (x.get("id") and (x.get("title") or "").strip() and m):
            continue
        out.append({"title": clean(x["title"]), "url": f"{FLAMINGWHEELS}m/{m[1]}{m[2]}{m[3]}/{x['id']}.htm",
                    "time": f"{m[1]}-{m[2]}-{m[3]}T{m[4]}{m[5] or ':00'}+08:00",
                    "section": clean(x.get("columnName") or "").lstrip("├└ "),
                    "text": _s.paragraphs(x.get("content") or "")})
    return out


LINE_TODAY = "https://today.line.me/"
LINE_SKIP = ("AD", "POLL_ROTATE", "QUIZ", "WEATHER", "COUNT_DOWN", "BANNER", "COMBO")


def line_today(text, site=None, get=None):
    """LINE TODAY 台灣 (an aggregator: 自由, 民視, 鏡報… republished): the site's url is one tab's page data
    (webapi/portal/page/<tab>), "tabs" more tabs read the same way; every article list on them (their modules'
    listings: headline, digest, the topics' and 最新訊息 queries; not ads, polls, the per-reader ForYou and the
    most-read ranks) read with /api/v6/listings/<id>, each article once, its publisher as the author"""
    pages = [text] + [get(f"{LINE_TODAY}webapi/portal/page/{t}?country=tw") for t in (site or {}).get("tabs") or []]
    ids = []
    for p in pages:
        for m in (json.loads(p) or {}).get("modules") or []:
            if m.get("source") != "LISTING" or m.get("type") in LINE_SKIP:
                continue
            ids += [x["id"] for x in m.get("listings") or [] if x.get("id") and x["id"] not in ids]
    out, seen = [], set()
    for i in ids:
        try:
            d = json.loads(get(f"{LINE_TODAY}api/v6/listings/{i}?offset=0&length=100&country=tw"))
        except Exception:  # noqa: BLE001: one list that fails leaves the others
            continue
        for x in d.get("items") or []:
            h = (x.get("url") or {}).get("hash") if isinstance(x.get("url"), dict) else None
            if not h or h in seen or not (x.get("title") or "").strip():
                continue
            seen.add(h)
            out.append({"title": clean(x["title"]), "url": f"{LINE_TODAY}tw/v3/article/{h}",
                        "time": x.get("publishTimeUnix") or "", "section": x.get("categoryName") or "",
                        "author": x.get("publisher") or None})
    return out


PRESSREADER = "https://ingress.pressreader.com/services/"
PRESSREADER_INIT = "https://www.pressreader.com/authentication/v1/initialize"
_pr_token = {"at": 0, "token": None}


def pressreader_token():
    """PressReader's anonymous bearer token (no account: what its own pages get), kept 45 min (it lasts about an hour)"""
    if not _pr_token["token"] or time.time() - _pr_token["at"] > 2700:
        d = json.loads(fetch(PRESSREADER_INIT, data=json.dumps({"tickets": [], "language": "en-us", "urlReferrer": "",
                                                               "url": "https://www.pressreader.com/"}).encode(),
                             headers={"Content-Type": "application/json"}))
        _pr_token.update(at=time.time(), token=d["bearerToken"])
    return _pr_token["token"]


def pressreader_get(path, token=None):
    """one PressReader API answer (ingress.pressreader.com/services/<path>) with the anonymous token → JSON"""
    return json.loads(fetch(PRESSREADER + path, headers={"Authorization": "Bearer " + (token or pressreader_token())}))


def pressreader(text, site=None, get=None):
    """PressReader's e-paper of one title ("cid": its catalogue id, "slug": its address name). The site's url is
    PressReader's token request (POST authentication/v1/initialize: the anonymous token its own pages get, no
    account); with it the title's record names the latest issue, and that issue's table of contents (free) gives one
    row per article, dated the issue's day. The article's text comes from PressReader's article API
    (cache_fetch.pressreader_text)"""
    token = (json.loads(text) or {}).get("bearerToken")
    if not token:
        raise RuntimeError("PressReader gave no anonymous token")
    _pr_token.update(at=time.time(), token=token)
    key = (pressreader_get(f"catalog/v2/publications/{site['cid']}", token).get("latestIssue") or {}).get("key") or ""
    m = re.match(r"\w+?(\d{4})(\d{2})(\d{2})\d{14}$", key)
    if not m:
        return []
    toc = pressreader_get(f"toc/?issue={key}&version=2", token)
    out, seen = [], set()
    for pg in toc.get("Pages") or []:
        for a in pg.get("Articles") or []:
            if not a.get("Id") or not (a.get("Title") or "").strip() or a["Id"] in seen:
                continue
            seen.add(a["Id"])
            out.append({"title": clean(a["Title"]), "time": f"{m[1]}-{m[2]}-{m[3]}",
                        "url": f"https://www.pressreader.com/hong-kong/{site['slug']}/{m[1]}{m[2]}{m[3]}/{a['Id']}",
                        "section": clean(pg.get("SectionName") or ""), "author": clean(a.get("Byline") or "") or None})
    return out


STUDENT_HK = "https://student.hk/request/xys_pc/"


def student_hk_sign(path, ms=None):
    """the "sign" header student.hk's own script (its axios interceptor) puts on every API call: base64 of
    {"url", "method", "timestamp"}, its two halves' characters swapped at steps of 6, 2, 8 and 3; unsigned calls get an
    empty answer"""
    import base64
    s = base64.b64encode(json.dumps({"url": path, "method": "post", "timestamp": int(ms if ms is not None else time.time() * 1000)},
                                    separators=(",", ":")).encode()).decode()
    n, h = list(s), len(s) // 2
    for c in (6, 2, 8, 3):
        for i in range(0, h, c):
            n[i], n[h + i] = n[h + i], n[i]
    return "".join(n)


def student_hk(text, site=None, get=None):
    """學友社 student.hk (a Nuxt page; its lists come from a signed API): each of the site's "columns" (columnId: name)
    read with newNews/getNewsColumnData, newest first, the whole story in each row (content). The story's page is
    the site's own /commonDetial?rid=… route. The site's url (its home page) is not read: fetches_itself"""
    out, seen = [], set()
    for cid, name in (site or {}).get("columns", {}).items():
        path = "newNews/getNewsColumnData"
        d = json.loads(fetch(STUDENT_HK + path, data=json.dumps({"columnId": cid, "pageNum": 0, "pageSize": 30,
                                                                  "appInfoId": 561}).encode(),
                             headers={"Content-Type": "application/json", "sign": student_hk_sign(path),
                                      "Origin": "https://www.student.hk", "Referer": "https://www.student.hk/"}) or "{}")
        for x in ((d.get("data") or [{}])[0] or {}).get("list") or []:
            if not x.get("id") or not (x.get("title") or "").strip() or x["id"] in seen:
                continue
            seen.add(x["id"])
            out.append({"title": clean(x["title"]), "time": (x.get("pubDate") or "").replace(" ", "T") + "+08:00" if x.get("pubDate") else "",
                        "url": "https://www.student.hk/commonDetial?" + urllib.parse.urlencode({"rid": x["id"], "columnId": cid, "breadCrumbName": name}),
                        "section": name, "author": ((x.get("mapProperties") or {}).get("authorValue") or None),
                        "text": _s.paragraphs(x.get("content") or "")})
    return out


student_hk.fetches_itself = True  # sites_fetch.read: the site's url is not read first

SITE_PARSERS = {"flamingwheels": flamingwheels, "line_today": line_today, "pressreader": pressreader,
                "student_hk": student_hk}
