#!/usr/bin/env python3
"""cost-profile.py — what each part of YYC's news stream cost over a window, like a profiler (Deyao, 2026-10-16: "do
a cost analysis … project the cost to the whole month and see what each component uses so we can decide how to
cost-optimise"). Information only: nothing reads it to decide what the pipeline does.

    infra/cost-profile.sh [HOURS | START END]     from a Jarvis 2 session (yyc store): runs this in the monitor pod,
                                                  which holds the database and the provider keys; writes
                                                  ~/artifacts/cost-profile/<start>/cost-profile.{md,csv}
    python3 infra/cost-profile.py [--hours 24 | --start ISO --end ISO] [--json] [--hetzner-json FILE|-] [--no-live]

Per component, two kinds of figure, always labelled:
  provider   the provider's own record: OpenRouter key yyc-stream's all-time usage (GET /api/v1/key), TikHub's day
             usage and per-endpoint request counts (user/get_user_daily_usage, America/Los_Angeles days), IPRoyal
             sub-user yycstream's traffic used (× the per-GB price of its last order), AnyAPI's balance, Hetzner's list
             prices (servers, backups, volumes, IPs; --hetzner-json, else the monitor's Hetzner snapshots; the invoice
             is the billed figure), Cloudflare account YYC's subscriptions and its usage beyond the Workers Paid
             included amounts at list prices (the monitor's Cloudflare snapshots). Counters come from
             provider_snapshots (the monitor stores its balances panel every 5 min, costlog.snapshot) differenced over
             the window: the last snapshot at or before each end (the first after the start when none is before).
  our log    what the app recorded per call: ai_runs (OpenRouter's charged cost per call, purpose, model, job),
             paid_calls (each TikHub/AnyAPI request, job, endpoint; TikHub priced at its own endpoint list),
             solver_spend (each solve), kv proxy:bytes / proxy:jobbytes (bytes per site and job per HKT hour).
A component's cost is the provider figure where one exists, else our log; its sub-components (model × purpose,
proxy per site and job, TikHub per job and endpoint, solver per site) split it by our log. Solvers run on Deyao's own
accounts (EXPENSES.md "Not yet separated"): their balances are shown beside, not used.
Projection: the window's cost per day × 30 (a 30-day month), × the days of the window's calendar month, and × the
days left from the window's end to that month's end.
"""
import argparse, csv, io, json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import corpus  # noqa: E402

UA = "yyc-cost-profile/1"
MIN_COVERAGE = 0.95  # provider snapshots must span this share of the window to be used as the cost
TOP_SHARE = 0.8  # components flagged "top" until their cumulative share reaches this
SUB_TOP = 10     # the biggest sub-components across all components, listed first


def _get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=dict(headers or {}, **{"User-Agent": UA, "Accept": "application/json"}))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _q(sql, args=()):
    return corpus.db().execute(sql, args).fetchall()


# ---- provider snapshots ----

def snaps(provider, start, end, keys):
    """the provider's ok snapshots from the last one at or before start (else the first after it) to the last one at
    or before end, oldest first: [(at, data)], data holding only the top-level `keys` the caller reads. Postgres
    picks them: a month of whole IPRoyal snapshots (27 kB each, every 5 min) parsed into Python OOM-killed the
    monitor at 1 GiB (2026-11-18: the spending panel alone peaked at 957 MiB on the 18th of the month)"""
    first = _q("""SELECT at FROM provider_snapshots WHERE provider=%s AND status='ok' AND at <= %s
                  ORDER BY at DESC LIMIT 1""", (provider, start))
    lo = first[0][0] if first else start
    return [(r[0], r[1] or {}) for r in _q(
        """SELECT at, (SELECT jsonb_object_agg(k, v) FROM jsonb_each(data) AS e(k, v) WHERE k = ANY(%s))
           FROM provider_snapshots WHERE provider=%s AND status='ok' AND at >= %s AND at <= %s ORDER BY at""",
        (list(keys), provider, lo, end))]


def span(ss):
    return {"from": ss[0][0].isoformat(), "to": ss[-1][0].isoformat()} if len(ss) >= 2 else None


def span_text(ss):
    """the snapshots' span for a sentence: 'from 16 Oct 10:43 to 16:38 HKT'"""
    if len(ss) < 2:
        return "(fewer than two snapshots)"
    a, b = (x[0].astimezone(corpus.HKT) for x in (ss[0], ss[-1]))
    return f"from {a:%d %b %H:%M} to {b:%d %b %H:%M} HKT" if a.date() != b.date() else f"from {a:%d %b %H:%M} to {b:%H:%M} HKT"


def coverage(ss, start, end):
    """the share of the window the snapshots span (a provider figure is the component's cost only from MIN_COVERAGE)"""
    if len(ss) < 2:
        return 0.0
    lo, hi = max(ss[0][0], start), min(ss[-1][0], end)
    return max(0.0, (hi - lo).total_seconds() / (end - start).total_seconds())


