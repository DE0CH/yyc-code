# Moving the apps between clusters

How the apps move from one cluster to another with no gap for anything that serves. Done once (2026-10-20): from the
old single node `yyc-cluster` (overlay `clusters/legacy`, deleted with it) to the Hetzner cluster `yyc`
(`clusters/hetzner`). A later move = an overlay for the new cluster with MIGRATION STATE patches (everything off but the
serving Deployments), then `cluster/migrate.sh STEP` with FROM/TO = the two overlays (git edits of both overlays'
MIGRATION STATE, commit, push, reconcile both, check); each step is re-runnable.

## Rules

- Anything that serves (web, mcp, mailmerge, the tunnel connectors) runs on both clusters while it moves: the new
  cluster's copies are up and checked first, then the old ones stop.
- A singleton never runs twice: poller and whatsapp hand over through their Postgres locks (the new pod waits on the
  lock, the old one drains and lets go); transcribe's clips are leased; monitor has no lock, so the old one stops first.
- The WiseSearch relay holds the one WisersOne session: the old relay drains and saves it, its volume is copied, the new
  one starts on it. The only step with a gap (about a minute, plus the git push).
- A CronJob moves only between runs: the old cluster's CronJobs are suspended (a running job always finishes), their
  volumes' files are copied, then the new cluster's start. Suspends set in `apps/` by others stay as they are.
- Nothing lands on the cluster that is serving unless it fits: a dry run (`kubectl apply --dry-run=server`) of the
  overlay first, and the node's requested memory checked after.

## Steps (the 2026-10-20 move)

| Step | When (UTC) | What |
|---|---|---|
| prepare | 05:30 | the new cluster with Flux; `web`, `mcp`, `flagd`, `mailmerge` running; Service `wisers` bridged to the old relay (an EndpointSlice → the old node's NodePorts over the private network) |
| `connectors` | 05:46–05:49 | the new connectors joined `yyc-stream` and `mailmerge`, the old ones stopped (an outside request loop saw no failure caused by it) |
| `singletons` | 06:00 | poller, whatsapp, transcribe: the new ones waited on their locks, took over when the old ones drained |
| `monitor` | 06:12 | |
| `wisers` | 06:13–06:15 | the session carried over (no new login); gap about 2.5 minutes (a push race in the middle) |
| `precopy` | 08:33 | the dumps (6.2 GB) ahead, nothing stopped |
| `jobs` | 08:45–09:03 | the old CronJobs suspended, the two running jobs finished, the volumes' files copied node to node, the CronJobs on; every CronJob had a clean run by 09:50 |
| old node | 09:59 | snapshot `445820218` (21.7 GB, kept), server deleted |

## Setup

```bash
S=~/.yyc-cluster   # cluster/README.md
cd news-stream/deploy/cluster
SECRETS=$S OLD_SSH=root@<old node> FROM=<old overlay> TO=<new overlay> ./migrate.sh connectors   # … then singletons, monitor, wisers, precopy, jobs
SECRETS=$S ./verify.sh
```
