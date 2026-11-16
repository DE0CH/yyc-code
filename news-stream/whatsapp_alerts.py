#!/usr/bin/env python3
"""WhatsApp alerts: every kept stream item, posted to its stream's WhatsApp group (Deyao, 2026-10-16; route "E. Self-hosted
unofficial client" of docs/whatsapp/: a linked device of YYC's own WhatsApp number, whose primary phone is the Aliyun
cloud phone acp-hln9ilimxqzruvccu).

The same items, content and order as Telegram (telegram_alerts.py): each stream in streams.json with a `whatsapp_group`
(the group's jid, …@g.us) gets every item filed into stream_items for it, as one message: the category in bold when the
stream sorts its items, the mark (marks.py) + the headline, the source with the article's link, then 詳情 (the item
page). WhatsApp has no hyperlinks, so the links are written out. A sent message is never recalled or edited.
Delivery follows stream_items.id: kv `whatsapp_sent` holds the last id per stream; a stream that gains a group starts
from its newest item (no backfill). At most once: the cursor moves BEFORE each send, so a send that fails (the socket
dropped mid-send) is reported on the monitor and never repeated. While the device is not connected nothing is sent and
no cursor moves: the items wait. Sends go out as they are filed (Deyao, 2026-10-16: no pacing or warm-up), round robin
across streams; a filing wakes the sender at once through LISTEN yyc_live (listen(); Deyao, 2026-10-23).

The WhatsApp side is whatsapp/gateway.js (Baileys), a Node process this one starts and talks to on 127.0.0.1: it holds
the linked device's keys in Postgres (table whatsapp_auth) and writes its state to kv `whatsapp_status`. This process
is the singleton: it takes the Postgres advisory lock 'yyc whatsapp' (podlock.py) before it starts the gateway (two
connections of one linked device replace each other), and on SIGTERM it stops sending, closes the gateway and releases
the lock, so the next pod takes over without re-linking (deploy/apps/whatsapp.yaml). A lock whose connection was cut
and taken by another copy meanwhile stops this one the same way within seconds (podlock.CHECK).

Deyao hears on Discord (notify.py) once when the device is unlinked (logged out from the phone) or the number banned.

    python3 whatsapp_alerts.py run                  the sender (the Deployment's command)
    python3 whatsapp_alerts.py status               the gateway's state
    python3 whatsapp_alerts.py pair 447448090466    link: prints the 8-character code to type on the phone
                                                    (WhatsApp → Linked devices → Link with phone number instead)
    python3 whatsapp_alerts.py groups               make the missing groups (one per stream in streams.json, "YYC ·
                                                    <name>", only admins post), print jid + invite link
    python3 whatsapp_alerts.py invite JID           the group's invite link
    python3 whatsapp_alerts.py test STREAM          post a test message to that stream's group
(all but run talk to the gateway of the running pod: kubectl exec deploy/whatsapp -- python3 whatsapp_alerts.py …)
"""
import json, os, signal, subprocess, sys, threading, time, urllib.error, urllib.request
from datetime import datetime

import corpus, events, marks, notify, titles
from telegram_alerts import item_url

KEY = "whatsapp_sent"
PORT = int(os.environ.get("WHATSAPP_GATEWAY_PORT", "8790"))
IDLE = 3     # seconds between checks for new items; a filed item wakes the sender at once (listen(), WAKE)
BATCH = 50
STOP = threading.Event()
WAKE = threading.Event()  # set by listen() when a stream item is filed anywhere
HERE = os.path.dirname(os.path.abspath(__file__))
STARTED, ALIVE = "/tmp/whatsapp-started", "/tmp/whatsapp-alive"
ALERT_STATES = {"logged_out": "unlinked (logged out on the phone, or removed from Linked devices)",
                "banned": "banned by WhatsApp (the server refused the account: 403)"}


class GatewayDown(Exception):
    """the gateway is not running, or WhatsApp is not connected"""


