#!/usr/bin/env python3
"""Telegram alerts: every kept stream item, posted to its stream's Telegram channel (one-way; the bot never reads).

Each stream in streams.json with a `telegram_chat` (the channel's numeric id, -100…) gets every item filed into
stream_items for it, as its mark (marks.py) + the headline, then its source linking to the item (link previews always off). The bot is an admin of each channel with "post messages".
Delivery follows stream_items.id: kv `telegram_sent` holds the last id sent per stream, so a restart or a Telegram
outage resumes where it stopped, and a stream that gains a channel starts from its newest item (no backlog).
The channel is 串流's copy: every filed item is sent, and a sent message is never deleted or edited (Deyao,
2026-10-15: "there shouldn't be recalling of messages"; a filed post stays a member of its stream, streams.py). The
scorer waits for a news post's page before it can file it (streams.due), so an alert goes out on the full-text score.
Runs as a thread of the poller (app.py); with no TELEGRAM_BOT_TOKEN it does nothing.

Env: TELEGRAM_BOT_TOKEN  the bot's token from @BotFather

    python3 telegram_alerts.py test STREAM     post a test message to that stream's channel
"""
import html, json, os, sys, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime

import corpus, events, marks, titles
import streams as scoring  # the module; `streams` below is the stream list

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
KEY = "telegram_sent"
GAP = 3.1    # seconds between posts to one channel: Telegram allows ~20 a minute per channel
IDLE = 3     # seconds between checks for new items; a filing in this process wakes the sender at once (streams.FILED)
LAST = {}    # stream -> time.monotonic() of its channel's last post (GAP is per channel)
HOLD = {}    # stream -> time.monotonic() before which its channel isn't tried (Telegram's retry_after, a network error)
BATCH = 50
STOP = threading.Event()  # the poller's drain: stop after the message in hand


class Retry(Exception):
    """Telegram or the network failed in a way that may pass: keep the item and try again later"""


