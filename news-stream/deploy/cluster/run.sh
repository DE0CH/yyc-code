#!/bin/bash
# run.sh — run one command in a pod of its own (never inside a serving pod: a script's memory shares that pod's cgroup,
# and the monitor and the poller were OOM-killed that way on 2026-10-20). The pod has the news stream's current image
# (the poller's), the database, Secret app-env and the Secrets named with --secrets, its own memory, priority batch, and
# is deleted when the command ends. The command's stdout comes back on stdout and its stderr on stderr (the pod keeps
# stdout in a file and prints it after a marker line), the exit code is the command's. No attach: `kubectl run -i`
# drops input sent before it attaches and repeated the output when it fell back to the logs (2026-10-21).
#
#   SECRETS=~/.yyc-cluster news-stream/deploy/cluster/run.sh -- python /app/infra/control.py show
#   SECRETS=~/.yyc-cluster news-stream/deploy/cluster/run.sh --memory 1Gi --secrets monitor-keys -- python3 /app/infra/cost-profile.py --json
#
# Options: --memory (default 512Mi, request = limit), --cpu (default 1), --secrets a,b (envFrom, besides app-env),
# --stdin-env NAME: this script's stdin (small: it travels in the pod spec) becomes env NAME in the pod and the
# command's stdin. --node NAME: run on that node (e.g. to compare its egress IP with another's). --timeout S (default
# 3600): the wait's deadline; then the pod is deleted and run.sh fails. A pod that is gone (deleted, evicted) ends the
# wait as a failure (the wait once spun forever on a deleted pod: its phase reads empty, 22 Oct 2026).
# Prints no secret.
set -euo pipefail
MEM=512Mi CPU=1 EXTRA="" STDIN_ENV="" NODE="" TIMEOUT=3600
while [ $# -gt 0 ]; do
  case "$1" in
    --memory) MEM=$2; shift 2 ;;
    --cpu) CPU=$2; shift 2 ;;
    --secrets) EXTRA=$2; shift 2 ;;
    --stdin-env) STDIN_ENV=$2; shift 2 ;;
    --node) NODE=$2; shift 2 ;;
    --timeout) TIMEOUT=$2; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
[ $# -gt 0 ] || { sed -n '2,17p' "$0"; exit 2; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="run-$(date -u +%m%d%H%M%S)-$RANDOM"
INPUT=""; [ -n "$STDIN_ENV" ] && INPUT=$(cat)
OVR=$(INPUT="$INPUT" NODE="$NODE" python3 - "$NAME" "$MEM" "$CPU" "$EXTRA" "$STDIN_ENV" "$@" <<'PY'
import json, os, sys
name, mem, cpu, extra, stdin_env, *cmd = sys.argv[1:]
env_from = [{"secretRef": {"name": "app-env"}}] + [{"secretRef": {"name": s}} for s in extra.split(",") if s]
print(json.dumps({"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": "yyc",
                 "labels": {"app": "run"}}, "spec": {
    "restartPolicy": "Never", "priorityClassName": "batch", "imagePullSecrets": [{"name": "ghcr-pull"}],
    **({"nodeName": os.environ["NODE"]} if os.environ.get("NODE") else {}),
    "securityContext": {"runAsUser": 10001, "runAsGroup": 10001},
    "containers": [{"name": name, "image": "IMAGE",
                    "command": ["sh", "-c", 'if [ -n "$RUN_STDIN_VAR" ]; then printenv "$RUN_STDIN_VAR" | "$@"; else "$@" </dev/null; fi >/tmp/run.out;'
                                ' rc=$?; echo "===RUN-STDOUT rc=$rc==="; cat /tmp/run.out; exit $rc',
                                "sh"] + cmd,
                    "envFrom": env_from,
                    "env": [{"name": "DATABASE_URL", "valueFrom": {"secretKeyRef": {"name": "db-app", "key": "uri"}}},
                            {"name": "FLAGD_HOST", "value": "flagd.yyc.svc.cluster.local"}]
                           + ([{"name": stdin_env, "value": os.environ["INPUT"]}, {"name": "RUN_STDIN_VAR", "value": stdin_env}]
                              if stdin_env else []),
                    "resources": {"requests": {"cpu": "100m", "memory": mem, "ephemeral-storage": "50Mi"},
                                  "limits": {"cpu": cpu, "memory": mem, "ephemeral-storage": "1Gi"}}}]}}))
PY
)
IMG=$("$HERE/cluster.sh" ssh "kubectl -n yyc get deploy poller -o jsonpath='{.spec.template.spec.containers[0].image}'")
OVR=${OVR/\"IMAGE\"/\"$IMG\"}
LOG=$(mktemp); trap 'rm -f "$LOG"' EXIT
printf '%s' "$OVR" | "$HERE/cluster.sh" ssh "NAME=$NAME TIMEOUT=$TIMEOUT bash -c '
  kubectl apply -f - >/dev/null
  end=\$((\$(date +%s) + TIMEOUT))
  while :; do
    if ! out=\$(kubectl -n yyc get pod \$NAME -o jsonpath={.status.phase} 2>&1); then
      case \$out in *NotFound*) echo \"run.sh: pod \$NAME is gone (deleted or evicted)\" >&2; exit 1 ;; esac
    fi
    case \$out in Succeeded|Failed) break ;; esac
    if [ \$(date +%s) -ge \$end ]; then
      echo \"run.sh: pod \$NAME not finished after \$TIMEOUT s (phase \${out:-unknown}); deleted\" >&2
      kubectl -n yyc logs \$NAME 2>/dev/null; kubectl -n yyc delete pod \$NAME --wait=false >/dev/null; exit 1
    fi
    sleep 3
  done
  kubectl -n yyc logs \$NAME
  kubectl -n yyc delete pod \$NAME --wait=false >/dev/null'" > "$LOG" || true  # a failed wait: no marker, the log goes to stderr
python3 - "$LOG" <<'PY'
import re, sys
log = open(sys.argv[1], encoding="utf-8", errors="replace").read()
m = None
for m in re.finditer(r"^===RUN-STDOUT rc=(\d+)===\n", log, re.M):
    pass
if not m:
    sys.stderr.write(log)
    sys.exit(1)
sys.stderr.write(log[:m.start()])
sys.stdout.write(log[m.end():])
sys.exit(int(m.group(1)))
PY
