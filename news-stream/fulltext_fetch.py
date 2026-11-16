#!/usr/bin/env python3
"""Full text for search (the 搜尋 section, WisersOne's WiseSearch): the article text of EVERY news post in the corpus
that has none yet, not only the posts a profile tagged relevant (that is cache_fetch.py). Newest first, so today's
articles are searchable within the hour; older rows (the archive backfill inserts them without text) follow as runs
have time left. The text is kept for internal search only: pages show the headline, a short extract and the link.

    python3 fulltext_fetch.py               one run (at most MAX_PAGES pages or DEADLINE seconds)
    python3 fulltext_fetch.py stats         rows with and without text, per website (no fetching)
    python3 fulltext_fetch.py refill [SINCE] [--sids A,B] [--only] [--dry]  read again the posts seen since SINCE (default
                                            the stage's start point, corpus.cutoff) that kept no article text (refill())
    python3 fulltext_fetch.py retry HOST [DAYS] a host's pages of the last DAYS (3) days read with nothing kept: due again
    python3 fulltext_fetch.py dates [N]     publish times for undated (or future-dated) posts from their pages
    python3 fulltext_fetch.py date-only [N] mark posts stored at a midnight that was only a day, then their pages' times
    python3 fulltext_fetch.py <url>…        what the extractor gets (test; writes nothing)

Run by the CronJob `fulltext` every 30 minutes. Per page it uses cache_fetch.fetch_one (the same extractors: ld+json
articleBody or the largest paragraph cluster, 香港01's page data, 商業電台's VueApp data, 橙新聞's API, LIHKG's API).
Resumable: every page tried gets cached_at (text "" when nothing came back), so a run never repeats a page; a page
that failed with a network error or HTTP 429/5xx is left for a later run, at most TRIES times (kv fulltext:tries).
Polite: one request at a time per site (cache_fetch.site: HKET's subdomains are one), HOST_GAP seconds between them, at most RATE requests a second overall,
sources.UA. Hosts that refuse the cluster's IP go through RESI_PROXY like their listings (信報, 香港討論區), on YYC's
IPRoyal sub-user, read like every other host: no daily cap, every date (Deyao, 2026-10-14: budgets are safety stops);
kv fulltext:proxy:<day> counts the proxied pages read, for the monitor.
Sites behind Cloudflare's managed challenge (成報, 亞洲週刊, 明報月刊) get one browser pass after the page reads (at most
BROWSER_PAGES pages; run under xvfb-run).
HKET's JavaScript-only sites (invest.hket.com, ps.hket.com) are read through their content API (cache_fetch.HKET_API).
Time: the page reads stop at DEADLINE; the browser pass opens no page after RUN_BUDGET - cache_fetch.BROWSER_PAGE_MAX,
so a run ends inside RUN_BUDGET whatever its backlog (the CronJob kills it at activeDeadlineSeconds 1740). Pages a
run did not reach stay untried for the next run. The browser pass runs in batches of BROWSER_BATCH pages, a fresh
Chrome each and saved after each (one Chrome over many pages outgrew the pod's memory, /dev/shm included; limit 2560Mi).
Each page read also gives the address the page states for itself (canonical.py: <link rel=canonical> / og:url), kept
in posts.canonical_url when it differs from the post's url: what the site's 開啟原文 opens.
Pages of sites that complete a story after first publishing its headline (GROWING_HOSTS: 商業電台, RTHK) read short
are read again by later runs (reread()). NOW's Telegram posts and RTHK's YouTube uploads (PAGE_SIDS) are read too.
Titles that a listing could only make from the URL (sources_extra.SLUG_TITLES: 巴士的報) are replaced by the page's
own og:title / <title>, and so is any title with no word in it (a sitemap slug that is a thread id, titles.PLACEHOLDER).
"""
import json, os, re, sys, threading, time, urllib.error
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import cache_fetch
import canonical, corpus, media, paywall, resi
from cache_fetch import host

