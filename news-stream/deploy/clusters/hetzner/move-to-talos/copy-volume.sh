#!/bin/bash
# copy-volume.sh PVC [ITEM ...] — copy the files of the old cluster's PVC yyc/PVC into yyc-talos's PVC yyc/PVC (plan
# yyc-ai/refactor/talos-apps-move.md, T4b-precopy and T4b-jobs; the 2026-10-20 move's copy_volumes, for two clusters).
# ITEMs: top-level names to copy (default: everything). Written for the move, not yet run (prepared 2026-11-05).
#
# A receiver pod on yyc-talos mounts the new PVC and listens behind a NodePort (30990); a sender pod on the old cluster
# mounts the old PVC, beside the pods that hold it (RWO: the same podAffinity as the jobs, label yyc/data=PVC), and
# streams tar to that NodePort over network yyc (kubectl exec through an API server timed out on GBs on 2026-10-20).
# Then both sides' byte and file counts are compared, Chrome's Singleton* locks (they name the old pod's host) removed,
# ownership set to the jobs' 10001, and both helpers deleted. Re-runnable: the receiver extracts over what is there.
#
# Env: OLD = argv prefix for kubectl on the old cluster (default: ssh to yyc-master1 with ~/.yyc-cluster), NEW_KUBECONFIG
# = yyc-talos's admin kubeconfig (default ~/talos/kubeconfig, through talos.sh tunnel).
set -euo pipefail
pvc=${1:?PVC}; shift; items=${*:-.}
S=${SECRETS:-$HOME/.yyc-cluster}
old() { ${OLD:-ssh -i $S/admin -o UserKnownHostsFile=$S/known_hosts root@2a01:4f8:c013:e0b5::1} "$@"; }
new() { KUBECONFIG=${NEW_KUBECONFIG:-$HOME/talos/kubeconfig} "$@"; }
IMG=alpine/socat:1.8.1.1   # socat + busybox tar/du/find; already pulled by the tunnel connectors on both clusters
SEC='securityContext: {runAsUser: 10001, runAsGroup: 10001, fsGroup: 10001}'

new kubectl -n yyc delete pod,svc "recv-$pvc" --ignore-not-found --wait >/dev/null
new kubectl -n yyc apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata: {name: recv-$pvc, namespace: yyc, labels: {app: recv-$pvc, yyc/data: $pvc}}
spec:
  $SEC
  nodeSelector: {kubernetes.io/arch: amd64}
  priorityClassName: batch
  containers:
    - name: c
      image: $IMG
      command: [sh, -c, "socat -u TCP-LISTEN:9000,reuseaddr STDOUT | tar -x -C /dst && echo EXTRACTED && sleep 3600"]
      resources: {requests: {cpu: 100m, memory: 64Mi}, limits: {cpu: "1", memory: 128Mi}}
      volumeMounts: [{name: v, mountPath: /dst}]
  volumes: [{name: v, persistentVolumeClaim: {claimName: $pvc}}]
---
apiVersion: v1
kind: Service
metadata: {name: recv-$pvc, namespace: yyc}
spec:
  type: NodePort
  externalTrafficPolicy: Cluster
  selector: {app: recv-$pvc}
  ports: [{port: 9000, targetPort: 9000, nodePort: 30990}]
EOF
new kubectl -n yyc wait --for=condition=Ready "pod/recv-$pvc" --timeout=600s >/dev/null
node=$(new kubectl -n yyc get pod "recv-$pvc" -o jsonpath='{.status.hostIP}')
echo "receiver on $node:30990" >&2

old kubectl -n yyc delete pod "send-$pvc" --ignore-not-found --wait >/dev/null
old kubectl -n yyc apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata: {name: send-$pvc, namespace: yyc, labels: {yyc/data: $pvc}}
spec:
  $SEC
  restartPolicy: Never
  priorityClassName: batch
  affinity:
    podAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector: {matchLabels: {yyc/anchor: "true", yyc/data: $pvc}}
          topologyKey: kubernetes.io/hostname
  containers:
    - name: c
      image: $IMG
      command: [sh, -c, "cd /src && tar -c $items | socat -u STDIN TCP:$node:30990 && du -sb $items | awk '{s+=\$1} END {print \"BYTES\", s}' && find $items | wc -l | sed 's/^/FILES /'"]
      resources: {requests: {cpu: 100m, memory: 64Mi}, limits: {cpu: "1", memory: 128Mi}}
      volumeMounts: [{name: v, mountPath: /src, readOnly: true}]
  volumes: [{name: v, persistentVolumeClaim: {claimName: $pvc}}]
EOF
for _ in $(seq 720); do
  ph=$(old kubectl -n yyc get pod "send-$pvc" -o jsonpath='{.status.phase}')
  [ "$ph" = Succeeded ] && break
  [ "$ph" = Failed ] && { old kubectl -n yyc logs "send-$pvc" >&2; exit 1; }
  sleep 5
done
[ "$ph" = Succeeded ] || { echo "send-$pvc: still $ph after 1 h" >&2; exit 1; }
src=$(old kubectl -n yyc logs "send-$pvc")
for _ in $(seq 60); do new kubectl -n yyc logs "recv-$pvc" | grep -q EXTRACTED && break; sleep 2; done
dst=$(new kubectl -n yyc exec "recv-$pvc" -- sh -c "cd /dst && find . -maxdepth 3 -name 'Singleton*' -exec rm -f {} + ; du -sb $items | awk '{s+=\$1} END {print \"BYTES\", s}'; find $items | wc -l | sed 's/^/FILES /'")
echo "old: $(echo $src)  new: $(echo $dst)" >&2
old kubectl -n yyc delete pod "send-$pvc" --wait=false >/dev/null
new kubectl -n yyc delete pod,svc "recv-$pvc" --wait=false >/dev/null
# the Singleton* locks are removed on the new side only, so its counts may be lower by exactly those files
[ "$(echo "$src" | grep FILES)" = "$(echo "$dst" | grep FILES)" ] || echo "file counts differ (Singleton* locks removed: expected only for Chrome profiles)" >&2
