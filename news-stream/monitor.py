"""Deyao's monitor (Deyao, 2026-10-14: "a monitor (read only, no write) dashboard, locked to my account, about sources
health and account balance, and spending monitoring"). Read-only: it only reads Postgres, the Kubernetes API and the
providers' own billing/usage APIs (GET), and changes nothing anywhere.

Two halves:
  the page /app/monitor (the edge app: web-app/src/routes/app.monitor.tsx, -MonitorPage.tsx) and /app/monitor/data, for
           Deyao's Access account only (any other identity gets 404); /app is behind Cloudflare Access. Both read the
           data from Postgres (kv monitor:data, below): nothing on the read path calls this service.
  this     `gunicorn monitor:application` in the Deployment `monitor` (deploy/apps/monitor.yaml), which computes the
           panels and publishes each snapshot into kv monitor:data. It holds the provider keys the web side doesn't
           (IPROYAL_API, HETZNER_YYC_READ, AWS_YYC_MONITOR_*, CLOUDFLARE_YYC_MONITOR_TOKEN), reads the cluster with a read-only
           ServiceAccount, and nothing reaches it over the network (no Service; NetworkPolicy with no ingress). GET /data (its own copy), /healthz
           and /readyz answer only inside the pod (the probes, kubectl exec).

Every panel is computed by its own background loop (every TTL seconds, below) into a snapshot, published at once into
kv monitor:data as {panel: {"stored_at": <unix seconds>, "value": {…}}} (one panel per statement, so pods and loops
never overwrite each other's panels); a reader (published()) adds each panel's age, so it answers at once however slow
a provider or query is, and one failing provider never blanks the rest (a failed refresh keeps the last good snapshot,
also one another pod published, and says why). /readyz answers 200 only once every panel has its first snapshot. Each figure says where it comes from: "provider API" (the provider's own record, what an
expense claim can rest on) or "our log" (what this app recorded, e.g. OpenRouter's charged cost per call in ai_spend).

No budget-shaped figures (Deyao, 2026-10-15: "don't display when it runs out etc (the progress bar), because we removed
the budget logic"; 2026-10-16: no projections): spend per day/month, the last 6 h / 24 h, per provider, purpose, model
and job, and balances are shown; a cap's headroom, a run-out date or a projection are not. An error is current only
until something later worked (settle(), error_current, state_current): the page shows an old one muted, never red. A provider's requests actually failing for credit, balance or a limit
(the stops panel) are shown as the event they are, with the error the provider returned.

  python3 monitor.py            print the JSON once (in a pod with the keys)
"""
import csv, io, json, os, re, ssl, sys, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

import corpus
import costlog
import datefmt
import resi

HKT = corpus.HKT
UA = "yyc-monitor/1"
NS = os.environ.get("MONITOR_NAMESPACE", "yyc")
TTL = {"stops": 30, "sources": 120, "jobs": 60, "pipeline": 120, "telegram": 60, "whatsapp": 60, "balances": 300, "spending": 300,
       "wisers": 60, "catalogue": 300, "videos": 120, "cloudflare": 300, "corpus": 600,
       "database": 60, "storage": 1800}
QUIET_SHARE = 0.25  # a source is quiet when its last 24 h bring under this share of its usual day
PUBLISHED_RE = r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?([+-]\d{2}(:?\d{2})?|Z)?$"  # castable
QUIET_MIN = 3  # … and its usual day has at least this many posts (a source with one post a week is never "quiet")
IPROYAL_SUBUSER = "yycstream"  # YYC's one IPRoyal sub-user: every job's proxy traffic (resi.py) but YouTube's
# YYC's IPRoyal ISP IP (Deyao, 2026-10-16): all YouTube traffic (media.youtube_clip_audio); 30-day plan, auto-renew on;
# IPRoyal's Fair Usage allowance for it is 100 GB per 30 days (beyond it the IP keeps working at reduced speed).
# IPRoyal's API reports no traffic for ISP orders, so the figure is our own count (kv isp:bytes:<day>, resi.count_isp)
ISP_IP, ISP_ORDER, ISP_ALLOWANCE_BYTES = "81.181.135.61", 85121454, 100e9


def now():
    return datetime.now(HKT)


def iso(dt):
    return dt.isoformat(timespec="seconds")


# ---- snapshots: each panel computed by its own background loop, /data serves the last snapshot at once ----
# Live 2026-10-15: computing the panels inside the first /data request after a pod start took 81 s (the pipeline
# panel 43.6 s), over web.py's 40 s, so the page showed a 502. Now a request never computes: it serves what the loops
# last stored, each panel with its age; the pod reports ready (/readyz) only once every panel has a first snapshot, so
# a rollout never sends the page to an empty monitor.

_cache, _locks = {}, {k: threading.Lock() for k in TTL}
READY_AFTER = 180  # seconds: past this, /readyz says ready even if a panel still has no snapshot (it shows "pending")
_started = {"at": None}


def compute(name, if_missing=False):
    """compute one panel now, store it and publish it (kv monitor:data). A failure keeps the last good snapshot (its
    age grows; the published one when this process has none yet) and says why.
    if_missing: only when no snapshot exists yet (another thread may have just stored one)"""
    with _locks[name]:
        if if_missing and name in _cache:
            return _cache[name][1]
        t = time.time()
        try:
            v = PANELS[name]()
        except Exception as e:
            v = {"error": repr(e)[:400]}
        v["computed_at"], v["seconds"] = iso(now()), round(time.time() - t, 2)
        prev = _cache.get(name)
        if prev is None and "error" in v:
            prev = stored(name)  # e.g. a new pod whose first try fails: the snapshot the old pod published stays
        if "error" in v and prev and "error" not in prev[1]:
            snap = (prev[0], dict(prev[1], refresh_error=v["error"], refresh_failed_at=v["computed_at"]))
        else:
            snap = (time.time(), v)
        _cache[name] = snap
        publish(name, *snap)
        if name == "balances" and "error" not in v:  # the providers' own counters, kept for the cost profile
            costlog.snapshot(v.get("providers"))
        return snap[1]


# ---- published snapshots (kv monitor:data): what the page reads, without calling this service ----

KV = "monitor:data"


def publish(name, at, value):
    """store one panel's snapshot in kv monitor:data, leaving the other panels as they are"""
    try:
        corpus.db().execute(
            """INSERT INTO kv (k, v) VALUES (%s, jsonb_build_object(%s::text, %s::jsonb)) ON CONFLICT (k) DO UPDATE
               SET v = (CASE WHEN jsonb_typeof(kv.v) = 'object' THEN kv.v ELSE '{}'::jsonb END) || EXCLUDED.v""",
            (KV, name, corpus.J({"stored_at": at, "value": value})))
    except Exception as e:  # the database is down: the next refresh publishes again
        print(f"monitor: publishing {name} failed: {e!r}"[:300], file=sys.stderr)


def stored(name):
    """(stored_at, value) of a panel's published snapshot, or None"""
    try:
        x = (corpus.kv_get(KV) or {}).get(name)
    except Exception:
        return None
    return (float(x["stored_at"]), x["value"]) if isinstance(x, dict) and "value" in x else None


def published():
    """/app/monitor/data: the published snapshots in the shape of data(), each with its age now"""
    d = corpus.kv_get(KV) or {}
    d = d if isinstance(d, dict) else {}
    out = {}
    for k in PANELS:
        x = d.get(k)
        if isinstance(x, dict) and "value" in x:
            out[k] = dict(x["value"], age_seconds=round(time.time() - float(x["stored_at"]), 1))
        else:
            out[k] = {"pending": True, "error": "first snapshot still being computed"}
    out["at"] = iso(now())
    return out


def panel(name, fresh=False):
    """a panel's value for another panel (spending reads balances): the stored one, computed first if there is none"""
    hit = _cache.get(name)
    if hit and not fresh:
        return hit[1]
    return compute(name, if_missing=not fresh)


def snapshot(name):
    """the stored snapshot with its age in seconds, or {"pending": True} before the first one"""
    hit = _cache.get(name)
    if not hit:
        return {"pending": True, "error": "first snapshot still being computed"}
    return dict(hit[1], age_seconds=round(time.time() - hit[0], 1))


def data():
    return dict({k: snapshot(k) for k in PANELS}, at=iso(now()))


def refresh_all():
    """compute every panel once, in this thread (python3 monitor.py, tests)"""
    for k in PANELS:
        compute(k)


def ready():
    if all(k in _cache for k in PANELS):
        return True
    return _started["at"] is not None and time.time() - _started["at"] > READY_AFTER


def _loop(name, stop):
    """one panel: compute, wait its TTL, again; a slow panel never holds the others up"""
    while not stop.is_set():
        compute(name)
        stop.wait(TTL.get(name, 60))


# ---- HTTP (GET only) ----

def get(url, headers=None, timeout=25, raw=False):
    req = urllib.request.Request(url, headers=dict(headers or {}, **{"User-Agent": UA, "Accept": "application/json"}))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
    return b.decode("utf-8", "replace") if raw else json.loads(b)


def direct(req, timeout=20, context=None):
    """urlopen without the pod's HTTP(S)_PROXY (deploy/apps/outbound.yaml): Python's urllib ignores NO_PROXY's CIDR
    ranges, so a pod or node address (the pg pod's exporter, a kubelet) would go to the socks pool, which refuses
    private addresses (HTTP 403)"""
    hs = [urllib.request.ProxyHandler({})] + ([urllib.request.HTTPSHandler(context=context)] if context else [])
    return urllib.request.build_opener(*hs).open(req, timeout=timeout)


def _sa_get(url):
    """GET url with this pod's ServiceAccount token, TLS checked against the cluster's CA, never through the proxy"""
    sa = "/var/run/secrets/kubernetes.io/serviceaccount"
    tok = open(f"{sa}/token").read().strip()
    ctx = ssl.create_default_context(cafile=f"{sa}/ca.crt")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
    with direct(req, context=ctx) as r:
        return json.load(r)


def k8s(path):
    """the Kubernetes API with this pod's ServiceAccount (read-only Role in deploy/apps/monitor.yaml)"""
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    host = f"[{host}]" if ":" in host else host
    return _sa_get(f"https://{host}:{os.environ.get('KUBERNETES_SERVICE_PORT', '443')}{path}")


def kubelet(ip, path):
    """a node's kubelet (port 10250) with this pod's ServiceAccount: /stats/summary needs only `get nodes/stats`
    (ClusterRole yyc-monitor-nodes-read); its serving certificate is signed by the cluster's CA (k3s server-ca)"""
    return _sa_get(f"https://{f'[{ip}]' if ':' in ip else ip}:10250{path}")


def poll_state():
    return {r[0]: r[1] for r in corpus.db().execute("SELECT k, v FROM poll_state WHERE k IN ('last_poll', 'source_status')")}


