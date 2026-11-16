"""each topic = its definition (streams.json brief) + its background brief (briefs/<id>.md): the scorer reads the briefs with citations and sources stripped, and they are in
defs_hash, so an edit rescores history"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import streams


class Briefs(unittest.TestCase):
    def test_strip(self):
        md = "# T\n\nBuilt in 1983 [1][2]. Toll **168** [3, 4] and 79 [5–7].\n\n---\n\n## Sources\n\n1. x\n"
        self.assertEqual(streams.strip_citations(md), "# T\n\nBuilt in 1983. Toll **168** and 79.")

    def test_every_brief_stripped(self):
        for f in os.listdir(streams.BRIEFS):
            if f.endswith(".md"):
                b = streams.background(f[:-3])
                self.assertNotRegex(b, r"\[\d+\]", f)
                self.assertNotIn("## Sources", b, f)

    def test_short(self):
        # every scoring call carries every background (Deyao, 2026-10-28: the ~210k characters of long briefs were
        # most of the scorer's cost, and pushed calls past Haiku's 100k-token price step)
        total = sum(len(streams.background(f[:-3]) or "") for f in os.listdir(streams.BRIEFS)
                    if f.endswith(".md") and f != "README.md")
        self.assertLess(total, 40000)

    def test_in_prompt_and_defs(self):
        sts = [{"id": "WFC", "name": "宏福苑", "brief": "scope"}, {"id": "ZZZ", "name": "none", "brief": "scope"}]
        p = streams.score_prompt(sts, [{"id": 1, "title": "t", "text": "x"}])
        # each topic is two parts (Deyao, 2026-10-23): its definition and its background
        wfc = p[p.index('<topic id="WFC"'):p.index("</topic>")]
        self.assertIn("<definition>scope</definition>", wfc)
        self.assertIn("<background>", wfc)
        zzz = p[p.index('<topic id="ZZZ"'):]
        self.assertIn("<definition>scope</definition>", zzz)
        self.assertNotIn("<background>", zzz[:zzz.index("</topic>")])
        self.assertLess(p.index('<topic id="ZZZ"'), p.index("\nPosts:\n"))
        orig = streams.background
        try:
            streams.background = lambda sid: None
            without = streams.defs_hash(sts)
        finally:
            streams.background = orig
        self.assertNotEqual(without, streams.defs_hash(sts))


if __name__ == "__main__":
    unittest.main()
