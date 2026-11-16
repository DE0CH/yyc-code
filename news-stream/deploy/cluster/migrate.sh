#!/bin/bash
# migrate.sh STEP — move the apps from one cluster (OLD, Flux path clusters/<from>) to another (NEW, clusters/<to>) with
# no gap for anything that serves (../MIGRATE.md). Each step edits the overlays' MIGRATION STATE, commits, pushes
# (git pull --rebase, never a force-push), reconciles both clusters and checks the result. Re-runnable.
#
#   connectors   NEW's tunnel connectors join yyc-stream and mailmerge; then OLD's stop
#   singletons   NEW's poller, whatsapp, transcribe start and wait on their Postgres locks; then OLD's stop
#                (they hand over through the locks: never two working at once)
#   monitor      OLD's monitor stops, NEW's starts (no lock; it serves nobody directly, a minute without is fine)
#   wisers       the WiseSearch relay: OLD's drains and saves its session, its volume is copied, NEW's starts on it
#                (WisersOne allows one session: never two relays); the only step with a gap, about a minute
#   precopy      the dumps (big, written once a night) copied ahead, nothing stopped
#   jobs         OLD's CronJobs stop (a running job always finishes first), the volumes' files are copied, NEW's
#                CronJobs start (each keeps the suspend it has in ../apps)
#
# Env: SECRETS (cluster.sh), OLD_SSH = root@<a node of OLD that has the files and NEW's kubeconfig>, FROM/TO = overlay names,
# REPO = a clone of DE0CH/yyc-code (default: this one). Runs kubectl on OLD over ssh and on NEW via cluster.sh ssh; the
# file copies run on OLD, straight to NEW's API over the private network (/root/hk3s/kubeconfig from cluster.sh).
set -euo pipefail
: "${SECRETS:?}" "${OLD_SSH:?}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO=${REPO:-$(git -C "$HERE" rev-parse --show-toplevel)}
FROM=${FROM:?overlay of the old cluster}; TO=${TO:?overlay of the new cluster}
OV=$REPO/news-stream/deploy/clusters
old() { ssh -i "$SECRETS/admin" -o UserKnownHostsFile="$SECRETS/known_hosts" -o StrictHostKeyChecking=accept-new "$OLD_SSH" "$@"; }
new() { SECRETS=$SECRETS "$HERE/cluster.sh" ssh "$@"; }
TRAILER="

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

