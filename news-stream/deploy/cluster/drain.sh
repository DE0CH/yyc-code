#!/bin/bash
# drain.sh NODE — move every pod off a node without an outage (deploy/README.md "Moving pods off a node").
#
#   SECRETS=~/.yyc-cluster news-stream/deploy/cluster/drain.sh yyc-pool-small-worker3
#
# 1. kubectl cordon NODE: nothing new lands there (CronJobs' next runs start elsewhere).
# 2. Waits for the Jobs running there to finish (infra changes never cut a running job; at most JOB_WAIT seconds).
# 3. Every Deployment with a pod there is moved by its OWN rolling update (kubectl rollout restart + rollout status):
#    a singleton starts its new pod elsewhere first and hands over through its lock, a 2-replica Deployment replaces one
#    pod at a time. The singletons' PDBs (maxUnavailable 0) make a bare `kubectl drain` wait on them instead.
# 4. kubectl drain NODE: what is left (volume anchors, a node-bound pod such as wisers, DaemonSets ignored) is evicted.
#    wisers follows its ReadWriteOnce volume: its move is an eviction with a gap of its restart.
# Undo: kubectl uncordon NODE. Prints no secret.
set -euo pipefail
NODE=${1:?usage: drain.sh NODE}
JOB_WAIT=${JOB_WAIT:-3600}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$HERE/cluster.sh" ssh "NODE=$NODE JOB_WAIT=$JOB_WAIT bash -s" <<'EOF'
set -euo pipefail
kubectl cordon "$NODE"
end=$((SECONDS + JOB_WAIT))
while :; do
  jobs=$(kubectl get pods -A --field-selector "spec.nodeName=$NODE,status.phase=Running" \
    -o jsonpath='{range .items[?(@.metadata.ownerReferences[0].kind=="Job")]}{.metadata.namespace}/{.metadata.name} {end}')
  [ -z "$jobs" ] && break
  [ $SECONDS -ge $end ] && { echo "jobs still running after ${JOB_WAIT}s: $jobs (left to the drain)"; break; }
  echo "waiting for jobs on $NODE: $jobs"; sleep 20
done
# the Deployments with a pod on the node: pod -> ReplicaSet -> Deployment
for rs in $(kubectl get pods -A --field-selector "spec.nodeName=$NODE" \
    -o jsonpath='{range .items[?(@.metadata.ownerReferences[0].kind=="ReplicaSet")]}{.metadata.namespace}/{.metadata.ownerReferences[0].name}{"\n"}{end}' | sort -u); do
  ns=${rs%%/*}
  dep=$(kubectl -n "$ns" get rs "${rs#*/}" -o jsonpath='{.metadata.ownerReferences[0].name}')
  case "$ns/$dep" in
    yyc/volume-*|yyc/wisers) echo "leaving $ns/$dep to the drain (node-bound volume)"; continue ;;
    kube-system/*|flux-system/*) echo "leaving $ns/$dep to the drain (system)"; continue ;;
  esac
  echo "moving $ns/$dep by rolling update"
  kubectl -n "$ns" rollout restart "deploy/$dep"
  kubectl -n "$ns" rollout status "deploy/$dep" --timeout=15m
done
kubectl drain "$NODE" --ignore-daemonsets --delete-emptydir-data --timeout=20m
echo "drained $NODE (kubectl uncordon $NODE to bring it back)"
EOF
