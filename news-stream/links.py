#!/usr/bin/env python3
"""Posts that are only a link (Deyao, 2026-10-15). An X post by 伊凡投資心得 (HKIC, 14 Oct 17:45) reached 串流 with
the title "https://t.co/3DS8gH0wK7" and 「來源只提供標題」: it was an X Article, its text nothing but the link. The same
happens on every platform: Threads posts that are an am730 / The Standard link, Facebook posts that are a YouTube link,
an outlet's X post that is its article's URL. Such a post says nothing until what it links to is read.

What this does:
  - A post whose content (its text, else its title) is only a link, or a link and a few words ("詳情：https://…",
    fewer than MOSTLY characters besides links and punctuation), gets what the link points to appended to its text as
    one block 〔連結內容：<the linked page's title>〕<its description> (tikhub_fetch.link_block), so the scorer, the
    search and the item page read real content. The page's og:title / og:description (else <title> / meta
    description); a YouTube link by its oEmbed (title, channel) with the video queued for transcription (media.py);
    an X post's own link (an X Article, a t.co) by the tweet detail through TikHub (USD 0.001, metered), which gives
    the whole post again (tikhub_fetch.x_tweet).
  - Its title (titles.py, kind "linked"): the linked page's title, with no model call, marked 「連結標題」 on the site
    and 〔連結標題〕 on Telegram and in exports: a bare URL is never the title shown, and neither is a site's name
    (Deyao, 2026-10-20: headline() skips og:title / <title> that are only the site's name, corpus.site_named). An
    HKGolden thread (a JavaScript page) is read through HKGolden's API instead (hkgolden.py): the post takes the
    thread's title and first post.
  - Hashtags and @mentions are not words here: "https://… #晴報健康 #健康" is only a link.
  - The scorer holds a post whose content is only URLs (HELD_SQL, streams.due / backfill_due) until it is resolved;
    a thread here resolves new posts within seconds (the bounded wait). A link that can't be read is tried again
    BACKOFF later, TRIES times in all, then left (status failed): the post is then released to the scorer, whose
    near-empty gate (thin.py) rejects it: 「內容不足，未評分」, never scored, filed or sent.

Table link_scan: one row per post tried (tries, next_try, status pending | done | failed, error).

    python3 links.py status            posts due, held, resolved, failed
    python3 links.py once [N]          resolve up to N due posts now and print them
    python3 links.py fix-titles        correct every stored post titled with a URL (fix_titles) and print the counts
    python3 links.py fix-site-titles   correct every stored post titled with its site's name (fix_site_titles)
    python3 links.py backfill-x [DAYS] read every X post of the last DAYS days (default 30) again through the tweet
                                       detail: text with links expanded, X Article, quoted tweet, photos, videos, counts
"""
import html, json, re, sys, threading, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import corpus, hkgolden
import tikhub_fetch as th

HKT = corpus.HKT
BLOCK = th.LINK_OPEN  # 〔連結內容：
MOSTLY = 12           # a post with fewer characters than this besides links and punctuation is "only a link"
TRIES = 6
BACKOFF = (120, 600, 1800, 3600, 3 * 3600)  # seconds before the next try, after each failure
DAYS = 7              # posts this recent are resolved by the thread
WORKERS = 6
IDLE = 5              # seconds between the thread's rounds when nothing was due
TIMEOUT = 15
STOP = threading.Event()

URL = re.compile(r"https?://\S+")
JUNK = re.compile(r"[\s　.,:;!?…。，：；！？、「」『』【】()（）—|/#@~<>《》_*\-]")
# the same in Postgres (HELD_SQL / due): [[:space:]] for \s
PG_URL = r"https?://\S+"
PG_JUNK = "[[:space:]　.,:;!?…。，：；！？、「」『』【】()（）—|/#@~<>《》_*-]"


def content(p):
    return (p.get("text") or "").strip() or (p.get("title") or "")


TAG = re.compile(r"[#＃@＠][^\s#＃@＠，。：；！？、,:;!?「」『』【】()（）\[\]<>《》\"'“”]+")  # hashtags and @mentions


