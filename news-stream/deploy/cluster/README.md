# The YYC cluster `yyc` (Hetzner project `yyc`)

k3s built with [hetzner-k3s](https://github.com/vitobotta/hetzner-k3s) v2.6.0 (MIT) from `hetzner-k3s.yaml`, Flux from
`../clusters/hetzner/`. Every server IPv6-only (no public IPv4; Deyao, 2026-10-13), on the private network `yyc`
(10.0.0.0/16, eu-central), firewall `yyc` = SSH in, everything from the private network, ICMP; the k8s API only from
the private network. What people use leaves through Cloudflare tunnels (outbound only). Uptime target: reasonable,
not high (Deyao, 2026-10-20): one control-plane node, rebuilt from an etcd snapshot if lost.

| Server | Type | Location | Role |
|---|---|---|---|
| `yyc-master1` | cx23 | fsn1 | the control plane (embedded etcd, single member); tainted `CriticalAddonsOnly=true:NoExecute` |
| `yyc-pool-small-worker1..5` | cx23 | fsn1 | pool `small` (label `pool=apps`), 2770 Mi allocatable each; the 5th since 2026-10-27 (Deyao): room for the poller's 2 Gi rollout off the browser-data node, as YYC AI's UI and its JuiceFS mount took the cpx32's |
| `yyc-pool-apps-worker2` | cx33 | fsn1 | apps node from the first build (label `pool=apps`, 6.54 Gi allocatable; its pool is no longer in the config, hetzner-k3s leaves it alone) |
| `yyc-pool-large-worker1` | cpx32 | fsn1 | pool `large` (label `pool=apps`, 2026-10-21), 4 vCPU / 8 GB, 6.54 Gi allocatable: room for the poller's 2 Gi rollout surge beside the jobs |
| `yyc-pool-large-worker2` | cpx32 | fsn1 | pool `large` (label `pool=apps`, Deyao, 2026-10-29), 4 vCPU / 8 GB: room for the in-cluster site and yyc-mcp (the Cloudflare Workers moving next to Postgres) and their rollout surges |
| `yyc-pool-db-worker1` | cx33 | fsn1 | pool `db` (label `pool=db`, taint `dedicated=db:NoSchedule`; Deyao, 2026-10-27): YYC's Postgres alone (`../db/`), private address 10.0.0.2 (the sandbox hosts' JuiceFS reaches NodePort 30432 there) |

Volumes (fsn1): `yyc-etcd-snapshots` (10 GB, on yyc-master1) and the apps' PVCs (StorageClass `yyc-block`), Postgres's 100 Gi among them.
`./cluster.sh nodes` lists the servers with their IPv6 and private address.

**Why the master stays tainted:** it is the only control-plane node. A job's memory spike or an eviction there would
take the API and etcd with it, and there is no second one to fail over to; its 4 GB would add little next to the apps
nodes. Apps and jobs run on `pool=apps` nodes only.

**Where pods may run:** any `pool=apps` node (Deyao, 2026-10-20: every workload, the poller included, worked with
~160 ms to the database when it was in Frankfurt). Postgres runs alone on `yyc-pool-db-worker1` (taint `dedicated=db`). Volumes attach only within their location (fsn1), so the jobs that mount one need a node in fsn1.

## Nodes: one location, one exit

Every node is in fsn1. Each node's WARP lands at its nearest Cloudflare colo, so all of them leave from PRG
(104.28.207.215 on 21 Oct) and any pod reads the same sites wherever it runs: no workload needs a node rule for its
exit. A node in another location gets another colo, and sites may refuse it (21 Oct: a hel1 node's exit, colo HEL,
failed 14 of 17 sampled sites that fsn1 read; 403/429/409, TLS handshake timeouts, challenge pages). Before a node
outside fsn1 joins, read the sample sites from it and from an fsn1 node (`run.sh --node NAME`) and compare; a WARP exit
can't be picked per region (anycast lands at the nearest colo).

