#!/bin/bash
# talos.sh: YYC's Talos cluster yyc-talos (README). Machine configs are rendered here from the store, never committed.
#
#   talos.sh gen NODE ROLE        render ~/talos/NODE.yaml (ROLE controlplane|news|db|worker) from YYC_TALOS_SECRETS, the
#                                 patches (common; controlplane+public, worker+public or worker+private by role) and the
#                                 node's WARP key (not for a worker)
#                                 YYC_TALOS_WARP_<NODE suffix> (warp-register.py), at release.env's Talos version
#   talos.sh apply NODE IP [--dry-run]  apply ~/talos/NODE.yaml to the running node IP (mode auto: Talos applies
#                                 without a reboot what it can; --dry-run prints the change only). One node at a time.
#   talos.sh tunnel               forward the three control nodes' Talos (50000) and Kubernetes (6443) APIs to
#                                 127.0.0.1:5000{1,2,3} / 6443 through yyc-master1 (network `yyc` only; no public port)
#   talos.sh talosconfig          write ~/talos/talosconfig for the tunnel's endpoints
#   talos.sh addons               install or upgrade the cluster add-ons (helm, pinned versions, addons/*.values.yaml)
#                                 and the PriorityClasses and StorageClass; KUBECONFIG = the new cluster
#   talos.sh manifests            kubectl apply -k of this folder (kustomization.yaml: the etcd snapshots, node work,
#                                 Descheduler, Alloy, sealed/); needs the control nodes' KubeTalosAPIAccessConfig
#                                 first (it makes the talos.dev ServiceAccount CRD)
# Env: SECRETS dir of the old cluster's admin key (~/.yyc-cluster, as cluster.sh).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
OUT=${TALOS_OUT:-$HOME/talos}
# shellcheck source=release.env
. "$here/release.env"
VERSION=$TALOS_VERSION
ENDPOINT=https://10.0.0.21:6443
mkdir -p "$OUT" && chmod 700 "$OUT"
secret() { session-secret --keys "$1" --exec bash -c "printf %s \"\$$1\""; }

case "${1:-}" in
  gen)
    node=$2 role=$3
    key=YYC_TALOS_WARP_$(echo "${node#yyc-talos-}" | tr a-z- A-Z_)
    [ -s "$OUT/secrets.yaml" ] || (umask 077; secret YYC_TALOS_SECRETS > "$OUT/secrets.yaml")
    case $role in worker|news-private|db-private) ;; *) false ;; esac || [ -s "$OUT/$node.warp.json" ] || (umask 077; secret "$key" > "$OUT/$node.warp.json")
    case $role in worker|news-private|db-private) ;; *) false ;; esac || NODE=$node python3 - "$OUT/$node.warp.json" > "$OUT/$node.patch.yaml" <<'PY'
import json, os, sys
w = json.load(open(sys.argv[1]))
print(f"""apiVersion: v1alpha1
kind: WireguardConfig
name: warp0                     # Cloudflare WARP: outgoing IPv4 (DESIGN 15.2.1 I6: never wg0, never udp 51821)
mtu: 1280
privateKey: {w['privateKey']}
addresses:                      # WARP's /12, so its 172.16.0.1 is a gateway in the link's subnet
  - address: {w['address'].split('/')[0]}/12
routes:                         # the IPv4 default route (Talos takes no 0.0.0.0/0 destination)
  - gateway: 172.16.0.1
peers:
  - publicKey: {w['peerPublicKey']}
    endpoint: "{w['endpoint']}"
    allowedIPs: [0.0.0.0/0]
    persistentKeepaliveInterval: 25s""")