def increase(ss, f):
    """the total rise of a monotonic counter f(data) (None skipped)"""
    vals = [f(d) for _, d in ss]
    vals = [v for v in vals if v is not None]
    return sum(max(0.0, b - a) for a, b in zip(vals, vals[1:])) if len(vals) >= 2 else None


def drops(ss, f):
    """the total fall of a balance f(data) (top-ups, which raise it, are left out)"""
    vals = [f(d) for _, d in ss]
    vals = [v for v in vals if v is not None]
    return sum(max(0.0, a - b) for a, b in zip(vals, vals[1:])) if len(vals) >= 2 else None


def day_counter(ss, f, day="usage_today_date"):
    """a per-day counter (TikHub's day usage): the rise within a day, and the whole new value when the day changes"""
    total, prev = 0.0, None
    for _, d in ss:
        v = f(d)
        if v is None:
            continue
        if prev is not None:
            total += (v - prev[1]) if d.get(day) == prev[0] else v
        prev = (d.get(day), v)
    return total if len(ss) >= 2 else None


def endpoint_counts(ss):
    """TikHub's own requests per endpoint over the snapshots (uri_counts, per TikHub day)"""
    out, prev = {}, None
    for _, d in ss:
        cur = d.get("uri_counts") or {}
        if prev is not None:
            same = d.get("usage_today_date") == prev[0]
            for k, v in cur.items():
                out[k] = out.get(k, 0) + (v - prev[1].get(k, 0) if same else v)
        prev = (d.get("usage_today_date"), cur)
    return out


# ---- components ----

def comp(cid, name, provider_usd, app_usd, provider_coverage, provider_basis, app_basis, subs, **extra):
    return dict({"id": cid, "name": name, "provider_usd": provider_usd, "app_usd": app_usd,
                 "provider_coverage": provider_coverage,
                 "provider_basis": provider_basis, "app_basis": app_basis, "subs": subs}, **extra)


def openrouter(start, end, activity=None):
    ss = snaps("openrouter", start, end, ["usage_total_usd"])
    prov = increase(ss, lambda d: d.get("usage_total_usd"))
    subs = [{"name": f"{p} × {m}", "kind": "purpose × model", "calls": n, "app_usd": float(u or 0)}
            for p, m, n, u in _q("""SELECT purpose, coalesce(model, '?'), count(*), sum(usd) FROM ai_runs
                                    WHERE at >= %s AND at < %s AND purpose <> 'agent_turn' GROUP BY 1, 2""", (start, end))]
    jobs = [{"job": j, "calls": n, "usd": float(u or 0)}
            for j, n, u in _q("""SELECT coalesce(job, '(not recorded)'), count(*), sum(usd) FROM ai_runs
                                 WHERE at >= %s AND at < %s AND purpose <> 'agent_turn' GROUP BY 1 ORDER BY 3 DESC""",
                              (start, end))]
    app = sum(s["app_usd"] for s in subs)
    return comp("openrouter", "OpenRouter (AI models, key yyc-stream)", prov, app, coverage(ss, start, end),
                "OpenRouter GET /api/v1/key: key yyc-stream's all-time usage, risen over " + json.dumps(span(ss)),
                "ai_runs: OpenRouter's charged cost per call (usage.cost), by purpose and model", subs,
                by_job=jobs, provider_by_model=activity)


def current_models(end, rate_hours=24, lookback_days=35):
    """per purpose (agent_turn left out: its calls are logged again under their own purpose), the model in use at `end`
    (the model of its latest call), since when (its first call after the last call of another model), the model it
    replaced and that model's last call, its spend since it took over, and its rate now: USD per hour over the last
    `rate_hours`, or since it took over when that is later (Deyao, 2026-10-16: a switch, e.g. transcription from
    Gemini 4 Argon to Qwen3-ASR on 2026-10-15, shows at once). Looks back `lookback_days`: a model in use longer shows
    its first call in that time as `since` (previous_model None)."""
    ms = {}
    for p, m, lo, hi, n, u in _q("""SELECT purpose, coalesce(model, '?'), min(at), max(at), count(*), coalesce(sum(usd), 0)
                                   FROM ai_runs WHERE at >= %s AND at < %s AND purpose <> 'agent_turn' GROUP BY 1, 2""",
                                (end - timedelta(days=lookback_days), end)):
        ms.setdefault(p, []).append({"model": m, "first": lo, "last": hi, "calls": n, "usd": float(u)})
    out = []
    for p, rows in ms.items():
        rows.sort(key=lambda r: r["last"])
        cur, prev = rows[-1], (rows[-2] if len(rows) > 1 else None)
        since = cur["first"]
        if prev and prev["last"] > since:  # the models took turns: the current run starts after the other's last call
            since = _q("""SELECT min(at) FROM ai_runs WHERE purpose=%s AND coalesce(model, '?')=%s AND at > %s AND at < %s""",
                       (p, cur["model"], prev["last"], end))[0][0]
        frm = max(since, end - timedelta(hours=rate_hours))
        (usd_since,), (usd_rate,) = (_q("""SELECT coalesce(sum(usd), 0) FROM ai_runs WHERE purpose=%s
                                           AND coalesce(model, '?')=%s AND at >= %s AND at < %s""", (p, cur["model"], t, end))[0]
                                     for t in (since, frm))
        hrs = max((end - frm).total_seconds() / 3600, 1 / 60)
        out.append({"purpose": p, "model": cur["model"], "since": since.isoformat(), "last_call": cur["last"].isoformat(),
                    "previous_model": prev["model"] if prev else None,
                    "replaced_at": prev["last"].isoformat() if prev else None, "usd_since": float(usd_since),
                    "rate_usd_per_hour": float(usd_rate) / hrs, "rate_from": frm.isoformat(), "rate_hours": round(hrs, 3)})
    out.sort(key=lambda r: -r["rate_usd_per_hour"])
    return out