def _rest(text):
    """the words besides links, hashtags, @mentions and punctuation"""
    return JUNK.sub("", TAG.sub("", URL.sub("", text or "")))


def bare(text):
    """only URLs (and punctuation / whitespace)"""
    return bool(URL.search(text or "")) and not _rest(text)


def mostly_link(text):
    """a link and fewer than MOSTLY characters of words, and not yet resolved"""
    return bool(URL.search(text or "")) and BLOCK not in (text or "") and len(_rest(text)) < MOSTLY


PG_TAG = "[#＃@＠][^[:space:]#＃@＠，。：；！？、,:;!?「」『』【】()（）<>《》\"“”\\[\\]]+"


def _sql_rest(col):
    return (f"regexp_replace(regexp_replace(regexp_replace({col}, '{PG_URL}', '', 'g'), '{PG_TAG}', '', 'g'), "
            f"'{PG_JUNK}', '', 'g')")


_CONTENT = "coalesce(nullif(btrim({p}.text), ''), {p}.title, '')"


def held_sql(p="p"):
    """SQL: the post is held from scoring (a post, not a comment, whose content is only URLs, hashtags and @mentions),
    until it is resolved or links.py gives up on it (then the scorer's near-empty gate rejects it: thin.py)"""
    c = _CONTENT.format(p=p)
    return (f"({p}.thread IS NULL AND {c} ~ '{PG_URL}' AND {_sql_rest(c)} = '' AND NOT EXISTS "
            f"(SELECT 1 FROM link_scan ls WHERE ls.post_id = {p}.id AND ls.status = 'failed'))")


HELD_SQL = held_sql()


def linkish(title):
    """a title that says nothing but the link: empty, 〔連結〕host, a URL, or a few words around one"""
    t = (title or "").strip()
    return not t or t.startswith(corpus.LINK_TITLE) or bool(URL.search(t)) and len(_rest(t)) < MOSTLY \
        or len(_rest(t)) < 4


def linked_title(text):
    """the linked page's title when the post's own text (before the block) is only a link → str or None"""
    text = text or ""
    i = text.find(BLOCK)
    if i < 0 or not (bare(text[:i]) or mostly_link(text[:i])):
        return None
    j = text.find("〕", i)
    t = text[i + len(BLOCK):j].strip() if j > 0 else ""
    return t or None


# ---- reading what a link points to ----

def fetch_page(url):
    import cache_fetch
    h = cache_fetch.host(url)
    import resi
    proxy = resi.url("hk") if h.endswith(cache_fetch.PROXY_HOSTS) else None
    return cache_fetch.get(url, proxy=proxy)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-HK,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _meta(doc, *xps):
    for xp in xps:
        for v in doc.xpath(xp):
            v = re.sub(r"[ \t\r\n]+", " ", str(v)).strip()
            if v:
                return v
    return ""


FOUNDER_TITLE = re.compile(r"<founder-title>(.*?)</founder-title>", re.S | re.I)  # the e-paper CMS (澳門日報): in a comment
TITLE_BLOCKS = ("//body//*[self::div or self::h2 or self::h3 or self::span or self::p]"
                "[contains(concat(' ', normalize-space(@class), ' '), ' title ') or contains(@class, '-title')"
                " or contains(@class, '_title')][not(ancestor::nav or ancestor::footer or ancestor::aside)]")


def site_names_of(doc):
    """the names a page gives its site (og:site_name, application-name)"""
    return [re.sub(r"\s+", " ", str(v)).strip() for xp in ('//meta[@property="og:site_name"]/@content',
                                                          '//meta[@name="application-name"]/@content')
            for v in doc.xpath(xp)]