MAX_PAGES = int(os.environ.get("FULLTEXT_MAX_PAGES", 4000))
DEADLINE = int(os.environ.get("FULLTEXT_DEADLINE", 1000))   # seconds of page reads a run may take (the passes follow)
RUN_BUDGET = 1560      # seconds a whole run may take: the job's activeDeadlineSeconds 1740 less pod start, Chrome start/stop
RATE = 8               # requests a second, all hosts together
HOST_GAP = 1.0         # seconds between two requests to one host
THREADS = 16           # hosts read at once
TRIES = 3              # network errors before a page is given up
BROWSER_PAGES = 150    # at most; the time left in RUN_BUDGET decides how many are read
BROWSER_BATCH = 10     # pages per Chrome: a fresh browser per batch keeps memory flat (one renderer grew past 600 MB in 25)
SOURCE_TYPES = ("News", "Newspaper", "Magazine", "Official", "Blog")
# sources of another type whose post links its story's own page (2026-10-20): NOW's Telegram channel (each message a
# headline and a link to the story's telegra.ph copy or its news.now.com page) and RTHK's YouTube uploads (the
# description is the video's written text)
PAGE_SIDS = ("now_telegram", "rthk_youtube")
# sites that publish a story as its headline or first lines and complete the same page later (2026-10-20: 商業電台
# 2655518 20 characters at 09:42, 731 by 17:00; 2655558 99 → 709; RTHK 1874609 its title at 14:23, 583 characters
# later): a page read with under FULL_CHARS is read again REREAD_AFTER after its last read, at most REREADS times
# (商業電台's traffic notes stay one line: three reads each, about 40 a day)
GROWING_HOSTS = ("881903.com", "news.rthk.hk")
REREAD_AFTER = timedelta(hours=1)
REREAD_WITHIN = timedelta(days=1)  # a story still short a day after it was first read is the story
REREADS = 3
FULL_CHARS = 500       # feed text this long already is the article; a shorter one (an RSS summary) gets the page read
SLUG_SIDS = ("bastillepost", "hkbusiness", "reuters", "youngpost_sitemap", "metrohk_sitemap", "scmp_photos")  # = sources_extra.SLUG_TITLES (not imported: that pulls in every source module)
TRANSIENT = (TimeoutError, ConnectionError, urllib.error.URLError)


SKIP_RE = "(^|\\.)(" + "|".join(re.escape(h) for h in cache_fetch.SKIP_HOSTS) + ")$"  # never read: left out in SQL


def forum_page(x):
    """a Forum-type post that is not a thread (2026-10-20: ulifestyle topic pages, getjetso deals, asiaxpat jobs): its
    page is read like a news post's; a thread's opening post comes from its collector (twopass.UNIT_URLS)"""
    import twopass
    return x.get("source_type") == "Forum" and not any(re.search(u, x.get("url") or "") for u in twopass.UNIT_URLS)


def page_typed(x):
    """a post whose text this job reads from its page: news-like types, PAGE_SIDS, forum pages that are not threads"""
    return x.get("source_type") in SOURCE_TYPES or x.get("sid") in PAGE_SIDS or forum_page(x)


def typed_sql(alias=""):
    """page_typed() in SQL (its arguments: types(), in order)"""
    a = alias + "." if alias else ""
    return f"({a}source_type = ANY(%s) OR {a}sid = ANY(%s) OR ({a}source_type = 'Forum' AND NOT ({a}url ~ ANY(%s))))"


def types():
    import twopass
    return [list(SOURCE_TYPES), list(PAGE_SIDS), list(twopass.UNIT_URLS)]


def will_read(x):
    """True when a run of this job will read the post's page for its text (the rule of candidates(): a news, official
    or blog post that is not a comment, never tried, with no text or only a feed summary, on a host it reads). The item
    page says 「全文尚未收集（排隊中）」 for it; a social post's text comes from its collector and is never read here, except
    a YouTube video's description when its collector didn't get it (sources.youtube_due: each run reads those)."""
    import sources
    if not corpus.in_stage(x, "fulltext"):  # the stage's backlog: never read (corpus.seen_cond)
        return False
    if sources.youtube_due(x):
        return True
    return (x.get("cached_at") is None and not x.get("thread") and page_typed(x)
            and len((x.get("text") or "").strip()) < FULL_CHARS and not (x.get("sid") or "").startswith("tikhub_")
            and not host(x.get("url") or "").endswith(cache_fetch.SKIP_HOSTS))


def candidates(limit):
    """posts with no text or only a feed summary (under FULL_CHARS), or no publish time or only a day (the page states
    it), never tried, newest day first"""
    rows = corpus.db().execute(
        f"""SELECT id, url, sid, day FROM posts WHERE (text IS NULL OR length(btrim(text)) < {FULL_CHARS}
                                                      OR published IS NULL
                                                      OR (date_only AND url !~ '{cache_fetch.DAY_ONLY_PAGES.pattern}'))
           AND cached_at IS NULL AND thread IS NULL AND substring(url from '://([^/:?#]+)') !~ %s
           AND {typed_sql()} AND {corpus.seen_cond("posts", "fulltext")}
           ORDER BY day DESC, id DESC LIMIT %s""",
        (SKIP_RE, *types(), limit * 3)).fetchall()
    out = []
    for r in rows:
        if host(r["url"]).endswith(cache_fetch.SKIP_HOSTS) or (r["sid"] or "").startswith("tikhub_"):
            continue
        out.append((r["id"], r["url"], r["sid"], r["day"]))
    return out[:limit]


