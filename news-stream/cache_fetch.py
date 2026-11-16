#!/usr/bin/env python3
"""The "cached" copy K-Matrix shows next to every post: the article text of each post that a profile tagged relevant
in the last DAYS days and that has no text yet (most sources give only a headline). Run by the yyc-cache timer every
30 minutes (infra/provision.sh), in the venv, under xvfb-run (for the browser pass below).

    python3 cache_fetch.py            one run (at most MAX_PAGES pages)
    python3 cache_fetch.py <url>…     print what the extractor gets from these pages (test; writes nothing)

Per page: LIHKG threads through LIHKG's own API (the first post), 高登 threads through HKGolden's (hkgolden.py), YouTube videos from the watch page's data
(description and view count), everything else as a page: the article body from its ld+json articleBody or, failing
that, the largest cluster of <p> paragraphs (inside <article> when there is one), with scripts, navigation, headers
and footers stripped; paragraphs are kept. 信報 and 香港討論區 refuse the server's IP, so their pages go through
RESI_PROXY as their listings do (sources.py); 自由時報 and 菱傳媒 refuse the cluster's IP and go through YYC's ISP IP
(ISP_HOSTS, env ISP_PROXY). Reuters goes through paywall.reuters (a solved DataDome session on the
yycstream proxy), Bloomberg's story comes from its page data (paywall.bloomberg_text), and a paywalled page's public
part is marked an excerpt (meta "via"), which the archive job (paywall.py) completes from archive.today. Sites behind Cloudflare's managed challenge
(成報, 亞洲週刊, 明報月刊) are read in one real-browser pass at the end, like browser_fetch.py. Social posts (Facebook,
Instagram, Threads, X and the TikHub platforms) are not fetched: their text comes from their collectors. PDFs (a
.pdf link, a page that only opens one, a notice's attached ones: ATTACH_XPATHS) are read with PyMuPDF; a catalogue
site's article pages are asked with its list's own headers (CATALOGUE_HEADERS).
A page that fails is marked (text "", cached_at set) so it isn't retried every run.
Polite: one request at a time per host, at most RATE requests a second overall, 20 s timeout, sources.UA.
"""
import html, json, os, re, sys, threading, time, urllib.error, urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import canonical, corpus, hkgolden, lihkg_api, nomedia, paywall, resi, sources, walls

DAYS = 3
MAX_PAGES = 300
RATE = 4            # requests per second, all hosts together
TIMEOUT = 20
TEXT_MAX = 20000
SKIP_HOSTS = ("facebook.com", "fb.com", "instagram.com", "threads.net", "threads.com", "x.com", "twitter.com",
              "douyin.com", "xiaohongshu.com", "weibo.cn", "weibo.com", "bilibili.com", "kuaishou.com", "zhihu.com",
              "ixigua.com", "mp.weixin.qq.com", "tiktok.com", "reddit.com", "lemon8-app.com", "pipix.com",
              "linkedin.com", "news.google.com", "t.me")
# Reuters (DataDome), Bloomberg and the paywalled outlets are read too (Deyao, 2026-10-15): paywall.py
SOLVER_HOSTS = ("reuters.com",)  # only through paywall.reuters (a solved DataDome session on the residential proxy)
PROXY_HOSTS = ("hkej.com", "discuss.com.hk")                      # refuse the server's IP (sources.py does the same)
# refuse the cluster's IP (HTTP 403 to it, 200 to YYC's IPRoyal ISP IP, 2026-10-20): read on the ISP IP (env ISP_PROXY,
# flat rate, one client at a time: sites_fetch.isp_lock) before the per-GB residential one: 自由時報, 菱傳媒 (Google News)
ISP_HOSTS = ("ltn.com.tw", "turnnewsapp.com")
ISP_TRIES = 3  # the ISP IP answers "Tunnel connection failed: 403" while another client holds it
BROWSER_HOSTS = ("singpao.com.hk", "yzzk.com", "mingpaomonthly.com",  # Cloudflare managed challenge (browser_fetch.py)
                 "finance.mingpao.com")  # 明報 instant finance pages (the challenge answers 403)
# pages a real browser reads although their host serves others plainly: 明報's instant news refuses plain clients per
# section (經濟: the challenge's 403; 娛樂: an IIS "400 Invalid URL"; 港聞: 200), its daily paper (/pns/) never
BROWSER_PATHS = ("news.mingpao.com/ins/",)


def _catalogue_hosts():
    """the catalogue sites' hosts by route (sites.json): an article page goes the way its site's list goes (proxy
    "isp" → ISP_HOSTS, "hk" / "mo" / true → PROXY_HOSTS, "browser" → BROWSER_HOSTS; "page_proxy" → PROXY_HOSTS
    whatever the list's route), and with the list's own
    "headers" (CATALOGUE_HEADERS); and the WordPress sites listed by their REST API on the residential proxy
    (WP_API_HOSTS)"""
    import json as _json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites.json")
    isp, resi_, br, wp = set(), set(), set(), set()
    AGGREGATOR_HOSTS.clear()
    try:
        sites = _json.load(open(path))
    except (OSError, ValueError):
        return (), (), (), ()
    for s_ in sites:
        h = (urllib.parse.urlsplit(s_.get("url") or "").hostname or "").removeprefix("www.")
        if not h:
            continue
        if s_.get("headers"):
            CATALOGUE_HEADERS.setdefault(h, dict(s_["headers"]))
        if s_.get("tls_noverify"):
            NOVERIFY.add(h)
        if s_.get("aggregator"):
            AGGREGATOR_HOSTS.add(h)
        if s_.get("kind") == "wp_json" and s_.get("proxy") and s_.get("proxy") != "isp":
            wp.add(h)
        if isinstance(s_.get("proxy"), str) and s_["proxy"] != "isp":
            PROXY_COUNTRY[h] = s_["proxy"]  # yycstream with the list's exit country (Taiwan's sites: tw)
        if s_.get("browser"):
            br.add(h)
        elif s_.get("page_proxy"):  # its article pages take another route than its list (uwants: the ISP IP gets
            resi_.add(h)            # a reCAPTCHA wall on every thread page, yycstream the thread, 2026-10-21)
        elif s_.get("proxy") == "isp":
            isp.add(h)
        elif s_.get("proxy"):
            resi_.add(h)
    return tuple(sorted(isp)), tuple(sorted(resi_)), tuple(sorted(br)), tuple(sorted(wp))


# aggregators (sites.json "aggregator": true; Deyao, 2026-10-21: headline4hk.com): a post's page is a copy of another
# outlet's story with a link to it (og:url / canonical on the outlet's site, 「星島原文連結」): fetch_one gives that link as
# meta["original"], and the full-text readers follow it (follow.pointer) and keep the original's text
# (fulltext_fetch.save). By host, no "www."
AGGREGATOR_HOSTS = set()
ORIGINAL_LINK = re.compile(r"原文|原始連結|original (article|story)|read more at|source link", re.I)
# the headers a catalogue site's list is read with, by host (no "www."): its article pages need them too (2026-10-20:
# 澳門導報's WAF lets through only a request with its "waf_verified=1" cookie, 澳門法治報 serves its story only to a
# Googlebot User-Agent, a 1.4 KB shell to a browser's)
CATALOGUE_HEADERS = {}
# catalogue sites read without checking their TLS certificate ("tls_noverify": the server leaves out its intermediate
# certificate; 澳門城市大學 2026-10-20: every article page failed CERTIFICATE_VERIFY_FAILED while its list was read)
NOVERIFY = set()
# catalogue sites on yycstream with an exit country of their own ("proxy": "tw", "us", "my", "jp"…; 2026-10-23: Taiwan's
# 華視, 中華日報, 台灣醒報… answer only Taiwan IPs): their article pages take the same exit, by host (no "www."); the rest
# of PROXY_HOSTS go out in Hong Kong
PROXY_COUNTRY = {}
# WordPress sites listed through their REST API on the residential proxy (their pages refuse the cluster's IP): an
# article is read from the API by its slug (fetch_one; 澳門平台 2026-10-20: pages 403 on every route, the API open)
_cat_isp, _cat_resi, _cat_browser, WP_API_HOSTS = _catalogue_hosts()
ISP_HOSTS += _cat_isp
PROXY_HOSTS += _cat_resi
BROWSER_HOSTS += _cat_browser
NOVERIFY_HOSTS = tuple(sorted(NOVERIFY))
# sites that ban an IP that reads too fast: seconds between two requests to them, from every reader (get()).
# 澳門人才網 job853.com (2026-10-20): after ~28 job pages 2 s apart it answers HTTP 403 「您的IP因異常請求已被臨時封禁，
# 請稍後訪問。」 to every page for about 10 minutes (the readers saved those posts as "")
# Thomasnet (2026-10-20): read 2 s apart it answered 403 to 15 of 57 article pages (urllib and curl_cffi alike), 200
# to the same pages minutes later. A SLOW_SITES site's 403 is its rate limit: HTTP 429, read again later
SLOW_SITES = {"job853.com": 10.0, "thomasnet.com": 6.0}
BANNED = re.compile(r"您的IP因異常請求已被臨時封禁")  # a 403 with this body is a rate limit (429): tried again later


def catalogue_headers(url):
    """the headers of the catalogue list whose host serves url (CATALOGUE_HEADERS), else None"""
    h = host(url).removeprefix("www.")
    return next((v for k, v in CATALOGUE_HEADERS.items() if h == k or h.endswith("." + k)), None)
JUNK = re.compile(r"cookie|版權所有|all rights reserved|訂閱|subscribe|javascript|廣告|advertisement|相關新聞|延伸閱讀|"
                  r"下載.{0,6}app|share this|分享至|追蹤我們|follow us|特别声明|特別聲明|Notice: The content above|"
                  r"概不構成任何投資意見|資料由.{2,20}提供|"
                  r"^(copyright|©)\s*(©\s*)?\d{4}|"  # a page footer (TVB: "Copyright 2026 TVB Limited", 2026-10-20)
                  r"不支援你正使用的瀏覽器|最新的瀏覽器版本|pccwmediaiapps@|"
                  r"^分享工具$", re.I)  # NOW's player notice (2026-10-20); RTHK's share bar label (2026-10-21)


# a line that is only a date and time, with at most a short label (TVB 「發佈： 2026-10-20 16:10」, RTHK
# 「2026-10-20 HKT 09:35」): a page's dateline, never its story (2026-10-20)
DATELINE = re.compile(r"\W{0,3}\w{0,6}\W{0,3}\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}日?\s*(HKT)?\s*\d{1,2}[:：]\d{2}([:：]\d{2})?\s*")
# boilerplate that runs long (disclaimers, copyright lines): junk at any length. The other JUNK words are junk only in
# a short line: a story paragraph may say them (明報 2026-10-20: 台鐵's 政治廣告 story lost every paragraph to 廣告)
LONG_JUNK = re.compile(r"版權所有|all rights reserved|特别声明|特別聲明|Notice: The content above|概不構成任何投資意見|"
                       r"^声明：新华财经|"  # 新華財經 (cnfin.com)'s disclaimer under every story (2026-10-20)
                       r"資料由.{2,20}提供|^(copyright|©)\s*(©\s*)?\d{4}|"
                       r"^\d{4}\s*[-–]\s*\d{4},?\s*(copyright|©)|"  # 「1998-2024, Copyright ©2024 Quam.net …」 (2026-10-21)
                       r"不支援你正使用的瀏覽器|最新的瀏覽器版本|pccwmediaiapps@", re.I)
JUNK_SHORT = 30   # characters: a line shorter than this with a JUNK word is a label or button (「廣告」, 「訂閱明報」)
JUNK_LEAD = 120   # a line up to this long that STARTS with a JUNK word is a link line (「相關新聞：…」, 「延伸閱讀：…」)


def junk(t):
    """a line of page furniture (share buttons, ad labels, 相關新聞 links, cookie notes, disclaimers), not of the story"""
    if LONG_JUNK.search(t[:80]):
        return True
    m = JUNK.search(t[:80])
    return bool(m and (len(t) < JUNK_SHORT or (m.start() <= 8 and len(t) < JUNK_LEAD)))


BOX = re.compile(r"(^|\s)(fc-(dialog|consent|root)|consent|cookie|gdpr|modal|popup|loginbox|paywall)", re.I)
# sites that append the next story to an article page (infinite scroll) in a second copy of the story's own block:
# only the first is the page's story (巴士的報 2026-10-20: 「往下看更多文章」 then a second div.article-body, which
# was often the longer one and taken for the story). Keyed by the host the page states (canonical / og:url).
FIRST_ONLY = {"bastillepost.com": '//div[contains(concat(" ", normalize-space(@class), " "), " article-body ")]'}
# a page footer that isn't a <footer>, by class or id (2026-10-20: 大公文匯's <div class="page-footer"> … WWW.TKWW.HK
# LIMITED.; 紫荊's <div class="zj-footer"> address <p>s; 香港商報's <div id="footer2">)
FOOT = re.compile(r"(^|\s)([a-z0-9]+-)?footer\d*(\s|$)", re.I)
# the description a site puts on every page (its own blurb), never a story's (2026-10-20: 香港商報's short stories and
# 文匯報's chart-only pages stored it): 文匯報, 香港商報, 大公文匯網, 大公報, 點新聞 (Chinese and English), 橙新聞
SITE_DESC = re.compile(r"1948年9月9日在香港創刊|^香港商報是一份歷史悠久|^大公文匯網是香港大公文匯傳媒集團官方網站|"
                       r"^「忘己之為大，無私之謂公」|^點新聞立足香港，環顧國際|^Through dots, we connect|"
                       r"^橙新聞\(OrangeNews\.hk\)是一個|^headline for hk、headline4hk")  # headline4hk (2026-10-21)