# edit an overlay's kustomization.yaml (python: replace A by B once; no-op when B is already there)
edit() {
  python3 - "$1" "$2" "$3" <<'PY'
import sys
p, a, b = sys.argv[1:]
s = open(p).read()
if b and b in s:
    sys.exit(0)
if a not in s:
    if not b:
        sys.exit(0)  # a removal already made
    sys.exit(f"{p}: expected text not found:\n{a}")
open(p, "w").write(s.replace(a, b, 1))
PY
}
commit() {
  git -C "$REPO" add -A news-stream/deploy/clusters
  git -C "$REPO" diff --cached --quiet || git -C "$REPO" commit -qm "deploy: move to $TO, $1$TRAILER"
  git -C "$REPO" pull -q --rebase origin main && git -C "$REPO" push -q origin HEAD:main
}
reconcile() { old flux reconcile kustomization yyc-apps --with-source >/dev/null; new flux reconcile kustomization yyc-apps --with-source >/dev/null; }
reconcile_new() { new flux reconcile kustomization yyc-apps --with-source >/dev/null; }
reconcile_old() { old flux reconcile kustomization yyc-apps --with-source >/dev/null; }
MARK_FROM="  # MIGRATION STATE (../../MIGRATE.md): what still runs here"
stop_on_old() {  # $1 = regex of Deployment names
  edit "$OV/$FROM/kustomization.yaml" "$MARK_FROM" "$MARK_FROM
  - target: {kind: Deployment, name: \"$1\"}
    patch: |-
      - {op: replace, path: /spec/replicas, value: 0}"
}

# copy_volumes "VOLUME:ITEM ITEM" …: tar ITEMs from /var/lib/yyc/data on OLD into VOLUME on NEW (a busybox helper pod with
# the volume's label yyc/data=VOLUME, so it lands where that volume's jobs would). Chrome's Singleton* lock files name the
# old pod's hostname, so they are removed (a copied one makes Chrome refuse the profile)
copy_volumes() {
  for g in "$@"; do
    v=${g%%:*}; items=${g#*:}
    new "kubectl -n yyc delete pod copy-$v --ignore-not-found --wait >/dev/null; kubectl -n yyc apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata: {name: copy-$v, namespace: yyc, labels: {yyc/data: $v}}
spec:
  securityContext: {runAsUser: 10001, runAsGroup: 10001, fsGroup: 10001}
  priorityClassName: batch
  containers:
    - name: c
      image: busybox:1.37
      command: [sleep, '7200']
      resources: {requests: {cpu: 50m, memory: 64Mi}, limits: {cpu: '1', memory: 64Mi}}
      volumeMounts: [{name: v, mountPath: /dst}]
  volumes: [{name: v, persistentVolumeClaim: {claimName: $v}}]
EOF
kubectl -n yyc wait --for=condition=Ready pod/copy-$v --timeout=600s >/dev/null" || { echo "copy-$v not Ready (volume attach?)" >&2; exit 1; }
    # stream node to node over the private network (kubectl exec through the API timed out on GBs): the mount of the
    # volume on the helper's node is /var/lib/kubelet/pods/<pod uid>/volumes/kubernetes.io~csi/<pv>/mount
    read -r node uid pv < <(new "kubectl -n yyc get pod copy-$v -o jsonpath='{.status.hostIP} {.metadata.uid} ' && kubectl -n yyc get pvc $v -o jsonpath='{.spec.volumeName}'") || true   # no trailing newline: read returns 1
    [ -n "${node:-}" ] && [ -n "${uid:-}" ] && [ -n "${pv:-}" ] || { echo "copy-$v: no node/uid/pv" >&2; exit 1; }
    dst=/var/lib/kubelet/pods/$uid/volumes/kubernetes.io~csi/$pv/mount
    echo "copy $items → $v on $node" >&2
    old "cd /var/lib/yyc/data && ls -d $items 2>/dev/null | xargs tar -cf - | ssh -i /root/hk3s/admin -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR root@$node 'tar -C $dst -xf - && chown -R 10001:10001 $dst && find $dst -maxdepth 2 -name \"Singleton*\" -exec rm -f {} + && du -sh $dst'"
    new kubectl -n yyc delete pod copy-$v --wait >/dev/null
  done
}

case "${1:-}" in
  connectors)
    edit "$OV/$TO/kustomization.yaml" "  - target: {kind: Deployment, name: cloudflared}
    patch: |-
      - {op: replace, path: /spec/replicas, value: 0}
" ""
    commit "the tunnels' connectors join"; reconcile_new
    new 'kubectl -n yyc rollout status deploy/cloudflared --timeout=300s && kubectl -n mailmerge rollout status deploy/cloudflared --timeout=300s'
    stop_on_old cloudflared; commit "the old connectors stop"; reconcile_old
    ;;
  singletons)
    edit "$OV/$TO/kustomization.yaml" 'name: "poller|whatsapp|monitor|transcribe|wisers"}' 'name: "monitor|wisers"}'
    commit "poller, whatsapp, transcribe wait on their locks"; reconcile_new
    for d in poller whatsapp transcribe; do new kubectl -n yyc rollout status deploy/$d --timeout=600s; done
    stop_on_old "poller|whatsapp|transcribe"; commit "the old poller, whatsapp, transcribe hand over"; reconcile_old
    for i in $(seq 90); do [ -z "$(old "kubectl -n yyc get pods -l 'app in (poller,whatsapp,transcribe)' -o name")" ] && break; sleep 10; done
    new 'for d in poller whatsapp; do kubectl -n yyc logs deploy/$d --tail 3; done'
    ;;
  monitor)
    stop_on_old monitor; commit "the old monitor stops"; reconcile_old
    edit "$OV/$TO/kustomization.yaml" 'name: "monitor|wisers"}' 'name: "wisers"}'
    commit "the monitor starts"; reconcile_new; new kubectl -n yyc rollout status deploy/monitor --timeout=600s
    ;;
  wisers)
    old flux suspend kustomization yyc-apps >/dev/null
    t0=$(date +%s)
    old 'kubectl -n yyc scale deploy/wisers --replicas=0 >/dev/null; for i in $(seq 70); do [ -z "$(kubectl -n yyc get pods -l app=wisers -o name)" ] && break; sleep 5; done; kubectl -n yyc get pods -l app=wisers'
    src=$(old "kubectl get pv \$(kubectl -n yyc get pvc wisers -o jsonpath='{.spec.volumeName}') -o jsonpath='{.spec.hostPath.path}{.spec.local.path}'")
    new 'kubectl -n yyc delete pod wisers-copy --ignore-not-found --wait >/dev/null; kubectl -n yyc apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata: {name: wisers-copy, namespace: yyc}