def refill(since=None, sids=(), dry=False, only=False):
    """read again every news post seen since `since` (default the stage's start point, corpus.cutoff("fulltext")) whose page read kept no article
    text: missing, only the title again (titles.title_only), or under FULL_CHARS; plus every post of `sids` (junk
    longer than that, e.g. 晴報's template text); only=True: the posts of `sids` alone. Hosts never read are left out. The job's own run() reads them (its
    routes, pacing, browser pass); the scorer scores each changed post again (streams.HASH). → posts read"""
    import titles
    since = since or corpus.cutoff("fulltext")
    rows = corpus.db().execute(
        f"""SELECT id, url, sid, day, title, left(coalesce(text, ''), length(coalesce(title, '')) + 200) AS head,
                  length(btrim(coalesce(text, ''))) AS n FROM posts
           WHERE seen::timestamptz >= %s::timestamptz AND {corpus.seen_cond("posts", "fulltext")}  -- never the stage's backlog
             AND thread IS NULL AND {typed_sql()}
             AND substring(url from '://([^/:?#]+)') !~ %s ORDER BY day DESC, id DESC""",
        (since, *types(), SKIP_RE)).fetchall()
    todo = [(r["id"], r["url"], r["sid"], r["day"]) for r in rows
            if not (r["sid"] or "").startswith("tikhub_") and (not only or r["sid"] in sids)
            and (r["sid"] in sids or r["n"] < FULL_CHARS or titles.title_only(r["head"], r["title"]))]
    print(f"refill: {len(todo)} posts since {since}", flush=True)
    if dry or not todo:
        return len(todo)
    ids = [t[0] for t in todo]
    corpus.db().execute("UPDATE posts SET cached_at = NULL WHERE id = ANY(%s)", (ids,))
    with corpus.lock(REFILL_KV):
        q = corpus.kv_get(REFILL_KV) or []
        have = set(q)
        corpus.kv_set(REFILL_KV, q + [i for i in ids if i not in have])
    chunk = refill_take(REFILL_CHUNK)
    run(chunk, refill_ids=[t[0] for t in chunk])  # one chunk now; each fulltext CronJob run reads the next ones
    return len(todo)


REFILL_KV = "fulltext:refill"  # the refill queue: post ids still to read, oldest request first (checkpointed in kv)
REFILL_CHUNK = int(os.environ.get("FULLTEXT_REFILL_CHUNK", 500))  # queued posts a run takes beside its candidates


def refill_take(n):
    """the next n queued posts → [(id, url, sid, day)] (ids no longer in posts are dropped from the queue)"""
    q = corpus.kv_get(REFILL_KV) or []
    if not q or n <= 0:
        return []
    rows = {r["id"]: r for r in corpus.db().execute("SELECT id, url, sid, day FROM posts WHERE id = ANY(%s)", (q[:n],))}
    return [(i, rows[i]["url"], rows[i]["sid"], rows[i]["day"]) for i in q[:n] if i in rows]


def refill_settle(ids):
    """drop from the queue the posts this run tried (cached_at set again: set_text on every tried page) and the ones
    gone; the rest (past the run's deadline, a proxy pause) stay for the next run"""
    if not ids:
        return
    left = {r[0] for r in corpus.db().execute("SELECT id FROM posts WHERE id = ANY(%s) AND cached_at IS NULL", (ids,))}
    done = set(ids) - left
    with corpus.lock(REFILL_KV):
        q = corpus.kv_get(REFILL_KV) or []
        corpus.kv_set(REFILL_KV, [i for i in q if i not in done])


def retry(h, days=3):
    """the pages of host h (and its subdomains) from the last `days` days that were read with nothing kept are due
    again (an extractor fixed after they were tried: ps.hket.com before its content API was read) → posts made due"""
    since = (datetime.now(corpus.HKT) - timedelta(days=days)).strftime("%Y-%m-%d")
    r = corpus.db().execute(
        f"""UPDATE posts SET cached_at = NULL WHERE thread IS NULL AND day >= %s AND cached_at IS NOT NULL
           AND {corpus.seen_cond("posts", "fulltext")} AND coalesce(length(btrim(text)), 0) < %s AND substring(url from '://([^/:?#]+)') ~ %s""",
        (since, FULL_CHARS, r"(^|\.)" + re.escape(h) + "$"))
    return r.rowcount


