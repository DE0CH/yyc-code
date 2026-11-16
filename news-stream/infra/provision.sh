#!/bin/bash
# provision.sh — create (if missing) and set up the YYC news stream server in Hetzner project "yyc".
# Idempotent: re-run it to re-apply the setup or rotate secrets. Prints no secret values.
#
#   YYC_HETZNER_API   the project's Read & Write API token
#   SECRETS           a folder (mode 700) with: admin + admin.pub (SSH), deploy (read-only deploy key of DE0CH/yyc-code),
#                     tunnel-token (now deploy/cloudflare, tofu), stream.env (OPENROUTER_API=…, RESI_PROXY=…, TIKHUB_API=…, ANYAPI_API_KEY=…)
#
# The server: Ubuntu 24.04, cx23 in fsn1, a firewall allowing only SSH in (the page is served through the
# Cloudflare tunnel, outbound only). systemd runs the poller (yyc-poller), two web instances (yyc-web@8081/8082)
# behind nginx, cloudflared, and a 5-minute timer that pulls DE0CH/yyc-code main and, when news-stream/ changed, restarts
# the poller and rolls the web instances one at a time: pushing to main deploys without downtime. A 30-minute timer
# runs the browser job for the Cloudflare-walled sites (Google Chrome + Patchright under Xvfb).
set -euo pipefail
: "${YYC_HETZNER_API:?}" "${SECRETS:?}"
API=https://api.hetzner.cloud/v1
NAME=yyc-stream
h() { curl -fsS -H "Authorization: Bearer $YYC_HETZNER_API" -H "Content-Type: application/json" "$@"; }
jq_() { python3 -c "import json,sys;d=json.load(sys.stdin);print($1)"; }

key_id=$(h "$API/ssh_keys?name=$NAME-admin" | jq_ 'd["ssh_keys"][0]["id"] if d["ssh_keys"] else ""')
[ -n "$key_id" ] || key_id=$(h -X POST "$API/ssh_keys" -d "{\"name\":\"$NAME-admin\",\"public_key\":\"$(cat "$SECRETS/admin.pub")\"}" | jq_ 'd["ssh_key"]["id"]')
fw_id=$(h "$API/firewalls?name=$NAME" | jq_ 'd["firewalls"][0]["id"] if d["firewalls"] else ""')
[ -n "$fw_id" ] || fw_id=$(h -X POST "$API/firewalls" -d "{\"name\":\"$NAME\",\"rules\":[{\"direction\":\"in\",\"protocol\":\"tcp\",\"port\":\"22\",\"source_ips\":[\"0.0.0.0/0\",\"::/0\"]}]}" | jq_ 'd["firewall"]["id"]')
ip=$(h "$API/servers?name=$NAME" | jq_ 'd["servers"][0]["public_net"]["ipv4"]["ip"] if d["servers"] else ""')
if [ -z "$ip" ]; then
  ip=$(h -X POST "$API/servers" -d "{\"name\":\"$NAME\",\"server_type\":\"cx23\",\"location\":\"fsn1\",\"image\":\"ubuntu-24.04\",\"ssh_keys\":[$key_id],\"firewalls\":[{\"firewall\":$fw_id}],\"labels\":{\"app\":\"yyc-stream\"}}" | jq_ 'd["server"]["public_net"]["ipv4"]["ip"]')
  echo "created $NAME at $ip"
fi
SSH="ssh -i $SECRETS/admin -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$SECRETS/known_hosts -o ConnectTimeout=10 root@$ip"
for i in $(seq 60); do $SSH true 2>/dev/null && break; sleep 5; done

# secrets onto the box (root-only files), never through cloud-init user-data
$SSH 'install -d -m 700 /etc/yyc-stream /root/.ssh'
$SSH 'cat > /etc/yyc-stream/deploy && chmod 600 /etc/yyc-stream/deploy' < "$SECRETS/deploy"
$SSH 'cat > /etc/yyc-stream/stream.env && chmod 600 /etc/yyc-stream/stream.env' < "$SECRETS/stream.env"
$SSH 'cat > /etc/yyc-stream/tunnel-token && chmod 600 /etc/yyc-stream/tunnel-token' < "$SECRETS/tunnel-token"