def host(url):
    return (urllib.parse.urlparse(url).hostname or "").lower()


def site(url):
    """the site a host belongs to (hket.com for inews/video/topick.hket.com; info.gov.hk; on.cc): its subdomains share
    one rate limit, so the full-text readers pace them as one"""
    parts = host(url).split(".")
    n = 3 if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("com", "org", "net", "edu", "gov", "co") else 2
    return ".".join(parts[-n:])


# ---- extraction ----

def ld_body(doc):
    best = ""
    for s in doc.xpath('//script[contains(@type, "ld+json")]/text()'):
        try:
            d = json.loads(s.strip())
        except ValueError:  # not JSON (信報's instant news, 2026-10-20: "datePublished": 2026-…", a quote missing):
            for m in re.finditer(r'"articleBody"\s*:\s*("(?:[^"\\]|\\.)*")', s):  # its articleBody as written
                try:
                    b = json.loads(m.group(1), strict=False)  # strict=False: raw newlines inside the string
                except ValueError:
                    continue
                if len(b) > len(best):
                    best = b
            continue
        stack = [d]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                b = o.get("articleBody")
                if isinstance(b, str) and len(b) > len(best):
                    best = b
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
    return best


def ptext(el):
    return re.sub(r"[ \t　\xa0]+", " ", el.text_content()).strip()


TEMPLATE = re.compile(r"\{\{.*\}\}", re.S)  # an unrendered template ({{recommend.title}}, {{hket:inline-image …}})
HIDDEN_MAX = 300  # a display:none element with less text than this is not part of the story


def link_only(el):
    """an element whose text is all link text (a related-article list, a tag cloud)"""
    t = re.sub(r"\s+", "", el.text_content())
    return bool(t) and t == re.sub(r"\s+", "", "".join(a.text_content() for a in el.iter("a")))


def cluster_lines(best):
    """the lines of the winning paragraph cluster in order: its <p>s, and the sub-headings and bullet lists between
    them (晴報, 2026-10-20: <h2> cross-heads and <div class="bulletpotint"><ul><li>), never link-only ones; when the
    story is split into several blocks of the same kind between pictures (資本: <div class="prose …"> blocks), every
    such block of the same parent"""
    blocks = [best]
    parent, cls = best.getparent(), best.get("class")
    if parent is not None and cls:
        blocks = [b for b in parent.iterchildren(best.tag) if b.get("class") == cls and b.find("p") is not None] or [best]
    for block in blocks:
        kids = list(block.iterchildren())
        last_p = max((i for i, el in enumerate(kids) if el.tag == "p"), default=-1)
        for i, el in enumerate(kids):
            if el.tag == "p":
                yield ptext(el)
            elif el.tag in ("h2", "h3", "h4") and i < last_p and not link_only(el) and len(ptext(el)) < 80:
                yield ptext(el)  # a cross-head: a paragraph follows
            elif el.tag == "table":  # one line per row (星島's rent tables, 2026-10-20)
                for tr in el.iter("tr"):
                    yield " | ".join(ptext(c) for c in tr.xpath("./th|./td"))
            elif el.tag in ("ul", "ol", "div") and (el.tag != "div" or (len(el) and all(c.tag in ("ul", "ol") for c in el))):
                for li in el.iter("li"):
                    if not link_only(li) and not TEMPLATE.search(li.text_content()):
                        yield ptext(li)


DOUBLE_ENTITY = re.compile(r"&amp;(#\d{2,7};|#x[0-9a-f]{2,6};)", re.I)


def article_text(page):
    """the article body of an HTML page as paragraphs ("\\n"-joined), or ""."""
    import lxml.html
    # numeric entities escaped twice (&amp;#39318;): the SCMP magazines' bodies (ELLE, Cosmopolitan, Harper's BAZAAR
    # HK, 2026-10-20) are written this way, so their paragraphs read as "&#39318;…" and only one plain one was kept
    page = DOUBLE_ENTITY.sub(r"&\1", page or "")
    try:
        doc = lxml.html.fromstring(page)
    except Exception:
        return ""
    page_host = host(((doc.xpath('//link[@rel="canonical"]/@href') or doc.xpath('//meta[@property="og:url"]/@content'))
                      or [""])[0].strip())
    for suffix, xp in FIRST_ONLY.items():
        if page_host.endswith(suffix):
            for later in doc.xpath(xp)[1:]:
                later.drop_tree()
    # an <article> holding the page's one <h1> is the story (Yahoo 2026-10-20: its recommendation cards outside it carry
    # more text than a short story and were taken for it); checked before <header>s are dropped (the h1 sits in one)
    story = [a for a in doc.xpath("//article") if a.xpath(".//h1")] if len(doc.xpath("//h1")) == 1 else []
    body = ld_body(doc)
    if "<" in body:
        body = sources.paragraphs(body)
    elif "&" in body:  # an articleBody with escaped entities (Asia Financial: &quot; &nbsp;)
        body = sources.paragraphs(html.unescape(body))
    desc = ""
    for xp in ('//meta[@property="og:description"]/@content', '//meta[@name="description"]/@content'):
        got = doc.xpath(xp)
        if got and len(got[0].strip()) > len(desc):
            desc = got[0].strip()
    # the headline in its forms (<title> with the site's name, og:title, <h1>), letters and digits only: a block or a
    # description that is only the headline again is not the story (文匯報's video and infographic posts, 2026-10-20)
    heads = [_letters(x) for x in doc.xpath("//title/text()|//meta[@property='og:title']/@content|//h1//text()")]
    heads = [x for x in heads if len(x) >= 6]

    def headline(t):
        t = _letters(t)
        return bool(t) and any(t in x or (x in t and len(t) - len(x) <= 6) for x in heads)
    if len(_letters(desc)) < 5 or SITE_DESC.search(desc) or headline(desc):  # "””" (文匯's e-paper), a site blurb
        desc = ""
    for bad in doc.xpath("//script|//style|//noscript|//iframe|//button|//select|//figure//figcaption"):
        bad.drop_tree()
    total = len(_letters(doc.text_content()))
    # a <form> is a search box or a login, except one that holds most of the page (ASP.NET WebForms wraps the whole
    # page in <form id="form1">: 保安司, 治安警察局, 2026-10-20), and so are <nav>, <footer>, <header>, <aside>
    for bad in doc.xpath("//form|//nav|//footer|//header|//aside"):
        # except one that holds most of the page (上報 wraps its whole page in a <header> it never closes, 2026-10-20)
        if bad.getparent() is not None and len(_letters(bad.text_content())) <= total / 2:
            bad.drop_tree()
    for bad in doc.xpath("//*[@class or @id]"):  # consent dialogs, pop-ups, paywall/login boxes, page footers
        if bad.getparent() is not None and (BOX.search(" ".join((bad.get("class") or "", bad.get("id") or "")))
                                            or FOOT.search(bad.get("class") or "") or FOOT.search(bad.get("id") or "")):
            bad.drop_tree()
    for bad in doc.xpath('//*[not(*)][contains(translate(@style, " ", ""), "display:none")]'):
        bad.drop_tree()  # hidden fields (紫荊: <p style="display: none;"> with the post id and its JSON URL, 2026-10-20)
    for bad in doc.xpath('//*[contains(translate(@style, " ", ""), "display:none")]'):  # short hidden bits (2026-10-20:
        # 信報's hidden 「印刷版出報日」 notice, 晴報's {{hket:ul-video}} template); a long hidden block may be the story
        if bad.getparent() is not None and len(bad.text_content().strip()) < HIDDEN_MAX:
            bad.drop_tree()
    # 明報 (news.mingpao.com /pns/ and /ins/, 2026-10-20): the first paragraph in <div id="upper">, the rest in <div
    # class="articlelogin"> (cross-heads and the byline as <h2>); the two blocks never share a parent
    mp = doc.xpath('//article[contains(concat(" ", normalize-space(@class), " "), " txt4 ")]')
    if mp:
        t = "\n".join(x for x in (ptext(e) for e in mp[0].xpath(
            './/div[@id="upper"]/p | .//div[contains(@class, "articlelogin")]/*[self::p or self::h2 or self::h3]'))
            if x and not junk(x))
        if len(t) > len(body):
            body = t
    marked = doc.xpath('//*[@itemprop="articleBody"]')  # a page that marks its body (e.g. now.com: one <p> per <div>)
    if marked:
        t = "\n".join(x for x in (ptext(p) for p in marked[0].iter("p")) if x and not junk(x))
        if t:  # short too (2026-10-20: NOW's video items, two written lines; it wins only over text not much longer)
            body = body if len(body) > len(t) else t
    # a story in <div class="paragraph"> blocks (on.cc instant news; the Oriental Daily paper: cross-head + content
    # divs inside each), no <p>: every block in order
    # (Drupal's Paragraphs module writes "paragraph paragraph--type--…" too: 城大's contact box, 2026-10-20, not a story block)
    blocks = [d for d in doc.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " paragraph ")]')
              if "photoParagraph" not in (d.get("class") or "") and "paragraph--" not in (d.get("class") or "")]
    if blocks:
        parts = []
        for d in blocks:
            for x in d.xpath('./div[contains(@class, "subtitle") or contains(@class, "content")]') or [d]:
                t = ptext(x)
                if t and not junk(t):
                    parts.append(t)
        t = "\n".join(parts)
        if len(t) > len(body):
            body = t

    # eZ Platform pages (the SCMP magazines ELLE, Cosmopolitan, Harper's BAZAAR HK, Esquire HK, 2026-10-20): the story
    # is a run of <div class="ezrichtext-field"> blocks, one per section or item, never under one parent: every block
    # in order (their related-article and footer blocks are dropped with the boxes and footers above)
    ez = doc.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " ezrichtext-field ")]')
    if len(ez) >= 2:
        t = "\n".join(x for d in ez for x in (ptext(p) for p in d.iter("p")) if x and not junk(x))
        if len(t) > len(body):
            body = t

    # Strikingly blog posts (hkwisdom天機, 澳門特報, 2026-10-20): every paragraph or sub-heading is its own <div
    # class="s-blog-post-section"> block, so no block holds more than one paragraph: every block in order
    sk = doc.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " s-blog-post-section ")]')
    if len(sk) >= 2:
        parts = []
        for d in sk:
            for e in d.xpath('.//p|.//h1|.//h2|.//h3|.//h4|.//li') or [d]:
                t = ptext(e)
                if t and not junk(t):
                    parts.append(t)
        t = "\n".join(parts)
        if len(t) > len(body):
            body = t

    def cluster(scopes):  # the element whose own <p> children carry the most text (the largest paragraph cluster)
        best, best_len = None, 0
        for scope in scopes:
            groups = defaultdict(list)
            for p in scope.iter("p"):
                t = ptext(p)
                if len(t) >= 12 and not junk(t) and not DATELINE.fullmatch(t):
                    groups[p.getparent()].append(t)
            for parent, ps in groups.items():
                n = sum(len(t) for t in ps)
                if n > best_len:
                    best, best_len = parent, n
        return best, best_len
    story = [a for a in story if a.getparent() is not None]
    best, best_len = cluster(story or doc.xpath("//article") or [doc])
    whole, whole_len = cluster([doc])
    if not story and whole_len > 2 * best_len:  # the story sits outside the page's <article> elements (法庭線: related posts only)
        best = whole
    text = ""
    if best is not None:  # every paragraph of the winning cluster, short ones too, with its sub-headings, lists, tables
        text = "\n".join(t for t in cluster_lines(best) if t and not junk(t) and not DATELINE.fullmatch(t))
    if len(text) < 200:  # pages without <p> paragraphs: the text blocks between <br>s inside the biggest block
        cands, sibs = [], defaultdict(list)
        for el in doc.xpath("//div|//td|//section|//span"):
            own = [re.sub(r"\s+", " ", x).strip() for x in el.xpath("text()")]
            own = [x for x in own if len(x) >= 20 and not junk(x) and not headline(x) and not DATELINE.fullmatch(x)]
            if own:
                cands.append("\n".join(own))
                if el.tag == "div":
                    sibs[el.getparent()].append(cands[-1])
        # a story in one <div> per paragraph (上報, 2026-10-20): the parent whose text <div>s carry the most text
        cands += ["\n".join(v) for v in sibs.values() if len(v) > 1]
        alt = max(cands, key=len, default="")
        if len(alt) > len(text):
            text = alt
    if WP_PAGE.search(page[:300000]):
        # a WordPress story: its one entry-content block when that holds the paragraph cluster and more (2026-10-21:
        # stories split into several blocks between pictures or headings, where the cluster was one block of them:
        # technow, menstyle.be, flyformiles, u4get, CNET; a short story with no cluster at all: 香港網絡)
        wp = wp_body(doc, lambda l: junk(l) or headline(l) or bool(DATELINE.fullmatch(l)) or bool(LEAKED_ATTR.search(l)))
        if len(text) < 200 and len(wp) > len(text) or len(wp) > 1.2 * len(text) and contained(text, wp):
            text = wp
    if len(body) > len(text) * 0.8:
        text = "\n".join(l.strip() for l in body.splitlines() if l.strip())
    # some pages carry the story only in their description; one that is the headline, the dateline and the story's
    # first words again (香港網絡's copies of RTHK, cut off at 「提及」, 2026-10-21) is not more than the story
    rest = _letters(desc)
    for x in sorted(heads, key=len, reverse=True):
        rest = rest.replace(x, "")
    rest = re.sub(r"\d{4}\d{1,2}\d{1,2}(hkt)?\d{3,4}", "", rest)
    if len(text) < 200 and len(desc) > len(text) and not junk(desc) and len(rest) > len(_letters(text)):
        text = desc
    lines, seen = [], set()
    for l in text.split("\n"):
        if TEMPLATE.search(l) and len(TEMPLATE.sub("", l).strip()) < 10:  # an unrendered template line, not text
            continue
        if l.strip() and l not in seen:
            seen.add(l)
            lines.append(l)
    return "\n".join(lines)[:TEXT_MAX]


