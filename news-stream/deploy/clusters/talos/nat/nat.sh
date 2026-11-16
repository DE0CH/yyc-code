#!/bin/bash
# nat.sh: yyc-nat1, the router of network `yyc` for nodes with no public IP (the yyc-talos workers; README "Workers
# without public IPs"). Hetzner counts every primary IP, IPv6 ones too, against the account's limit (16, Deyao
# 2026-11-05: no limit raise), so the workers have none and reach the outside through this one box:
#   - IPv4 out: network `yyc`'s route 0.0.0.0/0 -> 10.0.0.30; here forwarded and masqueraded out warp0 (this box's own
#     WARP account, store key YYC_NAT1_WARP, warp-register.py), MSS clamped to warp0's MTU;
#   - DNS: dnsmasq on 10.0.0.30:53 forwarding to Hetzner's resolvers over this box's IPv6 (never through WARP,
#     lessons/91).
# A network box only (claude-env CLAUDE.md "Network boxes are network-only"): no workloads. Debian 13, cax11 fsn1 (its
# WARP exits at 104.28.207.215, colo FRA, like the old fsn1 nodes; nbg1 exited at CDG), public IPv6 only, firewall yyc-talos (ICMP + udp 51820 in; SSH only through yyc-master1 over network `yyc`).
#
#   nat.sh create        create the server, attach it to network `yyc` at 10.0.0.30 (YYC_HETZNER_API)
#   nat.sh setup         install and configure it over SSH via yyc-master1 (YYC_NAT1_WARP, ~/.yyc-cluster/admin)
#   nat.sh route         add network `yyc`'s route 0.0.0.0/0 -> 10.0.0.30
#   nat.sh watchdog      install (or update) the WARP watchdog below; also run by `setup`
#   nat.sh check         forwarding, NAT, WARP handshake, DNS, IPv4 timings and the watchdog's log from the box itself
#
# The watchdog (claude-env lessons/99): WARP can keep this box's tunnel up (fresh handshake, Cloudflare-hosted sites
# fast) while every connection to an origin outside Cloudflare waits ~5 s for its first byte at Cloudflare's end
# (2026-11-06 11:46-12:15 UTC: EC2, S3, GitHub, Google and even Hetzner's fsn1 object storage, for every worker; the old
# cluster's nodes on the same exit address were fast). Slow, not failed, so the check bounds the time: every minute,
# HTTPS over IPv4 to three hosts outside Cloudflare, each allowed 3 s (healthy: 0.03-0.4 s); none within 3 s on two
# checks in a row restarts wg-quick@warp0 (at most once in 10 min), which fixed it at once. A restart pauses the
# workers' IPv4 for about 3 s. Log: journalctl -t nat-watchdog.
set -euo pipefail
NAME=yyc-nat1 IP=10.0.0.30
KEY=${SECRETS:-$HOME/.yyc-cluster}/admin
SSHO=(-i "$KEY" -o UserKnownHostsFile="${SECRETS:-$HOME/.yyc-cluster}/known_hosts" -o StrictHostKeyChecking=accept-new
      -o ConnectTimeout=15 -o "ProxyCommand=cloudflared access tcp --hostname jump.yyc-internal.com")
on() { ssh "${SSHO[@]}" "root@$IP" "$@"; }
hc() { session-secret --keys YYC_HETZNER_API --exec bash -c 'HCLOUD_TOKEN=$YYC_HETZNER_API hcloud "$@"' hcloud "$@"; }

case "${1:-}" in
  create)
    # attached at its fixed address before its first boot (cloud-init writes the address it sees at first boot)
    if ! hc server describe "$NAME" >/dev/null 2>&1; then
      hc server create --name "$NAME" --type cax11 --location fsn1 --image debian-13 --without-ipv4 \
        --ssh-key yyc-cluster-admin --label yyc-cluster=talos --label role=nat --firewall yyc-talos \
        --start-after-create=false
      hc server attach-to-network "$NAME" --network yyc --ip "$IP"
      hc server poweron "$NAME"
    fi
    hc server describe "$NAME" -o json | jq '{name, ipv6: .public_net.ipv6.ip, private: .private_net[0].ip}'
    ;;
  watchdog)
    on bash -s <<'EOF'
set -euo pipefail
cat > /usr/local/sbin/yyc-nat-watchdog <<'W'
#!/bin/bash
# (installed by nat.sh watchdog) restart wg-quick@warp0 when IPv4 outside Cloudflare is failing or slow twice in a row
set -u
fail=/run/yyc-nat-watchdog.fail last=/var/lib/yyc-nat-watchdog.last
ok() { for u in https://ec2.ap-southeast-1.amazonaws.com/ https://api.github.com/ https://fsn1.your-objectstorage.com/; do
  curl -4 -s -o /dev/null -m 3 "$u" && return 0; done; return 1; }
