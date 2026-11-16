#!/bin/bash
# warp-watchdog.sh — installs a check of the node's outgoing IPv4 (its WARP tunnel, node-setup.sh) on every node of the
# YYC cluster (hetzner-k3s.yaml additional_post_k3s_commands, embedded by cluster.sh; an existing node: `cluster.sh
# warp-watchdog`). Idempotent; prints no secret.
#
# Why: a node's WARP tunnel can keep its handshake and still pass only Cloudflare-hosted IPv4 (claude-env lessons/93):
# 2026-11-05 on large-worker2, then on apps-worker2 and large-worker1 together for hours, which left both old `web` pods
# without the EC2 API (no new sandbox host for staff) and failed JuiceFS's object storage PUTs on that node. Restarting
# wg-quick@wgwarp fixed each at once. The check: every 2 min, HTTPS over IPv4 to three hosts outside Cloudflare
# (any HTTP answer counts); when none answers on two checks in a row, restart the tunnel (at most once in 30 min) and
# log it (journalctl -t warp-watchdog). Cost of a restart: the node's IPv4 flows pause for seconds (its old cloudflared
# connector reconnects its QUIC connections; tabs on that connector reconnect once), against a node that has no
# outside IPv4 at all.
#
# It also keeps the tunnel's two policy rules at the back (claude-env lessons/97): wg-quick adds them without a
# priority, so the kernel puts them just in front of the first rule there is. At boot that is 32764/32765, but a
# restart on the sandbox gateway node (yyc-sbx-gw) put them at 97/98, in front of sandboxd's `iif wg0` rules (99/100),
# and the sandboxes' traffic to egress pods on other nodes went into flannel's WireGuard, which drops a 10.200.x
# source: half the sandboxes' model calls hung for 2 min (2026-11-05/06). yyc-warp-rules moves them back to
# 32764/32765; it runs after every start of wg-quick@wgwarp (ExecStartPost) and on every check.
set -euo pipefail

cat > /usr/local/sbin/yyc-warp-rules <<'EOF'
#!/bin/bash
# (installed by warp-watchdog.sh) wg-quick@wgwarp's two policy rules at fixed priorities 32764/32765, behind every
# other rule; adds before it deletes, so IPv4 never lacks them. Does nothing while the tunnel is down.
set -u
t=51820
ip link show wgwarp >/dev/null 2>&1 || exit 0
rules=$(ip -4 rule show)
grep -qE "^32764:.*lookup main suppress_prefixlength 0\$" <<< "$rules" || ip -4 rule add pref 32764 table main suppress_prefixlength 0
grep -qE "^32765:.*not from all fwmark .* lookup $t\$" <<< "$rules" || ip -4 rule add pref 32765 not fwmark $t table $t
ip -4 rule show | awk -F: -v t="$t" '($2 ~ /lookup main suppress_prefixlength 0$/ || $2 ~ ("lookup " t "$")) && $1 != 32764 && $1 != 32765 {print $1}' |
  while read -r p; do ip -4 rule del pref "$p" && logger -t warp-watchdog "moved a WARP policy rule from priority $p to the back"; done
exit 0
EOF
chmod 755 /usr/local/sbin/yyc-warp-rules
mkdir -p /etc/systemd/system/wg-quick@wgwarp.service.d
printf '[Service]\nExecStartPost=/usr/local/sbin/yyc-warp-rules\n' > /etc/systemd/system/wg-quick@wgwarp.service.d/20-rules-at-back.conf

cat > /usr/local/sbin/yyc-warp-watchdog <<'EOF'
#!/bin/bash
# the check (installed by warp-watchdog.sh): restarts wg-quick@wgwarp when IPv4 to hosts outside Cloudflare fails twice
set -u
fail=/run/yyc-warp-watchdog.fail last=/var/lib/yyc-warp-watchdog.last
/usr/local/sbin/yyc-warp-rules
ok() { for u in https://api.github.com/ https://www.google.com/ https://ec2.ap-southeast-1.amazonaws.com/; do
  curl -4 -s -o /dev/null -m 8 "$u" && return 0; done; return 1; }
if ok; then rm -f "$fail"; exit 0; fi
n=$(( $(cat "$fail" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$fail"
logger -t warp-watchdog "IPv4 outside Cloudflare failed ($n in a row)"
[ "$n" -ge 2 ] || exit 0
now=$(date +%s); prev=$(cat "$last" 2>/dev/null || echo 0)
if [ $((now - prev)) -lt 1800 ]; then logger -t warp-watchdog "restarted less than 30 min ago: not again"; exit 0; fi
echo "$now" > "$last"
systemctl restart wg-quick@wgwarp
sleep 3
if ok; then logger -t warp-watchdog "restarted wg-quick@wgwarp: IPv4 back"; rm -f "$fail"
else logger -t warp-watchdog "restarted wg-quick@wgwarp: IPv4 still failing"; fi
EOF
chmod 755 /usr/local/sbin/yyc-warp-watchdog

cat > /etc/systemd/system/yyc-warp-watchdog.service <<'EOF'
[Unit]
Description=YYC: restart the WARP tunnel when outgoing IPv4 outside Cloudflare fails
After=wg-quick@wgwarp.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/yyc-warp-watchdog
EOF

cat > /etc/systemd/system/yyc-warp-watchdog.timer <<'EOF'
[Unit]
Description=YYC: the WARP tunnel check every 2 min

[Timer]
OnBootSec=5min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
/usr/local/sbin/yyc-warp-rules
systemctl enable -q --now yyc-warp-watchdog.timer
echo "warp-watchdog: $(systemctl is-active yyc-warp-watchdog.timer); rules: $(ip -4 rule show | grep -E 'suppress_prefixlength 0$|lookup 51820$' | cut -d: -f1 | paste -sd' ')"