# WordPress (2026-10-21, 香港網絡 internet.com.hk): the story is the theme's one <div class="entry-content"> block; a
# short story there (a copy of RTHK's three lines, its markup a <p> holding an <h2>, which the parser splits) has no
# paragraph cluster of 200 characters, so the page's truncated description was kept instead
LEAKED_ATTR = re.compile(r"[″”\"]\s*(title|alt|src|class)=[”″\"]")  # markup as text: 香港網絡's 「10:04″ title=”…”>」 lines
WP_PAGE = re.compile(r'<meta name="generator" content="WordPress|/wp-content/(themes|plugins)/')
WP_BODY = '//div[contains(concat(" ", normalize-space(@class), " "), " entry-content ")]'
# inside it: sharing buttons, related-post lists and buttons the plugins put there
WP_DROP = ('.//*[contains(@class, "sharedaddy") or contains(@class, "jp-relatedposts") or contains(@class, "yarpp") or '
           'contains(@class, "addtoany") or contains(@class, "post-share") or contains(@class, "related") or '
           'contains(@class, "wp-block-buttons") or contains(@class, "social")]')


# WordPress's MemberPress paywall (2026-10-21: InsuranceAsia News): a logged-out reader gets <div
# class="mepr-unauthorized-excerpt"> (the story's first lines) and a subscription pitch; the paragraph cluster was a
# sidebar's promoted story
MEPR_EXCERPT = '//div[contains(concat(" ", normalize-space(@class), " "), " mepr-unauthorized-excerpt ")]'


def memberpress_excerpt(page):
    """the public excerpt of a MemberPress-walled page, or None when the page has no such wall"""
    if "mepr-unauthorized-excerpt" not in page:
        return None
    import lxml.html
    try:
        found = lxml.html.fromstring(page).xpath(MEPR_EXCERPT)
    except Exception:
        return None
    return block_text(found[0]).strip()[:TEXT_MAX] if found else None


def contained(a, b, share=0.9):
    """are (almost) all of a's 4-character runs of letters in b? (a block's text inside a larger one)"""
    a, b = _letters(a), _letters(b)
    ga = {a[i:i + 4] for i in range(max(0, len(a) - 3))}
    return bool(ga) and len(ga & {b[i:i + 4] for i in range(max(0, len(b) - 3))}) >= share * len(ga)


def wp_body(doc, drop):
    """the text of a WordPress page's one entry-content block, its lines that `drop` rejects left out ("" when the
    page has none or several: an archive page)"""
    found = doc.xpath(WP_BODY)
    if len(found) != 1:
        return ""
    for e in found[0].xpath(WP_DROP + " | .//nav | .//aside | .//form | .//footer | .//script | .//style | .//figure"):
        if e.getparent() is not None:
            e.drop_tree()
    for e in found[0].xpath(".//ul | .//ol | .//section | .//div | .//p"):  # link lists: tags, related posts, archives
        if e.getparent() is not None and link_only(e):
            e.drop_tree()
    lines, seen = [], set()
    for l in block_text(found[0], drop=drop).split("\n"):
        if l not in seen:
            seen.add(l)
            lines.append(l)
    return "\n".join(lines)[:TEXT_MAX]


# sites whose story is one marked block of text and tables, no <p> paragraphs (2026-10-20: 經濟通's
# <div class="article-detail">: lines between <br>s, and tables such as 《異動股》 and 《外圍重點》 that are the story):
# that block's text with its tables, one row a line
# 澳門城市大學 cityu.edu.mo: a WordPress story in many sections (cross-heads, names, Q&A as their own blocks), so the
# largest <p> cluster was one section of it; 治安警察局 fsm.gov.mo: the story is one <span id="lbl_msg"> of lines between
# <br>s, its cross-heads short lines; 保安司 gss.gov.mo: <span id="ContentPlaceHolder1_lbcontent">, its first paragraphs
# bare text between <br>s; 聖保祿學校 esp.edu.mo (SharePoint): the page's rich-text field, its section heads and items
# short <span>s (2026-10-20)
BODY_XPATHS = {"etnet.com.hk": '//div[contains(concat(" ", normalize-space(@class), " "), " article-detail ")]',
               "cityu.edu.mo": '//article//div[contains(concat(" ", normalize-space(@class), " "), " entry-content ")]',
               "fsm.gov.mo": '//span[@id="lbl_msg"]',
               "gss.gov.mo": '//span[@id="ContentPlaceHolder1_lbcontent"]',
               "esp.edu.mo": '//div[contains(concat(" ", normalize-space(@class), " "), " ms-rtestate-field ")]',
               # 城大 (Drupal, 2026-10-20): the story is several field--name-body blocks between pictures and quotes, under
               # the page's full node; every block in order (BODY_ALL)
               "cityu.edu.hk": '//article[contains(@class, "node--view-mode-full")]//div[contains(@class, "field--name-body")]',
               # 安聯投資 (Sitecore, 2026-10-20): the story is several <div class="u-padding-top-s c-copy"> rich-text
               # blocks between its charts, inside "agi-rte" sections (the largest cluster was one of them); every one
               "allianzgi.com": '//div[contains(concat(" ", normalize-space(@class), " "), " agi-rte ")]'
                                '//div[contains(concat(" ", normalize-space(@class), " "), " c-copy ")]'
                                '[contains(concat(" ", normalize-space(@class), " "), " u-padding-top-s ")]',
               # 公民教育委員會 (2026-10-20): the page's main_contents_area; its details are a table (劇目名稱 / 故事大綱 /
               # 演出日期) the paragraph cluster left out
               "cpce.gov.hk": '//div[contains(concat(" ", normalize-space(@class), " "), " main_contents_area ")]',
               # Media OutReach Newswire (2026-10-21): the release is text between <br>s in the "innerband-content" block
               # (no <p>), so the largest paragraph cluster was a "more news" card's excerpt of the English release
               "media-outreach.com": '//div[contains(concat(" ", normalize-space(@class), " "), '
                                     '" mo-ui-news-article-layout-innerband-content ")]'}
# of these, the marked block is the whole story when the page has it, empty too (聖保祿學校's photo-only pages: an empty
# rich-text field, the page's other text its footer address)
BODY_ONLY = ("fsm.gov.mo", "gss.gov.mo", "esp.edu.mo")
BODY_ALL = ("cityu.edu.hk", "allianzgi.com")  # sites whose marked blocks are all the story (else the first one)
BLOCK_TAGS = {"p", "div", "br", "tr", "li", "table", "caption", "h1", "h2", "h3", "h4", "ul", "ol", "section", "blockquote"}


def block_text(el, drop=None):
    """an element's text as lines: a line ends at every block element and <br>; a table row is one line, its cells
    separated by " | "; drop: the test a line is left out by (default: a JUNK word in its first 80 characters)"""
    lines, cur = [], []

    def end():
        t = re.sub(r"[ \t　\xa0\r\n]+", " ", "".join(cur)).strip()
        if t:
            lines.append(t)
        cur.clear()

    def walk(e, cell=False):  # inside a cell, a <br> or block is a space: the row stays one line
        if not isinstance(e.tag, str) or e.tag in ("script", "style", "noscript", "figcaption"):  # a picture's caption
            return
        block = e.tag in BLOCK_TAGS and not cell
        if block:
            end()
        elif cell and e.tag in BLOCK_TAGS:
            cur.append(" ")
        if e.tag in ("td", "th"):
            if "".join(cur).strip():
                cur.append(" | ")
            cell = True
        cur.append(e.text or "")
        for c in e:
            walk(c, cell)
            cur.append(c.tail or "")
        if block:
            end()
    walk(el)
    end()
    return "\n".join(l for l in lines if not (drop(l) if drop else JUNK.search(l[:80])))


# a forum thread's page read as a page (fulltext_fetch.forum_page, 2026-10-20: forums with no thread collector): its
# opening post, the first Discuz! postmessage_<pid> (uwants, xocat, hkitalk, 香港公營房屋討論區) or MyBB post_body
# (香港論壇), Discuz! 4's td.line holding <a name="pid…"> (賽馬世界); the generic extractor took the forum's 「重要聲明」 disclaimer, its footer or "Users browsing this thread"
FORUM_POST = ('(//*[starts-with(@id, "postmessage_")] | '
              '//div[contains(concat(" ", normalize-space(@class), " "), " post_body ")][starts-with(@id, "pid_")] | '
              '//td[contains(concat(" ", normalize-space(@class), " "), " line ")][a[starts-with(@name, "pid")]])[1]')
# inside it: attachment menus (file name, size, time; hidden), ad slots, the edit note, the subject repeated
FORUM_DROP = ('.//*[contains(translate(@style, " ", ""), "display:none")] | .//ins | .//script | .//style | '
              './/*[@id="threadtitle"] | ./span[@class="bold"] | .//*[contains(concat(" ", normalize-space(@class), " "), " t_attach ") or '
              'contains(concat(" ", normalize-space(@class), " "), " a_pr ") or '
              'contains(concat(" ", normalize-space(@class), " "), " pstatus ") or '
              'contains(concat(" ", normalize-space(@class), " "), " tip ")]')


EDIT_NOTE = re.compile(r"\[?\s*本帖最[後后]由 .+ (編輯|编辑)\s*\]?")  # Discuz's 「[ 本帖最後由 X 於 … 編輯 ]」


def forum_post(page):
    """the opening post's text of a Discuz! or MyBB thread page ("" when it has none: a picture or video only), or None
    when the page is not one"""
    if 'id="postmessage_' not in page and 'id="pid_' not in page and 'name="pid' not in page:
        return None
    import lxml.html
    try:
        found = lxml.html.fromstring(page).xpath(FORUM_POST)
    except Exception:
        return None
    if not found:
        return None
    for e in found[0].xpath(FORUM_DROP):
        if e.getparent() is not None:
            e.drop_tree()
    found[0].tag = "div"  # Discuz X's post is a <td>: its <br> lines stay lines, not a table row's cells
    return block_text(found[0], drop=lambda l: junk(l) or EDIT_NOTE.fullmatch(l))[:TEXT_MAX]


