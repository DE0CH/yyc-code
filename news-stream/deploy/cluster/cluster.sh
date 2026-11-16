#!/bin/bash
# cluster.sh — build and operate YYC's k3s cluster in Hetzner project "yyc" (cluster/README.md). Prints no secret.
#
#   cluster.sh create      network `yyc`, then hetzner-k3s create (3 control-plane nodes + pool apps) from a bastion in
#                          the private network; re-runnable: hetzner-k3s adds what is missing and re-applies the rest
#   cluster.sh flux        Flux + its secret (the repo's read-only deploy key) + clusters/hetzner/flux-sync.yaml
#   cluster.sh cert        the Sealed Secrets controller's public cert -> /infra/sealed-secrets.pem (after a rebuild:
#                          then `infra/seal.py --all`, commit, push; README "Secrets after a rebuild")
#   cluster.sh ca          the k3s server CA (ConfigMap kube-root-ca.crt) -> /infra/yyc-cluster-ca.pem, the CA YYC AI's
#                          importer trusts for this API across clusters (bridge B7; after a rebuild: commit and push it)
#   cluster.sh upgrade     hetzner-k3s upgrade to k3s_version in hetzner-k3s.yaml
#   cluster.sh ssh [CMD]   ssh to the first control-plane node (kubectl/flux there), e.g. cluster.sh ssh kubectl get nodes
#   cluster.sh nodes       servers of the cluster with their IPv6 and private address
#   cluster.sh node-dns    node-dns.sh on every server of the cluster, one at a time (pods' DNS through the node's
#                          resolver over IPv6, not WARP), then a rolling restart of CoreDNS so it reads the new upstream
#   cluster.sh warp-watchdog  warp-watchdog.sh on every server of the cluster (the WARP tunnel restarted when IPv4
#                          outside Cloudflare fails twice in a row, claude-env lessons/93)
#   cluster.sh db-forward [PORT]  YYC's Postgres on localhost:PORT (default 15432) of this machine, until stopped:
#                          ssh -L to a `kubectl port-forward` of Service pg-rw.db on the control-plane node (the
#                          database has no public address); connect with sslmode=require and a role's password
#
# Env: YYC_HETZNER_API (project yyc, Read & Write), SECRETS = a folder (mode 700) with admin + admin.pub
# (YYC_CLUSTER_SSH_KEY), flux-deploy (YYC_FLUX_DEPLOY_KEY); BASTION = IPv6 of a server in
# the private network to run hetzner-k3s on (default: the first control-plane node, else the old node yyc-cluster);
# HK3S_CONFIG = another config file than hetzner-k3s.yaml (a one-off change, e.g. a temporary pool).
# Why a bastion: hetzner-k3s connects only over IPv4, and to the PRIVATE address when servers have no public IPv4.
set -euo pipefail
: "${YYC_HETZNER_API:?}" "${SECRETS:?}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$(dirname "$HERE")"
HK3S_VERSION=2.6.0
HELM_VERSION=3.19.0
FLUX_VERSION=${FLUX_VERSION:-2.9.6}
NET=yyc
API=https://api.hetzner.cloud/v1
h() { curl -sS --fail-with-body -H "Authorization: Bearer $YYC_HETZNER_API" -H "Content-Type: application/json" "$@"; }
py() { python3 -c "import json,sys;d=json.load(sys.stdin);$1"; }
ssh_() { local ip=$1; shift; ssh -i "$SECRETS/admin" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$SECRETS/known_hosts" -o ConnectTimeout=15 -o ServerAliveInterval=30 "root@$ip" "$@"; }
v6() { h "$API/servers?name=$1" | py 'print(d["servers"][0]["public_net"]["ipv6"]["ip"].split("/")[0]+"1" if d["servers"] else "")'; }
first_master() { v6 yyc-master1; }
bastion() {
  if [ -n "${BASTION:-}" ]; then echo "$BASTION"; return; fi
  local m; m=$(first_master); [ -n "$m" ] && { echo "$m"; return; }
  v6 yyc-cluster
}

ensure_network() {
  local id; id=$(h "$API/networks?name=$NET" | py 'print(d["networks"][0]["id"] if d["networks"] else "")')
  if [ -z "$id" ]; then
    id=$(h -X POST "$API/networks" -d "{\"name\":\"$NET\",\"ip_range\":\"10.0.0.0/16\",\"subnets\":[{\"type\":\"cloud\",\"network_zone\":\"eu-central\",\"ip_range\":\"10.0.0.0/16\"}],\"labels\":{\"cluster\":\"yyc\"}}" | py 'print(d["network"]["id"])')
    echo "network $NET created ($id)" >&2
  fi
  echo "$id"
}

# the bastion must be in the private network: attach it when it isn't (the old node yyc-cluster during the build)
ensure_bastion_in_network() {
  local ip=$1 net=$2 srv
  srv=$(h "$API/servers" | py "
for s in d['servers']:
    if s['public_net']['ipv6'] and s['public_net']['ipv6']['ip'].split('/')[0]+'1' == '$ip':
        print(s['id'], ' '.join(str(p['network']) for p in s['private_net']))")
  [ -n "$srv" ] || { echo "bastion $ip is not a server of project yyc" >&2; exit 1; }
  set -- $srv
  if ! printf '%s\n' "${@:2}" | grep -qx "$net"; then
    h -X POST "$API/servers/$1/actions/attach_to_network" -d "{\"network\":$net}" >/dev/null
    echo "bastion attached to network $NET" >&2; sleep 15
  fi
  # a NIC added to a running server gets no address by itself on Ubuntu 24.04: DHCP on it (networkd), once
  ssh_ "$ip" 'set -e; for i in $(seq 20); do IF=$(ip -o link | awk -F": " "/mtu 1450/{print \$2}" | grep -v -E "flannel|cni|veth|wg" | head -1); [ -n "$IF" ] && break; sleep 3; done
    [ -n "$IF" ] || { echo "no private NIC"; exit 1; }
    ip -4 addr show dev $IF | grep -q "inet 10\.0\." || { printf "[Match]\nName=%s\n[Network]\nDHCP=ipv4\n[DHCPv4]\nUseRoutes=true\n" $IF > /etc/systemd/network/50-private.network; networkctl reload; networkctl reconfigure $IF; sleep 8; }
    ip -4 addr show dev $IF | grep -o "inet 10\.0\.[0-9.]*"'
}

# hetzner-k3s, helm and the rendered config in /root/hk3s on the bastion (the token only in a 600 file, removed after)
prepare_bastion() {
  local ip=$1
  ssh_ "$ip" "set -e; install -d -m 700 /root/hk3s; cd /root/hk3s
    [ \"\$(./hetzner-k3s --version 2>/dev/null | grep -o '[0-9.]*' | head -1)\" = $HK3S_VERSION ] || { curl -fsSL -o hetzner-k3s https://github.com/vitobotta/hetzner-k3s/releases/download/v$HK3S_VERSION/hetzner-k3s-linux-amd64; chmod +x hetzner-k3s; }
    command -v helm >/dev/null || { curl -fsSL https://get.helm.sh/helm-v$HELM_VERSION-linux-amd64.tar.gz | tar -xz -C /usr/local/bin --strip-components=1 linux-amd64/helm; }
    command -v kubectl >/dev/null"
  ssh_ "$ip" 'cat > /root/hk3s/admin && chmod 600 /root/hk3s/admin' < "$SECRETS/admin"
  ssh_ "$ip" 'cat > /root/hk3s/admin.pub' < "$SECRETS/admin.pub"
  local b64 dns wd; b64=$(base64 -w0 "$HERE/node-setup.sh"); dns=$(base64 -w0 "$HERE/node-dns.sh")
  wd=$(base64 -w0 "$HERE/warp-watchdog.sh")
  sed -e "s|@NODE_SETUP_B64@|$b64|" -e "s|@NODE_DNS_B64@|$dns|" -e "s|@WARP_WATCHDOG_B64@|$wd|" \
    "${HK3S_CONFIG:-$HERE/hetzner-k3s.yaml}" | ssh_ "$ip" 'cat > /root/hk3s/hetzner-k3s.yaml'
  printf '%s' "$YYC_HETZNER_API" | ssh_ "$ip" 'umask 077; cat > /root/hk3s/token'
}

run_hk3s() {
  local ip=$1; shift
  ssh_ "$ip" "cd /root/hk3s && trap 'rm -f token' EXIT && HCLOUD_TOKEN=\$(cat token) ./hetzner-k3s $* --config hetzner-k3s.yaml" 2>&1 | tee -a "$SECRETS/hetzner-k3s.log"
  ssh_ "$ip" 'cat /root/hk3s/kubeconfig' > "$SECRETS/kubeconfig-yyc" 2>/dev/null && chmod 600 "$SECRETS/kubeconfig-yyc" || true
}

cmd=${1:-}; shift || true
case "$cmd" in
  create)
    net=$(ensure_network); b=$(bastion); echo "bastion $b"
    ensure_bastion_in_network "$b" "$net"
    prepare_bastion "$b"
    run_hk3s "$b" create --skip-current-ip-validation
    ;;
  upgrade)
    b=$(bastion); prepare_bastion "$b"; run_hk3s "$b" upgrade --skip-current-ip-validation ;;
  flux)
    m=$(first_master); [ -n "$m" ] || { echo "no yyc-master1"; exit 1; }
    ssh_ "$m" FLUX_VERSION="$FLUX_VERSION" bash -s <<'REMOTE'
set -euo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; mkdir -p /root/.kube && ln -sf $KUBECONFIG /root/.kube/config
if [ "$(flux --version 2>/dev/null | awk '{print $3}')" != "$FLUX_VERSION" ]; then
  curl -fsSL "https://github.com/fluxcd/flux2/releases/download/v$FLUX_VERSION/flux_${FLUX_VERSION}_linux_amd64.tar.gz" | tar -xz -C /usr/local/bin flux
fi
# Flux's controllers tolerate nothing special: they run on the apps nodes (the control plane is tainted)
flux install --components=source-controller,kustomize-controller,helm-controller --timeout 5m >/dev/null
REMOTE
    ssh_ "$m" 'umask 077; cat > /root/flux-deploy' < "$SECRETS/flux-deploy"
    ssh_ "$m" 'ssh-keyscan -t ed25519,ecdsa,rsa github.com 2>/dev/null > /root/github_known_hosts
      kubectl -n flux-system create secret generic yyc-git --from-file=identity=/root/flux-deploy --from-file=known_hosts=/root/github_known_hosts --dry-run=client -o yaml | kubectl apply -f - >/dev/null
      shred -u /root/flux-deploy'
    ssh_ "$m" 'kubectl apply -f -' < "$DEPLOY/clusters/hetzner/flux-sync.yaml"
    ssh_ "$m" 'flux get kustomizations'
    ;;
  cert)
    m=$(first_master); out="$(dirname "$(dirname "$DEPLOY")")/infra/sealed-secrets.pem"
    ssh_ "$m" 'kubectl -n kube-system rollout status deploy/sealed-secrets-controller --timeout=300s >/dev/null &&
      kubectl get --raw /api/v1/namespaces/kube-system/services/sealed-secrets-controller:http/proxy/v1/cert.pem' > "$out.new"
    openssl x509 -in "$out.new" -noout >/dev/null && mv "$out.new" "$out" && openssl x509 -in "$out" -noout -fingerprint -sha256 ;;
  ca)
    m=$(first_master); out="$(dirname "$(dirname "$DEPLOY")")/infra/yyc-cluster-ca.pem"
    ssh_ "$m" "kubectl -n default get configmap kube-root-ca.crt -o jsonpath='{.data.ca\\.crt}'" > "$out.new"
    openssl x509 -in "$out.new" -noout >/dev/null && mv "$out.new" "$out" && openssl x509 -in "$out" -noout -subject -fingerprint -sha256 ;;
  ssh)
    m=$(first_master); ssh_ "$m" "$@" ;;
  db-forward)
    port=${1:-15432}; m=$(first_master)
    ssh_ "$m" -o ExitOnForwardFailure=yes -L "127.0.0.1:$port:127.0.0.1:$port" \
      "kubectl -n db port-forward svc/pg-rw --address 127.0.0.1 $port:5432" ;;
  node-dns)
    h "$API/servers?label_selector=cluster=yyc" | py '
