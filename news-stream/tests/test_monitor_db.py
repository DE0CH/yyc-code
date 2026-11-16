#!/usr/bin/env python3
"""The live dashboard's Postgres and object storage figures (monitor.database, monitor.storage; Deyao, 2026-11-05:
"add wiser status, database load, database free space, storage size (s3) to the system panel of the live monitor"),
from canned answers of the three read-only sources: the Kubernetes API, the instance's metrics exporter (Prometheus
text, as CloudNativePG 1.27 writes it) and the node's kubelet /stats/summary. No network, no database.

    python3 tests/test_monitor_db.py

1. load: client connections by state against max_connections, the longest open transaction, transactions per second
   from two reads (none on the first, none across a Postgres restart), the pod's CPU and memory against its limit.
2. space: the data volume's filesystem (the PVC's entry in the pod's volumes), database sizes largest first, WAL.
3. alerts (one Discord message when bad, one on recovery: monitor._alert): free space under PG_FREE_FLOOR; connections
   at PG_CONN_SHARE of max_connections; a WAL archive failure newer than the last archived file, no file archived for
   PG_ARCHIVE_STALE; a failed or stale (weekly) base backup.
5. backups: per cluster (pg, magpie-pg), CloudNativePG's ContinuousArchiving condition and the weekly restore test's
   ConfigMap: a failed last run, or no success for RESTORE_STALE (or none yet), is an alert; an unreadable source isn't.
4. storage: ListBuckets then HEAD per bucket, Hetzner's x-rgw-object-count / x-rgw-bytes-used summed; a bucket that
   can't be read keeps its error and the rest stand.
"""
import json, os, sys, time, unittest, urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("DATABASE_URL", "postgresql:///nonexistent")
import monitor  # noqa: E402

NOW = time.time()
POD = {"items": [{"metadata": {"name": "pg-1"}, "status": {"podIP": "10.244.9.4"},
                  "spec": {"nodeName": "yyc-pool-db-worker1", "containers": [
                      {"name": "postgres", "resources": {"limits": {"memory": "6Gi"}}}, {"name": "plugin-barman-cloud"}]}}]}
NODE = {"status": {"addresses": [{"type": "Hostname", "address": "yyc-pool-db-worker1"}, {"type": "InternalIP", "address": "10.0.0.2"}],
                   "capacity": {"cpu": "4"}}}


def summary(free=77482471424):
    return {"pods": [{"podRef": {"name": "other", "namespace": "yyc"}},
                     {"podRef": {"name": "pg-1", "namespace": "db"}, "cpu": {"usageNanoCores": 1390000000},
                      "memory": {"workingSetBytes": 4907 * 2**20},
                      "volume": [{"name": "scratch-data", "capacityBytes": 1, "usedBytes": 1, "availableBytes": 1},
                                 {"name": "pgdata", "pvcRef": {"name": "pg-1", "namespace": "db"}, "capacityBytes": 105089261568,
                                  "usedBytes": 27590012928, "availableBytes": free}]}]}


def metrics(xacts=1000, idle=103, active=7, arch_fail=NOW - 86400 * 2, arch_last=NOW - 25, since=25.0, bk=NOW - 3600 * 12,
            bk_fail=0, start=1.793065897e9):
    return "\n".join([
        "# HELP cnpg_backends_total Number of backends",
        "# TYPE cnpg_backends_total gauge",
        f'cnpg_backends_total{{application_name="yyc-news-stream",datname="app",state="idle",usename="app"}} {idle}',
        f'cnpg_backends_total{{application_name="yyc-news-stream",datname="app",state="active",usename="app"}} {active - 1}',
        'cnpg_backends_total{application_name="jarvis-egress:egress-1",datname="jarvis",state="active",usename="jarvis"} 1',
        'cnpg_backends_max_tx_duration_seconds{application_name="yyc-news-stream",datname="app",state="active",usename="app"} 95.04',
        'cnpg_backends_max_tx_duration_seconds{application_name="jarvis-egress:egress-1",datname="jarvis",state="active",usename="jarvis"} 0.006',
        "cnpg_backends_waiting_total 0",
        'cnpg_pg_settings_setting{name="max_connections"} 300',
        'cnpg_pg_settings_setting{name="archive_timeout"} 300',
        f'cnpg_pg_stat_database_xact_commit{{datname="app"}} {xacts - 10}',
        'cnpg_pg_stat_database_xact_rollback{datname="app"} 10',
        'cnpg_pg_database_size_bytes{datname="jarvis"} 1.9199667e+07',
        'cnpg_pg_database_size_bytes{datname="app"} 2.4288335539e+10',
        'cnpg_collector_pg_wal{value="size"} 3.18767104e+09',
        'cnpg_collector_pg_wal_archive_status{value="ready"} 0',
        f"cnpg_pg_stat_archiver_last_archived_time {arch_last}",
        f"cnpg_pg_stat_archiver_last_failed_time {arch_fail}",
        f"cnpg_pg_stat_archiver_seconds_since_last_archival {since}",
        "cnpg_pg_stat_archiver_failed_count 15",
        f"cnpg_pg_postmaster_start_time {start}",
        f"barman_cloud_cloudnative_pg_io_last_available_backup_timestamp {bk}",
        f"barman_cloud_cloudnative_pg_io_last_failed_backup_timestamp {bk_fail}", ""])