def headline(url, doc, page=""):
    """the page's own headline, never its site's name (Deyao, 2026-10-20): og:title / twitter:title / <title>, and
    when those are only the site's name (corpus.site_named against og:site_name, application-name, SITE_NAMES) the
    e-paper's founder-title (澳門日報), <h1>, a block of class "title" (華僑報) → "" when the page states only its site's
    name (a JavaScript shell: HKGolden's thread pages)"""
    names = site_names_of(doc)
    cands = [v for xp in ('//meta[@property="og:title"]/@content', '//meta[@name="twitter:title"]/@content',
                          '//title/text()') for v in doc.xpath(xp)[:1]]
    cands += [html.unescape(re.sub(r"<[^>]+>", "", m)) for m in FOUNDER_TITLE.findall(page or "")[:1]]
    cands += [e.text_content() for e in doc.xpath("//body//h1")[:2]]
    cands += [e.text_content() for e in doc.xpath(TITLE_BLOCKS)[:3]]
    for c in cands:
        t = re.sub(r"[ \t\r\n]+", " ", str(c)).strip()
        if t and not corpus.site_named(t, *names, url=url) and len(_rest(t)) >= 2:
            return t
    return ""


def parse_page(url, page):
    """a page's own title and description → {"url", "title", "desc"} (title "" when none: a page that states only its
    site's name states none)"""
    import lxml.html
    try:
        doc = lxml.html.fromstring(page)
    except Exception:  # noqa: BLE001
        return {"url": url, "title": "", "desc": ""}
    desc = _meta(doc, '//meta[@property="og:description"]/@content', '//meta[@name="description"]/@content',
                 '//meta[@name="twitter:description"]/@content')
    return {"url": url, "title": headline(url, doc, page), "desc": desc}


def page_info(url):
    """→ {"url", "title", "desc", "clips"} for a link (raises when it can't be read)"""
    import media
    vid = media.youtube_id(url)
    if vid:
        d = fetch_json("https://www.youtube.com/oembed?format=json&url=" +
                       urllib.parse.quote(f"https://www.youtube.com/watch?v={vid}", safe=""))
        return {"url": url, "title": (d.get("title") or "").strip(),
                "desc": f"YouTube · {d['author_name']}" if d.get("author_name") else "YouTube",
                "clips": [media.youtube_clip(vid)]}
    info = parse_page(url, fetch_page(url))
    info["clips"] = []
    return info


# ---- one post ----

X_STATUS = re.compile(r"^https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/status/(\d+)")
SOCIAL = re.compile(r"^https?://([^/]+\.)?(x\.com|twitter\.com|t\.co)/")


def x_reread(pid, url):
    """an X post read again through the tweet detail → its new text (stored with counts, photos, videos) or None"""
    m = X_STATUS.match(url or "")
    if not m:
        return None
    d = th.x_detail(m.group(1))
    if not d or d.get("text") is None:
        raise RuntimeError("tweet detail: no tweet")
    x = th.x_tweet(d)
    if not x["text"]:
        return None
    corpus.set_content(pid, x["title"], x["text"], x["engagement"] or None)
    corpus.attach_media(pid, x["images"], x["clips"])
    return x["text"]


def resolve(p):
    """read what the post links to and append it → the new text (raises when it can't be read)"""
    text = content(p)
    if X_STATUS.match(p.get("url") or "") and any(SOCIAL.match(u) for u in URL.findall(text)):
        text = x_reread(p["id"], p["url"]) or text  # the tweet itself says where t.co / x.com/i/article lead
        if not mostly_link(text):
            return text
    link = next((u for u in URL.findall(text) if not X_STATUS.match(u) and not SOCIAL.match(u)), None)
    if not link:
        raise RuntimeError("no link to read")
    if tid := hkgolden.thread_id(link):  # an HKGolden thread (its page is a JavaScript shell): HKGolden's own API
        return hkgolden_post(p["id"], tid, text)
    info = page_info(link.rstrip(".,)」）"))
    if not info["title"]:
        raise RuntimeError("the page states no title")
    new = text.rstrip() + "\n\n" + th.link_block(info["title"], info["desc"])
    # the post's title: the linked page's, when its own is only the link (〔連結〕host) or a few words around it
    own = corpus.db().execute("SELECT title FROM posts WHERE id=%s", (p["id"],)).fetchone()
    corpus.set_content(p["id"], info["title"] if linkish(own["title"] if own else "") else None, new)
    if info.get("clips"):
        corpus.attach_media(p["id"], None, info["clips"])
    return new


