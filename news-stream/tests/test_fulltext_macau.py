#!/usr/bin/env python3
"""Full text for the catalogue sources that read little or none (2026-10-20): ASP.NET pages wrapped in one <form>,
pages that only open a PDF, attached PDFs, Strikingly and Nuxt posts, 市民日報's API, 力報's e-paper issues, TDM's
share links, Imperva's stub, and the list's own headers on its article pages. Fixtures are real pages (scripts,
styles and images stripped, except the script a rule reads) under fixtures/fulltext/macau_*.

    python3 tests/test_fulltext_macau.py
"""
import json, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import cache_fetch  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "fulltext")


def page(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class Fetches:
    """cache_fetch.get / get_bytes replaced by a table of answers; the calls made are kept"""

    def __init__(self, answers, files=None):
        self.answers, self.files, self.calls = answers, files or {}, []

    def __enter__(self):
        self.old = cache_fetch.get, cache_fetch.get_bytes
        cache_fetch.get = self.get
        cache_fetch.get_bytes = self.get_bytes
        return self

    def __exit__(self, *a):
        cache_fetch.get, cache_fetch.get_bytes = self.old

    def get(self, url, proxy=None, headers=None, isp=False):
        self.calls.append((url, headers))
        return self.answers[url]

    def get_bytes(self, url, proxy=None, headers=None, isp=False):
        self.calls.append((url, headers))
        return self.files[url]


def a_pdf(*lines):
    import pymupdf
    doc = pymupdf.open()
    pg = doc.new_page()
    y = 72
    for ln in lines:
        pg.insert_text((72, y), ln, fontname="china-t" if any(ord(c) > 127 for c in ln) else "helv", fontsize=11)
        y += 40
    return doc.tobytes()


class WebForms(unittest.TestCase):
    """ASP.NET WebForms wrap the whole page in <form id="form1">; dropping every <form> dropped the story"""

    def test_gss_story_inside_the_page_form(self):
        t = cache_fetch.article_text(page("macau_gss_webforms.html"))
        self.assertTrue(t.startswith("保安司司長陳子勁於2026年1月25日至29日率領警察總局梁文昌局長"), t[:60])
        self.assertIn("代表團包括保安司司長辦公室林燕生主任", t)

    def test_fsm_story_inside_the_page_form(self):
        t = cache_fetch.article_text(page("macau_fsm_webforms.html"))
        self.assertTrue(t.startswith("新學年臨近，為確保廣大師生平安、順暢抵校"), t[:60])
        self.assertIn("所有道路使用者，均應遵從現場警員的指示，有序行進。", t)

    def test_a_search_form_is_still_dropped(self):
        html = ("<html><body><form><p>搜尋本站全部文章與資料庫內容的表格說明文字，請輸入關鍵字</p></form><article><h1>標題</h1>"
                + "".join(f"<p>第{i}段正文內容，這是一篇新聞報道的段落，內容足夠長。</p>" for i in range(8)) + "</article></body></html>")
        t = cache_fetch.article_text(html)
        self.assertNotIn("搜尋本站", t)
        self.assertIn("第7段正文內容", t)


class Pdfs(unittest.TestCase):
    def test_redirect_page_names_its_pdf(self):
        url = "https://www.ha.org.hk/visitor/ha_view_content.asp?Content_ID=283936&Dimension=100&Lang=ENG&Ver=HTML"
        self.assertEqual(cache_fetch.pdf_link(url, page("macau_ha_pdf_redirect.html")),
                         "https://www.ha.org.hk/haho/ho/pad/260916PAE.pdf")
        self.assertIsNone(cache_fetch.pdf_link(url, "<html><body><p>x</p></body></html>"))

    def test_ha_press_release_read_from_its_pdf(self):
        url = "https://www.ha.org.hk/visitor/ha_view_content.asp?Content_ID=283936&Dimension=100&Lang=ENG&Ver=HTML"
        pdf = a_pdf("Hospital Authority welcomes the Policy Address", "The Hospital Authority (HA) welcomed the measures",
                    "- 2-")
        with Fetches({url: page("macau_ha_pdf_redirect.html")},
                     {"https://www.ha.org.hk/haho/ho/pad/260916PAE.pdf": pdf}):
            t, _, _ = cache_fetch.fetch_one(url)
        self.assertIn("Hospital Authority welcomes the Policy Address", t)
        self.assertIn("The Hospital Authority (HA) welcomed the measures", t)
        self.assertNotIn("- 2-", t)  # a page number

    def test_pdf_url_read_as_pdf(self):
        url = "https://www.cfmmagazine.com/static/upload/pdf/88.pdf"
        with Fetches({}, {url: a_pdf("中國融資 第八十八期", "Lalamove以創新精神為驅動")}) as f:
            t, _, _ = cache_fetch.fetch_one(url)
        self.assertIn("Lalamove以創新精神為驅動", t)
        self.assertEqual([c[0] for c in f.calls], [url])

    def test_safp_tender_reads_its_attached_pdf(self):
        url = "https://www.safp.gov.mo/zh-hant/news/tender/202607/7e341b1f27c94190921124e343736f62"
        att = "https://www.safp.gov.mo/static/2026/09/01/9f1b4ecb-feb7-4f63-9ce9-e50999932fb0.pdf?filename=開標結果"
        self.assertEqual(cache_fetch.attached_pdfs(url, page("macau_safp_tender.html")), [att])
        with Fetches({url: page("macau_safp_tender.html")}, {att: a_pdf("書面諮詢 – 開標結果", "博維資訊系統有限公司 32,551,000.00")}):
            t, _, _ = cache_fetch.fetch_one(url)
        self.assertIn("書面諮詢 – 開標結果", t)
        self.assertIn("32,551,000.00", t)


class Blocks(unittest.TestCase):
    def test_strikingly_post_every_block(self):
        t = cache_fetch.article_text(page("macau_strikingly_hkwisdom.html"))
        self.assertTrue(t.startswith("第四屆夏季青年奧林匹克運動會中國體育代表團今日（20日）在北京正式成立。"), t[:60])
        self.assertIn("66名新星出擊 參賽跨21大項", t)
        self.assertIn("代表團團長張家勝在成立大會上表示", t)
        self.assertIn("開啟這段歷史性的青奧之旅。", t)

    def test_nuxt_post_from_page_data(self):
        p = page("macau_nuxt_yoliving.html")
        self.assertLess(len(cache_fetch.article_text(p)), 200)  # the page's HTML has only the description
        t = cache_fetch.nuxt_text(p)
        self.assertTrue(t.startswith("今日，澳門葡京人 H853 的 AVENUE，"), t[:40])
        self.assertIn("在訪問中，WJP 主理人 Keita 提到：", t)
        self.assertIn("三種文化的「融合」，而不是碰撞", t)  # a heading, its "#" taken out
        self.assertNotIn("![", t)
        self.assertNotIn("imgs.yoliving.com", t)


class Apis(unittest.TestCase):
    def test_shimindaily_story_from_its_api(self):
        url = "https://www.shimindaily.net/blog-details.html?id=2112481764900388866"
        api = "https://shimindaily.expo.macauo2o.com/exhibition-api/exhibitionNews/app/2112481764900388866"
        d = {"code": 0, "data": {"onlineDate": "2026-10-20 17:51:15",
                                 "content": "<p>嘉賓大合照</p><p>2026 年 10 月 15 日，由梅氏中醫藥與系統醫學聯合主辦的說明會圓滿落幕。</p>"}}
        meta = {}
        with Fetches({api: json.dumps(d, ensure_ascii=False)}):
            t, _, _ = cache_fetch.fetch_one(url, meta)
        self.assertIn("由梅氏中醫藥與系統醫學聯合主辦的說明會圓滿落幕", t)
        self.assertEqual(meta["published"], "2026-10-20T17:51+08:00")

    def test_shimindaily_taken_down_is_empty(self):
        url = "https://www.shimindaily.net/blog-details.html?id=2109999694129651714"
        api = "https://shimindaily.expo.macauo2o.com/exhibition-api/exhibitionNews/app/2109999694129651714"
        with Fetches({api: '{"code":10094,"msg":"文章已下線","data":null}'}):
            self.assertEqual(cache_fetch.fetch_one(url)[0], "")

    def test_tdm_share_link_reads_the_api(self):
        url = "https://www.tdm.com.mo/zh-hant/sharelink/news/1252164"
        api = "https://www.tdm.com.mo/api/v1.0/news/1252164"
        d = {"data": {"detail": {"date": "10/20/2026 17:08:00", "content": "<p>(澳門電台消息) 澳琴露營節將於本週末舉行。</p>"}}}
        meta = {}
        with Fetches({api: json.dumps(d, ensure_ascii=False)}):
            t, _, _ = cache_fetch.fetch_one(url, meta)
        self.assertEqual(t, "(澳門電台消息) 澳琴露營節將於本週末舉行。")
        self.assertEqual(meta["published"], "2026-10-20T17:08+08:00")

    def test_exmoo_issue_is_its_articles_in_page_order(self):
        url = "https://www.exmoo.com/read/5225.html"
        p = page("macau_exmoo_issue.html")
        art = lambda h, b: f"<html><body><h1>{h}</h1><article>" + "".join(f"<p>{b}{i}，這是報道的一段正文，內容足夠長。</p>" for i in range(4)) + "</article></body></html>"  # noqa: E731
        answers = {url: p}
        import re
        ids = []
        for i in re.findall(r"article_id&quot;:&quot;article_(\d+)", p):
            if i not in ids:
                ids.append(i)
        self.assertEqual(ids[:3], ["265866", "265863", "265864"])
        for n, i in enumerate(ids):
            answers[f"https://www.exmoo.com/article/{i}.html"] = art(f"標題{n}", f"第{n}篇")
        with Fetches(answers) as f:
            t, _, _ = cache_fetch.fetch_one(url)
        lines = t.split("\n")
        self.assertEqual(lines[0], "標題0")
        self.assertIn("第0篇0，這是報道的一段正文，內容足夠長。", t)
        self.assertLess(t.find("標題0"), t.find("標題1"))
        self.assertIn(f"標題{len(ids) - 1}", t)
        self.assertEqual(len(f.calls), 1 + len(ids))


class Routes(unittest.TestCase):
    def test_incapsula_stub_is_a_challenge(self):
        self.assertTrue(cache_fetch.challenged(page("macau_incapsula_cityu.html")))
        url = "https://www.cityu.edu.hk/media/news/2026/10/16/cityuhk-annual-principals-reception-showcases-achievements"
        old = cache_fetch.ISP_HOSTS
        cache_fetch.ISP_HOSTS = ()  # the page as it answers on any route
        try:
            with Fetches({url: page("macau_incapsula_cityu.html")}):
                with self.assertRaises(cache_fetch.Challenge):
                    cache_fetch.fetch_one(url)
        finally:
            cache_fetch.ISP_HOSTS = old

    def test_list_headers_go_with_the_article_page(self):
        self.assertEqual(cache_fetch.catalogue_headers("https://www.am-zm.com/news/7195.html"), {"Cookie": "waf_verified=1"})
        self.assertIn("Googlebot", cache_fetch.catalogue_headers("https://macaulawnews.com/newsinfo/3214004.html")["User-Agent"])
        self.assertIsNone(cache_fetch.catalogue_headers("https://www.example.com/a.html"))
        url = "https://www.am-zm.com/news/7195.html"
        body = "<html><body><article><h1>t</h1>" + "<p>（本報消息） 行政長官岑浩輝近日表示，特區政府正密鑼緊鼓編製施政報告。</p>" * 5 + "</article></body></html>"
        with Fetches({url: body}) as f:
            cache_fetch.fetch_one(url)
        self.assertEqual(f.calls[0][1], {"Cookie": "waf_verified=1"})


if __name__ == "__main__":
    unittest.main()