def reread():
    """the pages of GROWING_HOSTS read with under FULL_CHARS, last read REREAD_AFTER ago or more, of posts first
    seen within REREAD_WITHIN, are due again, each at most REREADS times (kv fulltext:reread:<id>) → posts made due"""
    now = datetime.now(corpus.HKT)
    rows = corpus.db().execute(
        f"""SELECT id FROM posts WHERE thread IS NULL AND cached_at IS NOT NULL AND cached_at::timestamptz <= %s
           AND seen::timestamptz >= %s AND coalesce(length(btrim(text)), 0) < %s AND {corpus.seen_cond("posts", "fulltext")}
           AND substring(url from '://([^/:?#]+)') ~ %s""",
        (now - REREAD_AFTER, now - REREAD_WITHIN, FULL_CHARS,
         "(^|\\.)(" + "|".join(re.escape(h) for h in GROWING_HOSTS) + ")$")).fetchall()
    n = 0
    for r in rows:
        if int(corpus.kv_get(f"fulltext:reread:{r['id']}", 0) or 0) >= REREADS:
            continue
        corpus.kv_add(f"fulltext:reread:{r['id']}", 1)
        corpus.db().execute("UPDATE posts SET cached_at = NULL WHERE id=%s", (r["id"],))
        n += 1
    return n


def page_title(page, url=None):
    """the page's og:title / <title>; "" when that is only its site's name (巴士的報's video pages:
    og:title 「巴士的報」), so the slug title stays (Deyao, 2026-10-20: never a site's name for a title)"""
    import html, lxml.html
    import links
    try:
        doc = lxml.html.fromstring(page)
    except Exception:
        return ""
    names = links.site_names_of(doc)
    for xp in ('//meta[@property="og:title"]/@content', "//title/text()"):
        got = doc.xpath(xp)
        if got and got[0].strip():
            t = re.sub(r"\s*[|｜\-–]\s*(巴士的報|Bastille Post|Hong Kong Business|Media OutReach Newswire).*$", "",
                       html.unescape(got[0].strip()))
            t = re.sub(r"(\s+#[^\s#]+)+$", "", t)  # trailing hashtags (Media OutReach's og:title: 「… #IOMed #國際調解院」)
            if t and not corpus.site_named(t, *names, url=url):
                return t
    return ""


PAGE_VIAS = ("page", "browser", "original", "page_data", "solver", "archive")  # texts an earlier page read kept


def original_text(pid, url, original):
    """an aggregator post's original (cache_fetch.AGGREGATOR_HOSTS): followed into the corpus (follow.pointer: a
    duplicate, or a new post of its outlet read at once), and its text: the corpus post's, else a read of its page here
    (an outlet no source reads, or one read later by the 30-minute job) → (text, the original's URL); ("", url) when
    neither gives a text"""
    import follow, titles
    final, tid = original, None
    try:
        _, tid, final = follow.pointer(pid, url, original)
    except Exception as e:  # noqa: BLE001: the original is still read below
        print("fulltext: following", original[:100], repr(e)[:160], flush=True)
    if tid:
        r = corpus.db().execute("SELECT title, text FROM posts WHERE id=%s", (tid,)).fetchone()
        if r and (r["text"] or "").strip() and not titles.title_only(r["text"], r["title"]):
            return r["text"], final
    final = cache_fetch.ORIGINAL_FIX.sub(r"\1/home/", final)  # a link recorded before the fix (NOW's /mobile/ 404s)
    try:
        return cache_fetch.fetch_one(final)[0], final
    except Exception as e:  # noqa: BLE001: the aggregator's own copy stays
        print("fulltext: original", final[:100], repr(e)[:160], flush=True)
        return "", final


ROUTE_OF_VIA = {"browser": "browser", "solver": "captcha solver", "archive": "archive.today"}


def note(pid, ok, text="", via=None, reason=None, kept=False, route=None):
    """the live dashboard's Full text panel (events.py): a page read with its outcome"""
    import events
    t = (text or "").strip()
    events.emit("fulltext", pid, ok=ok, chars=len(t), head=t[:700] or None, via=via, kept=kept or None,
                route=route or ROUTE_OF_VIA.get(via or "") or cache_fetch.last_route() or "direct",
                reason=None if ok else (reason or "the page had no article text"))