def platform(endpoint):
    """the platform an endpoint is for: TikHub's /api/v1/<platform>/…, AnyAPI's <platform>.<call>"""
    parts = [x for x in (endpoint or "").split("/") if x]
    if len(parts) >= 3 and parts[0] == "api":
        return parts[2]
    return (endpoint or "?").split(".", 1)[0]


def calls_by_job(provider, start, end, prices):
    """paid_calls of a provider per job, each job's endpoints, biggest USD first. A failed call (not ok: HTTP 400, a
    402 refusal…) costs nothing (TikHub bills successful requests only: its own per-endpoint counts match our
    successful calls, 2026-10-16) and is counted apart with its statuses. USD per ok call: the cost the provider
    stated in the response (AnyAPI's costUsd), else prices[endpoint] (TikHub's endpoint list), else AnyAPI's USD 0.03
    list price for AnyAPI and nothing for an unpriced TikHub endpoint."""
    jobs = {}
    for j, ep, status, ok, n, u in _q("""SELECT coalesce(job, '(not recorded)'), endpoint, status, ok, count(*), sum(usd)
                                         FROM paid_calls WHERE provider=%s AND at >= %s AND at < %s
                                         GROUP BY 1, 2, 3, 4""", (provider, start, end)):
        e = jobs.setdefault(j, {}).setdefault(ep, {"endpoint": ep, "platform": platform(ep), "calls": 0,
                                                    "failed_calls": 0, "failed_statuses": {},
                                                    "unit_usd": prices.get(ep), "usd": 0.0})
        if ok:
            e["calls"] += n
            if u is not None:
                e["usd"] += float(u)
            elif ep in prices:
                e["usd"] += n * prices[ep]
            elif provider == "anyapi":
                e["usd"] += n * 0.03
        else:
            e["failed_calls"] += n
            st = str(status) if status is not None else "no answer"
            e["failed_statuses"][st] = e["failed_statuses"].get(st, 0) + n
    out = []
    for j, eps in jobs.items():
        es = sorted(eps.values(), key=lambda e: (-e["usd"], -e["calls"], e["endpoint"]))
        out.append({"job": j, "usd": sum(e["usd"] for e in es), "calls": sum(e["calls"] for e in es),
                    "failed_calls": sum(e["failed_calls"] for e in es), "endpoints": es})
    out.sort(key=lambda r: (-r["usd"], -r["calls"], r["job"]))
    return {"usd": sum(r["usd"] for r in out), "calls": sum(r["calls"] for r in out),
            "failed_calls": sum(r["failed_calls"] for r in out), "by_job": out}


def tikhub(start, end, prices):
    ss = snaps("tikhub", start, end, ["usage_today_usd", "usage_today_date", "uri_counts"])
    prov = day_counter(ss, lambda d: d.get("usage_today_usd"))
    eps = endpoint_counts(ss)
    p_eps = sorted(({"endpoint": k, "calls": v, "usd": v * prices[k] if k in prices else None}
                    for k, v in eps.items() if v), key=lambda e: -(e["usd"] or 0))
    cb = calls_by_job("tikhub", start, end, prices)
    subs = [{"name": f"{j['job']} · {e['endpoint']}", "kind": "job · endpoint", "calls": e["calls"],
             "failed_calls": e["failed_calls"], "unit_usd": e["unit_usd"], "app_usd": e["usd"]}
            for j in cb["by_job"] for e in j["endpoints"]]
    unpriced = sorted({e["endpoint"] for j in cb["by_job"] for e in j["endpoints"] if e["unit_usd"] is None})
    return comp("tikhub", "TikHub (social platforms; YYC account)", prov, cb["usd"],
                coverage(ss, start, end),
                "TikHub user/get_user_daily_usage: its charged USD per day, risen " + span_text(ss),
                "paid_calls: our count of successful requests × TikHub's own endpoint price (get_all_endpoints_info)",
                subs, provider_calls=sum(eps.values()) if ss else None, provider_endpoints=p_eps,
                unpriced_endpoints=unpriced, by_job=cb["by_job"], calls=cb["calls"], failed_calls=cb["failed_calls"])


