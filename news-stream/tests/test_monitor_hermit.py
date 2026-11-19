"""The monitor reads YYC AI's operator snapshot from hermit (aviary hermit spec 18.3, 21.4; DE0CH/aviary yyc/refactor
spec-platform 15.5): ai_slim and ai_activity read a snapshot shaped as hermit's own contract with the monitor
(tests/fixtures/hermit-monitor-contract.json, a copy of aviary crates/hermit/tests/fixtures/monitor-contract.json,
which hermit's ops tests hold its snapshot to), and ai_snapshot sends only hermit's bearer to HERMIT_OPS_URL."""
import json, os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import monitor  # noqa: E402

CONTRACT = json.load(open(os.path.join(HERE, "fixtures", "hermit-monitor-contract.json")))
SAMPLE = {"string": "2026-11-04T10:00:00Z", "number": 3, "boolean": True, "object": {}, "array": []}


def snapshot(n=1):
    """a snapshot holding every path of both contracts, each array with n rows, values by type"""
    root = {}
    for contract in ("ai_slim", "ai_activity"):
        for path, typ in CONTRACT[contract].items():
            parts = path.split(".")
            for i in range(len(parts)):
                prefix = parts[:i + 1]
                node = [root]
                for j, part in enumerate(prefix):
                    last = j == len(prefix) - 1
                    nxt = []
                    for cur in node:
                        key, arr = (part[:-2], True) if part.endswith("[]") else (part, False)
                        if arr:
                            cur.setdefault(key, [{} for _ in range(n)])
                            nxt.extend(cur[key])
                        elif last and i == len(parts) - 1:
                            t = typ.rstrip("?")
                            if t == "array":
                                cur.setdefault(key, [{} for _ in range(n)])
                            else:
                                cur.setdefault(key, SAMPLE[t] if t != "object" else {})
                        else:
                            cur.setdefault(key, {})
                            nxt.append(cur[key])
                    node = nxt
    return root


class HermitSnapshot(unittest.TestCase):
    def test_slim(self):
        s = monitor.ai_slim(snapshot())
        self.assertEqual(s["at"], SAMPLE["string"])
        self.assertTrue(s["service"] and s["hosts"] and s["sandboxes"])
        self.assertIn("hermit", s["source"])
        json.dumps(s)

    def test_activity(self):
        d = snapshot()
        events, cur = monitor.ai_activity(d, None)
        self.assertEqual(events, [])           # a first read only sets the cursor
        d2 = snapshot(2)
        d2["at"] = "2026-11-04T10:00:10Z"
        for row in d2["budget"]["byUser"]:
            row["callsToday"] = 9
        events, cur2 = monitor.ai_activity(d2, cur)
        json.dumps(events)
        self.assertIsInstance(cur2, dict)

    def test_bearer_only(self):
        with mock.patch.dict(os.environ, {"HERMIT_OPS_TOKEN": "t"}), mock.patch.object(monitor, "get") as g:
            monitor.ai_snapshot()
        url, headers = g.call_args[0][0], g.call_args[0][1]
        self.assertEqual(url, monitor.HERMIT_OPS_URL)
        self.assertEqual(headers, {"Authorization": "Bearer t", "Aviary-Protocol": "hermit-core=1"})
        self.assertIn("hermit-bridge.yyc.svc.cluster.local/ops/snapshot", monitor.HERMIT_OPS_URL)

    def test_token_required(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                monitor.ai_snapshot()


if __name__ == "__main__":
    unittest.main()