def cluster(archiving=True, msg="Continuous archiving is working"):
    return {"status": {"conditions": [{"type": "Ready", "status": "True"},
                                      {"type": "ContinuousArchiving", "status": "True" if archiving else "False", "message": msg}]}}


def restore_cm(*runs):
    """ConfigMap restore-test-<cluster> as db/restore-test.yaml writes it: history.jsonl, one run per line"""
    return {"data": {"last.json": json.dumps(runs[-1]), "history.jsonl": "\n".join(json.dumps(r) for r in runs)}}


def run(days_ago, ok=True, why=""):
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - days_ago * 86400))
    return {"at": at, "ok": ok, "restore_seconds": 312, "heartbeat_gap_seconds": 41, "why": why}


class Database(unittest.TestCase):
    def run_db(self, free=77482471424, clusters=None, tests=None, offsite_hours=1, **m):
        alerts = {}
        ts = lambda h: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - h * 3600))  # noqa: E731
        offsite = {"metadata": {"creationTimestamp": ts(48)}, "status": {"lastSuccessfulTime": ts(offsite_hours)}}
        clusters = clusters or {}
        tests = tests if tests is not None else {**{c: restore_cm(run(2)) for c in monitor.PG_CLUSTERS}, "dumps": restore_cm(run(0.5))}

        def k8s(p):
            if "/pods" in p:
                return POD
            if "/clusters/" in p:
                return clusters.get(p.rsplit("/", 1)[1]) or cluster()
            if "/cronjobs/pg-offsite-copy" in p:
                return offsite
            if "/configmaps/dump-load-test" in p:
                if "dumps" not in tests:
                    raise urllib.error.HTTPError(p, 404, "Not Found", {}, None)
                return tests["dumps"]
            if "/configmaps/restore-test-" in p:
                c = p.rsplit("/restore-test-", 1)[1]
                if c not in tests:
                    raise urllib.error.HTTPError(p, 404, "Not Found", {}, None)
                return tests[c]
            return NODE
        with mock.patch.object(monitor, "k8s", k8s), \
                mock.patch.object(monitor, "kubelet", lambda ip, p: self.assertEqual((ip, p), ("10.0.0.2", "/stats/summary")) or summary(free)), \
                mock.patch.object(monitor, "exporter", lambda ip: self.assertEqual(ip, "10.244.9.4") or metrics(**m)), \
                mock.patch.object(monitor, "_alert", lambda key, bad, since, a, b: alerts.__setitem__(key, (bad, a))):
            return monitor.database(), alerts

    def setUp(self):
        monitor._pg_prev.clear()

    def test_1_load(self):
        d, _ = self.run_db()
        self.assertEqual(d["connections"], {"total": 110, "max": 300, "by_state": {"idle": 103, "active": 7}, "waiting_on_locks": 0})
        self.assertEqual(d["longest_transaction"], {"seconds": 95.0, "database": "app", "user": "app",
                                                    "application": "yyc-news-stream", "state": "active"})
        self.assertIsNone(d["tps"])  # the first read: nothing to compare with
        monitor._pg_prev["at"] -= 60
        d, _ = self.run_db(xacts=4000)
        self.assertEqual(d["tps"], 50.0)
        d, _ = self.run_db(xacts=10, start=1.8e9)  # Postgres restarted: its counters started again
        self.assertIsNone(d["tps"])
        self.assertEqual((d["cpu_cores"], d["node_cores"], d["memory_mib"], d["memory_limit_mib"]), (1.39, 4, 4907, 6144))
        self.assertEqual((d["pod"], d["node"]), ("pg-1", "yyc-pool-db-worker1"))

    def test_2_space(self):
        d, _ = self.run_db()
        self.assertEqual(d["space"], {"pvc": "pg-1", "capacity_bytes": 105089261568, "used_bytes": 27590012928,
                                      "free_bytes": 77482471424, "floor_bytes": 20 * 2**30})
        self.assertEqual([x["name"] for x in d["databases"]], ["app", "jarvis"])
        self.assertEqual(d["wal_bytes"], 3187671040)

    def test_3_alerts(self):
        _, a = self.run_db()
        self.assertEqual({k: v[0] for k, v in a.items()}, {"db:space": False, "db:connections": False, "db:archive": False,
                                                          "db:archiving:pg": False, "db:archiving:magpie-pg": False,
                                                          "db:restoretest:pg": False, "db:restoretest:magpie-pg": False,
                                                          "db:dumpload": False, "db:offsite": False})
        _, a = self.run_db(free=19 * 2**30)
        self.assertTrue(a["db:space"][0])
        self.assertIn("19.0 GiB free", a["db:space"][1])
        _, a = self.run_db(idle=233, active=7)  # 240 of 300
        self.assertTrue(a["db:connections"][0])
        self.assertIn("240 of 300 connections", a["db:connections"][1])
        _, a = self.run_db(arch_fail=NOW - 60, arch_last=NOW - 600, since=600.0)
        self.assertTrue(a["db:archive"][0])
        self.assertIn("archiving a WAL file failed", a["db:archive"][1])
        _, a = self.run_db(since=float(monitor.PG_ARCHIVE_STALE + 60), arch_last=NOW - monitor.PG_ARCHIVE_STALE - 60)
        self.assertIn("no WAL file archived for 31 min", a["db:archive"][1])
        _, a = self.run_db(bk=NOW - 6 * 86400)  # weekly base backups: six days old is fine
        self.assertFalse(a["db:archive"][0])
        _, a = self.run_db(bk=NOW - 8 * 86400)
        self.assertIn("no base backup since", a["db:archive"][1])
        d, a = self.run_db(bk_fail=NOW - 60)
        self.assertIn("the base backup failed", a["db:archive"][1])
        self.assertEqual(d["archive"]["problems"], [a["db:archive"][1].split(": ", 1)[1].split(". Point-in-time")[0]])


    def test_5_backups(self):
        """WAL archiving per cluster (CloudNativePG's condition) and the weekly restore tests (db/restore-test.yaml)"""
        d, a = self.run_db(clusters={"magpie-pg": cluster(False, "unexpected failure invoking barman-cloud-wal-archive")})
        self.assertTrue(a["db:archiving:magpie-pg"][0])
        self.assertIn("barman-cloud-wal-archive", a["db:archiving:magpie-pg"][1])
        self.assertFalse(a["db:archiving:pg"][0])
        self.assertEqual(d["backups"]["pg"]["restore_test"]["restore_seconds"], 312)
        # the last run failed: its reason in the message
        d, a = self.run_db(tests={"pg": restore_cm(run(9), run(2, False, "heartbeat 900 s before the target (want 0-180)")),
                                  "magpie-pg": restore_cm(run(2))})
        self.assertTrue(a["db:restoretest:pg"][0])
        self.assertIn("failed: heartbeat 900 s", a["db:restoretest:pg"][1])
        self.assertFalse(a["db:restoretest:magpie-pg"][0])
        # passed, but over 8 days ago; and never run (no ConfigMap): both stale
        d, a = self.run_db(tests={"pg": restore_cm(run(9)), })
        self.assertTrue(a["db:restoretest:pg"][0])
        self.assertIn("has not succeeded since", a["db:restoretest:pg"][1])
        self.assertTrue(a["db:restoretest:magpie-pg"][0])
        self.assertIsNone(d["backups"]["magpie-pg"]["restore_test"])
        # the daily dump load: a failed run, then three days without a pass
        _, a = self.run_db(tests={**{c: restore_cm(run(2)) for c in monitor.PG_CLUSTERS},
                                  "dumps": restore_cm(run(1), run(0.5, False, "app: pg_restore: error"))})
        self.assertIn("failed: app: pg_restore", a["db:dumpload"][1])
        _, a = self.run_db(tests={**{c: restore_cm(run(2)) for c in monitor.PG_CLUSTERS}, "dumps": restore_cm(run(3))})
        self.assertTrue(a["db:dumpload"][0])
        # the R2 copy: four hours since its last success
        _, a = self.run_db(offsite_hours=4)
        self.assertTrue(a["db:offsite"][0])
        self.assertIn("off-site copy to R2", a["db:offsite"][1])
        # a source that can't be read: kept as an error, no alert either way
        def boom(p):
            raise urllib.error.HTTPError(p, 403, "Forbidden", {}, None)
        with mock.patch.object(monitor, "_alert", lambda *x: self.fail("no alert on an unreadable source")):
            with mock.patch.object(monitor, "k8s", boom):
                out = monitor.backups(monitor.now())
        self.assertIn("error", out["pg"])