def gateway(method, path, body=None, timeout=60):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        msg = (json.loads(e.read() or b"{}") or {}).get("error") or str(e)
        if e.code == 503:
            raise GatewayDown(msg)
        raise ValueError(f"{path} {e.code}: {msg}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise GatewayDown(repr(e)) from e


def text(it, stream=None):
    """Telegram's message (telegram_alerts.text) as WhatsApp text: *bold* category, mark + headline, the source and the
    article's link, 詳情 and the item page"""
    detail = item_url(it, stream)
    t = marks.text(it)
    return ((f"*【{it['category']}】*\n" if it.get("category") else "")
            + (f"({t}) " if t else "") + titles.plain(it) + "\n"
            + f"{it.get('website') or it.get('source') or ''} ↗ {it['url']}"
            + (f"\n詳情 {detail}" if detail else ""))


def cursors(streams):
    """the last stream_items.id sent per stream; a stream new to WhatsApp starts at its newest item"""
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
    return [(r["id"], titles.item_row(r)) for r in corpus.db().execute(
        f"""SELECT si.id, si.item, {titles.ITEM_COLS} FROM stream_items si {titles.ITEM_JOIN}
            WHERE si.stream=%s AND si.id>%s ORDER BY si.id LIMIT %s""", (stream, after, BATCH))]


def note_sent(sid):
    """when this stream's last send worked (kv whatsapp_sent_at {stream: time}): the monitor shows it, and a send
    error before it is history, not a live problem (monitor.send_error)"""
    try:
        at = corpus.kv_get("whatsapp_sent_at") or {}
        at[sid] = datetime.now(corpus.HKT).isoformat(timespec="seconds")
        corpus.kv_set("whatsapp_sent_at", at)
    except Exception:
        pass


def note_error(sid, err):
    """the latest send failure (kv whatsapp_error), shown on the monitor"""
    try:
        corpus.kv_set("whatsapp_error", {"at": datetime.now(corpus.HKT).isoformat(timespec="seconds"), "stream": sid,
                                          "error": str(err)[:300]})
    except Exception:
        pass


def linked():
    try:
        return gateway("GET", "/status").get("state") == "linked"
    except GatewayDown:
        return False


def send_due(streams):
    """post every unsent item, round robin across groups; → how many were posted. Nothing (and no cursor) moves while
    the device is not connected. A stream whose flag is off sends nothing and keeps its place."""
    streams = [s for s in streams if s.get("whatsapp_group")]
    if not streams or not linked():
        return 0
    sent = cursors(streams)
    queues = {s["id"]: pending(s["id"], sent[s["id"]]) for s in streams}
    group = {s["id"]: s["whatsapp_group"] for s in streams}
    n = 0
    while any(queues.values()) and not STOP.is_set():
        for sid, q in queues.items():
            if not q or STOP.is_set():
                continue
            if not linked():
                return n
            rid, it = q.pop(0)
            sent[sid] = rid
            corpus.kv_set(KEY, sent)  # at most once: the cursor moves first
            try:
                gateway("POST", "/send", {"jid": group[sid], "text": text(it, sid)})
                n += 1
                note_sent(sid)
                events.emit("alert", it.get("post_id"), channel="whatsapp", stream=sid, item=rid, ok=True)
            except (GatewayDown, ValueError) as e:
                print(f"whatsapp {sid}: item {rid} not sent: {e}", flush=True)
                note_error(sid, f"item {rid} not sent: {e}")
                events.emit("alert", it.get("post_id"), channel="whatsapp", stream=sid, item=rid, ok=False, error=str(e))
    return n


def check_alerts(st):
    """one Discord message when the device becomes unlinked or the number banned; again only after a link in between"""
    state = (st or {}).get("state")
    last = corpus.kv_get("whatsapp_alerted")
    if state in ALERT_STATES and last != state:
        who = st.get("me") or "YYC's number"
        if notify.deyao(f"YYC WhatsApp alerts stopped: the sender for {who} is {ALERT_STATES[state]}. "
                        f"Stream messages wait until it is linked again (whatsapp_alerts.py pair, news-stream README "
                        f"\"WhatsApp\")."):
            corpus.kv_set("whatsapp_alerted", state)
    elif state == "linked" and last:
        corpus.kv_set("whatsapp_alerted", None)


# ---- the running sender ----

held = {"proc": None}
LOCK = None  # podlock.Lock('whatsapp'): one connection of the linked device at a time


def start_gateway():
    env = dict(os.environ, WHATSAPP_GATEWAY_PORT=str(PORT))
    held["proc"] = subprocess.Popen(["node", os.path.join(HERE, "whatsapp", "gateway.js")], env=env)
    for _ in range(120):
        try:
            gateway("GET", "/status", timeout=5)
            return
        except GatewayDown:
            if held["proc"].poll() is not None:
                raise SystemExit(f"gateway exited with {held['proc'].returncode}")
            time.sleep(0.5)
    raise SystemExit("gateway did not start")


def heartbeat():
    while True:
        open(ALIVE, "w").close()
        p = held["proc"]
        if p is not None and p.poll() is not None and not STOP.is_set():
            print(f"gateway exited with {p.returncode}; exiting so the pod restarts", flush=True)
            os._exit(1)
        time.sleep(15)


def on_term(*_):
    STOP.set()
    WAKE.set()


def listen():
    """wake the sender the moment a stream item is filed (Deyao, 2026-10-23: the pipeline tighter): the live trigger
    on stream_items (corpus.SCHEMA live_stream_item) announces feed:<day> on channel yyc_live; IDLE stays the fallback.
    LISTEN needs a direct connection (a transaction pooler doesn't pass notifications; live.py does the same)"""
    import psycopg
    while not STOP.is_set():
        try:
            with psycopg.connect(corpus.DATABASE_URL, autocommit=True, application_name="yyc-whatsapp-wake",
                                 connect_timeout=5) as c:
                c.execute("LISTEN yyc_live")
                WAKE.set()  # an item filed while the listener was away (a switchover) is sent now, not at IDLE
                while not STOP.is_set():
                    if any(n.payload.startswith("feed:") for n in c.notifies(timeout=1)):
                        WAKE.set()
        except Exception as e:  # noqa: BLE001: the IDLE check still sends; reconnect
            print("whatsapp: listener failed, reconnecting:", repr(e)[:200], flush=True)
            STOP.wait(corpus.RETRY_GAP)


def run():
    signal.signal(signal.SIGTERM, on_term)
    open(STARTED, "w").close()  # ready while waiting for the lock: the old pod lets go only once this one is ready
    threading.Thread(target=heartbeat, daemon=True).start()
    global LOCK
    import podlock
    LOCK = podlock.Lock("whatsapp", STOP)  # lost to another copy: STOP, so this one closes its connection and exits
    if not LOCK.take():
        return
    start_gateway()
    STREAMS = all_streams()
    threading.Thread(target=listen, name="whatsapp-wake", daemon=True).start()
    while not STOP.is_set():
        try:
            check_alerts(gateway("GET", "/status"))
            with corpus.stage("whatsapp") as restarted:  # back after a stop: nothing filed before is sent
                if restarted:
                    skip_backlog(STREAMS)
                send_due(STREAMS)
        except Exception as e:
            print("whatsapp failed:", repr(e)[:300], flush=True)
            note_error(None, repr(e))
            STOP.wait(30)
        WAKE.wait(IDLE)  # an item filed during send_due set it: the next pass starts at once
        WAKE.clear()
    print("stopping: closing the gateway, releasing the lock", flush=True)
    p = held["proc"]
    p.terminate()
    try:
        p.wait(20)
    except subprocess.TimeoutExpired:
        p.kill()
    LOCK.release()
    os._exit(0)


def all_streams():
    return json.load(open(os.path.join(HERE, "streams.json")))


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "run":
        return run()
    if cmd == "status":
        print(json.dumps(gateway("GET", "/status"), ensure_ascii=False, indent=1))
    elif cmd == "pair":
        print(json.dumps(gateway("POST", "/pair", {"phone": argv[1]}, timeout=120), ensure_ascii=False))
    elif cmd == "invite":
        print("https://chat.whatsapp.com/" + gateway("GET", f"/invite?jid={argv[1]}")["code"])
    elif cmd == "groups":
        for s in all_streams():  # every stream (Deyao, 2026-10-16), with or without a Telegram channel
            if s.get("whatsapp_group"):
                code = gateway("GET", f"/invite?jid={s['whatsapp_group']}")["code"]
                print(s["id"], s["whatsapp_group"], "https://chat.whatsapp.com/" + code, "(exists)")
                continue
            g = gateway("POST", "/group", {"subject": f"YYC · {s.get('name') or s['id']}",
                                           "description": f"YYC 新聞監察：{s.get('name') or s['id']} 串流的每一則新項目（只限管理員發訊）"},
                        timeout=120)
            print(s["id"], g["jid"], "https://chat.whatsapp.com/" + g["invite"], "(new: add to streams.json)")
    elif cmd == "test":
        s = {x["id"]: x for x in all_streams()}[argv[1]]
        print(gateway("POST", "/send", {"jid": s["whatsapp_group"], "text": f"YYC {s.get('name') or s['id']} 測試訊息"}))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
