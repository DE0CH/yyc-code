#!/usr/bin/env python3
"""cost-report.py — YYC's model spend for one month, as OpenRouter itself records it, for reimbursement.

    OPENROUTER_MGMT=<management/provisioning key> infra/cost-report.py 2026-10 OUTDIR

Reads OpenRouter's activity API filtered to the API key "yyc-stream" (the only key the YYC server uses; hash
below) and the key's own usage counters, and writes OUTDIR/openrouter-yyc-<month>.csv (OpenRouter's rows: date,
model, provider, requests, tokens, usage USD) and OUTDIR/openrouter-yyc-<month>.html (a one-page summary to print
or save as PDF). The Hetzner side is Hetzner's own monthly invoice PDF, which lists project "yyc" separately.
Note: OpenRouter's activity API returns about the last 30 days, so run this early in the following month.
"""
import csv, html, json, os, sys, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

KEY_NAME = "yyc-stream"
KEY_HASH = "e71aa6f3decc164f61dadb0731870001b4f66c5b5c9a566c6c18b9106490df9b"


def api(path):
    req = urllib.request.Request("https://openrouter.ai/api/v1" + path,
                                 headers={"Authorization": "Bearer " + os.environ["OPENROUTER_MGMT"],
                                          "User-Agent": "yyc-cost-report/1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    month, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    rows = [r for r in api(f"/activity?api_key_hash={KEY_HASH}")["data"] if r["date"].startswith(month)]
    rows.sort(key=lambda r: (r["date"], r["model"]))
    key = next((k for k in api("/keys")["data"] if k.get("hash") == KEY_HASH), {})
    total = sum(r["usage"] for r in rows)
    reqs = sum(r["requests"] for r in rows)
    with open(os.path.join(out, f"openrouter-yyc-{month}.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "model", "provider", "requests", "prompt_tokens", "completion_tokens", "usage_usd"])
        for r in rows:
            w.writerow([r["date"][:10], r["model"], r["provider_name"], r["requests"], r["prompt_tokens"],
                        r["completion_tokens"], f"{r['usage']:.6f}"])
        w.writerow(["TOTAL", "", "", reqs, "", "", f"{total:.6f}"])
    days = defaultdict(lambda: [0, 0.0])
    for r in rows:
        days[r["date"][:10]][0] += r["requests"]
        days[r["date"][:10]][1] += r["usage"]
    made = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = "".join(f"<tr><td>{d}</td><td class=n>{n}</td><td class=n>{u:.4f}</td></tr>" for d, (n, u) in sorted(days.items()))
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>YYC model spend {month}</title><style>
body{{font:14px/1.5 -apple-system,system-ui,sans-serif;max-width:720px;margin:24px auto;padding:0 16px;color:#111;background:#fff}}
h1{{font-size:20px}} table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid #ddd;padding:4px 6px;text-align:left}}
.n{{text-align:right}} .muted{{color:#666;font-size:12px}}</style></head><body>
<h1>YYC news stream — AI model spend, {month}</h1>
<p>Provider: <b>OpenRouter</b> (openrouter.ai), account chendeyao000@gmail.com, paid by card.<br>
API key used only by YYC's news-stream server: <b>{html.escape(KEY_NAME)}</b> (key hash {KEY_HASH[:16]}…).<br>
Figures are OpenRouter's own usage records for that key (activity API), in US dollars.</p>
<p><b>Total for {month}: USD {total:.2f}</b> ({reqs} requests)</p>
<table><tr><th>Date</th><th class=n>Requests</th><th class=n>USD</th></tr>{body}
<tr><th>Total</th><th class=n>{reqs}</th><th class=n>{total:.4f}</th></tr></table>
<p class=muted>Key counters reported by OpenRouter at {made}: usage this month USD {key.get('usage_monthly', 0):.4f}, all-time
USD {key.get('usage', 0):.4f}, monthly limit USD {key.get('limit')}. Per-model detail: openrouter-yyc-{month}.csv.
Hosting for the same service is billed separately by Hetzner (project "yyc" on the Hetzner invoice).</p>
</body></html>"""
    with open(os.path.join(out, f"openrouter-yyc-{month}.html"), "w") as f:
        f.write(page)
    print(f"{month}: USD {total:.4f} over {reqs} requests; files in {out}")


if __name__ == "__main__":
    main()
