#!/usr/bin/env python3
"""marks.py: the traffic light + remark Telegram uses for an item (no database needed).

    python3 tests/test_marks.py
"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import marks  # noqa: E402


class Marks(unittest.TestCase):
    def test_light(self):
        for sent, light in (("positive", "🟢"), ("neutral", "🟡"), ("mixed", "🟡"), ("irony", "🟡"), ("negative", "🔴"), (None, "")):
            self.assertEqual(marks.mark({"sentiment": sent, "source": "明報"})["light"], light)

    def test_remark_order(self):
        m = lambda labels, **kw: marks.mark(dict({"sentiment": "neutral", "labels": labels, "source": "明報"}, **kw))
        self.assertEqual(m(["engaging", "issue"])["remark"], "👀")  # 值得留意 before 熱議
        self.assertEqual(m(["trend"]), {"light": "🟡", "remark": "🔥", "note": "熱議"})
        self.assertEqual(m([], source_type="Official")["remark"], "📌")
        self.assertEqual(m(["kol"], source_type="Official")["remark"], "🔥")  # a label wins over the source
        self.assertEqual(m(["praise", "review"])["remark"], "")
        self.assertEqual(marks.text({"sentiment": "negative", "labels": ["crisis"]}), "🔴👀")

    def test_official_by_source(self):  # stream items carry sid/source, not source_type: corpus.classify decides
        self.assertEqual(marks.mark({"sid": "newsgov_top", "source": "政府新聞網"})["remark"], "📌")


if __name__ == "__main__":
    unittest.main()
