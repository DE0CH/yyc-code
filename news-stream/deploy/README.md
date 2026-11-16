# YYC cluster: how the news stream is deployed

The target is in `../ARCHITECTURE.md`. This folder is everything that makes it real; git is the source of truth
(Flux applies it; never edit the cluster by hand).

| Path | What |
|---|---|
| `apps/` | the cloud-agnostic base: namespaces `yyc` and `mailmerge`, PriorityClasses, quotas, the workloads, the volumes (StorageClass `yyc-block`); `sealed/` = the Secrets as SealedSecrets (applied by the fenced Kustomization `yyc-apps-secrets`); `kustomization.yaml` pins the image tag |
| `clusters/hetzner/` | the Hetzner cluster `yyc`: `apps/` + StorageClass `yyc-block` (Hetzner Volumes) + `sealed-secrets.yaml` (the Sealed Secrets controller and the fenced applier) + `flux-sync.yaml` (Kustomizations `yyc-apps` = this folder, `yyc-aiwatch`, `yyc-db`, the fenced `yyc-apps-secrets` / `yyc-aiwatch-secrets`; applied only by `cluster/cluster.sh flux`, not by Flux: an edit there reaches the cluster when that runs, and a Kustomization dropped from it stays until deleted with `kubectl -n flux-system delete kustomization NAME`) + the move's MIGRATION STATE |
| `cluster/` | the Hetzner cluster's build and operation: `hetzner-k3s.yaml`, `cluster.sh`, `node-setup.sh`, `migrate.sh`, `verify.sh` (`cluster/README.md`) |
| `MIGRATE.md` | moving the apps between clusters without a gap (the steps and where they stand) |

Another cluster (e.g. on AWS) = another folder under `clusters/` with its StorageClass `yyc-block` and its node
provisioning; `apps/` stays as it is.

## Secrets

Kept in the Jarvis 2 store `yyc`: `YYC_HETZNER_API` (project yyc, Read & Write, token "yyc-cluster"),
`YYC_CLUSTER_SSH_KEY` (root on every node; public half = Hetzner SSH key `yyc-cluster-admin`), `YYC_FLUX_DEPLOY_KEY`,
`YYC_GHCR_PULL_TOKEN`, `YYC_WARP_CONF` (the old node's WARP config; each Hetzner cluster node registers its own,
`cluster/node-setup.sh`), and every value of the cluster's Secrets (the map: `/infra/secrets.txt`).

**Sealed Secrets** (Deyao, 2026-10-21: "sealed secrets is better"; until then SOPS/age, whose key every session holding
the store `yyc` had in its env, and it leaked once). The Secrets are in git as SealedSecrets (`apps/sealed/*.yaml`,
`../../ai-watch/deploy/sealed/*.yaml`), sealed to the public cert `/infra/sealed-secrets.pem`. The only key that opens
them is the controller's (kube-system, `clusters/hetzner/sealed-secrets.yaml`): it exists only in the cluster (and its
etcd snapshots), no copy anywhere else, no store key. Lose the cluster = re-seal from the store
(`cluster/README.md` "Secrets after a rebuild"). The `sealed/` folders are applied by the FENCED Kustomizations
`yyc-apps-secrets` / `yyc-aiwatch-secrets` as ServiceAccount `flux-system/sealed-secrets-applier`, which may manage
SealedSecret objects in yyc, mailmerge, db, aiwatch and nothing else: a manifest pushed there can't create
a Role, a pod or read a Secret. Never give those Kustomizations another ServiceAccount or drop `serviceAccountName`
(without it Flux applies as cluster-admin). Residual, known: `yyc-apps` and `yyc-aiwatch` themselves still apply as
cluster-admin, so a push to the rest of `news-stream/deploy/` or `ai-watch/deploy/` could still mount or read any Secret
(as it could under SOPS); fencing those too is a separate change.