def save(pid, text, title=None, published=None, via="page", original=None, url=None, failed=False, reason=None):
    """the page read: its text (the feed's own text when that is longer), its title for slug-titled sources, and,
    for a post with no publish time or only a day, the time the page states, else a dated post's of the same article.
    via: where the text came from (paywall.VIA): the feed's when its text is kept; 'excerpt' stays 'excerpt' (a
    paywalled page: the archive job looks for the rest). original: an aggregator's page's link to the story it copies
    (cache_fetch.original_link; url = the post's own): the original's text when it has one (via 'original', its URL
    the text's), else the aggregator's own copy. failed: the read failed (a 403, a parse error): a text the post has
    stays (2026-10-21: a refill's second read of a uwants thread got a 403 from one residential exit and wiped the text
    the first read had kept); only cached_at says it was tried"""
    import titles
    via_url = None
    route = ROUTE_OF_VIA.get(via or "") or cache_fetch.last_route()  # the post's own page's route (before an original's read)
    if failed:
        r = corpus.db().execute("SELECT title, text, text_via, text_via_url FROM posts WHERE id=%s", (pid,)).fetchone()
        if r and r["text_via"] in PAGE_VIAS and (r["text"] or "").strip() and not titles.title_only(r["text"], r["title"]):
            corpus.set_text(pid, r["text"], via=r["text_via"], via_url=r["text_via_url"])
            note(pid, False, via=via, reason=reason, kept=True, route=route)
            return  # (a feed's text goes the usual way below: kept, an excerpt for a paywalled outlet)
    if not title and (text or "").strip() and (page := cache_fetch.last_page()):
        # a post link following stored before its page was read (〔連結〕host: an outlet only this job reads, e.g. on the
        # ISP IP; Media OutReach 2026-10-21), or one whose listing gave a title with no word in it (a sitemap slug that
        # is a thread id: 香港高登's 「8099523」, titles.PLACEHOLDER), takes the page's own headline
        cur = corpus.db().execute("SELECT title, thread FROM posts WHERE id=%s", (pid,)).fetchone()
        if cur and not cur["thread"] and ((cur["title"] or "").startswith(corpus.LINK_TITLE)
                                          or titles.PLACEHOLDER.match((cur["title"] or "").strip())):
            title = page_title(page, url) or None
    if original:
        otext, ourl = original_text(pid, url or "", original)
        if otext.strip() and len(otext.strip()) >= len((text or "").strip()) * 0.8:
            text, via, via_url = otext, "original", ourl
    if title:
        corpus.db().execute("UPDATE posts SET title=%s WHERE id=%s", (title[:500], pid))
    text = (text or "").strip()[:cache_fetch.TEXT_MAX]
    r = corpus.db().execute("SELECT title, text, text_via, published, date_only FROM posts WHERE id=%s", (pid,)).fetchone()
    if r and titles.title_only(text, r["title"], title):
        text = ""  # only the headline again is no article text (Deyao, 2026-10-20: Dimsum Daily's 「全文」)
    # the feed's own text stays when longer; an earlier page read's text (junk a broken extractor kept) does not
    # (nor a stored line of page furniture: Quamnet's paid posts kept the copyright footer an old read had stored)
    if r and r["text_via"] not in ("page", "browser", "original") and len((r["text"] or "").strip()) > len(text) \
            and not titles.title_only(r["text"], r["title"]) and not cache_fetch.junk((r["text"] or "").strip()[:200]):
        text = r["text"]
        via = "excerpt" if via == "excerpt" else "feed"
    corpus.set_text(pid, text, via=via if text or via == "excerpt" else None,  # also recomputes hay + grams; sets cached_at
                    via_url=via_url if via == "original" else None)
    note(pid, bool(text) and not failed, text, via, reason if failed else None, kept=failed and bool(text), route=route)
    if r and (r["published"] is None or r["date_only"]):  # a page time replaces a day (corpus.set_published)
        corpus.set_published(pid, published or corpus.sibling_published(pid))


def tries(pid):
    return corpus.kv_add(f"fulltext:tries:{pid}", 1)


def plan(todo):
    """→ ({site: [(id, url, sid)]}, pages skipped): every candidate by site (cache_fetch.site: a site's subdomains share
    one rate limit, one queue); a proxied host's pages only when RESI_PROXY is set (they can't be read without it)"""
    by_site, skipped = defaultdict(list), 0
    for pid, url, sid, day in todo:
        if host(url).endswith(cache_fetch.PROXY_HOSTS) and not resi.configured():
            skipped += 1
            continue
        if host(url).endswith(cache_fetch.ISP_HOSTS) and not os.environ.get("ISP_PROXY"):  # the ISP IP's hosts likewise
            skipped += 1
            continue
        by_site[cache_fetch.site(url)].append((pid, url, sid))
    return by_site, skipped


def run(todo=None, refill_ids=None):
    """one run over candidates() and the next REFILL_CHUNK queued refill posts (or the given (id, url, sid, day)
    rows: refill's first chunk, refill_ids its queued ids); the queue is settled at the end, so a run that stops at its
    deadline leaves the rest queued"""
    try:
        return _run(todo, refill_ids)
    finally:
        if refill_ids is not None:
            refill_settle(refill_ids)
        elif todo is None and _taken["ids"]:
            refill_settle(_taken["ids"])


_taken = {"ids": []}


