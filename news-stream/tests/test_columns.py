#!/usr/bin/env python3
"""The political-column parsers in sources_extra.py on small samples of each site's markup (no network, no database).

    python3 tests/test_columns.py
"""
import json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sources_extra as e  # noqa: E402


class Columns(unittest.TestCase):
    def test_am730(self):
        page = '''<li class="newslist-item"><div class="newsitem"><div class="newsitem-entry">
          <h6 class="newsitem-title"><a href="/本地/1057627/羅淑佩-ray-online">羅淑佩撐青年設計師｜Ray Online</a></h6>
          <div class="newsitem-time">4日前</div></div></div></li>
          <li><h6 class="newsitem-title"><a href="/本地/1057000/other">另一篇新聞</a></h6></li>'''
        got = e.am730_column("Ray Online")(page)
        self.assertEqual([x["title"] for x in got], ["羅淑佩撐青年設計師"])
        self.assertEqual(got[0]["url"], "https://www.am730.com.hk/本地/1057627/羅淑佩-ray-online")
        self.assertTrue(got[0]["time"])  # 4 days ago, as an ISO time

    def test_epoch(self):
        page = ('<a href="/news/2026-10-09/25870471" aria-label="【解碼新香港】林兆彬｜ 穿草鞋與國家安全的關係">'
                '<a href="/news/2026-10-09/25870471">'
                '<a href="/news/2026-10-08/21744681" aria-label="【文藝講呢啲】林兆彬｜ JFFT">')
        got = e.epoch_author("【解碼新香港】")(page)
        self.assertEqual([(x["title"], x["time"][:10]) for x in got], [("穿草鞋與國家安全的關係", "2026-10-09")])

    def test_eastweek(self):
        sm = ('<urlset><url><loc>https://eastweek.stheadline.com/witness/21973/懲教署抗熱措施獲讚-政官莊</loc>'
              '<news:news><news:publication_date>2026-10-10T06:30:00+00:00</news:publication_date>'
              '<news:title><![CDATA[懲教署抗熱措施獲讚｜政官莊]]></news:title></news:news></url>'
              '<url><loc>https://eastweek.stheadline.com/fun/22038/曼谷飯店</loc><news:news>'
              '<news:title>曼谷飯店</news:title></news:news></url></urlset>')
        got = e.column_sitemap("政官莊", "政官莊")(sm)
        self.assertEqual([(x["title"], x["section"]) for x in got], [("懲教署抗熱措施獲讚", "政官莊")])

    def test_stheadline_prefix(self):
        feed = json.dumps([{"data": [{"title": "<font>大棋盤</font>︱貝森特來港吹淡風", "url": "/politics/3625434/x",
                                      "updated_at": 1791852241, "channel_rect": {"name": "政情"}},
                                     {"title": "無關新聞", "url": "/x/1/y"}]}])
        got = e.stheadline_column("大棋盤")(feed)
        self.assertEqual([x["title"] for x in got], ["貝森特來港吹淡風"])

    def test_hk01_tag_feed_keeps_untitled(self):
        feed = json.dumps({"items": [{"type": 1, "data": {"title": "宏福火行政問責支援組本月埋班", "publishUrl": "https://www.hk01.com/x/60396119",
                                                          "publishTime": 1, "mainCategory": "政情"}}]})
        got = e.hk01_column("政圈風聲", None)(feed)  # tagged but the title lacks the column name: kept
        self.assertEqual(len(got), 1)


if __name__ == "__main__":
    unittest.main()
