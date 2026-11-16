#!/usr/bin/env bash
# cost-profile.sh — the cost profile of YYC's news stream for a window, as files (infra/cost-profile.py explains it).
#
#   news-stream/infra/cost-profile.sh                       the last 24 hours
#   news-stream/infra/cost-profile.sh 48                    the last 48 hours
#   news-stream/infra/cost-profile.sh 2026-10-16T03:00:00+00:00 2026-10-17T03:00:00+00:00
#
# From a Jarvis 2 session with the `yyc` store (YYC_CLUSTER_SSH_KEY, YYC_HETZNER_API). Hetzner's list prices are read
# here (the monitor pod holds no Hetzner token), EUR→USD at the ECB reference rate (api.frankfurter.app); the profile
# itself runs in a one-off pod (deploy/cluster/run.sh: the database and the monitor's provider keys) and comes back as JSON. Output:
# ${OUT:-~/artifacts/cost-profile}/<start UTC>/cost-profile.md, cost-profile.csv, profile.json; the markdown is printed.
set -euo pipefail
: "${YYC_CLUSTER_SSH_KEY:?the yyc store is needed (YYC_CLUSTER_SSH_KEY)}" "${YYC_HETZNER_API:?the yyc store is needed (YYC_HETZNER_API)}"
S=~/.yyc-cluster; install -d -m 700 "$S"
[ -s "$S/admin" ] || { printf '%s\n' "$YYC_CLUSTER_SSH_KEY" > "$S/admin"; chmod 600 "$S/admin"; }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

python3 - "$TMP/hetzner.json" <<'PY'
import json, os, sys, urllib.request
def get(url, h=None):
    req = urllib.request.Request(url, headers=dict(h or {}, **{"User-Agent": "yyc-cost-profile/1"}))
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)
H = {"Authorization": "Bearer " + os.environ["YYC_HETZNER_API"]}
api = "https://api.hetzner.cloud/v1/"
pricing = get(api + "pricing", H)["pricing"]
HOURS = 730  # Hetzner bills hourly up to the monthly price; a month's price spread over 730 h
items = []
for s in get(api + "servers", H)["servers"]:
    loc = (s.get("location") or s.get("datacenter", {}).get("location"))["name"]
    p = next(x for x in s["server_type"]["prices"] if x["location"] == loc)
    m = float(p["price_monthly"]["net"])
    items.append({"name": f"server {s['name']} ({s['server_type']['name']}, {loc})", "hourly_net": m / HOURS})
    if s.get("backup_window"):
        pct = float(pricing["server_backup"]["percentage"])
        items.append({"name": f"backups of {s['name']} ({pct:g}% of the server)", "hourly_net": m * pct / 100 / HOURS})
vol = float(pricing["volume"]["price_per_gb_month"]["net"])
for v in get(api + "volumes", H)["volumes"]:
    items.append({"name": f"volume {v['name']} ({v['size']} GB)", "hourly_net": v["size"] * vol / HOURS})
for ip in get(api + "primary_ips", H)["primary_ips"]:
    if ip["type"] == "ipv4":
        p = next(x for x in pricing["primary_ips"] if x["type"] == "ipv4")["prices"][0]
        items.append({"name": f"primary IPv4 {ip['name']}", "hourly_net": float(p["price_monthly"]["net"]) / HOURS})
fx = get("https://api.frankfurter.app/latest?from=EUR&to=USD")
json.dump({"currency": pricing["currency"], "items": items, "eur_usd": fx["rates"]["USD"],
           "eur_usd_basis": f"ECB reference rate {fx['date']} via api.frankfurter.app"}, open(sys.argv[1], "w"))
PY

ARGS=()
if [ $# -eq 2 ]; then ARGS=(--start "$1" --end "$2"); elif [ $# -eq 1 ]; then ARGS=(--hours "$1"); fi
# the profile runs in a pod of its own (deploy/cluster/run.sh: never inside the monitor pod, which it OOM-killed)
SECRETS="$S" timeout 900 "$(dirname "$0")/../deploy/cluster/run.sh" --memory 1Gi --secrets monitor-keys --stdin-env HETZNER_JSON \
  -- python3 /app/infra/cost-profile.py --json --hetzner-json - ${ARGS[*]:-} < "$TMP/hetzner.json" > "$TMP/out.json"

python3 - "$TMP/out.json" "${OUT:-$HOME/artifacts/cost-profile}" <<'PY'
import json, os, sys
from datetime import datetime, timezone
d = json.load(open(sys.argv[1]))
start = datetime.fromisoformat(d["profile"]["start"]).astimezone(timezone.utc)
out = os.path.join(sys.argv[2], start.strftime("%Y-%m-%dT%H%MZ"))
os.makedirs(out, exist_ok=True)
open(os.path.join(out, "cost-profile.md"), "w").write(d["md"])
open(os.path.join(out, "cost-profile.csv"), "w").write(d["csv"])
json.dump(d["profile"], open(os.path.join(out, "profile.json"), "w"), ensure_ascii=False, indent=1, default=str)
print(d["md"])
print(f"files: {out}/cost-profile.md, cost-profile.csv, profile.json")
PY
