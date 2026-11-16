#!/usr/bin/env python3
"""Each CronJob run's memory peak is measured, not sampled (Deyao, 2026-10-20: "right-sized ... from measured usage"):
when a job's process exits, corpus records its container's cgroup memory.peak (Chrome under xvfb-run included) in kv
jobpeak:<cronjob> (the last PEAK_KEEP runs); the monitor's jobs panel shows each CronJob's highest recorded peak beside
its memory limit. kubectl top's 15-60 s samples miss spikes (the poller's OOM kills, 20 Oct). Only CronJob pods record
(their name <cronjob>-<minutes>-<rand>, costlog.job()).

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_job_peak.py
"""
import os, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import corpus, costlog, monitor  # noqa: E402


def peak_file(n):
    f = tempfile.NamedTemporaryFile("w", delete=False)
    f.write(f"{n}\n")
    f.close()
    return f.name


class JobPeak(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'jobpeak:%%'")
        costlog._job = None

    def tearDown(self):
        costlog._job = None

    def test_a_cronjob_pod_records_its_peak(self):
        env = {"HOSTNAME": "sites-29875633-vbm4w", "KUBERNETES_SERVICE_HOST": "10.45.0.1"}
        with mock.patch.dict(os.environ, env):
            for n in (300 << 20, 512 << 20):
                costlog._job = None
                corpus.record_peak(peak_file(n))
        runs = corpus.kv_get("jobpeak:sites")
        self.assertEqual([r["peak_mib"] for r in runs], [300, 512])

    def test_other_processes_record_nothing(self):
        with mock.patch.dict(os.environ, {"HOSTNAME": "poller-6cf45bcc4f-qnkh7", "KUBERNETES_SERVICE_HOST": "x"}):
            corpus.record_peak(peak_file(1 << 30))
        with mock.patch.dict(os.environ, {"HOSTNAME": "laptop"}):
            os.environ.pop("KUBERNETES_SERVICE_HOST", None)
            corpus.record_peak(peak_file(1 << 30))
        self.assertIsNone(corpus.kv_get("jobpeak:poller"))
        self.assertFalse(corpus.db().execute("SELECT count(*) FROM kv WHERE k LIKE 'jobpeak:%%'").fetchone()[0])

    def test_the_monitor_shows_peak_and_limit(self):
        corpus.kv_set("jobpeak:sites", [{"at": "x", "peak_mib": 159}, {"at": "y", "peak_mib": 201}])
        cj = {"metadata": {"name": "sites", "creationTimestamp": "2026-10-19T00:00:00Z"},
              "spec": {"schedule": "3-59/10 * * * *", "jobTemplate": {"spec": {"activeDeadlineSeconds": 900, "template": {
                  "spec": {"containers": [{"resources": {"limits": {"memory": "1Gi"}}}]}}}}},
              "status": {"lastSuccessfulTime": "2026-10-21T00:00:00Z"}}
        with mock.patch.object(monitor, "k8s", lambda p: {"items": [cj] if p.endswith("/cronjobs") else []}), \
                mock.patch.object(monitor, "_alert", lambda *a, **k: None):
            r = monitor.jobs()["cronjobs"][0]
        self.assertEqual((r["memory_peak_mib"], r["memory_limit_mib"]), (201, 1024))


if __name__ == "__main__":
    unittest.main()