The Secrets (namespace `yyc` unless named; store keys in `/infra/secrets.txt`):
`app-env` (the old server's `/etc/yyc-stream/app.env`: OPENROUTER_API, RESI_PROXY (the bare login of YYC's one IPRoyal
sub-user, `http://yycstream:<password>@geo.iproyal.com:12321`, no suffix: resi.py adds country and session per request;
every proxied job uses it), TIKHUB_API, CAPSOLVER_API
(Deyao's own solver account; also in `monitor-keys` for the balance), TWOCAPTCHA_API (YYC's own 2Captcha account,
YYC_TWOCAPTCHA_API in the store `yyc`; never Deyao's personal one; also in `wisers` and `monitor-keys`),
DISCORD_WEBHOOK (notify.py: the `yyc-alerts` channel's webhook in Deyao's Discord server); add ANYAPI_API_KEY here when it exists), `tunnel` (the `yyc-stream` tunnel token),
`monitor-keys` (the monitor pod's alone: IPROYAL_API, CAPSOLVER_API, and
OPENROUTER_MANAGEMENT_KEY = OpenRouter management key `yyc-monitor`, also `YYC_OPENROUTER_MANAGEMENT_KEY` in the store
`yyc`, which reads the account's credit; HETZNER_YYC_READ = Hetzner token `yyc-monitor` of project yyc, Read only
(`YYC_HETZNER_READ` in the store); CLOUDFLARE_YYC_MONITOR_TOKEN = Cloudflare token `yyc-monitor` on account YYC only,
Account Analytics Read + Billing Read (`YYC_CF_MONITOR_TOKEN` in the store), with CLOUDFLARE_YYC_ACCOUNT_ID; never in
`app-env`),
`db-app` (key `uri` = YYC_DB_APP_URI: role `app` on YYC's Postgres, Service `pg-rw.db`; every workload's `DATABASE_URL`),
in namespace `db`: `pg-role-*` (each Postgres role's password, YYC_PG_*_PASSWORD; `yyc_edge`'s is also in the Hyperdrive
config `yyc-pg`) and `pg-backup-s3` (the bucket yyc-pg-backups' S3 credential `yyc-ai`),
`mail-account` (the old server's `/etc/yyc-stream/mail-account`, not mounted by any workload), `ghcr-pull` (classic
GitHub token `yyc-cluster-ghcr-pull`, read:packages only, in namespace `yyc`: the image holds private code, so the
package stays private), `telegram`, `wisers`, `ai-tools`, `isp-proxy`, `mailmerge/tunnel`.

Change a secret: put the new value into the store first (`jarvis2 put-key yyc KEY --explain … < value`; a new key
needs its explanation), then `infra/seal.py NS/NAME` (values from the env; a value not yet refreshed into this
session's env: `--stdin KEY`), commit, push. A new Secret: also a line in `/infra/secrets.txt` (seal.py adds the file
to `sealed/kustomization.yaml`). Nothing in git can be decrypted outside the cluster; to see a value, read the store.

## The image

`news-stream/Dockerfile` (Ubuntu 24.04, Python venv with the old server's pinned packages, Google Chrome for
Patchright's `channel="chrome"`, Xvfb, fonts-noto-cjk; user 10001; code in `/app`). `.github/workflows/news-stream-image.yml`
builds `ghcr.io/de0ch/yyc-news-stream:<12-char sha>` on every push touching `news-stream/` (not `deploy/` or `*.md`),
checks the imports and Chrome, and commits the tag into `apps/kustomization.yaml` (`[skip ci]`; never moves the pin
backwards, retries on a push race, no force-push). Flux sees the commit within a minute and rolls the Deployments.

## Workloads (namespace `yyc`)

- `web` (Deployment, 2 replicas): the AI server, `gunicorn app:application` (web.py: POST /app/ai/*, GET /api/ai/estimate, /app/api/v1/*, /app/login, /healthz; the site's pages are the
  edge app's, `../web-app/`); RollingUpdate one pod at a time (maxSurge 0 / maxUnavailable 1: the other keeps serving), readiness `/healthz` every 3 s, preStop
  `sleep 10` then gunicorn drains (graceful 300 s, grace period 330 s), PDB minAvailable 1. Service `web` :80.
- `poller` (Deployment, 1 replica, RollingUpdate maxSurge 1 / maxUnavailable 0): `python app.py`; the new pod waits on
  the Postgres poller lock while the old one finishes its poll and the scoring in hand and lets go only when it exits
  (its lock heartbeat runs through the drain; grace 1500 s, the drain's bound in poller.yaml).
- `wisers` (Deployment, 1 replica, RollingUpdate maxSurge 1 / maxUnavailable 0, grace 90 s): the WiseSearch relay
  (`../wisers/relay.py`), behind the tunnel's `^/app/wisers` ingress (yyc-edge sends it only `/app/wisers/api/`). Its
  state (the session, jobs, saved responses, documents, images, downloads, logs) is in Postgres (`../wisers/store.py`),
  so it runs on any node; the leader lock (a Postgres advisory lock) hands the WisersOne session over without a second
  login (`../README.md` "WiseSearch 轉接"). PDB maxUnavailable 0. Readiness `/healthz` (503 while draining), liveness
  `/livez`.
- `cloudflared` (Deployment, 2 replicas, rolled one at a time, PDB minAvailable 1): the `yyc-stream` tunnel's connector. The tunnel's ingress (deploy/cloudflare/tofu/tunnels.tf) sends the
  host to `http://127.0.0.1:8080`, so a socat sidecar listens there and forwards to Service `web`.
- CronJobs (`cronjobs.yaml`), one per former timer, same command, schedule (UTC), deadline and memory:
  browser :14/:44, facebook :00/:30, cache :07/:37, comments :15/:45, replies every 5 min from :02 (the whole 香港討論區 site), lihkg every 5 min, instagram :04, tikhub :06,
  fulltext :22/:52 (new, not a former timer: the article text of every news post, for 搜尋),
  paywall :17/:47 (paywall.py archive: paywalled posts' text from archive.today, under xvfb-run for its captcha),
  reports :05 (TZ Asia/Hong_Kong), uptime every minute. All `concurrencyPolicy: Forbid`. The browser jobs run under
  `xvfb-run` with a 1 GiB `/dev/shm`. comments also runs under xvfb-run (its Facebook session refresh opens a headed
  Chrome; the old unit lacked it).
  Every xvfb-run job starts as `sh -c 'xvfb-run …; exit $?'`: as the container's PID 1, xvfb-run never receives
  Xvfb's ready signal and hangs until the Job deadline (all of them did, 2026-10-13 to 2026-10-14).
- The database is YYC's own Postgres (`db/postgres.yaml`, Flux Kustomization `yyc-db`; `../ARCHITECTURE.md` "Data"):
  every workload gets `DATABASE_URL` from Secret `db-app` (key `uri`, role `app`, Service `pg-rw.db`). Backups: `db/pg-dump-daily.yaml` (a dump of each database
  every day, into the locked bucket yyc-pg-backups) and the WAL archive into yyc-pg-pitr (`../ARCHITECTURE.md` "Data").

- `monitor` (Deployment): computes Deyao's monitor and publishes it into kv `monitor:data` (monitor.py); no Service,
  NetworkPolicy `monitor-no-ingress` admits nothing, its probes run inside the pod (exec).

State: none on volumes for the jobs (Deyao, 2026-11-08: state in the application). Chrome's cookies are in Postgres
(chrome_state.py: each run starts on a fresh folder and saves back), the uptime probe's failures too (uptime_log.py), so
any job runs on any node with room; tests/test_rollouts.py fails a CronJob that mounts a volume or follows another pod.
Every pod has a scratch emptyDir at `/var/lib/yyc/data`. No Deployment or job has a volume: the WiseSearch relay's state
is in Postgres too (wisers/store.py, 2026-11-09).

## Sizes, priorities, quotas

- **Every container sets CPU, memory and ephemeral-storage requests and limits.** Memory requests are the measured use
  with headroom (Deyao, 2026-11-08: "cut now from today's numbers", so the servers can be packed): services from the
  kubelets' working set that day, at least their recorded peaks (transcribe 384, peak 318 on 20 Oct); CronJobs at 1.25 x
  the highest of their last 20 runs' memory.peak (kv jobpeak:<cronjob>), at least 128 Mi (fulltext 1280: 1212 on 21 Oct).
  Limits keep the earlier ceilings (poller 2048: OOM-killed 4 times at 1280; Chrome OOM-killed at 1500 Mi), so a pod
  above its request uses spare room on its node; a node whose pods all peak at once can run out of memory (the kubelet
  then evicts the pods furthest above their requests, `batch` before `serving`).
  CPU requests at about half the measured peak (21 Oct: poller 600m, monitor 200m, the browser jobs, sites and
  fulltext 300m, forums 200m), so the scheduler spreads the CPU-heavy jobs over the 2-vCPU nodes; quota 14 CPUs.
  Each CronJob run records its memory peak (cgroup memory.peak, kv jobpeak:<cronjob>), shown on the monitor beside
  the limit: resize from those.
  emptyDirs have a sizeLimit (scratch data dirs 512 Mi, /dev/shm 1 Gi).
- **Never run a command inside a serving pod** (its memory is the pod's: the monitor and the poller were OOM-killed that
  way on 2026-10-20): `cluster/run.sh [--memory 1Gi] [--secrets monitor-keys] [--stdin-env NAME] -- CMD …` runs it in a
  pod of its own (the current image, the database, app-env), prints its stdout and stderr and exits with its code
  (`infra/cost-profile.sh`, `infra/control.py`, `infra/thresholds.py` use it).
- **Moving pods off a node** (a node move, a resize): `cluster/drain.sh NODE` cordons it, waits for its running Jobs,
  moves every Deployment there by its own rolling update (singletons start the new pod elsewhere first and hand over
  through their locks), then `kubectl drain`s the rest. The singletons poller, transcribe, whatsapp, monitor and wisers have
  PodDisruptionBudgets of maxUnavailable 0, so a bare `kubectl drain` waits on them instead of cutting them;
  Deployments of 2 replicas (web, cloudflared, yyc-mcp, flagd, mailmerge) have minAvailable 1 and spread over nodes
  (topologySpreadConstraints).
- **Stale jobs** (monitor.py `jobs`): a CronJob is late after 3 of its intervals plus its deadline without a success, a
  Deployment down after 10 minutes with fewer ready pods than replicas; both on the monitor and to Deyao on Discord
  (notify.deyao: once when it starts, once when it ends; tests/test_monitor_late.py).
- **The monitor's Postgres figures** (monitor.py `database`; the live dashboard's Systems panel, /app/live; Deyao,
  2026-11-05): load (client connections by state of max_connections, transactions per second, the longest open
  transaction, the pod's CPU and memory), space (the data volume's filesystem as the instance sees it, database sizes,
  WAL) and backups (WAL archiving, the nightly base backup), read-only from the instance's CloudNativePG metrics exporter
  (`http://<pod>:9187/metrics`, its role `cnpg_metrics_exporter`, member of `pg_monitor`), the node's kubelet
  (`/stats/summary`, ClusterRole `yyc-monitor-nodes-read`: `get nodes/stats`) and Role `monitor-db-read` (namespace db,
  pods get/list). The exporter's role needs CONNECT on database `app` (closed to PUBLIC; without it every default query
  failed with "permission denied for database app"): granted by hand as postgres on 2026-11-05 (`GRANT CONNECT ON
  DATABASE app TO cnpg_metrics_exporter`; check: `SELECT datacl FROM pg_database WHERE datname = 'app'`; a database
  restored from a dump needs it again). Alerts to Deyao (monitor._alert, once bad, once recovered): under 20 GiB free on
  the volume (`db:space`; more than max_wal_size), connections at 80% of max_connections (`db:connections`), a WAL
  archive failure newer than the last archived file, no file archived for 30 min, a failed base backup or none for 26 h
  (`db:archive`). Object storage (monitor.py `storage`, every 30 min): ListBuckets + HEAD per bucket, whose answer carries
  Hetzner's own bucket stats (`x-rgw-object-count`, `x-rgw-bytes-used`), with the project-scoped S3 credential
  `yyc-videos` (Secret video-s3: GET/HEAD only in the code; Hetzner's S3 credentials have no read-only kind).
  tests/test_monitor_db.py.
- **Lock connections** (corpus.lock_connect): the server ends one idle for 90 s (idle_session_timeout) and every holder
  speaks every 15 s, so a holder whose network path dies silently frees its lock within 90 s; every connection has
  tcp_user_timeout 30 s (20 Oct 14:09 UTC: ghost sessions held the whatsapp, transcribe, tikhub and 'yyc youtube' locks
  for nine hours; tests/test_lock_ghost.py).
- **Rollouts:** Deployments of 2 replicas replace one pod at a time (maxSurge 0 / maxUnavailable 1: the other keeps
  serving, no extra memory; Deyao, 2026-10-20), except yyc-mcp (maxSurge 1 / maxUnavailable 0: its manifest lands
  before the workflow pins the image built for it, so both old pods keep serving until a new one is ready); singletons start the new pod first (maxSurge 1 / maxUnavailable 0) and
  hand over through their locks. `tests/test_rollouts.py` checks both. The poller's new pod needs 2 Gi free on a node: when none has it, the
  new pod waits Pending (the old one keeps polling, no gap) and, being `serving`, takes the first room a finishing job
  frees.
- **PriorityClasses** (`apps/priorityclasses.yaml`): `serving` (100000: Deployments, YYC AI's pods) above `batch`
  (1000: CronJobs). Neither preempts (`preemptionPolicy: Never`): a rollout's extra pod never evicts a running job.
- **ResourceQuota + LimitRange** in `yyc` (24 Gi memory, 80 pods, 6 PVCs) and `mailmerge` (1 Gi, no PVCs)
  (`apps/limits.yaml`); a container without its own sizes gets the LimitRange's defaults.
- **Kubelet** (every node): pid limit, kube/system reserved, eviction thresholds (`cluster/README.md`).

## Cloudflare (tofu)

YYC's own Cloudflare account (`YYC`) is code (Deyao, 2026-10-21): `../deploy/cloudflare/tofu/*.tf` (OpenTofu, Cloudflare
provider 5.28.1), applied by the GitHub workflow `.github/workflows/cloudflare.yml` (Deyao, 2026-10-28: GitOps runs in
workflows; Flux tofu-controller before): every push touching it
plans and applies (a change of the access catalogue, the Actions secret `YYC_CATALOGUE_JSON`, with a dispatch or the next run), and every 30 minutes a run reverts dashboard drift; runs never overlap. Secrets (Actions, from the
store `yyc`): `YYC_CF_ACCESS_EDGE_TOKEN`, the Google sign-in client `YYC_CF_GOOGLE_CLIENT_*` (Google Cloud project
`yyc-access-login`), the Microsoft sign-in client `YYC_ENTRA_CLIENT_ID/_SECRET/_TENANT_ID` (Entra app
"Cloudflare Access (YYC staff sign-in)" in YYC's Microsoft 365 tenant, secret expires 2028-11-01), `YYC_TOFU_R2_*`, `YYC_DISCORD_WEBHOOK`, `YYC_CATALOGUE_JSON` (the access catalogue, `/infra/catalogue.sh`); set with `gh secret set` and the token `YYC_CI_SECRETS_TOKEN`.
State: R2 bucket `yyc-tofu-state` on YYC's account (backend in `tofu/main.tf`, lockfile locking; token `yyc-tofu-state`).
Outputs (tunnel tokens, service token, Access audiences): `tofu output` there with the R2 key; the tokens are copied into
the store `yyc` (`YYC_CF_*_TUNNEL_TOKEN`, `YYC_MCP_OIDC_CLIENT_*`) and sealed from there. A failed run, and the first
good one after it, go to Discord; runs: https://github.com/DE0CH/yyc-code/actions/workflows/cloudflare.yml (re-run there, or
`gh workflow run cloudflare.yml`). Before a push that deletes or replaces something, plan against the live state
locally (`AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY` = the R2 key, `TF_VAR_*` as in the workflow, `tofu plan`).

What it holds: the Access organisation (team `yyc-hk.cloudflareaccess.com`), the sign-in methods (an e-mailed code, Google, Microsoft; the code was off 2026-11-03 to 2026-11-05),
the Access apps and their reusable allow policies (among them the OIDC app `YYC MCP sign-in`, yyc-mcp's sign-in step;
outputs `mcp_oidc_*` → store yyc `YYC_MCP_OIDC_CLIENT_*`), the tunnels `yyc-stream`,
`mailmerge`, `yyc-ai` and their ingress, the zone `yyc-internal.com`'s records, and Cloudflare for SaaS: the custom
hostnames below (fallback origin `stream.yyc-internal.com` = tunnel yyc-stream; mailmerge and ai have their own origin
records), and the routes of the Worker `yyc-edge` (`../edge`, `deploy.sh`) for yyc and yyc-beta.
Done once in the dashboard, not code: the domain (moved from Deyao's account 2026-10-23), the zone, Zero Trust Free and
Cloudflare for SaaS (USD 0).

**Deyao's five records (outside git).** These names live in Deyao's own zone `deyaochen.com` (his account) and change
only by hand; each is a DNS-only CNAME to `customers.yyc-internal.com`, which makes it a custom hostname of YYC's SaaS
zone (certificate, Access, Worker, tunnel all on YYC's account):

| Record | Serves |
|---|---|
| `newswatch.yyc-internal.com` | the news stream (Worker yyc-edge → yyc-web / tunnel yyc-stream) |
| `yyc-beta.deyaochen.com` | the beta site (same) |
| `mailmerge.deyaochen.com` | Mail Merge (tunnel mailmerge) |
| `ai.yyc.deyaochen.com` | 301 to YYC AI at `ai.yyc-internal.com` (Worker yyc-edge) |

The monitor's Cloudflare panel checks them every 5 minutes by public DNS (no token) and Discords Deyao when one stops
pointing there. **New YYC addresses go on `yyc-internal.com` through tofu** (Deyao, 2026-10-23), never as new
deyaochen.com records. Adding a custom hostname anyway: certificates validate by HTTP once the record points here
(`ssl.method = "http"`); a name that is still on Cloudflare elsewhere can't pre-validate the hostname, only the
certificate (by TXT), and turns active seconds after its record changes (2026-10-23: 7-24 s).

## Namespace `mailmerge`

YYC's mail merge app (public repo DE0CH/mailmerge; Deyao, 2026-10-20: "Its yyc's actually"). Its users differ from
the news stream's, so it has its own namespace, tunnel and login gate (CLAUDE.md "Hosting"). `apps/mailmerge.yaml`: Deployment `mailmerge` (2 replicas, rolled one pod at a time, readiness
`/healthz`, PDB, image pinned by digest; deploy a new version = bump `sha-<7>@sha256:…` to a build of the repo's `image`
workflow and push), Service `mailmerge` :80, Deployment `cloudflared` (2 replicas) for the tunnel `mailmerge`
(on YYC's Cloudflare account, id df61ce84-2e17-4a2e-b3bc-b812e683fd10; token in the SealedSecret `sealed/mailmerge-tunnel.yaml`, source
YYC_CF_MAILMERGE_TUNNEL_TOKEN in the store `yyc`, an output of deploy/cloudflare). NetworkPolicies: ingress only from pods of the namespace; egress only
to the cluster DNS and to public addresses (SMTP 587 to the users' mail servers, the Cloudflare edge), never to
pods, Services, the nodes (10.0.0.0/8 covers the private network) or link-local; `cluster/verify.sh` checks both
directions. Stateless: what a user enters stays in their browser.

Cloudflare (YYC's account, as code: "Cloudflare (tofu)" above): the tunnel's ingress `mailmerge.deyaochen.com` →
`http://mailmerge.mailmerge.svc.cluster.local:80`; the custom hostname `mailmerge.deyaochen.com` with origin
`mailmerge.yyc-internal.com` (the tunnel); the Access app `YYC Mail Merge` (shown in the App Launcher; sign-in Google or
Microsoft; YYC's users, the one access catalogue, `/yyc-ai/README.md` "The staff allowlist"). Deyao's record `mailmerge.deyaochen.com` is a DNS-only CNAME to `customers.yyc-internal.com`.

## Operating

```bash
S=~/.yyc-cluster                                   # the keys from the store yyc (cluster/README.md)
c() { SECRETS=$S news-stream/deploy/cluster/cluster.sh ssh "$@"; }   # kubectl/flux on yyc-master1
c kubectl get pods -A -o wide
c flux reconcile kustomization yyc-apps --with-source   # apply main now
c kubectl -n yyc logs deploy/poller --tail 50
c kubectl -n yyc rollout restart deploy/web
SECRETS=$S news-stream/deploy/cluster/verify.sh          # everything, through Cloudflare too
```
