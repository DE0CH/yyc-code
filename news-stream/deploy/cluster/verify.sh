#!/bin/bash
# verify.sh — checks of the cluster and of what it serves, from outside and inside; changes nothing that stays.
#   1. nodes Ready, apps nodes labelled, control plane tainted; every node's IPv4 out through its own WARP account
#   2. every Deployment available; CronJobs' last runs; PVCs bound
#   3. from a pod: IPv4 out (github, ghcr.io), the residential proxy, DNS; Services web, wisers, mailmerge
#   4. mail merge isolation: no path from namespace mailmerge to yyc, nor from yyc to mailmerge
#   5. through Cloudflare (the tunnels and Access): /healthz, /app/wisers/api/, mailmerge.deyaochen.com, with
#      a temporary Access service token and temporary policies on the three Access apps (both removed on exit)
# Env: SECRETS (cluster.sh), YYC_CF_ACCESS_EDGE_TOKEN (yyc store: Access apps + service tokens on YYC's Cloudflare
#      account; the temporary policies are also reverted by the next tofu run if the cleanup is missed).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${SECRETS:?}"
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
c() { SECRETS=$SECRETS "$HERE/cluster.sh" ssh "$@"; }

echo "== nodes"
c 'kubectl get nodes --no-headers -o custom-columns=N:.metadata.name,S:.status.conditions[-1].type,P:.metadata.labels.pool,T:.spec.taints[0].key' | while read -r n s p t; do
  case "$n" in *master*) [ "$t" = CriticalAddonsOnly ] && ok "$n $s tainted" || bad "$n not tainted";; *) [ "$p" = apps ] && ok "$n $s pool=$p" || bad "$n pool=$p";; esac
done
for ip in $(SECRETS=$SECRETS "$HERE/cluster.sh" nodes | awk '{print $4}'); do
  r=$(ssh -i "$SECRETS/admin" -o UserKnownHostsFile="$SECRETS/known_hosts" -o StrictHostKeyChecking=accept-new "root@$ip" 'echo "$(hostname) key=$(wg show wgwarp public-key | cut -c1-8) $(curl -4 -s -m 10 https://www.cloudflare.com/cdn-cgi/trace | grep -E "^warp=")"')
  case "$r" in *warp=on*) ok "$r";; *) bad "$r";; esac
done

echo "== workloads"
c 'kubectl get deploy -A --no-headers' | awk '{split($3,r,"/"); print (r[1]==r[2] ? "ok" : "bad"), $1"/"$2, $3}' | while read -r s l; do [ "$s" = ok ] && ok "$l" || bad "not available: $l"; done
c 'kubectl get pvc -A --no-headers' | awk '{print $1"/"$2" "$3}' | while read -r l; do case "$l" in *Bound) ok "pvc $l";; *) echo "note pvc $l (binds at its first pod)";; esac; done
c 'kubectl -n yyc get cronjobs --no-headers -o custom-columns=N:.metadata.name,S:.spec.suspend,L:.status.lastScheduleTime,OK:.status.lastSuccessfulTime' | sed 's/^/     /'