def hours(start, end):
    """the HKT hour keys whose hour starts in [start rounded down to the hour, end)"""
    t = start.astimezone(corpus.HKT).replace(minute=0, second=0, microsecond=0)
    out = []
    while t < end:
        out.append(t.strftime("%Y-%m-%dT%H"))
        t += timedelta(hours=1)
    return out


# IPRoyal's GB is decimal: 1 GB = 10^9 bytes. Its data-usage-report in B, KB, MB and GB gives 8323 B = 8.323 KB =
# 0.008323 MB, 1093325 B = 0.001093 GB (2026-10-16), and sub-user yycstream's traffic_used (GB, what a "10 GB" top-up
# adds to) read 21.708 against the report's 21.895 × 10^9 bytes to that day (20.39 GiB).
BYTES_PER_GB = 10 ** 9
BYTES_PER_MB = 10 ** 6


def proxy_traffic(start, end):
    """IPRoyal traffic in BYTES (never USD; Deyao, 2026-10-16): our count per job and each job's sites, biggest first
    (kv proxy:jobbytes per job, site and HKT hour; a site's hourly bytes beyond its jobs' as "(job not recorded)"),
    beside IPRoyal's own figure: sub-user yycstream's traffic_used risen over the window (provider snapshots)."""
    ss = snaps("iproyal", start, end, ["subusers"])
    used = lambda d: sum(float(s.get("traffic_used") or 0) for s in d.get("subusers") or [])  # noqa: E731
    gb = increase(ss, used)
    hs = hours(start, end)
    site, jobsite, seen = {}, {}, set()
    for k, v in _q("""SELECT k, v FROM kv WHERE (k LIKE 'proxy:bytes:%%' OR k LIKE 'proxy:jobbytes:%%')
                      AND right(k, 13) = ANY(%s)""", (hs,)):
        parts = k.split(":")
        if parts[-1] not in hs:
            continue
        n = float(v)
        seen.add(parts[-1])
        if parts[1] == "bytes":
            site[parts[2]] = site.get(parts[2], 0) + n
        else:
            key = (parts[2], ":".join(parts[3:-1]))
            jobsite[key] = jobsite.get(key, 0) + n
    for s, n in site.items():
        rest = n - sum(v for (_, s2), v in jobsite.items() if s2 == s)
        if rest > n * 0.001:
            jobsite[("(job not recorded)", s)] = rest
    jobs = {}
    for (j, s), n in jobsite.items():
        jobs.setdefault(j, []).append({"site": s, "bytes": int(round(n))})
    by_job = [{"job": j, "bytes": sum(x["bytes"] for x in xs), "sites": sorted(xs, key=lambda x: (-x["bytes"], x["site"]))}
              for j, xs in jobs.items()]
    by_job.sort(key=lambda r: (-r["bytes"], r["job"]))
    return {"bytes": sum(r["bytes"] for r in by_job), "by_job": by_job, "bytes_per_gb": BYTES_PER_GB,
            "counted_from": min(seen) if seen else None,  # the first HKT hour with a count in the window
            "provider_bytes": int(round(gb * BYTES_PER_GB)) if gb is not None else None,
            "provider_coverage": coverage(ss, start, end),
            "provider_basis": "IPRoyal /residential-subusers: sub-user yycstream's traffic_used (GB, 1 GB = 10^9 bytes) "
                              "risen " + span_text(ss) + "; IPRoyal's counters lag by hours",
            "basis": "kv proxy:jobbytes per job, site and HKT hour (resi.count: curl's byte counts, "
                     "compressed bodies, the browser's request sizes)"}


def iproyal(start, end):
    ss = snaps("iproyal", start, end, ["price_per_gb_usd"])
    price = next((d.get("price_per_gb_usd") for _, d in reversed(ss) if d.get("price_per_gb_usd")), None)
    pt = proxy_traffic(start, end)
    gb = pt["provider_bytes"] / BYTES_PER_GB if pt["provider_bytes"] is not None else None
    prov = gb * price if gb is not None and price else None
    total = pt["bytes"]
    subs = [{"name": f"{x['site']} ({j['job'].strip('()')})", "kind": "site (job)", "bytes": x["bytes"],
             "gb": round(x["bytes"] / BYTES_PER_GB, 4), "share_of_component": x["bytes"] / total if total else 0,
             "app_usd": x["bytes"] / BYTES_PER_GB * (price or 0)}
            for j in pt["by_job"] for x in j["sites"]]
    return comp("iproyal", "IPRoyal residential proxy (sub-user yycstream)", prov, total / BYTES_PER_GB * (price or 0),
                pt["provider_coverage"],
                f"IPRoyal /residential-subusers: yycstream's traffic_used risen {gb if gb is None else round(gb, 4)} GB "
                f"over {json.dumps(span(ss))} × USD {price}/GB (its last order); IPRoyal's counters lag by hours",
                "kv proxy:bytes / proxy:jobbytes: curl's byte counts per site and job (resi.count) × the same price",
                subs, provider_gb=gb, price_per_gb_usd=price)