def hkgolden_post(pid, tid, text):
    """the post becomes the HKGolden thread it links to: its title, its first post as the text (the link-only text
    or a 〔連結內容〕 block of the site's name replaced; a longer real text kept), counts, author, publish time →
    the new text"""
    t = hkgolden.thread(tid)
    text = (text or "").strip()
    new = t["text"] or t["title"]
    if not (BLOCK in text or mostly_link(text) or len(new) > len(text)):
        new = text
    corpus.set_content(pid, t["title"], new, t["engagement"] or None)
    if t["author"]:
        corpus.db().execute("UPDATE posts SET author=%s WHERE id=%s AND author IS NULL", (t["author"], pid))
    if t["published"]:
        corpus.set_published(pid, t["published"])
    return new


def _now():
    return datetime.now(HKT)


OVERLAP = 500  # ids below the watermark looked at again (a transaction that took an id earlier may commit later)


def _round(limit=50):
    """→ (posts due, the highest post id looked at): posts newer than the watermark (kv links_upto, by primary key:
    the content regexes never run over the whole table) whose content is (mostly) a link and not resolved, of the last
    DAYS days, and the posts whose next try has come (link_scan)"""
    db = corpus.db()
    c = _CONTENT.format(p="p")
    now = _now()
    hi = db.execute("SELECT coalesce(max(id), 0) FROM posts").fetchone()[0]
    upto = (corpus.kv_get("links_upto") or {}).get("id")
    lo = max(0, upto - OVERLAP) if upto is not None else 0
    cols = "p.id, p.url, p.title, p.text, p.sid, p.platform, coalesce(p.published, p.seen) AS t"
    new = db.execute(f"""
        SELECT {cols} FROM posts p LEFT JOIN link_scan l ON l.post_id = p.id
        WHERE p.id > %s AND p.id <= %s AND l.post_id IS NULL AND p.thread IS NULL AND coalesce(p.published, p.seen) >= %s
          AND {corpus.seen_cond("p", "links")}
          AND {c} ~ '{PG_URL}' AND position(%s in {c}) = 0 AND char_length({_sql_rest(c)}) < %s""",
        (lo, hi, (now - timedelta(days=DAYS)).isoformat(timespec="seconds"), BLOCK, MOSTLY)).fetchall()
    again = db.execute(f"""
        SELECT {cols} FROM link_scan l JOIN posts p ON p.id = l.post_id
        WHERE l.status = 'pending' AND l.next_try <= %s AND position(%s in {c}) = 0 AND {corpus.seen_cond("p", "links")}""",
        (now.isoformat(timespec="seconds"), BLOCK)).fetchall()
    rows = {r["id"]: dict(r) for r in list(new) + list(again)}
    out = sorted(rows.values(), key=lambda r: (r["t"] or "", r["id"]), reverse=True)
    return out[:limit], (hi if len(out) <= limit else None)


def due(limit=50):
    """the posts the next round reads (see _round)"""
    return _round(limit)[0]


def _mark(pid, link, ok, err=None, counted=True):
    """counted=False: a pause (the proxy out of traffic): pending again after the first back-off, no try counted"""
    c = corpus.db()
    with c:
        r = c.execute("SELECT tries FROM link_scan WHERE post_id=%s", (pid,)).fetchone()
        tries = (r["tries"] if r else 0) + (0 if ok or not counted else 1)
        status = "done" if ok else ("failed" if tries >= TRIES else "pending")
        nxt = None if ok or status == "failed" else \
            (_now() + timedelta(seconds=BACKOFF[max(min(tries, len(BACKOFF)) - 1, 0)])).isoformat(timespec="seconds")
        c.execute("""INSERT INTO link_scan (post_id, url, tries, status, at, next_try, error) VALUES (%s,%s,%s,%s,%s,%s,%s)
                     ON CONFLICT (post_id) DO UPDATE SET url=excluded.url, tries=excluded.tries, status=excluded.status,
                     at=excluded.at, next_try=excluded.next_try, error=excluded.error""",
                  (pid, link, tries, status, _now().isoformat(timespec="seconds"), nxt, (err or "")[:300] or None))


