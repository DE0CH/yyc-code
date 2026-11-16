#!/usr/bin/env python3
"""Sources behind Cloudflare's managed challenge, read with a real browser: 成報, 亞洲週刊, 明報月刊, 獨立媒體, and the
catalogue sites (sites.json) marked "browser" (正報, 東Touch, …; parsed by sites_fetch.read). Plain HTTP
clients are refused on these sites (成報 even with the clearance cookie: 429), so Patchright (Playwright patched not to
look automated) drives Google Chrome under Xvfb, ticks the challenge's checkbox when it stalls, reads each site's
article links and writes the document browser.json (corpus.file_put), which sources.py reads (one status per site).

CronJob browser (every 30 min): xvfb-run python3 browser_fetch.py; CronJob sites-browser: ... browser_fetch.py catalogue
No proxy: the server's own IP passes. Pictures, fonts, video and audio are never loaded (nomedia.block: text only,
Deyao 2026-10-20); Cloudflare's challenge (challenges.cloudflare.com) is never blocked.
"""
import os, re, sys, time, urllib.parse
from datetime import datetime, timezone, timedelta
from patchright.sync_api import sync_playwright

import chrome_state
import corpus
import nomedia
import resi

HKT = timezone(timedelta(hours=8))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
SINGPAO = "https://www.singpao.com.hk/"
SINGPAO_BUDGET = 75  # seconds for all of 成報's pages: the job (deadline 300 s) reads three more sites after it
START = time.monotonic()  # the job's start (its CronJob deadline is 300 s)
RETRY_BEFORE = 150  # seconds into the job: a site's second load only before this (亞洲週刊/明報月刊, details_site)
SINGPAO_RECENT_IDS = 150  # 成報's article ids rise steadily; keep those near the newest (no dates on its lists)


AWS_WAF = re.compile(r"token\.awswaf\.com|awsWafCookieDomainList|gokuProps", re.I)  # AWS WAF's challenge page


def _aws_waf(pg):
    try:
        return bool(AWS_WAF.search(pg.content() or ""))
    except Exception:
        return False


def cleared(pg):
    """wait out the managed challenge (any page can raise it again), ticking its checkbox when it stalls; AWS WAF's
    challenge too (Prestige HK, 2026-10-20: the site's own title on a page that is only the challenge script)"""
    for i in range(30):
        try:
            title = pg.title().lower()
        except Exception:
            title = "loading"
        if not any(w in title for w in ("moment", "loading", "verification", "checking", "請稍候", "请稍候")) and not _aws_waf(pg):
            pg.wait_for_load_state("domcontentloaded", timeout=30000)
            return True
        if i in (4, 10, 16, 22):
            frame = pg.locator("iframe[src*='challenges.cloudflare.com']")
            box = frame.bounding_box() if frame.count() else None
            if box:
                pg.mouse.click(box["x"] + 30, box["y"] + box["height"] / 2)
        time.sleep(2.5)
    return False


def links(pg, selector):
    for _ in range(5):  # the page may still be navigating right after the challenge
        try:
            return pg.eval_on_selector_all(selector, "els => els.map(e => [e.href, (e.innerText || e.title || '').trim()])")
        except Exception:
            time.sleep(3)
    return []


def singpao(pg):
    """the home page and each section's list page, within SINGPAO_BUDGET. A page that fails (a 45 s goto, live
    2026-10-15) is skipped and tried again on the next run; the site fails only when no page could be read"""
    got, errors = [], []
    end = time.monotonic() + SINGPAO_BUDGET
    for n, url, timeout in [(0, SINGPAO, 90000)] + [(n, f"{SINGPAO}index.php?fi=news{n}", 45000) for n in range(1, 10)]:
        left = end - time.monotonic()
        if left < 5:  # the job's other sites need the rest of its 300 s deadline: the next run reads these
            print(f"成報: time budget spent, pages {n}-9 left for the next run", flush=True)
            break
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=min(timeout, int(left * 1000)))
            cleared(pg)
            got += links(pg, "a[href*='fi=news']")
        except Exception as e:
            errors.append(e)
            print(f"成報: page {n} skipped: {e!r}"[:200], flush=True)
    if errors and not got:
        raise errors[0]
    found = {}
    for href, title in got:
        m = re.search(r"fi=(news\d+)&id=(\d+)", href)
        title = re.sub(r"\.\.$", "", " ".join(title.split()))
        if m and len(title) > 4:
            i = int(m.group(2))
            if i not in found or len(title) > len(found[i][1]):
                found[i] = (f"{SINGPAO}index.php?fi={m.group(1)}&id={i}", title)
    newest = max(found) if found else 0
    return [{"title": t, "url": u, "published": None} for i, (u, t) in sorted(found.items()) if i >= newest - SINGPAO_RECENT_IDS]