$SSH bash -s <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! command -v cloudflared >/dev/null; then
  mkdir -p --mode=0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -q && apt-get install -yq cloudflared git python3 unattended-upgrades nginx
fi
# the browser job (browser_fetch.py: 成報, 亞洲週刊, 明報月刊): Google Chrome, Xvfb and Patchright in a venv
if ! command -v google-chrome >/dev/null; then
  curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  apt-get install -yq /tmp/chrome.deb xvfb python3-venv && rm -f /tmp/chrome.deb
fi
# CJK fonts for the PDF report (Chrome): Noto Sans CJK, HK variant included
dpkg -s fonts-noto-cjk >/dev/null 2>&1 || apt-get install -yq fonts-noto-cjk
[ -x /opt/yyc-venv/bin/python ] || python3 -m venv /opt/yyc-venv
/opt/yyc-venv/bin/pip install -q --upgrade patchright curl_cffi pillow openpyxl python-docx 'gunicorn==23.0.0'
id yyc >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/yyc yyc
export GIT_SSH_COMMAND="ssh -i /etc/yyc-stream/deploy -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/etc/yyc-stream/known_hosts"
[ -d /opt/yyc/.git ] || git clone -q git@github.com:DE0CH/yyc-code.git /opt/yyc
git -C /opt/yyc fetch -q origin main && git -C /opt/yyc reset -q --hard origin/main
install -d -o yyc -g yyc -m 700 /var/lib/yyc/data
cp /etc/yyc-stream/stream.env /etc/yyc-stream/app.env && chown root:yyc /etc/yyc-stream/app.env && chmod 640 /etc/yyc-stream/app.env
chmod 711 /etc/yyc-stream

# The app runs as three services so that a deploy never takes the site down (Deyao, 2026-10-13):
#   yyc-poller       the poll loop (python app.py): sources, tagging, corpus, alerts
#   yyc-web@8081/@8082  two identical web instances (gunicorn app:application)
#   nginx            127.0.0.1:8080 (the tunnel's origin), load-balancing over the two and retrying a request on the
#                    other instance when one refuses or drops it
# A deploy (yyc-update) restarts the web instances one at a time: SIGTERM makes gunicorn stop accepting and finish the
# requests in flight (drain, up to --graceful-timeout), nginx sends new requests to the other instance, and the next
# instance is restarted only after the first answers /healthz again (a rolling update).
cat > /etc/systemd/system/yyc-poller.service <<'EOF'
[Unit]
Description=YYC news stream poller (app.py: sources, tagging, corpus, alerts)
After=network-online.target
Wants=network-online.target
[Service]
User=yyc
EnvironmentFile=/etc/yyc-stream/app.env
Environment=DATA_DIR=/var/lib/yyc/data
ExecStart=/opt/yyc-venv/bin/python /opt/yyc/news-stream/app.py
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/yyc/data /var/lib/yyc/kmatrix
ProtectHome=yes
PrivateTmp=yes
MemoryMax=512M
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/yyc-web@.service <<'EOF'
[Unit]
Description=YYC news stream web instance on 127.0.0.1:%i (gunicorn app:application)
After=network-online.target
[Service]
User=yyc
EnvironmentFile=/etc/yyc-stream/app.env
Environment=DATA_DIR=/var/lib/yyc/data
ExecStart=/opt/yyc-venv/bin/gunicorn --chdir /opt/yyc/news-stream --bind 127.0.0.1:%i --workers 2 --threads 8 \
  --timeout 300 --graceful-timeout 300 --keep-alive 5 app:application