def one(p):
    link = next(iter(URL.findall(content(p))), None)
    try:
        resolve(p)
        _mark(p["id"], link, True)
        return True
    except Exception as e:  # noqa: BLE001: a link that can't be read now is tried again later
        import resi
        _mark(p["id"], link, False, f"{type(e).__name__}: {e}", counted=not isinstance(e, resi.ProxyOut))
        print("links:", p["url"][:90], "→", (link or "")[:90], repr(e)[:160], flush=True)
        return False


def run_once(limit=50):
    """one round → how many posts were resolved"""
    posts, hi = _round(limit)
    n = 0
    if posts:
        with ThreadPoolExecutor(min(WORKERS, len(posts))) as ex:
            n = sum(ex.map(one, posts))
    if hi is not None:  # every new post up to hi was looked at (a round cut at `limit` moves no watermark)
        corpus.kv_set("links_upto", {"id": hi})
    return n


def loop():
    while not STOP.is_set():
        try:
            with corpus.stage("links"):  # a restart starts from now (corpus.STAGES): the backlog waits
                n = run_once()
            STOP.wait(0.5 if n else IDLE)
        except Exception as e:  # noqa: BLE001
            print("links failed:", repr(e)[:300], flush=True)
            STOP.wait(60)


def start(stop=None):
    """the resolver thread (the poller's; stop = the scorer's drain event)"""
    global STOP
    STOP = stop or STOP
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


# ---- the X backfill ----

def backfill_x(days=30, workers=4):
    """every X post (not a comment) of the last `days` days read again through the tweet detail (USD 0.001 each) →
    counts {posts, updated, failed}"""
    cut = (_now() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = [dict(r) for r in corpus.db().execute(
        """SELECT id, url FROM posts WHERE thread IS NULL AND platform = 'X' AND coalesce(published, seen) >= %s
           ORDER BY coalesce(published, seen) DESC""", (cut,))]
    stat = {"posts": len(rows), "updated": 0, "failed": 0}
    lock = threading.Lock()

    def go(r):
        try:
            before = corpus.db().execute("SELECT title, text, engagement FROM posts WHERE id=%s", (r["id"],)).fetchone()
            x_reread(r["id"], r["url"])
            after = corpus.db().execute("SELECT title, text, engagement FROM posts WHERE id=%s", (r["id"],)).fetchone()
            with lock:
                stat["updated"] += dict(before) != dict(after)
        except Exception as e:  # noqa: BLE001
            with lock:
                stat["failed"] += 1
            print("backfill-x:", r["url"], repr(e)[:160], flush=True)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(go, rows))
    return stat


# ---- posts stored with a URL for a title (before tidy_title; Deyao, 2026-10-15) ----

URL_TITLE_SQL = r"title ~ '^\s*https?://'"  # (a title ending in a link is left: rewording 5,000 would rescore them)


def url_titled(db=None):
    """how many posts have a title that starts with a URL (the count Deyao's check uses)"""
    return (db or corpus.db()).execute(f"SELECT count(*) FROM posts WHERE {URL_TITLE_SQL}").fetchone()[0]


