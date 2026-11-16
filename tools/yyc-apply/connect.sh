#!/bin/bash
# connect.sh: the deploy job's way to the API server (deploy-workflows.md 3). Starts `cloudflared access tcp` to
# k8s-deploy.yyc-internal.com on 127.0.0.1:6443 in the background (the Access service token comes from the env
# TUNNEL_SERVICE_TOKEN_ID / TUNNEL_SERVICE_TOKEN_SECRET, never argv), writes a kubeconfig whose credential is
# gha-token.sh (a fresh GitHub OIDC token per need; nothing stored) and whose CA is $YYC_DEPLOY_CA (default
# infra/yyc-talos-ca.pem, the Talos cluster's Kubernetes CA; 127.0.0.1 is in the serving certificate's SANs), exports KUBECONFIG to later steps, and waits until
# `kubectl version` answers (3 tries, 20 s apart).
set -euo pipefail
: "${TUNNEL_SERVICE_TOKEN_ID:?}" "${TUNNEL_SERVICE_TOKEN_SECRET:?}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
HOST=${YYC_DEPLOY_HOST:-k8s-deploy.yyc-internal.com}
CA=${YYC_DEPLOY_CA:-$ROOT/infra/yyc-talos-ca.pem}
T=${RUNNER_TEMP:-$(mktemp -d)}
export TUNNEL_SERVICE_TOKEN_ID TUNNEL_SERVICE_TOKEN_SECRET
nohup cloudflared access tcp --hostname "$HOST" --url 127.0.0.1:6443 > "$T/cloudflared.log" 2>&1 &
echo $! > "$T/cloudflared.pid"
unset TUNNEL_SERVICE_TOKEN_ID TUNNEL_SERVICE_TOKEN_SECRET
KC="$T/kubeconfig"
umask 077
cat > "$KC" <<YAML
apiVersion: v1
kind: Config
clusters:
  - name: yyc
    cluster:
      server: https://127.0.0.1:6443
      certificate-authority: $CA
users:
  - name: gha
    user:
      exec:
        apiVersion: client.authentication.k8s.io/v1
        command: $HERE/gha-token.sh
        interactiveMode: Never
        provideClusterInfo: false
contexts:
  - name: yyc
    context: {cluster: yyc, user: gha}
current-context: yyc
YAML
[ -n "${GITHUB_ENV:-}" ] && echo "KUBECONFIG=$KC" >> "$GITHUB_ENV"
for i in 1 2 3; do
  if KUBECONFIG="$KC" kubectl version --request-timeout=15s >/dev/null 2>&1; then
    KUBECONFIG="$KC" kubectl auth whoami -o jsonpath='{.status.userInfo.username}{"\n"}' || true
    echo "connected to the API server through $HOST"; exit 0
  fi
  [ $i = 3 ] || sleep 20
done
echo "no answer from the API server through $HOST; cloudflared said:" >&2
tail -n 20 "$T/cloudflared.log" >&2
exit 1