def details_site(home):
    """亞洲週刊 / 明報月刊 (one platform): /article/details/<section>/<issue>/<epoch ms>/<title>. A load that shows no
    article links is loaded once more while the job is under RETRY_BEFORE seconds old (亞洲週刊 read 0 articles on 2
    of 4 runs, 16 Oct 2026, the next run fine); a site that still shows none raises with the page's title and URL"""
    def read(pg):
        got, seen = [], None
        for attempt in range(2):
            if attempt and time.monotonic() - START > RETRY_BEFORE:
                break
            pg.goto(home, wait_until="domcontentloaded", timeout=90000)
            cleared(pg)
            for _ in range(6):  # the article list renders after load
                time.sleep(2.5)
                got = links(pg, "a[href*='/article/details/']")
                if got:
                    break
            if got:
                break
            try:
                seen = (pg.title(), pg.url)
            except Exception as e:
                seen = (repr(e)[:80], home)
        if not got:
            raise RuntimeError(f"no article links on the page (title {seen[0]!r}, {seen[1]})"[:200])
        out = {}
        for href, title in got:
            path = urllib.parse.unquote(urllib.parse.urlparse(href).path)
            m = re.match(r"/article/details/([^/]+)/[^/]+/(\d{12,13})/", path)
            title = " ".join(title.split())
            if m and len(title) > 4 and (href not in out or len(title) > len(out[href]["title"])):
                ts = datetime.fromtimestamp(int(m.group(2)) / 1000, timezone.utc).astimezone(HKT)
                out[href] = {"title": title, "url": href, "section": m.group(1), "published": ts.isoformat(timespec="minutes")}
        return list(out.values())
    return read


def rss_site(url):
    """an RSS feed Cloudflare only serves to a real browser (獨立媒體: its pages answer "Attention Required" even to the
    browser, its rss.xml passes); Chrome shows the XML as text in a <pre>"""
    def read(pg):
        pg.goto(url, wait_until="domcontentloaded", timeout=90000)
        cleared(pg)
        return rss_items(pg.inner_text("pre") if pg.locator("pre").count() else pg.content())
    return read


def rss_items(text):
    """sources.rss rows → browser.json items (published = the feed's pubDate; sources.parse_time reads it; text = the
    story the item's description carries, feed_body)"""
    import sources
    return [dict({"title": it["title"], "url": it["url"], "section": it.get("section") or None,
                  "published": it.get("time") or None, "author": it.get("author")},
                 **({"text": t} if (t := feed_body(it.get("desc") or "")) else {}))
            for it in sources.rss(text) if it.get("title") and it.get("url")]


def feed_body(desc):
    """the whole story a Drupal feed item's description carries (獨立媒體 2026-10-20: its <description> is the node,
    the taxonomy fields, the picture and the body field <div class="field-name-body">; the article pages refuse even
    the browser at times, Cloudflare's block page) → its paragraphs, or "" when the description has no body field"""
    import html as htmlmod
    import lxml.html
    import sources
    if "field-name-body" not in desc:
        desc = htmlmod.unescape(desc)  # escaped once more inside the XML
        if "field-name-body" not in desc:
            return ""
    try:
        body = lxml.html.fromstring(desc).xpath('//div[contains(@class, "field-name-body")]')
    except Exception:
        return ""
    return sources.paragraphs(lxml.html.tostring(body[0], encoding="unicode")) if body else ""


def catalogue_site(site):
    """a catalogue site (sites.json) marked "browser": the page, feed or sitemap loaded in Chrome (the challenge passed,
    then loaded again for the document itself), parsed by sites_fetch.read like every other catalogue site"""
    import sites_fetch

    def fetch(url, pg):
        pg.goto(url, wait_until="domcontentloaded", timeout=90000)
        if not cleared(pg):
            raise RuntimeError(f"challenge not passed on {url}")
        resp = pg.goto(url, wait_until="domcontentloaded", timeout=90000)
        if resp is not None and resp.ok and not re.search(r"text/html", resp.headers.get("content-type", "")):
            return resp.text()  # a feed, sitemap or JSON answer: the body itself, not Chrome's XML viewer
        return pg.content()

    def read(pg):
        rows = sites_fetch.read(site, fetch=lambda u: fetch(u, pg))
        return [{"title": r["title"], "url": r["url"], "section": r.get("section") or None,
                 "published": r.get("time") or None, "author": r.get("author")} for r in rows]
    return read


def catalogue_sites():
    import sites_fetch
    return [(s["id"], s["name"], catalogue_site(s)) for s in sites_fetch.load_read() if s.get("browser")]