def _run(todo=None, refill_ids=None):
    start = time.monotonic()
    cache_fetch.RATE = RATE  # cache_fetch.pace() reads it on every request
    again = reread() if todo is None else 0
    from_candidates = todo is None
    _taken["ids"] = []
    if todo is None:
        todo = candidates(MAX_PAGES)
        queued = [t for t in refill_take(REFILL_CHUNK) if t[0] not in {x[0] for x in todo}]
        _taken["ids"] = [t[0] for t in queued] + [t[0] for t in todo if t[0] in set(corpus.kv_get(REFILL_KV) or [])]
        todo = todo + queued
    by_host, skipped_proxy = plan(todo)
    proxied, out = [0], [False]
    stats, per_site = defaultdict(int), defaultdict(lambda: [0, 0])
    browser, lock = [], threading.Lock()

    def host_run(items):
        last = 0.0
        for pid, url, sid in items:
            if time.monotonic() - start > DEADLINE:
                with lock:
                    stats["deadline"] += 1
                continue
            h = host(url)
            if h.endswith(cache_fetch.BROWSER_HOSTS) and challenged.get(h):
                browser.append((pid, url))
                continue
            if out[0] and h.endswith(cache_fetch.PROXY_HOSTS + cache_fetch.SOLVER_HOSTS):
                with lock:  # IPRoyal out of traffic: the proxied pages wait, untried, for a later run
                    stats["proxy_out_waiting"] += 1
                continue
            wait = last + HOST_GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()
            if h.endswith(cache_fetch.PROXY_HOSTS):
                with lock:
                    proxied[0] += 1
            try:
                title, meta = None, {}
                if sid in SLUG_SIDS and not h.endswith(cache_fetch.SOLVER_HOSTS):
                    page = cache_fetch.get(url)
                    text, title = cache_fetch.article_text(page), page_title(page, url)
                    meta["published"] = cache_fetch.page_date(page)
                else:  # Reuters (slug-titled too): its reader gives the title with the text (paywall.reuters)
                    text, _, _ = cache_fetch.fetch_one(url, meta=meta)
                    title = meta.get("title") if sid in SLUG_SIDS else None
                page = cache_fetch.last_page()  # the post's own page (an aggregator's original is read in save)
                save(pid, text, title, meta.get("published"), meta.get("via", "page"), meta.get("original"), url)
                cache_fetch._seen.page = page
                media.seen_page(pid, url, cache_fetch.last_page())  # the page's video and audio, from the same HTML
                canonical.save(pid, url, cache_fetch.last_page())  # the address the page states (開啟原文 opens it)
                with lock:
                    stats["ok" if text else "empty"] += 1
                    per_site[h][0 if text else 1] += 1
            except cache_fetch.Challenge:
                challenged[h] = True
                browser.append((pid, url))
            except resi.ProxyOut as e:  # a pause (resi.py): no try counted, the post stays a candidate
                out[0] = True
                with lock:
                    stats["proxy_out"] += 1
                print("proxy out of traffic, proxied pages wait:", url[:100], flush=True)
            except Exception as e:
                code = getattr(e, "code", None)
                transient = isinstance(e, TRANSIENT) and not (isinstance(e, urllib.error.HTTPError) and code not in (429, 500, 502, 503, 504))
                if transient and tries(pid) < TRIES:
                    with lock:
                        stats["retry_later"] += 1
                    continue
                save(pid, "", via=paywall.failed_via(url), failed=True, reason=f"{type(e).__name__}: {e}")  # a paywalled page: its feed summary is an excerpt
                with lock:
                    stats["failed"] += 1
                    per_site[h][1] += 1
                print("failed:", url[:120], repr(e)[:120], flush=True)

    yt = {}

    def youtube_run():  # YouTube video posts' descriptions (sources.youtube_fill_text), beside the page reads
        import sources
        try:
            yt["filled"] = sources.youtube_fill_text(DEADLINE)
        except Exception as e:  # noqa: BLE001: the posts stay due for the next run
            yt["error"] = repr(e)[:200]

    yt_thread = threading.Thread(target=youtube_run, daemon=True) if from_candidates else None
    if yt_thread:
        yt_thread.start()
    challenged = {}
    with ThreadPoolExecutor(THREADS) as ex:
        list(ex.map(host_run, sorted(by_host.values(), key=len, reverse=True)))
    if yt_thread:
        yt_thread.join(max(0.0, start + DEADLINE + 60 - time.monotonic()))
        stats["youtube_descriptions"] = yt.get("filled", yt.get("error", "running"))
    until = start + RUN_BUDGET - cache_fetch.BROWSER_PAGE_MAX  # no browser page opens after this
    part = browser[:BROWSER_PAGES]
    for b in range(0, len(part), BROWSER_BATCH):
        batch = part[b:b + BROWSER_BATCH]
        if time.monotonic() >= until:
            stats["browser_next_run"] += len(part) - b
            break
        dates = {}
        try:
            got = cache_fetch.browser_pass([u for _, u in batch], profile="fulltext-profile", until=until, dates=dates)
        except Exception as e:
            print("browser pass failed:", repr(e)[:200], flush=True)
            got = {}
        for pid, url in batch:
            if url in got:  # opened; a page the pass did not reach stays untried for the next run
                save(pid, got[url], published=dates.get(url), via="browser")
                stats["browser_ok" if got[url] else "browser_empty"] += 1
            else:
                stats["browser_next_run"] += 1
    rows = sorted(per_site.items(), key=lambda kv: -sum(kv[1]))
    key = "fulltext:proxy:" + datetime.now(corpus.HKT).strftime("%Y-%m-%d")  # one fulltext run at a time (the CronJob)
    proxy_today = int(corpus.kv_get(key, 0) or 0) + proxied[0]
    corpus.kv_set(key, proxy_today)
    print(f"fulltext: {len(todo)} candidates ({again} growing pages read again), {sum(map(len, by_host.values()))} planned, proxied {proxied[0]} "
          f"(today {proxy_today}){f', no RESI_PROXY: skipped {skipped_proxy}' if skipped_proxy else ''}, "
          f"{time.monotonic() - start:.0f} s: {dict(stats)}", flush=True)
    for h, (ok, bad) in rows[:40]:
        print(f"  {h:32} text {ok:5}  none {bad:5}", flush=True)