PY
    case $role in worker|news-private|db-private) ;; *) false ;; esac || chmod 600 "$OUT/$node.patch.yaml"
    # roles: controlplane has a public IPv6 and WARP (public.yaml + warp0); a worker has no public IP and goes out
    # through yyc-nat1 (private.yaml, nat/nat.sh)
    case $role in
      controlplane) out=controlplane patches=(controlplane public eth0-dhcp) ;;
      news|db) out=worker patches=(public eth0-dhcp ../node-patches/$role) ;;  # old x86 servers (plan talos-apps-move.md)
      worker) out=worker patches=(worker private) ;;
      news-private) out=worker patches=(private ../node-patches/news) ;;  # a news node with no public IP (yyc-nat1)
      db-private) out=worker patches=(private ../node-patches/db) ;;  # a database node with no public IP (yyc-nat1)
      *) echo "role: controlplane|news|news-private|db|db-private|worker" >&2; exit 2 ;;
    esac
    args=(--config-patch @"$here/patches/common.yaml")
    for p in "${patches[@]}"; do args+=(--config-patch @"$here/patches/$p.yaml"); done
    case $role in worker|news-private|db-private) ;; *) false ;; esac || args+=(--config-patch @"$OUT/$node.patch.yaml")
    talosctl gen config yyc-talos "$ENDPOINT" --with-secrets "$OUT/secrets.yaml" --talos-version "$VERSION" \
      --install-image "$TALOS_INSTALLER:$VERSION" "${args[@]}" --output-types "$out" --output "$OUT/$node.yaml" \
      --force >/dev/null
    chmod 600 "$OUT/$node.yaml"
    talosctl validate --config "$OUT/$node.yaml" --mode cloud
    echo "$OUT/$node.yaml"
    ;;
  apply)
    node=$2 ip=$3
    [ -s "$OUT/$node.yaml" ] || { echo "no $OUT/$node.yaml: talos.sh gen $node ROLE first" >&2; exit 1; }
    talosctl --talosconfig "$OUT/talosconfig" -n "$ip" apply-config --file "$OUT/$node.yaml" --mode auto ${4:-}
    ;;
  manifests)
    kubectl apply -k "$here"
    ;;
  talosconfig)
    [ -s "$OUT/secrets.yaml" ] || (umask 077; secret YYC_TALOS_SECRETS > "$OUT/secrets.yaml")
    talosctl gen config yyc-talos "$ENDPOINT" --with-secrets "$OUT/secrets.yaml" --output-types talosconfig \
      --output "$OUT/talosconfig" --force >/dev/null
    talosctl --talosconfig "$OUT/talosconfig" config endpoint 127.0.0.1:50001 127.0.0.1:50002 127.0.0.1:50003
    chmod 600 "$OUT/talosconfig"; echo "$OUT/talosconfig"
    ;;
  tunnel)
    # through the jump host yyc-nat1 (jump.yyc-internal.com, tunnel yyc-deploy; Access service token
    # YYC_DEPLOY_ACCESS_CLIENT_ID/_SECRET in the env as TUNNEL_SERVICE_TOKEN_ID/_SECRET)
    exec ssh -N -i "${SECRETS:-$HOME/.yyc-cluster}/admin" -o UserKnownHostsFile="${SECRETS:-$HOME/.yyc-cluster}/known_hosts" \
      -o ExitOnForwardFailure=yes -o ServerAliveInterval=20 -o "ProxyCommand=cloudflared access tcp --hostname jump.yyc-internal.com" \
      -L 50001:10.0.0.21:50000 -L 50002:10.0.0.22:50000 -L 50003:10.0.0.23:50000 \
      -L 6443:10.0.0.21:6443 -L 6444:10.0.0.22:6443 -L 6445:10.0.0.23:6443 root@10.0.0.30
    ;;
  addons)
    for r in "cilium https://helm.cilium.io" "hcloud https://charts.hetzner.cloud" \
             "sealed-secrets https://bitnami.github.io/sealed-secrets" "jetstack https://charts.jetstack.io" \
             "cnpg https://cloudnative-pg.github.io/charts" "juicefs https://juicedata.github.io/charts/"; do
      helm repo add $r >/dev/null 2>&1 || true
    done
    helm repo update >/dev/null
    kubectl apply -f "$here/priorityclasses.yaml" -f "$here/storageclass.yaml"
    a="$here/addons"
    up() { helm upgrade --install "$1" "$2" --version "$3" -n "$4" --create-namespace -f "$a/$5.values.yaml" --wait --timeout 10m; }
    up cilium cilium/cilium 1.20.4 kube-system cilium
    up hccm hcloud/hcloud-cloud-controller-manager 1.39.2 kube-system hcloud-ccm
    up hcloud-csi hcloud/hcloud-csi 2.24.1 kube-system hcloud-csi
    up sealed-secrets-controller sealed-secrets/sealed-secrets 2.20.0 kube-system sealed-secrets
    up cert-manager jetstack/cert-manager v1.21.2 cert-manager cert-manager
    up cloudnative-pg cnpg/cloudnative-pg 0.29.1 cnpg-system cloudnative-pg
    up plugin-barman-cloud cnpg/plugin-barman-cloud 0.8.1 cnpg-system plugin-barman-cloud
    up juicefs-csi-driver juicefs/juicefs-csi-driver 0.33.0 kube-system juicefs-csi
    ;;
  *) sed -n 2,19p "$0"; exit 2 ;;
esac