def flight_ld_text(page):
    """a Next.js page's JSON-LD Article inside its flight data (U Lifestyle's community posts, 2026-10-20: the post is
    the Article's "text"; the HTML has only its description) → its articleBody or text, else ''"""
    best = ""
    s = flight_data(page)  # as sent: next_flight's unescaping breaks a JSON string that quotes HTML (<a href=\\"…\\">)
    for m in re.finditer(r'\[?\{"@context":"https?://schema\.org"', s):
        try:
            d = json.JSONDecoder().raw_decode(s[m.start():])[0]
        except ValueError:
            continue
        stack = [d]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if re.search(r"Article|Posting", str(o.get("@type") or "")):
                    for k in ("articleBody", "text"):
                        if isinstance(o.get(k), str) and len(o[k]) > len(best):
                            best = o[k]
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
    if re.search(r"</?[a-z][^>]*>", best):  # its hashtags as <a> links: their words kept
        best = html.unescape(re.sub(r"<[^>]+>", " ", best))
    best = "\n".join(re.sub(r"[ \t]+", " ", l).strip() for l in best.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", best).strip()[:TEXT_MAX]


def marked_body(url, page):
    """the story of a page on a BODY_XPATHS site (block_text of its marked block, "" when it is empty), else "" (not
    such a site, the block not on the page: None for a BODY_ONLY site)"""
    xp = next((x for h, x in BODY_XPATHS.items() if host(url) == h or host(url).endswith("." + h)), None)
    if not xp:
        return ""
    import lxml.html
    try:
        found = lxml.html.fromstring(page).xpath(xp)
    except Exception:
        found = []
    if not found:
        return None if host(url).endswith(BODY_ONLY) else ""
    if host(url).endswith(BODY_ALL):
        return "\n".join(t for t in (block_text(f) for f in found) if t)[:TEXT_MAX]
    return block_text(found[0])[:TEXT_MAX]


def _letters(t):
    return re.sub(r"[\W_]+", "", t or "").lower()


def discuss_text(page):
    """a discuss.com.hk archiver page: the first post's text (BBCode stripped) and its author"""
    import lxml.html
    doc = lxml.html.fromstring(page)
    bodies = doc.xpath('//div[@class="archiver_postbody"]')
    if not bodies:
        return article_text(page), None
    text = sources.paragraphs(lxml.html.tostring(bodies[0], encoding="unicode"))
    text = re.sub(r"\[img\].*?\[/img\]|\[/?(url|quote|i|b|u|size|color|font|align)[^\]]*\]", "", text, flags=re.S)
    cite = doc.xpath('//div[@class="archiver_post"][1]//cite/text()')
    return text.strip()[:TEXT_MAX], (cite[0].strip() if cite else None)


# ---- fetching ----

_rate_lock = threading.Lock()
_last = [0.0]


def pace():
    with _rate_lock:
        wait = _last[0] + 1 / RATE - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()


_slow_next = {}


def slow(url):
    """wait for a SLOW_SITES site's next turn (every thread and reader shares it)"""
    gap = SLOW_SITES.get(site(url))
    while gap:
        with _rate_lock:
            at, now = _slow_next.get(site(url), 0.0), time.monotonic()
            if at <= now:
                _slow_next[site(url)] = now + gap
                return
        time.sleep(min(at - now, gap))


_seen = threading.local()


def last_page():
    """the HTML of this thread's last get() (the full-text readers hand it to media.py, which finds the page's video
    and audio without a second request); None after a failed get"""
    return getattr(_seen, "page", None)


def last_route():
    """the route of this thread's last get(): direct | direct (Chrome TLS) | proxy | ISP IP (the live dashboard's Full
    text panel, events.py); None before any"""
    return getattr(_seen, "route", None)


def _isp_lock():
    import sites_fetch
    return sites_fetch.isp_lock()


def get(url, proxy=None, headers=None, isp=False):
    """urllib, then curl_cffi's Chrome fingerprint when the site refuses it. A URL whose path is a media file (a radio
    programme's .mp3, a picture, a clip) is never requested, and a response that is one is never read (sources.fetch):
    HTTP 415 (nomedia.py, text only). isp=True: proxy is the ISP IP, held under its lock, its tunnel refusal tried again"""
    _seen.page = None
    _seen.route = "ISP IP" if isp else "proxy" if proxy else "direct"
    if nomedia.media_url(url):
        raise nomedia.refuse(url)
    url = urllib.parse.quote(url, safe=":/?&=%#+,;@!$'()*~[]")  # non-ASCII paths (香港01)
    tls = {"verify": False} if NOVERIFY_HOSTS and host(url).removeprefix("www.").endswith(NOVERIFY_HOSTS) else {}
    slow(url)
    pace()
    if isp:
        for i in range(ISP_TRIES):
            try:
                with _isp_lock():
                    _seen.page = sources.fetch(url, proxy=proxy, isp=True, headers=headers, timeout=TIMEOUT)
                return _seen.page
            except urllib.error.URLError as e:
                if "Tunnel connection failed: 403" not in str(e) or i == ISP_TRIES - 1:
                    raise
                time.sleep(3)
    try:
        _seen.page = sources.fetch(url, proxy=proxy, headers=headers, timeout=TIMEOUT, **tls)
        return _seen.page
    except walls.Unsolved as e:  # a proof-of-work wall still standing: tried again later, never an empty article
        raise Challenge(url) from e
    except urllib.error.HTTPError as e:
        # 30x: urllib gave up on a redirect loop (安聯投資 2026-10-20: 302 to the same URL with Set-Cookie until the
        # cookies come back; urllib keeps none): curl_cffi keeps the cookies through the redirects
        if e.code not in (301, 302, 303, 307, 308, 403, 429, 503) or proxy:
            raise
    from curl_cffi import requests  # some sites refuse urllib's TLS fingerprint but take a browser's (no proxy)
    pace()
    _seen.route = "direct (Chrome TLS)"
    r = requests.get(url, impersonate="chrome", timeout=TIMEOUT, **tls,
                     headers={"Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8", **(headers or {})})
    if r.status_code == 403 and (BANNED.search((r.text or "")[:500]) or site(url) in SLOW_SITES):
        raise urllib.error.HTTPError(url, 429, "HTTP 429 (IP banned for a while)", {}, None)
    if r.status_code >= 400:
        if challenged(r.text or ""):  # Cloudflare's managed challenge (明報's article pages): the browser pass reads it
            raise Challenge(url)
        # an HTTPError like urllib's: a 429 / 5xx is transient for the readers (tried again later), a 403 / 404 is not
        raise urllib.error.HTTPError(url, r.status_code, f"HTTP {r.status_code}", {}, None)
    _seen.page = r.text
    return r.text


def blocked(page):
    """Cloudflare's block page (error 1020 "Sorry, you have been blocked"): a wall, never an article"""
    return bool(re.search(r"cf-error-details|Sorry, you have been blocked|<title>Attention Required! \| Cloudflare", page[:20000]))


def challenged(page):
    """Cloudflare's managed challenge, or Imperva Incapsula's script stub (香港城市大學 2026-10-20: a 212-byte page that
    loads /_Incapsula_Resource on every route tried, the cluster's IP, the ISP IP, yycstream; a real Chrome passes it)"""
    if "wsidchk" in page[:20000] and "正在验证您的请求" in page[:20000]:
        # the hosting wall 「正在验证您的请求，请稍候…」 (現代澳門日報 todaymacao.com to the cluster's IP at times,
        # 2026-10-20: a script checks navigator.webdriver, the user agent, plugins and window size, then submits a
        # form with wsidchk; a real Chrome passes it)
        return True
    return bool(re.search(r"<title>(Just a moment|請稍候|Attention Required)", page[:3000], re.I)) or "cf-chl" in page[:20000] \
        or incapsula(page) or aws_waf(page)


def aws_waf(page):
    """AWS WAF's challenge page (Prestige HK 2026-10-20: CloudFront's 202 with window.gokuProps and
    token.awswaf.com's script, about 2 kB, to every plain client; a real browser passes it)"""
    return len(page or "") < 10000 and bool(re.search(r"awsWafCookieDomainList|gokuProps|token\.awswaf\.com", page or ""))


INCAPSULA_WAITS = 10  # more 3-second looks while Imperva's interstitial is still up (a fresh profile took over 24 s once)


def lang_cookie(url):
    """the language cookie a Sitecore site keys its unprefixed pages on (城大, 2026-10-20: www.cityu.edu.hk/media/… is
    English or Chinese by the cookie home#lang the last page set, whatever the URL) → a cookie for url, or None"""
    u = urllib.parse.urlsplit(url)
    if not host(url).endswith("cityu.edu.hk"):
        return None
    m = re.match(r"/(zh-hk|zh-cn)(/|$)", u.path, re.I)
    lang = {"zh-hk": "zh-HK", "zh-cn": "zh-CN"}[m.group(1).lower()] if m else "en"
    return {"name": "home#lang", "value": lang, "domain": u.hostname, "path": "/"}


def incapsula(page):
    """Imperva's interstitial (2026-10-20: 城大 answers it to the cluster's IP and the ISP IP alike, about 1,150 bytes):
    a script from /_Incapsula_Resource that sets a cookie and reloads; a real browser passes it"""
    return len(page or "") < 5000 and "_Incapsula_Resource" in (page or "")


PUB_META = ('//meta[@property="article:published_time"]/@content', '//meta[@itemprop="datePublished"]/@content',
            '//*[@itemprop="datePublished"]/@datetime', '//meta[@name="pubdate"]/@content',
            '//meta[@name="publishdate"]/@content', '//meta[@name="publish-date"]/@content',
            '//meta[@property="og:published_time"]/@content', '//meta[@name="parsely-pub-date"]/@content',
            '//time[contains(@class, "publish")]/@datetime', '//meta[@name="date"]/@content')
MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), 1)}


def _when(v):
    """a page's date string → ISO HKT to the minute (corpus.posts.published), or None; a time more than
    corpus.FUTURE ahead or before 2000 is not a publish time"""
    d = sources.parse_time((v or "").strip())
    if not d:
        return None
    if isinstance(d, corpus.DateOnly):  # the page states only the day: the bare day, never 00:00
        return d.strftime("%Y-%m-%d") if 2000 <= d.year and d <= datetime.now(corpus.HKT) + corpus.FUTURE else None
    d = d.astimezone(corpus.HKT)
    if d.year < 2000 or d > datetime.now(corpus.HKT) + corpus.FUTURE:
        return None
    return d.isoformat(timespec="minutes")


# article pages that state only the day (tests/date_samples/pages: 東方日報's print edition, the 大公/文匯 e-papers,
# 金管局, 經濟導報, 熱血時報, 號外, 信報's print edition): a date-only post there is not read again just for a time
DAY_ONLY_PAGES = re.compile(r"orientaldaily\.on\.cc/|tkww\.hk/epaper/view/|hkma\.gov\.hk/|jdonline\.com\.hk/|"
                            r"passiontimes\.hk/|citymagazine\.hk/|hkej\.com/dailynews/")