Room for the poller's 2 Gi rollout (deploy/README.md "Rollouts"): the new poller pod needs 2 Gi free on a node
without the browser-data Volume (the poller's anti-affinity). `yyc-pool-large-worker1` (cpx32, 2026-10-21) held it
until YYC AI's UI and its JuiceFS mount (only on the 8 GB nodes) took it; on 2026-10-27 a rollout stayed Pending, and
the 5th cx23 (Deyao, 2026-10-27) brings the room back. Before adding a workload, check that a 2 Gi slot remains.

## Scripts

- `cluster.sh create`: makes the network `yyc` if missing, puts the bastion in it, then `hetzner-k3s create` there;
  re-runnable (adds what is missing). `cluster.sh upgrade` (k3s_version), `cluster.sh flux` (Flux + its two secrets +
  `../clusters/hetzner/flux-sync.yaml`), `cluster.sh ssh CMD` (kubectl/flux on yyc-master1), `cluster.sh nodes`.
  `HK3S_CONFIG=other.yaml` runs it with a one-off config (e.g. a temporary pool).
- `node-setup.sh`: every node's first boot, before k3s (embedded into the config by cluster.sh): its own WARP account.
- `warp-watchdog.sh`: every node, after k3s (embedded; existing nodes: `cluster.sh warp-watchdog`): a 2-min check that
  restarts the WARP tunnel when IPv4 to hosts outside Cloudflare fails twice in a row (a tunnel that passes only
  Cloudflare's addresses, claude-env lessons/93), and keeps the tunnel's two policy rules at priorities 32764/32765
  after every start (`yyc-warp-rules`, ExecStartPost): wg-quick alone puts them in front of the first rule there is,
  which on the sandbox gateway node is sandboxd's `iif wg0` rules (claude-env lessons/97).
- `migrate.sh STEP`: moves the apps between clusters without a gap for anything that serves (`../MIGRATE.md`).
- `etcd-backup.sh`: on the master (`cluster.sh ssh bash -s < etcd-backup.sh`): systemd timer `yyc-etcd-snapshot`,
  an etcd snapshot every 6 hours onto the Volume `yyc-etcd-snapshots` (`/var/lib/yyc-etcd-snapshots`), 28 kept;
  k3s's own hourly snapshots (24 kept) stay in `/var/lib/rancher/k3s/server/db/snapshots`.
- `cluster.sh db-forward [PORT]`: YYC's Postgres on localhost:PORT (default 15432) of the session (an ssh tunnel to a
  `kubectl port-forward` of Service `pg-rw.db`; the database has no public address).
- `cluster.sh cert`: the Sealed Secrets controller's public cert into `/infra/sealed-secrets.pem` (after a rebuild).
- `cluster.sh ca`: the k3s server CA (ConfigMap `kube-root-ca.crt`) into `/infra/yyc-cluster-ca.pem`; after a rebuild.
  No deploy workflow reaches this cluster: Flux applies it (`yyc-ai.yml` targets the Talos cluster, `../clusters/talos/`).
- `verify.sh`: nodes, WARP per node, workloads, volumes, egress from a pod, mail merge isolation, and the sites through
  Cloudflare and Access (a temporary service token and policies, removed on exit).

```bash
S=~/.yyc-cluster; install -d -m 700 $S                         # the yyc store's keys, never in git
printf '%s\n' "$YYC_CLUSTER_SSH_KEY" > $S/admin; chmod 600 $S/admin; ssh-keygen -y -f $S/admin > $S/admin.pub
printf '%s\n' "$YYC_FLUX_DEPLOY_KEY" > $S/flux-deploy; chmod 600 $S/*
cd news-stream/deploy/cluster
SECRETS=$S ./cluster.sh create && SECRETS=$S ./cluster.sh flux  # YYC_HETZNER_API in the env
SECRETS=$S ./cluster.sh ssh kubectl get pods -A
SECRETS=$S ./verify.sh                                           # CLOUDFLARE_API in the env
```

## How it is built (and why)

- **hetzner-k3s connects only over IPv4.** With no public IPv4 it SSHes to the servers' PRIVATE addresses, so
  cluster.sh runs it on a bastion inside the private network: `yyc-master1` (any control-plane node works), or the old
  node while it exists. Its kubeconfig points at the masters' private addresses; from outside, use `cluster.sh ssh`.
- **Outgoing IPv4 = Cloudflare WARP, one free account per node** (`node-setup.sh`): GitHub, ghcr.io and most HK news
  sites have no IPv6. A WireGuard key works for one peer at a time, so each node registers its own account with WARP's
  API (reachable over IPv6) at first boot and keeps it in `/etc/wireguard/wgwarp.conf` (`wg-quick@wgwarp`, IPv4
  only, no DNS line, endpoint over IPv6). The name starts with `wg` so hetzner-k3s never takes it for the private
  interface. wg-quick's policy routing sends only IPv4 to the internet through it; the private network, the metadata
  service and flannel's routes stay in the main table. k3s starts after it at boot.
- **Pods are IPv4-only** (cluster CIDR 10.244.0.0/16, Services 10.45.0.0/16, DNS 10.45.0.10): their traffic leaves
  masqueraded through the node's WARP. So the tunnel connectors use `--edge-ip-version auto` (they reach Cloudflare's
  edge over IPv4/QUIC through WARP).