EPAPER_SIDS = ("tkp_epaper", "wwp_epaper")  # their sitemaps' "T00:00:00Z" (the day in UTC) was stored as 08:00 HKT


def mark_date_only():
    """existing posts whose stored time is a day read as midnight become date-only (Deyao, 2026-10-14): published
    exactly 00:00 HKT (the e-papers': 08:00 HKT, their UTC midnight) → date_only, on the moment first seen within that
    day (corpus.within_day, here in SQL). → rows changed"""
    r = corpus.db().execute(
        """WITH d AS (SELECT id, left(published, 10) AS day, seen FROM posts
                      WHERE thread IS NULL AND date_only IS NOT TRUE
                        AND (published LIKE '%%T00:00+08:00' OR (sid = ANY(%s) AND published LIKE '%%T08:00+08:00')))
           UPDATE posts p SET date_only = TRUE, published = to_char(
                 least(greatest(d.seen::timestamptz, (d.day || 'T00:00:00+08:00')::timestamptz),
                       (d.day || 'T23:59:00+08:00')::timestamptz) AT TIME ZONE 'Asia/Hong_Kong',
                 'YYYY-MM-DD"T"HH24:MI') || '+08:00'
           FROM d WHERE p.id = d.id""", (list(EPAPER_SIDS),))
    return r.rowcount


def backfill_date_only(limit=None):
    """mark_date_only(), then every date-only post whose page can state a time (not cache_fetch.DAY_ONLY_PAGES) gets
    the time its page states (backfill_dates) → counts"""
    marked = mark_date_only()
    print("date_only: marked", marked, flush=True)
    return dict(backfill_dates(limit, date_only=True), marked=marked)