DATELINES = [  # pages whose time is only in their visible dateline (verbatim samples: tests/date_samples/pages)
    # 香港商報: 責任編輯：<font>…</font></span> <span>2026-10-14 21:15:01<font…>原創</font></span>
    (re.compile(r"責任編輯：.{0,300}?<span>\s*(\d{4})-(\d{2})-(\d{2}) (\d{1,2}):(\d{2})", re.S), "ymdHM"),
    # 政府新聞處 press releases: 2026年3月22日（星期日）<br /> 香港時間16時40分
    (re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日（星期.）\s*<br\s*/?>\s*香港時間(\d{1,2})時(\d{2})分"), "ymdHM"),
    # … and in English: Ends/Tuesday, April 21, 2026 <br/> Issued at HKT 19:05
    (re.compile(r"Ends/\w+,\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\s*<br\s*/?>\s*Issued at HKT (\d{1,2}):(\d{2})"), "BdyHM"),
]


def page_date(page):
    """the publish time an article page states (Deyao, 2026-10-14: a post the listing gave no date takes its page's):
    JSON-LD datePublished, the article:published_time / itemprop / pubdate / date metas, a <time class=published>,
    China Daily's "Published: 16:55, October 14, 2026", the datelines in DATELINES → ISO HKT to the minute; a page
    that states only the day (a date, or a time of exactly midnight) → the bare day "YYYY-MM-DD"; or None"""
    import lxml.html
    try:
        doc = lxml.html.fromstring(page)
    except Exception:
        return None
    found = []  # the candidates in order; the first with a time wins, else the first bare day

    def cand(v):
        if w := _when(v):
            found.append(w)
            return not corpus.BARE_DATE.fullmatch(w)
        return False
    for s in doc.xpath('//script[contains(@type, "ld+json")]/text()'):
        s = re.sub(r"^\s*/\*\s*<!\[CDATA\[\s*\*/|/\*\s*\]\]>\s*\*/\s*$", "", s.strip())  # 文匯/大公's /*<![CDATA[*/ … /*]]>*/
        try:
            stack = [json.loads(s.strip())]
        except ValueError:  # not JSON (文匯/大公 put // comments in theirs): its datePublished values as written
            if any(cand(v) for v in re.findall(r'"datePublished"\s*:\s*"([^"]+)"', s)):
                return found[-1]
            continue
        while stack:
            o = stack.pop(0)
            if isinstance(o, dict):
                if isinstance(o.get("datePublished"), str) and cand(o["datePublished"]):
                    return found[-1]
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
    for xp in PUB_META:
        for v in doc.xpath(xp):
            if cand(v):
                return found[-1]
    m = re.search(r"Published:\s*(\d{1,2}):(\d{2}),\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", page)
    if m and m.group(3).lower() in MONTHS and cand(
            f"{m.group(5)}-{MONTHS[m.group(3).lower()]:02d}-{int(m.group(4)):02d}T{int(m.group(1)):02d}:{m.group(2)}:00+08:00"):
        return found[-1]
    for rx_, order in DATELINES:
        if m := rx_.search(page):
            g = dict(zip(order, m.groups()))
            mon = MONTHS.get(g["B"].lower()) if "B" in g else int(g["m"])
            if mon and cand(f"{g['y']}-{mon:02d}-{int(g['d']):02d}T{int(g['H']):02d}:{g['M']}:00+08:00"):
                return found[-1]
    return found[0] if found else None


# HKET sites read through their content API: the JavaScript-only ones (invest, ps), and the paper's (paper,
# specialist: their pages give only the public part, the API's "paid" flag says whether that is all; 2026-10-20)
HKET_API = {"invest.hket.com": "invest", "ps.hket.com": "epc", "paper.hket.com": "invest", "specialist.hket.com": "invest"}


def hket_api(url):
    """a JavaScript-only HKET article (its page is an empty shell) → (its content API URL, headers), else None"""
    h = host(url)
    if h in HKET_API and (m := re.search(r"/article/(\d+)", url)):
        return (f"https://data02.hket.com/content?id={m.group(1)}&channel={HKET_API[h]}",
                {"Origin": "https://" + h, "Referer": "https://" + h + "/"})
    return None


# Sino Group (www.sino.com, 2026-10-20): a React page; its text is in Kentico's delivery API behind web-cdn.sino.com,
# found by the page's slug (the URL's last segment) and language (/en/ /tc/ /sc/)
SINO_API = "https://web-cdn.sino.com/20a53f0a-15c8-0029-b8df-e495023b403f/items?elements.slug={slug}&language={lang}&depth=0&limit=1"


def sino_api(url):
    m = re.match(r"https?://(?:www\.)?sino\.com/(en|tc|sc)/(?:[^?#]*/)?([a-z0-9][\w,.-]*)/?(?:[?#]|$)", url or "")
    if not m or m.group(0).rstrip("/").count("/") < 5:  # /en/ or /en/x only: a front or section page
        return None
    return SINO_API.format(slug=urllib.parse.quote(m.group(2), safe=",.-"), lang=m.group(1))


def sino_text(js):
    """the item's text: every rich-text (HTML) element in order (a press release's body, a property's disclaimer)"""
    items = (json.loads(js).get("items") or [])
    if not items:
        return ""
    parts = []
    for k, e in (items[0].get("elements") or {}).items():
        v = e.get("value")
        if e.get("type") in ("rich_text", "custom") and isinstance(v, str) and "<" in v and not k.startswith("seo_"):
            t = sources.paragraphs(v)
            if t.strip():
                parts.append(t)
    return "\n".join(parts)[:TEXT_MAX]


# Vintage Grail (vintagegrail.com, 2026-10-20): a Nuxt page over its own API; an article's sections are details[] (in
# order), a watch's or accessory's text its detail.description (null for most watches: the page shows specs only)
VINTAGEGRAIL_API = {"explore": "article", "media-room": "article", "timepieces": "watch", "accessories": "accessories"}


def vintagegrail_api(url):
    m = re.match(r"https?://(?:www\.)?vintagegrail\.com/([a-z-]+)/(?:[^/?#]+/)?([^/?#]+)/?$", url or "")
    if not m or m.group(1) not in VINTAGEGRAIL_API:
        return None
    return f"https://api.vintagegrail.com/api/v1/web/{VINTAGEGRAIL_API[m.group(1)]}/item/{m.group(2)}"


def vintagegrail_text(js):
    it = ((json.loads(js).get("record") or {}).get("item") or {})
    if it.get("details"):
        parts = [sources.paragraphs(d.get("description") or "") for d in sorted(it["details"], key=lambda d: int(d.get("sequence") or 0))]
    else:
        parts = [sources.paragraphs((it.get("detail") or {}).get("description") or it.get("description") or "")]
    return "\n".join(html.unescape(p) for p in parts if p.strip())[:TEXT_MAX]


# 華富財經 Quamnet (2026-10-21): a post page is a React shell (<div id="single-post-root" data-post-id=…>, the only
# text its sidebar and the copyright footer, which the extractor took for the story); the post is its own JSON API's
# item.body, Markdown whose paragraphs are sentences ending 「。」 followed by a space
QUAMNET_POST = re.compile(r"https?://(?:www\.)?quamnet\.com/(?:[a-z_]+/)?post/([A-Za-z0-9_-]+)")


def quamnet_text(js, meta):
    it = (json.loads(js).get("item") or {})
    body = it.get("body") or ""
    if it.get("fmt") != "md" and "<" in body:
        body = sources.paragraphs(body)
    body = re.sub(r"(?<=[。！？」])[ \t]+(?=\S)", "\n", body)
    body = re.sub(r"^\s{0,3}(#{1,6}|[*-]|>)\s+", "", body, flags=re.M)  # Markdown headings, bullets, quotes
    body = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), body)
    if (w := _when((it.get("published_at") or "").replace(" ", "T") + ":00+08:00" if it.get("published_at") else "")):
        meta["published"] = w
    if it.get("is_free") is False:  # a paid post: the API gives the logged-out reader's part only
        meta["via"] = "excerpt"
    return "\n".join(l.strip() for l in body.splitlines() if l.strip())[:TEXT_MAX]


RESI_TRIES = 3  # reads of a page through yycstream when an exit gets a 403: each request leaves from another IP


TRADINGVIEW_NEWS = re.compile(r"tradingview\.com/news/([^/?#]+?:\d+)(?:-[^/?#]*)?/?(?:[?#].*)?$")
MOOMOO_POST = re.compile(r"moomoo\.com/(?:[a-z-]+/)?news/post/(\d+)")  # (its videos and flashes have no post body)
LINE_ARTICLE = re.compile(r"today\.line\.me/(\w\w)/v3/article/(\w+)")
PRESSREADER_ARTICLE = re.compile(r"pressreader\.com/[\w-]+/[\w-]+/\d{8}/(\d+)")
SMG_NEWS = re.compile(r"smg\.gov\.mo/(zh|en|pt)/news-detail/(\d+)")


def ast_text(node):
    """TradingView's story body (astDescription: a tree of {type, children}, strings at the leaves) → paragraphs"""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    inner = "".join(ast_text(c) for c in node.get("children") or [])
    return inner + "\n" if node.get("type") in ("p", "h1", "h2", "h3", "li", "blockquote") else inner


def api_story(url, h, meta):
    """the sites whose article page holds no story text, read from the API their pages use (2026-10-23, the source-gap
    hosts) → (text, author, engagement), or None for any other URL:
      TradingView news (every language's host): news-headlines.tradingview.com/v3/story, the body as a tree
        (astDescription); a Reuters story's id is its tag: URI;
      Moomoo news: news-site-api/detail/get-post-info (the page is an obfuscated script), Traditional Chinese;
      LINE TODAY: webapi/portal/page/setting/article, its content and the outlet's original (sourceUrl: the
        aggregator's original, follow.pointer);
      PressReader: the article API with the anonymous token (sources_extra.pressreader_token);
      地球物理氣象局 (an SPA): cms.smg.gov.mo/<lang>/api/news/<id>"""
    if m := TRADINGVIEW_NEWS.search(url):
        sid = urllib.parse.unquote(m.group(1))
        sid = "tag:" + sid if "," in sid and not sid.startswith("tag:") else sid
        lang = "zh-Hant" if h.startswith(("tw.", "cn.", "hk.")) else "en"
        d = json.loads(get("https://news-headlines.tradingview.com/v3/story?" + urllib.parse.urlencode({"id": sid, "lang": lang})))
        if w := _when(str(d.get("published") or "")):
            meta["published"] = w
        text = re.sub(r"\n{2,}", "\n", ast_text(d.get("astDescription") or "")).strip() or d.get("shortDescription") or ""
        return text[:TEXT_MAX], None, {}
    if m := MOOMOO_POST.search(url):
        d = (json.loads(get(f"https://www.moomoo.com/news/news-site-api/detail/get-post-info?id={m.group(1)}",
                            headers={"x-news-site-lang": "1"})).get("data") or {}).get("data") or {}
        if w := _when(str(d.get("timestamp") or "")):
            meta["published"] = w
        return sources.paragraphs(d.get("contentTrans") or d.get("content") or "")[:TEXT_MAX], d.get("source") or None, {}
    if m := LINE_ARTICLE.search(url):
        d = (json.loads(get(f"https://today.line.me/webapi/portal/page/setting/article?hash={m.group(2)}&country={m.group(1)}"))
             .get("data") or {})
        if w := _when(str(d.get("publishTimeUnix") or "")):
            meta["published"] = w
        if aggregator(url) and (d.get("sourceUrl") or "").startswith("http"):
            meta["original"] = d["sourceUrl"]
        return sources.paragraphs(d.get("content") or "")[:TEXT_MAX], d.get("publisher") or None, {}
    if m := PRESSREADER_ARTICLE.search(url):
        import sources_extra
        d = sources_extra.pressreader_get(f"articles/GetItems?articles={m.group(1)}&comment=LatestByAll&viewType=text")
        a = (d.get("Articles") or [{}])[0]
        paras = [b if isinstance(b, str) else (b.get("Text") or "") if isinstance(b, dict) else "" for b in a.get("Blocks") or []]
        return "\n".join(sources.paragraphs(p) for p in paras if p.strip())[:TEXT_MAX], a.get("Byline") or None, {}
    if m := SMG_NEWS.search(url):
        lang = {"zh": "zh_TW", "en": "en", "pt": "pt"}[m.group(1)]
        d = (json.loads(get(f"https://cms.smg.gov.mo/{lang}/api/news/{m.group(2)}")) or [{}])[0]
        if w := _when((d.get("startdate") or "").replace(" ", "T") + "+08:00" if d.get("startdate") else ""):
            meta["published"] = w
        return sources.paragraphs(((d.get("translations") or {}).get(lang) or {}).get("content") or "")[:TEXT_MAX], None, {}
    return None


def proxy_country(h):
    """the yycstream exit country for a PROXY_HOSTS host: its catalogue site's own (PROXY_COUNTRY), else Hong Kong"""
    h = h.removeprefix("www.")
    return next((c for k, c in PROXY_COUNTRY.items() if h == k or h.endswith("." + k)), "hk")


def resi_get(url, proxy, headers):
    """get(), and through the residential proxy (a new exit per request) again on a 403: Cloudflare refuses some exits
    and takes others (uwants, 2026-10-21: 16 of 40 thread reads in one run got "Attention Required!")"""
    for i in range(RESI_TRIES if proxy else 1):
        try:
            return get(url, proxy=proxy, headers=headers)
        except urllib.error.HTTPError as e:
            if not proxy or e.code != 403 or i == RESI_TRIES - 1:
                raise


def original_link(url, page):
    """an aggregator page's link to the story it copies: its og:url or canonical when they name another site, else a
    link whose text says 原文 (「星島原文連結」) to another site → URL or None"""
    import lxml.html
    try:
        doc = lxml.html.fromstring(page)
    except Exception:
        return None
    here = site(url)
    for v in doc.xpath('//meta[@property="og:url"]/@content | //link[@rel="canonical"]/@href'):
        v = urllib.parse.urljoin(url, v.strip())
        if v.startswith("http") and site(v) != here:
            return ORIGINAL_FIX.sub(r"\1/home/", v)
    for a in doc.xpath("//a[@href]"):
        v = urllib.parse.urljoin(url, a.get("href").strip())
        if v.startswith("http") and site(v) != here and ORIGINAL_LINK.search(a.text_content() or ""):
            return ORIGINAL_FIX.sub(r"\1/home/", v)
    return None


ORIGINAL_FIX = re.compile(r"^(https?://news\.now\.com)/mobile/")  # headline4hk's NOW links: the /mobile/ player answers 404


def aggregator(url):
    h = host(url).removeprefix("www.")
    return any(h == a or h.endswith("." + a) for a in AGGREGATOR_HOSTS)