for s in d["servers"]:
    if s["public_net"]["ipv6"]: print(s["name"], s["public_net"]["ipv6"]["ip"].split("/")[0]+"1")' | while read -r name ip; do
      echo "== $name"; ssh_ "$ip" 'bash -s' < "$HERE/node-dns.sh" || { echo "node-dns failed on $name" >&2; exit 1; }
    done
    m=$(first_master)
    ssh_ "$m" 'kubectl -n kube-system rollout restart deploy/coredns && kubectl -n kube-system rollout status deploy/coredns --timeout=180s' ;;
  warp-watchdog)
    h "$API/servers?label_selector=cluster=yyc" | py '
for s in d["servers"]:
    if s["public_net"]["ipv6"]: print(s["name"], s["public_net"]["ipv6"]["ip"].split("/")[0]+"1")' | while read -r name ip; do
      echo "== $name"; ssh_ "$ip" 'bash -s' < "$HERE/warp-watchdog.sh" || { echo "warp-watchdog failed on $name" >&2; exit 1; }
    done ;;
  nodes)
    h "$API/servers?label_selector=cluster=yyc" | py '
for s in d["servers"]:
    print(s["name"], s["server_type"]["name"], s["location"]["name"], s["public_net"]["ipv6"]["ip"].split("/")[0]+"1" if s["public_net"]["ipv6"] else "-", " ".join(p["ip"] for p in s["private_net"]), s["status"])' ;;
  *) sed -n '2,25p' "$0"; exit 1 ;;
esac
