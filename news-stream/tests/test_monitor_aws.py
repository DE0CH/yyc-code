#!/usr/bin/env python3
"""The monitor's AWS provider (monitor.bal_aws; Deyao, 2026-11-17: "add aws usage tracking to the YYC live monitor,
make sure it doesn't overflow"): YYC's AWS member account, AWS's own figures.

    python3 tests/test_monitor_aws.py

1. Without the key the provider says what it needs and shows no figure.
2. Cost Explorer's answer (per UTC day and service, pages followed) becomes the month so far, yesterday, today, each
   day and the month per service; EC2's answer the instances not terminated, the running ones counted per type.
3. Cost Explorer is charged per request: one read per AWS_CE_TTL however often the balances are computed; a new pod
   takes the published snapshot's figures instead of asking again; a failed read keeps the last figures and says why.
4. The request is signed for its service and region (Signature Version 4) and is a POST that reads only.
5. The live dashboard's line: one short first line, then the services and the running instances; the spending
   window costs AWS at the latest closed day's rate per service, and the history carries AWS's own days.
"""
import json, os, sys, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import monitor  # noqa: E402

TODAY = datetime.now(timezone.utc).date()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731
EC2, EBS, S3 = "Amazon Elastic Compute Cloud - Compute", "EC2 - Other", "Amazon Simple Storage Service"
KEYS = {"AWS_YYC_MONITOR_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE", "AWS_YYC_MONITOR_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"}


def ce_day(day, costs, estimated=False):
    return {"TimePeriod": {"Start": day, "End": day}, "Estimated": estimated,
            "Groups": [{"Keys": [k], "Metrics": {"UnblendedCost": {"Amount": str(v), "Unit": "USD"}}} for k, v in costs.items()]}