- **Pods' DNS never goes through WARP** (`node-dns.sh`, 2026-11-04): CoreDNS forwards outside names to
  169.254.20.10, a dummy interface `yycdns` on each node where that node's systemd-resolved listens; resolved asks
  Hetzner's resolvers over the node's IPv6. hetzner-k3s writes `nameserver 8.8.8.8` to `/etc/k8s-resolv.conf`
  (kubelet's resolv-conf); node-dns.sh, run after it (additional_post_k3s_commands; existing nodes: `cluster.sh
  node-dns`), replaces it. Why: 8.8.8.8 through one node's WARP went silent for minutes to hours (2026-11-02/03,
  ~69,000 CoreDNS timeouts on one node, every outside name; YYC AI's web_search failed on it).
- **The private network carries k3s**: node IPs, etcd, the API and flannel (wireguard-native between nodes).
- **Kubelet** (all nodes, `kubelet_args`): pod-max-pids 4096; kube-reserved cpu 200m / 400 Mi / 2 Gi disk;
  system-reserved cpu 150m / 350 Mi / 2 Gi disk; eviction-hard memory < 300 Mi, disk < 10 %, inodes < 5 %; container
  logs 3 × 20 Mi. A cx33 then has 3650m CPU and 6.54 GiB memory allocatable.
- **Storage**: hetzner-k3s installs the hcloud CSI driver; `../clusters/hetzner/storageclass.yaml` defines `yyc-block`
  (Hetzner Volumes, Retain, WaitForFirstConsumer), the only StorageClass the apps name. Volumes live in fsn1, like the
  apps nodes.
- **metrics-server**: hetzner-k3s's addon (`kubectl top`). No Cluster Autoscaler, no sandbox pool and no gVisor here:
  YYC AI's sandboxes run in their own cluster.
- **Addons off**: traefik, servicelb, local-path, system-upgrade-controller (k3s upgrades = `cluster.sh upgrade`).

## Rebuilding the control plane

If `yyc-master1` is lost, the apps nodes keep running their pods (the API is gone until it's back):

1. Recreate the server: `SECRETS=$S BASTION=<IPv6 of any apps node> ./cluster.sh create` builds `yyc-master1` anew
   (the bastion only needs to be in the private network). Don't let the new server start k3s as a fresh cluster with
   workloads yet: stop it (`systemctl stop k3s`) right after the install.
2. Attach the Volume `yyc-etcd-snapshots` to the new server (Console or `hcloud volume attach yyc-etcd-snapshots
   --server yyc-master1 --automount`), pick the newest snapshot in `/mnt/HC_Volume_*/etcd/`.
3. Restore with the cluster's token (`YYC_K3S_TOKEN`, store yyc):
   `k3s server --cluster-reset --cluster-reset-restore-path=<snapshot> --token=<YYC_K3S_TOKEN>`, then
   `systemctl start k3s`. The agents reconnect by themselves (their K3S_URL is `https://10.0.0.3:6443`: give the new
   master that private IP: `hcloud server detach-from-network` + `attach-to-network --network yyc --ip 10.0.0.3`).
4. `cluster.sh ssh bash -s < etcd-backup.sh` again, `verify.sh`.
5. `cluster.sh ca`: the restored cluster keeps its CA, so `git diff infra/yyc-cluster-ca.pem` is empty; if not, commit
   and push it (the deploy workflow trusts only that CA).

The etcd snapshot holds the Sealed Secrets controller's key too, so after this restore the SealedSecrets in git still
open: no re-sealing.

## Secrets after a rebuild

The cluster's Secrets are SealedSecrets (`../README.md` "Secrets"). Their key exists only in the cluster: there is no
copy outside it (Deyao, 2026-10-21). A cluster rebuilt from nothing (no etcd snapshot) gets a NEW controller key, so
the SealedSecrets in git no longer open (their Secrets stay missing; nothing else breaks) until they are re-sealed from
the store `yyc`, which holds every value (`/infra/secrets.txt` maps each Secret key to its store key):