def fetch_one(url, meta=None):
    """→ (text, author, engagement) ; raises when the page can't be read; text "" = read but nothing found. meta (a
    dict) gets "published": the publish time the page states (page_date), when it states one"""
    h = host(url)
    meta = {} if meta is None else meta
    if api := hket_api(url):  # the page is an empty JavaScript shell
        d = json.loads(get(api[0], headers=api[1]))
        t = re.fullmatch(r"(\d{1,2}):(\d{2}) (\d{4})/(\d{2})/(\d{2})", (d.get("displayDate") or "").strip())
        if t and (w := _when(f"{t[3]}-{t[4]}-{t[5]}T{int(t[1]):02d}:{t[2]}:00+08:00")):
            meta["published"] = w
        c = d.get("content") or {}
        if not d.get("paid") and (c.get("full") or c.get("paywall")):
            # a free article (paid false; all of 地產站 ps.hket.com, 2026-10-20): the logged-out page shows it whole,
            # "paywall" being only the part after the paywall's place, so the whole text
            return sources.paragraphs(c.get("full") or (c.get("nonPaywall") or "") + c["paywall"])[:TEXT_MAX], None, {}
        lead, body = sources.paragraphs(c.get("lead") or ""), sources.paragraphs(c.get("nonPaywall") or "")
        if lead and body.startswith(lead[:20]):  # the lead is the body's first sentence, not a paragraph of its own
            lead, body = "", max(lead, body, key=len)  # (a paid article's lead, its teaser, runs on past it)
        if d.get("paid"):  # a paid article: the free part only (its terms), archive.today may have more
            meta["via"] = "excerpt"
        return "\n".join(x for x in (lead, body) if x)[:TEXT_MAX], None, {}
    if tid := hkgolden.thread_id(url):  # 高登: the thread page is a JavaScript shell (its <title> is the site's name)
        t = hkgolden.thread(tid)
        meta["title"] = t["title"]
        if t["published"]:
            meta["published"] = t["published"]
        return t["text"][:TEXT_MAX], t["author"], t["engagement"]
    if h.endswith("lihkg.com"):
        m = re.search(r"/thread/(\d+)", url)
        r = lihkg_api.client().json(f"thread/{m.group(1)}/page/1?order=reply_time") or {}  # the shared LIHKG client
        first = (r.get("item_data") or [{}])[0]
        return sources.paragraphs(first.get("msg") or ""), r.get("user_nickname"), {
            k: r[f] for k, f in (("replies", "no_of_reply"), ("likes", "like_count"), ("dislikes", "dislike_count"))
            if isinstance(r.get(f), int)}
    if h.endswith(("youtube.com", "youtu.be")):
        page = get(url)
        i = page.find("var ytInitialPlayerResponse = ")
        vd = (json.JSONDecoder().raw_decode(page[i + 30:])[0].get("videoDetails") or {}) if i >= 0 else {}
        desc, author = vd.get("shortDescription") or "", vd.get("author")
        views = int(vd["viewCount"]) if str(vd.get("viewCount", "")).isdigit() else None
        i = page.find("var ytInitialData = ")
        if not vd and i >= 0:  # the player data is withheld from the server's IP (LOGIN_REQUIRED); the page data has these
            stack = [json.JSONDecoder().raw_decode(page[i + 20:])[0]]
            while stack:
                o = stack.pop()
                if isinstance(o, dict):
                    if not desc and isinstance(o.get("attributedDescription"), dict):
                        desc = o["attributedDescription"].get("content") or ""
                    if views is None and isinstance(o.get("videoViewCountRenderer"), dict):
                        views = sources.count(((o["videoViewCountRenderer"].get("viewCount") or {}).get("simpleText")))
                    if not author and isinstance(o.get("videoOwnerRenderer"), dict):
                        author = "".join(r.get("text", "") for r in (o["videoOwnerRenderer"].get("title") or {}).get("runs") or [])
                    stack.extend(o.values())
                elif isinstance(o, list):
                    stack.extend(o)
        return desc.strip(), author, {"views": views} if views is not None else {}
    if h == "apps.sfc.hk" and "refNo=" in url:  # 證監會 e-Distribution: a JS page over its own JSON API
        ref = re.search(r"refNo=([\w-]+)", url).group(1)
        d = json.loads(get(f"https://apps.sfc.hk/edistributionWeb/api/news/content?refNo={ref}&lang=TC"))
        return sources.paragraphs(d.get("html") or ""), None, {}
    if h.endswith("881903.com"):  # 商業電台: the article is in the page's VueApp.main({...}) data
        page = get(url.replace("/news/amp/", "/news/"))  # the AMP copy has no VueApp data
        s = page[page.find("VueApp.main(") + 12:]
        art = (json.JSONDecoder().raw_decode(s)[0].get("article") or {}) if "VueApp.main(" in page else {}
        return sources.paragraphs(art.get("content") or ""), None, {}
    if h.endswith("orangenews.hk") and (m := re.search(r"orangenews\.hk/[^/]+/([A-Za-z0-9]+)/", url)):
        # 橙新聞: the page fills its story in with JavaScript from its own API (a form POST)
        body = urllib.parse.urlencode({"handlerName": "contentDetailsHandler", "params": json.dumps(
            {"newsCode": m.group(1), "queryReplay": "2", "isPc": "1", "getLike": "0"})}).encode()
        pace()
        d = (json.loads(sources.fetch("https://api.orangenews.hk/app/bus/content/common/newsDetails", data=body, timeout=TIMEOUT,
                                      headers={"Referer": "https://www.orangenews.hk/"})).get("data") or {})
        return sources.paragraphs(d.get("txt") or "")[:TEXT_MAX] or (d.get("description") or ""), d.get("author") or None, {}
    if h.endswith(SOLVER_HOSTS):  # Reuters: its content API with a solved DataDome cookie (paywall.py)
        return paywall.reuters(url, meta)[:TEXT_MAX], None, {}
    if h.endswith(paywall.DIRECT_HOSTS):  # Bloomberg: the story in the page's own data (proof of concept)
        try:
            return paywall.bloomberg(url, meta)[:TEXT_MAX], None, {}
        except paywall.Blocked as e:  # its robot check everywhere: the feed's summary stays, archive.today is tried
            print("bloomberg:", url[:100], e, flush=True)
            meta["via"] = "excerpt"
            return "", None, {}
    if h.removeprefix("www.") == "hongkongcard.com" and (m := re.search(r"/forum/show/(\d+)", url)):
        # 香港信用卡優惠網討論區: the thread page is a Vue shell over its JSON (2026-10-20)
        d = json.loads(get(f"https://www.hongkongcard.com/vue/forum/threads/{m.group(1)}",
                           headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})).get("data") or {}
        eng = {k: d[f] for k, f in (("replies", "total_replies"), ("views", "total_views"), ("likes", "total_vote_ups"),
                                    ("dislikes", "total_vote_downs")) if isinstance(d.get(f), int)}
        return sources.paragraphs(d.get("content") or "")[:TEXT_MAX], (d.get("user") or {}).get("nickname"), eng
    if h.removeprefix("www.") == "hkhorsedb.com" and urllib.parse.urlsplit(url).path == "/article.php":
        # 香港賽馬資料庫: its RSS (backend.php) links /article.php, a 404; the article is under /cseh/ (2026-10-20)
        url = url.replace("hkhorsedb.com/article.php", "hkhorsedb.com/cseh/article.php", 1)
    if "discuss.com.hk" in h:  # a 香港討論區 thread link in any form: its archiver page holds the opening post
        url = sources.discuss_url(url) or url
    if api := sino_api(url):
        return sino_text(get(api)), None, {}
    if m := QUAMNET_POST.match(url):
        return quamnet_text(get(f"https://www.quamnet.com/api/v1/post/{m.group(1)}",
                                headers={"Accept": "application/json"}), meta), None, {}
    if api := vintagegrail_api(url):
        return vintagegrail_text(get(api)), None, {}
    if h.endswith(tuple(canonical.REPATH)):  # a listed address that answers 404: the site's working one (canonical.py)
        url = canonical.rehost(url) or url
    if h == "hkcd.com.hk":  # 香港商報: the bare name's certificate is for another host; www has the same pages (2026-10-20)
        url = url.replace("//hkcd.com.hk/", "//www.hkcd.com.hk/", 1)
    if h.endswith("flamingwheels.online") and (m := re.search(r"_FgID=([\w+=-]+)", url)):
        # 風火時報: a jQuery shell; the story comes from its POST API, the id hidden in _FgID (fg_id)
        d = json.loads(sources.fetch("https://www.flamingwheels.online/nowall_ct_news_find.do",
                                     data=urllib.parse.urlencode({"id": fg_id(m.group(1))}).encode(), timeout=TIMEOUT))
        if w := _when((d.get("createDate") or "").replace(" ", "T") + "+08:00" if d.get("createDate") else ""):
            meta["published"] = w
        return sources.paragraphs(d.get("content") or "")[:TEXT_MAX], None, {}
    if h.endswith("shimindaily.net") and (m := re.search(r"blog-details\.html\?id=(\d+)", url)):
        # 市民日報: a JavaScript shell over its exhibition-api (2026-10-20); code 10094 「文章已下線」 = taken down
        d = json.loads(get(f"https://shimindaily.expo.macauo2o.com/exhibition-api/exhibitionNews/app/{m.group(1)}"))
        d = d.get("data") or {}
        if w := _when((d.get("onlineDate") or "").replace(" ", "T") + "+08:00" if d.get("onlineDate") else ""):
            meta["published"] = w
        return sources.paragraphs(d.get("content") or "")[:TEXT_MAX], None, {}
    if h.endswith("exmoo.com") and re.search(r"/read/\d+\.html", url):
        return exmoo_issue(url), None, {}
    if h.endswith("tdm.com.mo") and (m := re.search(r"/(?:news-detail|sharelink/news)/(\d+)", url)):
        # TDM: a Vue shell over its JSON API (the list's shareUrl is /sharelink/news/<id>, 2026-10-20)
        d = ((json.loads(get(f"https://www.tdm.com.mo/api/v1.0/news/{m.group(1)}")).get("data") or {}).get("detail") or {})
        t = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2}):\d{2}", (d.get("date") or "").strip())  # MM/DD/YYYY, Macau time
        if t and (w := _when(f"{t[3]}-{t[1]}-{t[2]}T{t[4]}:{t[5]}:00+08:00")):
            meta["published"] = w
        return sources.paragraphs(d.get("content") or "")[:TEXT_MAX], None, {}
    if (got := api_story(url, h, meta)) is not None:  # the 2026-10-23 gap sites whose pages hold no story (below)
        return got
    if (h == "msn.com" or h.endswith(".msn.com")) and (m := re.search(r"msn\.com/([a-z]{2}-[a-z]{2})/.*/ar-([A-Za-z0-9]+)", url)):
        # MSN (2026-10-20): an article page is a JavaScript shell (<title>MSN</title>, no text); its Detail API has
        # the story's HTML
        d = json.loads(get(f"https://assets.msn.com/content/view/v2/Detail/{m.group(1)}/{m.group(2)}"))
        if w := _when(d.get("publishedDateTime") or ""):
            meta["published"] = w
        names = ", ".join(a["name"] for a in d.get("authors") or [] if isinstance(a, dict) and a.get("name"))
        return sources.paragraphs(d.get("body") or "")[:TEXT_MAX], names or None, {}
    if h.removeprefix("www.") == "ifengus.com" and (m := re.search(r"/article/([^/?#]+)", url)):
        # 鳳凰美洲網 (2026-10-20): an Angular shell (the server answers 404 to the article path); the story is the
        # paragraph blocks of /api/articles/<slug> (many posts are a video block only: no text)
        slug = urllib.parse.quote(urllib.parse.unquote(m.group(1)))
        d = json.loads(get(f"https://www.ifengus.com/api/articles/{slug}"))
        if w := _when(d.get("publishedAt") or ""):
            meta["published"] = w
        paras = [sources.paragraphs(b.get("html") or "") for b in d.get("blocks") or [] if b.get("type") == "paragraph"]
        return "\n".join(x for x in [d.get("dek") or ""] + paras if x.strip())[:TEXT_MAX], d.get("author") or None, {}
    if h.removeprefix("www.").endswith(WP_API_HOSTS) and (slug := wp_slug(url)):
        # a WordPress site whose pages refuse us but whose REST API answers (澳門平台 2026-10-20: 403 to the cluster's
        # IP and the ISP IP, the browser got "Website owner? …"): the post by its slug
        d = json.loads(get(f"{urllib.parse.urlsplit(url).scheme}://{urllib.parse.urlsplit(url).netloc}/wp-json/wp/v2/"
                           f"posts?slug={slug}&_fields=content,date_gmt"))
        if d and isinstance(d, list) and isinstance(d[0], dict):
            if d[0].get("date_gmt") and (w := _when(d[0]["date_gmt"] + "+00:00")):
                meta["published"] = w
            text = sources.paragraphs((d[0].get("content") or {}).get("rendered") or "")[:TEXT_MAX]
            if text:
                return text, None, {}
    isp = h.endswith(ISP_HOSTS)
    proxy = resi.url(proxy_country(h)) if h.endswith(PROXY_HOSTS) else os.environ.get("ISP_PROXY") if isp else None
    if h.endswith(PROXY_HOSTS) and not proxy:
        raise RuntimeError("RESI_PROXY not set")
    if isp and not proxy:
        raise RuntimeError("ISP_PROXY not set")
    hdr = catalogue_headers(url)
    if urllib.parse.urlsplit(url).path.lower().endswith(".pdf"):  # a PDF itself (中國融資's issues, /static/upload/pdf/88.pdf)
        try:
            return pdf_text(get_bytes(url, proxy=proxy, headers=hdr, isp=isp)), None, {}
        except urllib.error.HTTPError as e:
            if e.code in (403, 503) and h.endswith(BROWSER_HOSTS):  # Cloudflare's challenge before the PDF (澳門衛生局's
                raise Challenge(url) from e                        # press releases, 2026-10-20): the browser pass
            raise
    try:
        page = get(url, proxy=proxy, headers=hdr, isp=True) if isp else resi_get(url, proxy, hdr)
    except Exception:
        if h.endswith(BROWSER_HOSTS) or re.sub(r"^https?://(www\.)?", "", url).startswith(BROWSER_PATHS):
            raise Challenge(url)  # Cloudflare answers 403 before the challenge page
        raise
    if challenged(page) or (not page.strip() and h.endswith(BROWSER_HOSTS)):
        raise Challenge(url)  # (an empty answer from a browser site: CloudFront's 202 for AWS WAF's challenge, Prestige)
    if (frame := frame_src(url, page)) is not None:  # a frameset over the page (易發車網's pub_art.php)
        page = get(frame, proxy=proxy, headers=hdr, isp=True) if isp else get(frame, proxy=proxy, headers=hdr)
    if len(page) < 6000 and re.search(r"google\.com/recaptcha/|g-recaptcha", page):  # a reCAPTCHA wall (uwants, 2026-10-20):
        raise urllib.error.HTTPError(url, 429, "reCAPTCHA wall", {}, None)  # a pause: read again later, never ""
    if (js := WRITE_SHELL.search(page[:5000])) and "smart-design-mode" in page[:500]:
        page = written(get(html.unescape(js.group(1)))) or page  # 凡科: the page is written by a script
    if w := page_date(page):
        meta["published"] = w
    if "discuss.com.hk" in h:
        text, author = discuss_text(page)
        return text, author, {}
    if (text := forum_post(page)) is not None:
        return text, None, {}
    if h == "news.rthk.hk" and (text := rthk_text(page)):
        return text, None, {}
    if (text := fragment_body(url, page)) is not None:  # one #press-N section of a page of press releases (大館)
        return text, None, {}
    if LOGIN_WALL.search(page):  # a login wall's headline and pitch only (Regulation Asia): no story text
        meta["via"] = "excerpt"
        return "", None, {}
    if (ex := memberpress_excerpt(page)) is not None:  # a WordPress MemberPress paywall: its public excerpt only
        meta["via"] = "excerpt"
        return ex, None, {}
    if h.endswith("hk01.com"):  # 香港01: the article's blocks are in the page's __NEXT_DATA__
        text = hk01_text(page)
        if text:
            return text, None, {}
    text = article_text(page)
    body = marked_body(url, page)
    if body is not None and (len(body) > len(text) or h.endswith(BODY_ONLY)):
        text = body
    if len(text) < 200 and (pdf := pdf_link(url, page)):  # the page only opens a PDF (醫院管理局's press releases)
        text = max(text, pdf_text(get_bytes(pdf, proxy=proxy, headers=hdr, isp=isp)), key=len)
    for pdf in attached_pdfs(url, page):  # a notice whose content is its attached PDFs (行政公職局's tenders)
        text = "\n".join(x for x in (text, pdf_text(get_bytes(pdf, proxy=proxy, headers=hdr, isp=isp))) if x)[:TEXT_MAX]
    if len(text) < 200 and "window.__NUXT__" in page:  # Nuxt (YO!生活): the post's text is a string in the page data
        text = max(text, nuxt_text(page), key=len)
    if h == "telegra.ph":  # NOW's Telegram channel links its stories' telegra.ph copies, made by the RSStT bot
        text = re.sub(r"\n*Generated by RSStT\b.*$", "", text, flags=re.S)
    if len(text) < 200 and "self.__next_f.push" in page:  # Next.js (資本): the article is in the flight data
        text = max(text, article_text(next_flight(page)), flight_ld_text(page), key=len)
    if paywall.excerpt(url, page, text):  # the public part of a paywalled article: archive.today is tried for the rest
        meta["via"] = "excerpt"
    if aggregator(url) and (orig := original_link(url, page)):  # the story it copies: the readers take that one's text
        meta["original"] = orig
    return text, None, {}


