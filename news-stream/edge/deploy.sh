#!/usr/bin/env bash
# Deploy the router Worker yyc-edge (worker.js) on YYC's Cloudflare account and check it: the site's pages answer from the
# edge app (X-Edge APP), the AI server's paths from the cluster (X-Edge ORIGIN), and the Access gate is still in front
# of /app. Its routes (newswatch.yyc-internal.com/*, yyc-beta.deyaochen.com/* on the SaaS zone yyc-internal.com) are OpenTofu's
# (deploy/cloudflare/tofu/saas.tf), so wrangler.toml has none.
# Needs: YYC_CF_API_TOKEN (yyc store: token yyc-edge, Workers Scripts on the YYC account) and YYC_EDGE_SECRET (yyc
# store: the X-YYC-Edge secret Worker yyc-web checks; set as this Worker's secret EDGE_SECRET on every deploy).
set -euo pipefail
cd "$(dirname "$0")"
: "${YYC_EDGE_SECRET:?YYC_EDGE_SECRET from the yyc store}"
export CLOUDFLARE_API_TOKEN="${YYC_CF_API_TOKEN:?YYC_CF_API_TOKEN from the yyc store}"
npx -y wrangler@4 deploy >/dev/null
printf '%s' "$YYC_EDGE_SECRET" | npx -y wrangler@4 secret put EDGE_SECRET >/dev/null
sleep 10
U=https://newswatch.yyc-internal.com
edge() { curl -s -o /dev/null -D - "$U$1" | tr -d '\r' | awk -F': ' 'tolower($1)=="x-edge"{print $2}'; }
for p in / /dashboard /item/1 /version; do
  e=$(edge "$p"); [ "$e" = APP ] || { echo "$p answered X-Edge '$e', not APP"; exit 1; }
done
e=$(edge "/api/ai/estimate?kind=persona"); [ "$e" = ORIGIN ] || { echo "/api/ai/estimate answered X-Edge '$e', not ORIGIN"; exit 1; }
c=$(curl -s -o /dev/null -w '%{http_code}' "$U/app/me")
[ "$c" = 302 ] || { echo "ACCESS GATE? /app/me answered $c without login"; exit 1; }
r=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "https://yyc.deyaochen.com/item/1?x=1")
[ "$r" = "301 $U/item/1?x=1" ] || { echo "the moved name yyc.deyaochen.com answered '$r', not a 301 to $U"; exit 1; }
echo "deployed: pages from the edge app, the AI server's paths from the cluster, /app behind Access (302), yyc.deyaochen.com a 301 to it"