EC2_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DescribeInstancesResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/"><requestId>r</requestId><reservationSet>
<item><instancesSet><item><instanceId>i-0aaa</instanceId><instanceState><code>16</code><name>running</name></instanceState>
<instanceType>c7i.xlarge</instanceType><launchTime>2026-11-17T01:02:03.000Z</launchTime>
<tagSet><item><key>Name</key><value>aws-922e69</value></item><item><key>app</key><value>hermit-host</value></item></tagSet></item></instancesSet></item>
<item><instancesSet><item><instanceId>i-0bbb</instanceId><instanceState><code>16</code><name>running</name></instanceState>
<instanceType>c7i.xlarge</instanceType><launchTime>2026-11-17T02:02:03.000Z</launchTime><instanceLifecycle>spot</instanceLifecycle>
<tagSet><item><key>app</key><value>hermit-host</value></item></tagSet></item>
<item><instanceId>i-0ccc</instanceId><instanceState><code>48</code><name>terminated</name></instanceState>
<instanceType>t3.large</instanceType><launchTime>2026-11-16T02:02:03.000Z</launchTime></item>
<item><instanceId>i-0ddd</instanceId><instanceState><code>80</code><name>stopped</name></instanceState>
<instanceType>t3.large</instanceType><launchTime>2026-11-15T02:02:03.000Z</launchTime></item></instancesSet></item>
</reservationSet></DescribeInstancesResponse>"""


class Aws(unittest.TestCase):
    def setUp(self):
        monitor._aws["costs"] = None
        self.calls = []
        self.ce_pages = [{"ResultsByTime": [ce_day(D(2), {EC2: 1.0, EBS: 0.25}), ce_day(D(1), {EC2: 2.4, EBS: 0.3, S3: 0.06})],
                          "NextPageToken": "p2"},
                         {"ResultsByTime": [ce_day(D(0), {EC2: 0.5, "Tax": 0}, estimated=True)]}]
        self.fail_ce = False

        def post(service, region, body, headers, ak, sk, timeout=25):
            self.calls.append((service, region, body, headers))
            if service == "ce":
                if self.fail_ce:
                    raise RuntimeError("AWS ce HTTP 400: ThrottlingException")
                return json.dumps(self.ce_pages[1 if b"NextPageToken" in body else 0])
            return EC2_XML
        for p in (mock.patch.object(monitor, "aws_post", post), mock.patch.object(monitor, "stored", lambda name: None),
                  mock.patch.dict(os.environ, KEYS)):
            p.start()
            self.addCleanup(p.stop)

    def ce_calls(self):
        return [c for c in self.calls if c[0] == "ce"]

    def test_1_not_connected(self):
        with mock.patch.dict(os.environ, {k: "" for k in KEYS}):
            p = monitor.bal_aws()
        self.assertEqual(p["status"], "not_connected")
        self.assertIn("AWS_YYC_MONITOR_ACCESS_KEY_ID", p["needs"])
        self.assertFalse(any(k.endswith("_usd") for k in p))
        self.assertEqual(self.calls, [])
        self.assertIn("bal_aws", monitor.balances.__code__.co_names)  # one of the balances panel's providers

    def test_2_figures(self):
        p = monitor.bal_aws()
        self.assertEqual((p["id"], p["status"], p["name"]), ("aws", "ok", "AWS (account yyc)"))
        this_month = lambda n: D(n).startswith(TODAY.strftime("%Y-%m"))  # noqa: E731
        days = {D(2): 1.25, D(1): 2.76, D(0): 0.5}
        self.assertEqual(p["by_day_usd"], dict(sorted(days.items())))
        self.assertAlmostEqual(p["month_usd"], sum(v for n, v in ((2, 1.25), (1, 2.76), (0, 0.5)) if this_month(n)))
        self.assertEqual((p["yesterday"], p["yesterday_usd"], p["today_usd"]), (D(1), 2.76, 0.5))
        self.assertEqual(p["services_yesterday"], [{"name": "EC2", "usd": 2.4}, {"name": "EC2 other (disks, traffic)", "usd": 0.3},
                                                   {"name": "S3", "usd": 0.06}])
        self.assertEqual(p["services_month"][0]["name"], "EC2")
        self.assertNotIn("Tax", [s["name"] for s in p["services_month"]])  # a zero line is no line
        self.assertEqual(p["estimated_days"], [D(0)])
        # the instances: the terminated one is gone, the stopped one listed, the running ones counted per type
        self.assertEqual([(i["id"], i["name"], i["state"], i["market"]) for i in p["instances"]],
                         [("i-0ddd", "", "stopped", "on-demand"), ("i-0aaa", "aws-922e69", "running", "on-demand"),
                          ("i-0bbb", "hermit-host", "running", "spot")])
        self.assertEqual((p["running"], p["running_types"]), (2, [{"type": "c7i.xlarge", "n": 2}]))
        self.assertIn("Cost Explorer", p["source"])
        # both pages were read, for one month back to tomorrow, per day and service
        b = json.loads(self.ce_calls()[0][2])
        self.assertEqual(len(self.ce_calls()), 2)
        self.assertEqual((b["Granularity"], b["Metrics"], b["GroupBy"][0]["Key"]), ("DAILY", ["UnblendedCost"], "SERVICE"))
        self.assertEqual(b["TimePeriod"]["End"], (TODAY + timedelta(days=1)).isoformat())
        self.assertLessEqual(b["TimePeriod"]["Start"], TODAY.replace(day=1).isoformat())
        self.assertEqual([c[1] for c in self.calls if c[0] == "ec2"], ["eu-central-1"])

    def test_3_cost_explorer_is_read_once_per_ttl(self):
        for _ in range(5):
            monitor.bal_aws()
        self.assertEqual(len(self.ce_calls()), 2)                          # one read (two pages)
        self.assertEqual(len([c for c in self.calls if c[0] == "ec2"]), 5)  # the instances every round
        # the kept figures are AWS_CE_TTL old: read again; the read fails: the last figures stay, with the reason
        monitor._aws["costs"]["at"] -= monitor.AWS_CE_TTL + 1
        self.fail_ce = True
        p = monitor.bal_aws()
        self.assertEqual((p["status"], p["yesterday_usd"]), ("ok", 2.76))
        self.assertIn("ThrottlingException", p["costs_error"])
        self.fail_ce = False
        self.assertIsNone(monitor.bal_aws()["costs_error"])
        # a new pod: the published snapshot's figures, no request
        kept, n = monitor.bal_aws(), len(self.ce_calls())
        monitor._aws["costs"] = None
        with mock.patch.object(monitor, "stored", lambda name: (0, {"providers": [kept]})):
            p = monitor.bal_aws()
        self.assertEqual((len(self.ce_calls()), p["yesterday_usd"]), (n, 2.76))
        # no figures at all and the read fails: the provider is an error (balances() says so), not a zero
        monitor._aws["costs"] = None
        self.fail_ce = True
        b = {x["id"]: x for x in monitor.balances()["providers"] if x["id"] == "aws"}["aws"]
        self.assertEqual(b["status"], "error")
        self.assertIn("ThrottlingException", b["error"])

    def test_5_live_line_and_spending(self):
        p = monitor.bal_aws()
        f = monitor.provider_line(p)
        self.assertEqual(f[0][0], "month")
        self.assertRegex(f[0][1], r"^USD \d+\.\d\d · yesterday USD 2\.76$")
        self.assertLessEqual(len(" ".join(f[0])), 44)  # the first line stays short: it shares its row with the name and the source
        self.assertEqual(f[1], ["by service", "EC2 " + f"{p['services_month'][0]['usd']:.2f}" + " · EC2 other " +
                                f"{p['services_month'][1]['usd']:.2f}" + " · S3 0.06"])
        self.assertEqual(f[2], ["running now", "2 × c7i.xlarge"])
        self.assertEqual(monitor.provider_line(dict(p, running_types=[]))[2], ["running now", "no instance"])
        many = [{"type": f"m7i.{k}", "n": 9 - i} for i, k in enumerate(("large", "xlarge", "2xlarge", "4xlarge", "8xlarge"))]
        self.assertEqual(monitor.provider_line(dict(p, running_types=many))[2],
                         ["running now", "9 × m7i.large, 8 × m7i.xlarge, 7 × m7i.2xlarge, +2 more types"])
        self.assertEqual(monitor.provider_line({"id": "aws", "status": "error"}), [])
        end = datetime.now(timezone.utc)
        w = monitor.aws_window(p, end - timedelta(hours=6), end)
        self.assertAlmostEqual(w["cost_usd"], 2.76 / 4)
        self.assertEqual([(x["name"], round(x["cost_usd"], 4)) for x in w["subs"]],
                         [("EC2", 0.6), ("EC2 other (disks, traffic)", 0.075), ("S3", 0.015)])
        self.assertIn(D(1), w["provider_basis"])
        self.assertIsNone(monitor.aws_window({"id": "aws", "status": "not_connected"}, end - timedelta(hours=6), end))


class Signature(unittest.TestCase):
    def test_4_signed_post(self):
        seen = {}

        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"{}"

        def urlopen(req, timeout=None):
            seen.update(url=req.full_url, method=req.get_method(), headers={k.lower(): v for k, v in req.header_items()}, body=req.data)
            return R()
        with mock.patch.object(monitor.urllib.request, "urlopen", urlopen):
            monitor.aws_post("ce", "us-east-1", b"{}", {"content-type": "application/x-amz-json-1.1",
                                                        "x-amz-target": "AWSInsightsIndexService.GetCostAndUsage"}, *KEYS.values())
        self.assertEqual((seen["url"], seen["method"]), ("https://ce.us-east-1.amazonaws.com/", "POST"))
        a = seen["headers"]["authorization"]
        day = seen["headers"]["x-amz-date"][:8]
        self.assertRegex(a, rf"^AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/{day}/us-east-1/ce/aws4_request, "
                            r"SignedHeaders=content-type;host;x-amz-date;x-amz-target, Signature=[0-9a-f]{64}$")
        self.assertNotIn(KEYS["AWS_YYC_MONITOR_SECRET_ACCESS_KEY"], json.dumps(seen["headers"]))
        # the only calls the monitor makes: reads
        with open(monitor.__file__) as f:
            src = f.read()
        self.assertEqual(sorted(set(__import__("re").findall(r'AWSInsightsIndexService\.(\w+)|"Action": "(\w+)"', src))),
                         [("", "DescribeInstances"), ("GetCostAndUsage", "")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
