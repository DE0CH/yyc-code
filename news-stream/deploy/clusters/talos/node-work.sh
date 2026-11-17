#!/bin/bash
# node-work.sh: Talos and Kubernetes upgrades of yyc-talos, one node at a time (README "Node work"; yyc-ai/refactor
# DESIGN 15.2 N8). Run by .github/workflows/talos-nodes.yml as gha:talos-nodes; from a session it works the same with
# the admin kubeconfig. Every change runs as a Job in talos-ops (node-work.yaml) whose talosctl has role os:admin.
#
#   node-work.sh upgrade [--node NAME] [--dry-run]
#       every node whose Talos version differs from release.env's TALOS_VERSION: control nodes first, then the
#       workers, one at a time (talosctl upgrade drains, upgrades, reboots and waits; then the node must be Ready at
#       the new version and talosctl health green before the next). --node: only that node.
#   node-work.sh upgrade-k8s --to VERSION [--dry-run]
#       talosctl upgrade-k8s (control plane one node at a time, then every kubelet); --dry-run is talosctl's own plan.
#
# Rules (N8), checked before every node:
#   - a node not Ready (other than the one being upgraded) stops the run red: never two nodes down.
#   - a Deployment a PodDisruptionBudget does not let go (a singleton) is moved off the node first by its own rolling
#     update; one that cannot move stops the run red with the node uncordoned and untouched.
#   - workers that hold a CloudNativePG primary go last, so each Cluster switches over once.
# Env: KUBECONFIG.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=release.env
. "$here/release.env"
NS=talos-ops
TALOSCTL_IMAGE=ghcr.io/siderolabs/talosctl:$TALOS_VERSION

action=${1:-}; shift || true
node_only= dry= k8s_to=
while [ $# -gt 0 ]; do
  case "$1" in
    --node) node_only=$2; shift 2 ;;
    --dry-run) dry=1; shift ;;
    --to) k8s_to=$2; shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
case "$action" in upgrade|upgrade-k8s) ;; *) sed -n 2,20p "$0"; exit 2 ;; esac

notice() { echo "::notice::$*"; }