def anyapi(start, end):
    ss = snaps("anyapi", start, end, ["balance_usd"])
    prov = drops(ss, lambda d: d.get("balance_usd"))
    cb = calls_by_job("anyapi", start, end, {})
    subs = [{"name": f"{j['job']} · {e['endpoint']}", "kind": "job · endpoint", "calls": e["calls"],
             "failed_calls": e["failed_calls"], "app_usd": e["usd"]}
            for j in cb["by_job"] for e in j["endpoints"]]
    return comp("anyapi", "AnyAPI (Facebook search)", prov, cb["usd"], coverage(ss, start, end),
                "AnyAPI GET /v1/balance: its fall " + span_text(ss),
                "paid_calls: costUsd per search (else the USD 0.03 list price)", subs,
                by_job=cb["by_job"], calls=cb["calls"], failed_calls=cb["failed_calls"])


def solvers(start, end):
    subs = [{"name": f"{p} · {site}", "kind": "solver · site", "calls": n, "app_usd": float(u or 0)}
            for p, site, n, u in _q("""SELECT provider, purpose, count(*), sum(usd) FROM solver_spend
                                       WHERE at::timestamptz >= %s AND at::timestamptz < %s GROUP BY 1, 2""", (start, end))]
    shared = {}
    for p in ("capsolver", "2captcha"):
        ss = snaps(p, start, end, ["balance_usd"])
        shared[p] = drops(ss, lambda d: d.get("balance_usd"))
    return comp("solvers", "Captcha solvers (CapSolver, 2Captcha; Deyao's own accounts)", None,
                sum(s["app_usd"] for s in subs), None, None,
                "solver_spend: each solve's charge as the provider states it (2Captcha's cost per task, CapSolver's "
                "list price)", subs, shared_account_balance_drop_usd=shared,
                note="the accounts are shared with Deyao's other work, so their balance drops are not YYC's alone")


def hetzner_comp(start, end, h, eur_usd=None):
    if not h:
        return hetzner_snapshots(start, end, eur_usd)
    hrs = (end - start).total_seconds() / 3600
    rate = float(h.get("eur_usd") or 1)
    subs = [{"name": i["name"], "kind": "resource", "app_usd": None,
             "provider_usd": i["hourly_net"] * hrs * rate} for i in h["items"]]
    return comp("hetzner", "Hetzner Cloud (project yyc)", sum(s["provider_usd"] for s in subs), None, 1.0,
                f"Hetzner API list prices (net of VAT; the monthly price spread over its hours: Hetzner bills hourly up "
                f"to the monthly price), EUR → USD at {rate} ({h.get('eur_usd_basis', 'given')}); the invoice is the "
                f"billed figure", None, subs, currency=h.get("currency"))


HOURS_PER_MONTH = 730  # a monthly price spread over its hours (Hetzner bills hourly up to the monthly price)


def hetzner_snapshots(start, end, eur_usd=None):
    """Hetzner from the monitor's snapshots (bal_hetzner, read-only token yyc-monitor): the project's list price per
    month (servers, backups, IPv4, volumes; net of VAT) spread over the window's hours"""
    name = "Hetzner Cloud (project yyc)"
    ss = snaps("hetzner", start, end, ["list_monthly_net"])
    vals = [float(d["list_monthly_net"]) for _, d in ss if d.get("list_monthly_net") is not None]
    if not vals or not eur_usd:
        return comp("hetzner", name, None, None, None, None, None, [],
                    note="no Hetzner figures: " + ("no ok snapshot of the monitor's Hetzner card in the window"
                                                   if not vals else "no EUR → USD rate"))
    hrs = (end - start).total_seconds() / 3600
    monthly = sum(vals) / len(vals)
    usd = monthly / HOURS_PER_MONTH * hrs * float(eur_usd)
    return comp("hetzner", name, usd, None, coverage(ss, start, end),
                f"Hetzner API list prices from the monitor's snapshots (EUR {monthly:.4f}/month net of VAT, spread over "
                f"{HOURS_PER_MONTH} h), EUR → USD at {eur_usd}; the invoice is the billed figure", None,
                [{"name": "project yyc at list prices", "kind": "resource", "app_usd": None, "provider_usd": usd}],
                currency="EUR")


def period_counter(ss, f, period="period_start"):
    """a counter that restarts each billing period (Cloudflare's usage since the period's start): the rise within a
    period, and the whole new value when the period changes"""
    total, prev = 0.0, None
    for _, d in ss:
        v = f(d)
        if v is None:
            continue
        if prev is not None:
            total += max(0.0, v - prev[1]) if d.get(period) == prev[0] else v
        prev = (d.get(period), v)
    return total if len(ss) >= 2 else None


