#!/bin/bash
# gateway-node-work.sh [--k8s VERSION] [--node NAME]: the node work of hermit's gateway-maintenance runbook (aviary
# fabric/host/gateway-maintenance.md, hermit 14.7 step 3) for yyc-talos: dispatches talos-nodes.yml on main with
# mode=apply, gateway_runbook=true and node=<the gateway node> (action upgrade; with --k8s, upgrade-k8s to VERSION),
# then waits for that run and exits with its result (0 = success). The runbook needs a command that blocks until the
# node work is done, which `gh workflow run` alone does not. Usage, from a session holding the hermit port-forward:
#
#   hermit-admin.sh gateway-maintenance --node-work news-stream/deploy/clusters/talos/gateway-node-work.sh
#
# --node: the gateway node (default: the one node labelled hermit.yyc/gateway=true, read with kubectl; yyc-talos-gw1).
# Env: GH_TOKEN with Actions write on DE0CH/yyc-code; KUBECONFIG when --node is not given.
set -euo pipefail
REPO=DE0CH/yyc-code
k8s='' node=''
while [ $# -gt 0 ]; do
  case "$1" in
    --k8s) k8s=${2:?--k8s VERSION}; shift 2 ;;
    --node) node=${2:?--node NAME}; shift 2 ;;
    *) sed -n '2,11p' "$0"; exit 2 ;;
  esac
done
if [ -z "$node" ]; then
  node=$(kubectl get nodes -l hermit.yyc/gateway=true -o jsonpath='{.items[*].metadata.name}')
  if [ -z "$node" ] || [ "${node// /}" != "$node" ]; then
    echo "gateway-node-work: not exactly one node labelled hermit.yyc/gateway=true: '$node'" >&2; exit 1
  fi
fi

since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
args=(-f mode=apply -f gateway_runbook=true -f "node=$node")
if [ -n "$k8s" ]; then args+=(-f action=upgrade-k8s -f "k8s_version=$k8s"); else args+=(-f action=upgrade); fi
gh workflow run talos-nodes.yml -R "$REPO" --ref main "${args[@]}"
echo "gateway-node-work: dispatched talos-nodes.yml (${k8s:+upgrade-k8s $k8s, }node $node) at $since" >&2

# the dispatch's run: the first workflow_dispatch run of talos-nodes.yml created at or after $since
id=''
for _ in $(seq 1 60); do
  id=$(gh run list -R "$REPO" --workflow talos-nodes.yml --event workflow_dispatch --limit 10 --json databaseId,createdAt \
    | jq -r --arg s "$since" '[.[] | select(.createdAt >= $s)] | sort_by(.createdAt) | .[0].databaseId // empty')
  [ -n "$id" ] && break
  sleep 5
done
[ -n "$id" ] || { echo "gateway-node-work: no run of the dispatch appeared within 5 min" >&2; exit 1; }
echo "gateway-node-work: run https://github.com/$REPO/actions/runs/$id" >&2
if ! gh run watch "$id" -R "$REPO" --interval 30 --exit-status >/dev/null; then
  echo "gateway-node-work: run $id failed" >&2; exit 1
fi
echo "gateway-node-work: run $id succeeded" >&2