nodes_json=$(kubectl get nodes -o json)
# name ip version role, control nodes first, then by name
# the nodes of the CloudNativePG primaries: they go last among the workers, so a Cluster switches over once (to an
# instance on a node already done) instead of once per node (yyc-talos, 2026-11-17: db1, db2, db3 in name order moved
# Cluster pg's primary three times in three minutes)
primary_nodes=$(kubectl get pods -A -l cnpg.io/instanceRole=primary -o json | jq -c '[.items[].spec.nodeName] | unique')
mapfile -t NODES < <(jq -r --argjson pri "$primary_nodes" '.items
  | map({n: .metadata.name,
         ip: ([.status.addresses[] | select(.type == "InternalIP") | .address][0]),
         v: (.status.nodeInfo.osImage | capture("\\((?<v>v[^)]+)\\)").v // "unknown"),
         cp: (.metadata.labels | has("node-role.kubernetes.io/control-plane"))})
  | sort_by((if .cp then 0 else 1 end), (if (.n | IN($pri[])) then 1 else 0 end), .n)[]
  | "\(.n) \(.ip) \(.v) \(if .cp then "control" else "worker" end)"' <<< "$nodes_json")
cp_ips=$(printf '%s\n' "${NODES[@]}" | awk '$4 == "control" {print $2}')

other_cp() { awk -v x="$1" '$0 != x {print; exit}' <<< "$cp_ips"; }

all_ready_but() {  # every node Ready except $1
  kubectl get nodes -o json | jq -e --arg skip "${1:-}" '[.items[] | select(.metadata.name != $skip)
    | select(([.status.conditions[] | select(.type == "Ready")][0].status) != "True") | .metadata.name] | length == 0' >/dev/null
}

# run_job NAME AVOID_NODE INIT_ARGS_JSON MAIN_ARGS_JSON TIMEOUT: a Job of talosctl containers (init first, then main),
# on any node but AVOID_NODE; waits for it and prints its logs; fails when the Job fails
run_job() {
  local name=$1 avoid=$2 init=$3 main=$4 timeout=$5
  jq -n --arg name "$name" --arg ns "$NS" --arg avoid "$avoid" --arg img "$TALOSCTL_IMAGE" \
        --argjson init "$init" --argjson main "$main" '
  def c($n; $a): {name: $n, image: $img, args: $a,
    env: [{name: "TALOSCONFIG", value: "/var/run/secrets/talos.dev/config"}, {name: "HOME", value: "/tmp"}],
    resources: {requests: {cpu: "50m", memory: "128Mi"}, limits: {memory: "512Mi"}},
    securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: ["ALL"]}},
    volumeMounts: [{name: "talosconfig", mountPath: "/var/run/secrets/talos.dev", readOnly: true},
                   {name: "tmp", mountPath: "/tmp"}]};
  {apiVersion: "batch/v1", kind: "Job",
   metadata: {name: $name, namespace: $ns, labels: {app: "node-work"}},
   spec: {backoffLimit: 0, activeDeadlineSeconds: 5400, ttlSecondsAfterFinished: 604800,
     template: {metadata: {labels: {app: "node-work"}}, spec: {
       restartPolicy: "Never", automountServiceAccountToken: false, enableServiceLinks: false,
       priorityClassName: "serving",
       securityContext: {runAsNonRoot: true, runAsUser: 65534, runAsGroup: 65534, seccompProfile: {type: "RuntimeDefault"}},
       tolerations: [{key: "node-role.kubernetes.io/control-plane", operator: "Exists", effect: "NoSchedule"}],
       affinity: {nodeAffinity: {
         requiredDuringSchedulingIgnoredDuringExecution: {nodeSelectorTerms: [{matchExpressions: [
           {key: "kubernetes.io/hostname", operator: "NotIn", values: [$avoid]}]}]},
         preferredDuringSchedulingIgnoredDuringExecution: [{weight: 100, preference: {matchExpressions: [
           {key: "node-role.kubernetes.io/control-plane", operator: "Exists"}]}}]}},
       initContainers: ($init | to_entries | map(c("step\(.key)"; .value))),
       containers: [c("main"; $main)],
       volumes: [{name: "talosconfig", secret: {secretName: "node-work"}}, {name: "tmp", emptyDir: {sizeLimit: "64Mi"}}]}}}}' \
  | kubectl create -f - >/dev/null
  echo "job $NS/$name started"
  local t=0 st=
  while :; do
    st=$(kubectl -n "$NS" get job "$name" -o json | jq -r '
      if ([.status.conditions[]? | select(.type == "Complete" and .status == "True")] | length) > 0 then "ok"
      elif ([.status.conditions[]? | select(.type == "Failed" and .status == "True")] | length) > 0 then "failed"
      else "running" end')
    [ "$st" = running ] || break
    [ "$t" -lt "$timeout" ] || { st=timeout; break; }
    sleep 15; t=$((t + 15))
  done
  kubectl -n "$NS" logs "job/$name" --all-containers --prefix --tail=-1 2>/dev/null || true
  [ "$st" = ok ] || { echo "::error::job $NS/$name: $st"; return 1; }
}

