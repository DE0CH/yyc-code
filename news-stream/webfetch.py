"""YYC AI's web_fetch, served from YYC's own capture (Deyao, 2026-10-27: "when running web fetch with a url from a
source we already capture / know how to capture, it should either hit cache or run our custom full page and content
capture, it runs on the cluster"; "the returned result should label its special kind").

The tools role of YYC AI (DE0CH/jarvis3 chat/src/tools/builtin.ts web_fetch) asks here first, unless the model asked
for the page unmodified (`original`). POST /internal/web_fetch {url} on the AI server (web.py), bearer WEB_FETCH_TOKEN,
reached inside the cluster only (Service web.yyc; the public site never routes /internal). Answers, by `kind`:

  cache    the corpus already has this article (follow.have: its URL or url_key) with its text: the stored text, the
           post's details page, when it was captured
  capture  a site a source of ours reads (follow.outlet_for) whose posts the pipeline reads from their URL
           (follow.readable): the page read now by the news stream's own capture (cache_fetch.fetch_one: per-site
           extractors, the proxies for sites that refuse the cluster, Reuters' solved session, PDFs), kept in table
           web_fetch_pages for FRESH so the next fetch of the URL is a cache hit
  none     not ours to read (an unknown site, a forum or social post, a site only a real browser opens): the caller
           fetches it as before; `why` says which
"""
import hashlib, os, time
from datetime import datetime, timedelta

import corpus

TEXT_MIN = 200                 # letters: a stored text shorter than this is a headline or an excerpt, not the article
FRESH = timedelta(hours=6)     # a page captured here is served again for this long
SCHEMA = """CREATE TABLE IF NOT EXISTS web_fetch_pages (
  key TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT, text TEXT NOT NULL, published TEXT, author TEXT, source TEXT,
  via TEXT, at TIMESTAMPTZ NOT NULL DEFAULT now())"""
_schema = {"done": False}


def _key(url):
    return hashlib.sha256((corpus.url_key(url) or url).encode()).hexdigest()


def _ensure():
    if not _schema["done"]:
        corpus.db().execute(SCHEMA)
        _schema["done"] = True


def cached(url):
    """the corpus's copy of this article with its text → dict or None"""
    import dashboard_url, follow
    pid = follow.have(url)
    if not pid:
        return None
    r = corpus.db().execute("""SELECT id, url, title, text, author, website, published, seen, cached_at, text_via
                               FROM posts WHERE id = %s""", (pid,)).fetchone()
    if not r or len((r["text"] or "").strip()) < TEXT_MIN:
        return None
    return {"kind": "cache", "url": r["url"], "title": r["title"], "text": r["text"], "author": r["author"],
            "source": r["website"], "published": r["published"], "captured_at": r["cached_at"] or r["seen"],
            "excerpt": r["text_via"] == "excerpt", "post_id": r["id"], "dashboard_url": dashboard_url.post_url(r["id"])}


def recent(url):
    """this URL captured here within FRESH → dict or None"""
    _ensure()
    r = corpus.db().execute("SELECT * FROM web_fetch_pages WHERE key = %s AND at > now() - %s",
                            (_key(url), FRESH)).fetchone()
    if not r:
        return None
    return {"kind": "cache", "url": r["url"], "title": r["title"], "text": r["text"], "author": r["author"],
            "source": r["source"], "published": r["published"], "captured_at": r["at"].isoformat(timespec="seconds"),
            "excerpt": r["via"] == "excerpt"}


def capture(url):
    """read the page with the news stream's own capture → dict (kind capture, or none with why)"""
    import cache_fetch, follow
    o = follow.outlet_for(url)
    if not o:
        return {"kind": "none", "why": "no source of YYC's reads this site"}
    sid, name = o
    if why := follow.readable(sid, name, url):
        return {"kind": "none", "why": why}
    meta = {}
    try:
        text, author, _eng = cache_fetch.fetch_one(url, meta)
    except cache_fetch.Challenge:
        return {"kind": "none", "why": "the site opens only in a real browser"}
    except Exception as e:  # noqa: BLE001: the caller reads the page itself
        return {"kind": "none", "why": f"YYC's capture failed: {repr(e)[:200]}"}
    if len((text or "").strip()) < TEXT_MIN:
        return {"kind": "none", "why": "YYC's capture found no article text"}
    now = datetime.now(corpus.HKT)
    out = {"kind": "capture", "url": url, "title": meta.get("title"), "text": text, "author": author, "source": name,
           "published": meta.get("published"), "captured_at": now.isoformat(timespec="seconds"),
           "excerpt": meta.get("via") == "excerpt"}
    _ensure()
    corpus.db().execute("""INSERT INTO web_fetch_pages (key, url, title, text, published, author, source, via, at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                           ON CONFLICT (key) DO UPDATE SET url = excluded.url, title = excluded.title, text = excluded.text,
                             published = excluded.published, author = excluded.author, source = excluded.source,
                             via = excluded.via, at = excluded.at""",
                        (_key(url), url, out["title"], text, out["published"], author, name, meta.get("via")))
    return out


def web_fetch(url):
    """the answer for POST /internal/web_fetch: the corpus's copy, this server's recent capture, a capture now, or none"""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValueError("url must be a full http(s) URL")
    t = time.monotonic()
    out = cached(url) or recent(url) or capture(url)
    out["ms"] = round((time.monotonic() - t) * 1000)
    return out


def token_ok(header):
    """the bearer token of POST /internal/web_fetch (WEB_FETCH_TOKEN; unset = the route is off)"""
    want = os.environ.get("WEB_FETCH_TOKEN", "")
    got = (header or "").removeprefix("Bearer ").strip()
    return bool(want) and hashlib.sha256(got.encode()).digest() == hashlib.sha256(want.encode()).digest()