def catalogue_proxies():
    """{sid: exit country} of the browser sites with a "proxy" (yycstream; Lifestyle Asia: hk)"""
    import sites_fetch
    return {s["id"]: s["proxy"] for s in sites_fetch.load_read()
            if s.get("browser") and isinstance(s.get("proxy"), str) and s["proxy"] != "isp"}


SITES = [("singpao", "成報", singpao), ("yzzk", "亞洲週刊", details_site("https://www.yzzk.com/")),
         ("mpmonthly", "明報月刊", details_site("https://www.mingpaomonthly.com/")),
         ("inmediahk", "獨立媒體", rss_site("https://www.inmediahk.net/rss.xml"))]


CATALOGUE_DOC = "browser-sites.json"  # the catalogue's browser sites: their own job (CronJob sites-browser), own profile
CATALOGUE_BUDGET = int(os.environ.get("BROWSER_SITES_SECONDS", 1500))  # seconds of reading; the job's deadline is 1800


CATALOGUE_BATCH = 8  # catalogue sites per Chrome: a fresh browser per batch keeps memory flat (OOM at 1536 Mi, 2026-10-20)


def fresh_tab(ctx, pg):
    """a new tab after a site failed: the failed one can still be navigating (伊莉's 403 went on to Chrome's error
    page and interrupted the next site's goto, 2026-10-21; tests/test_browser_isolation.py)"""
    new = ctx.new_page()
    try:
        pg.close()
    except Exception:  # noqa: BLE001: a crashed tab is closed already
        pass
    return new


def main(catalogue=False):
    """the four sites (browser.json), or with catalogue=True the catalogue sites marked "browser" (browser-sites.json,
    a Chrome profile of its own so the two jobs never share one, a fresh Chrome every CATALOGUE_BATCH sites)"""
    result = {"fetched": datetime.now(HKT).isoformat(timespec="seconds"), "sites": {}}
    sites = catalogue_sites() if catalogue else SITES
    profile = "browser-profile-sites" if catalogue else "browser-profile"  # chrome_state's names (Postgres)
    batch = CATALOGUE_BATCH if catalogue else len(sites) or 1
    # the batches: the sites on the cluster's IP, then those with a "proxy" country, each country in its own Chrome
    # on yycstream (a profile of its own: a challenge's cookie is bound to the IP that passed it)
    countries = catalogue_proxies() if catalogue else {}
    groups = [(None, [s for s in sites if s[0] not in countries])]
    for cc in sorted(set(countries.values())):
        groups.append((cc, [s for s in sites if countries.get(s[0]) == cc]))
    batches = [(cc, g[b:b + batch]) for cc, g in groups for b in range(0, len(g), batch)]
    with sync_playwright() as p:
        for i, (cc, group) in enumerate(batches):
            if catalogue and time.monotonic() - START > CATALOGUE_BUDGET:
                for _, rest in batches[i:]:
                    for sid, name, _ in rest:
                        result["sites"][sid] = {"name": name, "ok": False, "items": [], "error": "not reached this run (time budget)"}
                break
            prof = profile + (f"-{cc}" if cc else "")
            kw = {"proxy": resi.playwright(cc, session=resi.session_id(), lifetime="30m")} if cc else {}
            ctx = chrome_state.launch(p, prof, channel="chrome", headless=False, no_viewport=True, **kw)
            flush = resi.count_browser(ctx, f"browser-{cc}") if cc else None
            try:
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                for sid, name, read in group:
                    try:
                        items = read(pg)
                        result["sites"][sid] = {"name": name, "ok": bool(items), "items": items,
                                                "error": None if items else "no articles (challenge not passed?)"}
                    except Exception as e:
                        if cc:
                            try:
                                resi.check_browser(e)  # IPRoyal out of traffic: a pause, not the site's failure
                            except resi.ProxyOut as out:
                                result["sites"][sid] = {"name": name, "ok": None, "items": [], "off": resi.STATUS,
                                                        "error": repr(out)[:200]}
                                pg = fresh_tab(ctx, pg)
                                continue
                        result["sites"][sid] = {"name": name, "ok": False, "items": [], "error": repr(e)[:200]}
                        pg = fresh_tab(ctx, pg)
                    print(f"{name}: {len(result['sites'][sid]['items'])} articles", flush=True)
            finally:
                chrome_state.close(ctx)
                if flush:
                    flush()
            if catalogue:  # what is read so far is kept: a later batch's crash loses only its own sites
                corpus.file_put(CATALOGUE_DOC, dict(result, partial=True))
    corpus.file_put(CATALOGUE_DOC if catalogue else "browser.json", result)


if __name__ == "__main__":
    main(catalogue=sys.argv[1:] == ["catalogue"])