def backfill_dates(limit=None, date_only=False):
    """existing posts with no publish time (or one after they were seen: a misread) get the time their page states,
    else a dated post's of the same article (Deyao, 2026-10-14, after the POL incident). Dates only: the text,
    cached_at, scores, stream items and Telegram are left alone (a post's score depends on its title and text). One
    request at a time per host, HOST_GAP apart; 信報/香港討論區 through RESI_PROXY; Cloudflare-challenged hosts in one
    browser pass. date_only=True: the posts that have only a day instead (a page time replaces the day; a page that
    states only a day, or none, leaves it). → counts"""
    future = f"published::timestamptz > seen::timestamptz + interval '{int(corpus.FUTURE.total_seconds())} seconds'"
    which = (f"date_only AND url !~ '{cache_fetch.DAY_ONLY_PAGES.pattern}'" if date_only
             else f"date_only IS NOT TRUE AND (published IS NULL OR {future})")
    rows = corpus.db().execute(
        f"""SELECT id, url, sid, published, seen, date_only FROM posts WHERE thread IS NULL AND source_type = ANY(%s)
            AND {which} ORDER BY id DESC""" + (" LIMIT %s" if limit else ""),
        (list(SOURCE_TYPES),) + ((limit,) if limit else ())).fetchall()
    stats, lock = defaultdict(int), threading.Lock()
    by_host, browser = defaultdict(list), []
    for r in rows:
        h = host(r["url"])
        if h.endswith(cache_fetch.SKIP_HOSTS) or (r["sid"] or "").startswith("tikhub_"):
            stats["no_page"] += 1
            continue
        if h.endswith(cache_fetch.PROXY_HOSTS) and not resi.configured():
            stats["no_proxy"] += 1
            continue
        (browser if h.endswith(cache_fetch.BROWSER_HOSTS) else by_host[h]).append(r)

    def done(r, published, how):
        published = published or corpus.sibling_published(r["id"])
        future = r["published"] is not None and not r["date_only"]
        if r["date_only"]:
            published = published if published and corpus.set_published(r["id"], published) else None
        elif published and not corpus.set_published(r["id"], published, replace=future) and \
                not corpus.BARE_DATE.fullmatch(published) and not corpus._plausible(published, r["seen"]):
            published = None  # the page's time is after the post was first seen: an update time, not usable
        if not published and future and not r["date_only"]:  # a wrong time and none to replace it: undated
            corpus.db().execute("UPDATE posts SET published=NULL, day=%s WHERE id=%s", (corpus.day_of(None, r["seen"]), r["id"]))
        with lock:
            stats["dated" if published else "none"] += 1
            stats[f"{r['sid']}:{'dated' if published else 'none'}"] += 1
            stats[how] += 1

    def host_run(items):
        last = 0.0
        for r in items:
            wait = last + HOST_GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()
            meta = {}
            try:
                if r["sid"] in SLUG_SIDS and not host(r["url"]).endswith(cache_fetch.SOLVER_HOSTS):
                    meta["published"] = cache_fetch.page_date(cache_fetch.get(r["url"]))
                else:
                    cache_fetch.fetch_one(r["url"], meta=meta)
            except cache_fetch.Challenge:
                browser.append(r)
                continue
            except resi.ProxyOut:  # a pause: the row stays unread for a later run
                with lock:
                    stats["proxy_out"] += 1
                continue
            except Exception as e:
                with lock:
                    stats["failed"] += 1
                print("dates:", r["url"][:100], repr(e)[:120], flush=True)
                done(r, None, "read_failed")
                continue
            done(r, meta.get("published"), "read")

    cache_fetch.RATE = RATE
    with ThreadPoolExecutor(THREADS) as ex:
        list(ex.map(host_run, sorted(by_host.values(), key=len, reverse=True)))
    if browser:
        dates = {}
        try:
            cache_fetch.browser_pass([r["url"] for r in browser], profile="fulltext-profile", dates=dates)
        except Exception as e:
            print("dates: browser pass failed:", repr(e)[:200], flush=True)
        for r in browser:
            done(r, dates.get(r["url"]), "browser")
    out = dict(stats, rows=len(rows))
    print("dates:", json.dumps(out, ensure_ascii=False, sort_keys=True), flush=True)
    return out


def report():
    rows = corpus.db().execute(
        """SELECT website, count(*) AS n, count(*) FILTER (WHERE length(text) > 0) AS with_text,
                  count(*) FILTER (WHERE text IS NULL AND cached_at IS NULL) AS waiting, min(day) AS first
           FROM posts WHERE thread IS NULL AND source_type = ANY(%s) GROUP BY 1 ORDER BY 2 DESC""",
        (list(SOURCE_TYPES),)).fetchall()
    print(f"{'website':34} {'posts':>7} {'text':>7} {'waiting':>7}  first day")
    for r in rows:
        print(f"{(r['website'] or '')[:34]:34} {r['n']:7} {r['with_text']:7} {r['waiting']:7}  {r['first']}")


if __name__ == "__main__":
    if sys.argv[1:] == ["stats"]:
        report()
    elif sys.argv[1:2] == ["date-only"]:  # python3 fulltext_fetch.py date-only [LIMIT]: backfill_date_only()
        backfill_date_only(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    elif sys.argv[1:2] == ["refill"]:  # python3 fulltext_fetch.py refill [SINCE] [--sids a,b] [--only] [--dry]: refill()
        a = sys.argv[2:]
        sids = a[a.index("--sids") + 1].split(",") if "--sids" in a else ()
        since = next((x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or a[i - 1] != "--sids")), None)
        refill(since, sids, dry="--dry" in a, only="--only" in a)
    elif sys.argv[1:2] == ["retry"]:  # python3 fulltext_fetch.py retry HOST [DAYS]: retry()
        print("due again:", retry(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3))
    elif sys.argv[1:2] == ["dates"]:  # python3 fulltext_fetch.py dates [LIMIT]: backfill_dates()
        backfill_dates(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    elif len(sys.argv) > 1:
        for u in sys.argv[1:]:
            try:
                t, _, _ = cache_fetch.fetch_one(u)
            except Exception as e:
                t = f"FAILED {e!r}"[:200]
            print(f"== {u}\nchars={len(t)}\n{t[:600]}\n")
    else:
        with corpus.stage("fulltext"):  # the CronJob's run; a restart starts from now (corpus.STAGES)
            run()