# a page that is only a redirect to a PDF: window.open('…pdf') / location.href = '…pdf' / a meta refresh to one
# (醫院管理局's ha_view_content.asp press releases: <script>window.open('/haho/ho/pad/260916PAE.pdf', '_self')</script>)
PDF_REDIRECT = re.compile(r"""(?:window\.open\(|location(?:\.href)?\s*=|location\.(?:replace|assign)\(|http-equiv=["']?refresh["']?[^>]*url=)\s*["']?"""
                          r"""([^"'<>\s)]+\.pdf)\b""", re.I)
PDF_MAX = 20 * 1024 * 1024  # bytes: a PDF bigger than this is not read


# a page that only shows a PDF: an <iframe>/<embed>/<object> of it, or of pdf.js's viewer.html?file=<pdf> (香港紅十字會's
# 紅十字脈搏 issue pages, 2026-10-20: the page's one text is the PDF's path)
PDF_EMBED = re.compile(r"""<(?:iframe|embed|object)\b[^>]*?\b(?:src|data|data-src)=["'](?:[^"']*?[?&]file=)?([^"'?&#<>\s]+\.pdf)\b""", re.I)


def pdf_link(url, page):
    """the PDF a page sends the browser to (PDF_REDIRECT, in its first 20 kB) or only shows (PDF_EMBED), absolute;
    else None"""
    m = PDF_REDIRECT.search(page[:20000]) or PDF_EMBED.search(page)
    return urllib.parse.urljoin(url, html.unescape(m.group(1))) if m else None


def frame_src(url, page):
    """a page that is only a <frameset> (易發車網 2026-10-20: pub_art.php is a frameset whose one frame,
    index2.php?tourl=…, redirects to the article's page): its first frame's address on the same site; else None"""
    if len(page) > 5000 or not re.search(r"<frameset\b", page, re.I):
        return None
    m = re.search(r"""<frame\b[^>]*\bsrc=["']?([^"'\s>]+)""", page, re.I)
    u = urllib.parse.urljoin(url, html.unescape(m.group(1))) if m else None
    return u if u and site(u) == site(url) and u != url else None


# pages of several posts, each post a #fragment of it: the section the fragment names (大館 2026-10-20: every press
# release is an <article class="press-article"> holding <div id="press-N" class="anchor-tag">, its text in
# <div class="collapse-wrap"> under the title, ending in its "Download" and "Copy Url" buttons)
FRAGMENT_XPATHS = {"taikwun.hk": '//article[.//*[@id=$frag]]//div[contains(concat(" ", normalize-space(@class), " "), " collapse-wrap ")]'}


def fragment_body(url, page):
    """the text of the section url's #fragment names on a FRAGMENT_XPATHS site ("" when it is not on the page); else
    None (not such a site, no fragment)"""
    frag = urllib.parse.urlsplit(url).fragment
    xp = next((x for k, x in FRAGMENT_XPATHS.items() if host(url) == k or host(url).endswith("." + k)), None)
    if not frag or not xp:
        return None
    import lxml.html
    try:
        found = lxml.html.fromstring(page).xpath(xp, frag=frag)
    except Exception:
        return None
    text = "\n".join(t for t in (block_text(f) for f in found) if t)
    return re.sub(r"(\n(Download|Copy Url))+$", "", text)[:TEXT_MAX]


# a login wall: the headline and a subscription pitch instead of the story (Regulation Asia 2026-10-20: every article,
# logged out, is "To get access to this article, please login or get in touch to request subscription information")
LOGIN_WALL = re.compile(r">\s*To get access to this article, please login or get in touch to request subscription information")


def wp_slug(url):
    """a WordPress post address's slug (its last path segment), else None"""
    seg = [x for x in urllib.parse.urlsplit(url).path.split("/") if x]
    return seg[-1] if seg and re.fullmatch(r"[\w%-]+", seg[-1]) and not seg[-1].isdigit() else None


def get_bytes(url, proxy=None, headers=None, isp=False):
    """a file's bytes (a PDF) on the route its page took; at most PDF_MAX"""
    import urllib.request
    slow(url)
    pace()
    h = {"User-Agent": sources.UA, **(headers or {})}
    opener = urllib.request.build_opener(*([urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []))
    with (_isp_lock() if isp else _nolock()):
        try:
            r = opener.open(urllib.request.Request(urllib.parse.quote(url, safe=":/?&=%#+,;@!$'()*~[]"), headers=h),
                            timeout=TIMEOUT)
        except Exception as e:
            if proxy and not isp:
                resi.check(e)  # IPRoyal out of traffic: resi.ProxyOut (a pause, as in sources.fetch)
            raise
        with r:
            data = r.read(PDF_MAX + 1)
    if proxy and isp:
        resi.count_isp(len(data) + resi.OVERHEAD)
    elif proxy:
        resi.count(len(data) + resi.OVERHEAD, url)
    if len(data) > PDF_MAX:
        raise ValueError(f"PDF over {PDF_MAX} bytes: {url}")
    return data


# sites whose notice pages hold their content only as attached PDFs: the PDF links of the page's file list (行政公職局
# 2026-10-20: a tender's page is its title and <div class="page__article-files"> with 開標結果.pdf); at most ATTACH_MAX
ATTACH_XPATHS = {"safp.gov.mo": '//div[contains(@class, "page__article-files")]//a[contains(@href, ".pdf")]/@href'}
ATTACH_MAX = 3


def attached_pdfs(url, page):
    xp = next((x for k, x in ATTACH_XPATHS.items() if host(url) == k or host(url).endswith("." + k)), None)
    if not xp:
        return []
    import lxml.html
    try:
        hrefs = lxml.html.fromstring(page).xpath(xp)
    except Exception:
        return []
    out = []
    for x in hrefs:
        u = urllib.parse.urljoin(url, x.strip())
        if u not in out:
            out.append(u)
    return out[:ATTACH_MAX]


def nuxt_text(page):
    """a Nuxt page's story (YO!生活 2026-10-20: the post is Markdown in a string argument of window.__NUXT__'s function,
    the page's own HTML has only its description): the longest string there that starts as the page's description
    does, Markdown marks (pictures, headings, bold, quotes) and HTML tags taken out; else "" """
    import lxml.html
    i = page.find("window.__NUXT__")
    if i < 0:
        return ""
    try:
        desc = (lxml.html.fromstring(page).xpath('//meta[@property="og:description"]/@content') or [""])[0]
    except Exception:
        desc = ""
    lead = _letters(desc)[:12]
    if len(lead) < 6:
        return ""
    end = page.find("</script>", i)
    best = ""
    for m in re.finditer(r'"((?:[^"\\]|\\.){200,})"', page[i:end if end > 0 else None]):
        try:
            v = json.loads(m.group(0))
        except ValueError:
            continue
        if _letters(v).startswith(lead) and len(v) > len(best):
            best = v
    if not best:
        return ""
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", best)          # pictures
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)          # links: their text
    t = re.sub(r"<[^>]+>", "\n", t)
    t = re.sub(r"(?m)^\s*(#{1,6}|>)\s*", "", t)             # headings, quotes
    t = t.replace("**", "").replace("⸻", "")
    return "\n".join(x.strip() for x in t.splitlines() if x.strip())[:TEXT_MAX]


def exmoo_issue(url):
    """力報's e-paper issue (exmoo.com/read/<n>.html, 2026-10-20): its pages are pictures whose hot spots name the
    articles printed there (article_<id>, www.exmoo.com/article/<id>.html): every article in page order, its headline
    and its text, until TEXT_MAX"""
    page = get(url)
    ids = []
    for i in re.findall(r"article_id&quot;:&quot;article_(\d+)|\"article_id\":\s*\"article_(\d+)", page):
        i = i[0] or i[1]
        if i not in ids:
            ids.append(i)
    import lxml.html
    parts, n = [], 0
    for i in ids:
        if n >= TEXT_MAX:
            break
        try:
            p = get(f"https://www.exmoo.com/article/{i}.html")
        except Exception as e:  # one article gone: the others still make the issue
            print("exmoo:", i, repr(e)[:100], flush=True)
            continue
        try:
            head = " ".join((lxml.html.fromstring(p).xpath("//h1") or [None])[0].text_content().split())
        except Exception:
            head = ""
        t = "\n".join(x for x in (head, article_text(p)) if x)
        parts.append(t)
        n += len(t)
    return "\n\n".join(parts)[:TEXT_MAX]


class _nolock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def pdf_text(data):
    """a PDF's text (PyMuPDF): one line per text block, a block's lines joined (a space between two Latin words), the
    page numbers ("- 2-") left out"""
    import pymupdf
    paras = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for pg in doc:
            for b in pg.get_text("blocks", sort=True):
                if b[6] != 0:  # a picture
                    continue
                t = ""
                for ln in (x.strip() for x in b[4].splitlines()):
                    if not ln:
                        continue
                    if t and re.search(r"[A-Za-z0-9,.;:)\"']$", t) and re.match(r"[A-Za-z0-9(\"']", ln):
                        t += " "
                    t += ln
                t = re.sub(r"[ \t\xa0]+", " ", t).strip()
                if t and not re.fullmatch(r"[-–\s]*\d{1,3}[-–\s]*", t):
                    paras.append(t)
    return "\n".join(paras)[:TEXT_MAX]


FG_ALPHABET = "OPQRSTUVWXYZABCDEFGHIJKLMNabcdefghijklmnopqrstuvwxyz0123456789+-"  # 風火時報's jq.flamingwheels.marshal.js