def cloudflare(start, end):
    """Cloudflare account YYC from the monitor's snapshots (bal_cloudflare, token yyc-monitor): the subscriptions'
    monthly price spread over the window's hours, plus the rise of the usage beyond the plan's included amounts (at
    Cloudflare's list prices)"""
    name = "Cloudflare (account YYC)"
    ss = snaps("cloudflare", start, end, ["subscriptions_monthly_usd", "overage_usd", "usage", "period_start"])
    if not ss:
        return comp("cloudflare", name, None, None, None, None, None, [],
                    note="no ok snapshot of the monitor's Cloudflare card in the window")
    hrs = (end - start).total_seconds() / 3600
    monthly = sum(float(d.get("subscriptions_monthly_usd") or 0) for _, d in ss) / len(ss)
    sub = monthly / HOURS_PER_MONTH * hrs
    over = period_counter(ss, lambda d: d.get("overage_usd")) or 0.0
    ids = {u["id"] for _, d in ss for u in d.get("usage") or []}
    rise = {i: period_counter(ss, lambda d, i=i: next((u["used"] for u in d.get("usage") or [] if u["id"] == i), None))
            for i in sorted(ids)}
    subs = [{"name": f"subscriptions (Workers Paid USD {monthly:.2f}/month)", "kind": "subscription", "app_usd": None,
             "provider_usd": sub},
            {"name": "usage beyond the included amounts", "kind": "usage", "app_usd": None, "provider_usd": over}]
    return comp("cloudflare", name, sub + over, None, coverage(ss, start, end),
                f"Cloudflare's subscriptions (USD {monthly:.2f}/month spread over {HOURS_PER_MONTH} h) + the rise of "
                f"the usage beyond the Workers Paid included amounts at list prices (GraphQL analytics); the invoice is "
                f"the billed figure", None, subs, usage_increase=rise)


FREE = [("Exa", "free tier (the data API's web search): USD 10.00 of credit each month; Exa's own charged cost per call is "
                "on the monitor's Exa card (tool_spend)")]


# ---- the profile ----

def profile(start, end, tikhub_prices=None, hetzner=None, live=False, activity=None, eur_usd=None):
    comps = [openrouter(start, end, activity), tikhub(start, end, tikhub_prices or {}), iproyal(start, end),
             anyapi(start, end), solvers(start, end), hetzner_comp(start, end, hetzner, eur_usd), cloudflare(start, end)]
    for c in comps:
        use = c["provider_usd"] is not None and (c["provider_coverage"] or 0) >= MIN_COVERAGE
        c["cost_usd"] = c["provider_usd"] if use else (c["app_usd"] or 0)
        c["cost_source"] = "provider" if use else "our log"
        if c["provider_usd"] is not None and not use:
            c["cost_note"] = (f"the provider's snapshots span only {c['provider_coverage']:.0%} of the window, so its "
                              f"figure is shown but the cost is our log")
        base = sum((s.get("app_usd") or s.get("provider_usd") or 0) for s in c["subs"])
        for s in c["subs"]:
            v = s.get("provider_usd") if s.get("provider_usd") is not None else (s.get("app_usd") or 0)
            s.setdefault("share_of_component", v / base if base else 0)
            s["cost_usd"] = c["cost_usd"] * s["share_of_component"]  # the component's cost split by our log
        c["subs"].sort(key=lambda s: (-s["cost_usd"], -(s.get("app_usd") or 0)))
    total = sum(c["cost_usd"] for c in comps)
    comps.sort(key=lambda c: -c["cost_usd"])
    cum = 0.0
    for c in comps:
        c["share"] = c["cost_usd"] / total if total else 0
        c["top"] = cum < TOP_SHARE and c["cost_usd"] > 0
        cum += c["share"]
    days = (end - start).total_seconds() / 86400
    per_day = total / days if days else 0
    t = end.astimezone(corpus.HKT)
    first = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (first + timedelta(days=32)).replace(day=1)
    month_days = (nxt - first).days
    left = (nxt - t).total_seconds() / 86400
    for c in comps:
        c["per_30_days_usd"] = c["cost_usd"] / days * 30 if days else 0
        for s in c["subs"]:
            s["share_of_total"] = s["cost_usd"] / total if total else 0
            s["per_30_days_usd"] = s["cost_usd"] / days * 30 if days else 0
    top_subs = sorted(({"component": c["id"], **s} for c in comps for s in c["subs"]),
                      key=lambda s: -s["cost_usd"])[:SUB_TOP]
    return {"start": start.isoformat(), "end": end.isoformat(), "days": days, "total_usd": total,
            "components": comps, "top_subs": top_subs, "free": FREE, "current_models": current_models(end),
            "projection": {"per_day_usd": per_day, "per_30_days_usd": per_day * 30,
                           "calendar_month": first.strftime("%Y-%m"), "per_calendar_month_usd": per_day * month_days,
                           "month_end_usd": per_day * left, "days_left_in_month": left,
                           "basis": f"the window's cost per day (USD {per_day:.4f}, {days:.3f} days) × 30; × the "
                                    f"{month_days} days of {first:%Y-%m}; × the {left:.2f} days from the window's end "
                                    f"to the month's end (HKT). Linear: the window's mix and volume are assumed to "
                                    f"continue; one-off backfills in the window count as recurring"}}


def fmt(v, d=4):
    return "—" if v is None else f"{v:,.{d}f}"