# singletons NODE: the Deployments (namespace/name) with a pod on NODE that a PodDisruptionBudget does not let go
# (disruptionsAllowed 0: the news stream's poller, wisers, whatsapp, monitor…). talosctl's drain can only evict, so it
# would wait on them until its timeout; they move by their own rolling update instead (new pod elsewhere first, handover
# through their lock: news-stream/deploy/README.md "Moving pods off a node").
singletons() {
  local pods pdbs
  pods=$(kubectl get pods -A --field-selector "spec.nodeName=$1" -o json)
  pdbs=$(kubectl get pdb -A -o json)
  jq -r --argjson pdbs "$pdbs" '.items[]
    | select(.status.phase == "Running" and ((.metadata.ownerReferences // [])[0].kind == "ReplicaSet"))
    | . as $p
    | select([$pdbs.items[] | select(.metadata.namespace == $p.metadata.namespace and (.status.disruptionsAllowed // 0) == 0)
              | (.spec.selector.matchLabels // {}) | select(length > 0) | to_entries
              | all(.[]; ($p.metadata.labels // {})[.key] == .value)] | any)
    | "\(.metadata.namespace) \(.metadata.ownerReferences[0].name)"' <<< "$pods" | sort -u | while read -r ns rs; do
      echo "$ns/$(kubectl -n "$ns" get rs "$rs" -o jsonpath='{.metadata.ownerReferences[0].name}')"
    done
}

# move_singletons NODE: cordon NODE, then each singleton's own rollout; a rollout that cannot finish (no room for the
# second pod) uncordons NODE, undoes the restart and stops the run red, with the node untouched
move_singletons() {
  local node=$1 list d
  list=$(singletons "$node")
  [ -n "$list" ] || return 0
  kubectl cordon "$node" >/dev/null
  for d in $list; do
    echo "$node: moving ${d} by its own rolling update"
    kubectl -n "${d%%/*}" rollout restart "deploy/${d#*/}" >/dev/null
    if ! kubectl -n "${d%%/*}" rollout status "deploy/${d#*/}" --timeout=30m; then
      kubectl -n "${d%%/*}" rollout undo "deploy/${d#*/}" >/dev/null || true
      kubectl uncordon "$node" >/dev/null || true
      echo "::error::$node: $d did not move off it (no room for its second pod?); node uncordoned, nothing upgraded"
      return 1
    fi
  done
}

stamp=$(date -u +%Y%m%d%H%M%S)

if [ "$action" = upgrade-k8s ]; then
  [ -n "$k8s_to" ] || { echo "upgrade-k8s needs --to VERSION" >&2; exit 2; }
  cp=$(head -1 <<< "$cp_ips")
  cp_name=$(printf '%s\n' "${NODES[@]}" | awk -v ip="$cp" '$2 == ip {print $1}')
  args=$(jq -nc --arg n "$cp" --arg to "$k8s_to" --arg d "${dry:+--dry-run}" '["-n", $n, "upgrade-k8s", "--to", $to] + (if $d != "" then [$d] else [] end)')
  # the Job avoids the control node talosctl drives; upgrade-k8s itself goes node by node
  run_job "upgrade-k8s-$stamp" "$cp_name" '[]' "$args" 5400
  exit 0
fi

# upgrade
target=$TALOS_VERSION
# Hetzner's own installer only: the generic one's UKI boots an arm64 node as platform metal, with no network (release.env)
case "$TALOS_INSTALLER" in
  factory.talos.dev/hcloud-installer/*) ;;
  *) echo "::error::release.env: TALOS_INSTALLER must be factory.talos.dev/hcloud-installer/<schematic>, not $TALOS_INSTALLER"; exit 1 ;;
esac
image="$TALOS_INSTALLER:$TALOS_VERSION"
did=0
for line in "${NODES[@]}"; do
  read -r name ip ver role <<< "$line"
  if [ -n "$node_only" ] && [ "$name" != "$node_only" ]; then continue; fi
  if [ "$ver" = "$target" ]; then echo "$name ($role): at $target"; continue; fi
  if [ -n "$dry" ]; then
    echo "$name ($role, $ip): would upgrade $ver -> $target ($image)$(singletons "$name" | sed 's/^/; first moves /' | tr -d '\n')"
    continue
  fi
  all_ready_but "" || { echo "::error::a node is not Ready: not starting on $name"; kubectl get nodes; exit 1; }
  other=$(other_cp "$ip")
  echo "$name ($role, $ip): upgrade $ver -> $target"
  up=$(jq -nc --arg n "$ip" --arg i "$image" '[["-n", $n, "upgrade", "--image", $i, "--wait", "--timeout", "30m", "--drain-timeout", "15m", "--progress", "plain"]]')
  health=$(jq -nc --arg n "$other" '["-n", $n, "health", "--wait-timeout", "15m"]')
  move_singletons "$name"
  # a failed upgrade Job leaves the node cordoned by talosctl's drain: uncordon it, the run is red either way
  run_job "upgrade-$name-$stamp" "$name" "$up" "$health" 5400 || { kubectl uncordon "$name" >/dev/null || true; exit 1; }
  # the node back, Ready, at the new version
  for _ in $(seq 1 40); do
    now=$(kubectl get node "$name" -o json | jq -r '(.status.nodeInfo.osImage | capture("\\((?<v>v[^)]+)\\)").v // "") + " " + ([.status.conditions[] | select(.type == "Ready")][0].status)')
    [ "$now" = "$target True" ] && break
    sleep 15
  done
  [ "$now" = "$target True" ] || { echo "::error::$name is not Ready at $target after the upgrade ($now)"; exit 1; }
  echo "$name: Ready at $target"
  did=$((did + 1))
done
echo "upgraded $did node(s)${dry:+ (dry run: none)}"
