#!/usr/bin/env python3
"""An RSS item's text is its <content:encoded> (the whole article, WordPress) when that is longer than its
<description> (an excerpt ending 「[…]」).

2026-10-20: 集誌社's feed (thecollectivehk.com/feed/) gives a 1,394-character excerpt in <description> and the whole
3,414-character article in <content:encoded>; only the excerpt was kept.

    python3 tests/test_rss_full.py
"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sources  # noqa: E402

BODY = "".join(f"<p>調查宏福苑火災的獨立委員會預計本周內向特首提交報告，第{i}段。</p>" for i in range(12))
FEED = f"""<?xml version="1.0"?><rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>宏福苑聽證｜獨委會將交報告</title><link>https://thecollectivehk.com/a/</link>
<pubDate>Tue, 20 Oct 2026 08:20:38 +0000</pubDate><dc:creator><![CDATA[集誌社記者]]></dc:creator>
<description><![CDATA[調查宏福苑火災的獨立委員會預計本周內向特首提交報告 [&#8230;]]]></description>
<content:encoded><![CDATA[{BODY}]]></content:encoded></item>
<item><title>短</title><link>https://x.example/b</link><description>完整的描述文字，比內容更長。</description>
<content:encoded><![CDATA[<p>短</p>]]></content:encoded></item>
</channel></rss>"""


class RssFull(unittest.TestCase):
    def test_content_encoded(self):
        a, b = sources.rss(FEED)
        self.assertIn("第11段", a["text"])
        self.assertNotIn("[…]", a["text"])
        self.assertIsNone(b["text"])  # shorter than its description
        self.assertIsNone(sources._whole("前文……" * 50 + "閱讀全文", "短"))  # a cut content is no whole article

    def test_ingested_text(self):
        """the feed's whole article becomes the post's text for any RSS source (sources.extras)"""
        it = sources.rss(FEED)[0]
        self.assertIn("第11段", sources.extras("thecollectivehk", it)["text"])


if __name__ == "__main__":
    unittest.main()
