#!/bin/bash
# node-dns.sh — pods' DNS without WARP, on every node of the YYC cluster (hetzner-k3s.yaml additional_post_k3s_commands,
# embedded by cluster.sh; run it on an existing node with `cluster.sh node-dns`). Idempotent; prints no secret.
#
# CoreDNS forwards names outside the cluster to the file kubelet hands pods with dnsPolicy Default:
# /etc/k8s-resolv.conf. hetzner-k3s writes `nameserver 8.8.8.8` there, which a pod (IPv4-only) reaches only through
# the node's WARP, and that path went silent for minutes to hours on one node (2026-11-02/03: ~69,000 CoreDNS timeouts,
# every outside name, web_search's api.exa.ai among them). Instead each node's own systemd-resolved, which asks
# Hetzner's resolvers over the node's native IPv6, answers pods on a node-local address:
#   - a dummy interface `yycdns` holds 169.254.20.10/32 (link-local, the address NodeLocal DNSCache uses); a pod
#     reaches it through its node's cni0, so each CoreDNS pod asks the node it runs on;
#   - resolved listens there too (DNSStubListenerExtra), besides its usual 127.0.0.53;
#   - /etc/k8s-resolv.conf names it. Kubelet reads that file when it creates a pod: CoreDNS picks it up on restart
#     (`kubectl -n kube-system rollout restart deploy/coredns`).
# Answers carry A and AAAA records as before; only the question travels over IPv6. Pods still connect to IPv4
# addresses through WARP.
set -euo pipefail
A=169.254.20.10

mkdir -p /etc/systemd/network /etc/systemd/resolved.conf.d
printf '[NetDev]\nName=yycdns\nKind=dummy\n' > /etc/systemd/network/10-yycdns.netdev
printf '[Match]\nName=yycdns\n\n[Network]\nAddress=%s/32\nLinkLocalAddressing=no\nIPv6AcceptRA=no\n' $A > /etc/systemd/network/10-yycdns.network
networkctl reload
for i in $(seq 20); do ip -4 addr show yycdns 2>/dev/null | grep -q "$A/32" && break; sleep 1; done
ip -4 addr show yycdns | grep -q "$A/32" || { echo "FATAL: yycdns has no $A"; exit 1; }

printf '# pods ask this node over 169.254.20.10 (news-stream/deploy/cluster/node-dns.sh)\n[Resolve]\nDNSStubListenerExtra=%s\n' $A \
  > /etc/systemd/resolved.conf.d/10-yyc-pods.conf
systemctl restart systemd-resolved
for i in $(seq 20); do getent ahostsv4 github.com >/dev/null && break; sleep 1; done

# the answer must come from the new listener before kubelet hands it out
command -v dig >/dev/null || { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -yq dnsutils; }
dig +time=3 +tries=2 +short @$A github.com A | grep -q '^[0-9.]*$' || { echo "FATAL: no A record from $A"; exit 1; }
echo "nameserver $A" > /etc/k8s-resolv.conf.new && mv /etc/k8s-resolv.conf.new /etc/k8s-resolv.conf
echo "node-dns: pods' upstream DNS = $A (systemd-resolved over IPv6: $(resolvectl dns eth0 2>/dev/null | cut -d: -f2- | xargs))"