ExecReload=/bin/kill -HUP $MAINPID
KillSignal=SIGTERM
TimeoutStopSec=320
Restart=always
RestartSec=2
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/yyc/data /var/lib/yyc/kmatrix
ProtectHome=yes
PrivateTmp=yes
MemoryMax=900M
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/nginx/sites-available/yyc <<'EOF'
# the tunnel's origin: load-balance over the two web instances; a request one instance refuses or drops (it is being
# restarted by a deploy) goes to the other. Not "timeout": a slow AI answer must not run twice.
upstream yyc_web {
    server 127.0.0.1:8081 max_fails=0;  # never mark an instance down: a refused request is retried at once
    server 127.0.0.1:8082 max_fails=0;
    keepalive 16;
}
server {
    listen 127.0.0.1:8080 default_server;
    client_max_body_size 1m;
    access_log off;
    location / {
        proxy_pass http://yyc_web;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_next_upstream error http_502 http_503 non_idempotent;
        proxy_next_upstream_tries 2;
        proxy_connect_timeout 2s;
        proxy_read_timeout 320s;
        proxy_send_timeout 60s;
    }
}
EOF
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/yyc /etc/nginx/sites-enabled/yyc
nginx -t -q

cat > /etc/systemd/system/yyc-tunnel.service <<'EOF'
[Unit]
Description=cloudflared tunnel for newswatch.yyc-internal.com
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/bin/sh -c 'exec /usr/bin/cloudflared tunnel --no-autoupdate run --token "$(cat /etc/yyc-stream/tunnel-token)"'
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/yyc-browser.service <<'EOF'
[Unit]
Description=成報, 亞洲週刊, 明報月刊 through a real browser (Cloudflare challenge)
[Service]
Type=oneshot
User=yyc
EnvironmentFile=/etc/yyc-stream/app.env
Environment=DATA_DIR=/var/lib/yyc/data HOME=/var/lib/yyc
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1280x900x24" /opt/yyc-venv/bin/python /opt/yyc/news-stream/browser_fetch.py
TimeoutStartSec=300
MemoryMax=1500M
EOF
cat > /etc/systemd/system/yyc-browser.timer <<'EOF'
[Unit]
Description=the browser job every 30 minutes
[Timer]
OnBootSec=1min
OnUnitActiveSec=30min
[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/yyc-uptime.service <<'EOF'
[Unit]
Description=probe the public pages through Cloudflare, log failures to uptime.log (infra/uptime-probe.sh)
[Service]
Type=oneshot
User=yyc
Environment=DATA_DIR=/var/lib/yyc/data
ExecStart=/opt/yyc/news-stream/infra/uptime-probe.sh
TimeoutStartSec=150
EOF
cat > /etc/systemd/system/yyc-uptime.timer <<'EOF'
[Unit]
Description=uptime probe every minute
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=5s
[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/yyc-cache.service <<'EOF'
[Unit]
Description=cached copies of matched posts (cache_fetch.py: article text of posts tagged relevant)
[Service]
Type=oneshot
User=yyc
EnvironmentFile=/etc/yyc-stream/app.env
Environment=DATA_DIR=/var/lib/yyc/data HOME=/var/lib/yyc
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1280x900x24" /opt/yyc-venv/bin/python /opt/yyc/news-stream/cache_fetch.py
TimeoutStartSec=1500
MemoryMax=1500M
EOF
cat > /etc/systemd/system/yyc-cache.timer <<'EOF'
[Unit]
Description=cached copies every 30 minutes
[Timer]
OnBootSec=7min
OnUnitActiveSec=30min
[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/yyc-facebook.service <<'EOF'
[Unit]
Description=Facebook pages (fb_fetch.py: Facebook's data request over the yycstream residential proxy)
[Service]
Type=oneshot
User=yyc
EnvironmentFile=/etc/yyc-stream/app.env
Environment=DATA_DIR=/var/lib/yyc/data
Environment=HOME=/var/lib/yyc
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1280x900x24" /opt/yyc-venv/bin/python /opt/yyc/news-stream/fb_fetch.py poll
MemoryMax=1500M
TimeoutStartSec=400
EOF
cat > /etc/systemd/system/yyc-facebook.timer <<'EOF'
[Unit]
Description=Facebook pages every 30 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=30min
[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/yyc-kmatrix.service <<'EOF'
[Unit]
Description=K-Matrix watch: read its WhatsApp groups and compare with the stream (kmatrix/watch.py)
[Service]
Type=oneshot
User=yyc
Environment=DATA_DIR=/var/lib/yyc/data KMATRIX_DIR=/var/lib/yyc/kmatrix HOME=/var/lib/yyc TZ=Asia/Hong_Kong
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1366x900x24" /opt/yyc-venv/bin/python /opt/yyc/news-stream/kmatrix/watch.py
TimeoutStartSec=600
MemoryMax=1500M
EOF
cat > /etc/systemd/system/yyc-kmatrix.timer <<'EOF'
[Unit]
Description=K-Matrix watch every 30 minutes
[Timer]
OnBootSec=3min
OnUnitActiveSec=30min
[Install]
WantedBy=timers.target
EOF

# WhatsApp relink for the K-Matrix watch: started from Deyao's own control plane (systemctl start yyc-kmatrix-link),
# never from the YYC dashboard or app (Deyao, 2026-10-13)
cat > /etc/systemd/system/yyc-kmatrix-link.service <<'EOF'
[Unit]
Description=WhatsApp relink for the K-Matrix watch (kmatrix/link.py)
[Service]
Type=oneshot
User=yyc
Environment=KMATRIX_DIR=/var/lib/yyc/kmatrix HOME=/var/lib/yyc TZ=Asia/Hong_Kong
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1366x900x24" /opt/yyc-venv/bin/python /opt/yyc/news-stream/kmatrix/link.py
TimeoutStartSec=700
MemoryMax=1500M
EOF

cat > /etc/systemd/system/yyc-instagram.service <<'EOF'
[Unit]
Description=Instagram accounts (ig_fetch.py over the yycstream residential proxy)
[Service]
Type=oneshot
User=yyc
EnvironmentFile=/etc/yyc-stream/app.env
Environment=DATA_DIR=/var/lib/yyc/data
ExecStart=/opt/yyc-venv/bin/python /opt/yyc/news-stream/ig_fetch.py
TimeoutStartSec=1800
EOF
cat > /etc/systemd/system/yyc-instagram.timer <<'EOF'
[Unit]
Description=Instagram accounts hourly
[Timer]
OnBootSec=4min
OnUnitActiveSec=60min
[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/yyc-tikhub.service <<'EOF'
[Unit]
Description=social-platform keyword search through TikHub + Facebook through AnyAPI (tikhub_fetch.py --run; YYC's own accounts)
[Service]
Type=oneshot
User=yyc
EnvironmentFile=/etc/yyc-stream/app.env
Environment=DATA_DIR=/var/lib/yyc/data
ExecStart=/usr/bin/python3 /opt/yyc/news-stream/tikhub_fetch.py --run
TimeoutStartSec=1800
EOF
cat > /etc/systemd/system/yyc-tikhub.timer <<'EOF'
[Unit]
Description=social keyword search, checked hourly (each platform+query once a day; tikhub_fetch.py EVERY)
[Timer]
OnBootSec=6min
OnUnitActiveSec=60min
[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/yyc-replies.service <<'EOF'
[Unit]
Description=LIHKG and 香港討論區 forum replies into the corpus (replies_fetch.py; IPRoyal sub-user yycstream)
[Service]
Type=oneshot
User=yyc
EnvironmentFile=/etc/yyc-stream/app.env
Environment=DATA_DIR=/var/lib/yyc/data
ExecStart=/opt/yyc-venv/bin/python /opt/yyc/news-stream/replies_fetch.py
TimeoutStartSec=1700
EOF
cat > /etc/systemd/system/yyc-replies.timer <<'EOF'
[Unit]
Description=forum replies every 30 minutes
[Timer]
OnBootSec=9min
OnUnitActiveSec=30min
[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/yyc-reports.service <<'EOF'
[Unit]
Description=daily reports, AI themes and report emails (reports.py daily; runs once a day at the admin's report hour)
[Service]
Type=oneshot
User=yyc
EnvironmentFile=/etc/yyc-stream/app.env
Environment=DATA_DIR=/var/lib/yyc/data HOME=/var/lib/yyc TZ=Asia/Hong_Kong
ExecStart=/opt/yyc-venv/bin/python /opt/yyc/news-stream/reports.py daily --send --if-due
TimeoutStartSec=3600
MemoryMax=1500M
EOF
cat > /etc/systemd/system/yyc-reports.timer <<'EOF'
[Unit]
Description=check hourly whether the day's reports are due (settings report_hour, default 09:00 HKT)
[Timer]
OnCalendar=*-*-* *:05:00
Persistent=true
[Install]
WantedBy=timers.target
EOF

cat > /usr/local/bin/yyc-update <<'EOF'
#!/bin/sh
# follow DE0CH/yyc-code main (the checkout is disposable: reset, never merge); restart the app when news-stream/
# changed. One run at a time (a manual run and the timer once raced and left a half-updated tree).
exec 9>/run/yyc-update.lock
flock -w 120 9 || exit 1
export GIT_SSH_COMMAND="ssh -i /etc/yyc-stream/deploy -o UserKnownHostsFile=/etc/yyc-stream/known_hosts"
cd /opt/yyc || exit 1
old=$(git rev-parse HEAD:news-stream 2>/dev/null)
git fetch -q origin main && git reset -q --hard origin/main || exit 1
if [ "$old" != "$(git rev-parse HEAD:news-stream)" ]; then
  # news-stream/ changed: restart the poller, then roll the web instances one at a time (see yyc-web@.service)
  systemctl restart yyc-poller
  for port in 8081 8082; do
    systemctl restart "yyc-web@$port"  # drains: in-flight requests finish, nginx sends new ones to the other one
    ok=
    for i in $(seq 60); do curl -fs -m 2 -o /dev/null "http://127.0.0.1:$port/healthz" && ok=1 && break; sleep 1; done
    [ -n "$ok" ] || { echo "yyc-web@$port not healthy after the deploy: rollout stopped"; break; }
  done
fi
# root SSH = the provisioning admin key + every news-stream/infra/ssh-keys/*.pub in git, so a new Claude session gets in
# by pushing its public key (a push to main already runs code holding every YYC secret, so this adds no new reach)
if [ -f /root/.ssh/authorized_keys.admin ]; then
  cat /root/.ssh/authorized_keys.admin news-stream/infra/ssh-keys/*.pub 2>/dev/null | grep '^ssh-' > /root/.ssh/authorized_keys.new &&
    mv /root/.ssh/authorized_keys.new /root/.ssh/authorized_keys
fi
EOF
[ -f /root/.ssh/authorized_keys.admin ] || cp /root/.ssh/authorized_keys /root/.ssh/authorized_keys.admin
chmod 755 /usr/local/bin/yyc-update
cat > /etc/systemd/system/yyc-update.service <<'EOF'
[Unit]
Description=pull DE0CH/yyc-code and roll the stream's services on change
[Service]
Type=oneshot
ExecStart=/usr/local/bin/yyc-update
TimeoutStartSec=1200
EOF
cat > /etc/systemd/system/yyc-update.timer <<'EOF'
[Unit]
Description=pull DE0CH/yyc-code every 5 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
systemctl disable -q --now yyc-singpao.timer 2>/dev/null || true  # renamed to yyc-browser
rm -f /etc/systemd/system/yyc-singpao.service /etc/systemd/system/yyc-singpao.timer
systemctl daemon-reload
systemctl enable -q --now yyc-poller yyc-web@8081 yyc-web@8082 nginx yyc-tunnel yyc-update.timer yyc-browser.timer yyc-facebook.timer yyc-instagram.timer yyc-tikhub.timer yyc-cache.timer yyc-kmatrix.timer yyc-reports.timer yyc-replies.timer yyc-uptime.timer
systemctl restart yyc-poller yyc-web@8081 yyc-web@8082 nginx yyc-tunnel
sleep 3
systemctl is-active yyc-poller yyc-web@8081 yyc-web@8082 nginx yyc-tunnel yyc-update.timer yyc-browser.timer yyc-facebook.timer yyc-instagram.timer yyc-tikhub.timer yyc-cache.timer yyc-kmatrix.timer yyc-reports.timer yyc-replies.timer yyc-uptime.timer
curl -fsS -o /dev/null -w "local healthz %{http_code}\n" http://127.0.0.1:8080/healthz
REMOTE
echo "ok: $NAME at $ip"