def markdown(p):
    s = datetime.fromisoformat(p["start"]).astimezone(timezone.utc)
    e = datetime.fromisoformat(p["end"]).astimezone(timezone.utc)
    pr = p["projection"]
    out = [f"# YYC news stream: cost profile", "",
           f"Window: **{s:%Y-%m-%d %H:%M} UTC → {e:%Y-%m-%d %H:%M} UTC** ({p['days'] * 24:.1f} h; "
           f"{s.astimezone(corpus.HKT):%Y-%m-%d %H:%M} → {e.astimezone(corpus.HKT):%Y-%m-%d %H:%M} HKT)", "",
           f"**Total: USD {p['total_usd']:.4f}** in the window · USD {pr['per_30_days_usd']:.2f} per 30-day month · "
           f"USD {pr['per_calendar_month_usd']:.2f} for {pr['calendar_month']} at this rate · "
           f"USD {pr['month_end_usd']:.2f} for the {pr['days_left_in_month']:.2f} days left in {pr['calendar_month']}", "",
           f"Projection basis: {pr['basis']}.", "",
           "Figures: **provider** = the provider's own record; **our log** = what the app recorded. A component's cost "
           "is the provider figure where one exists; its parts split it by our log.", "",
           "## Components", "",
           "| | Component | Cost USD | Source | Provider USD | Our log USD | Share | Per 30 days |",
           "|---|---|---:|---|---:|---:|---:|---:|"]
    for c in p["components"]:
        out.append(f"| {'**TOP**' if c['top'] else ''} | {c['name']} | {fmt(c['cost_usd'])} | {c['cost_source']} | "
                   f"{fmt(c['provider_usd'])} | {fmt(c['app_usd'])} | {c['share']:.1%} | {fmt(c['per_30_days_usd'], 2)} |")
    out += ["", f"## Top {SUB_TOP} parts", "", "| Component | Part | Cost USD | Share of total | Per 30 days |",
            "|---|---|---:|---:|---:|"]
    for t in p["top_subs"]:
        out.append(f"| {t['component']} | {t['name']} | {fmt(t['cost_usd'])} | {t['share_of_total']:.1%} | "
                   f"{fmt(t['per_30_days_usd'], 2)} |")
    for c in p["components"]:
        out += ["", f"## {c['name']}", "", f"- Cost: USD {fmt(c['cost_usd'])} ({c['cost_source']}), "
                f"{c['share']:.1%} of the total, USD {fmt(c['per_30_days_usd'], 2)} per 30 days"]
        if c.get("provider_basis"):
            out.append(f"- Provider: USD {fmt(c['provider_usd'])} — {c['provider_basis']}")
        if c.get("app_basis"):
            out.append(f"- Our log: USD {fmt(c['app_usd'])} — {c['app_basis']}")
        for k in ("cost_note", "note", "unpriced_endpoints", "shared_account_balance_drop_usd", "provider_calls"):
            if c.get(k):
                out.append(f"- {k.replace('_', ' ')}: {json.dumps(c[k], ensure_ascii=False)}")
        if c["subs"]:
            out += ["", "| Part | Calls | GB | Our log USD | Cost USD | Share of component |",
                    "|---|---:|---:|---:|---:|---:|"]
            for s in c["subs"][:40]:
                out.append(f"| {s['name']} | {s.get('calls', '')} | {s.get('gb', '')} | {fmt(s.get('app_usd'))} | "
                           f"{fmt(s['cost_usd'])} | {s['share_of_component']:.1%} |")
            if len(c["subs"]) > 40:
                out.append(f"| … {len(c['subs']) - 40} more in the CSV | | | | | |")
        if c.get("by_job"):
            out += ["", "By job (our log): " + ", ".join(f"{j['job']} USD {j['usd']:.4f} ({j['calls']} calls)"
                                                         for j in c["by_job"])]
        if c.get("provider_endpoints"):
            out += ["", "TikHub's own counts per endpoint (provider): " + ", ".join(
                f"{e['endpoint']} {e['calls']} × = USD {fmt(e['usd'])}" for e in c["provider_endpoints"][:15])]
        if c.get("provider_by_model"):
            out += ["", "OpenRouter's activity for the UTC days the window touches (provider, whole days): " + ", ".join(
                f"{r['model']} USD {r['usd']:.4f}" for r in c["provider_by_model"])]
    if p.get("current_models"):
        out += ["", "## AI models in use at the window's end (our log: ai_runs)", "",
                "| Purpose | Model | Since | Replaced | USD per hour now | Rate basis |", "|---|---|---|---|---:|---|"]
        for m in p["current_models"]:
            out.append(f"| {m['purpose']} | {m['model']} | {m['since'][:16]} | "
                       f"{(m['previous_model'] + ' (last call ' + m['replaced_at'][:16] + ')') if m['previous_model'] else '—'} | "
                       f"{m['rate_usd_per_hour']:.4f} | spend over the {m['rate_hours']:.1f} h from {m['rate_from'][:16]} |")
    out += ["", "## No spend", ""] + [f"- {n}: {why}" for n, why in p["free"]]
    return "\n".join(out) + "\n"


