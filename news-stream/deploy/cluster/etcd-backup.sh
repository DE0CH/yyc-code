#!/bin/bash
# etcd-backup.sh — on the control-plane node (`cluster.sh ssh bash -s < etcd-backup.sh`): every 6 hours an etcd snapshot
# onto the Hetzner Volume `yyc-etcd-snapshots` (10 GB, attached to yyc-master1, mounted by Hetzner's automount), 28 kept
# (7 days). k3s's own hourly snapshots (24 kept) stay on the node's disk; this copy survives losing the node.
# Restore: cluster/README.md "Rebuilding the control plane". Idempotent.
set -euo pipefail
VOL=$(findmnt -rn -S /dev/disk/by-id/scsi-0HC_Volume_* -o TARGET 2>/dev/null | head -1)
[ -z "$VOL" ] && VOL=$(ls -d /mnt/HC_Volume_* | head -1)
mountpoint -q "$VOL" || { echo "volume not mounted"; exit 1; }
install -d -m 700 "$VOL/etcd"
ln -sfn "$VOL/etcd" /var/lib/yyc-etcd-snapshots
cat > /etc/systemd/system/yyc-etcd-snapshot.service <<EOF
[Unit]
Description=etcd snapshot of the YYC cluster onto the Volume yyc-etcd-snapshots (news-stream/deploy/cluster/etcd-backup.sh)
After=k3s.service
[Service]
Type=oneshot
ExecStart=/usr/local/bin/k3s etcd-snapshot save --dir /var/lib/yyc-etcd-snapshots --name yyc
ExecStartPost=/usr/local/bin/k3s etcd-snapshot prune --dir /var/lib/yyc-etcd-snapshots --name yyc --snapshot-retention 28
EOF
cat > /etc/systemd/system/yyc-etcd-snapshot.timer <<'EOF'
[Unit]
Description=etcd snapshot every 6 hours (yyc-etcd-snapshot.service)
[Timer]
OnCalendar=*-*-* 00/6:30:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
grep -q HC_Volume /etc/fstab || echo "note: no fstab line for the volume (Hetzner automount writes one)"
systemctl daemon-reload
systemctl enable --now yyc-etcd-snapshot.timer
systemctl start yyc-etcd-snapshot.service
ls -la /var/lib/yyc-etcd-snapshots/
systemctl list-timers yyc-etcd-snapshot.timer --no-pager | head -3
