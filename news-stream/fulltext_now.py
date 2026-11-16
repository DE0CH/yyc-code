#!/usr/bin/env python3
"""Full text on sight (Deyao, 2026-10-14): each new article's page is read as soon as the poll ingests it, so the
scorer judges the whole story and the alert goes out on that final score. A thread pool of the poller (app.py).

  - What: every new post of a news-like type (fulltext_fetch.SOURCE_TYPES, or of fulltext_fetch.PAGE_SIDS) whose feed text is short (under
    FULL_CHARS) or whose listing gave no publish time or only a day (the time the page states fills it), not a comment, and not on a host only the 30-minute `fulltext` job can read (the paid proxy's
    信報 / 香港討論區, the ISP IP's hosts (cache_fetch.ISP_HOSTS), the browser-pass and render hosts, social platforms): those keep that job. The scorer waits
    for the page either way (streams.due, WAIT_TEXT at most); only hosts never read (cache_fetch.SKIP_HOSTS) are
    scored on their feed text at once.
  - How: the same extractors as fulltext_fetch.py (cache_fetch.fetch_one; the page's own title for slug-titled
    sources) and the same save (corpus.set_text sets cached_at, so the scorer knows the page was tried); one
    request at a time per host, HOST_GAP apart, cache_fetch.RATE overall.
  - Failures: a network error is left for the 30-minute job to retry (the scorer's wait ends anyway); a page that
    answers but has nothing is saved as "" (tried).
"""
import queue, threading, time

import cache_fetch, canonical, corpus, fulltext_fetch, media, paywall, resi

THREADS = 6
FULL_CHARS = fulltext_fetch.FULL_CHARS  # feed text this long already is the article (some RSS feeds carry it whole)
HOST_GAP = fulltext_fetch.HOST_GAP
SKIP = cache_fetch.PROXY_HOSTS + cache_fetch.ISP_HOSTS + cache_fetch.BROWSER_HOSTS + cache_fetch.SKIP_HOSTS
STOP = threading.Event()  # the poller's drain: each worker finishes the page in hand
Q = queue.Queue()
_hosts, _lock = {}, threading.Lock()  # host → when its next request may go


def wanted(p):
    if p.get("thread") or not fulltext_fetch.page_typed(p):  # news-like, PAGE_SIDS, a forum page that is no thread
        return False
    if p.get("cached_at") is not None:
        return False
    if len((p.get("text") or "").strip()) >= FULL_CHARS and p.get("published") is not None and not (
            p.get("date_only") and not cache_fetch.DAY_ONLY_PAGES.search(p["url"])):
        return False  # the feed carried the article; an undated or date-only post's page is read for its time anyway
    return not cache_fetch.host(p["url"]).endswith(SKIP)


def enqueue(post_ids):
    """called after every ingest with the new posts' ids"""
    if not post_ids:
        return
    rows = corpus.db().execute(f"""SELECT id, url, sid, thread, source_type, text, cached_at, published, date_only FROM posts
                                   WHERE id = ANY(%s) AND {corpus.seen_cond("posts", "fulltext")}""",  # never a backlog post
                               (list(post_ids),)).fetchall()
    for r in rows:
        if wanted(dict(r)):
            Q.put((r["id"], r["url"], r["sid"]))


def _slot(h):
    """wait for this site's turn (one request at a time per site, HOST_GAP apart)"""
    while True:
        with _lock:
            at = _hosts.get(h, 0)
            now = time.monotonic()
            if at <= now:
                _hosts[h] = now + HOST_GAP
                return
        time.sleep(min(at - now, HOST_GAP))


def fetch(pid, url, sid):
    _slot(cache_fetch.site(url))  # a site's subdomains share one rate limit (HKET)
    try:
        title, meta = None, {}
        if sid in fulltext_fetch.SLUG_SIDS and not cache_fetch.host(url).endswith(cache_fetch.SOLVER_HOSTS):
            page = cache_fetch.get(url)
            text, title = cache_fetch.article_text(page), fulltext_fetch.page_title(page)
            meta["published"] = cache_fetch.page_date(page)
        else:  # Reuters (slug-titled too): its reader gives the title with the text (paywall.reuters)
            text, _, _ = cache_fetch.fetch_one(url, meta=meta)
            title = meta.get("title") if sid in fulltext_fetch.SLUG_SIDS else None
        page = cache_fetch.last_page()  # the post's own page (an aggregator's original is read in save)
        fulltext_fetch.save(pid, text, title, meta.get("published"), meta.get("via", "page"), meta.get("original"), url)
        cache_fetch._seen.page = page
        media.seen_page(pid, url, cache_fetch.last_page())  # the page's video and audio, from the same HTML
        canonical.save(pid, url, cache_fetch.last_page())  # the address the page states (開啟原文 opens it)
        return bool(text)
    except cache_fetch.Challenge:
        return False  # the 30-minute job's browser pass reads it
    except fulltext_fetch.TRANSIENT:
        return False  # retried by the 30-minute job
    except resi.ProxyOut:
        return False  # IPRoyal out of traffic (Reuters, Bloomberg's residential retry): a pause, the 30-minute job reads it
    except Exception as e:
        fulltext_fetch.save(pid, "", via=paywall.failed_via(url), failed=True, reason=f"{type(e).__name__}: {e}")
        print("fulltext now failed:", url[:120], repr(e)[:120], flush=True)
        return False


def worker():
    while not STOP.is_set():
        try:
            pid, url, sid = Q.get(timeout=1)
        except queue.Empty:
            continue
        try:
            fetch(pid, url, sid)
        except Exception as e:
            print("fulltext now:", repr(e)[:200], flush=True)


def start():
    ts = [threading.Thread(target=worker, daemon=True) for _ in range(THREADS)]
    for t in ts:
        t.start()
    return ts