1. `cluster.sh create`, `cluster.sh flux` as above. Flux installs the controller (`../clusters/hetzner/sealed-secrets.yaml`);
   its first start generates the key (up to a few minutes at 100m CPU).
2. `cluster.sh cert` writes the new public cert to `/infra/sealed-secrets.pem`.
3. In a session holding the store `yyc`: `infra/seal.py --check` (every store key is in the env), then
   `infra/seal.py --all` re-seals every Secret into `apps/sealed/` and `/ai-watch/deploy/sealed/`.
4. `cluster.sh ca` writes the new cluster's CA to `/infra/yyc-cluster-ca.pem` (the deploy workflow's kubeconfig
   trusts only that CA: with the old one every `yyc-ai.yml` deploy fails TLS).
5. Commit and push (cert, CA and sealed files together). Flux applies them; `kubectl get sealedsecrets -A` shows each Synced.

Never back the controller key up out of the cluster (`kubectl get secret -l sealedsecrets.bitnami.com/sealed-secrets-key`):
that would bring back the copy the move to Sealed Secrets removed. Key renewal is off (`keyrenewperiod: 0`), so the cert
in git stays valid for the life of the cluster.

## Adding capacity

A new pool in `hetzner-k3s.yaml` (`worker_node_pools`, label `pool=apps`), then `cluster.sh create`. Take the cheapest
type per GB in stock (`GET /v1/server_types` → `locations[].available`) in fsn1: the Volumes' jobs need fsn1, and
another location's WARP exit is another colo that sites may refuse ("Nodes"). Any eu-central location (fsn1, nbg1,
hel1) can join the private network. Locations in
other network zones (ash, hil, sin) can't join the private network: they would need flannel over the public IPv6
(wireguard-native already encrypts it) and a firewall rule for the nodes' IPv6s; not built.

## Pitfalls

- A re-run of `cluster.sh create` after removing control-plane nodes rewrites the master's k3s args and restarts k3s
  there once (pods keep running; the API pauses).
- After nodes come and go, the Hetzner cloud controller can keep a stale view (it kept looking for a deleted master and
  left new nodes tainted `node.cloudprovider.kubernetes.io/uninitialized`): `kubectl -n kube-system rollout restart
  deploy/hcloud-cloud-controller-manager`.
- hetzner-k3s can hang on a download from GitHub over WARP (an open connection, no progress): stop it and run again.
- New servers may get private IPs of deleted ones; hetzner-k3s ignores host keys, other ssh needs
  `-o StrictHostKeyChecking=accept-new` with a fresh known_hosts.
- Streaming GBs through `kubectl exec` (the API) times out; copy node to node over the private network (`migrate.sh`).
- A wisers rollout needs 768 Mi free on the node of the running relay (the new pod joins it: one volume). If it stays
  Pending, make room without a gap: cordon that node, `kubectl -n yyc rollout restart deploy/poller` (the poller moves
  through its lock handover), uncordon.

- hetzner-k3s reads `HCLOUD_TOKEN` only when the config has no `hetzner_token` key at all (an empty one wins).
- A pool's nodes are named by index (`…-worker1`, `…-worker2`): with `instance_count: 1` and only worker2 left,
  hetzner-k3s keeps trying to create worker1.
- Hetzner's limits are per account: `server_shared_cores_limit` (14 shared vCPU now) refuses a server with
  `resource_limit_exceeded`; hetzner-k3s retries forever (stop it: `pkill -f "hetzner-k3s create"` on the bastion).
- hetzner-k3s's docs still show `enable_public_net_ipv4`; v2.6.0 reads `networking.public_network.ipv4` (unknown keys
  are ignored silently).

## Surge capacity (not built)

Design note (Deyao, 2026-10-20). If a roll-out or a burst ever needs temporary extra memory: create a temporary node in
the Hetzner **runner** project (not project `yyc`), join it to the YYC cluster over a WireGuard link (Hetzner private
networks don't span projects), use it only for the surge and delete it afterwards. Why the runner project: automation
should not hold the `yyc` project's API token to create and delete servers, in case it deletes the wrong thing (e.g. a
cluster node); the runner project holds nothing that matters. Not needed now: 2-replica Deployments roll with maxSurge
0 / maxUnavailable 1, and only the singletons (poller, wisers, whatsapp) need about 1.2 GiB headroom for their lock
handover. To check if built: billing separation (runner-project spend used for YYC must be attributable, EXPENSES.md),
node labels/taints so only surge pods land there, and how the k3s join token is handled.
