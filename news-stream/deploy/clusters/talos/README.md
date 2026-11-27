# yyc-talos: YYC's Talos cluster

The cluster YYC moves to (DE0CH/aviary yyc/refactor DECISIONS 13, DESIGN 15; plan: session "Kernel patches on K8s runners",
https://1e6d165c-page.deyaochen.com/). Built 2026-11-04 (step T1: control plane and add-ons).

- **Control nodes:** `yyc-talos-cp1..3`, cax11 (ARM) in nbg1 (cx23 sold out everywhere; the plan's fallback), network
  `yyc` at 10.0.0.21-23, placement group `yyc-talos-control`, Hetzner label `yyc-cluster=talos`, firewall
  `yyc-talos` (ICMP and KubeSpan udp 51820 in; nothing else public). They keep Talos's control-plane taint: workloads
  run on workers; cluster add-ons tolerate the taint. Image: Talos v1.14.5 (Image Factory vanilla
  schematic) as Hetzner snapshots 451879029 (arm) and 451879019 (x86), label `os=talos`.
- **Network:** pods 10.210.0.0/16, services 10.211.0.0/16 (never 10.244.0.0/16, the old cluster's); Cilium 1.20.4 in
  VXLAN tunnel mode, MTU 1320, kube-proxy kept; KubeSpan on (MTU 1370, no pod networks); the Hetzner cloud controller
  adds a network `yyc` route per node pod /24. Outgoing IPv4: WARP on `warp0` (one WARP account per node,
  `warp-register.py`, store key `YYC_TALOS_WARP_<NODE>`); DNS: Hetzner's resolvers over IPv6. eth1 (network `yyc`)
  gets its address by DHCP (`patches/common.yaml`: Talos's hcloud platform configures only eth0).
- **API:** endpoint https://10.0.0.21:6443 (KubePrism gives every node a local HA endpoint); certificate SANs include
  127.0.0.1 and all three addresses. GitHub Actions' OIDC tokens for `yyc-ai.yml` are trusted through Talos's
  `KubeAuthenticationConfig` (`patches/controlplane.yaml`). CA:
  `infra/yyc-talos-ca.pem`.
- **Operator access:** nothing is public. `talos.sh tunnel` (or `~/artifacts/on-start/talos-tunnel.sh`) forwards the
  three Talos APIs to 127.0.0.1:50001-50003 and the Kubernetes APIs to 127.0.0.1:6443-6445 through `yyc-master1`;
  `talos.sh talosconfig` writes the talosconfig, `talosctl kubeconfig` the admin kubeconfig (server
  https://127.0.0.1:6443).
- **Secrets:** machine secrets `YYC_TALOS_SECRETS` and the WARP keys in store `yyc`; machine configs are rendered by
  `talos.sh gen NODE ROLE` into `~/talos/` and never committed. Sealed Secrets has this cluster's own key (cert
  `infra/sealed-secrets-talos.pem`).
- **Add-ons** (`talos.sh addons`, values in `addons/`): Cilium, the Hetzner cloud controller and CSI driver
  (StorageClass `yyc-block`), Sealed Secrets 0.40.0, cert-manager v1.21.2, CloudNativePG 1.30.1 (the old cluster's
  version) and its Barman Cloud plugin, PriorityClasses `serving` and `batch`.

- **Scheduling:** MostAllocated (`patches/controlplane.yaml` KubeSchedulerConfig): a pod goes to the fullest node that
  fits; the Descheduler (`descheduler.yaml`, 04:00 HKT, HighNodeUtilization, workers only) empties nearly empty workers.
  It never evicts in `db`, `yyc-ai-fabric` or `kube-system`; `suspend: true` on its CronJob stops it.
- **etcd snapshots:** `etcd-backup.yaml`, hourly at minute 17: a Job in `talos-ops` takes `talosctl etcd snapshot`
  from the first control node that answers (Talos role `os:etcd:backup`, from Talos's Kubernetes API access:
  `KubeTalosAPIAccessConfig`) and uploads it to Hetzner bucket `yyc-talos-etcd` (fsn1) under `yyc-talos/`, 7 days kept.
  Credential: SealedSecret `talos-ops/etcd-backup-s3` (store keys `YYC_S3_ACCESS_KEY`, `YYC_S3_SECRET_KEY`). Restore:
  Talos's disaster recovery (`talosctl bootstrap --recover-from=<snapshot>`) on a node made from `YYC_TALOS_SECRETS`.
- **Postgres high availability and backups (Deyao, 2026-11-09: the industry standard):**
  - Copies: `pg` 3 (db1, db2, db3: one per dedicated db node in fsn1) and `magpie-pg` 3 (w1-w3), both strict: a commit
    waits for one standby (`any 1`, dataDurability required) and a failover promotes only a standby holding every
    confirmed commit (failoverQuorum). Standbys can be swapped with no effect on writes. The db nodes are cpx42 (16 GB;
    Deyao, 2026-11-09: the data tools' queries need the posts table in memory); `db/resize-node.sh NODE TYPE` resizes a
    standby's node in place (disk kept).
  - Moving a primary (for a resize or upgrade of its node, or of Postgres) is `db/switchover.py CLUSTER TARGET`, with
    zero downtime: it fences writes first (an EXCLUSIVE lock on every table of every database: running write
    transactions finish, new writes wait, reads go on), then the operator switches over; every app runs the work cut
    by it again (news-stream `corpus.retrying`, aviary-pg `retry`, ai-watch `db.retrying`, the web app's and yyc-mcp's
    `db.ts`, tools' `CallLog`, egress's buffered log), which the fence makes safe. Both clusters are
    `primaryUpdateStrategy: supervised`, so the operator never switches over unfenced by itself. Measured on a test
    cluster (2026-11-09): writes held 10 s (fence 0.1 s, stop and promotion 4.7 s, the -rw Service moving 3 s, its
    route reaching the nodes 2.4 s); requests in that window wait, none fails. Planned switchovers run in Deyao's
    working hours (claude-env CLAUDE.md).
  - Backups: the WAL archive (continuous) plus a base backup (pg weekly, Sunday 03:00 HKT; magpie-pg nightly) to
    bucket `yyc-pg-pitr`, point-in-time restore to any moment of the last 14 days; a daily locked `pg_dump` of each
    database to `yyc-pg-backups` (Object Lock, 30 days); `db/heartbeat.yaml` writes `ops.heartbeat` every minute in both.
  - Off-site: `db/offsite-copy.yaml` copies both buckets hourly to Cloudflare R2 bucket `yyc-pg-offsite` (YYC's
    account, Western Europe). Its bucket lock keeps every object 14 days (no token can delete one sooner); the job's
    token `yyc-pg-offsite` (store keys `YYC_PG_OFFSITE_R2_*`, SealedSecret `db/pg-offsite-r2`) can only read and write
    objects in that bucket. The lock rule was set with a one-off setup token, deleted after (cloudflare-dash skill).
  - Restore tests: `db/restore-test.yaml`, weekly on Tuesdays (pg 11:00, magpie-pg 14:00 HKT), from Hetzner in odd ISO
    weeks and from R2 in even weeks: a throwaway Cluster restored to 10 minutes ago (StorageClass `yyc-scratch`, reclaim
    Delete), checked (the databases, `ops.heartbeat` within 180 s of the target, `pg_amcheck`), timed, deleted. Results:
    ConfigMap `db/restore-test-<cluster>`; the monitor Discords Deyao on a failure or no success for 8 days, and when
    WAL archiving stops. A manual run: `kubectl -n db create job --from=cronjob/restore-test-pg <name>` (env
    `SOURCE_STORE=hetzner|r2` forces the source).
- **Logs:** `alloy.yaml`, Grafana Alloy on every node: pod logs, Talos's services (`machine.logging` json_lines to
  127.0.0.1:6050) and the kernel (`KmsgLogConfig`, 127.0.0.1:6051) to Grafana Cloud with labels `cluster="yyc-talos"`,
  `node`, `source` pod|talos|kernel. Credential: SealedSecret `alloy/grafana` (store keys `YYC_GRAFANA_LOGS_URL`,
  `YYC_GRAFANA_LOGS_USER`, `YYC_GRAFANA_WRITE_TOKEN`).
- **JuiceFS CSI driver** (chart 0.33.0, `addons/juicefs-csi.values.yaml`): the old cluster's values, node plugin on the
  workers labelled `yyc.hetzner/network=yyc`.

## Applying

Machine config: `talos.sh gen <node> controlplane|worker`, then `talos.sh apply <node> <ip> --dry-run` and
`talos.sh apply <node> <ip>`, one node at a time (the control nodes: wait for `talosctl health` between them).
Helm add-ons: `talos.sh addons`. Manifests: `talos.sh manifests` (`kubectl apply -k` of this folder; needs the control
nodes' `KubeTalosAPIAccessConfig` first, which creates the `talos.dev` ServiceAccount CRD). Secrets: lines in
`infra/secrets.txt`, sealed with `infra/seal.py --cert infra/sealed-secrets-talos.pem NS/NAME` into `sealed/`.

## A new node

1. A control node: a WARP key first: `warp-register.py <name> | store-put.py …` (store key `YYC_TALOS_WARP_<suffix>`);
   a worker needs none. Then `talos.sh gen <name> controlplane|worker` (a worker gets `patches/worker.yaml`: labels
   `yyc.hetzner/network=yyc`, `pool=apps`).
2. A control node: `hcloud server create --name <name> --type <type> --location <loc> --image <snapshot>
   --without-ipv4 --label yyc-cluster=talos --placement-group … --firewall yyc-talos --user-data-from-file
   ~/talos/<name>.yaml --start-after-create=false`, `hcloud server attach-to-network <name> --network yyc --ip <ip>`,
   `hcloud server poweron <name>`. A worker: the same with `--without-ipv6 --network yyc` and no firewall (a server
   with no public IP needs its network at creation), then `detach-from-network` and `attach-to-network … --ip <ip>`
   before `poweron`. Images: x86 (cpx) snapshot 451879019, ARM (cax) 451879029. Workers: `yyc-talos-w1..` at 10.0.0.31..
   (w1-w3: cax21 ARM in nbg1, placement group `yyc-talos-workers`, no public IP: "Workers without public IPs"; every
   YYC AI image is built for amd64 and arm64). A news node with no public IP: role `news-private` (private.yaml and
   node-patches/news.yaml: pool=news, none of the YYC AI labels), created as a worker; `yyc-talos-news6..8` (cx23 hel1,
   10.0.0.41-43, 2026-11-08). A database node with no public IP: role `db-private` (private.yaml and node-patches/db.yaml: pool=db, taint
   dedicated=db), created as a worker; `yyc-talos-db3` (fsn1, 10.0.0.3, 2026-11-09; cpx42 since 2026-11-09). The news pool's size comes from packing (Deyao, 2026-11-08): every news Deployment's
   requests plus the CronJobs' worst overlapping minute (each run lasting its activeDeadlineSeconds) must fit, replicas
   on different nodes; on 2026-11-08 that was 7.5 GiB steady + 6.1 GiB of jobs, 5 x cx23 with 2.3 GiB to spare.

## Workers without public IPs

Hetzner counts every primary IP, IPv6 ones too, against the account's limit (16 across all projects), and the limit
stays (Deyao, 2026-11-05). So the workers are created with no public IP (`--without-ipv4 --without-ipv6`): network
`yyc` is their only link (`patches/private.yaml`: DHCP on eth0, default route via 10.0.0.1, DNS 10.0.0.30 and
10.0.0.29), and they have no WARP of their own. Network `yyc`'s route 0.0.0.0/0 sends their outgoing traffic to
**yyc-nat2** (10.0.0.29, alias 10.0.0.30; cax11 fsn1, Debian, a public IPv4 and no IPv6: "we should have at least one
ipv4", Deyao, 2026-11-27). The box is `nat/nat2.yaml`, its user data:

- **Out:** to Cloudflare's tunnel edge (nftables set `direct`: cloudflared's connections) through its own IPv4, so
  YYC AI's site stays reachable when WARP fails; everything else through WARP (`warp0`, account `YYC_NAT1_WARP`, exit
  104.28.207.215, colo FRA), because many news sites refuse Hetzner's addresses and accept WARP's (from its own IPv4,
  24 of the poller's sources failed every round, 2026-11-27). Masqueraded either way.
- **WARP check:** `yyc-warp-check.timer` asks EC2 through `warp0` every minute with a 3 s bound and restarts
  `wg-quick@warp0` at every failure (a restart pauses WARP about 3 s; log `journalctl -t warp-check`). Its predecessor
  restarted at most once in 10 min, so a restart that did not help left all IPv4 down for 10 min: YYC AI's site 530
  07:58-08:08 UTC on 2026-11-27, 47 min in all on 2026-11-26, when WARP needed 34 restarts.
- **DNS:** dnsmasq on 10.0.0.29 and 10.0.0.30, forwarding to Hetzner's resolvers.
- **The jump host:** sshd at 10.0.0.30 (`jump.yyc-internal.com`, talos.sh's tunnel, `deploy-access.yaml` name that
  address), root by the `yyc-cluster-admin` key; its host keys are the old yyc-nat1's, so `known_hosts` files stay
  valid. The address is a Hetzner alias IP: `yyc-jump-alias.service` adds it to the system only when the metadata
  lists it (carried earlier, while another server held it, the box answered that server to itself).

KubeSpan between workers and control nodes runs over network `yyc`. yyc-nat2 is a network box only, and a single one:
while it is down, workers have no outgoing traffic (image pulls, WARP), though traffic inside the cluster and network
`yyc` keeps flowing.

### yyc-nat2

Created 2026-11-27 (Hetzner server 177908877), replacing yyc-nat1 (its snapshot: image 461607840). Again, from
nothing, with `YYC_HETZNER_API` (no hcloud CLI needed: the API):

1. `POST /servers` with `name: yyc-nat2, server_type: cax11, location: fsn1, image: debian-13`, ssh key
   `yyc-cluster-admin`, labels `yyc-cluster=talos, role=nat`, firewall `yyc-talos`,
   `public_net: {enable_ipv4: true, enable_ipv6: false}`, `user_data` = `nat/nat2.yaml`, `start_after_create: false`;
   then `attach_to_network` network `yyc` at `10.0.0.29` before its first boot (cloud-init records the address it sees
   then) and `poweron`.
2. `/etc/wireguard/warp0.conf` from `YYC_NAT1_WARP` over SSH (the key is a secret, never user data): `Address`,
   `PrivateKey` and the peer's `PublicKey` from the key, `MTU = 1280`, `Table = off`,
   `PostUp = ip route replace default dev warp0 table 51; ip rule add fwmark 0x51 lookup 51 priority 1000` (and the
   matching `PostDown`), `Endpoint = 162.159.192.1:2408` (WARP's IPv4 endpoint: the box has no IPv6),
   `AllowedIPs = 0.0.0.0/0`, `PersistentKeepalive = 25`; then `systemctl restart wg-quick@warp0`.
3. The jump address: `change_alias_ips` on network `yyc` with `["10.0.0.30"]` once no other server holds it, then
   `systemctl restart yyc-jump-alias`; for the same `known_hosts`, the host keys of the box it replaces.
4. Network `yyc`'s route `0.0.0.0/0` to `10.0.0.29` (`delete_route`, `add_route`: Hetzner has no update; the open
   connections are cut once). The nodes' resolvers came over first, with `talosctl patch mc --mode=no-reboot`
   (`patches/private.yaml`'s ResolverConfig; Talos's merge appends to the list).

A check from the box: `curl --interface warp0 -s -o /dev/null -w '%{http_code} %{time_appconnect}s\n'
https://ec2.ap-southeast-1.amazonaws.com/` (through WARP) and the same with `--interface eth0` (its own IPv4).

## Outgoing proxy pool (`socks.yaml`, 2026-11-05)

DaemonSet `egress/socks` (yyc-socks, socks/README.md) on every worker in network `yyc`, host network, Service
`socks.egress:1080`, as on the old cluster. The workers share one exit: yyc-nat2's WARP (yyc-nat1's account and address before it), 104.28.207.215 (colo FRA), the
same address the old cluster's nodes leave from (both read 2026-11-05), so a copy per worker gives availability, not
exit diversity (the old pool had none either: README of the old cluster, "one location, one exit"). No IPv6 there:
`FAMILIES=ipv4` and the sandbox resolvers over IPv4. Sample of 20 sites the stream and YYC AI read, 2026-11-05, old
master (IPv6 first) against w1 (IPv4 through yyc-nat1's WARP): the same answer for 17; news.mingpao.com 403 -> 200 and
www.hkej.com 403 -> 301 (their Cloudflare refuses Hetzner's IPv6, not WARP), www.reuters.com 401 -> 403 (refused on
both). Not chosen: copies on the control nodes (their own WARP, colo CDG, and native IPv6; but workloads stay off
control nodes) or on the NAT box (a network box). Image tag: `images:` in kustomization.yaml, pinned by socks-image.yml.

## Node work (`.github/workflows/talos-nodes.yml`, `node-work.sh`)

- Weekly, Tuesday 04:00 HKT: a newer patch release of `release.env`'s minor is committed to `release.env`, then every
  node is upgraded to `release.env`'s version, control nodes first, one at a time (`talosctl upgrade`: drain, upgrade,
  reboot, wait; then the node Ready at the new version and `talosctl health` green). A push to `release.env` does the
  same; a dispatch runs `upgrade` (one node or all) or `upgrade-k8s` (`k8s_version`), `mode` dry-run by default.
- **Singletons move first; database primaries go last** (`node-work.sh`, 2026-11-17). Before a node's upgrade the script
  cordons it and moves every Deployment there that a PodDisruptionBudget does not let go (the news stream's poller,
  wisers, whatsapp, monitor, transcribe, tiktok-videos) by its own rolling update: `talosctl upgrade`'s drain only
  evicts and waited 15 min on the poller at `yyc-talos-news1`, then failed. A singleton with no room for its second
  pod stops the run red, the node uncordoned and untouched (the news pool needs 2 Gi free on one node for the poller:
  claude-env lessons/107). Workers holding a CloudNativePG primary are taken last, so a Cluster switches over once:
  in name order db1, db2, db3 moved Cluster `pg`'s primary three times, with connections refused on and off for 3 min
  25 s (04:23:46-04:27:11 UTC) and whatsapp and aiwatch restarting. RBAC for it: `node-work.yaml` (`gha:talos-nodes`
  may patch nodes and Deployments, read pods, ReplicaSets and PodDisruptionBudgets).
- **The installer is Hetzner's (`hcloud-installer`), never the generic `installer`** (`release.env`; `node-work.sh`
  refuses any other). The arm64 nodes (control nodes, `yyc-talos-w*`) boot a UKI and the kernel command line comes with
  the installer: the generic one's lacks `talos.platform=hcloud` and `net.ifnames=0`, so the node boots as platform
  `metal` with links `enp1s0`/`enp7s0`, no hostname (`talos-xxx-xxx`) and no address, and that boot also empties the
  cached platform network config (yyc-talos-cp1 on v1.14.6, 2026-11-17: down for 80 min, the run stopped there).
  A node in that state: Hetzner console → reset → Space for the boot menu → the previous `Talos (vX)` entry (never a
  "Reset to maintenance mode" one); if `talosctl get platformmetadata` then still says `metal` and `dmesg` shows the
  metadata fetch timing out, `talosctl patch mc --mode=no-reboot --patch @patches/eth0-dhcp.yaml`; then `talosctl
  upgrade --image <hcloud-installer>:<version>` (the old entry is booted once only; the bad UKI stays the default
  until replaced).
- **The sandbox gateway** is no node of this cluster (the plain server `yyc-sbx-gw1`, 10.0.0.8, DE0CH/yyc-code
  `DE0CH/aviary yyc/infra/gateway.sh`): node work never stops the sandboxes' network.
- **Path:** the runner reaches the API through tunnel `yyc-deploy` (deploy-workflows 3) as `gha:talos-nodes` (its own
  GitHub environment `talos-nodes` with the Access secrets `YYC_DEPLOY_ACCESS_CLIENT_ID`/`_SECRET`; the authenticator in
  `patches/controlplane.yaml`; RBAC in `node-work.yaml`: nodes read, Jobs in `talos-ops`). Each upgrade is a Job in
  `talos-ops` whose talosctl carries Talos role `os:admin` from Talos ServiceAccount `node-work`, scheduled away from
  the node it upgrades. No Talos or cluster credential is stored in GitHub.

## Readiness for YYC AI

`DE0CH/aviary yyc/infra/talos-ready.sh` (read-only, both clusters) checks DESIGN 15.2 N1-N12 and the build inputs I1-I9; its
PENDING lines are the tests that need probe pods, a GitHub run or the gateway image.

## Still to do (T1-T2, DESIGN 15.2)

Workers (N5, N7, N12); `gha:talos-cluster` in the control nodes' authenticator (patches/controlplane.yaml),
then `talos-cluster.yml`'s push trigger on; `talos-ready.sh` green.

## Cluster workflow (`.github/workflows/talos-cluster.yml`)

`kubectl diff -k` then `kubectl apply -k` of this folder (no prune), as `gha:talos-cluster` (GitHub environment
`talos-cluster`, main only, the Access secrets `YYC_DEPLOY_ACCESS_CLIENT_ID`/`_SECRET`; cluster-admin by
`cluster-access.yaml`) through tunnel `yyc-deploy`. Dispatch: `mode` apply (default) or plan. Not the machine config,
the Helm add-ons or `db/`.

## Deploy path (tunnel `yyc-deploy`, deploy-workflows 3 and 7; live 2026-11-05)

tofu `deploy.tf` (tunnel, `k8s-deploy.yyc-internal.com`, Access app "YYC deploy API" admitting one service token);
store `yyc` keys `YYC_CF_DEPLOY_TUNNEL_TOKEN` (sealed as `yyc-deploy/tunnel`), `YYC_DEPLOY_ACCESS_CLIENT_ID`/`_SECRET`
(GitHub environment secrets of `yyc-ai`, `talos-nodes`, `talos-cluster`, each main only; set with `YYC_CI_ENV_TOKEN`);
connector `yyc-deploy/cloudflared` (2 replicas, `deploy-access.yaml`). The same path from a session:
`cloudflared access tcp --hostname k8s-deploy.yyc-internal.com --url 127.0.0.1:16443` with the service token in
`TUNNEL_SERVICE_TOKEN_ID`/`_SECRET`.
