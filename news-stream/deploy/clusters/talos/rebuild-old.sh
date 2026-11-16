#!/bin/bash
# rebuild-old.sh OLD NEW: an old k3s server becomes Talos node NEW, keeping its primary IPv6 (the account's primary IP
# limit is full) and its network-yyc address (README "A new node"; plan yyc-ai/refactor/talos-apps-move.md section 3).
# NEW's machine config must exist (talos.sh gen NEW <role>). Steps, each checked before the next:
#   1. drain OLD with ../../cluster/drain.sh (rolling moves, Jobs finish first; no outage), then delete its k8s node
#   2. shut it down, detach any volume (Talos must see only its own disk; the volumes stay), snapshot it (the way back)
#   3. rebuild it IN PLACE with the Talos image (x86 or ARM by type) and the machine config as user-data
#      (`hcloud server rebuild --user-data-from-file`), rename it NEW, label yyc-cluster=talos: the server, its type,
#      location, primary IPv6 and network-yyc address are kept, so a type that is sold out (cx33 in every EU
#      location, 2026-11-07) is never lost. TYPE changes the type first (`change-type`, disk kept), e.g. back to the
#      type before an in-place resize.
#   4. wait until NEW is Ready in yyc-talos
# Env: TYPE (another server type for NEW, e.g. back to the type before an in-place resize), YYC_HETZNER_API, SECRETS (~/.yyc-cluster: the old cluster's admin key), KUBECONFIG = yyc-talos.
set -euo pipefail
old=${1:?OLD} new=${2:?NEW}
here=$(cd "$(dirname "$0")" && pwd)
OUT=${TALOS_OUT:-$HOME/talos}
[ -s "$OUT/$new.yaml" ] || { echo "no $OUT/$new.yaml: talos.sh gen $new <role> first" >&2; exit 1; }
export HCLOUD_TOKEN=$YYC_HETZNER_API SECRETS=${SECRETS:-$HOME/.yyc-cluster}
MASTER=root@2a01:4f8:c013:e0b5::1
oldk() { ssh -i "$SECRETS/admin" -o UserKnownHostsFile="$SECRETS/known_hosts" -o ConnectTimeout=15 "$MASTER" kubectl "$@"; }
say() { echo "rebuild-old: $*"; }

j=$(hcloud server describe "$old" -o json)
type=${TYPE:-$(jq -r .server_type.name <<<"$j")}; loc=$(jq -r '.location.name // .datacenter.location.name' <<<"$j")
arch=$(jq -r .server_type.architecture <<<"$j"); pid=$(jq -r .public_net.ipv6.id <<<"$j")
ip=$(jq -r '.private_net[0].ip' <<<"$j")
image=451879019; [ "$arch" = arm ] && image=451879029
say "$old: $type $loc $arch, IPv6 primary IP $pid, network address $ip -> $new (image $image)"

if oldk get node "$old" >/dev/null 2>&1; then
  say "draining $old"
  "$here/../../cluster/drain.sh" "$old"
  oldk delete node "$old"
fi
hcloud server shutdown "$old" >/dev/null 2>&1 || true
for _ in $(seq 30); do [ "$(hcloud server describe "$old" -o json | jq -r .status)" = off ] && break; sleep 5; done
for v in $(hcloud server describe "$old" -o json | jq -r '.volumes[]'); do hcloud volume detach "$v" | tail -1; done
say "snapshot of $old"
hcloud server create-image --type snapshot --label yyc-cluster=k3s \
  --description "$old (old k3s server, drained) before its rebuild into $new, $(date -u +%F)" "$old" | tail -1
cur=$(hcloud server describe "$old" -o json | jq -r .server_type.name)
[ "$type" = "$cur" ] || hcloud server change-type --keep-disk "$old" "$type" | tail -1
hcloud server rebuild --image "$image" --user-data-from-file "$OUT/$new.yaml" "$old" 2>&1 | grep -v -i password | tail -1
hcloud server update "$old" --name "$new" | tail -1
hcloud server add-label --overwrite "$new" yyc-cluster=talos | tail -1
hcloud server poweron "$new" 2>/dev/null | tail -1 || true
say "waiting for $new to be Ready"
for _ in $(seq 60); do
  [ "$(kubectl get node "$new" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)" = True ] && break
  sleep 10
done
kubectl get node "$new" -L pool,topology.kubernetes.io/region,kubernetes.io/arch
