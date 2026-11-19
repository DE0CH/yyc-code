#!/usr/bin/env python3
"""Coverage audit 2026-10-14 (government and English outlets): every item an outlet publishes comes in, not a
selection of its sections. Measured gaps: The Standard's world/entertainment/sport/social-buzz stories (a section
filter), SCMP's sections without an RSS feed we read (sport, tech, lifestyle, magazines…), Reuters Breakingviews
(only in the full sitemap, never in the news sitemap), news.gov.hk's Chinese categories (only top stories + finance
were read), Young Post's Spark and learning-zone stories (only two news pages were read).

    python3 tests/test_coverage_gov_english.py
"""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paywall, sources, sources_extra as se  # noqa: E402


def spec(sid):
    return next(s for s in sources.SOURCES + se.EXTRA if s[0] == sid)


STANDARD = ('<urlset>' + ''.join(
    f'<url><loc>https://www.thestandard.com.hk/{sec}/article/34{i}/x-{i}</loc><news:news><news:publication_date>'
    f'2026-10-14T15:32:19+00:00</news:publication_date><news:title>Story {sec}</news:title></news:news></url>'
    for i, sec in enumerate(("news", "world", "entertainment", "social-buzz", "sports", "lifestyle"))) + '</urlset>')


class Coverage(unittest.TestCase):
    def test_standard_every_section(self):
        got = se.standard_sitemap(STANDARD)
        self.assertEqual([x["section"] for x in got], ["news", "world", "entertainment", "social-buzz", "sports", "lifestyle"])
        self.assertEqual(got[1]["time"], "2026-10-14T15:32:19+08:00")  # HKT labelled +00:00

    def test_scmp_every_section(self):
        sid, name, url, parser, proxy = spec("scmp_sitemap")
        self.assertEqual((name, url, proxy), ("南華早報", "https://www.scmp.com/sitemap/google-news.xml", False))
        page = ('<urlset><url><loc>https://www.scmp.com/sport/racing/article/3370915/derby-winner</loc><n:news>'
                '<n:publication_date>2026-10-14T15:04:38.000Z</n:publication_date><n:title>Derby winner rolls back the years'
                '</n:title></n:news></url></urlset>')
        with mock.patch.object(sources, "fetch", return_value=page):
            got = sources.fetch_source(spec("scmp_sitemap"))
        self.assertEqual([(x["title"], x["section"]) for x in got], [("Derby winner rolls back the years", "sport")])
        self.assertEqual(got[0]["published"].isoformat(), "2026-10-14T15:04:38+00:00")
        self.assertLessEqual(sources.EVERY.get("scmp_sitemap", 300), 900)  # ~110 stories a day, 2 days in the sitemap

    def test_scmp_video_and_photos(self):
        """SCMP coverage audit 2026-10-20: SCMP's videos (/video/…) and photo galleries (/photos/…) are in none of the
        feeds we read (google-news.xml and the RSS hold articles only); its video-format.xml (6.4 MB) and galleries.xml
        list them newest first, so only their head is read"""
        sid, name, url, parser, proxy = spec("scmp_video")
        self.assertEqual((name, url, proxy), ("南華早報", "https://www.scmp.com/sitemap/video-format.xml", False))
        page = ('<urlset><url><loc>https://www.scmp.com/video/world/3371485/japan-launches-spacecraft</loc><video:video>'
                '<video:description><![CDATA[Read more here: https://sc.mp/990488\nAn uncrewed Japanese spacecraft is on its way.]]>'
                '</video:description><video:publication_date>2026-10-20T07:53:46.000Z</video:publication_date>'
                '<video:title>Japan successfully launches spacecraft</video:title></video:video></url>'
                '<url><loc>https://www.scmp.com/video/asia/3371405/cut-off-entry')
        with mock.patch.object(sources, "fetch_head", return_value=page) as head:
            got = sources.fetch_source(spec("scmp_video"))
        self.assertLessEqual(head.call_args[0][1], 100_000)
        self.assertEqual([(x["title"], x["section"]) for x in got], [("Japan successfully launches spacecraft", "world")])
        self.assertEqual(got[0]["published"].isoformat(), "2026-10-20T07:53:46+00:00")
        self.assertIn("uncrewed Japanese spacecraft", got[0]["text"])

        sid, name, url, parser, proxy = spec("scmp_photos")
        self.assertEqual((name, url, proxy), ("南華早報", "https://www.scmp.com/sitemap/galleries.xml", False))
        page = ('<urlset><url><lastmod>2026-10-20T08:00:57.000Z</lastmod><loc>https://www.scmp.com/photos/hong-kong/3371488/'
                'new-family-friendly-rooftop-playground-opens</loc></url></urlset>')
        with mock.patch.object(sources, "fetch_head", return_value=page):
            got = sources.fetch_source(spec("scmp_photos"))
        self.assertEqual([(x["title"], x["section"]) for x in got], [("new family friendly rooftop playground opens", "hong-kong")])
        self.assertIn("scmp_photos", se.SLUG_TITLES)  # the page's own headline replaces the slug
        for s in ("scmp_video", "scmp_photos"):
            self.assertLessEqual(sources.EVERY.get(s, 300), 900)

    def test_newsgov_every_category(self):
        cats = {"admin", "environment", "health", "infrastructure", "law_order", "school_work", "finance",
                "nationalsecurity", "clarification"}
        urls = [s[2] for s in sources.SOURCES + se.EXTRA if s[0].startswith("newsgov")]
        for lang in ("tc", "en"):
            got = {u.split("/categories/")[1].split("/")[0] for u in urls if f".hk/{lang}/categories/" in u}
            self.assertEqual(got, cats, lang)
            self.assertIn(f"https://www.news.gov.hk/{lang}/common/html/topstories.rss.xml", urls)

    def test_reuters_breakingviews_from_full_sitemap(self):
        news = ('<urlset><url><loc>https://www.reuters.com/world/china/x-2026-10-14/</loc><news:news><news:publication_date>'
                '2026-10-14T06:21:52.604Z</news:publication_date><news:title>X</news:title></news:news></url></urlset>')
        full = ('<urlset><url><loc>https://www.reuters.com/world/china/x-2026-10-14/</loc><lastmod>2026-10-14T07:00:00Z</lastmod></url>'
                '<url><loc>https://www.reuters.com/commentary/breakingviews/mega-ipos-broaden-appeal-tycoon-wealth-tactics-2026-10-14/</loc>'
                '<lastmod>2026-10-14T14:04:57.335Z</lastmod></url>'
                '<url><loc>https://www.reuters.com/business/old-story-updated-2026-09-30/</loc><lastmod>2026-10-14T14:00:00Z</lastmod></url>'
                '</urlset>')

        def fake(url, **kw):
            return full if "/sitemap/" in url else news
        # every Reuters sitemap is read with the solved DataDome session since 30 Oct 2026 (paywall.reuters_page)
        with mock.patch.object(paywall, "reuters_page", side_effect=fake):
            got = sources.fetch_source(spec("reuters"))
        by = {x["url"]: x for x in got}
        bv = by["https://www.reuters.com/commentary/breakingviews/mega-ipos-broaden-appeal-tycoon-wealth-tactics-2026-10-14/"]
        self.assertEqual(bv["title"], "Mega ipos broaden appeal tycoon wealth tactics")
        self.assertEqual(bv["section"], "commentary")
        self.assertEqual(bv["published"].isoformat(), "2026-10-14T14:04:57.335000+00:00")
        old = by.get("https://www.reuters.com/business/old-story-updated-2026-09-30/")
        self.assertTrue(old is None or old["published"].date().isoformat() == "2026-09-30")  # an update is not a new story
        self.assertEqual(sum(u.startswith("https://www.reuters.com/world/china/x-") for u in by), 1)
        self.assertIn("reuters", se.SLUG_TITLES)  # the page's own title replaces the slug when it is read

    def test_youngpost_whole_site(self):
        sid, name, url, parser, proxy = spec("youngpost_sitemap")
        self.assertEqual(url, "https://www.youngpostclub.com/sitemap/articles.xml")
        page = ('<urlset><url><loc>https://www.youngpostclub.com/spark/news/sports/article/3370834/hong-kong-tennis-star-wins</loc>'
                '<lastmod>2026-10-14T07:00:27.000+00:00</lastmod></url></urlset>')
        with mock.patch.object(sources, "fetch_head", return_value=page, create=True) as head, \
             mock.patch.object(sources, "fetch", return_value=page):
            got = sources.fetch_source(spec("youngpost_sitemap"))
        self.assertEqual([(x["title"], x["section"]) for x in got], [("hong kong tennis star wins", "spark")])
        self.assertIn("youngpost_sitemap", se.SLUG_TITLES)
        self.assertTrue(head.called)  # only the newest part of the 9 MB sitemap is read (it lists newest first)


if __name__ == "__main__":
    unittest.main()