def ago_h(ts):
    try:
        return (now() - datetime.fromisoformat(ts)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def settle(err, ok_times):
    """an error record {at, …} with `current`: False once a later success is recorded (ok_times: ISO times of the
    successes that count for it), with `resolved_at` = the first such success (Deyao, 2026-10-16: an old error must
    never look like a live problem). None stays None."""
    if not err:
        return err
    try:
        at = datetime.fromisoformat(err["at"])
    except (KeyError, TypeError, ValueError):
        return dict(err, current=True)
    later = []
    for x in ok_times or []:
        try:
            if x and datetime.fromisoformat(x.replace("Z", "+00:00")) > at:
                later.append((datetime.fromisoformat(x.replace("Z", "+00:00")), x))
        except (TypeError, ValueError):
            continue
    if not later:
        return dict(err, current=True)
    return dict(err, current=False, resolved_at=min(later)[1])


def send_error(err, sent_at):
    """a channel's last send error, settled by the sends that worked after it: that stream's, or any stream's for an
    error that names none (the sender's loop failing)"""
    sent_at = sent_at or {}
    sid = (err or {}).get("stream")
    return settle(err, [sent_at.get(sid)] if sid else list(sent_at.values()))


# ---- sources ----

def source_kinds():
    """sid → (name, kind): kind = direct | proxy | browser | social | google news (from the source definitions)"""
    import app  # the stream list and every source spec (sources.SOURCES + sources_extra + the Google News queries)
    out = {}
    for sp in list(app.sources.SOURCES) + list(app.EXTRA):
        sid, name = sp[0], sp[1]
        kind = "proxy" if len(sp) > 4 and sp[4] else "google news" if sid.startswith("gnews") else "direct"
        out[sid] = (name, kind)
    try:
        for doc in ("browser.json", "browser-sites.json"):
            for sid, site in (corpus.file_get(doc, {}) or {}).get("sites", {}).items():
                out[sid] = (site.get("name") or sid, "browser")
    except LookupError:
        pass
    out.update({"discuss": ("香港討論區 search", "proxy"), "facebook": ("Facebook pages", "proxy"),
                "instagram": ("Instagram / Threads", "proxy"),
                "tikhub": ("Social search (TikHub, AnyAPI)", "social"), "browser": ("browser job", "browser"),
                "gnews_decode": ("Google News links → publisher URL", "google news")})
    try:
        for sid, site in (corpus.file_get("sites.json", {}) or {}).get("sites", {}).items():
            out[sid] = (site.get("name") or sid, "site")
    except LookupError:
        pass
    out["sites"] = ("catalogue sites job (sites.json)", "site")
    out["sites_browser"] = ("catalogue sites, real browser (sites-browser job)", "browser")
    import watchlist
    out.update({"watch_" + p: (f"Watched accounts on {watchlist.PLATFORM_NAMES[p]}", "social")
                for p in watchlist.PLATFORMS if p not in ("facebook", "instagram", "threads")})
    return out


def usual_day(buckets):
    """a source's usual posts per day: the median of the full days before the last 24 h (buckets 1…7), from its
    first day with posts on, leaving out that first day (a new source's first read brings in its backlog); None under
    2 such days"""
    old = [b for b in buckets if b >= 1]
    if not old:
        return None
    first = max(old)  # bucket 8 holds posts from before the window: the source is older, days 1…7 all count
    days = [buckets.get(b, 0) for b in range(1, (7 if first >= 8 else first - 1) + 1)]
    if len(days) < 2:
        return None
    days.sort()
    m = len(days) // 2
    return float(days[m]) if len(days) % 2 else (days[m - 1] + days[m]) / 2


def sources():
    st = poll_state()
    status = st.get("source_status") or {}
    kinds = source_kinds()
    t = now()
    # live rows only: a row the archive backfill wrote has seen = its publish time (README, Backfill). Bucket 0 = the
    # last 24 h, 1…7 the days before (24 h each, counted back from now), 8 = older (only to tell the source's age)
    per = {}
    # Days by PUBLISH time (seen when a post has none or an unreadable one): a source's first read brings its whole
    # listing at one moment (20 columns of 信報 on 12 Oct 19:45 made a "usual 10.5" for a column a day), and those
    # posts fall on the days they were published
    for sid, b, n in corpus.db().execute(
            """SELECT sid, greatest(0, floor(extract(epoch FROM (%s::timestamptz - CASE WHEN published ~ %s
                   THEN published::timestamptz ELSE seen::timestamptz END)) / 86400))::int AS b, count(*)
               FROM posts WHERE seen >= %s AND thread IS NULL AND published IS DISTINCT FROM seen GROUP BY 1, 2""",
            (iso(t), PUBLISHED_RE, iso(t - timedelta(days=9)))):
        per.setdefault(sid, {})[b] = n
    rows = []
    # the browser job's own line: its posts are filed under its sites' ids, so its 24 h is the sum of theirs; it has no
    # usual day of its own (summed days would count each new site's first-day backlog): each site is judged quiet itself
    sites = [sid for sid, (_, kind) in kinds.items() if kind == "browser" and sid not in ("browser", "sites_browser")]
    for sid, v in status.items():
        name, kind = kinds.get(sid, (sid, "direct"))
        n24, usual = (per.get(sid) or {}).get(0, 0), usual_day(per.get(sid) or {})
        if sid == "browser":
            n24, usual = sum((per.get(s) or {}).get(0, 0) for s in sites), None
        listed = v.get("new_24h")  # its listing's URLs first read in 24 h, whichever source stored them (app.note_new_24h)
        quiet = bool(v.get("ok") and usual is not None and usual >= QUIET_MIN and n24 < usual * QUIET_SHARE
                     and (listed is None or listed < usual * QUIET_SHARE))
        off = v.get("off") or None  # off on purpose (a feature flag, the safety stop): never failing (Deyao, 2026-10-15)
        state = "off" if off else "failing" if not v.get("ok") else "quiet" if quiet else "ok"
        rows.append({"id": sid, "name": name, "kind": kind, "ok": bool(v.get("ok")), "state": state, "off": off,
                     "items": v.get("items"),
                     "error": v.get("error"), "at": v.get("at"), "last_ok": v.get("last_ok"), "fails": v.get("fails"),
                     "fail_since": v.get("fail_since"), "last_error": v.get("last_error"),
                     "last_error_at": v.get("last_error_at"), "error_current": state == "failing", "posts_24h": n24, "usual_per_day": usual, "listed_new_24h": listed,
                     "quiet": quiet})
    rows.sort(key=lambda r: (["failing", "quiet", "ok", "off"].index(r["state"]), r["name"].lower()))
    return {"last_poll": st.get("last_poll"), "last_poll_minutes": round((ago_h(st.get("last_poll")) or 0) * 60, 1)
            if st.get("last_poll") else None,
            "counts": {"total": len(rows), "failing": sum(r["state"] == "failing" for r in rows),
                       "off": sum(r["state"] == "off" for r in rows), "quiet": sum(r["quiet"] for r in rows),
                       "proxy": sum(r["kind"] == "proxy" for r in rows), "browser": sum(r["kind"] == "browser" for r in rows),
                       "site": sum(r["kind"] == "site" for r in rows)},
            "rule": f"quiet = answering, but its last 24 h brought under {int(QUIET_SHARE * 100)}% of its usual day, "
                    f"counting both the posts stored under its id and its listing's links first read in 24 h (another "
                    f"source may list a story first) (usual = the median of its posts per day by publish time over the "
                    f"7 days before, its first day and archive backfill "
                    f"rows left out; only sources with a usual day of {QUIET_MIN} or more)",
            "rows": rows}


# ---- Telegram ----

def telegram():
    import app
    sent = corpus.kv_get("telegram_sent") or {}
    sent_at = corpus.kv_get("telegram_sent_at") or {}  # telegram_alerts.send_due: when each stream's last send worked
    c = corpus.db()
    out = []
    for s in app.STREAMS:
        sid = s["id"]
        last = sent.get(sid)
        top = c.execute("SELECT max(id), count(*) FROM stream_items WHERE stream=%s", (sid,)).fetchone()
        backlog = c.execute("SELECT count(*) FROM stream_items WHERE stream=%s AND id > %s",
                            (sid, last or 0)).fetchone()[0] if s.get("telegram_chat") else None
        when = c.execute("SELECT item->>'seen' FROM stream_items WHERE id=%s", (last,)).fetchone() if last else None
        out.append({"stream": sid, "name": s.get("name"), "channel": bool(s.get("telegram_chat")), "last_sent_id": last,
                    "last_sent_item_seen": when[0] if when else None, "last_sent_at": sent_at.get(sid),
                    "newest_id": top[0], "items": top[1], "backlog": backlog})
    return {"streams": out, "last_error": send_error(corpus.kv_get("telegram_error"), sent_at),
            "note": "backlog = stream items filed after the last one sent (items under a raised threshold are skipped, not sent)"}


# ---- WiseSearch 轉接 (Deyao, 2026-10-20: every search costs YYC's WisersOne quota; the log is at /app/wisers/log) ----

WISERS_STATS = os.environ.get("WISERS_STATS_URL", "http://wisers.yyc.svc.cluster.local:8898/stats")


WISERS_ERRORS = 3  # failed calls (not logged out) in the relay's 15-minute window that make it unhealthy


def wisers_problems(d):
    """what is wrong with the relay from its stats (wisers/relay.py health()): its health check (one free WisersOne
    call every probe_secs) failed twice in a row or stopped, staff calls were refused as logged out, or several failed, in the last
    15 minutes. Logged out itself is the "wisers:session" alert, not repeated here."""
    return [text for _, text in wisers_findings(d)]


def wisers_down(d):
    """staff cannot search right now (red on the pages; Deyao, 2026-11-17: red = a service lost now): the health check
    failed twice in a row, or staff calls were refused as logged out. The relay's other problems (a few failed calls,
    a health check that stopped) are cautions, amber"""
    return any(down for down, _ in wisers_findings(d))


def wisers_findings(d):
    """[(down, text), …]: each problem of wisers_problems, down = staff cannot search because of it"""
    h, ss = d.get("health"), d.get("session") or {}
    if not h or ss.get("state") == "logged_out":
        return []
    out, pr = [], h.get("probe") or {}
    every = float(h.get("probe_secs") or 300)
    age = _age_secs(pr.get("at"))
    if not pr:
        started = _age_secs(h.get("started"))
        if every > 0 and started is not None and started > 2 * every + 120:
            out.append((False, f"no health check since the relay started {round(started / 60)} min ago"))
    elif age is not None and age > 3 * every + 120:
        out.append((False, f"no health check for {round(age / 60)} min (the last at {pr.get('at')})"))
    elif not pr.get("ok") and int(pr.get("fails") or 2) >= 2:
        # one failed check alone is not a problem (2026-11-11: one in 10-25 timed out at 60 s, the next answered); the
        # relay checks again LOGIN_GAP (60 s) after a failure, so two in a row = about 2 min without an answer. A relay
        # that predates "fails" counts as two.
        n = int(pr.get("fails") or 2)
        out.append((True, f"its health check failed{f' {n} times in a row' if pr.get('fails') else ''} "
                          f"({pr.get('outcome')}: {str(pr.get('error') or '')[:160]})"))
        quiet = _age_secs(ss.get("last_ok"))
        if quiet is not None and quiet > 2 * every + 120:  # "held" alone hid nine hours of HTTP 401s (2026-11-06)
            out.append((True, f"Wisers has answered no call for {round(quiet / 60)} min (the last at {ss.get('last_ok')})"))
    f = h.get("failures") or {}
    if f.get("logged_out"):
        out.append((True, f"{f['logged_out']} staff call(s) refused as logged out in the last 15 min"))
    other = sum(v for k, v in f.items() if k != "logged_out")
    if other >= WISERS_ERRORS:
        out.append((False, f"{other} call(s) failed in the last 15 min ({', '.join(f'{v} {k}' for k, v in f.items() if k != 'logged_out')})"))
    return out


def _age_secs(at):
    try:
        return (now() - datetime.fromisoformat(at)).total_seconds()
    except (TypeError, ValueError):
        return None


def wisers():
    """searches the relay sent to WisersOne today and this month (HKT; answers from saved responses left out), from its
    search log (wisers/relay.py, the stats port: counts only). WisersOne itself shows no search quota or counter."""
    d = get(WISERS_STATS, timeout=15)
    # the relay holds the session of YYC's WisersOne account (dedicated to it, 2026-10-21) and logs in again by itself;
    # "logged_out" = that login failed: Discord Deyao once (he can take it back by hand), once when it is held again
    ss = d.get("session") or {}
    if ss.get("state") in ("held", "logged_out"):
        ll = ss.get("last_login") or {}
        _alert("wisers:session", ss["state"] == "logged_out", now(),
               f"YYC WiseSearch relay: logged out of WisersOne since {ss.get('since')} (the automatic login failed"
               f"{': ' + str(ll.get('error'))[:200] if ll.get('error') else ''}); searches fail until it logs in. "
               "Take it back by hand: kubectl -n yyc exec deploy/wisers -- python3 /app/wisers/login.py /var/lib/wisers/login. "
               "Monitor: https://newswatch.yyc-internal.com/app/monitor#wisers",
               f"YYC WiseSearch relay: holds the WisersOne session again ({_hm(now())}).")
    problems = wisers_problems(d)
    if "health" in d:
        _alert("wisers:health", bool(problems), now(),
               "YYC WiseSearch relay unhealthy: " + "; ".join(problems) + ". Relay events: kubectl -n yyc exec deploy/wisers "
               "-- tail /var/lib/wisers/events.jsonl. Monitor: https://newswatch.yyc-internal.com/app/monitor#wisers",
               f"YYC WiseSearch relay: healthy again ({_hm(now())}): its health check answers and no call failed in the "
               "last 15 min.")
        d["health"] = dict(d["health"], problems=problems, down=wisers_down(d))
    return dict(d, log="/app/wisers/log",
                note="the relay's own count (searches.jsonl); WisersOne shows no search quota or remaining count")


# ---- YYC's Postgres and object storage (the live dashboard's Systems panel; Deyao, 2026-11-05: "add wiser status,
# database load, database free space, storage size (s3) to the system panel of the live monitor") ----
# database(): the CloudNativePG Cluster db/pg (deploy/db/postgres.yaml) from three read-only sources, nothing written:
#   - the instance's own metrics exporter (http://<pod>:9187/metrics, CloudNativePG's default queries run as its role
#     cnpg_metrics_exporter, member of pg_monitor): connections by state, the longest open transaction, database sizes,
#     transactions (pg_stat_database), WAL archiving (pg_stat_archiver, archive_status), the last base backup. That role
#     needs CONNECT on database app (closed to PUBLIC); granted by hand as postgres on 2026-11-05 (deploy/README.md
#     "The monitor's Postgres figures"). The policy pg-ingress admits the pod network on the instance's other ports.
#   - the node's kubelet (/stats/summary, `get nodes/stats`): the pod's CPU and memory, and the data volume's
#     filesystem as the instance sees it (capacity, used, available on the PVC pg-1, Hetzner Volume, yyc-block).
#   - the Kubernetes API (Role monitor-db-read in namespace db: pods get/list): which pod is primary, its node, its
#     memory limit.
# storage(): YYC's Hetzner Object Storage buckets: ListBuckets, then HEAD each bucket, whose answer carries Hetzner's
# (Ceph RGW's) own bucket stats (x-rgw-object-count, x-rgw-bytes-used): two requests per bucket, never a listing,
# every STORAGE_TTL. Credential: the project-scoped S3 credential yyc-videos (Secret video-s3; Hetzner's S3
# credentials can't be restricted to reading, so the code only ever sends GET/HEAD).

PG_NS, PG_CLUSTER, PG_EXPORTER = "db", "pg", 9187
PG_FREE_FLOOR = 20 * 2**30  # bytes free on pg's volume under which Deyao is told: more than max_wal_size (16 GB) of WAL a checkpoint burst can write
PG_CONN_SHARE = 0.8  # client connections at this share of max_connections: Deyao is told (240 of 300)
PG_ARCHIVE_STALE = 1800  # seconds without a WAL file archived (archive_timeout is 300 s and the stream always writes)
PG_BACKUP_STALE = (7 * 24 + 6) * 3600  # the weekly base backup (Sunday 03:00 HKT; Deyao, 2026-11-09) older than this
PG_CLUSTERS = ("pg", "magpie-pg")  # the restore tests and the archiving check cover both (deploy/clusters/talos/db)
RESTORE_STALE = 8 * 86400  # no successful weekly restore test (db/restore-test.yaml, Tuesdays) for this long
DUMPLOAD_STALE = 2 * 86400  # no successful daily load of the dumps (db/dump-load-test.yaml, 10:30 HKT) for this long
OFFSITE_STALE = 3 * 3600  # no successful hourly copy to R2 (db/offsite-copy.yaml) for this long (6 h after it was created)
STORAGE_TTL = TTL["storage"]
S3_ENDPOINT = os.environ.get("MONITOR_S3_ENDPOINT", "https://fsn1.your-objectstorage.com")
_pg_prev = {}  # the last transaction count read: {"at": unix seconds, "xacts": n, "start": postmaster start}


def prom(text):
    """Prometheus text format → [(name, {label: value}, float)]"""
    out = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([A-Za-z_:][\w:]*)(?:\{(.*)\})?\s+(\S+)", line)
        if not m:
            continue
        try:
            v = float(m.group(3))
        except ValueError:
            continue
        out.append((m.group(1), dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', m.group(2) or "")), v))
    return out


def _one(ms, metric, **labels):
    for n, lab, v in ms:
        if n == metric and all(lab.get(k) == x for k, x in labels.items()):
            return v
    return None


def _ts(v):
    """a unix-seconds metric → ISO HKT, or None for 0/absent"""
    return iso(datetime.fromtimestamp(v, HKT)) if v else None


def exporter(ip):
    """the instance's metrics (Prometheus text), straight to the pod's address"""
    req = urllib.request.Request(f"http://{ip}:{PG_EXPORTER}/metrics", headers={"User-Agent": UA})
    with direct(req) as r:
        return r.read().decode("utf-8", "replace")


def database():
    pods = k8s(f"/api/v1/namespaces/{PG_NS}/pods?labelSelector=cnpg.io/cluster%3D{PG_CLUSTER},"
               "cnpg.io/instanceRole%3Dprimary")["items"]
    if not pods:
        raise RuntimeError(f"no primary pod of Cluster {PG_NS}/{PG_CLUSTER}")
    pod = pods[0]
    name, ip, node = pod["metadata"]["name"], pod["status"].get("podIP"), pod["spec"].get("nodeName")
    pg_c = next((c for c in pod["spec"]["containers"] if c["name"] == "postgres"), {})
    mem_limit = _mib(((pg_c.get("resources") or {}).get("limits") or {}).get("memory"))
    ms = prom(exporter(ip))
    t = time.time()
    # connections: client backends by state (the exporter counts pg_stat_activity's client backends)
    states = {}
    longest = None
    for n, lab, v in ms:
        if n == "cnpg_backends_total":
            states[lab.get("state") or "?"] = states.get(lab.get("state") or "?", 0) + int(v)
        elif n == "cnpg_backends_max_tx_duration_seconds" and v and (longest is None or v > longest["seconds"]):
            longest = {"seconds": round(v, 1), "database": lab.get("datname"), "user": lab.get("usename"),
                       "application": lab.get("application_name"), "state": lab.get("state")}
    conns = sum(states.values())
    max_conn = _one(ms, "cnpg_pg_settings_setting", name="max_connections")
    # transactions per second since the last read (pg_stat_database, every database)
    xacts = sum(v for n, _, v in ms if n in ("cnpg_pg_stat_database_xact_commit", "cnpg_pg_stat_database_xact_rollback"))
    start = _one(ms, "cnpg_pg_postmaster_start_time")
    tps = None
    if _pg_prev.get("start") == start and t > _pg_prev.get("at", t) and xacts >= _pg_prev.get("xacts", xacts + 1):
        tps = round((xacts - _pg_prev["xacts"]) / (t - _pg_prev["at"]), 1)
    _pg_prev.update(at=t, xacts=xacts, start=start)
    dbs = sorted(({"name": lab.get("datname"), "bytes": int(v)} for n, lab, v in ms if n == "cnpg_pg_database_size_bytes"),
                 key=lambda x: -x["bytes"])
    wal_bytes = _one(ms, "cnpg_collector_pg_wal", value="size")
    # WAL archiving and the base backups (Barman Cloud plugin, bucket yyc-pg-pitr)
    last_arch, last_fail = _one(ms, "cnpg_pg_stat_archiver_last_archived_time"), _one(ms, "cnpg_pg_stat_archiver_last_failed_time")
    since_arch = _one(ms, "cnpg_pg_stat_archiver_seconds_since_last_archival")
    ready = _one(ms, "cnpg_collector_pg_wal_archive_status", value="ready")
    bk_ok = _one(ms, "barman_cloud_cloudnative_pg_io_last_available_backup_timestamp")
    bk_fail = _one(ms, "barman_cloud_cloudnative_pg_io_last_failed_backup_timestamp")
    archive = {"last_archived": _ts(last_arch), "last_failed": _ts(last_fail),
               "seconds_since_archived": round(since_arch) if since_arch is not None else None,
               "failed_count": _one(ms, "cnpg_pg_stat_archiver_failed_count"), "waiting": int(ready) if ready is not None else None,
               "last_backup": _ts(bk_ok), "last_failed_backup": _ts(bk_fail)}
    archive_bad = []
    if last_fail and (not last_arch or last_fail > last_arch):
        archive_bad.append(f"archiving a WAL file failed at {_hm(datetime.fromtimestamp(last_fail, HKT))} and none archived since")
    elif since_arch is not None and since_arch > PG_ARCHIVE_STALE:
        archive_bad.append(f"no WAL file archived for {round(since_arch / 60)} min")
    if bk_fail and (not bk_ok or bk_fail > bk_ok):
        archive_bad.append(f"the base backup failed at {_hm(datetime.fromtimestamp(bk_fail, HKT))}")
    elif bk_ok and t - bk_ok > PG_BACKUP_STALE:
        archive_bad.append(f"no base backup since {_hm(datetime.fromtimestamp(bk_ok, HKT))}")
    archive["problems"] = archive_bad
    # the pod's CPU and memory and the data volume, from the kubelet
    nd = k8s(f"/api/v1/nodes/{node}")
    node_ip = next((a["address"] for a in nd["status"].get("addresses") or [] if a["type"] == "InternalIP"), None)
    cores = nd["status"].get("capacity", {}).get("cpu")
    summ = kubelet(node_ip, "/stats/summary")
    ps = next((p for p in summ.get("pods") or [] if p["podRef"]["name"] == name and p["podRef"]["namespace"] == PG_NS), {})
    vol = next((v for v in ps.get("volume") or [] if (v.get("pvcRef") or {}).get("name") == name), {})
    cpu = (ps.get("cpu") or {}).get("usageNanoCores")
    mem = (ps.get("memory") or {}).get("workingSetBytes")
    space = {"pvc": (vol.get("pvcRef") or {}).get("name"), "capacity_bytes": vol.get("capacityBytes"),
             "used_bytes": vol.get("usedBytes"), "free_bytes": vol.get("availableBytes"), "floor_bytes": PG_FREE_FLOOR}
    out = {"pod": name, "node": node,
           "connections": {"total": conns, "max": int(max_conn) if max_conn else None, "by_state": states,
                           "waiting_on_locks": int(_one(ms, "cnpg_backends_waiting_total") or 0)},
           "longest_transaction": longest, "tps": tps,
           "cpu_cores": round(cpu / 1e9, 2) if cpu is not None else None, "node_cores": int(cores) if cores else None,
           "memory_mib": round(mem / 2**20) if mem is not None else None, "memory_limit_mib": mem_limit,
           "space": space, "databases": dbs, "wal_bytes": int(wal_bytes) if wal_bytes is not None else None,
           "archive": archive,
           "source": "the instance's metrics exporter (CloudNativePG, pg_monitor: pg_stat_activity, pg_stat_database, "
                     "pg_stat_archiver), the node's kubelet (/stats/summary: the pod's CPU, memory, the volume's filesystem)"}
    now_ = now()
    free = space["free_bytes"]
    if free is not None:
        _alert("db:space", free < PG_FREE_FLOOR, now_,
               f"YYC Postgres: {free / 2**30:.1f} GiB free on its volume ({space['pvc']}, {space['capacity_bytes'] / 2**30:.1f} GiB, "
               f"under the {PG_FREE_FLOOR / 2**30:.0f} GiB floor; databases {sum(d['bytes'] for d in dbs) / 2**30:.1f} GiB, WAL "
               f"{(wal_bytes or 0) / 2**30:.1f} GiB). A full volume stops every write: grow the PVC (deploy/db/postgres.yaml "
               "storage.size). Monitor: https://newswatch.yyc-internal.com/app/live",
               f"YYC Postgres: {free / 2**30:.1f} GiB free on its volume again ({_hm(now_)}).")
    if max_conn:
        _alert("db:connections", conns >= PG_CONN_SHARE * max_conn, now_,
               f"YYC Postgres: {conns} of {int(max_conn)} connections in use ({', '.join(f'{v} {k}' for k, v in sorted(states.items()))}); "
               f"at {int(max_conn)} new connections are refused. Monitor: https://newswatch.yyc-internal.com/app/live",
               f"YYC Postgres: {conns} of {int(max_conn)} connections in use again ({_hm(now_)}).")
    out["backups"] = backups(now_)
    _alert("db:archive", bool(archive_bad), now_,
           "YYC Postgres backups: " + "; ".join(archive_bad) + ". Point-in-time restore (bucket yyc-pg-pitr) lags until it "
           "works: kubectl -n db logs pg-1 -c plugin-barman-cloud. Monitor: https://newswatch.yyc-internal.com/app/live",
           f"YYC Postgres backups: WAL archiving and base backups work again ({_hm(now_)}).")
    return out


def backups(now_):
    """per Postgres cluster: whether CloudNativePG says WAL archiving works (the Cluster's condition ContinuousArchiving)
    and the weekly restore test's last run and last success (ConfigMap restore-test-<cluster>, written by
    db/restore-test.yaml). Discords Deyao once when archiving stops, when a restore test fails, or when none has
    succeeded for RESTORE_STALE; once again when it is fine."""
    out = {}
    for c in PG_CLUSTERS:
        st = {"archiving": None, "archiving_message": None, "restore_test": None, "last_ok": None}
        try:
            cl = k8s(f"/apis/postgresql.cnpg.io/v1/namespaces/{PG_NS}/clusters/{c}")
            ca = next((x for x in (cl.get("status") or {}).get("conditions") or [] if x.get("type") == "ContinuousArchiving"), None)
            if ca:
                st["archiving"], st["archiving_message"] = ca.get("status") == "True", ca.get("message")
        except Exception as e:  # noqa: BLE001: one source down leaves the rest
            st["error"] = f"{type(e).__name__}: {e}"[:200]
        try:
            data = (k8s(f"/api/v1/namespaces/{PG_NS}/configmaps/restore-test-{c}").get("data") or {})
            runs = [json.loads(x) for x in (data.get("history.jsonl") or "").splitlines() if x.strip()]
            st["restore_test"] = runs[-1] if runs else None
            st["last_ok"] = max((r["at"] for r in runs if r.get("ok")), default=None)
        except urllib.error.HTTPError as e:
            if e.code != 404:  # 404: no test has run yet
                st["error"] = f"restore test result: HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            st["error"] = f"{type(e).__name__}: {e}"[:200]
        out[c] = st
        if st["archiving"] is not None:
            _alert(f"db:archiving:{c}", not st["archiving"], now_,
                   f"YYC Postgres {c}: WAL archiving to the backup bucket has stopped ({st['archiving_message'] or 'no reason given'}). "
                   f"Point-in-time restore lags until it works: kubectl -n db describe cluster {c}",
                   f"YYC Postgres {c}: WAL archiving works again ({_hm(now_)}).")
        last, ok_at = st["restore_test"], st["last_ok"]
        stale = ok_at is None or (now_ - datetime.fromisoformat(ok_at.replace("Z", "+00:00"))).total_seconds() > RESTORE_STALE
        failed = bool(last) and not last.get("ok")
        if "error" not in st:
            _alert(f"db:restoretest:{c}", failed or stale, now_,
                   f"YYC Postgres {c}: the weekly restore test " + (f"failed: {last.get('why') or 'no reason recorded'}" if failed else
                   f"has not succeeded since {ok_at or 'it started'}") + f". Logs: kubectl -n db logs job/<the latest restore-test-{c} job>",
                   f"YYC Postgres {c}: the restore test passed ({_hm(now_)}: restored in {(last or {}).get('restore_seconds')} s).")
    # the daily load test of the logical dumps (db/dump-load-test.yaml)
    st = {"last": None, "last_ok": None}
    try:
        data = (k8s(f"/api/v1/namespaces/{PG_NS}/configmaps/dump-load-test").get("data") or {})
        runs = [json.loads(x) for x in (data.get("history.jsonl") or "").splitlines() if x.strip()]
        st["last"] = runs[-1] if runs else None
        st["last_ok"] = max((r["at"] for r in runs if r.get("ok")), default=None)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            st["error"] = f"dump load test result: HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        st["error"] = f"{type(e).__name__}: {e}"[:200]
    out["dumps"] = st
    # the hourly off-site copy to R2 (db/offsite-copy.yaml): the CronJob's last success
    off = {"last_success": None}
    try:
        cj = k8s(f"/apis/batch/v1/namespaces/{PG_NS}/cronjobs/pg-offsite-copy")
        off["last_success"] = (cj.get("status") or {}).get("lastSuccessfulTime")
        created = cj.get("metadata", {}).get("creationTimestamp")
        ref = off["last_success"] or created
        if ref:
            age = (now_ - datetime.fromisoformat(ref.replace("Z", "+00:00"))).total_seconds()
            bad = age > (OFFSITE_STALE if off["last_success"] else 2 * OFFSITE_STALE)
            _alert("db:offsite", bad, now_,
                   f"YYC Postgres: the off-site copy to R2 (bucket yyc-pg-offsite) has not succeeded since "
                   f"{off['last_success'] or 'it was created'}. Logs: kubectl -n db logs job/<the latest pg-offsite-copy job>",
                   f"YYC Postgres: the off-site copy to R2 works again ({_hm(now_)}).")
    except Exception as e:  # noqa: BLE001
        off["error"] = f"{type(e).__name__}: {e}"[:200]
    out["offsite"] = off
    if "error" not in st:
        last, ok_at = st["last"], st["last_ok"]
        stale = ok_at is None or (now_ - datetime.fromisoformat(ok_at.replace("Z", "+00:00"))).total_seconds() > DUMPLOAD_STALE
        failed = bool(last) and not last.get("ok")
        _alert("db:dumpload", failed or stale, now_,
               "YYC Postgres: the daily load test of the pg_dumps " + (f"failed: {last.get('why') or 'no reason recorded'}"
               if failed else f"has not passed since {ok_at or 'it started'}") + ". Logs: kubectl -n db logs job/<the latest dump-load-test job>",
               f"YYC Postgres: the pg_dumps load again ({_hm(now_)}).")
    return out


def storage():
    import s3
    ak, sk = os.environ.get("VIDEO_S3_ACCESS_KEY"), os.environ.get("VIDEO_S3_SECRET_KEY")
    if not (ak and sk):
        raise RuntimeError("VIDEO_S3_ACCESS_KEY / VIDEO_S3_SECRET_KEY (Secret video-s3) are not in the monitor pod's env")
    root = s3.Client(S3_ENDPOINT, "fsn1", ak, sk, "")
    with root._open("GET", timeout=30) as r:
        names = re.findall(r"<Name>([^<]+)</Name>", r.read().decode())
    out = []
    for n in sorted(names):
        try:
            with s3.Client(S3_ENDPOINT, "fsn1", ak, sk, n)._open("HEAD", timeout=30) as r:
                h = r.headers
            out.append({"name": n, "objects": int(h.get("x-rgw-object-count") or 0), "bytes": int(h.get("x-rgw-bytes-used") or 0)})
        except Exception as e:  # noqa: BLE001: one bucket unreadable (e.g. in another location), the rest stand
            out.append({"name": n, "error": repr(e)[:200]})
    ok = [b for b in out if "error" not in b]
    return {"buckets": sorted(out, key=lambda b: -(b.get("bytes") or 0)), "bytes": sum(b["bytes"] for b in ok),
            "objects": sum(b["objects"] for b in ok), "endpoint": S3_ENDPOINT,
            "source": "Hetzner Object Storage's own bucket stats (HEAD bucket: x-rgw-object-count, x-rgw-bytes-used), "
                      f"project yyc, every {STORAGE_TTL // 60} min"}


# ---- WhatsApp ----

# the number's primary phone: the Aliyun cloud phone YYC rents (README "WhatsApp groups")
WHATSAPP_PHONE = {"id": "acp-hln9ilimxqzruvccu", "group": "ag-hln9ilimxqzrt8xos", "region": "cn-shanghai"}


def whatsapp():
    """the linked device's state (kv whatsapp_status, written by whatsapp/gateway.js), the last send per stream group
    (kv whatsapp_sent) and the latest send error"""
    import whatsapp_alerts
    sent = corpus.kv_get("whatsapp_sent") or {}
    sent_at = corpus.kv_get("whatsapp_sent_at") or {}  # whatsapp_alerts.send_due: when each group's last send worked
    c = corpus.db()
    out = []
    for s in whatsapp_alerts.all_streams():
        if not s.get("whatsapp_group"):
            continue
        sid = s["id"]
        last = sent.get(sid)
        top = c.execute("SELECT max(id) FROM stream_items WHERE stream=%s", (sid,)).fetchone()[0]
        backlog = c.execute("SELECT count(*) FROM stream_items WHERE stream=%s AND id > %s",
                            (sid, last or 0)).fetchone()[0] if last is not None else None
        when = c.execute("SELECT item->>'seen' FROM stream_items WHERE id=%s", (last,)).fetchone() if last else None
        out.append({"stream": sid, "name": s.get("name"), "last_sent_id": last, "newest_id": top, "backlog": backlog,
                    "last_sent_item_seen": when[0] if when else None, "last_sent_at": sent_at.get(sid)})
    return {"status": corpus.kv_get("whatsapp_status"), "phone": WHATSAPP_PHONE, "streams": out,
            "last_error": send_error(corpus.kv_get("whatsapp_error"), sent_at),
            "note": "linked = the sender's linked device is connected; backlog = items filed after the last one sent"}


# ---- jobs and pipeline ----

# the workloads a pipeline flag switches off (flags.py OFF_WHY): shown "off: <why>", never failing or late
FLAG_JOBS = {"deployments": {"images": "pictures", "transcribe": "transcription"}, "cronjobs": {"broadcast": "transcription"}}
FLAG_NOTE = {"transcribe": "; YouTube subtitles still read (no model)"}


def _flag_off(kind, name):
    import flags
    f = FLAG_JOBS[kind].get(name)
    why = flags.off(f) if f else None
    return why + FLAG_NOTE.get(name, "") if why else None


LATE_FACTOR = 3  # a CronJob is late after this many of its intervals (plus its deadline) without a success
DOWN_AFTER = 600  # seconds a Deployment may have fewer ready pods than it asks for before it counts as down
ALERTS_KV = "monitor:alerts"  # {"cronjob:<name>" | "deployment:<name>": {"since": iso, "sent": bool}}


def _cron_field(f, lo, hi):
    out = set()
    for part in f.split(","):
        rng, _, step = part.partition("/")
        a, b = (lo, hi) if rng == "*" else (int(rng.split("-")[0]), int(rng.split("-")[-1]) if "-" in rng or step else int(rng))
        if step and rng != "*" and "-" not in rng:
            b = hi
        out.update(range(a, b + 1, int(step or 1)))
    return sorted(out)


def interval_minutes(schedule):
    """the longest gap in minutes between two runs of a cron schedule (minute and hour fields; the CronJobs here run
    every day)"""
    f = schedule.split()
    times = [h * 60 + m for h in _cron_field(f[1], 0, 23) for m in _cron_field(f[0], 0, 59)]
    return max(b - a for a, b in zip(times, times[1:] + [times[0] + 1440]))


def _alert(key, bad, since, text_bad, text_ok):
    """Discord once when `key` goes bad (after it has been bad since `since`, a datetime), once when it is good again;
    state in kv ALERTS_KV, under the lock 'monitor alerts' (a rollout's two monitor pods send once)"""
    import notify
    with corpus.lock("monitor alerts", wait=False) as got:
        if not got:
            return
        st = corpus.kv_get(ALERTS_KV) or {}
        cur = st.get(key)
        import events  # the live dashboard's Problems panel
        if bad and not cur:
            st[key] = {"since": iso(since)}
            notify.deyao(text_bad)
            events.emit("problem", None, what=key.split(":")[0], key=key, state="failing", since=iso(since), reason=text_bad)
        elif not bad and cur:
            st.pop(key)
            notify.deyao(text_ok)
            events.emit("problem", None, what=key.split(":")[0], key=key, state="recovered", since=cur.get("since"),
                        reason=text_ok)
        else:
            return
        corpus.kv_set(ALERTS_KV, st)


def _limit(spec):
    c = (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec", {}).get("containers") or [{}]
    return ((c[0].get("resources") or {}).get("limits") or {}).get("memory")


def _mib(q):
    """a Kubernetes memory quantity → MiB"""
    if not q:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(Ki|Mi|Gi|K|M|G)?", str(q))
    if not m:
        return None
    f = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "K": 1000 / 1048576, "M": 1e6 / 1048576, "G": 1e9 / 1048576, None: 1 / 1048576}
    return round(float(m.group(1)) * f[m.group(2)])


def _hm(dt):
    """a time in the monitor's English alerts: "Tue 20 Oct 14:05 HKT" """
    return datefmt.format("dateTime", dt, lang="en") + " HKT"


def jobs():
    cjs = k8s(f"/apis/batch/v1/namespaces/{NS}/cronjobs")["items"]
    js = k8s(f"/apis/batch/v1/namespaces/{NS}/jobs")["items"]
    deps = k8s(f"/apis/apps/v1/namespaces/{NS}/deployments")["items"]
    by_owner = {}
    for j in js:
        for o in j["metadata"].get("ownerReferences") or []:
            if o.get("kind") == "CronJob":
                by_owner.setdefault(o["name"], []).append(j)
    t = now()
    out = []
    peaks = {r[0].split(":", 1)[1]: max((x.get("peak_mib") or 0) for x in r[1]) for r in corpus.db().execute(
        "SELECT k, v FROM kv WHERE k LIKE 'jobpeak:%%' AND jsonb_typeof(v) = 'array' AND jsonb_array_length(v) > 0")}
    for cj in cjs:
        name, spec, st = cj["metadata"]["name"], cj["spec"], cj.get("status") or {}
        runs = sorted(by_owner.get(name, []), key=lambda j: j["metadata"]["creationTimestamp"], reverse=True)

        def state(j):
            s = j.get("status") or {}
            if s.get("succeeded"):
                return "succeeded"
            if s.get("failed") and not s.get("active"):
                return "failed"
            return "running" if s.get("active") else "pending"
        last = runs[0] if runs else None
        ok_at = st.get("lastSuccessfulTime")
        deadline = ((spec.get("jobTemplate") or {}).get("spec") or {}).get("activeDeadlineSeconds") or 0
        late_after = LATE_FACTOR * interval_minutes(spec["schedule"]) + round(deadline / 60)
        late = None
        off = _flag_off("cronjobs", name)
        if not spec.get("suspend") and not off:
            ref = ok_at or cj["metadata"].get("creationTimestamp")  # never succeeded: late once it had its chances
            gap = (t - datetime.fromisoformat(ref.replace("Z", "+00:00"))).total_seconds() / 60 if ref else None
            late = gap is None or gap > late_after
        finished = [r for r in runs if state(r) in ("succeeded", "failed")]
        recent = [{"state": state(r), "start": (r.get("status") or {}).get("startTime") or r["metadata"]["creationTimestamp"],
                   "reason": next((c.get("reason") for c in (r.get("status") or {}).get("conditions") or []
                                   if c.get("type") == "Failed"), None)} for r in runs[:6]]
        out.append({"name": name, "schedule": spec["schedule"], "suspended": bool(spec.get("suspend")), "off": off,
                    "last_schedule": st.get("lastScheduleTime"), "last_success": ok_at, "active": len(st.get("active") or []),
                    "last_run": {"state": state(last), "start": (last.get("status") or {}).get("startTime"),
                                 "end": (last.get("status") or {}).get("completionTime")} if last else None,
                    "last_finished": state(finished[0]) if finished else None,
                    "recent": recent, "late": late, "late_after_minutes": late_after,
                    "memory_peak_mib": peaks.get(name), "memory_limit_mib": _mib(_limit(spec))})
        why = next((f"{r['state']} ({r['reason']})" if r["reason"] else r["state"] for r in recent if r["state"] == "failed"), None)
        _alert(f"cronjob:{name}", bool(late), t,
               f"YYC news stream: CronJob {name} has not succeeded for over {late_after} min (it runs every "
               f"{interval_minutes(spec['schedule'])} min; last success "
               f"{_hm(datetime.fromisoformat(ok_at.replace('Z', '+00:00'))) if ok_at else 'never'}"
               f"{'; last failed run: ' + why if why else ''}). Monitor: https://newswatch.yyc-internal.com/app/monitor",
               f"YYC news stream: CronJob {name} succeeded again ({_hm(t)}).")
    out.sort(key=lambda r: r["name"])
    st = corpus.kv_get(ALERTS_KV) or {}
    dl = []
    for d in deps:
        name = d["metadata"]["name"]
        want, ready = d["spec"].get("replicas") or 0, (d.get("status") or {}).get("readyReplicas") or 0
        off = _flag_off("deployments", name)
        short = bool(want) and ready < want and not off
        since = datetime.fromisoformat(st[f"short:{name}"]) if short and f"short:{name}" in st else t
        down = short and (t - since).total_seconds() > DOWN_AFTER
        dl.append({"name": name, "replicas": d["spec"].get("replicas"), "ready": ready,
                   "updated": (d.get("status") or {}).get("updatedReplicas") or 0,
                   "image": ((d["spec"]["template"]["spec"]["containers"] or [{}])[0].get("image") or "").split("/")[-1],
                   "off": off, "down": down})
        if short != (f"short:{name}" in st):  # when it first had too few ready pods: down after DOWN_AFTER of that
            with corpus.lock("monitor alerts", wait=False) as got:
                if got:
                    cur = corpus.kv_get(ALERTS_KV) or {}
                    if short:
                        cur[f"short:{name}"] = iso(t)
                    else:
                        cur.pop(f"short:{name}", None)
                    corpus.kv_set(ALERTS_KV, cur)
        _alert(f"deployment:{name}", down, since,
               f"YYC news stream: Deployment {name} has had {ready} of {want} pods ready since {_hm(since)}. "
               "Monitor: https://newswatch.yyc-internal.com/app/monitor",
               f"YYC news stream: Deployment {name} has all {want} pods ready again ({_hm(t)}).")
    return {"cronjobs": out, "deployments": sorted(dl, key=lambda d: d["name"])}


FT_TYPES = ("News", "Newspaper", "Magazine", "Official", "Blog", "Forum")  # = fulltext_fetch.SOURCE_TYPES + forums


def fulltext_sources(hours=24):
    """the full-text rate per source (sid) over the last `hours` (Deyao, 2026-10-20: a source that regresses must show):
    posts, full (text beyond the title), missing (read, nothing kept or only the title again: titles.title_only),
    pending (page not read yet), rate = full / the posts whose page was read, in %; worst first. Only each text's
    head is fetched: a text longer than its title by more than titles.TITLE_SLACK letters is never title-only."""
    import fulltext_fetch, titles
    since = iso(max(now() - timedelta(hours=hours), datetime.fromisoformat(corpus.FLOOR)))
    rows = corpus.db().execute(
        """SELECT sid, source, source_type AS type, url, title, left(coalesce(text, ''), length(coalesce(title, '')) + 200) AS head,
                  cached_at IS NULL AND coalesce(btrim(text), '') = '' AS pending
           FROM posts WHERE seen >= %s AND thread IS NULL AND source_type = ANY(%s)""", (since, list(FT_TYPES))).fetchall()
    by = {}
    for r in rows:
        d = by.setdefault(r["sid"] or "?", {"sid": r["sid"] or "?", "source": r["source"], "posts": 0, "full": 0,
                                             "missing": 0, "pending": 0})
        d["posts"] += 1
        if r["pending"]:
            d["pending"] += 1
        elif titles.title_only(r["head"], r["title"]) and not (r["type"] == "Forum" and (r["head"] or "").strip()
                                                               and not fulltext_fetch.forum_page({"source_type": "Forum", "url": r["url"]})):
            # a forum thread's opening post that is only its title is still its whole text (香港討論區: 「史卓豐…」)
            d["missing"] += 1
        else:
            d["full"] += 1
    for d in by.values():
        read = d["full"] + d["missing"]
        d["rate"] = round(100 * d["full"] / read) if read else None
    return sorted(by.values(), key=lambda d: (d["rate"] if d["rate"] is not None else 101, -d["posts"]))


def pipeline():
    import streams, fulltext_fetch
    c = corpus.db()
    t = now()
    st = poll_state()
    sc = streams.settings()
    # the queue: stage scoring's work only (streams.due keeps to corpus.seen_cond); backlog posts (first_run: a
    # source's first read, or its first after a gap) apart: they wait for a manual run
    dq = streams.due_counts(sc)
    scoring = {"since": sc.get("since"), "comments": bool(sc.get("comments")), "awaiting": dq["live"],
               "first_run": dq["first_run"], "oldest_waiting": dq["oldest_live"],
               "last_scored": c.execute("SELECT max(at) FROM scored").fetchone()[0]}
    hist = history_scoring()
    two = iso(max(t - timedelta(days=2), datetime.fromisoformat(corpus.cutoff("fulltext"))))
    types = list(fulltext_fetch.SOURCE_TYPES)
    # two index-only counts instead of one pass over every post's text (9.7 s live, 2026-10-15): the posts without text
    # come from the small partial index posts_doc_notext, the totals from posts_doc (corpus.SCHEMA)
    # waiting = stage fulltext's work (corpus.seen_cond: its backlog is never read)
    nt = c.execute(f"""SELECT count(*) FILTER (WHERE text IS NULL AND cached_at IS NULL AND seen >= %s),
                             count(*) FILTER (WHERE text IS NULL AND cached_at IS NULL AND {corpus.seen_cond("posts", "fulltext")}),
                             count(*)
                      FROM posts WHERE thread IS NULL AND (text IS NULL OR text = '') AND source_type = ANY(%s)""",
                   (two, types)).fetchone()
    tot = c.execute("SELECT count(*), max(cached_at) FROM posts WHERE thread IS NULL AND source_type = ANY(%s)",
                    (types,)).fetchone()
    fulltext = {"waiting_recent_2d": nt[0], "waiting_all": nt[1], "with_text": tot[0] - nt[2], "posts": tot[0],
                "last_read": tot[1],
                "proxy_pages_today": corpus.kv_get(f"fulltext:proxy:{t:%Y-%m-%d}", 0),
                "sources": fulltext_sources()}
    replies = {s: corpus.kv_get(f"replies:{s}:status") for s in ("lihkg", "discuss")}
    import forum_fetch  # the catalogue forums' thread readers (CronJob forums): one line per forum, off ones say why
    for k, v in forum_fetch.statuses().items():
        if v:
            replies[v.get("name") or k] = dict(v, skipped=f"off: {v['off']}" if v.get("off") else None)
    archive = {k[len("backfill:"):]: {x: y for x, y in (v or {}).items() if x != "todo"}
               for k, v in c.execute("SELECT k, v FROM kv WHERE k LIKE 'backfill:%%' ORDER BY k")}
    try:
        runs = corpus.file_get("social_runs.json")
    except LookupError:
        runs = []
    try:
        th = corpus.file_get("tikhub.json")
        social_failing = sorted({k for k, v in (th.get("checks") or {}).items() if not v.get("ok")})
    except LookupError:
        social_failing = []
    day = t.strftime("%Y-%m-%d")
    # queued = clips of posts seen since the restart cutoff (corpus.media_cond): older clips are never transcribed
    live = corpus.media_cond("media", "transcription")
    q = c.execute(f"""SELECT count(*) FILTER (WHERE status='queued' AND {live}) AS queued,
                            count(*) FILTER (WHERE status='done' AND captions IS NOT NULL AND done_at >= %s) AS captions_24h,
                            count(*) FILTER (WHERE status='no_direct' AND done_at >= %s) AS no_direct_24h,
                            count(*) FILTER (WHERE status='no_direct') AS no_direct,
                            count(*) FILTER (WHERE status='queued' AND next_try IS NOT NULL AND {live}) AS waiting_host,
                            count(*) FILTER (WHERE status='done') AS done, count(*) FILTER (WHERE status='failed') AS failed,
                            count(*) FILTER (WHERE status='done' AND done_at >= %s) AS done_24h,
                            coalesce(sum(duration) FILTER (WHERE status='done' AND done_at >= %s), 0) AS seconds_24h,
                            min(newest) FILTER (WHERE status='queued' AND {live}) AS oldest_queued, max(done_at) AS last_done
                     FROM media""", (iso(t - timedelta(days=1)),) * 4).fetchone()
    hosts = [dict(r) for r in c.execute(f"""SELECT host, count(*) FILTER (WHERE status='done') AS done,
                                                  count(*) FILTER (WHERE status='queued' AND {live}) AS queued,
                                                  count(*) FILTER (WHERE status='failed') AS failed
                                           FROM media WHERE first_seen >= %s GROUP BY host ORDER BY 2 DESC, 3 DESC""",
                                        (iso(t - timedelta(days=1)),))]
    spent = c.execute("SELECT coalesce(sum(usd), 0) FROM ai_spend WHERE purpose='transcribe' AND day=%s", (day,)).fetchone()[0]
    scan = c.execute("SELECT count(*), max(at) FROM media_scan WHERE at >= %s", (iso(t - timedelta(days=1)),)).fetchone()
    transcribe = dict(q, minutes_24h=round(float(q["seconds_24h"]) / 60, 1), usd_today=float(spent), hosts_24h=hosts,
                      pages_scanned_24h=scan[0], last_scan=scan[1])
    transcribe.pop("seconds_24h")
    import flags
    # off on purpose (flags `transcription` / `pictures`, Deyao 2026-10-20: cost): "off: <why>", never failing
    transcribe["off"] = flags.off("transcription")
    transcribe["captions_only_24h"] = c.execute(
        f"SELECT count(*) FROM media WHERE status='queued' AND captions_at >= %s AND captions IS NOT NULL AND {live}",
        (iso(t - timedelta(days=1)),)).fetchone()[0]
    import images as imgs
    th = imgs.threshold()
    # pictures waiting = of posts seen since the restart cutoff (images.due keeps to it)
    # hash IS NULL in the WHERE: the post_images_due index (tries included) and posts_id_seen (seen) answer it without
    # reading either table's pages (it read 700 MB every 2 min, 2026-10-27)
    pi = c.execute(f"""SELECT count(*) FILTER (WHERE tries < %s) AS due, count(*) FILTER (WHERE tries >= %s) AS failed
                      FROM post_images pi JOIN posts p ON p.id = pi.post_id
                      WHERE pi.hash IS NULL AND {corpus.seen_cond("p", "pictures")}""",
                   (imgs.TRIES, imgs.TRIES)).fetchone()
    im = c.execute("""SELECT count(*) AS scored, count(*) FILTER (WHERE score >= %s) AS meaningful,
                             count(*) FILTER (WHERE at >= %s) AS scored_24h,
                             count(*) FILTER (WHERE at >= %s AND score >= %s) AS meaningful_24h, max(at) AS last
                      FROM images""", (th, iso(t - timedelta(days=1)), iso(t - timedelta(days=1)), th)).fetchone()
    ispent = c.execute("SELECT coalesce(sum(usd), 0) FROM ai_spend WHERE purpose='images' AND day=%s", (day,)).fetchone()[0]
    iscan = c.execute("SELECT count(*) FROM image_scan WHERE at >= %s", (iso(t - timedelta(days=1)),)).fetchone()[0]
    pictures = dict(pi, **im, threshold=th, model=imgs.MODEL, usd_today=float(ispent), posts_looked_at_24h=iscan,
                    off=flags.off("pictures"))
    db = c.execute("SELECT pg_database_size(current_database()), (SELECT reltuples::bigint FROM pg_class WHERE relname='posts')").fetchone()
    import follow
    links_followed = follow.monitor()  # link following (follow.py): links of the posts that reached a stream
    lim = corpus.kv_get("openrouter_limit")  # ai.py's hard stop: OpenRouter's requests failing for the key's limit or credit
    return {"ai_limit": lim or None, "social_limit": social_limit(), "watch": watching(),
            "poller": {"last_poll": st.get("last_poll"),
                       "minutes_ago": round(ago_h(st.get("last_poll")) * 60, 1) if st.get("last_poll") else None},
            "scoring": scoring, "history_scoring": hist, "fulltext": fulltext, "transcribe": transcribe, "pictures": pictures,
            "replies": replies, "follow": links_followed,
            "archive_backfill": archive, "social": {"last_run": runs[-1] if runs else None, "failing_checks": social_failing},
            "database": {"bytes": db[0], "posts_estimate": db[1]}}


def history_scoring():
    """the history scorer's last recorded state (kv score_backfill). `state_current`: a "limit" state counts only while
    OpenRouter's stop is still recorded (kv openrouter_limit, cleared by the first request that works again); after
    that it is a past state, shown with its age, never as a live problem"""
    import streams
    bf = corpus.kv_get("score_backfill") or {}
    hist = {k: bf.get(k) for k in ("on", "state", "left", "posts", "at", "error")}
    hist["state_current"] = not (hist.get("state") == "limit" and not corpus.kv_get("openrouter_limit"))
    hist["spent_usd"] = float(corpus.db().execute("SELECT coalesce(sum(usd), 0) FROM ai_spend WHERE purpose = ANY(%s)",
                                                  ([streams.BACKFILL_PURPOSE, streams.BACKFILL_PURPOSE + streams.VERIFY],)).fetchone()[0])  # both stages
    return hist


def watching():
    """the watched accounts (watchlist.json): accounts per group and platform, how many answer, the day's metered spend
    against the safety stop and whether it is paused"""
    import watchlist
    rows = watchlist.load()
    by = {}
    for r in rows:
        g = by.setdefault(r["platform"], {"accounts": 0, "groups": {}})
        g["accounts"] += 1
        g["groups"][r["group"]] = g["groups"].get(r["group"], 0) + 1
    try:
        w = corpus.file_get("watch.json")
    except LookupError:
        w = {}
    for st in (w.get("accounts") or {}).values():
        g = by.get(st.get("platform"))
        if g is not None:
            g["ok"] = g.get("ok", 0) + (1 if st.get("ok") else 0)
            g["failing"] = g.get("failing", 0) + (1 if st.get("ok") is False and not st.get("off_by") else 0)
            if st.get("off"):
                g["off"] = st["off"]
    week = [(now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    return {"platforms": by, "accounts": len(rows), "last_run": w.get("fetched"), "posts_in_file": len(w.get("items") or []),
            "paused": corpus.kv_get("watch_paused"),
            "today": watchlist.spend(), "days": {d: watchlist.spend(d)["total"] for d in week[1:]},
            "posts_24h": corpus.db().execute("SELECT count(*) FROM posts WHERE sid LIKE 'watch\\_%%' AND seen >= %s",
                                             (iso(now() - timedelta(days=1)),)).fetchone()[0]}


def social_limit():
    """tikhub_fetch's safety stop: {provider: {since, at, message, notified}} for TikHub / AnyAPI requests failing for the balance"""
    return corpus.kv_get("social_limit") or None


# ---- stops: a provider's requests failing for credit, balance or a limit right now (an event, not a projection) ----

STOP_NAMES = {"openrouter": "OpenRouter", "tikhub": "TikHub", "anyapi": "AnyAPI", "capsolver": "CapSolver",
              "2captcha": "2Captcha", "iproyal": "IPRoyal (yycstream)"}


# resi.JOB (the script's name) → what the monitor calls it
PROXY_JOBS = {"app": "poller (信報, 香港討論區 search, Google News)", "media": "transcriber", "replies_fetch": "香港討論區 / LIHKG",
              "fulltext_fetch": "full text", "cache_fetch": "article cache", "comments_fetch": "comments",
              "fb_fetch": "Facebook pages", "ig_fetch": "Instagram / Threads", "paywall": "paywalled pages",
              "broadcast_fetch": "broadcasts", "backfill": "backfill", "links": "links"}


def stop_tag(message):
    """a short tag for the error the provider returned: "out of credit", "balance used up", "limit reached", or the
    HTTP status when the message names nothing more"""
    m = (message or "").lower()
    if "traffic" in m:
        return "out of traffic"
    if "credit" in m:
        return "out of credit"
    if "balance" in m:
        return "balance used up"
    if "limit" in m:
        return "limit reached"
    code = re.search(r"\b([45]\d\d)\b", m)
    return f"HTTP {code.group(1)}" if code else "requests failing"


def error_text(message):
    """the provider's error as it said it, its first sentence ('Insufficient credits. Add more using …' gives
    'Insufficient credits')"""
    first = re.split(r"(?<=[.!?])\s|\n", (message or "").strip(), maxsplit=1)[0].rstrip(".")
    return first[:120] or None


# A stop clears itself once the provider answers again (Deyao, 2026-10-20): a job clears its record with its next request
# that works, but a job that sends nothing while stopped (no archive.today captcha to solve, a paused run) never would,
# so while a record is there the monitor asks the provider's own read-only API whether it can serve requests now, at
# most every PROBE_EVERY seconds per provider, and clears the record on a yes. No key here to ask with, an error, or
# nothing left: the record stays (a job's next failure renews it either way).
PROBE_EVERY = 300
_probed = {}  # provider → unix time of its last probe


def _solver_has_balance(env, url):
    key = os.environ.get(env)
    if not key:
        return None
    b = post_read(url, {"clientKey": key})
    return not b.get("errorId") and float(b.get("balance") or 0) > 0


def _openrouter_serves():
    key = os.environ.get("OPENROUTER_API")
    if not key:
        return None
    k = get("https://openrouter.ai/api/v1/key", {"Authorization": "Bearer " + key})["data"]
    if k.get("limit_remaining") is not None and float(k["limit_remaining"]) <= 0:
        return False  # the key's own limit
    mgmt = os.environ.get("OPENROUTER_MANAGEMENT_KEY")
    if not mgmt:
        return None  # the account's credit can't be read without the management key
    c = get("https://openrouter.ai/api/v1/credits", {"Authorization": "Bearer " + mgmt})["data"]
    return float(c["total_credits"]) - float(c["total_usage"]) > 0


def _tikhub_has_balance():
    key = os.environ.get("TIKHUB_API")
    if not key:
        return None
    u = get("https://api.tikhub.io/api/v1/tikhub/user/get_user_info", {"Authorization": "Bearer " + key})["user_data"]
    return float(u.get("balance") or 0) > 0  # the search endpoints refuse the free daily credit (402)


def _anyapi_has_balance():
    key = os.environ.get("ANYAPI_API_KEY")
    if not key:
        return None
    return float(get("https://api.getanyapi.com/v1/balance", {"Authorization": "Bearer " + key}).get("usd") or 0) > 0


def _iproyal_has_traffic():
    key = os.environ.get("IPROYAL_API")
    if not key:
        return None
    subs = get("https://resi-api.iproyal.com/v1/residential-subusers?per_page=100", {"Authorization": "Bearer " + key})
    return sum(float(s.get("traffic_available") or 0) for s in subs.get("data", [])
               if s.get("username") == IPROYAL_SUBUSER) > 0


def _clear_social(prov):
    lim = dict(corpus.kv_get("social_limit") or {})
    if lim.pop(prov, None):
        corpus.kv_set("social_limit", lim or None)


def _clear_solver(prov):
    lim = dict(corpus.kv_get("solver_limit") or {})
    if lim.pop(prov, None):
        corpus.kv_set("solver_limit", lim or None)


# provider → (does it serve requests now?, clear its record)
PROBES = {
    "openrouter": (_openrouter_serves, lambda: corpus.kv_set("openrouter_limit", None)),
    "tikhub": (_tikhub_has_balance, lambda: _clear_social("tikhub")),
    "anyapi": (_anyapi_has_balance, lambda: _clear_social("anyapi")),
    "capsolver": (lambda: _solver_has_balance("CAPSOLVER_API", "https://api.capsolver.com/getBalance"),
                  lambda: _clear_solver("capsolver")),
    "2captcha": (lambda: _solver_has_balance("TWOCAPTCHA_API", "https://api.2captcha.com/getBalance"),
                 lambda: _clear_solver("2captcha")),
    "iproyal": (_iproyal_has_traffic, lambda: corpus.kv_set("proxy_out", None)),
}


def clear_answered(pids):
    """probe each recorded stop's provider (at most every PROBE_EVERY s) → the ones cleared now"""
    cleared = set()
    for pid in pids:
        if pid not in PROBES or time.time() - _probed.get(pid, 0) < PROBE_EVERY:
            continue
        ask, clear = PROBES[pid]
        try:
            serves = ask()
        except Exception as e:  # noqa: BLE001: can't tell; the stop stays until a later probe
            print(f"stops: probe of {pid} failed:", repr(e)[:160], flush=True)
            _probed[pid] = time.time()
            continue
        if serves is None:
            continue  # no key to ask with: a later probe (with the key deployed) tries again
        _probed[pid] = time.time()
        if serves:
            clear()
            cleared.add(pid)
            print(f"stops: {pid} answers again (its own API): stop cleared", flush=True)
    return cleared


def stops():
    """every provider whose requests fail now for its credit, balance or a limit, from the records the jobs keep when
    it happens: kv openrouter_limit (ai.py) and kv social_limit (tikhub_fetch.py), each cleared by the first request
    that works again; kv solver_limit (paywall.py) likewise; kv proxy_out (resi.py). A record whose provider's own API
    says it serves again is cleared here (clear_answered)"""
    recs = {"openrouter": corpus.kv_get("openrouter_limit")}
    recs.update(corpus.kv_get("social_limit") or {})
    recs.update(corpus.kv_get("solver_limit") or {})  # paywall.py: CapSolver, 2Captcha
    px = corpus.kv_get("proxy_out")  # resi.py: IPRoyal yycstream out of traffic, every proxied job paused (one line)
    if px:
        jobs = ", ".join(PROXY_JOBS.get(j, j) for j in sorted(px.get("jobs") or {}, key=lambda j: PROXY_JOBS.get(j, j)))
        recs["iproyal"] = dict(px, message=f"out of traffic: {px.get('message') or ''} — paused (nothing given up, "
                                           f"they carry on by themselves): {jobs}")
    for pid in clear_answered([p for p, r in recs.items() if r]):
        recs[pid] = None
    out = [{"id": pid, "name": STOP_NAMES.get(pid, pid), "tag": stop_tag(r.get("message")),
            "error_text": error_text(r.get("message")), "since": r.get("since"), "last_failed": r.get("at"),
            "message": r.get("message"), "notified": bool(r.get("notified"))}
           for pid, r in recs.items() if r]
    return {"providers": sorted(out, key=lambda x: x["since"] or "")}


# ---- balances: each provider's own API ----

def not_connected(pid, name, needs):
    return {"id": pid, "name": name, "status": "not_connected", "needs": needs}


def bal_openrouter():
    """Deyao's OpenRouter account: its credit (GET /api/v1/credits: bought, used, balance), which answers only to a
    management key (yyc-monitor, OPENROUTER_MANAGEMENT_KEY in Secret monitor-keys, held by this pod alone), and key
    yyc-stream's own usage (GET /api/v1/key). Facts only: no projection against the balance (Deyao, 2026-10-15)."""
    key = os.environ.get("OPENROUTER_API")
    if not key:
        return not_connected("openrouter", "OpenRouter", "OPENROUTER_API (key yyc-stream) in the monitor pod's env")
    k = get("https://openrouter.ai/api/v1/key", {"Authorization": "Bearer " + key})["data"]
    out = {"id": "openrouter", "name": "OpenRouter", "account": "key yyc-stream", "status": "ok",
           "source": "provider API (GET /api/v1/key)",
           "usage_month_usd": k.get("usage_monthly"), "usage_today_usd": k.get("usage_daily"),
           "usage_week_usd": k.get("usage_weekly"), "usage_total_usd": k.get("usage"),
           "rate_usd_per_hour": round(hourly_ai_rate(), 4),
           "rate_basis": "our log: OpenRouter's charged cost per call (ai_runs), last 24 h"}
    mgmt = os.environ.get("OPENROUTER_MANAGEMENT_KEY")
    if not mgmt:
        out["credit_needs"] = "OPENROUTER_MANAGEMENT_KEY (management key yyc-monitor) in Secret monitor-keys"
        return out
    c = get("https://openrouter.ai/api/v1/credits", {"Authorization": "Bearer " + mgmt})["data"]
    bought, used = float(c["total_credits"]), float(c["total_usage"])
    # every YYC key's own usage (yyc-stream, YYC AI's yyc-chat, yyc-aiwatch, yyc-search): GET /api/v1/keys
    keys = [{"name": k.get("name"), "usage_today_usd": k.get("usage_daily"), "usage_month_usd": k.get("usage_monthly"),
             "disabled": bool(k.get("disabled"))}
            for k in get("https://openrouter.ai/api/v1/keys", {"Authorization": "Bearer " + mgmt}).get("data", [])
            if str(k.get("name") or "").startswith("yyc-")]
    out.update(credits_bought_usd=bought, credits_used_usd=used, credit_balance_usd=round(bought - used, 6),
               keys=sorted(keys, key=lambda k: k["name"]),
               source="provider API (GET /api/v1/credits and /api/v1/keys with management key yyc-monitor; GET /api/v1/key)")
    return out


def hourly_ai_rate():
    """USD per hour over the last 24 h, from OpenRouter's charged cost of each call (ai_runs; only purposes ai_spend
    counts: an agent turn's calls are logged twice, as agent_turn and as their own purpose)"""
    r = corpus.db().execute("""SELECT coalesce(sum(usd), 0) FROM ai_runs WHERE at >= now() - interval '24 hours'
                               AND purpose IN (SELECT DISTINCT purpose FROM ai_spend)""").fetchone()[0]
    return float(r) / 24


def bal_tikhub():
    key = os.environ.get("TIKHUB_API")
    if not key:
        return not_connected("tikhub", "TikHub", "TIKHUB_API in the monitor pod's env")
    h = {"Authorization": "Bearer " + key}
    u = get("https://api.tikhub.io/api/v1/tikhub/user/get_user_info", h)["user_data"]
    d = get("https://api.tikhub.io/api/v1/tikhub/user/get_user_daily_usage", h)
    dd = d.get("data") or {}
    return {"id": "tikhub", "name": "TikHub", "account": u.get("email"), "status": "ok",
            "source": "provider API (user/get_user_info, user/get_user_daily_usage; both free)",
            "balance_usd": u.get("balance"), "free_credit_usd": u.get("free_credit"),
            "usage_today_usd": dd.get("usage"), "usage_today_date": dd.get("date"),
            "usage_today_tz": d.get("time_zone"), "requests_today": dd.get("total_request_per_day"),
            "paid_requests_today": dd.get("paid_request_per_day"), "balance_usage_today_usd": dd.get("balance_usage"),
            "uri_counts": dd.get("uri_counts") or {}}  # TikHub's own requests per endpoint today (the cost profile)


def bal_anyapi():
    key = os.environ.get("ANYAPI_API_KEY")
    if not key:
        return not_connected("anyapi", "AnyAPI", "ANYAPI_API_KEY (Secret ai-tools) in the monitor pod's env")
    b = get("https://api.getanyapi.com/v1/balance", {"Authorization": "Bearer " + key})
    return {"id": "anyapi", "name": "AnyAPI", "account": "YYC's AnyAPI account", "status": "ok",
            "source": "provider API (GET /v1/balance)", "balance_usd": b.get("usd")}


def iproyal_usage(key, subs, start, end):
    """{day: GB} summed over the sub-users (IPRoyal's own usage report, per sub-user, per day and host)"""
    out = {}
    for s in subs:
        text = get(f"https://resi-api.iproyal.com/v1/residential/data-usage-report?hash={s['hash']}&date_from={start}"
                   f"&date_to={end}&measurement_unit=GB&rounding_decimal=4", {"Authorization": "Bearer " + key}, raw=True)
        per = {}
        for r in csv.DictReader(io.StringIO(text)):
            try:
                per[r["date"]] = per.get(r["date"], 0) + float(r["data"])
            except (KeyError, ValueError):
                continue
        s["by_day_gb"] = {k: round(v, 4) for k, v in sorted(per.items())}
        for k, v in per.items():
            out[k] = out.get(k, 0) + v
    return {k: round(v, 4) for k, v in sorted(out.items())}


def isp_usage(t=None):
    """the ISP IP's traffic, our count: today (HKT) and the last 30 days, against its Fair Usage allowance"""
    t = t or datetime.now(resi.HKT)
    days = [(t - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)]
    got = {r["k"].rsplit(":", 1)[1]: float(r["v"]) for r in corpus.db().execute(
        "SELECT k, v FROM kv WHERE k = ANY(%s)", ([f"isp:bytes:{d}" for d in days],))}
    return {"ip": ISP_IP, "order": ISP_ORDER, "today_bytes": got.get(days[0], 0.0),
            "last30_bytes": sum(got.values()), "by_day": dict(sorted(got.items())),
            "allowance_bytes": ISP_ALLOWANCE_BYTES,
            "basis": "our count on the wire (resi.metered_url: every byte to and from the ISP IP); IPRoyal's API "
                     "reports no traffic for ISP orders"}


def isp_order(key):
    """the ISP order as IPRoyal states it (expiry, auto-renew): its reseller API"""
    try:
        o = get(f"https://apid.iproyal.com/v1/reseller/orders/{ISP_ORDER}", {"X-Access-Token": key})
    except Exception as e:  # noqa: BLE001: the card stands without it
        return {"error": repr(e)[:200]}
    return {"expire_date": o.get("expire_date"), "plan": o.get("plan_name"), "status": o.get("status"),
            "auto_renew": ((o.get("auto_extend_settings") or {}).get("is_enabled"))}


def bal_iproyal():
    key = os.environ.get("IPROYAL_API")
    if not key:
        return not_connected("iproyal", "IPRoyal", "IPROYAL_API (the account's API token) in Secret monitor-keys")
    h = {"Authorization": "Bearer " + key}
    me = get("https://resi-api.iproyal.com/v1/me", h)
    subs = [{k: s.get(k) for k in ("hash", "username", "traffic_available", "traffic_used")}
            for s in get("https://resi-api.iproyal.com/v1/residential-subusers?per_page=100", h).get("data", [])
            if s.get("username") == IPROYAL_SUBUSER]
    orders = [o for o in get("https://resi-api.iproyal.com/v1/residential/orders?per_page=20", h).get("data", [])
              if o.get("status") == "confirmed" and o.get("quantity")]
    last = max(orders, key=lambda o: o.get("created_at") or "") if orders else None
    per_gb = round(float(last["amount"]) / float(last["quantity"]), 4) if last else None
    t = now()
    start = min(t.replace(day=1), t - timedelta(days=29)).strftime("%Y-%m-%d")
    by_day = iproyal_usage(key, subs, start, t.strftime("%Y-%m-%d"))
    try:
        split = resi.by_site(t)
    except Exception as e:  # noqa: BLE001: the split is our own count; the provider's figures stand without it
        split = {"error": repr(e)[:200]}
    return {"id": "iproyal", "name": "IPRoyal (residential)", "account": "sub-user " + IPROYAL_SUBUSER,
            "status": "ok", "source": "provider API (/v1/me, /residential-subusers, /residential/orders, data-usage-report)",
            "unallocated_gb": me.get("available_traffic"), "subusers": subs,
            "left_gb": round(sum(s["traffic_available"] or 0 for s in subs), 4),
            "used_gb": round(sum(s["traffic_used"] or 0 for s in subs), 4),
            "price_per_gb_usd": per_gb, "price_basis": (f"IPRoyal order {last['id']} of {last['created_at'][:10]}: "
                                                          f"USD {last['amount']} for {last['quantity']} GB") if last else None,
            "by_day_gb": by_day, "by_site": split, "isp": dict(isp_usage(), **isp_order(key)),
            "by_site_source": "our count (kv proxy:bytes:<site>, resi.py: curl's byte counts, compressed bodies, "
                              "downloads; estimates beside IPRoyal's own figure above)"}


def post_read(url, body, timeout=25):
    """a provider's read-only call that only takes a POST body (the captcha solvers' getBalance): changes nothing"""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def bal_solver(pid, name, env, url, account):
    """a captcha solver's balance (paywall.py: Reuters' DataDome on CapSolver, archive.today's reCAPTCHA on
    2Captcha). CapSolver is Deyao's own account, not a YYC-only one (EXPENSES.md), so its balance is the whole
    account's; 2Captcha is YYC's own account (TWOCAPTCHA_API = YYC's key, Deyao, 2026-10-19); YYC's part of either is
    the solver series in spending (solver_spend)"""
    key = os.environ.get(env)
    if not key:
        return not_connected(pid, name, f"{env} in Secret monitor-keys")
    b = post_read(url, {"clientKey": key})
    if b.get("errorId"):
        raise RuntimeError(f"{b.get('errorCode')} {b.get('errorDescription')}")
    return {"id": pid, "name": name, "account": account, "status": "ok",
            "source": f"provider API (POST {url})", "balance_usd": b.get("balance")}


def bal_capsolver():
    return bal_solver("capsolver", "CapSolver", "CAPSOLVER_API", "https://api.capsolver.com/getBalance",
                      "Deyao's own account (shared, not YYC-only)")


def bal_2captcha():
    return bal_solver("2captcha", "2Captcha", "TWOCAPTCHA_API", "https://api.2captcha.com/getBalance", "YYC's own account")


def bal_hetzner():
    key = os.environ.get("HETZNER_YYC_READ")
    if not key:
        return not_connected("hetzner", "Hetzner Cloud (project yyc)",
                             "a Read-only API token of project yyc (Hetzner Console → yyc → Security → API tokens) as "
                             "HETZNER_YYC_READ in Secret monitor-keys. Hetzner's API has no billing endpoint: it gives "
                             "the servers and list prices, the invoice stays the evidence")
    h = {"Authorization": "Bearer " + key}
    servers = get("https://api.hetzner.cloud/v1/servers", h)["servers"]
    pricing = get("https://api.hetzner.cloud/v1/pricing", h)["pricing"]
    ips = get("https://api.hetzner.cloud/v1/primary_ips", h)["primary_ips"]
    volumes = get("https://api.hetzner.cloud/v1/volumes", h).get("volumes", [])
    rows, total = [], 0.0
    for s in servers:
        loc = s["location"]["name"]
        p = next((x for x in s["server_type"]["prices"] if x["location"] == loc), None)
        m = float(p["price_monthly"]["net"]) if p else 0.0
        b = m * float(pricing["server_backup"]["percentage"]) / 100 if s.get("backup_window") else 0.0
        rows.append({"name": s["name"], "type": s["server_type"]["name"], "location": loc, "status": s["status"],
                     "monthly_net": round(m, 4), "backups_net": round(b, 4), "created": s["created"]})
        total += m + b
    ip4 = next((x for x in pricing["primary_ips"] if x["type"] == "ipv4"), None)
    n4 = sum(1 for i in ips if i["type"] == "ipv4")
    ip_rows = []
    if ip4 and n4:
        price = next((x for x in ip4["prices"] if x["location"] == (rows[0]["location"] if rows else "fsn1")), ip4["prices"][0])
        total += n4 * float(price["price_monthly"]["net"])
        ip_rows = [{"name": i.get("name") or i.get("ip"), "created": i.get("created"),
                    "monthly_net": round(float(price["price_monthly"]["net"]), 4)} for i in ips if i["type"] == "ipv4"]
    vol_gb = float(pricing["volume"]["price_per_gb_month"]["net"])
    vols = [{"name": v["name"], "size_gb": v["size"], "monthly_net": round(v["size"] * vol_gb, 4), "created": v.get("created")}
            for v in volumes]
    total += sum(v["monthly_net"] for v in vols)
    return {"id": "hetzner", "name": "Hetzner Cloud (project yyc)", "status": "ok", "currency": pricing["currency"],
            "volumes": vols,
            "source": "provider API list prices (servers, pricing, primary IPs); billed amounts are only on the invoice",
            "servers": rows, "ipv4": n4, "ipv4_ips": ip_rows, "list_monthly_net": round(total, 4), "vat_rate": pricing["vat_rate"]}


# ---- AWS (YYC's member account 551897326524: YYC AI's sandbox hosts, their AMI and image bucket) ----
# Cost: Cost Explorer's GetCostAndUsage, AWS's own billed figures (UnblendedCost per UTC day and service; a member
# account sees its own costs only). AWS charges USD 0.01 a request and brings its cost data up to date at most three
# times a day, so one request is read every AWS_CE_TTL and kept (in this process, and in the published snapshot for a
# new pod); the instances are read with every balances round (EC2 DescribeInstances, free).
# Credential: IAM user yyc-monitor (infra/aws-monitor-iam.py: ce:GetCostAndUsage and ec2:DescribeInstances, nothing
# else), AWS_YYC_MONITOR_ACCESS_KEY_ID / AWS_YYC_MONITOR_SECRET_ACCESS_KEY in Secret monitor-keys.
AWS_NAME = "AWS (account yyc)"
AWS_CE_TTL = 6 * 3600
AWS_REGIONS = [r for r in os.environ.get("AWS_YYC_REGIONS", "eu-central-1").split(",") if r]  # where YYC's instances run
AWS_SHORT = {"Amazon Elastic Compute Cloud - Compute": "EC2", "EC2 - Other": "EC2 other (disks, traffic)",
             "Amazon Simple Storage Service": "S3", "Amazon Virtual Private Cloud": "VPC (public IPv4)",
             "AWS Cost Explorer": "Cost Explorer", "AWS Key Management Service": "KMS", "AmazonCloudWatch": "CloudWatch",
             "AWS Data Transfer": "Data transfer"}
_aws = {"costs": None}


def aws_post(service, region, body, headers, ak, sk, timeout=25):
    """one signed POST to an AWS API (Signature Version 4, as s3.py) → the response text; an error carries AWS's own
    message"""
    import hashlib, hmac
    import s3
    host = f"{service}.{region}.amazonaws.com"
    t = datetime.now(timezone.utc)
    amz, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    h = dict({k.lower(): v for k, v in headers.items()}, **{"host": host, "x-amz-date": amz})
    names = sorted(h)
    canon = "\n".join(["POST", "/", "", "".join(f"{n}:{h[n]}\n" for n in names), ";".join(names),
                       hashlib.sha256(body).hexdigest()])
    scope = f"{day}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canon.encode()).hexdigest()])
    sig = hmac.new(s3.signing_key(sk, day, region, service), sts.encode(), hashlib.sha256).hexdigest()
    h["authorization"] = f"AWS4-HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={';'.join(names)}, Signature={sig}"
    h.pop("host")
    h["User-Agent"] = UA
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://{host}/", data=body, method="POST", headers=h),
                                    timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"AWS {service} HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from None


def aws_costs(ak, sk, start, end):
    """Cost Explorer: UnblendedCost per UTC day and service for [start, end) (dates) → {"days": {day: {service:
    usd}}, "estimated": [days AWS still marks as estimated], "requests": n}"""
    days, est, token, n = {}, [], None, 0
    while True:
        body = {"TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()}, "Granularity": "DAILY",
                "Metrics": ["UnblendedCost"], "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}]}
        if token:
            body["NextPageToken"] = token
        j = json.loads(aws_post("ce", "us-east-1", json.dumps(body).encode(), {
            "content-type": "application/x-amz-json-1.1", "x-amz-target": "AWSInsightsIndexService.GetCostAndUsage"}, ak, sk))
        n += 1
        for r in j.get("ResultsByTime") or []:
            d = days.setdefault(r["TimePeriod"]["Start"], {})
            for g in r.get("Groups") or []:
                usd = float(g["Metrics"]["UnblendedCost"]["Amount"])
                if usd:
                    d[g["Keys"][0]] = d.get(g["Keys"][0], 0.0) + usd
            if r.get("Estimated") and r["TimePeriod"]["Start"] not in est:
                est.append(r["TimePeriod"]["Start"])
        token = j.get("NextPageToken")
        if not token or n >= 20:
            return {"days": days, "estimated": est, "requests": n}


def aws_instances(ak, sk, region):
    """EC2 DescribeInstances in one region → every instance not terminated: name, type, state, launch time, spot or
    on-demand"""
    import xml.etree.ElementTree as ET
    out, token = [], None
    while True:
        q = {"Action": "DescribeInstances", "Version": "2016-11-15", "MaxResults": "1000"}
        if token:
            q["NextToken"] = token
        x = aws_post("ec2", region, urllib.parse.urlencode(q).encode(),
                     {"content-type": "application/x-www-form-urlencoded; charset=utf-8"}, ak, sk)
        root = ET.fromstring(re.sub(r'\sxmlns="[^"]+"', "", x, count=1))
        for i in root.iter("instancesSet"):
            for it in i.findall("item"):
                state = it.findtext("instanceState/name")
                if state in ("terminated", "shutting-down"):
                    continue
                tags = {t.findtext("key"): t.findtext("value") for t in it.findall("tagSet/item")}
                out.append({"id": it.findtext("instanceId"), "name": tags.get("Name") or tags.get("app") or "",
                            "type": it.findtext("instanceType"), "state": state, "launched": it.findtext("launchTime"),
                            "market": it.findtext("instanceLifecycle") or "on-demand", "region": region})
        token = root.findtext("nextToken")
        if not token:
            return out


def aws_costs_cached(ak, sk):
    """the kept Cost Explorer answer, read again once it is AWS_CE_TTL old; a new pod takes the published snapshot's.
    A failed read keeps the last answer and says why → (costs, error or None)"""
    c = _aws["costs"]
    if c is None:
        prev = next((p for p in ((stored("balances") or (0, {}))[1].get("providers") or []) if p.get("id") == "aws"), {})
        if prev.get("costs"):
            c = _aws["costs"] = prev["costs"]
    if c and time.time() - c["at"] < AWS_CE_TTL:
        return c, None
    today = datetime.now(timezone.utc).date()
    try:
        got = aws_costs(ak, sk, min(today.replace(day=1), today - timedelta(days=30)), today + timedelta(days=1))
    except Exception as e:  # noqa: BLE001
        if c:
            return c, repr(e)[:300]
        raise
    c = _aws["costs"] = dict(got, at=time.time(), read=iso(now()))
    return c, None


def bal_aws():
    """YYC's AWS account: what AWS has billed this month, yesterday and today so far (UTC days), per service, and the
    instances running now"""
    ak, sk = os.environ.get("AWS_YYC_MONITOR_ACCESS_KEY_ID"), os.environ.get("AWS_YYC_MONITOR_SECRET_ACCESS_KEY")
    if not (ak and sk):
        return not_connected("aws", AWS_NAME, "the access key of IAM user yyc-monitor (infra/aws-monitor-iam.py: Cost "
                                              "Explorer and EC2 read only) as AWS_YYC_MONITOR_ACCESS_KEY_ID and "
                                              "AWS_YYC_MONITOR_SECRET_ACCESS_KEY in Secret monitor-keys")
    costs, err = aws_costs_cached(ak, sk)
    inst = [i for r in AWS_REGIONS for i in aws_instances(ak, sk, r)]
    today = datetime.now(timezone.utc).date()
    month, yday = today.strftime("%Y-%m"), (today - timedelta(days=1)).isoformat()
    by_day = {d: round(sum(s.values()), 6) for d, s in sorted(costs["days"].items())}
    svc = lambda ds: sorted(({"name": AWS_SHORT.get(k, re.sub(r"^(Amazon|AWS)\s+", "", k)), "usd": round(v, 6)}  # noqa: E731
                             for k, v in ds.items()), key=lambda x: -x["usd"])
    by_service = {}
    for d, s in costs["days"].items():
        if d.startswith(month):
            for k, v in s.items():
                by_service[k] = by_service.get(k, 0.0) + v
    running = [i for i in inst if i["state"] == "running"]
    types = {}
    for i in running:
        types[i["type"]] = types.get(i["type"], 0) + 1
    return {"id": "aws", "name": AWS_NAME, "status": "ok",
            "account": "YYC's AWS member account 551897326524 (its own invoice)", "month": month,
            "month_usd": round(sum(v for d, v in by_day.items() if d.startswith(month)), 6),
            "yesterday": yday, "yesterday_usd": by_day.get(yday), "today_usd": by_day.get(today.isoformat()),
            "by_day_usd": by_day, "services_month": svc(by_service), "services_yesterday": svc(costs["days"].get(yday) or {}),
            "estimated_days": costs.get("estimated") or [], "costs_read": costs.get("read"), "costs_error": err,
            "costs": costs, "instances": sorted(inst, key=lambda i: i.get("launched") or ""), "running": len(running),
            "running_types": [{"type": k, "n": v} for k, v in sorted(types.items(), key=lambda x: -x[1])],
            "regions": AWS_REGIONS,
            "source": f"provider API (Cost Explorer GetCostAndUsage: UnblendedCost per UTC day and service, read every "
                      f"{AWS_CE_TTL // 3600} h at USD 0.01 a request; EC2 DescribeInstances in {', '.join(AWS_REGIONS)}); "
                      f"AWS completes a day's cost up to 24 h after it ends"}


# Workers Paid: what the plan includes each month and the list price beyond it, per million units
# (developers.cloudflare.com/workers/platform/pricing, read 2026-10-16). Durable Objects' duration is billed at 128 MB.
CF_PRICE_BASIS = ("Workers Paid included amounts and list prices beyond them: "
                  "https://developers.cloudflare.com/workers/platform/pricing/ (read 2026-10-16); Cloudflare rounds "
                  "billable usage up to its billing unit, and its invoice is the billed figure")
CF_INCLUDED = [  # id, name, unit, included per month, USD per million beyond
    ("workers_requests", "Workers requests", "requests", 10_000_000, 0.30),
    ("workers_cpu_ms", "Workers CPU time", "CPU ms", 30_000_000, 0.02),
    ("do_requests", "Durable Objects requests", "requests", 1_000_000, 0.15),
    ("do_duration_gb_s", "Durable Objects duration", "GB-s", 400_000, 12.50),
    ("kv_reads", "KV reads", "reads", 10_000_000, 0.50),
    ("kv_writes", "KV writes, deletes and lists", "operations", 1_000_000, 5.00),
]
CF_USAGE_QUERY = """query($a: String!, $start: Time!, $end: Time!, $sd: Date!, $ed: Date!) { viewer {
  accounts(filter: {accountTag: $a}) {
    w: workersInvocationsAdaptive(limit: 1000, filter: {datetime_geq: $start, datetime_leq: $end}) {
      sum { requests errors cpuTimeUs } dimensions { scriptName } }
    do: durableObjectsInvocationsAdaptiveGroups(limit: 1000, filter: {date_geq: $sd, date_leq: $ed}) {
      sum { requests } dimensions { scriptName } }
    dop: durableObjectsPeriodicGroups(limit: 1000, filter: {date_geq: $sd, date_leq: $ed}) {
      sum { activeTime } dimensions { namespaceId } }
    kv: kvOperationsAdaptiveGroups(limit: 1000, filter: {date_geq: $sd, date_leq: $ed}) {
      sum { requests } dimensions { actionType } } } } }"""


def cf_graphql(token, query, variables):
    req = urllib.request.Request("https://api.cloudflare.com/client/v4/graphql",
                                 data=json.dumps({"query": query, "variables": variables}).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                          "User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def bal_cloudflare():
    """YYC's Cloudflare account (the edge app yyc-web and its Workers): its subscriptions (Workers Paid USD 5.00 a
    month from 2026-10-16) and the billing period's Workers, Durable Objects and KV usage from Cloudflare's GraphQL
    analytics against the plan's included amounts. Token yyc-monitor (Account Analytics Read + Billing Read on account
    YYC only), CLOUDFLARE_YYC_MONITOR_TOKEN + CLOUDFLARE_YYC_ACCOUNT_ID in Secret monitor-keys."""
    name = "Cloudflare (account YYC)"
    key, acct = os.environ.get("CLOUDFLARE_YYC_MONITOR_TOKEN"), os.environ.get("CLOUDFLARE_YYC_ACCOUNT_ID")
    if not key or not acct:
        return not_connected("cloudflare", name, " and ".join(
            n for n, v in (("CLOUDFLARE_YYC_MONITOR_TOKEN (token yyc-monitor: Account Analytics Read + Billing Read on "
                            "account YYC)", key), ("CLOUDFLARE_YYC_ACCOUNT_ID", acct)) if not v) + " in Secret monitor-keys")
    subs = get(f"https://api.cloudflare.com/client/v4/accounts/{acct}/subscriptions",
               {"Authorization": "Bearer " + key}).get("result") or []
    rows = [{"name": (x.get("rate_plan") or {}).get("public_name") or (x.get("rate_plan") or {}).get("id"),
             "id": (x.get("rate_plan") or {}).get("id"), "price_usd": x.get("price"), "currency": x.get("currency"),
             "frequency": x.get("frequency"), "state": x.get("state"), "period_start": x.get("current_period_start"),
             "period_end": x.get("current_period_end")} for x in subs]
    main = next((r for r in rows if r["id"] == "workers_paid"), None)
    t = datetime.now(timezone.utc)
    z = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    start = (main or {}).get("period_start") or z(t.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    end = z(t)
    d = cf_graphql(key, CF_USAGE_QUERY, {"a": acct, "start": start, "end": end, "sd": start[:10], "ed": end[:10]})
    if d.get("errors"):
        raise RuntimeError("Cloudflare GraphQL: " + json.dumps(d["errors"])[:250])
    a = ((d.get("data") or {}).get("viewer") or {}).get("accounts") or [{}]
    a = a[0] if a else {}
    by_script = {}
    for r in a.get("w") or []:
        s = by_script.setdefault(r["dimensions"]["scriptName"], {"requests": 0, "cpu_ms": 0.0, "errors": 0})
        s["requests"] += r["sum"]["requests"]
        s["cpu_ms"] = round(s["cpu_ms"] + r["sum"]["cpuTimeUs"] / 1000, 3)
        s["errors"] += r["sum"].get("errors") or 0
    kv = {}
    for r in a.get("kv") or []:
        kv[r["dimensions"]["actionType"]] = kv.get(r["dimensions"]["actionType"], 0) + r["sum"]["requests"]
    used = {"workers_requests": sum(s["requests"] for s in by_script.values()),
            "workers_cpu_ms": round(sum(s["cpu_ms"] for s in by_script.values()), 3),
            "do_requests": sum(r["sum"]["requests"] for r in a.get("do") or []),
            "do_duration_gb_s": round(sum(r["sum"]["activeTime"] for r in a.get("dop") or []) / 1e6 * 128 / 1024, 3),
            "kv_reads": kv.get("read", 0),
            "kv_writes": sum(v for k, v in kv.items() if k != "read")}
    usage = [{"id": i, "name": n, "unit": u, "used": used[i], "included": inc, "price_per_million_usd": pm,
              "overage_usd": round(max(0, used[i] - inc) / 1e6 * pm, 6)} for i, n, u, inc, pm in CF_INCLUDED]
    return {"id": "cloudflare", "name": name, "status": "ok", "account": f"YYC ({acct})",
            "plan": (main or {}).get("name"), "subscriptions": rows,
            "subscriptions_monthly_usd": sum(r["price_usd"] or 0 for r in rows if r["frequency"] == "monthly"),
            "period_start": start, "period_end": (main or {}).get("period_end"), "usage_to": end,
            "usage": usage, "overage_usd": round(sum(x["overage_usd"] for x in usage), 6), "by_script": by_script,
            "source": "provider API (GET /accounts/{id}/subscriptions; GraphQL analytics workersInvocationsAdaptive, "
                      "durableObjectsInvocationsAdaptiveGroups, durableObjectsPeriodicGroups, kvOperationsAdaptiveGroups "
                      "since the billing period's start)",
            "price_basis": CF_PRICE_BASIS}


EXA_TOOLS = ("live_web_search", "live_page_fetch")


def bal_exa():
    """YYC's Exa account (chendeyao.uk@gmail.com, Free tier), used by the AI assistant's live_web_search and
    live_page_fetch (key yyc-news-stream, EXA_API_KEY in Secret ai-tools). Exa's per-key usage API is its Team Management
    API, which Exa enables per team on request (support@exa.ai) and has not for this team, and an API key reads no
    balance; so the card shows Exa's own charged cost of each call (costDollars.total in Exa's responses, which
    tools/spend.py keeps in tool_spend.provider_cost)."""
    name = "Exa"
    if not os.environ.get("EXA_API_KEY"):
        return not_connected("exa", name, "EXA_API_KEY (key yyc-news-stream, Secret ai-tools) in the monitor pod's env")
    t = datetime.now(timezone.utc)
    first = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day = t.replace(hour=0, minute=0, second=0, microsecond=0)
    from tools import spend  # its table, made when missing
    rows = spend._db().execute(
        """SELECT at >= %s, count(*), coalesce(sum(provider_cost), 0), count(provider_cost) FROM tool_spend
           WHERE tool = ANY(%s) AND ok AND at >= %s GROUP BY 1""", (day, list(EXA_TOOLS), first)).fetchall()
    month = [sum(r[1] for r in rows), sum(float(r[2]) for r in rows)]
    today = next(([r[1], float(r[2])] for r in rows if r[0]), [0, 0.0])
    return {"id": "exa", "name": name, "status": "ok", "account": "YYC's Exa account (chendeyao.uk@gmail.com), "
                                                                 "key yyc-news-stream",
            "plan": "Free tier: USD 10.00 of credit each calendar month (resets on the 1st, unused credit does not "
                    "carry over) plus a one-time USD 10.00 onboarding bonus (exa.ai/docs/admin/billing)",
            "calls_month": month[0], "exa_charged_month_usd": round(month[1], 6),
            "calls_today": today[0], "exa_charged_today_usd": round(today[1], 6), "month": t.strftime("%Y-%m"),
            "usage_api": "not available: Exa's per-key usage and the team's balance are only in its Team Management "
                         "API, which Exa enables per team on request to support@exa.ai (exa.ai/docs/reference/"
                         "team-management/get-api-key-usage); it is not enabled for this team",
            "source": "Exa's own charged cost per call (costDollars.total in each response), kept in tool_spend; "
                      "UTC month and day"}


def balances():
    out = []
    for f in (bal_openrouter, bal_tikhub, bal_anyapi, bal_iproyal, bal_capsolver, bal_2captcha, bal_hetzner,
              bal_aws, bal_cloudflare, bal_exa):
        try:
            out.append(f())
        except Exception as e:
            name = f.__name__[4:]
            out.append({"id": name, "name": name, "status": "error", "error": repr(e)[:300]})
    return {"providers": out}


# ---- spending ----
# Facts only (Deyao, 2026-10-14/15/16: budgets are safety stops; no projection or run-out date): spend per day and this
# month (history), and what things cost NOW: the last 6 h and 24 h per component and part (infra/cost-profile.py's
# profile: provider figures where they cover the window, else our log), and per AI purpose the model in use, since
# when, and its rate since it took over (cost_profile.current_models), so a replaced model's spend reads as history.

WINDOWS = (6, 24)  # hours
_cp = {"mod": None, "prices": None, "prices_at": 0.0}
PRICES_TTL = 6 * 3600


def cost_profile():
    """infra/cost-profile.py as a module (the same logic as the cost profile, not a copy)"""
    if _cp["mod"] is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cost_profile", os.path.join(os.path.dirname(os.path.abspath(__file__)), "infra", "cost-profile.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _cp["mod"] = m
    return _cp["mod"]


def tikhub_prices():
    """TikHub's endpoint prices, read again every PRICES_TTL"""
    if _cp["prices"] is None or time.time() - _cp["prices_at"] > PRICES_TTL:
        got = cost_profile().tikhub_prices()
        if got or _cp["prices"] is None:
            _cp["prices"], _cp["prices_at"] = got, time.time()
    return _cp["prices"]


def slim(c):
    """a profile component for the page: its cost, where the figure comes from, its biggest parts"""
    return {"id": c["id"], "name": c["name"], "cost_usd": round(c["cost_usd"] or 0, 6), "cost_source": c["cost_source"],
            "provider_usd": c["provider_usd"], "app_usd": c["app_usd"], "cost_note": c.get("cost_note"),
            "basis": c["provider_basis"] if c["cost_source"].startswith("provider") and c["provider_basis"] else c["app_basis"],
            "subs": [{"name": x["name"], "cost_usd": round(x["cost_usd"], 6), "calls": x.get("calls"), "gb": x.get("gb")}
                     for x in c["subs"][:8] if (x["cost_usd"] or 0) > 0]}



_fx = {"rate": None, "basis": None, "at": 0.0}


def eur_usd():
    """EUR → USD: the ECB reference rate (frankfurter.app, as infra/cost-profile.py), read again every PRICES_TTL;
    (None, reason) when it cannot be read"""
    if _fx["rate"] is None or time.time() - _fx["at"] > PRICES_TTL:
        try:
            j = get("https://api.frankfurter.app/latest?from=EUR&to=USD")
            _fx.update(rate=float(j["rates"]["USD"]), basis=f"ECB reference rate of {j.get('date')} (frankfurter.app)",
                       at=time.time())
        except Exception as e:
            if _fx["rate"] is None:
                return None, f"EUR → USD rate not available ({e!r})"[:200]
    return _fx["rate"], _fx["basis"]


def _hours_in(created, start, end):
    """the hours of [start, end) a resource existed (created: its ISO time; unknown = the whole window)"""
    try:
        c = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        c = start
    return max(0.0, (end - max(start, c)).total_seconds() / 3600)


def hetzner_window(het, start, end, rate, rate_basis):
    """Hetzner for a window: each resource now in project yyc (servers, volumes, primary IPv4) at its monthly list
    price spread over 730 h (Hetzner bills hourly up to the monthly price; infra/cost-profile.py HOURS_PER_MONTH),
    for the hours of the window it has existed; EUR, and USD at the ECB rate. Resources deleted before now are not in
    Hetzner's list, so not here: the invoice is the billed figure."""
    if het.get("status") != "ok":
        return None
    hpm = cost_profile().HOURS_PER_MONTH
    res = [(f"{s['name']} ({s['type']}, {s['location']})", "server", s["monthly_net"] + (s.get("backups_net") or 0),
            s.get("created")) for s in het.get("servers") or []]
    res += [(f"volume {v['name']} ({v['size_gb']} GB)", "volume", v["monthly_net"], v.get("created"))
            for v in het.get("volumes") or []]
    res += [(f"primary IPv4 {i['name']}", "IPv4", i["monthly_net"], i.get("created")) for i in het.get("ipv4_ips") or []]
    subs = []
    for name, kind, monthly, created in res:
        eur = float(monthly) / hpm * _hours_in(created, start, end)
        subs.append({"name": name, "kind": kind, "eur": eur, "monthly_eur": float(monthly),
                     "app_usd": None, "provider_usd": eur * rate if rate else None})
    eur = sum(x["eur"] for x in subs)
    usd = eur * rate if rate else None
    for x in subs:
        x["share_of_component"] = x["eur"] / eur if eur else 0
        x["cost_usd"] = x["provider_usd"]
    subs.sort(key=lambda x: -x["eur"])
    return {"id": "hetzner", "name": "Hetzner Cloud (project yyc)", "provider_usd": usd, "app_usd": None,
            "cost_usd": usd, "cost_source": "provider list prices", "cost_eur": eur, "eur_usd": rate,
            "provider_basis": f"Hetzner API list prices (net of VAT) of the resources in project yyc now, the monthly "
                              f"price spread over {hpm} h for the hours each existed in the window; EUR → USD at "
                              f"{rate_basis}; the invoice is the billed figure",
            "app_basis": None, "provider_coverage": 1.0, "subs": subs,
            "cost_note": None if rate else "no USD figure: " + str(rate_basis)}


def aws_window(aws, start, end):
    """AWS for a window: Cost Explorer's figures are per UTC day and a day is complete up to 24 h after it ends, so a
    window is costed at the rate of the latest day AWS has closed (yesterday, UTC), per service; the month's and
    each day's own figures are in the history"""
    if aws.get("status") != "ok" or aws.get("yesterday_usd") is None:
        return None
    hours = (end - start).total_seconds() / 3600
    subs = [{"name": x["name"], "kind": "service", "app_usd": None, "provider_usd": x["usd"] / 24 * hours,
             "cost_usd": x["usd"] / 24 * hours} for x in aws.get("services_yesterday") or [] if x["usd"] > 0]
    usd = sum(x["cost_usd"] for x in subs)
    for x in subs:
        x["share_of_component"] = x["cost_usd"] / usd if usd else 0
    return {"id": "aws", "name": AWS_NAME, "provider_usd": usd, "app_usd": None, "cost_usd": usd,
            "cost_source": "provider API (daily rate)", "provider_coverage": 1.0, "app_basis": None, "cost_note": None,
            "provider_basis": f"AWS Cost Explorer: the UnblendedCost of {aws.get('yesterday')} (UTC, the latest day AWS "
                              f"has closed) per service, spread over its 24 h, for the window's hours", "subs": subs}


def exa_window(start, end):
    """Exa for a window: Exa's own charged cost per call (costDollars.total, tool_spend; as the Exa card)"""
    try:
        from tools import spend
        rows = spend._db().execute(
            """SELECT tool, count(*), coalesce(sum(provider_cost), 0) FROM tool_spend
               WHERE tool = ANY(%s) AND ok AND at >= %s AND at < %s GROUP BY 1""", (list(EXA_TOOLS), start, end)).fetchall()
    except Exception:
        return None
    subs = [{"name": t, "kind": "tool", "calls": n, "app_usd": float(u), "cost_usd": float(u),
             "share_of_component": 0} for t, n, u in rows]
    usd = sum(x["app_usd"] for x in subs)
    return {"id": "exa", "name": "Exa (the data API's web search; free tier)", "provider_usd": None, "app_usd": usd,
            "cost_usd": usd, "cost_source": "provider per call", "provider_coverage": None, "provider_basis": None,
            "app_basis": "Exa's own charged cost per call (costDollars.total in each response, tool_spend); within the "
                         "free tier's USD 10.00 monthly credit", "subs": sorted(subs, key=lambda x: -x["app_usd"])}


NOT_RECORDED = ("(job not recorded)", "(not recorded)", "-", "", None)


def job_states(jobs):
    """which jobs run now (Deyao, 2026-10-21: "turned off services should not enter the ranking"), from the jobs panel
    (the cluster's CronJobs and Deployments as Kubernetes reports them): a job is live when it is a CronJob that is not
    suspended, or a Deployment with replicas and no "off" (a feature flag turned off, e.g. transcription); a job name
    that is neither (run.sh runs, trials, probes, refills) is a finished one-off run. A call whose job was not recorded
    can't be placed, so it stays live. Returns (state(job) -> (live, reason), basis)"""
    cj = {x["name"]: x for x in (jobs or {}).get("cronjobs") or []}
    dp = {x["name"]: x for x in (jobs or {}).get("deployments") or []}
    known = bool(cj or dp)

    def state(j):
        if j in NOT_RECORDED or not known:
            return True, None
        if j in cj:
            x = cj[j]
            if x.get("suspended") or x.get("off"):
                return False, "CronJob suspended" + (f" ({x['off']})" if x.get("off") else "")
            return True, None
        if j in dp:
            x = dp[j]
            if not x.get("replicas"):
                return False, "Deployment at 0 replicas" + (f" ({x['off']})" if x.get("off") else "")
            if x.get("off"):
                return False, "turned off: " + str(x["off"])
            return True, None
        return False, "one-off run (no CronJob or Deployment of that name now)"
    return state, ("the cluster's CronJobs and Deployments now (jobs panel)" if known else
                   "the jobs panel has no data, so nothing is counted as turned off")


def ai_parts(cp, windows, first, end, state, current):
    """AI spend per purpose × model, split by whether it runs now: live = its job runs now (job_states) and the model
    is the one the purpose uses now (the *_verify purposes: streams.CONFIRM_MODEL, the stage-2 model in the code's
    config; the others: the model of the purpose's latest call, cost_profile.current_models). USD per window: our log
    (ai_runs) × the window's OpenRouter cost / our log (the provider's figure split by our log, as the cost profile)"""
    try:
        import streams
        confirm = streams.CONFIRM_MODEL
    except Exception:
        confirm = None
    cur = {m["purpose"]: m["model"] for m in current}
    rows = {}
    spans = [("month", first, 1.0)] + [(k, s, f) for k, s, f in windows]
    for key, start, f in spans:
        for p, m, j, u in cp._q("""SELECT purpose, coalesce(model, '?'), coalesce(job, '(not recorded)'), sum(usd)
                                   FROM ai_runs WHERE at >= %s AND at < %s AND purpose <> 'agent_turn'
                                   GROUP BY 1, 2, 3""", (start, end)):
            live, why = state(j)
            want = confirm if p.endswith("_verify") and confirm else cur.get(p)
            if live and want and m != want:
                live, why = False, (f"replaced: the purpose uses {want} now" +
                                    (" (CONFIRM_MODEL)" if p.endswith("_verify") and confirm else ""))
            r = rows.setdefault((p, m, live, why), {"purpose": p, "model": m, "live": live, "reason": why,
                                                     "jobs": set(), "month_usd": 0.0, "h24_usd": 0.0, "h6_usd": 0.0})
            r["jobs"].add(j)
            r[{"month": "month_usd", "24h": "h24_usd", "6h": "h6_usd"}[key]] += float(u or 0) * f
    out = []
    for r in rows.values():
        r["jobs"] = sorted(r["jobs"])
        out.append(r)
    return out


def spend_bars(cp, raws, first, end, series, breakdown, om, ipr, het, cf, exa, rate, rate_basis, jobs, current, aws=None):
    """every spending item for the bar view (Deyao, 2026-10-21: "all the spending should get a bar graph … ordered by
    value … the bar should be 24h average"): per provider and per part (purpose × model, site (job), job · endpoint,
    solver · site, resource), each with its last 24 h and 6 h cost and this month's (HKT, our log; Hetzner and
    Cloudflare their monthly price). Parts of jobs or models that no longer run (job_states, ai_parts) are `live`
    False with their reason: the page keeps them out of the rankings and lists them apart; a provider's row counts
    its live parts only"""
    state, state_basis = job_states(jobs)
    w24, w6 = raws.get(24) or {}, raws.get(6) or {}
    comp = lambda p, i: next((c for c in p.get("components") or [] if c["id"] == i), None)  # noqa: E731
    sub = lambda p, i, n: next((x for x in (comp(p, i) or {}).get("subs") or [] if x["name"] == n), None)  # noqa: E731
    bm = next((b for b in breakdown if b["key"] == "month"), {})
    price = ipr.get("price_per_gb_usd")
    ser = {s["id"]: s for s in series}
    names = {}
    for p in (w24, w6):
        for c in p.get("components") or []:
            names.setdefault(c["id"], c["name"])
    # this month per part, and each part's job (the per-job breakdown names it)
    month, month_bytes, job_of = {"iproyal": {}, "tikhub": {}, "anyapi": {}, "solvers": {}}, {}, {}
    for b in breakdown:
        for j in (b.get("proxy") or {}).get("by_job") or []:
            for x in j["sites"]:
                k = f"{x['site']} ({j['job'].strip('()')})"
                job_of[("iproyal", k)] = j["job"]
                if b["key"] == "month":
                    month_bytes[k] = x["bytes"]
                    month["iproyal"][k] = x["bytes"] / cp.BYTES_PER_GB * price if price else None
        for prov in ("tikhub", "anyapi"):
            for j in (b.get(prov) or {}).get("by_job") or []:
                for e in j["endpoints"]:
                    k = f"{j['job']} · {e['endpoint']}"
                    job_of[(prov, k)] = j["job"]
                    if b["key"] == "month":
                        month[prov][k] = e["usd"]
    try:
        month["solvers"] = {x["name"]: x["app_usd"] for x in cp.solvers(first, end)["subs"]}
    except Exception:
        pass
    items = []
    # AI: per purpose × model, live and turned-off parts apart
    o24, o6 = comp(w24, "openrouter") or {}, comp(w6, "openrouter") or {}
    fac = lambda c: (c["cost_usd"] / c["app_usd"]) if c.get("app_usd") else 1.0  # noqa: E731
    for r in ai_parts(cp, [("24h", end - timedelta(hours=24), fac(o24)), ("6h", end - timedelta(hours=6), fac(o6))],
                      first, end, state, current):
        items.append({"id": "openrouter", "provider": names.get("openrouter", "OpenRouter"),
                      "name": f"{r['purpose']} × {r['model']}", "purpose": r["purpose"], "model": r["model"],
                      "kind": "purpose × model", "h24_usd": r["h24_usd"], "h6_usd": r["h6_usd"],
                      "month_usd": r["month_usd"], "live": r["live"], "reason": r["reason"], "jobs": r["jobs"]})
    # the other providers' parts from the windows' profiles
    seen = set()
    for p in (w24, w6):
        for c in p.get("components") or []:
            if c["id"] in ("openrouter", "exa"):  # AI: ai_parts; Exa: one row below
                continue
            for x in c.get("subs") or []:
                if (c["id"], x["name"]) in seen:
                    continue
                seen.add((c["id"], x["name"]))
                cid, n = c["id"], x["name"]
                s24, s6 = sub(w24, cid, n), sub(w6, cid, n)
                j = job_of.get((cid, n))
                live, why = state(j) if j is not None else (True, None)
                it = {"id": cid, "provider": names[cid], "name": n, "kind": x.get("kind"), "job": j,
                      "h24_usd": (s24 or {}).get("cost_usd") or 0.0, "h6_usd": (s6 or {}).get("cost_usd") or 0.0,
                      "month_usd": month.get(cid, {}).get(n), "live": live, "reason": why}
                if cid == "iproyal":
                    it["h24_bytes"] = (s24 or {}).get("bytes")
                    it["month_bytes"] = month_bytes.get(n)
                if cid == "hetzner":
                    it.update(h24_eur=(s24 or {}).get("eur"), month_usd=None,
                              month_text=f"EUR {x.get('monthly_eur', 0):.2f}/month (list)")
                if cid == "aws":  # this month per service: Cost Explorer's own figure
                    it["month_usd"] = next((s["usd"] for s in (aws or {}).get("services_month") or [] if s["name"] == n), None)
                if cid == "cloudflare":
                    it.update(month_usd=None, month_text=("USD 5.00/month (subscription)" if n.startswith("subscriptions")
                                                          else f"USD {float(cf.get('overage_usd') or 0):.4f} this billing period"))
                items.append(it)
    # month-only parts (no spend in the last 6 h / 24 h) of the per-part providers, so the turned-off list is complete
    for cid, m in month.items():
        for n, v in m.items():
            if (cid, n) not in seen and v:
                j = job_of.get((cid, n))
                live, why = state(j) if j is not None else (True, None)
                it = {"id": cid, "provider": names.get(cid, cid), "name": n, "job": j, "h24_usd": 0.0, "h6_usd": 0.0,
                      "month_usd": v, "live": live, "reason": why}
                if cid == "iproyal":
                    it["month_bytes"] = month_bytes.get(n)
                items.append(it)
    items.append({"id": "exa", "provider": names.get("exa", "Exa"), "name": "live_web_search + live_page_fetch",
                  "h24_usd": (comp(w24, "exa") or {}).get("cost_usd") or 0.0, "h6_usd": (comp(w6, "exa") or {}).get("cost_usd") or 0.0,
                  "month_usd": exa.get("exa_charged_month_usd"), "live": True, "reason": None})
    # a provider's row: its live parts; Hetzner and Cloudflare: their monthly price
    providers = []
    for cid, name in names.items():
        c24 = comp(w24, cid) or {}
        mine = [r for r in items if r["id"] == cid]
        live = [r for r in mine if r["live"]]
        row = {"id": cid, "name": name, "source": c24.get("cost_source"), "note": c24.get("cost_note"),
               "h24_usd": sum(r["h24_usd"] or 0 for r in live), "h6_usd": sum(r["h6_usd"] or 0 for r in live),
               "month_usd": sum(r["month_usd"] or 0 for r in live),
               "off_h24_usd": sum(r["h24_usd"] or 0 for r in mine if not r["live"]),
               "off_month_usd": sum(r["month_usd"] or 0 for r in mine if not r["live"]),
               "basis": c24.get("provider_basis") if (c24.get("cost_source") or "").startswith("provider") and c24.get("provider_basis")
               else c24.get("app_basis")}
        if not mine:  # a provider whose window has no parts (none so far): its window figure
            row.update(h24_usd=c24.get("cost_usd") or 0.0, h6_usd=(comp(w6, cid) or {}).get("cost_usd") or 0.0)
        if cid == "hetzner":
            row.update(month_usd=None, h24_eur=c24.get("cost_eur"),
                       month_text=f"EUR {float(het.get('list_monthly_net') or 0):.2f}/month (list)")
        if cid == "aws":  # the month as AWS bills it (services with no cost yesterday included)
            row.update(month_usd=(aws or {}).get("month_usd"))
        if cid == "cloudflare":
            row.update(month_usd=None, month_text=f"USD {float(cf.get('subscriptions_monthly_usd') or 0):.2f}/month + usage "
                                                  f"USD {float(cf.get('overage_usd') or 0):.4f}")
        providers.append(row)
    key = lambda r: (-(r["h24_usd"] or 0), -(r.get("month_usd") or 0), r["name"])  # noqa: E731
    jobs_seen = {j for b in breakdown for part in ("proxy", "tikhub", "anyapi", "openrouter")
                 for j in (x["job"] for x in (b.get(part) or {}).get("by_job") or [])}
    return {"providers": sorted(providers, key=key), "items": sorted(items, key=key), "eur_usd": rate,
            "eur_usd_basis": rate_basis,
            "jobs": {j: {"live": s[0], "reason": s[1]} for j in sorted(jobs_seen) for s in [state(j)]},
            "off_rule": "turned off = its job is not running now (" + state_basis + ": a suspended CronJob, a "
                        "Deployment at 0 replicas or with its feature turned off, or a job name that is neither: a "
                        "finished one-off run), or, for AI, a model the purpose no longer uses (the *_verify purposes: "
                        "CONFIRM_MODEL; the others: the model of the purpose's latest call). A call with no job "
                        "recorded stays in the ranking",
            "month_basis": "this month (HKT), per part: our log (ai_runs; paid_calls; IPRoyal: our byte count × the "
                           "per-GB price, counted from 15 Oct; solver_spend; Exa's charged cost, UTC month); a provider's "
                           "row: its running parts (the history below has the providers' own month figures); Hetzner "
                           "and Cloudflare: their monthly price",
            "basis": "bar = the last 24 h (the cost profile: the provider's own counter where its snapshots cover the "
                     "window, split over parts by our log; Hetzner: list prices spread over the hours; AWS: the latest closed "
                     "day's cost spread over its 24 h)"}


def spending():
    t = now()
    end = datetime.now(timezone.utc)
    days = [(t - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]
    c = corpus.db()
    first = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month0 = first.strftime("%Y-%m-%d")
    since = min(days[0], month0)
    mtd = lambda by: round(sum(v for d, v in by.items() if d >= month0), 4)  # noqa: E731
    ai_total = {}
    for d, usd in c.execute("SELECT day, sum(usd) FROM ai_spend WHERE day >= %s GROUP BY day ORDER BY day", (since,)):
        ai_total[d] = round(float(usd), 6)
    series = []
    bal = {b["id"]: b for b in (panel("balances").get("providers") or [])}
    orb = bal.get("openrouter") or {}
    series.append({"id": "openrouter", "name": "OpenRouter (AI)", "by_day": ai_total, "mtd_usd": mtd(ai_total),
                   "mtd_provider_usd": orb.get("usage_month_usd") if orb.get("status") == "ok" else None,
                   "source": "by day: our log (ai_spend: OpenRouter's charged cost per call, HKT days); month to date "
                             "also from the provider (OpenRouter /api/v1/key, UTC month)"})
    try:
        runs = corpus.file_get("social_runs.json")
    except LookupError:
        runs = []
    soc = {}
    for r in runs:
        d = (r.get("at") or "")[:10]
        soc[d] = round(soc.get(d, 0) + float(r.get("cost_usd") or 0), 4)
    series.append({"id": "social", "name": "TikHub + AnyAPI (social search)", "by_day": soc, "mtd_usd": mtd(soc),
                   "source": "our log (social_runs.json: successful requests × list price; AnyAPI's backup source can "
                             "cost more)", "provider_today_usd": (bal.get("tikhub") or {}).get("usage_today_usd")})
    sol = {}
    for d, usd in c.execute("SELECT day, sum(usd) FROM solver_spend WHERE day >= %s GROUP BY day ORDER BY day", (since,)):
        sol[d] = round(float(usd), 6)
    series.append({"id": "solvers", "name": "Captcha solvers (Reuters, archive.today)", "by_day": sol, "mtd_usd": mtd(sol),
                   "source": "our log (solver_spend: each solve's charge as the provider states it: 2Captcha's cost per "
                             "task, CapSolver's list price per DataDome solve)"})
    ipr = bal.get("iproyal") or {}
    if ipr.get("status") == "ok" and ipr.get("price_per_gb_usd"):
        usd = {d: round(gb * ipr["price_per_gb_usd"], 4) for d, gb in (ipr.get("by_day_gb") or {}).items()}
        series.append({"id": "iproyal", "name": "IPRoyal (proxy traffic)", "by_day": usd, "mtd_usd": mtd(usd),
                       "source": f"provider figures: IPRoyal's usage report (GB per day, YYC sub-users) × the per-GB "
                                 f"price of {ipr.get('price_basis')}"})
    else:
        series.append({"id": "iproyal", "name": "IPRoyal (proxy traffic)", "by_day": {}, "mtd_usd": None,
                       "source": "not connected: " + str(ipr.get("needs") or ipr.get("error"))})
    aws = bal.get("aws") or {}
    if aws.get("status") == "ok":
        usd = {d: round(v, 4) for d, v in (aws.get("by_day_usd") or {}).items()}
        series.append({"id": "aws", "name": "AWS (YYC AI's sandbox hosts)", "by_day": usd,
                       "mtd_usd": round(aws.get("month_usd") or 0, 4), "mtd_provider_usd": aws.get("month_usd"),
                       "source": "provider figures: AWS Cost Explorer (UnblendedCost per UTC day; the last day or two "
                                 "are still filling in)"})
    het = bal.get("hetzner") or {}
    fixed = []
    if het.get("status") == "ok":
        fixed.append({"id": "hetzner", "name": "Hetzner Cloud (project yyc)", "monthly": het["list_monthly_net"],
                      "currency": het["currency"], "source": het["source"]})
    cf = bal.get("cloudflare") or {}
    if cf.get("status") == "ok":
        fixed.append({"id": "cloudflare", "name": "Cloudflare (account YYC): " + ", ".join(
            f"{x['name']} USD {x['price_usd']:.2f}" for x in cf.get("subscriptions") or [] if x.get("price_usd")),
            "monthly": cf["subscriptions_monthly_usd"], "currency": "USD", "usage_beyond_included_usd": cf["overage_usd"],
            "source": cf["source"]})
    # now: the recent windows, the model per purpose and its rate, this month per purpose × model
    cp = cost_profile()
    prices = tikhub_prices()
    windows, models, raws = [], [], {}
    rate, rate_basis = eur_usd()
    for h in WINDOWS:
        p = cp.profile(end - timedelta(hours=h), end, tikhub_prices=prices)
        # Hetzner, AWS and Exa: their own figures for the window (hetzner_window, aws_window, exa_window), not the profile's
        extra = [x for x in (hetzner_window(het, end - timedelta(hours=h), end, rate, rate_basis),
                             aws_window(aws, end - timedelta(hours=h), end), exa_window(end - timedelta(hours=h), end)) if x]
        p["components"] = [x for x in p["components"] if x["id"] not in ("hetzner", "aws")] + extra
        comps = [slim(x) for x in p["components"]]
        windows.append({"hours": h, "start": p["start"], "end": p["end"], "components": comps,
                        "total_usd": round(sum(x["cost_usd"] or 0 for x in comps), 6)})
        models, raw = p["current_models"], p
        raws[h] = p
    current = {(m["purpose"], m["model"]) for m in models}
    o24 = next((x for x in raw["components"] if x["id"] == "openrouter"), {})  # the last window: 24 h, every part
    last24 = {x["name"]: x["cost_usd"] for x in o24.get("subs", [])}
    om = cp.openrouter(first, end)  # this month, purpose × model (our log)
    ai_models = []
    for x in om["subs"]:
        purpose, _, model = x["name"].partition(" × ")
        ai_models.append({"purpose": purpose, "model": model, "current": (purpose, model) in current,
                          "usd": round(x["app_usd"], 6), "calls": x["calls"],
                          "last_24h_usd": round(last24.get(x["name"], 0.0), 6)})
    ai_models.sort(key=lambda r: (not r["current"], -r["last_24h_usd"], -r["usd"]))
    breakdown = [per_job(cp, k, label, s, end, prices, ipr, month0)
                 for k, label, s in (("6h", "Last 6 h", end - timedelta(hours=6)), ("24h", "Last 24 h", end - timedelta(hours=24)),
                                     ("month", "This month (HKT)", first))]
    try:
        jobs_now = panel("jobs")
    except Exception:
        jobs_now = {}
    bars = spend_bars(cp, raws, first, end, series, breakdown, om, ipr, het, cf, bal.get("exa") or {}, rate, rate_basis,
                      jobs_now, models, aws)
    return {"month": t.strftime("%Y-%m"), "days": days, "series": series, "fixed": fixed, "bars": bars,
            "total_mtd_usd": round(sum(x["mtd_usd"] or 0 for x in series), 4),
            "windows": windows, "models": models, "ai_models": ai_models, "breakdown": breakdown,
            "proxy_unit": {"bytes_per_gb": cp.BYTES_PER_GB, "bytes_per_mb": cp.BYTES_PER_MB,
                           "note": "bytes, in decimal units as IPRoyal counts them (its GB is what a 10 GB top-up buys): "
                                   "1 GB = 1,000,000,000 bytes, 1 MB = 1,000,000 bytes"},
            "now_basis": "last 6 h / 24 h: infra/cost-profile.py's profile (the provider's own counter where its "
                         "snapshots cover the window, else our log; Hetzner: its list prices spread over the hours, AWS: the latest closed day's cost spread over its 24 h, Exa: its charged cost per call). Rate now: a "
                         "purpose's spend since its current model took over, over at most the last 24 h (our log: ai_runs)",
            "note": "USD; days are HKT except IPRoyal's report days and OpenRouter's month (UTC)"}


def per_job(cp, key, label, start, end, prices, ipr, month0):
    """one window of the per-job breakdown (Deyao, 2026-10-16): IPRoyal traffic in bytes per job and site (never USD),
    TikHub and AnyAPI USD per job and endpoint with failed calls apart, OpenRouter USD per job; each beside the
    provider's own figure (infra/cost-profile.py, the cost profile's own aggregation). This month's IPRoyal figure is
    its usage report (GB per day, the month's days) when the balances panel has it: the snapshots began 2026-10-16."""
    px = cp.proxy_traffic(start, end)
    if key == "month" and ipr.get("status") == "ok" and ipr.get("by_day_gb"):
        gb = sum(v for d, v in ipr["by_day_gb"].items() if d >= month0)
        px.update(provider_bytes=int(round(gb * cp.BYTES_PER_GB)), provider_coverage=1.0,
                  provider_basis="IPRoyal's data-usage-report for sub-user yycstream (GB per day, 1 GB = 10^9 bytes), "
                                 f"its days from {month0}; IPRoyal's counters lag by hours")
    th, an, orr = cp.tikhub(start, end, prices or {}), cp.anyapi(start, end), cp.openrouter(start, end)

    def calls(c):
        return {"usd": c["app_usd"], "calls": c["calls"], "failed_calls": c["failed_calls"], "by_job": c["by_job"],
                "provider_usd": c["provider_usd"], "provider_coverage": c["provider_coverage"],
                "provider_basis": c["provider_basis"], "basis": c["app_basis"]}
    return {"key": key, "label": label, "start": start.isoformat(), "end": end.isoformat(), "proxy": px,
            "tikhub": dict(calls(th), provider_calls=th.get("provider_calls"), unpriced_endpoints=th["unpriced_endpoints"]),
            "anyapi": calls(an),
            "openrouter": {"usd": orr["app_usd"], "by_job": orr["by_job"], "provider_usd": orr["provider_usd"],
                           "provider_coverage": orr["provider_coverage"], "provider_basis": orr["provider_basis"],
                           "basis": orr["app_basis"]}}


# ---- the WiseSearch catalogue sources (sites.json): what they add, measured, per route (Deyao, 2026-10-20) ----

def catalogue(hours=24):
    """per route (direct, ISP IP, yycstream, browser; a route the sites job found itself counts under it): sites,
    sites read OK, posts first seen, posts scored and confirmed (stage 2), article pages read for full text, and the
    proxied bytes (yycstream per site label; the ISP IP for the sites job). Scoring calls are batched (many posts a
    call), so posts scored is the measured unit; the USD line is the day's scoring spend, not split per source"""
    import resi
    t = now()
    since = iso(t - timedelta(hours=hours))
    try:
        sites = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites.json")))
    except (OSError, ValueError):
        sites = []
    st = {}
    for doc in ("sites.json", "browser-sites.json"):
        st.update((corpus.file_get(doc, {}) or {}).get("sites") or {})

    def route(s):
        if s.get("browser"):
            return "browser"
        px = s.get("proxy") or (st.get(s["id"]) or {}).get("auto_route")
        return {"isp": "isp", "hk": "yycstream", "mo": "yycstream", True: "yycstream"}.get(px, "direct")
    by = {}
    for s in sites:
        by.setdefault(route(s), []).append(s)
    c = corpus.db()
    day = t.strftime("%Y-%m-%d")
    out = {}
    for r, ss in sorted(by.items()):
        sids = [s["id"] for s in ss]
        q = c.execute("""SELECT count(*) FILTER (WHERE p.seen >= %s AND p.first_run IS NOT TRUE),
                                count(*) FILTER (WHERE p.seen >= %s AND p.first_run),
                                count(*) FILTER (WHERE p.cached_at >= %s)
                         FROM posts p WHERE p.sid = ANY(%s) AND p.thread IS NULL
                           AND (p.seen >= %s OR p.cached_at >= %s)""",  # bounded: the sites' every post read 200 MB
                      (since, since, since, sids, since, since)).fetchone()
        # posts scored = the scorer's own record (table scored: every post it scored, whatever the score); confirmed =
        # pairs stage 2 scored again (scores.screen kept)
        sc = c.execute("""SELECT (SELECT count(*) FROM scored d JOIN posts p ON p.id = d.post_id
                                   WHERE p.sid = ANY(%s) AND d.at >= %s),
                                 (SELECT count(*) FROM scores s JOIN posts p ON p.id = s.post_id
                                   WHERE p.sid = ANY(%s) AND s.scored_at >= %s AND s.screen IS NOT NULL)""",
                       (sids, since, sids, since)).fetchone()
        labels = sorted({resi.site(s["url"]) for s in ss})
        yb = sum(float(v or 0) for (v,) in c.execute("SELECT v FROM kv WHERE k = ANY(%s)",
                                                       ([f"proxy:bytes:{l}:{day}" for l in labels],))) if r == "yycstream" else None
        out[r] = {"sites": len(ss), "ok": sum(1 for x in sids if (st.get(x) or {}).get("ok")), "new_posts": q[0],
                  "backlog_posts": q[1], "pages_read": q[2], "posts_scored": sc[0], "confirmed": sc[1],
                  "yycstream_bytes_today": yb}
    isp = sum(float(v or 0) for (v,) in c.execute("SELECT v FROM kv WHERE k LIKE %s", (f"isp:jobbytes:sites:{day}%",)))
    usd = c.execute("SELECT coalesce(sum(usd), 0) FROM ai_runs WHERE at >= %s AND purpose LIKE 'scoring%%'", (since,)).fetchone()[0]
    return {"hours": hours, "routes": out, "isp_bytes_today_sites_job": isp, "scoring_usd_all_sources": round(float(usd), 4),
            "note": "posts scored = the catalogue's posts with a score in the window (the backlog's first read is scored "
                    "for 數據分析 too); yycstream bytes = today's count for these sites' labels (the poller's and the "
                    "full-text job's reads of those hosts included); ISP bytes = the sites job's today"}


# ---- TikTok videos (tiktok_video.py; Deyao, 2026-10-22) ----

def videos(days=14):
    """the TikTok videos of posts passing stage 1: per HKT day (newest first) posts queued, videos saved and their
    bytes, failures and gone; the queue now; the last failures with their reasons; the bucket's own figure (kv
    tiktok_video:bucket, ListObjectsV2 by the worker every 10 min)"""
    c = corpus.db()
    since = now() - timedelta(days=days)
    per = {}
    for r in c.execute("""SELECT to_char(queued_at AT TIME ZONE 'Asia/Hong_Kong', 'YYYY-MM-DD') d, count(*) n,
                                 count(*) FILTER (WHERE how = 'stage1') s1 FROM tiktok_videos WHERE queued_at >= %s GROUP BY 1""",
                       (since,)).fetchall():
        per.setdefault(r["d"], {})["queued"], per[r["d"]]["queued_stage1"] = r["n"], r["s1"]
    for r in c.execute("""SELECT to_char(done_at AT TIME ZONE 'Asia/Hong_Kong', 'YYYY-MM-DD') d,
                                 count(*) FILTER (WHERE status = 'ok' AND coalesce(format, '') NOT LIKE 'same video%%') ok,
                                 coalesce(sum(bytes) FILTER (WHERE status = 'ok' AND coalesce(format, '') NOT LIKE 'same video%%'), 0) b,
                                 count(*) FILTER (WHERE status = 'failed') failed, count(*) FILTER (WHERE status = 'gone') gone
                          FROM tiktok_videos WHERE done_at >= %s GROUP BY 1""", (since,)).fetchall():
        per.setdefault(r["d"], {}).update(ok=r["ok"], bytes=int(r["b"]), failed=r["failed"], gone=r["gone"])
    by_day = [dict({"day": d, "queued": 0, "queued_stage1": 0, "ok": 0, "bytes": 0, "failed": 0, "gone": 0}, **v)
              for d, v in sorted(per.items(), reverse=True)]
    st = {r["status"]: {"n": r["n"], "bytes": int(r["b"] or 0)} for r in
          c.execute("SELECT status, count(*) n, sum(bytes) b FROM tiktok_videos GROUP BY 1").fetchall()}
    retrying = c.execute("SELECT count(*) FROM tiktok_videos WHERE status='queued' AND tries > 0").fetchone()[0]
    fails = [dict(r) for r in c.execute(
        """SELECT post_id, status, tries, left(error, 300) AS error, coalesce(done_at, queued_at) AS at
           FROM tiktok_videos WHERE error IS NOT NULL AND (status IN ('failed', 'gone') OR tries > 0)
           ORDER BY coalesce(done_at, queued_at) DESC LIMIT 12""").fetchall()]
    for f in fails:
        f["at"] = iso(f["at"].astimezone(HKT))
    reasons = [dict(r) for r in c.execute(
        """SELECT status, left(regexp_replace(error, '[0-9]{6,}', 'N', 'g'), 120) AS reason, count(*) n FROM tiktok_videos
           WHERE status IN ('failed', 'gone') AND done_at >= %s GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10""", (since,)).fetchall()]
    return {"days": days, "by_day": by_day, "status": st, "retrying": retrying, "recent_failures": fails,
            "reasons": reasons, "bucket": corpus.kv_get("tiktok_video:bucket"),
            "note": "queued = TikTok posts whose stage-1 score reached 40 (stage1: at scoring; sweep/backlog: caught later); "
                    "downloads from the cluster's own IP (no proxy) into bucket yyc-tiktok-videos (Hetzner Object Storage)"}


# ---- Cloudflare: the deyaochen.com records and YYC's account as code ----

# Deyao's names that are custom hostnames of YYC's SaaS zone (deploy/README.md "Cloudflare (tofu)"): records in his own
# zone deyaochen.com, outside git, changed only by hand. Checked by a public DNS lookup (no Cloudflare token).
SAAS_NAMES = ("yyc.deyaochen.com", "yyc-beta.deyaochen.com", "mailmerge.deyaochen.com", "ai.yyc.deyaochen.com")
SAAS_TARGET = "customers.yyc-internal.com"
DOH = "https://cloudflare-dns.com/dns-query?type=CNAME&name="
# YYC's Cloudflare account as code is applied by the GitHub workflow cloudflare.yml, which posts a failed run to Discord
TOFU_RUNS = "https://github.com/DE0CH/yyc-code/actions/workflows/cloudflare.yml"


def _cname(name):
    req = urllib.request.Request(DOH + name, headers={"User-Agent": UA, "Accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    return [a["data"].rstrip(".").lower() for a in d.get("Answer") or [] if a.get("type") == 5]


def cloudflare():
    """each SAAS_NAMES record still a CNAME to SAAS_TARGET (Discord once when one is not, once when it is again); the
    OpenTofu runs of YYC's Cloudflare account are the GitHub workflow's (TOFU_RUNS), which reports its own failures"""
    t = now()
    names = []
    for n in SAAS_NAMES:
        try:
            got = _cname(n)
            err = None
        except Exception as e:
            got, err = None, repr(e)[:200]
        ok = got is not None and SAAS_TARGET in got
        names.append({"name": n, "cname": got, "ok": ok, "error": err})
        if got is not None:  # a failed lookup is not a wrong record
            _alert(f"dns:{n}", not ok, t,
                   f"YYC: {n} no longer CNAMEs to {SAAS_TARGET} (public DNS answers {got or 'no CNAME'}); the site is off "
                   "YYC's Cloudflare account. The record is in Deyao's zone deyaochen.com and is set by hand: DNS only, "
                   f"CNAME {SAAS_TARGET}. Monitor: https://newswatch.yyc-internal.com/app/monitor#cloudflare",
                   f"YYC: {n} CNAMEs to {SAAS_TARGET} again ({_hm(t)}).")
    return {"names": names, "target": SAAS_TARGET, "runs": TOFU_RUNS}


def corpus_counts():
    """the corpus's size for the site and the data API (web-app server/site.ts corpusStats, yyc-mcp): every post, the
    first day, posts per source type, counted here once every TTL and kept in kv corpus_stats. Counted by every
    Workers isolate on its own they read all of posts 130 times an hour (2026-10-27)"""
    c = corpus.db()
    # from the indexes alone (posts_source_type, posts_day): two scans of every post's page read 4 GB every 10 min
    by = {(t["source_type"] or "null"): t["n"] for t in c.execute("SELECT source_type, count(*) AS n FROM posts GROUP BY 1")}
    first = c.execute("SELECT min(day) AS first FROM posts").fetchone()["first"]
    v = {"posts": sum(by.values()), "first_day": first, "by_type": by, "at": now().isoformat(timespec="seconds")}
    corpus.kv_set("corpus_stats", v)
    return v


PANELS = {"corpus": corpus_counts, "stops": stops, "sources": sources, "telegram": telegram, "whatsapp": whatsapp, "jobs": jobs, "pipeline": pipeline, "balances": balances,
          "spending": spending, "wisers": wisers, "catalogue": catalogue, "videos": videos, "cloudflare": cloudflare,
          "database": database, "storage": storage}


# ---- the live dashboard's YYC AI and systems-health areas (/app/live; Deyao, 2026-10-28: "watch live stuff for the
# AI system as well, and also health of systems … other system healths like balance") ----
# Every LIVE_EVERY seconds one loop (not a panel: it never holds /readyz up) reads YYC AI's operator snapshot (hermit
# GET /ops/snapshot, the data of https://ai.yyc-internal.com/ops/: the verdict, deployments, hosts, sandboxes, budget,
# errors, spend; bearer HERMIT_OPS_TOKEN = hermit's monitor pair token, aviary hermit spec 18.3, 21.4), slims it, adds
# the systems health from this pod's own panels (balances, stops, jobs) plus the cluster's nodes and Postgres, and
# writes the result into kv LIVE_KV (small: the page reads it every 3 s; kv monitor:data is 1.3 MB). YYC AI's activity
# becomes events of kind 'ai' (the page's paced cards): what changed since the last snapshot (model calls per person
# from the budget's per-person counts, sandbox starts and releases, host events, egress errors, a change of status).
# Only the holder of the lock 'live ai' writes (a rollout's two pods: one). hermit runs on the new Talos cluster: until
# this monitor moves there (T4), it is reached through bridge B4 (Service yyc/hermit-bridge -> bridge
# yyc-bridge-new, yyc-ai/refactor DESIGN 15.5); the monitor's move changes HERMIT_OPS_URL's default to
# http://hermit.yyc-ai.svc.cluster.local/ops/snapshot?tz=Asia/Hong_Kong.

LIVE_KV = "live:sys"
LIVE_CURSOR = "live:ai:cursor"
LIVE_EVERY = 10
HERMIT_OPS_URL = os.environ.get("HERMIT_OPS_URL",
                                "http://hermit-bridge.yyc.svc.cluster.local/ops/snapshot?tz=Asia/Hong_Kong")


def ai_snapshot():
    """YYC AI's operator snapshot (raises when it can't be read)"""
    tok = os.environ.get("HERMIT_OPS_TOKEN")
    if not tok:
        raise RuntimeError("HERMIT_OPS_TOKEN (YYC AI hermit's monitor pair token) is not in the monitor pod's env")
    # hermit refuses a call without its protocol header (400 unsupported_protocol, aviary platform 3.4)
    return get(HERMIT_OPS_URL, {"Authorization": "Bearer " + tok, "Aviary-Protocol": "hermit-core=1"}, timeout=15)


def _gib(b):
    return None if b is None else round(float(b) / 2**30, 1)


def ai_slim(d):
    """what the page shows of the snapshot (no message contents exist in it: owners, ids, hosts, counts, costs)"""
    b = d.get("budget") or {}
    st, tot = b.get("status") or {}, b.get("total") or {}
    svc = [{"name": x.get("name"), "ready": x.get("ready"), "desired": x.get("desired"), "state": x.get("state"),
            "restarts": sum(p.get("restarts") or 0 for p in x.get("pods") or [])}
           for x in ((d.get("service") or {}).get("deployments") or [])]
    hosts = [{"name": h.get("name"), "kind": h.get("kind"), "gateway": h.get("gateway"), "status": h.get("status"),
              "reachable": h.get("reachable"), "sandboxes": h.get("sandboxes"), "slots": h.get("slots"),
              "cpus": h.get("cpus"), "load1": h.get("load1"), "mem_total_gib": _gib(h.get("memTotal")),
              "mem_free_gib": _gib(h.get("memAvailable")), "launched": h.get("launchedAt"), "instance": h.get("instanceId")}
             for h in ((d.get("hosts") or {}).get("list") or [])]
    sbx = d.get("sandboxes") or {}
    rec = d.get("recent") or {}
    starts = (d.get("starts") or {}).get("summary") or {}
    plan = (d.get("scaler") or {}).get("plan") or {}
    return {
        "at": d.get("at"), "health": d.get("health") or {}, "errors": d.get("errors") or {},
        "service": svc, "hosts": hosts, "max_hosts": (d.get("hosts") or {}).get("maxHosts"),
        "sandboxes": [{"owner": s.get("owner"), "status": s.get("status"), "host": s.get("host"), "age_s": s.get("ageS"),
                       "idle_s": s.get("idleS"), "migrations": s.get("migrations")} for s in sbx.get("list") or []],
        "max_active": sbx.get("maxActive"),
        "budget": {"spent_usd": st.get("spentUsd"), "limit_usd": st.get("limitUsd"), "since": st.get("since"),
                   "exhausted": st.get("exhausted"), "today_usd": tot.get("today"), "month_usd": tot.get("month"),
                   "calls_today": tot.get("callsToday")},
        "by_user": [{"owner": u.get("owner"), "today_usd": u.get("today"), "calls": u.get("callsToday")}
                    for u in b.get("byUser") or [] if u.get("callsToday")],
        "by_model": [{"model": m.get("model"), "today_usd": m.get("today"), "calls": m.get("callsToday")}
                     for m in b.get("byModel") or [] if m.get("callsToday")],
        "people_today": [{"owner": p.get("owner"), "conversations": p.get("today"), "live": p.get("live")}
                         for p in d.get("people") or [] if p.get("today") or p.get("live")],
        "recent": {k: rec.get(k) for k in ("failedStarts", "modelErrors", "hostErrors", "blocked")},
        "recent_minutes": d.get("recentMinutes"),
        "starts_24h": starts,
        "scaler": {k: plan.get(k) for k in ("available", "demand", "waiting", "planned", "pending")},
        "source": "YYC AI operator snapshot (hermit /ops/snapshot: its Postgres, the Kubernetes API, the fabric's "
                  "scaler, egress's summary); costs = OpenRouter's charged cost per call in egress_log",
    }


def ai_activity(d, cur):
    """the events since cursor `cur` → ([(at, data), …] oldest first, the new cursor). A first read (no cursor) only
    sets the cursor: the page's history starts now, not with a flood"""
    b = d.get("budget") or {}
    calls = {u.get("owner") or "?": [int(u.get("callsToday") or 0), float(u.get("today") or 0)] for u in b.get("byUser") or []}
    models = {m.get("model") or "?": int(m.get("callsToday") or 0) for m in b.get("byModel") or []}
    starts = (d.get("starts") or {}).get("list") or []
    vm = d.get("events") or []
    eg = (d.get("egress") or {}).get("recent") or []
    health = (d.get("health") or {}).get("state")
    newest = lambda rows, k="at": max([r.get(k) or "" for r in rows] + [""])
    nxt = {"day": d.get("today"), "calls": calls, "models": models, "health": health,
           "starts": max(newest(starts), (cur or {}).get("starts") or ""), "vm": max(newest(vm), (cur or {}).get("vm") or ""),
           "egress": max(newest(eg), (cur or {}).get("egress") or "")}
    if not cur:
        return [], nxt
    out, at = [], d.get("at")
    if cur.get("day") != d.get("today"):  # a new HK day: the per-day counts started again from 0
        cur = dict(cur, calls={}, models={})
    dm = {m: n - cur.get("models", {}).get(m, 0) for m, n in models.items() if n > cur.get("models", {}).get(m, 0)}
    busy = []
    for owner, (n, usd) in calls.items():
        n0, u0 = (cur.get("calls", {}).get(owner) or [0, 0.0])
        if n > n0:
            busy.append((owner, n - n0, round(usd - u0, 6), n, usd))
    for owner, dn, du, n, usd in sorted(busy, key=lambda x: -x[1]):
        out.append((at, {"what": "calls", "owner": owner, "calls": dn, "usd": du, "calls_today": n, "usd_today": round(usd, 6),
                         "models": dm if len(busy) == 1 else None}))
    for r in starts:
        if (r.get("at") or "") > (cur.get("starts") or ""):
            out.append((r["at"], {"what": r.get("what"), "owner": r.get("owner"), "host": r.get("host"), "ok": r.get("ok"),
                                  "ms": r.get("ms"), "reason": r.get("reason")}))
    for r in vm:
        if (r.get("at") or "") > (cur.get("vm") or ""):
            data = r.get("data") or {}
            out.append((r["at"], {"what": "host", "kind": r.get("kind"), "host": r.get("host"), "owner": r.get("owner"),
                                  "error": data.get("error") if isinstance(data, dict) else None,
                                  "detail": json.dumps(data, ensure_ascii=False)[:300] if data else None}))
    for r in eg:
        # quiet = expected noise or a provider glitch Claude Code retries (Jarvis 3 ops.ts QUIET; Deyao, 2026-11-02)
        if (r.get("at") or "") > (cur.get("egress") or "") and not r.get("quiet"):
            out.append((r["at"], {"what": "egress", "kind": r.get("kind"), "host": r.get("host"), "path": (r.get("path") or "")[:200],
                                  "status": r.get("status"), "decision": r.get("decision"), "owner": r.get("owner")}))
    if health and cur.get("health") and health != cur.get("health"):
        out.append((at, {"what": "status", "state": health, "was": cur.get("health"),
                         "findings": [f.get("text") for f in (d.get("health") or {}).get("findings") or []][:4]}))
    out.sort(key=lambda x: x[0] or "")
    return out, nxt


def nodes():
    """the cluster's nodes (ClusterRole monitor-nodes-read): Ready, pool, kubelet version"""
    out = []
    for n in k8s("/api/v1/nodes")["items"]:
        conds = {c["type"]: c for c in (n.get("status") or {}).get("conditions") or []}
        lab = n["metadata"].get("labels") or {}
        pressure = [t for t in ("MemoryPressure", "DiskPressure", "PIDPressure") if (conds.get(t) or {}).get("status") == "True"]
        out.append({"name": n["metadata"]["name"], "ready": (conds.get("Ready") or {}).get("status") == "True",
                    "pool": lab.get("pool") or lab.get("node.kubernetes.io/instance-type"), "pressure": pressure,
                    "unschedulable": bool((n.get("spec") or {}).get("unschedulable"))})
    return sorted(out, key=lambda x: x["name"])


def _slim_panel(name, keep):
    """a panel's snapshot for live:sys: the keys in `keep` plus when it was computed, or {error} (and the last good
    figures with refresh_error when only the latest refresh failed)"""
    at, v = _panel_value(name)
    if not v:
        return {"error": "no snapshot yet"}
    if "error" in v:
        return {"error": v["error"], "computed_at": v.get("computed_at")}
    out = {k: v.get(k) for k in keep}
    out["computed_at"] = v.get("computed_at")
    if v.get("refresh_error"):
        out["refresh_error"] = v["refresh_error"]
    return out


def wisers_slim():
    """the WiseSearch relay in one line's worth (the monitor's wisers panel; its detail stays on /app/monitor#wisers)"""
    s = _slim_panel("wisers", ("session", "health", "today", "month", "today_failed"))
    if "error" in s:
        return s
    ss, h = s.pop("session") or {}, s.pop("health") or {}
    pr = h.get("probe") or {}
    return dict(s, state=ss.get("state"), since=ss.get("since"), last_ok=ss.get("last_ok"),
                probe={k: pr.get(k) for k in ("at", "ok", "secs", "outcome")} if pr else None,
                failures=h.get("failures") or {}, logins_1h=h.get("logins_1h"), failed_logins_1h=h.get("failed_logins_1h"),
                problems=h.get("problems") or [], down=bool(h.get("down")))


def _usd(v, d=2):
    return "–" if v is None else f"USD {float(v):,.{d}f}"


def provider_line(p):
    """one provider's figures for the page: [[label, value], …] (the provider's own figures; the source beside them)"""
    i, f = p.get("id"), []
    if p.get("status") != "ok":
        return f
    if i == "openrouter":
        if p.get("credit_balance_usd") is not None:
            f.append(["credit", _usd(p["credit_balance_usd"])])
        for k in p.get("keys") or []:
            f.append([k["name"], f"{_usd(k['usage_today_usd'])} today · {_usd(k['usage_month_usd'])} month"])
        if not p.get("keys"):
            f.append(["yyc-stream", f"{_usd(p.get('usage_today_usd'))} today · {_usd(p.get('usage_month_usd'))} month"])
    elif i == "tikhub":
        f += [["balance", _usd(p.get("balance_usd"))], ["today", _usd(p.get("usage_today_usd"))]]
    elif i in ("anyapi", "capsolver", "2captcha"):
        f.append(["balance", _usd(p.get("balance_usd"), 3 if i != "anyapi" else 2)])
    elif i == "iproyal":
        days = p.get("by_day_gb") or {}
        today = days.get(now().strftime("%Y-%m-%d"))
        f += [["yycstream left", f"{p.get('left_gb')} GB"], ["unallocated", f"{p.get('unallocated_gb')} GB"]]
        if today is not None:
            f.append(["today", f"{today} GB"])
    elif i == "hetzner":
        f.append(["servers", f"{len(p.get('servers') or [])} · list {p.get('currency', 'EUR')} {p.get('list_monthly_net')}/month net"])
    elif i == "aws":
        # one short line (it must fit a phone's width beside the name), then two that give way first when the panel is full
        f.append(["month", f"{_usd(p.get('month_usd'))} · yesterday {_usd(p.get('yesterday_usd') or 0)}"])
        top = (p.get("services_month") or [])[:3]
        if top:
            f.append(["by service", " · ".join(f"{x['name'].split(' (')[0]} {float(x['usd']):,.2f}" for x in top)])
        types = p.get("running_types") or []
        f.append(["running now", (", ".join(f"{x['n']} × {x['type']}" for x in types[:3]) or "no instance") +
                  (f", +{len(types) - 3} more types" if len(types) > 3 else "")])
    elif i == "cloudflare":
        f.append(["plan", f"{p.get('plan')} · overage {_usd(p.get('overage_usd'))}"])
    elif i == "exa":
        f.append(["month", f"{_usd(p.get('exa_charged_month_usd'), 4)} · {p.get('calls_month')} calls"])
    return f


def _panel_value(name):
    hit = _cache.get(name) or stored(name)
    return (hit[0], hit[1]) if hit else (None, {})


def health_slim():
    """the systems-health area: providers' balances (with source and age), stops, the news stream's Deployments and
    CronJobs, the cluster's nodes, Postgres (load, space, archiving: the database panel), the WiseSearch relay (the
    wisers panel), the object storage buckets (the storage panel)"""
    out = {}
    bat, bal = _panel_value("balances")
    out["balances_at"] = bal.get("computed_at")
    out["providers"] = [{"id": p.get("id"), "name": p.get("name"), "status": p.get("status"), "figs": provider_line(p),
                         "source": p.get("source"), "error": p.get("error") or p.get("needs")}
                        for p in bal.get("providers") or []]
    _, stp = _panel_value("stops")
    out["stops"] = [{"id": s.get("id"), "name": s.get("name"), "tag": s.get("tag"), "since": s.get("since"),
                     "error": s.get("error_text")} for s in stp.get("providers") or []]
    _, jb = _panel_value("jobs")
    deps = jb.get("deployments") or []
    cjs = jb.get("cronjobs") or []
    out["deployments"] = {"n": len(deps), "ok": sum(1 for x in deps if x.get("off") or (x.get("ready") or 0) >= (x.get("replicas") or 0)),
                          "short": [f"{x['name']} {x.get('ready')}/{x.get('replicas')}" for x in deps
                                    if not x.get("off") and (x.get("ready") or 0) < (x.get("replicas") or 0)]}
    live_cj = [c for c in cjs if not c.get("suspended") and not c.get("off")]
    out["cronjobs"] = {"n": len(live_cj), "late": [c["name"] for c in live_cj if c.get("late")],
                       "failed": [c["name"] for c in live_cj if c.get("last_finished") == "failed"],
                       "running": sum(c.get("active") or 0 for c in live_cj),
                       "off": len(cjs) - len(live_cj)}
    out["jobs_at"] = jb.get("computed_at")
    try:
        out["nodes"] = nodes()
    except Exception as e:  # noqa: BLE001: one part missing, the rest stand
        out["nodes"] = {"error": repr(e)[:200]}
    out["postgres"] = _slim_panel("database", ("pod", "connections", "longest_transaction", "tps", "cpu_cores", "node_cores",
                                               "memory_mib", "memory_limit_mib", "space", "databases", "wal_bytes", "archive"))
    out["storage"] = _slim_panel("storage", ("buckets", "bytes", "objects"))
    out["wisers"] = wisers_slim()
    return out


def live_once():
    """one round: the snapshot → kv LIVE_KV and the 'ai' events (only under the lock)"""
    with corpus.lock("live ai", wait=False) as got:
        if not got:
            return False
        doc = {"at": iso(now())}
        snap = None
        try:
            snap = ai_snapshot()
            doc["ai"] = ai_slim(snap)
        except Exception as e:  # noqa: BLE001
            doc["ai"] = {"error": repr(e)[:300]}
        try:
            doc["health"] = health_slim()
        except Exception as e:  # noqa: BLE001
            doc["health"] = {"error": repr(e)[:300]}
        c = corpus.db()
        if snap is not None:
            cur = corpus.kv_get(LIVE_CURSOR)
            evs, nxt = ai_activity(snap, cur)
            with c:  # the events and the cursor in one transaction
                if evs:
                    c.executemany("INSERT INTO events (at, kind, post_id, data) VALUES (%s, 'ai', NULL, %s::jsonb)",
                                  [(a, json.dumps(x, ensure_ascii=False, default=str)) for a, x in evs])
                corpus.kv_set(LIVE_CURSOR, nxt)
        corpus.kv_set(LIVE_KV, doc)
        return True


def _live_loop(stop):
    while not stop.is_set():
        try:
            live_once()
        except Exception as e:  # noqa: BLE001: the next round tries again
            print(f"monitor: live round failed: {e!r}"[:300], file=sys.stderr)
        stop.wait(LIVE_EVERY)


# ---- the service (GET only) ----

def application(environ, start_response):
    path, method = environ.get("PATH_INFO", "/"), environ["REQUEST_METHOD"]
    if method not in ("GET", "HEAD"):
        start_response("405 Method Not Allowed", [("Content-Type", "text/plain")])
        return [b"method not allowed"]
    if path == "/healthz":  # liveness: the process answers
        body, ctype, code = b"ok", "text/plain", "200 OK"
    elif path == "/readyz":  # readiness: every panel has its first snapshot
        _start_warm()
        ok = ready()
        body, ctype, code = (b"ready", "text/plain", "200 OK") if ok else (b"warming", "text/plain", "503 Service Unavailable")
    elif path == "/data":
        _start_warm()
        body = json.dumps(data(), ensure_ascii=False, default=str).encode()
        ctype, code = "application/json; charset=utf-8", "200 OK"
    else:
        body, ctype, code = b"not found", "text/plain", "404 Not Found"
    start_response(code, [("Content-Type", ctype), ("Cache-Control", "no-store"), ("Content-Length", str(len(body)))])
    return [b"" if method == "HEAD" else body]


_warm = {"stop": None}
_warm_lock = threading.Lock()


def start_warm():
    """one daemon thread per panel (MONITOR_WARM=1 starts them when gunicorn's worker imports this module)"""
    with _warm_lock:
        if _warm["stop"] is not None:
            return
        _warm["stop"] = stop = threading.Event()
        _started["at"] = time.time()
        for k in PANELS:
            threading.Thread(target=_loop, args=(k, stop), name="panel-" + k, daemon=True).start()
        threading.Thread(target=_live_loop, args=(stop,), name="live", daemon=True).start()


def stop_warm():
    with _warm_lock:
        if _warm["stop"] is not None:
            _warm["stop"].set()
            _warm["stop"] = None
        _started["at"] = None


def _start_warm():
    if os.environ.get("MONITOR_WARM") == "1":
        start_warm()


_start_warm()

if __name__ == "__main__":
    refresh_all()
    json.dump(data(), sys.stdout, ensure_ascii=False, indent=1, default=str)