if ok; then rm -f "$fail"; exit 0; fi
n=$(( $(cat "$fail" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$fail"
logger -t nat-watchdog "IPv4 outside Cloudflare failed or took over 3 s ($n in a row)"
[ "$n" -ge 2 ] || exit 0
now=$(date +%s); prev=$(cat "$last" 2>/dev/null || echo 0)
if [ $((now - prev)) -lt 600 ]; then logger -t nat-watchdog "restarted less than 10 min ago: not again"; exit 0; fi
echo "$now" > "$last"
systemctl restart wg-quick@warp0
sleep 3
if ok; then logger -t nat-watchdog "restarted wg-quick@warp0: IPv4 back"; rm -f "$fail"
else logger -t nat-watchdog "restarted wg-quick@warp0: IPv4 still failing"; fi
W
chmod 755 /usr/local/sbin/yyc-nat-watchdog
cat > /etc/systemd/system/yyc-nat-watchdog.service <<'U'
[Unit]
Description=YYC: restart nat1's WARP tunnel when outgoing IPv4 outside Cloudflare fails or is slow
After=wg-quick@warp0.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/yyc-nat-watchdog
U
cat > /etc/systemd/system/yyc-nat-watchdog.timer <<'U'
[Unit]
Description=YYC: nat1's WARP check every minute
[Timer]
OnBootSec=3min
OnUnitActiveSec=1min
AccuracySec=5s
[Install]
WantedBy=timers.target
U
systemctl daemon-reload
systemctl enable -q --now yyc-nat-watchdog.timer
echo "nat-watchdog: $(systemctl is-active yyc-nat-watchdog.timer)"
EOF
    ;;
  setup)
    warp=$(session-secret --keys YYC_NAT1_WARP --exec bash -c 'printf %s "$YYC_NAT1_WARP"')
    python3 -c '
import json, sys
w = json.loads(sys.argv[1])
print(f"""[Interface]
PrivateKey = {w["privateKey"]}
Address = {w["address"]}
MTU = 1280
[Peer]
PublicKey = {w["peerPublicKey"]}
Endpoint = {w["endpoint"]}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25""")' "$warp" | on 'umask 077; mkdir -p /etc/wireguard; cat > /etc/wireguard/warp0.conf'
    on bash -s <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq wireguard-tools nftables dnsmasq curl dnsutils >/dev/null
cat > /etc/sysctl.d/90-nat.conf <<'S'
net.ipv4.ip_forward = 1
S
sysctl -q --system
# forward and masquerade network `yyc` out warp0; replies back in; MSS fits warp0's 1280 MTU
cat > /etc/nftables.conf <<'N'
#!/usr/sbin/nft -f
flush ruleset
table inet nat {
  chain postrouting {
    type nat hook postrouting priority srcnat;
    oifname "warp0" ip saddr 10.0.0.0/16 masquerade
  }
}
table inet filter {
  chain forward {
    type filter hook forward priority filter; policy drop;
    tcp flags syn tcp option maxseg size set rt mtu
    ct state established,related accept
    ip saddr 10.0.0.0/16 oifname "warp0" accept
  }
}
N
systemctl enable -q nftables && systemctl restart nftables
# DNS for network `yyc`: Hetzner's resolvers over IPv6
cat > /etc/dnsmasq.d/yyc.conf <<'D'
listen-address=10.0.0.30,127.0.0.1
bind-dynamic
no-resolv
server=2a01:4ff:ff00::add:1
server=2a01:4ff:ff00::add:2
cache-size=10000
D
systemctl enable -q dnsmasq && systemctl restart dnsmasq
systemctl enable -q wg-quick@warp0 && systemctl restart wg-quick@warp0
EOF
    "$0" watchdog
    ;;
  route)
    hc network describe yyc -o json | jq -e '.routes[] | select(.destination == "0.0.0.0/0")' >/dev/null \
      || hc network add-route yyc --destination 0.0.0.0/0 --gateway "$IP"
    ;;
  check)
    on 'sysctl -n net.ipv4.ip_forward; wg show warp0 latest-handshakes; curl -4 -sS -m 10 https://www.cloudflare.com/cdn-cgi/trace | grep -E "^(ip|warp|colo)="; dig +short @10.0.0.30 ghcr.io | head -2; nft list chain inet nat postrouting | grep masq
      echo "conntrack $(cat /proc/sys/net/netfilter/nf_conntrack_count)/$(cat /proc/sys/net/netfilter/nf_conntrack_max)"
      for u in https://ec2.ap-southeast-1.amazonaws.com/ https://api.github.com/ https://fsn1.your-objectstorage.com/ https://example.com/; do
        curl -4 -s -o /dev/null -m 10 -w "%{http_code} %{time_total}s $u\n" "$u"; done
      echo "watchdog: $(systemctl is-active yyc-nat-watchdog.timer)"; journalctl -t nat-watchdog -n 5 --no-pager -q'
    ;;
  *) sed -n 2,27p "$0"; exit 2 ;;
esac