def call(method, **params):
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", json.dumps(params).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["result"]
    except urllib.error.HTTPError as e:
        body = json.loads(e.read() or b"{}")
        if e.code == 429 or e.code >= 500:
            raise Retry(body.get("parameters", {}).get("retry_after", 30))
        raise ValueError(f"{method} {e.code}: {body.get('description')}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise Retry(30) from e


def tags(it):
    """the item's mark in brackets (marks.py: a traffic light for the sentiment, then one remark emoji), e.g. "(🔴👀) ";
    "" when there is none"""
    t = marks.text(it)
    return f"({t}) " if t else ""


SITE = "https://newswatch.yyc-internal.com"


def item_url(it, stream=None):
    """the post's item page, 詳情 (Deyao, 2026-10-14): /item/<post_id> (the filed item carries post_id), else looked up
    by its URL; None when the post isn't in the corpus. ?p=<stream>: the page opens with the channel's stream selected"""
    pid = it.get("post_id")
    if not pid:
        r = corpus.db().execute("SELECT id FROM posts WHERE url=%s", (it.get("url"),)).fetchone()
        pid = r[0] if r else None
    if not pid:
        return None
    return f"{SITE}/item/{pid}" + (f"?p={urllib.parse.quote(stream)}" if stream else "")


def text(it, stream=None):
    """the category in bold when the stream sorts its items into categories (streams.json `categories`; the scorer
    sets item["category"]), the mark and the headline (plain, not bold: Deyao, 2026-10-13), then the source as the
    link to the item (no bare URL) and 詳情, the link to the post's item page"""
    detail = item_url(it, stream)
    cat = f"<b>【{html.escape(it['category'])}】</b>\n" if it.get("category") else ""
    return (f"{cat}{tags(it)}{html.escape(titles.plain(it))}\n"
            f"<a href=\"{html.escape(it['url'])}\">{html.escape(it.get('website') or it.get('source') or '')} ↗</a>"
            + (f" · <a href=\"{html.escape(detail)}\">詳情</a>" if detail else ""))


def post(chat, it, stream=None):
    return call("sendMessage", chat_id=chat, text=text(it, stream), parse_mode="HTML",
                link_preview_options={"is_disabled": True})


def cursors(streams):
    """the last stream_items.id sent per stream; a stream new to Telegram starts at its newest item"""
    sent = corpus.kv_get(KEY, {})
    for s in streams:
        if s["id"] not in sent:
            r = corpus.db().execute("SELECT max(id) FROM stream_items WHERE stream=%s", (s["id"],)).fetchone()
            sent[s["id"]] = r[0] or 0
    corpus.kv_set(KEY, sent)
    return sent


def skip_backlog(streams):
    """the sender starts again after a stop (corpus.stage): every stream's cursor moves to its newest item, so what was
    filed before is not sent (Deyao, 2026-10-27: a backlog is processed only by a manual trigger)"""
    sent = corpus.kv_get(KEY, {})
    for s in streams:
        r = corpus.db().execute("SELECT max(id) FROM stream_items WHERE stream=%s", (s["id"],)).fetchone()
        sent[s["id"]] = max(r[0] or 0, sent.get(s["id"]) or 0)
    corpus.kv_set(KEY, sent)


def pending(stream, after):
    """(id, item as shown) of the stream's items after `after`, with the post's title from AI as it is at send time"""
    return [(r["id"], titles.item_row(r)) for r in corpus.db().execute(
        f"""SELECT si.id, si.item, {titles.ITEM_COLS} FROM stream_items si {titles.ITEM_JOIN}
            WHERE si.stream=%s AND si.id>%s ORDER BY si.id LIMIT %s""", (stream, after, BATCH))]


def note_sent(sid):
    """when this stream's last send worked (kv telegram_sent_at {stream: time}): the monitor shows it, and a send
    error before it is history, not a live problem (monitor.send_error)"""
    try:
        at = corpus.kv_get("telegram_sent_at") or {}
        at[sid] = datetime.now(corpus.HKT).isoformat(timespec="seconds")
        corpus.kv_set("telegram_sent_at", at)
    except Exception:
        pass


def note_error(sid, err):
    """the latest send failure (kv telegram_error), shown on Deyao's monitor (monitor.py)"""
    try:
        corpus.kv_set("telegram_error", {"at": datetime.now(corpus.HKT).isoformat(timespec="seconds"), "stream": sid,
                                          "error": str(err)[:300]})
    except Exception:
        pass


def send_due(streams):
    """post every unsent item, each channel at most one per GAP (Deyao, 2026-10-23: the pipeline tighter: a channel
    that hasn't posted for GAP posts at once, whatever the others do), round robin; items filed meanwhile join at once
    (streams.FILED); a channel Telegram holds back (Retry) waits alone while the others go on; → how many were posted.
    Returns when nothing is left but held channels."""
    streams = [s for s in streams if s.get("telegram_chat")]
    sent = cursors(streams)
    chat = {s["id"]: s["telegram_chat"] for s in streams}
    scoring.FILED.clear()
    queues = {s["id"]: pending(s["id"], sent[s["id"]]) for s in streams}
    n = 0
    while not STOP.is_set():
        if scoring.FILED.is_set():  # new items: read the queues again from the cursors
            scoring.FILED.clear()
            queues = {s["id"]: pending(s["id"], sent[s["id"]]) for s in streams}
        now = time.monotonic()
        ready = [sid for sid, q in queues.items() if q and HOLD.get(sid, 0) <= now]
        if not ready:
            return n
        for sid in ready:
            if time.monotonic() - LAST.get(sid, -GAP) < GAP:
                continue
            rid, it = queues[sid][0]
            try:
                post(chat[sid], it, sid)
                n += 1
                note_sent(sid)
                events.emit("alert", it.get("post_id"), channel="telegram", stream=sid, item=rid, ok=True)
            except Retry as e:  # this channel waits; the others go on
                HOLD[sid] = time.monotonic() + max(int(e.args[0] or 30), 5)
                print(f"telegram {sid}: held {HOLD[sid] - time.monotonic():.0f} s: {e.args[0]}", flush=True)
                continue
            except ValueError as e:  # Telegram refused this message for good (bad chat id, rights): skip it
                print(f"telegram {sid}: item {rid} not sent: {e}", flush=True)
                note_error(sid, f"item {rid} not sent: {e}")
                events.emit("alert", it.get("post_id"), channel="telegram", stream=sid, item=rid, ok=False, error=str(e))
            finally:
                LAST[sid] = time.monotonic()
            queues[sid].pop(0)
            sent[sid] = rid
            corpus.kv_set(KEY, sent)
        now = time.monotonic()
        waits = [GAP - (now - LAST.get(sid, -GAP)) for sid, q in queues.items() if q and HOLD.get(sid, 0) <= now]
        if waits:  # the next channel's GAP, or a filing, whichever comes first
            scoring.FILED.wait(max(0.05, min(waits)))
    return n


def loop(streams):
    while not STOP.is_set():
        try:
            with corpus.stage("telegram") as restarted:  # back after a stop: nothing filed before is sent
                if restarted:
                    skip_backlog(streams)
                send_due(streams)
            scoring.FILED.wait(IDLE)  # a filing wakes it at once; IDLE for held channels, flags and missed wakes
        except Retry as e:
            STOP.wait(max(int(e.args[0] or 30), 5))
        except Exception as e:
            print("telegram failed:", repr(e)[:300], flush=True)
            note_error(None, repr(e))
            STOP.wait(60)


def start(streams):
    """the sender thread (None when off); STOP + join() drains it"""
    if TOKEN and any(s.get("telegram_chat") for s in streams):
        t = threading.Thread(target=loop, args=(streams,), daemon=True)
        t.start()
        return t


if __name__ == "__main__" and sys.argv[1:2] == ["test"]:
    st = {s["id"]: s for s in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "streams.json")))}
    s = st[sys.argv[2]]
    post(s["telegram_chat"], {"title": f"YYC {s['name']} 測試訊息", "source": "YYC news stream",
                              "url": "https://newswatch.yyc-internal.com/"})
    print("sent")
