#!/usr/bin/env python3
"""Full text for the 明報 / Yahoo / 巴士的報 / 星島 / 香港01 / 獨立媒體 / Reuters readers (2026-10-20): the stored text is
the article's whole story. Fixtures are real pages (scripts, styles and images stripped) under fixtures/fulltext/.

    python3 tests/test_fulltext_papers_b.py
"""
import json, os, sys, unittest, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import browser_fetch, cache_fetch, paywall  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "fulltext")


def page(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class MingPao(unittest.TestCase):
    """news.mingpao.com: the first paragraph in div#upper, the rest in div.articlelogin (siblings' children)"""

    def test_story_about_advertising_keeps_its_paragraphs(self):
        # 台鐵's 政治廣告 story: every paragraph says 廣告, which JUNK dropped (stored text was "")
        t = cache_fetch.article_text(page("papers_b_mingpao_pns_ad.html"))
        self.assertTrue(t.startswith("【明報專訊】台灣「九合一」選舉將於下月底舉行"), t[:80])
        self.assertIn("綜合中央社及聯合新聞網報道，台鐵解釋", t)
        self.assertIn("台鐵指出，為顧及民眾觀感及公司形象，明年起將不再出租予政治廣告", t)

    def test_upper_and_lower_blocks_both_read(self):
        t = cache_fetch.article_text(page("papers_b_mingpao_pns_split.html"))
        self.assertTrue(t.startswith("【明報專訊】日本靖國神社上周六至周一舉行秋季大祭"), t[:80])
        self.assertIn("中國外交部發言人毛寧周一在例行記者會上指出", t)

    def test_cross_heads_and_byline(self):
        t = cache_fetch.article_text(page("papers_b_mingpao_pns_column.html"))
        lines = t.split("\n")
        self.assertTrue(lines[0].startswith("【明報專訊】「如果我們要動用武力"), lines[0][:60])
        self.assertIn("總統大選敗將  菲防長拱火謀利".replace("  ", " "), lines)
        self.assertIn("回顧這場風波，菲防長特奧多羅無疑是最大「戰狼」。", t)
        self.assertEqual(lines[-1], "文：劉實")
        self.assertNotIn("相關字詞", t)


class NextStory(unittest.TestCase):
    def test_yahoo_story_not_the_recommendation_cards(self):
        # the cards below the story carry more text than a short story; the <article> with the page's <h1> is the story
        t = cache_fetch.article_text(page("papers_b_yahoo_next_story.html"))
        self.assertTrue(t.startswith("今日(20日)下午3時49分，執法部門接獲報案，指新清水灣道近彩雲邨"), t[:80])
        self.assertIn("新清水灣道(往龍翔道方向)近彩興里的部分行車線現已封閉", t)
        self.assertNotIn("法國連日爆發全國示威", t)

    def test_bastillepost_first_article_body_only(self):
        t = cache_fetch.article_text(page("papers_b_bastillepost_next_story.html"))
        self.assertTrue(t.startswith("挪威滑雪名將阿克塞爾·隆德·斯溫達爾已加入國家隊教練團。"), t[:80])
        self.assertIn("斯溫達爾稱：「這是一個我深信的計劃", t)
        self.assertNotIn("特朗普周四在白宮設宴", t)


class CrossHeadsAndTables(unittest.TestCase):
    def test_stheadline_subheads_and_rent_table(self):
        t = cache_fetch.article_text(page("papers_b_stheadline_table.html"))
        lines = t.split("\n")
        self.assertIn("屈臣氏64.7萬租佐敦薈", lines)
        self.assertIn("地址 | 建築面積 | 月租（萬元） | 呎租（元） | 租客", lines)
        self.assertIn("尖沙咀廣東道30號新港中心地下G07號舖 | 3517 | 80 | 227 | 龍豐集團", lines)
        self.assertIn("受業界關注的另一宗成交，為中環皇后大道中47號", t)


class HK01(unittest.TestCase):
    def test_summary_fact_boxes(self):
        t = cache_fetch.hk01_text(page("papers_b_hk01_summary.html"))
        self.assertIn("地址：福岡市博多區博多站中央街1‑1（JR 博多站前廣場）", t.split("\n"))
        self.assertIn("福岡聖誕市集2026【1】光之街博多聖誕市集——博多站前經典會場", t)


class Junk(unittest.TestCase):
    def test_labels_and_link_lines_are_junk(self):
        for s in ("廣告", "Advertisement", "訂閱明報", "相關新聞：港鐵阿婆攬實扶手！港女1招KO「老柱女」 網民讚勇敢",
                  "延伸閱讀：法國示威淪暴力「零元購」 Hermès、蘋果全遭搶 特勤部隊急出動", "© 2026 Bastillepost. All rights reserved.",
                  "Share this article"):
            self.assertTrue(cache_fetch.junk(s), s)

    def test_story_paragraphs_that_say_a_junk_word_are_kept(self):
        for s in ("台鐵指出，為顧及民眾觀感及公司形象，明年起將不再出租予政治廣告；已刊登廣告因已出租，不受新規影響。",
                  "Analysts said the subscription business grew 12% in the quarter, while advertisement revenue fell."):
            self.assertFalse(cache_fetch.junk(s), s)


class Walls(unittest.TestCase):
    def test_cloudflare_block_page_is_a_wall(self):
        p = page("papers_b_cloudflare_block.html")
        self.assertTrue(cache_fetch.blocked(p))
        self.assertTrue(cache_fetch.challenged(p) or cache_fetch.blocked(p))
        self.assertFalse(cache_fetch.blocked(page("papers_b_mingpao_pns_split.html")))


class InMediaFeed(unittest.TestCase):
    def test_rss_item_carries_the_story(self):
        items = browser_fetch.rss_items(page("papers_b_inmediahk_rss.xml"))
        self.assertEqual(len(items), 1)
        t = items[0]["text"]
        self.assertIn("巴西總統大選第二輪投票日定於10月25日進行", t)
        self.assertIn("（本網歡迎各界投稿，文章內容為作者個人意見，並不代表本網立場。）", t)
        self.assertNotIn("內容:", t)
        self.assertNotIn("自由標籤", t)
        self.assertGreater(len(t), 3000)


class Reuters(unittest.TestCase):
    def query(self, url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(paywall.reuters_api(url)).query)["query"][0]
        return json.loads(q)["website_url"]

    def test_breakingviews_column_address(self):
        self.assertEqual(self.query("https://www.reuters.com/columns/breaking-view/merger-monday-channels-old-economy-vibes-2026-10-20/"),
                         "/commentary/breakingviews/merger-monday-channels-old-economy-vibes-2026-10-20/")
        self.assertEqual(self.query("https://www.reuters.com/columns/big-view/israels-key-election-has-high-economic-stakes-2026-10-20/"),
                         "/commentary/breakingviews/israels-key-election-has-high-economic-stakes-2026-10-20/")
        self.assertEqual(self.query("https://www.reuters.com/world/americas/trump-says-mexico-us-are-close-trade-deal-2026-10-20/"),
                         "/world/americas/trump-says-mexico-us-are-close-trade-deal-2026-10-20/")

    def test_zero_width_characters_removed(self):
        # verbatim from the content API (post 6565458): U+200B / U+200C / U+2060 between the words
        doc = {"result": {"content_elements": [
            {"type": "paragraph", "content": "German producer ​prices rose more ‌than expected in September, ​up ​6.7% on the ⁠year."}]}}
        text, _ = paywall.reuters_text(doc)
        self.assertEqual(text, "German producer prices rose more than expected in September, up 6.7% on the year.")


if __name__ == "__main__":
    unittest.main()
