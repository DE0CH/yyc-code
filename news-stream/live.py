"""Live updates for the site's open pages (ARCHITECTURE.md "Live updates"): Postgres triggers (corpus.SCHEMA, `live_*`)
announce every change a page shows on channel yyc_live as a topic ("feed:<day>", "scores:<day>", "item:<post id>");
this thread of the poller LISTENs, gathers the topics for BATCH seconds and POSTs them to the site's LiveHub (a
Cloudflare Durable Object holding each open page's WebSocket), which tells the pages showing them to read again.

Env: LIVE_URL (default the Worker's /live/notify), LIVE_SECRET (the Worker's secret; unset = off). The LISTEN needs a
direct connection (a transaction pooler doesn't pass notifications).
"""
import json, os, time, urllib.request

import psycopg

import corpus

URL = os.environ.get("LIVE_URL", "https://yyc-web.yyc-news.workers.dev/live/notify")
SECRET = os.environ.get("LIVE_SECRET", "")
BATCH = 1.0       # seconds of changes sent together
MAX_TOPICS = 500  # per POST


def send(topics):
    topics = sorted(topics)
    for i in range(0, len(topics), MAX_TOPICS):
        req = urllib.request.Request(URL, data=json.dumps({"topics": topics[i:i + MAX_TOPICS]}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Live-Secret": SECRET,
                                              "User-Agent": "yyc-news-stream-live/1"})  # Cloudflare refuses urllib's UA
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()


def catch_up():
    """the topics every open list page reads again after the listener was away: the feeds and scores of today and
    yesterday (HK time). An open item page misses only that gap's change to its item, until its own refresh"""
    from datetime import datetime, timedelta, timezone
    hk = datetime.now(timezone(timedelta(hours=8))).date()
    return {f"{kind}:{d.isoformat()}" for d in (hk, hk - timedelta(days=1)) for kind in ("feed", "scores")}


def run(stop):
    """until stop is set: LISTEN, batch, send; reconnects after any failure"""
    if not SECRET:
        print("live: LIVE_SECRET unset, live updates off", flush=True)
        return
    lost = False
    while not stop.is_set():
        try:
            with psycopg.connect(corpus.DATABASE_URL, autocommit=True, application_name="yyc-live", connect_timeout=5) as c:
                c.execute("LISTEN yyc_live")
                if lost:  # changes made while the listener was away (a switchover) were announced to nobody
                    lost = False
                    try:
                        send(catch_up())
                    except Exception as e:  # noqa: BLE001
                        print("live: catch-up send failed:", repr(e)[:200], flush=True)
                while not stop.is_set():
                    got = {n.payload for n in c.notifies(timeout=BATCH)}
                    if got:
                        try:
                            send(got)
                        except Exception as e:  # noqa: BLE001: a page misses one push; its own refresh catches up
                            print("live: send failed:", repr(e)[:200], flush=True)
        except Exception as e:  # noqa: BLE001
            if not lost:
                print("live: listener failed, reconnecting:", repr(e)[:200], flush=True)
            lost = True
            stop.wait(corpus.RETRY_GAP)


if __name__ == "__main__":
    import threading
    run(threading.Event())