def fg_id(fg):
    """風火時報's _FgID (its first 12 characters: base64 in its own alphabet, "_" for "=") → the story id"""
    s = fg[:12].replace("_", "=").rstrip("=")
    bits = "".join(format(FG_ALPHABET.index(c), "06b") for c in s)
    qs = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits) - len(bits) % 8, 8)).decode("latin-1")
    return re.search(r"id=(\d+)", qs).group(1)


def rthk_text(page):
    """an RTHK news page's story: its div.itemFullText, lines kept (2026-10-20: the generic extractor took a replay's
    dateline "2026-10-20 HKT 09:35" over its one written line); "" when the page has none"""
    import lxml.html
    try:
        got = lxml.html.fromstring(page).xpath('//div[contains(concat(" ", normalize-space(@class), " "), " itemFullText ")]')
    except Exception:
        return ""
    return sources.paragraphs(lxml.html.tostring(got[0], encoding="unicode"))[:TEXT_MAX] if got else ""


# 凡科 (website.xin) sites (澳門法治報, 2026-10-20): a 1.4 KB shell whose body is a script that document.write()s the
# whole page; its <script src> ends in .Body.js
WRITE_SHELL = re.compile(r"""<script src=['"](https://[^'"]+\.Body\.js[^'"]*)['"]""")
_JS_ESC = {"n": "\n", "r": "\r", "t": "\t", "b": "", "f": "", "v": "", "0": ""}


def written(js):
    """the HTML a script writes with document.write('…') (JavaScript string escapes undone), "" when none"""
    parts = re.findall(r"document\.write\('((?:[^'\\]|\\.)*)'\)", js or "")
    un = lambda m: chr(int(m.group(1)[1:], 16)) if m.group(1)[0] == "u" and len(m.group(1)) == 5 else _JS_ESC.get(m.group(1), m.group(1))
    return "".join(re.sub(r"\\(u[0-9a-fA-F]{4}|.)", un, x) for x in parts)


def flight_data(page):
    """a Next.js page's streamed flight data: its self.__next_f.push([1, "…"]) strings joined"""
    chunks = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)', page):
        try:
            chunks.append(json.loads(m.group(1)))
        except ValueError:
            pass
    return "".join(chunks)


def next_flight(page):
    """a Next.js page's streamed flight data (self.__next_f.push([1, "…"]) strings) as one HTML string"""
    s = flight_data(page)
    s = s.replace('\\"', '"')  # HTML inside a JSON string of the flight data
    return "<html><body>" + s + "</body></html>"


def hk01_text(page):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return ""
    paras, stack = [], [json.loads(m.group(1))]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if isinstance(o.get("blocks"), list) and any(isinstance(b, dict) and b.get("blockType") == "text" for b in o["blocks"]):
                for b in o["blocks"]:
                    if isinstance(b, dict) and b.get("blockType") == "summary" and isinstance(b.get("summary"), list):
                        # a fact box (address, dates, opening hours: 2026-10-20) is a list of lines, not htmlTokens
                        paras.extend(x.strip() for x in b["summary"] if isinstance(x, str) and x.strip())
                    elif isinstance(b, dict) and b.get("blockType") in ("text", "summary"):
                        for line in b.get("htmlTokens") or []:
                            t = "".join(x.get("content", "") for x in line if isinstance(x, dict)).strip()
                            if t:
                                paras.append(t)
                break
            stack.extend(reversed(list(o.values())))
        elif isinstance(o, list):
            stack.extend(reversed(o))
    import lxml.html
    try:
        desc = (lxml.html.fromstring(page).xpath('//meta[@property="og:description"]/@content') or [""])[0].strip()
    except Exception:
        desc = ""
    if desc and paras and not paras[0].startswith(desc[:15]):
        paras.insert(0, desc)
    return "\n".join(paras)[:TEXT_MAX]


BROWSER_PAGE_MAX = 180  # seconds one browser_pass page can take: goto 60 + cleared() 75 + its load wait 30 + 15 reading


class Challenge(Exception):
    pass


# the bytes of a PDF fetched by the page itself (its cookies, its user agent, the challenge passed), as numbers
PDF_FETCH = """async (u) => { const r = await fetch(u, {credentials: "include"});
  if (!r.ok) return {status: r.status, bytes: []};
  const b = new Uint8Array(await r.arrayBuffer()); return {status: r.status, bytes: b.length > %d ? [] : Array.from(b)}; }"""


def browser_pdf(pg, u):
    """a PDF behind Cloudflare's challenge (澳門衛生局 2026-10-20: /docs/…/…_000_.pdf answers the challenge's 403 to
    every plain client): the site's home page opened to pass the challenge, then the PDF fetched from that page →
    its text ("" when it could not be read)"""
    from browser_fetch import cleared
    parts = urllib.parse.urlsplit(u)
    pg.goto(f"{parts.scheme}://{parts.netloc}/", wait_until="domcontentloaded", timeout=60000)
    if not cleared(pg):
        return ""
    if pg.url.startswith("https:") and u.startswith("http:"):  # the home page went to https: so does the PDF
        u = "https:" + u[5:]  # (an http fetch from an https page is blocked as mixed content)
    r = {}
    for _ in range(5):  # the home page may still be navigating right after the challenge (the context is destroyed)
        try:
            r = pg.evaluate(PDF_FETCH % PDF_MAX, u)
            break
        except Exception:
            time.sleep(3)
    if r.get("status") != 200 or not r.get("bytes"):
        print("browser pdf:", u[:100], r.get("status"), flush=True)
        return ""
    return pdf_text(bytes(r["bytes"]))


def browser_country(u):
    """the yycstream exit a page's Chrome takes: its catalogue site's "proxy" country (PROXY_COUNTRY; Lifestyle Asia:
    hk), else None (the cluster's own IP)"""
    h = host(u).removeprefix("www.")
    return next((c for k, c in PROXY_COUNTRY.items() if h == k or h.endswith("." + k)), None)


def browser_pass(urls, profile="cache-profile", until=None, dates=None, country=None):
    """pages behind Cloudflare's managed challenge, in one real Chrome (Patchright) like browser_fetch.py → {url: text}
    for every page it opened ("" when nothing came back). until: a time.monotonic() after which no further page is
    opened (a page takes at most BROWSER_PAGE_MAX seconds); the pages not reached are not in the result. dates (a
    dict) gets {url: the publish time the page states}. Pages of a site with a "proxy" country (browser_country) are
    read in a Chrome of their own on yycstream with that exit (country), after the others."""
    out = {}
    if not urls:
        return out
    if country is None:
        later = {}
        for u in urls:
            if cc := browser_country(u):
                later.setdefault(cc, []).append(u)
        urls = [u for u in urls if not browser_country(u)]
        if urls:
            out.update(_chrome_pages(urls, profile, until, dates, ""))
        for cc, us in later.items():
            try:
                out.update(_chrome_pages(us, f"{profile}-{cc}", until, dates, cc))
            except resi.ProxyOut:  # IPRoyal out of traffic: these pages are not reached this run (a pause)
                pass
        return out
    return _chrome_pages(urls, profile, until, dates, country)


def _chrome_pages(urls, profile, until, dates, country):
    """browser_pass's pages in one Chrome: on the cluster's IP (country ""), else on yycstream with that exit"""
    out = {}
    from patchright.sync_api import sync_playwright
    from browser_fetch import cleared
    import chrome_state
    # browser_fetch.py's cookies (Postgres, chrome_state): the challenges already passed (a fresh profile is stuck on
    # the challenge), read and never written back, so the jobs never share state. On a proxy: no cookies (a challenge's
    # cookie is bound to the IP that passed it)
    kw, flush = {}, None
    if country:
        kw["proxy"] = resi.playwright(country, session=resi.session_id(), lifetime="30m")
    with sync_playwright() as p:
        ctx = chrome_state.launch(p, f"{profile}-proxy" if country else "browser-profile", save=False, channel="chrome",
                                  headless=False, no_viewport=True, **kw)
        if country:
            flush = resi.count_browser(ctx, f"browser-{country}")
        try:
            for u in urls:
                if until is not None and time.monotonic() >= until:
                    break
                out[u] = ""
                # each page in its own tab, closed after: one tab over 25 pages grew one renderer to 494 MB (the job
                # to 1.1 GB of its 1500Mi; two runs OOMKilled, 14 Oct 2026); the context keeps the challenge cookies
                pg = ctx.new_page()
                try:
                    pace()
                    if ck := lang_cookie(u):
                        ctx.add_cookies([ck])
                    if urllib.parse.urlsplit(u).path.lower().endswith(".pdf"):
                        out[u] = browser_pdf(pg, u)  # a PDF behind the challenge (澳門衛生局's press releases)
                        continue
                    pg.goto(u, wait_until="domcontentloaded", timeout=60000)
                    if cleared(pg):
                        for _ in range(5 + INCAPSULA_WAITS):  # the page may still be navigating right after the challenge
                            time.sleep(3)
                            try:
                                page = pg.content()
                                if incapsula(page):  # its script reloads the page once the cookie is set (up to 45 s)
                                    continue
                                if challenged(page) or blocked(page):  # still the wall: nothing read (獨立媒體 2026-10-20
                                    break                              # kept Cloudflare's "you have been blocked" text)
                                out[u] = max(article_text(page), marked_body(u, page) or "", key=len)
                                if dates is not None and (w := page_date(page)):
                                    dates[u] = w
                                break
                            except Exception:
                                pass
                except Exception as e:
                    if country:
                        resi.check_browser(e)  # IPRoyal out of traffic: ProxyOut, the page stays a candidate
                    print("browser:", u, repr(e)[:120], flush=True)
                finally:
                    try:
                        pg.close()
                    except Exception:
                        pass
        finally:
            chrome_state.close(ctx)
            if flush:
                flush()
    return out


def candidates(limit=MAX_PAGES):
    since = (datetime.now(corpus.HKT) - timedelta(days=DAYS - 1)).strftime("%Y-%m-%d")
    rows = corpus.db().execute("""SELECT DISTINCT p.id, p.url, p.sid FROM posts p JOIN tags t ON t.post_id=p.id
                                  WHERE t.relevant=1 AND p.day >= %s AND p.text IS NULL AND p.cached_at IS NULL
                                  AND """ + corpus.seen_cond("p", "cache") + " ORDER BY p.id DESC", (since,)).fetchall()
    out = []
    for r in rows:
        if r["sid"] in ("facebook", "instagram") or (r["sid"] or "").startswith("tikhub_") or host(r["url"]).endswith(SKIP_HOSTS):
            continue
        out.append((r["id"], r["url"]))
    return out[:limit]


def run():
    todo = candidates()
    by_host = defaultdict(list)
    for pid, url in todo:
        by_host[host(url)].append((pid, url))
    stats = defaultdict(int)
    browser = []

    def host_run(items):  # one host's pages in order (one request at a time per host)
        for pid, url in items:
            if host(url).endswith(BROWSER_HOSTS) and browser_hosts_challenged.get(host(url)):
                browser.append((pid, url))
                continue
            try:
                meta = {}
                text, author, eng = fetch_one(url, meta=meta)
                save(pid, text, author, eng, via=meta.get("via", "page"))
                canonical.save(pid, url, last_page())  # the address the page states (開啟原文 opens it)
                stats["ok" if text else "empty"] += 1
            except Challenge:
                browser_hosts_challenged[host(url)] = True
                browser.append((pid, url))
            except resi.ProxyOut:  # IPRoyal out of traffic: a pause, the page stays a candidate (not given up)
                stats["proxy_out"] += 1
            except Exception as e:
                save(pid, "", None, None)
                stats["failed"] += 1
                print("failed:", url, repr(e)[:150], flush=True)

    browser_hosts_challenged = {}
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(host_run, by_host.values()))
    if browser:
        try:
            got = browser_pass([u for _, u in browser])
        except Exception as e:
            print("browser pass failed:", repr(e)[:200], flush=True)
            got = {}
        for pid, url in browser:
            save(pid, got.get(url, ""), None, None, via="browser")
            stats["browser_ok" if got.get(url) else "browser_failed"] += 1
    print(f"cache: {len(todo)} pages: {dict(stats)}", flush=True)


def save(pid, text, author, eng, via=None):
    text = (text or "").strip()[:TEXT_MAX]
    corpus.set_text(pid, text, author=author or None, engagement=eng or None, via=via if text or via == "excerpt" else None)
    import events  # the live dashboard's Full text panel
    events.emit("fulltext", pid, ok=bool(text), chars=len(text), head=text[:700] or None, via=via,
                route="browser" if via == "browser" else last_route() or "direct",
                reason=None if text else "the page had no article text")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for u in sys.argv[1:]:
            try:
                t, a, e = fetch_one(u)
            except Challenge:
                t, a, e = browser_pass([u]).get(u, ""), None, None
            except Exception as ex:
                t, a, e = f"FAILED {ex!r}"[:200], None, None
            print(f"== {u}\nauthor={a} engagement={e} chars={len(t)}\n{t[:600]}\n")
    else:
        with corpus.stage("cache"):  # the CronJob's run; a restart starts from now (corpus.STAGES)
            run()
