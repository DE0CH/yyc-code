#!/usr/bin/env python3
"""A Threads post's comments are only its replies (Deyao, 2026-10-15): the logged-out post page also carries
Threads' relatedPosts (other people's posts) and the author's self_thread, and those were filed as the post's
comments, e.g. yuyeesuen's 18 Sep post under kinliuhk's 15 Oct post, which has no replies. No network, no database:
the fixtures are the real pages of 15 Oct 2026, cut down to the fields the parser reads.

    python3 tests/test_threads_comments.py
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import comments_fetch  # noqa: E402

FIX = os.path.join(HERE, "tests", "fixtures")


def page(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class ThreadsComments(unittest.TestCase):
    def test_no_replies_gives_no_comments(self):
        got, total = comments_fetch.ig_comments(page("threads_post_no_replies.html"),
                                                "https://www.threads.net/@kinliuhk/post/DefzPfUAieh")
        self.assertEqual(total, 0)
        self.assertEqual(got, [])  # the page's 5 related posts are not comments

    def test_only_direct_replies(self):
        got, total = comments_fetch.ig_comments(page("threads_post_with_replies.html"),
                                                "https://www.threads.net/@zuck/post/Dd1MqfcG0aH")
        self.assertEqual(total, 2648)
        authors = sorted(c["author"] for c in got)
        # 8 replies to the post and 1 reply to a reply; not the related posts, not zuck's own self_thread parts
        self.assertEqual(authors, sorted(["pom_mom_4", "david.the.canuck", "ncmedic201", "likes4mike", "berlette",
                                          "nldoty", "native_zoo_loo", "nickinnova1", "fattyfattymusic"]))
        for a in ("kiphakes", "jessy.netz", "wwf_uk", "zuck"):
            self.assertNotIn(a, authors)


if __name__ == "__main__":
    unittest.main()
