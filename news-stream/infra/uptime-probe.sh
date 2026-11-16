#!/bin/bash
# CronJob uptime (every minute): fetch the site through Cloudflare like a reader does and log each failure (time UTC,
# path, HTTP status, seconds) in Postgres (uptime_log.py: kv "uptime:failures", the last 90 days), so "the page was
# down" can be traced afterwards: uptime_log.py --show.
#   /           串流, rendered by the edge app (Worker yyc-web) with today's feed read from YYC's Postgres through Hyperdrive
#   /dashboard  數據分析's page, rendered by the edge app
#   /version    the edge app's build id (the Worker answers without the database)
#   /healthz    the cluster's AI server (Deployment web) through the router and the tunnel
HERE=$(cd "$(dirname "$0")/.." && pwd)
for p in / /dashboard /version /healthz; do
  r=$(curl -s -o /dev/null -m 30 -w "%{http_code} %{time_total}" "https://newswatch.yyc-internal.com$p")
  case "$r" in 200\ *) ;; *) python3 "$HERE/uptime_log.py" "$(date -u +%FT%TZ) $p $r" ;; esac
done
