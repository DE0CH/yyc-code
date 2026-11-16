#!/usr/bin/env python3
"""The comments of social posts for their two scorings (Deyao, 2026-10-14; a poller thread, app.py), on twopass.py:
Facebook, Instagram, Threads and YouTube posts (twopass.SOCIAL_URLS, whoever collected them) are scored as units with
their comments, at capture and again at published + 30 min. This is their fetcher:

  - capture: each new post (after the scoring switch, never scored) has its comments read at once
    (twopass.reading → read → twopass.captured); the scorer waits for that (twopass.FETCHER_URLS, streams.FIRST_WAIT
    at most). A read that fails still marks the post captured, so the score never waits on a broken platform.
  - second scoring: the posts whose second scoring is due (twopass.due_for_fetch) have their comments read again,
    then twopass.fetched, and streams scores them once more with every comment filed by then.
Readers: comments_fetch.py's facebook_post (logged-out session over yycstream), instagram_post (the post page over
yycstream; every comment through TikHub when the page shows fewer than the post counts; Threads replies from the
page) and youtube_post (YouTube's own web API, direct). The comments job keeps its own schedule for 搜尋 and the item
page; this thread only feeds the two scorings.

    python3 social_twopass.py once      one round by hand
"""
import concurrent.futures as cf
import sys, threading, time
from datetime import datetime, timedelta

import corpus, twopass

HKT = corpus.HKT
WORKERS = 6
MAX_AGE = timedelta(days=2)
IDLE = 10  # seconds between rounds with nothing to do
PROXY_RETRY = 120  # seconds the proxied second reads wait after IPRoyal said 402 (resi.py)
# a Facebook read for the scoring is bounded, so one busy thread never stalls this fetcher (a full read of a 1,774-comment
# thread took 18 min, 20 Oct 2026): at most FB_PAGES requests (10 comments a page, or a comment's replies), newest
# comments first (the scorer reads the newest streams.REPLY_CHARS characters' worth), and FB_READ_S seconds
FB_PAGES = 50
FB_READ_S = 120
STOP = threading.Event()
LIKES = ["https://www.facebook.com/%", "https://www.instagram.com/%", "https://www.threads.%", "https://www.youtube.com/watch%"]
_local = threading.local()


def now():
    return datetime.now(HKT)


def read(p):
    """read one post's comments into the corpus with the platform's reader"""
    import comments_fetch as cfm
    url = p["url"]
    p = dict(p, age_h=0)
    if "facebook.com/" in url:
        if not getattr(_local, "fb", None):
            _local.fb = cfm.FB()
        return cfm.facebook_post(_local.fb, p, end=time.time() + FB_READ_S, pages=FB_PAGES)
    if "instagram.com/" in url or "threads." in url:
        if not getattr(_local, "http", None):
            import resi
            _local.http = resi.session()  # counted; IPRoyal out of traffic → resi.ProxyOut
        return cfm.instagram_post(_local.http, p)
    if "youtube.com/watch" in url:
        return cfm.youtube_post(p)[0]
    return 0


def _read(p):
    try:
        read(p)
        return None
    except Exception as e:
        return repr(e)[:160]


def capture(p):
    twopass.reading(p["url"], now())
    err = _read(p)
    twopass.captured(p["url"], now())
    return err


def again(url):
    """the second read; with the proxy out of traffic (resi.py) it is NOT marked re-read: it stays due, read once the
    proxy has traffic again"""
    import resi
    p = corpus.db().execute("SELECT * FROM posts WHERE url=%s", (url,)).fetchone()
    if p:
        try:
            read(dict(p))
            err = None
        except resi.ProxyOut as e:
            return repr(e)[:160]
        except Exception as e:
            err = repr(e)[:160]
    else:
        err = "post gone"
    twopass.fetched(url, now())
    return err


def run_once(limit=60):
    """one round: new posts' capture reads, then the reads for due second scorings → how many posts"""
    import streams
    t = now()
    # c: the host prefilter and the anti-joins first, MATERIALIZED so the URL regexes run only on what is left (a plain
    # WHERE let the planner reorder them: 1.8 s → 0.2 s live, 29 Oct 2026)
    rows = [dict(r) for r in corpus.db().execute(
        f"""WITH c AS MATERIALIZED (
              SELECT p.* FROM posts p LEFT JOIN two_pass tp ON tp.url = p.url
              WHERE p.thread IS NULL AND p.id > %s AND p.seen >= %s AND {corpus.seen_cond("p", "comments")}
                AND split_part(p.url, '/', 3) ~ %s
                AND tp.captured_at IS NULL
                AND NOT EXISTS (SELECT 1 FROM scored s WHERE s.post_id = p.id))
            SELECT p.* FROM c p WHERE {twopass.fetcher_cond("p")} ORDER BY p.id DESC LIMIT %s""",
        (streams.settings()["from_id"], (t - MAX_AGE).isoformat(timespec="seconds"), twopass.FETCHER_HOSTS_RX, limit))]
    due = [u for like in LIKES for u in twopass.due_for_fetch(like, t, limit)]
    import resi
    out = resi.stopped()  # the proxy out of traffic: the proxied second reads wait (one round a PROXY_RETRY tries again)
    if out and (datetime.now(HKT) - datetime.fromisoformat(out["at"])).total_seconds() < PROXY_RETRY:
        due = [u for u in due if "youtube.com" in u]
    errs = []
    if rows or due:
        with cf.ThreadPoolExecutor(WORKERS) as ex:
            errs += [e for e in ex.map(capture, rows) if e]
            errs += [e for e in ex.map(again, due) if e]
    for e in errs[:3]:
        print("social_twopass:", e, flush=True)
    return len(rows) + len(due)


def loop():
    while not STOP.is_set():
        try:
            with corpus.stage("comments"):  # a restart starts from now (corpus.STAGES): the backlog waits
                n = run_once()
        except Exception as e:
            print("social_twopass:", repr(e)[:200], flush=True)
            n = 0
        STOP.wait(1 if n else IDLE)


def start():
    th = threading.Thread(target=loop, daemon=True, name="social_twopass")
    th.start()
    return th


if __name__ == "__main__":
    if sys.argv[1:] == ["once"]:
        print(run_once())