def to_csv(p):
    f = io.StringIO()
    w = csv.writer(f)
    w.writerow(["component", "sub", "kind", "calls", "gb", "provider_usd", "app_usd", "cost_usd", "share_of_total",
                "share_of_component", "per_30_days_usd", "top", "basis"])
    for c in p["components"]:
        w.writerow([c["id"], "", "component", "", "", c["provider_usd"], c["app_usd"], round(c["cost_usd"], 6),
                    round(c["share"], 6), 1, round(c["per_30_days_usd"], 4), c["top"],
                    c["provider_basis"] if c["cost_source"] == "provider" else c["app_basis"]])
        for s in c["subs"]:
            w.writerow([c["id"], s["name"], s.get("kind"), s.get("calls"), s.get("gb"), s.get("provider_usd"),
                        s.get("app_usd"), round(s["cost_usd"], 6), round(s["share_of_total"], 6),
                        round(s["share_of_component"], 6), round(s["per_30_days_usd"], 4), "", "split by our log"])
    pr = p["projection"]
    w.writerow(["TOTAL", "", "", "", "", "", "", round(p["total_usd"], 6), 1, "", round(pr["per_30_days_usd"], 4), "",
                pr["basis"]])
    return f.getvalue()


# ---- live inputs (in the monitor pod, which has the keys) ----

def live_inputs(start, end):
    got = {"tikhub_prices": {}, "activity": None, "eur_usd": None}
    try:  # Hetzner bills in EUR: the ECB reference rate
        got["eur_usd"] = float(_get("https://api.frankfurter.app/latest?from=EUR&to=USD")["rates"]["USD"])
    except Exception as e:  # noqa: BLE001
        print(f"cost-profile: EUR→USD rate failed: {e!r}"[:300], file=sys.stderr)
    try:  # a fresh snapshot, so the window's end has the providers' figures as of now
        import costlog, monitor
        costlog.snapshot(monitor.balances().get("providers"))
    except Exception as e:  # noqa: BLE001
        print(f"cost-profile: fresh snapshot failed: {e!r}"[:300], file=sys.stderr)
    got["tikhub_prices"] = tikhub_prices()
    mgmt = os.environ.get("OPENROUTER_MANAGEMENT_KEY")
    if mgmt:
        try:
            spec = __import__("importlib.util").util.spec_from_file_location(
                "cost_report", os.path.join(HERE, "infra", "cost-report.py"))
            days = {(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((end - start).days + 2)}
            d = _get(f"https://openrouter.ai/api/v1/activity?api_key_hash={_key_hash(spec)}",
                     {"Authorization": "Bearer " + mgmt})
            by = {}
            for r in d.get("data") or []:
                if r["date"][:10] in days and r["date"][:10] <= end.strftime("%Y-%m-%d"):
                    by[r["model"]] = by.get(r["model"], 0) + float(r["usage"])
            got["activity"] = [{"model": m, "usd": u} for m, u in sorted(by.items(), key=lambda x: -x[1])]
        except Exception as e:  # noqa: BLE001
            print(f"cost-profile: OpenRouter activity failed: {e!r}"[:300], file=sys.stderr)
    return got


def tikhub_prices():
    """TikHub's own price per endpoint (get_all_endpoints_info, free): {endpoint: USD}; {} without TIKHUB_API"""
    key = os.environ.get("TIKHUB_API")
    if not key:
        return {}
    try:
        d = _get("https://api.tikhub.io/api/v1/tikhub/user/get_all_endpoints_info", {"Authorization": "Bearer " + key})
        return {e["endpoint_uri"]: float(e["endpoint_cost"]) for e in d.get("data") or []}
    except Exception as e:  # noqa: BLE001
        print(f"cost-profile: TikHub prices failed: {e!r}"[:300], file=sys.stderr)
        return {}


def _key_hash(spec):
    m = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.KEY_HASH


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--hetzner-json", help="Hetzner figures (cost-profile.sh makes them), or - for stdin")
    ap.add_argument("--json", action="store_true", help="print {md, csv, profile} as one JSON document")
    ap.add_argument("--no-live", action="store_true")
    a = ap.parse_args()
    end = datetime.fromisoformat(a.end) if a.end else datetime.now(timezone.utc)
    start = datetime.fromisoformat(a.start) if a.start else end - timedelta(hours=a.hours)
    if start.tzinfo is None or end.tzinfo is None:
        sys.exit("give --start/--end with a time zone, e.g. 2026-10-16T03:00:00+00:00")
    het = None
    if a.hetzner_json:
        het = json.load(sys.stdin if a.hetzner_json == "-" else open(a.hetzner_json))
    li = {} if a.no_live else live_inputs(start, end)
    p = profile(start, end, tikhub_prices=li.get("tikhub_prices"), hetzner=het, live=not a.no_live,
                activity=li.get("activity"), eur_usd=li.get("eur_usd"))
    md, cs = markdown(p), to_csv(p)
    if a.json:
        print(json.dumps({"md": md, "csv": cs, "profile": p}, ensure_ascii=False, default=str))
    else:
        print(md)


if __name__ == "__main__":
    main()