def fix_titles():
    """correct every stored post whose title starts with a URL → counts {before, after, x_reread, retitled,
    resolved, unresolved, comments, stream_items}:
      - an X post whose content is an X Article or a t.co link: read again through the tweet detail (USD 0.001)
      - a post links.py resolved: the linked page's title (from its 〔連結內容〕 block)
      - a post that is (mostly) a link, not resolved yet: resolved now (else 〔連結〕host, held and tried again later)
      - any other: the title without its links (corpus.tidy_title)
      - comments: their parent post's title; stream items: the stored title (串流's page; Telegram messages are
        never edited or deleted)"""
    db = corpus.db()
    out = {"before": url_titled(db), "x_reread": 0, "retitled": 0, "resolved": 0, "unresolved": 0, "comments": 0,
           "stream_items": 0}
    rows = [dict(r) for r in db.execute(
        f"SELECT id, url, title, text, sid, platform, thread FROM posts WHERE {URL_TITLE_SQL} ORDER BY id")]
    for p in [r for r in rows if not r["thread"]]:
        text = content(p)
        if X_STATUS.match(p["url"]) and re.search(r"x\.com/i/article/|//t\.co/", text):
            try:
                if x_reread(p["id"], p["url"]):
                    out["x_reread"] += 1
                    p = dict(p, **dict(db.execute("SELECT title, text FROM posts WHERE id=%s", (p["id"],)).fetchone()))
                    text = content(p)
            except Exception as e:  # noqa: BLE001: a deleted tweet: what was stored is corrected below
                print("fix-titles:", p["url"], repr(e)[:160], flush=True)
            if not re.search(URL_TITLE_SQL_RE, p["title"] or "") and not mostly_link(text):
                continue
        lt = linked_title(text)
        if lt:
            corpus.set_content(p["id"], lt, p["text"] or text)
            out["retitled"] += 1
        elif mostly_link(text):
            if one(p):
                out["resolved"] += 1
            else:
                corpus.set_content(p["id"], p["title"], p["text"] or text)  # 〔連結〕host until it is read
                out["unresolved"] += 1
        else:
            corpus.set_content(p["id"], p["title"], p["text"] or text)
            out["retitled"] += 1
    for c in [r for r in rows if r["thread"]]:
        par = db.execute("SELECT title FROM posts WHERE url=%s", (c["thread"],)).fetchone()
        corpus.set_content(c["id"], par["title"] if par else c["title"], c["text"])
        out["comments"] += 1
    with db:
        for r in db.execute(rf"""SELECT si.id, p.title FROM stream_items si JOIN posts p ON p.url = si.url
                                WHERE si.item->>'title' ~ '^\s*https?://' AND p.title IS DISTINCT FROM si.item->>'title'"""
                            ).fetchall():
            db.execute("UPDATE stream_items SET item = jsonb_set(item, '{title}', to_jsonb(%s::text)) WHERE id=%s",
                       (r["title"], r["id"]))
            out["stream_items"] += 1
    out["after"] = url_titled(db)
    return out


URL_TITLE_SQL_RE = re.compile(r"^\s*https?://")


# ---- posts stored with their site's name for a title (Deyao, 2026-10-20: HKGolden 「香港高登討論區」) ----

def _site_candidates(db):
    names = sorted({n for ns in corpus.SITE_NAMES.values() for n in ns})
    rows = db.execute(
        """SELECT id, url, sid, source, section, website, title, text FROM posts
           WHERE thread IS NULL AND source_type = ANY(%(types)s) AND char_length(title) <= 60
             AND (btrim(title) = ANY(%(names)s) OR btrim(title) IN (btrim(source), btrim(website))
                  OR (source <> '' AND starts_with(title, source) AND char_length(title) <= char_length(source) + 5)
                  OR (website <> '' AND starts_with(title, website) AND char_length(title) <= char_length(website) + 5))
           ORDER BY id""", {"types": list(corpus.SITE_TITLE_TYPES), "names": names}).fetchall()
    return [dict(r) for r in rows if corpus.site_named(r["title"], r["source"], r["website"], url=r["url"])]


def site_titled(db=None):
    """how many posts (not comments; news, official, blog, forum) are titled with their site's name"""
    return len(_site_candidates(db or corpus.db()))


SLUG_SIDS = ("bastillepost", "hkbusiness", "reuters", "youngpost_sitemap", "metrohk_sitemap", "scmp_photos")  # = sources_extra.SLUG_TITLES


