#!/usr/bin/env python3
"""Stale jobs (Deyao, 2026-10-20: "make them more resilient"): a CronJob is late when it has not succeeded for
LATE_FACTOR times its own interval (from its schedule) plus its deadline, and a Deployment is down when fewer pods
are ready than it asks for, for DOWN_AFTER seconds. Both show on the monitor's jobs panel and reach Deyao on Discord
(notify.deyao) once when they start, once when they end; a suspended CronJob, a 0-replica Deployment and a workload
whose pipeline flag is off are never late or down. Before, `late` meant 3 h without success for any hourly schedule,
so the every-5-minutes jobs failing for nine hours (the ghost locks, 2026-10-20) showed late but told no one.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_monitor_late.py
"""
import os, sys, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import corpus  # noqa: E402

os.environ.pop("DISCORD_WEBHOOK", None)
import monitor, notify  # noqa: E402

T0 = datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)


def z(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def cron(name, schedule, ok_minutes_ago, suspend=False, deadline=600, created_hours_ago=48):
    st = {"lastSuccessfulTime": z(T0 - timedelta(minutes=ok_minutes_ago))} if ok_minutes_ago is not None else {}
    return {"metadata": {"name": name, "creationTimestamp": z(T0 - timedelta(hours=created_hours_ago))},
            "spec": {"schedule": schedule, "suspend": suspend,
                     "jobTemplate": {"spec": {"activeDeadlineSeconds": deadline}}}, "status": st}


def dep(name, replicas, ready):
    return {"metadata": {"name": name}, "spec": {"replicas": replicas, "template": {"spec": {"containers": [{"image": "x"}]}}},
            "status": {"readyReplicas": ready}}


class Interval(unittest.TestCase):
    def test_interval_from_schedule(self):
        for s, minutes in [("*/5 * * * *", 5), ("2-59/5 * * * *", 5), ("14,44 * * * *", 30), ("0,15,30,45 * * * *", 15),
                           ("6 * * * *", 60), ("* * * * *", 1), ("30 19 * * *", 1440), ("9-59/15 * * * *", 15),
                           ("3-59/10 * * * *", 10)]:
            self.assertEqual(monitor.interval_minutes(s), minutes, s)


class Late(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'monitor:alert%%'")
        self.sent = []
        self.cjs, self.deps = [], []
        self.orig = monitor.k8s, monitor.now, notify.deyao, monitor._flag_off
        monitor.k8s = lambda path: {"items": self.cjs if path.endswith("/cronjobs") else
                                    self.deps if path.endswith("/deployments") else []}
        self.t = T0
        monitor.now = lambda: self.t
        notify.deyao = lambda text: self.sent.append(text) or True
        monitor._flag_off = lambda kind, name: "off by flag" if name == "broadcast" else None

    def tearDown(self):
        monitor.k8s, monitor.now, notify.deyao, monitor._flag_off = self.orig

    def rows(self):
        return {r["name"]: r for r in monitor.jobs()["cronjobs"]}

    def test_late_is_a_multiple_of_the_jobs_own_interval(self):
        self.cjs = [cron("replies", "2-59/5 * * * *", 20), cron("forums", "4-59/5 * * * *", 30),
                    cron("tikhub", "6 * * * *", 150, deadline=3300), cron("tikhub2", "6 * * * *", 250, deadline=3300),
                    cron("pgdump", "30 19 * * *", 23 * 60), cron("comments", "15,45 * * * *", 600, suspend=True),
                    cron("broadcast", "20 * * * *", 900)]
        r = self.rows()
        self.assertFalse(r["replies"]["late"])       # 20 min < 3 × 5 min + 10 min deadline
        self.assertTrue(r["forums"]["late"])         # 30 min > 25
        self.assertFalse(r["tikhub"]["late"])        # 150 min < 3 h + 55 min
        self.assertTrue(r["tikhub2"]["late"])
        self.assertFalse(r["pgdump"]["late"])
        self.assertIsNone(r["comments"]["late"])
        self.assertIsNone(r["broadcast"]["late"])
        self.assertEqual(r["forums"]["late_after_minutes"], 25)

    def test_a_new_cronjob_is_not_late_before_its_first_chance(self):
        self.cjs = [cron("sites", "3-59/10 * * * *", None, created_hours_ago=0.1),
                    cron("old", "3-59/10 * * * *", None, created_hours_ago=5)]
        r = self.rows()
        self.assertFalse(r["sites"]["late"])
        self.assertTrue(r["old"]["late"])

    def test_discord_once_when_late_and_once_when_back(self):
        self.cjs = [cron("forums", "4-59/5 * * * *", 40)]
        monitor.jobs()
        monitor.jobs()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("forums", self.sent[0])
        self.cjs = [cron("forums", "4-59/5 * * * *", 1)]
        monitor.jobs()
        monitor.jobs()
        self.assertEqual(len(self.sent), 2)
        self.assertIn("forums", self.sent[1])

    def test_a_deployment_down_for_a_while(self):
        self.deps = [dep("whatsapp", 1, 0), dep("images", 0, 0), dep("web", 2, 2)]
        monitor.jobs()
        self.assertEqual(self.sent, [], "a pod restarting is not yet an outage")
        self.t = T0 + timedelta(seconds=monitor.DOWN_AFTER + 1)
        d = {x["name"]: x for x in monitor.jobs()["deployments"]}
        self.assertTrue(d["whatsapp"]["down"])
        self.assertFalse(d["images"]["down"])
        self.assertFalse(d["web"]["down"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("whatsapp", self.sent[0])
        self.deps = [dep("whatsapp", 1, 1)]
        monitor.jobs()
        self.assertEqual(len(self.sent), 2)


if __name__ == "__main__":
    unittest.main()
