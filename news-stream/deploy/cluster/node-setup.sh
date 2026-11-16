#!/bin/bash
# node-setup.sh — runs on every node of the YYC cluster at first boot, before k3s installs (hetzner-k3s.yaml
# additional_pre_k3s_commands; cluster.sh embeds this file). Idempotent; prints no secret.
#
# The nodes are IPv6-only (no public IPv4; Deyao, 2026-10-13). This gives each node outgoing IPv4 through its OWN free
# Cloudflare WARP account (a WireGuard key can be used by one peer at a time, so nodes never share one): it registers
# a new account with WARP's API (reachable over IPv6), writes /etc/wireguard/wgwarp.conf (IPv4 only, no DNS line, the
# endpoint over IPv6) and starts wg-quick@wgwarp. The interface name starts with "wg" so hetzner-k3s's install
# scripts never take it for the private network's interface (they pick by MTU 1450/1280 and skip *wg*).
# Routing: wg-quick puts 0.0.0.0/0 in table 51820; the main table keeps the private network (10.0.0.0/16), the
# metadata service and flannel's routes (suppress_prefixlength 0), so only IPv4 to the internet goes through WARP.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
command -v wg-quick >/dev/null || { apt-get update -q && apt-get install -yq wireguard-tools; }

# systemd-networkd drops routes and policy rules it didn't make when it reconfigures an interface (a DHCP renewal):
# that silently removed wg-quick's rules once. Keep foreign ones.
mkdir -p /etc/systemd/networkd.conf.d
if [ ! -f /etc/systemd/networkd.conf.d/10-keep-foreign.conf ]; then
  printf '[Network]\nManageForeignRoutingPolicyRules=no\nManageForeignRoutes=no\n' > /etc/systemd/networkd.conf.d/10-keep-foreign.conf
  systemctl restart systemd-networkd
fi

C=/etc/wireguard/wgwarp.conf
if [ ! -s $C ]; then
  install -d -m 700 /etc/wireguard
  priv=$(wg genkey); pub=$(printf '%s' "$priv" | wg pubkey)
  for i in $(seq 10); do
    r=$(curl -6 -fsS -m 30 -X POST https://api.cloudflareclient.com/v0a5641/reg \
          -H 'User-Agent: 1.1.1.1/6.38.9-5641 (Android 16.0.0)' -H 'CF-Client-Version: a-6.38.9-5641' \
          -H 'Content-Type: application/json; charset=UTF-8' \
          -d "{\"key\":\"$pub\",\"install_id\":\"\",\"fcm_token\":\"\",\"tos\":\"$(date -u +%FT%T.000Z)\",\"model\":\"PC\",\"serial_number\":\"\",\"locale\":\"en_US\",\"key_type\":\"curve25519\",\"tunnel_type\":\"wireguard\"}") && break
    sleep 10
  done
  umask 077
  PRIV="$priv" python3 -c '
import json, os, sys
d = json.loads(sys.stdin.read())["config"]
peer = d["peers"][0]
print(f"""# Cloudflare WARP (a free account of this node alone) for outgoing IPv4: news-stream/deploy/cluster/node-setup.sh
[Interface]
PrivateKey = {os.environ["PRIV"]}
Address = {d["interface"]["addresses"]["v4"]}/32
MTU = 1280
[Peer]
PublicKey = {peer["public_key"]}
AllowedIPs = 0.0.0.0/0
Endpoint = [2606:4700:d0::a29f:c001]:2408
PersistentKeepalive = 25""")' <<< "$r" > $C.new
  mv $C.new $C
fi
systemctl enable -q wg-quick@wgwarp
ip -4 rule | grep -q 51820 || systemctl restart wg-quick@wgwarp
for i in $(seq 30); do curl -4 -fsS -m 5 -o /dev/null https://github.com && break; sleep 2; done
curl -4 -fsS -m 10 -o /dev/null https://github.com || { echo "FATAL: no IPv4 through WARP"; exit 1; }

# k3s (server or agent) starts after WARP at boot: image pulls from ghcr.io/github need IPv4
for u in k3s k3s-agent; do
  mkdir -p /etc/systemd/system/$u.service.d
  printf '[Unit]\nAfter=wg-quick@wgwarp.service\nWants=wg-quick@wgwarp.service\n' > /etc/systemd/system/$u.service.d/10-warp.conf
done
systemctl daemon-reload
dpkg -s unattended-upgrades >/dev/null 2>&1 || apt-get install -yq unattended-upgrades
echo "node-setup: IPv4 out via WARP $(curl -4 -fsS -m 10 https://www.cloudflare.com/cdn-cgi/trace | grep -E '^warp=' )"