def fix_site_titles():
    """correct every stored post titled with its site's name → counts {before, hkgolden, page, slug, unresolved,
    failed, stream_items, after}:
      - an HKGolden thread: HKGolden's API (title, first post, counts), and its source type Forum, website 高登討論區
      - a slug-titled source (巴士的報): the title its URL gives (the page states only the site's name)
      - any other: the page's own headline (headline()); none → 〔連結〕host (no title yet)
      - stream items: their stored title and website follow (membership kept: no recall; Telegram is never edited)
    Scores are left alone: a post outside stage scoring's work is never scored again (streams.due)"""
    db = corpus.db()
    rows = _site_candidates(db)
    out = {"before": len(rows), "hkgolden": 0, "page": 0, "slug": 0, "unresolved": 0, "failed": 0, "stream_items": 0}
    for p in rows:
        try:
            if tid := hkgolden.thread_id(p["url"]) or hkgolden.thread_id(next(iter(URL.findall(p["text"] or "")), "")):
                hkgolden_post(p["id"], tid, p["text"] or p["url"])
                cl = corpus.classify({"sid": p["sid"], "source": p["source"], "section": p["section"], "url": p["url"]})
                db.execute("UPDATE posts SET website=%s, platform=%s, source_type=%s, region=%s WHERE id=%s",
                           (cl["website"], cl["platform"], cl["source_type"], cl["region"], p["id"]))
                out["hkgolden"] += 1
                continue
            title = ""
            if (p["sid"] or "") in SLUG_SIDS:
                import sources_extra
                title, kind = sources_extra.slug_title(p["url"]), "slug"
            if not title or corpus.site_named(title, p["source"], p["website"], url=p["url"]):
                title, kind = parse_page(p["url"], fetch_page(p["url"]))["title"], "page"
        except Exception as e:  # noqa: BLE001: a page that can't be read: no title yet
            print("fix-site-titles:", p["url"][:100], repr(e)[:160], flush=True)
            title, kind = "", "failed"
        if not title:
            title, kind = corpus.LINK_TITLE + corpus._host(p["url"]), ("unresolved" if kind != "failed" else kind)
        corpus.set_content(p["id"], title, p["text"] or p["url"])
        out[kind] += 1
    urls = [p["url"] for p in rows]
    with db:
        for r in db.execute("""SELECT si.id, p.title, p.website FROM stream_items si JOIN posts p ON p.url = si.url
                               WHERE si.url = ANY(%s) AND (p.title IS DISTINCT FROM si.item->>'title'
                                                           OR p.website IS DISTINCT FROM si.item->>'website')""",
                            (urls,)).fetchall():
            db.execute("""UPDATE stream_items SET item = item || jsonb_build_object('title', %s::text, 'website', %s::text)
                          WHERE id=%s""", (r["title"], r["website"], r["id"]))
            out["stream_items"] += 1
    out["after"] = site_titled(db)
    return out


def status():
    db = corpus.db()
    out = {"due": len(due(100000)),
           "held": db.execute(f"SELECT count(*) FROM posts p WHERE {HELD_SQL}").fetchone()[0]}
    for r in db.execute("SELECT status, count(*) AS n FROM link_scan GROUP BY status"):
        out[r["status"]] = r["n"]
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["status"]:
        print(json.dumps(status()))
    elif a[:1] == ["once"]:
        for p in due(int(a[1]) if a[1:] else 50):
            ok = one(p)
            r = corpus.db().execute("SELECT text FROM posts WHERE id=%s", (p["id"],)).fetchone()
            print(p["id"], "ok" if ok else "FAILED", p["url"], "|", (r["text"] or "")[-120:].replace("\n", " "))
    elif a[:1] == ["fix-titles"]:
        print(json.dumps(fix_titles()))
    elif a[:1] == ["fix-site-titles"]:
        print(json.dumps(fix_site_titles()))
    elif a[:1] == ["backfill-x"]:
        print(json.dumps(backfill_x(int(a[1]) if a[1:] else 30)))
    else:
        print(__doc__)