class Storage(unittest.TestCase):
    def test_4_buckets(self):
        class R:
            def __init__(self, body=b"", headers=None):
                self.body, self.headers = body, headers or {}

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        stats = {"yyc-pg-pitr": (8194, 159413804689), "yyc-ai-files": (927, 592411901)}

        def opener(self, method, key="", params=None, headers=None, data=None, timeout=60):
            if not self.bucket:
                self_.assertEqual(method, "GET")
                return R(b"<ListAllMyBucketsResult><Buckets>" + b"".join(
                    f"<Bucket><Name>{n}</Name></Bucket>".encode() for n in ["yyc-ai-files", "yyc-elsewhere", "yyc-pg-pitr"]) + b"</Buckets></ListAllMyBucketsResult>")
            self_.assertEqual(method, "HEAD")
            if self.bucket not in stats:
                raise OSError("301 Moved Permanently")
            n, b = stats[self.bucket]
            return R(headers={"x-rgw-object-count": str(n), "x-rgw-bytes-used": str(b)})
        self_ = self
        import s3
        with mock.patch.object(s3.Client, "_open", opener), \
                mock.patch.dict(os.environ, {"VIDEO_S3_ACCESS_KEY": "AK", "VIDEO_S3_SECRET_KEY": "SK"}):
            d = monitor.storage()
        self.assertEqual([b["name"] for b in d["buckets"]], ["yyc-pg-pitr", "yyc-ai-files", "yyc-elsewhere"])
        self.assertEqual((d["bytes"], d["objects"]), (159413804689 + 592411901, 8194 + 927))
        self.assertIn("301", d["buckets"][2]["error"])
        with mock.patch.dict(os.environ, {"VIDEO_S3_ACCESS_KEY": ""}):
            self.assertRaises(RuntimeError, monitor.storage)


class Direct(unittest.TestCase):
    def test_no_proxy(self):
        """the pod's HTTP(S)_PROXY is never used for a pod or node address (urllib ignores NO_PROXY's CIDR ranges)"""
        import urllib.request
        seen = []
        real = urllib.request.build_opener
        with mock.patch.dict(os.environ, {"http_proxy": "http://proxy.invalid:1", "HTTP_PROXY": "http://proxy.invalid:1"}), \
                mock.patch.object(urllib.request, "build_opener", lambda *hs: seen.append(hs) or real(*hs)):
            self.assertRaises(OSError, monitor.direct, urllib.request.Request("http://127.0.0.1:9/"), 2)
        self.assertTrue(any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {} for h in seen[0]))


class Prom(unittest.TestCase):
    def test_parse(self):
        ms = monitor.prom('# HELP x\nx{a="1",b="q\\"x"} 2.5e+00\ny 3\nz NaN\nbad line\n')
        self.assertEqual(ms[0], ("x", {"a": "1", "b": 'q\\"x'}, 2.5))
        self.assertEqual(ms[1], ("y", {}, 3.0))
        self.assertEqual(len(ms), 3)  # NaN parses as a float; the bad line is skipped


if __name__ == "__main__":
    unittest.main(verbosity=1)