spec:
  securityContext: {runAsUser: 10001, runAsGroup: 10001, fsGroup: 10001}
  priorityClassName: serving
  containers:
    - name: c
      image: busybox:1.37
      command: [sleep, "3600"]
      resources: {requests: {cpu: 10m, memory: 32Mi}, limits: {cpu: 500m, memory: 64Mi}}
      volumeMounts: [{name: v, mountPath: /dst}]
  volumes: [{name: v, persistentVolumeClaim: {claimName: wisers}}]
EOF
kubectl -n yyc wait --for=condition=Ready pod/wisers-copy --timeout=300s >/dev/null'
    old "tar -C '$src' -cf - . | KUBECONFIG=/root/hk3s/kubeconfig kubectl -n yyc exec -i wisers-copy -- tar -C /dst -xf - && KUBECONFIG=/root/hk3s/kubeconfig kubectl -n yyc exec wisers-copy -- sh -c 'rm -f /dst/leader.lock; ls -la /dst; du -sh /dst'"
    new kubectl -n yyc delete pod wisers-copy --wait >/dev/null
    stop_on_old wisers
    edit "$OV/$TO/kustomization.yaml" '  - target: {kind: Deployment, name: "wisers"}
    patch: |-
      - {op: replace, path: /spec/replicas, value: 0}
' ""
    edit "$OV/$TO/kustomization.yaml" "  # the relay still runs on the old node: Service wisers → its NodePorts there (../legacy/wisers-bridge.yaml)
  - target: {kind: Service, name: wisers}
    patch: |-
      - {op: remove, path: /spec/selector}
      - {op: replace, path: /spec/ports, value: [{name: http, port: 8899, targetPort: 30899}, {name: stats, port: 8898, targetPort: 30898}]}
" ""
    edit "$OV/$TO/kustomization.yaml" "  - wisers-bridge.yaml   # MIGRATION: deleted with the wisers step
" ""
    git -C "$REPO" rm -q --ignore-unmatch "$OV/$TO/wisers-bridge.yaml"
    commit "the WiseSearch relay moves with its saved session"; reconcile_new
    new 'kubectl -n yyc rollout status deploy/wisers --timeout=600s'
    echo "wisers: $(( $(date +%s) - t0 )) s from the old relay's drain to the new one Ready"
    old flux resume kustomization yyc-apps >/dev/null
    ;;
  precopy)
    # the big files that change only at night (pgdump/: 7 dumps, written 19:30 UTC) go first, with the jobs still running
    # on OLD; `PRECOPIED=1 migrate.sh jobs` then copies only the rest
    copy_volumes "dumps:pgdump"
    ;;
  jobs)
    edit "$OV/$FROM/kustomization.yaml" "$MARK_FROM" "$MARK_FROM
  - target: {kind: CronJob}
    patch: |-
      - {op: replace, path: /spec/suspend, value: true}"
    commit "the old CronJobs stop"; reconcile_old
    for i in $(seq 120); do n=$(old "kubectl -n yyc get jobs -o jsonpath='{range .items[?(@.status.active)]}{.metadata.name}{\"\\n\"}{end}'" | grep -c . || true); [ "$n" = 0 ] && break; echo "waiting for $n running jobs"; sleep 15; done
    dumps="uptime.log"; [ -n "${PRECOPIED:-}" ] || dumps="$dumps pgdump"   # pgdump/ already there after `precopy`
    copy_volumes "browser-data:browser-profile cache-profile fulltext-profile" "facebook-data:fb-profile fb-comments-profile" "dumps:$dumps"
    edit "$OV/$TO/kustomization.yaml" "  - target: {kind: CronJob}
    patch: |-
      - {op: replace, path: /spec/suspend, value: true}
" ""
    commit "the CronJobs start, with the data volume's files"; reconcile_new
    new kubectl -n yyc get cronjobs
    ;;
  *) sed -n '2,20p' "$0"; exit 1 ;;
esac
