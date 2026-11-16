#!/bin/bash
# resize-node.sh NODE TYPE: change a database node's Hetzner server type in place (e.g. yyc-talos-db1 cpx42), with no
# downtime: the node must not hold a Postgres primary (./switchover.sh moves it first); its copy stops while the server
# is off, and the other two keep serving and acknowledging writes (pg.yaml `synchronous: any 1`).
# The disk is kept (--keep-disk), so the server can go back to a smaller type. Steps: refuse a primary's node, wait for
# every copy on the other nodes to be ready (this node's own may be waiting for room), cordon, shut down (ACPI: Talos stops its pods cleanly; power off after 3 min), change the
# type, power on, wait for the node and every copy to be ready and caught up, uncordon.
# Env: KUBECONFIG (talos.sh kubeconfig), HCLOUD_TOKEN (YYC's Hetzner project `yyc`, store key YYC_HETZNER_API).
set -euo pipefail
node=${1:?NODE}; type=${2:?TYPE}
K() { kubectl -n db "$@"; }
say() { echo "$(date -u +%H:%M:%S) $*"; }
die() { say "STOP: $*"; exit 1; }

for c in pg magpie-pg; do
  p=$(K get cluster "$c" -o jsonpath='{.status.currentPrimary}')
  [ "$(K get pod "$p" -o jsonpath='{.spec.nodeName}')" != "$node" ] || die "$node holds $c's primary $p: ./switchover.sh first"
done
# its copy: the pod on it, or one waiting unscheduled for the room this resize makes (none of the other nodes has it)
copy=$(K get pods -l cnpg.io/cluster=pg --field-selector spec.nodeName="$node" -o name | head -1)
[ -n "$copy" ] || copy=$(K get pods -l cnpg.io/cluster=pg --field-selector spec.nodeName= -o name | head -1)
[ -n "$copy" ] || die "no pg copy on $node or waiting for it"
copy=${copy#pod/}
[ "$(hcloud server describe "$node" -o format='{{.ServerType.Name}}')" != "$type" ] || { say "$node is already $type"; exit 0; }

healthy() {  # every copy ready, and the standbys streaming within 64 MB of the primary
  local want ready lag
  want=$(K get cluster pg -o jsonpath='{.spec.instances}')
  ready=$(K get cluster pg -o jsonpath='{.status.readyInstances}')
  [ "$ready" = "$want" ] || return 1
  p=$(K get cluster pg -o jsonpath='{.status.currentPrimary}')
  lag=$(K exec "$p" -c postgres -- psql -qAtX -c "select coalesce(max(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)), 0)::bigint, count(*) from pg_stat_replication where state = 'streaming'" 2>/dev/null) || return 1
  [ "${lag#*|}" = "$((want - 1))" ] && [ "${lag%|*}" -lt 67108864 ]
}
wait_for() {  # wait_for SECONDS WHAT CMD…
  local t=$1 what=$2; shift 2
  for _ in $(seq "$t"); do "$@" && return 0; sleep 1; done
  die "$what: not within $t s"
}

others_ready() {  # every copy on the other nodes ready (this node's own may be waiting for the room this resize makes)
  K get pods -l cnpg.io/cluster=pg -o jsonpath='{range .items[*]}{.metadata.name},{.spec.nodeName},{.status.containerStatuses[0].ready}{"\n"}{end}' \
    | awk -F, -v n="$node" -v c="$copy" '$1 != c && $2 != n && $3 != "true" {bad = 1} END {exit bad}'
}
nodeready() { [ "$(kubectl get node "$node" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')" = True ]; }
wait_for 600 "every other copy ready before the stop" others_ready
say "$node ($copy): cordon, shut down"
kubectl cordon "$node" >/dev/null
hcloud server shutdown "$node" >/dev/null
off() { [ "$(hcloud server describe "$node" -o format='{{.Status}}')" = off ]; }
if ! (for _ in $(seq 180); do off && exit 0; sleep 1; done; exit 1); then
  say "no clean shutdown in 3 min: power off"
  hcloud server poweroff "$node" >/dev/null
  wait_for 120 "power off" off
fi
say "change type to $type (disk kept)"
if ! hcloud server change-type --keep-disk "$node" "$type" >/dev/null; then
  # e.g. the account's shared vCPU limit (2026-11-09): the server goes back on as it was
  hcloud server poweron "$node" >/dev/null
  wait_for 600 "node Ready again" nodeready
  kubectl uncordon "$node" >/dev/null
  die "the type change was refused; $node is back on as before"
fi
hcloud server poweron "$node" >/dev/null
wait_for 600 "node Ready" nodeready
kubectl uncordon "$node" >/dev/null
wait_for 900 "every copy ready and caught up" healthy
say "done: $node is $type, $(kubectl get node "$node" -o jsonpath='{.status.capacity.memory}') memory; $copy streaming"
