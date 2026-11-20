# pg-heartbeat

The heartbeat of YYC's two Postgres clusters on yyc-talos (Cluster `pg`, database `app`; Cluster `magpie-pg`,
database `magpie`), and the watch that replaces a primary that stopped committing. Deployment `db/pg-heartbeat`
(`news-stream/deploy/clusters/talos/db/heartbeat.yaml`), image `ghcr.io/de0ch/pg-heartbeat` built by
`.github/workflows/pg-heartbeat-image.yml`.

Every 10 s per cluster, on a new connection to `<cluster>-rw`: one row, `ops.heartbeat (src, at)`, set to `now()`.

- **Restore tests** read it: a restore to time T must show `ops.heartbeat.at` shortly before T (`../news-stream/deploy/clusters/talos/db/restore-test.yaml`);
  a standby's copy of the row shows how far behind it is.
- **The watch** (`src/watch.rs`, a pure rule with its tests): a primary that answers a read on a new connection
  but has not committed the heartbeat for 30 s has its Pod deleted, when the Cluster has another ready instance,
  at most once per 10 minutes. Nothing else counts: a write that fails with an error, or a database that cannot be
  reached at all, is a failover or switchover under way, or this Pod's own network, which the operator handles.
  The Cluster's `stopDelay: 60` (the Pod's grace period) has the kubelet kill a shutdown that waits on the same
  stall, and a standby takes over; synchronous replication loses nothing acknowledged.

Why (2026-11-20 08:28-08:46 UTC): pg-1's storage stalled. Reads went on, every commit waited (Postgres does not
apply `statement_timeout` to a commit's wait), CloudNativePG's probes kept the primary Ready and both standbys
connected, and its pre-shutdown `CHECKPOINT` waited on the same storage under a 1800 s grace: YYC AI, the news stream
and every other user of Cluster pg had no writes for 18 minutes though two healthy standbys stood by.

`tests/clusters.rs` pins the Clusters' side: `stopDelay` ≤ 60 s, `smartShutdownTimeout` ≤ 15 s and below it, and
`tcp_user_timeout` ≤ 15 s (a session whose client is gone lets go of its locks in seconds, not TCP's 15 minutes).

    pg-heartbeat watch CLUSTER:DATABASE:USER:PASSWORD_ENV...   the Deployment's command
    pg-heartbeat probe URL                                     one beat: committed | stalled | unreachable

The image workflow tests `probe` against a real Postgres whose commits never return (`synchronous_standby_names`
naming a standby that never connects), a healthy one and a stopped one.