echo "== from a pod"
out=$(c 'kubectl -n yyc delete pod verify-net --ignore-not-found --wait >/dev/null; IMG=$(kubectl -n yyc get deploy web -o jsonpath="{.spec.template.spec.containers[0].image}")
kubectl -n yyc run verify-net --image=$IMG --restart=Never --overrides="{\"spec\":{\"imagePullSecrets\":[{\"name\":\"ghcr-pull\"}],\"containers\":[{\"name\":\"verify-net\",\"image\":\"$IMG\",\"command\":[\"sleep\",\"600\"],\"envFrom\":[{\"secretRef\":{\"name\":\"app-env\"}}],\"resources\":{\"requests\":{\"cpu\":\"50m\",\"memory\":\"128Mi\"},\"limits\":{\"cpu\":\"500m\",\"memory\":\"128Mi\"}}}]}}" >/dev/null
kubectl -n yyc wait --for=condition=Ready pod/verify-net --timeout=300s >/dev/null || echo "pod not ready"
e() { kubectl -n yyc exec verify-net -- sh -c "$1"; }
for u in https://github.com https://ghcr.io/v2/ https://news.rthk.hk http://web.yyc/healthz http://wisers.yyc:8899/healthz http://mailmerge.mailmerge/healthz; do echo "GET $u $(e "curl -s -o /dev/null -m 15 -w %{http_code} $u")"; done
echo "RESI_PROXY $(e "curl -s -m 30 -x \"\$RESI_PROXY\" -o /dev/null -w %{http_code} https://ipv4.icanhazip.com")"
kubectl -n yyc delete pod verify-net --wait=false >/dev/null
for u in http://web.yyc.svc.cluster.local/healthz http://10.0.0.3:6443/; do echo "FROM-MAILMERGE $u $(kubectl -n mailmerge exec deploy/mailmerge -- node -e "const h=require(\"http\");const r=h.get(\"$u\",{timeout:5000},x=>{console.log(x.statusCode);process.exit()});r.on(\"timeout\",()=>{console.log(\"blocked\");process.exit()});r.on(\"error\",e=>{console.log(\"blocked:\"+e.code);process.exit()})")"; done')
echo "$out" | while read -r l; do case "$l" in
  "GET http://mailmerge"*) case "$l" in *" 000") ok "$l (blocked: mailmerge admits only its own pods)";; *) bad "$l (mailmerge reachable from yyc)";; esac;;
  GET*" 000"|GET*" 5"??) bad "$l";; GET*) ok "$l";;
  RESI*" 200") ok "$l";; RESI*) bad "$l";;
  FROM-MAILMERGE*blocked*) ok "$l";; FROM-MAILMERGE*) bad "$l";;
  *) echo "     $l";; esac; done

echo "== through Cloudflare (tunnels + Access)"
A=00916611232966eaf40906a57d09394e; CF=https://api.cloudflare.com/client/v4/accounts/$A/access
cf() { curl -s -H "Authorization: Bearer $YYC_CF_ACCESS_EDGE_TOKEN" -H "Content-Type: application/json" "$@"; }
tok=$(cf -X POST "$CF/service_tokens" -d "{\"name\":\"yyc-verify-$(date +%s)\",\"duration\":\"24h\"}")
TID=$(echo "$tok" | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["id"])')
CID=$(echo "$tok" | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["client_id"])')
CSEC=$(echo "$tok" | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["client_secret"])')
POLS=()
cleanup() { for p in "${POLS[@]}"; do cf -X DELETE "$CF/apps/${p%%:*}/policies/${p#*:}" >/dev/null; done; cf -X DELETE "$CF/service_tokens/$TID" >/dev/null; echo "     temporary Access policies and token removed"; }
trap cleanup EXIT
for app in "YYC WiseSearch relay" "YYC news stream: approved accounts" "YYC Mail Merge"; do
  id=$(cf "$CF/apps?per_page=100" | python3 -c "import json,sys;print([a['id'] for a in json.load(sys.stdin)['result'] if a['name'].startswith(sys.argv[1])][0])" "$app")
  pid=$(cf -X POST "$CF/apps/$id/policies" -d "{\"name\":\"verify (temporary)\",\"decision\":\"non_identity\",\"include\":[{\"service_token\":{\"token_id\":\"$TID\"}}],\"precedence\":99}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["id"])')
  POLS+=("$id:$pid")
done
sleep 20  # Access picks up policy changes within seconds
H=(-H "CF-Access-Client-Id: $CID" -H "CF-Access-Client-Secret: $CSEC")
chk() { local want=$1; shift; local code; code=$(curl -s -o /dev/null -m 30 -w '%{http_code}' "${H[@]}" "$@"); case " $want " in *" $code "*) ok "$code ${*: -1}";; *) bad "$code ${*: -1} (want $want)";; esac; }
chk "200" https://newswatch.yyc-internal.com/healthz
chk "200 401 403 404" https://newswatch.yyc-internal.com/app/wisers/api/status  # an answer from the relay
chk "200" https://mailmerge.deyaochen.com/healthz
[ $fail = 0 ] && echo "ALL OK" || { echo "SOME CHECKS FAILED"; exit 1; }
