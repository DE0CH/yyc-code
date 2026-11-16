#!/usr/bin/env python3
"""Full text for the 經濟通 / 經濟日報 / 晴報 / 資本 / 信報 group (2026-10-20: every source that has full text on its site
stores it). Real pages trimmed to fixtures (tests/fixtures/fulltext/), no network:

  - 經濟通 (etnet): tables (《異動股》, 《外圍重點》, 《B股行情》) were cut to the 50-character meta description
    ending "..."; the article's own tables are its text.
  - 經濟日報's content API (ps.hket.com, invest/paper/specialist.hket.com): an article the API marks paid False is
    free to every reader (the logged-out page shows it whole), so its whole text is kept, not marked an excerpt;
    a paid one keeps its public part (the longer of lead and nonPaywall) and stays an excerpt.
  - 晴報 (skypost): the sub-headings and bullet lists of the story were dropped, and a hidden
    {{hket:ul-video …}} template kept.
  - 資本 (capital): a story split into several rich-text blocks between pictures kept only one block.
  - 信報 instant news: a hidden "印刷版出報日" notice was taken for the story; the page's ld+json articleBody is not
    valid JSON (a missing quote), so its text was never read.

    python3 tests/test_fulltext_papers_a.py
"""
import json, os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import cache_fetch  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "fulltext")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def read(url, name):
    """fetch_one on a fixture page (no network)"""
    with mock.patch.object(cache_fetch, "get", return_value=fixture(name)):
        meta = {}
        text, _, _ = cache_fetch.fetch_one(url, meta=meta)
        return text, meta


class Etnet(unittest.TestCase):
    URL = "http://www.etnet.com.hk/www/tc/news/home_categorized_news_detail.php?newsid=ETN361020209"

    def test_table_article_kept_whole(self):
        text, _ = read(self.URL, "etnet_table.html")
        self.assertNotIn("...", text)
        self.assertIn("2家上市公司於3:45pm錄得異動紀錄", text)
        self.assertIn("中國育兒網絡", text)
        self.assertIn("-22.50%", text)
        self.assertIn("國恩科技", text)
        self.assertIn("24.52%", text)
        self.assertIn("《經濟通通訊社20日專訊》", text)

    def test_text_article_unchanged(self):
        text, _ = read(self.URL, "etnet_text.html")
        self.assertTrue(text.startswith("瑞博生物-B(06938)現價升6%"))
        self.assertIn("去年虧損1129萬瑞典克朗。", text)
        self.assertEqual(len([l for l in text.split("\n") if "恒生指數報23957" in l]), 1)


class HketApi(unittest.TestCase):
    def api(self, url, name):
        with mock.patch.object(cache_fetch, "get", return_value=fixture(name)) as get:
            meta = {}
            text, _, _ = cache_fetch.fetch_one(url, meta=meta)
            return text, meta, get.call_args[0][0]

    def test_free_article_whole(self):
        text, meta, _ = self.api("https://ps.hket.com/article/4211440/x", "hket_api_free.json")
        self.assertIn("長實發展的元朗萬芊", text)
        self.assertIn("楊桂玲續稱，萬芊以「三區六街」布局", text)
        self.assertIn("Fawn Avenue 楓木大道", text)
        self.assertNotEqual(meta.get("via"), "excerpt")
        self.assertEqual(meta["published"], "2026-10-20T15:35+08:00")

    def test_paid_article_public_part_excerpt(self):
        text, meta, _ = self.api("https://invest.hket.com/article/4210962/x", "hket_api_paid.json")
        self.assertEqual(meta.get("via"), "excerpt")
        self.assertTrue(text.startswith("人工智能（AI）信貸的第一顆地雷"))
        self.assertIn("CoreWeave", text)  # the lead carries more than nonPaywall's first sentence

    def test_paper_and_specialist_through_the_api(self):
        for url in ("https://paper.hket.com/article/4210913/x", "https://specialist.hket.com/article/4211005/x"):
            _, _, called = self.api(url, "hket_api_free.json")
            self.assertTrue(called.startswith("https://data02.hket.com/content?id="), called)
            self.assertIn("&channel=invest", called)


class Skypost(unittest.TestCase):
    def test_headings_and_lists_no_template(self):
        text, _ = read("https://skypost.hk/article/4209086/", "skypost_lists.html")
        self.assertNotIn("{{", text)
        self.assertIn("最佳早餐｜12款飲品碳水排行 無糖綠茶最穩、米漿居冠", text)
        self.assertIn("無糖綠茶（500毫升）：0克", text)
        self.assertIn("米漿（中杯）：約37克，蛋白質僅約2克", text)
        self.assertIn("2顆全蛋、豆漿300毫升", text)
        self.assertNotIn("英年早逝", text)  # the related-article cards are not the story
        lines = text.split("\n")
        self.assertLess(lines.index("碳水含量較低的選項（前7名，均在15克以內）："), lines.index("無糖綠茶（500毫升）：0克"))


class Capital(unittest.TestCase):
    def test_every_block(self):
        text, _ = read("https://www.capital-hk.com/lionclubai191026p01", "capital_blocks.html")
        self.assertTrue(text.startswith("國際獅子會中國港澳303區主辦"), text[:60])
        self.assertIn("賽事將於 2026 年 12 月至 2027 年 3 月期間舉行", text)
        self.assertIn("電話查詢： 2520 0316", text)


class HkejInstant(unittest.TestCase):
    def test_story_not_the_hidden_notice(self):
        with mock.patch.object(cache_fetch.resi, "url", return_value="http://proxy"):
            text, _ = read("https://www2.hkej.com/instantnews/china/article/4542531/x", "hkej_instant.html")
        self.assertNotIn("印刷版出報日", text)
        self.assertIn("中國人民銀行公布，內地10月貸款市場報價利率(LPR)維持不變。", text)
        self.assertIn("5年期以上LPR維持3.5厘。", text)

    def test_ld_body_tolerates_invalid_json(self):
        import lxml.html
        doc = lxml.html.fromstring(fixture("hkej_instant.html"))
        self.assertIn("1年期LPR維持3厘", cache_fetch.ld_body(doc))


if __name__ == "__main__":
    unittest.main()
