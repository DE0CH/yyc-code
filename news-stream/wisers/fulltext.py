"""Full text from YYC's own corpus before WisersOne (Deyao, 2026-10-29: "we have the full text in our own database? for
the ones we don't, log it and then have it be transparent to the user"). Opening a document in WisersOne spends the
account's click quota (60003), so the relay's document chain (relay.doc()) asks here first.

find(item) looks for the Wisers search item in posts, read-only, as role yyc_readonly (env WISERS_PG_*):
  web and social items (a url):  posts.url_key = corpus.url_key(url), else posts.url = the url (indexed lookups only)
  print items (no url):          the same headline (searchtext.norm, as posts.stitle) published within a day of the
                                 item's date; among several, the one from the same outlet (source / website), else none
A candidate is used only when its text is the article: not title-only (titles.title_only) and at least MIN_RATIO of
Wisers' wordcount (CJK characters plus Latin words). Returns {post_id, text, url, website, match} or None, and why not.
"""
import os, re, sys, threading
from datetime import date, timedelta

sys.path.insert(1, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # news-stream/: corpus, searchtext, titles

MIN_RATIO = float(os.environ.get("WISERS_CORPUS_MIN_RATIO", "0.6"))
_conn = {"c": None}
_lock = threading.Lock()


def enabled():
    return bool(os.environ.get("WISERS_PG_HOST"))


def _connect():
    import psycopg
    return psycopg.connect(host=os.environ["WISERS_PG_HOST"], port=int(os.environ.get("WISERS_PG_PORT", "5432")),
                           dbname=os.environ.get("WISERS_PG_DB", "app"), user=os.environ.get("WISERS_PG_USER", "yyc_readonly"),
                           password=os.environ.get("WISERS_PG_PASSWORD", ""), sslmode=os.environ.get("WISERS_PG_SSL", "require"),
                           connect_timeout=5, autocommit=True, options="-c statement_timeout=2000 -c default_transaction_read_only=on")


def _rows(sql, args):
    """a read on the one connection; a lost connection is opened again, and while the database is away (a switchover)
    the read is tried again (corpus.retrying: reads are always safe to repeat)"""
    import corpus

    def once():
        try:
            if _conn["c"] is None or _conn["c"].closed:
                _conn["c"] = _connect()
            with _conn["c"].cursor() as cur:
                cur.execute(sql, args)
                cols = [d.name for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:
            try:
                _conn["c"].close()
            except Exception:
                pass
            _conn["c"] = None
            raise
    with _lock:
        try:
            return once()
        except Exception as e:  # noqa: BLE001: a connection the server ended while idle: once more at once
            if not corpus.transient(e):
                raise
        return corpus.retrying(once, "wisers read")


def units(text):
    """CJK characters plus Latin words: how a wordcount is counted for a mixed text"""
    t = text or ""
    return len(re.findall(r"[㐀-鿿豈-﫿]", t)) + len(re.findall(r"[A-Za-z0-9]+", t))


COLS = "id, url, website, source, title, text, day, ai_title"


def candidates(it):
    """posts that may be this Wisers search item (raw item: url, headline, publishDate), with how each was found"""
    import corpus, searchtext
    url = (it.get("webUrl") or it.get("url") or "").strip()
    out = []
    if url:  # one indexed lookup each (posts_url_key_idx, then the UNIQUE url): an OR across them, or canonical_url
        # (no index), made Postgres read the whole table and time out on every web item (2026-10-29)
        key = corpus.url_key(url)
        rows = _rows(f"SELECT {COLS} FROM posts WHERE url_key = %s LIMIT 5", (key,)) if key else []
        rows = rows or _rows(f"SELECT {COLS} FROM posts WHERE url = %s LIMIT 5", (url,))
        if rows:
            return [(r, "url") for r in rows]
    head = searchtext.norm(it.get("headline") or "")
    day = (it.get("publishDate") or "")[:10]
    if len(head) < 4 or len(head.encode()) >= 1990 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return out
    d = date.fromisoformat(day)
    lo, hi = str(d - timedelta(days=1)), str(d + timedelta(days=1))
    # thread IS NULL and the octet_length guard as posts_live_stitle's own condition, so that partial index answers
    rows = _rows(f"SELECT {COLS} FROM posts WHERE day BETWEEN %s AND %s AND thread IS NULL "
                 f"AND octet_length(stitle) < 2000 AND (stitle = %s OR stitle LIKE %s) LIMIT 10", (lo, hi, head, head.replace("%", r"\%") + "\n%"))
    return [(r, "headline") for r in rows]


def find(it):
    """(match or None, why): the post whose text is this item's full text"""
    if not enabled():
        return None, "no corpus connection"
    import titles
    try:
        cands = candidates(it)
    except Exception as e:
        return None, f"corpus lookup failed: {str(e)[:120]}"
    if not cands:
        return None, "not in the corpus"
    src = (it.get("source") or "").strip().lower()
    want = int(it.get("wordcount") or 0) if str(it.get("wordcount") or "").isdigit() else 0
    good, why = [], []
    for r, how in cands:
        text = r.get("text") or ""
        if titles.title_only(text, r.get("title") or "", r.get("ai_title") or ""):
            why.append(f"post {r['id']}: title only")
            continue
        if want and units(text) < MIN_RATIO * want:
            why.append(f"post {r['id']}: {units(text)} of {want} words")
            continue
        good.append((r, how))
    if not good:
        return None, "; ".join(why) or "no usable text"
    if len(good) > 1 and good[0][1] == "headline":  # several outlets with the same headline: only the same outlet
        same = [g for g in good if src and src in f"{g[0].get('source') or ''} {g[0].get('website') or ''}".lower()]
        if len(same) != 1:
            return None, f"{len(good)} posts with this headline, none clearly from {it.get('source')}"
        good = same
    r, how = good[0]
    return {"post_id": r["id"], "text": r["text"], "url": r["url"], "website": r.get("website") or r.get("source") or "",
            "match": how}, ""
