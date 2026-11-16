#!/usr/bin/env python3
"""The WiseSearch relay's state in Postgres (wisers/store.py; Deyao, 2026-11-08: state in the application layer, no
volume). Needs TEST_DATABASE_URL; each test uses its own namespace.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_wisers_store.py

1. files: write/read (bytes and text), JSON, exists, mtime, listdir (direct children, subdirectories once), files under a
   directory, remove; a namespace never sees another's files.
2. logs: lines in the order appended.
"""
import json, os, sys, time, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_wisers import Store, new_ns  # noqa: E402


class Files(unittest.TestCase):
    def test_files(self):
        a, b = Store(new_ns("store-a")), Store(new_ns("store-b"))
        a.write("raw/x.json", json.dumps({"k": "中文"}))
        a.write("images/d1/thumb-1.jpg", b"\xff\xd8bin")
        a.write("images/d1/count.json", "{}")
        a.write("session.json", "{}")
        self.assertEqual(a.read_json("raw/x.json"), {"k": "中文"})
        self.assertEqual(a.read("images/d1/thumb-1.jpg"), b"\xff\xd8bin")
        self.assertIsNone(a.read_json("raw/missing.json"))
        a.write("raw/bad.json", "not json")
        self.assertIsNone(a.read_json("raw/bad.json"))
        self.assertTrue(a.exists("raw/x.json"))
        self.assertFalse(b.exists("raw/x.json"), "another namespace")
        self.assertAlmostEqual(a.mtime("raw/x.json"), time.time(), delta=60)
        self.assertEqual(a.listdir("raw"), ["bad.json", "x.json"])
        self.assertEqual(a.listdir(""), ["images", "raw", "session.json"])
        self.assertEqual(a.listdir("images"), ["d1"])
        self.assertEqual([p for p, _ in a.files("images")], ["images/d1/count.json", "images/d1/thumb-1.jpg"])
        a.remove("raw/bad.json")
        self.assertEqual(a.listdir("raw"), ["x.json"])
        self.assertEqual(a.listdir("raw_"), [], "LIKE wildcards are literal")

    def test_lines(self):
        s = Store(new_ns("store-lines"))
        for i in range(5):
            s.append("calls.jsonl", json.dumps({"i": i}) + "\n")
        self.assertEqual([json.loads(x)["i"] for x in s.lines("calls.jsonl")], [0, 1, 2, 3, 4])
        self.assertEqual(s.lines("searches.jsonl"), [])


if __name__ == "__main__":
    unittest.main()
