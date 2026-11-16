#!/usr/bin/env python3
"""The monitor's Cloudflare panel (monitor.cloudflare): each of Deyao's five names must CNAME to customers.yyc-internal.com
(public DNS); a wrong record alerts, a failed lookup does not; the tofu runs are the GitHub workflow's (linked).

    python3 tests/test_monitor_cloudflare.py
"""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import monitor  # noqa: E402



class Panel(unittest.TestCase):
    def run_panel(self, cnames):
        alerts = []
        def cname(n):
            v = cnames.get(n, [monitor.SAAS_TARGET])
            if isinstance(v, Exception):
                raise v
            return v
        with mock.patch.object(monitor, "_cname", cname), \
             mock.patch.object(monitor, "_alert", lambda k, bad, *a: alerts.append((k, bad))):
            return monitor.cloudflare(), dict(alerts)

    def test_all_good(self):
        v, a = self.run_panel({})
        self.assertTrue(all(n["ok"] for n in v["names"]))
        self.assertEqual(len(v["names"]), 5)
        self.assertFalse(any(a.values()))
        self.assertIn("actions/workflows/cloudflare.yml", v["runs"])

    def test_wrong_record_alerts(self):
        v, a = self.run_panel({"yyc.deyaochen.com": ["e5a5.cfargotunnel.com"]})
        self.assertTrue(a["dns:yyc.deyaochen.com"])

    def test_failed_lookup_does_not_alert(self):
        v, a = self.run_panel({"mailmerge.deyaochen.com": OSError("timeout")})
        self.assertNotIn("dns:mailmerge.deyaochen.com", a)
        self.assertFalse([n for n in v["names"] if n["name"] == "mailmerge.deyaochen.com"][0]["ok"])

    def test_live_lookup(self):
        self.assertIn(monitor.SAAS_TARGET, monitor._cname("yyc.deyaochen.com"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
