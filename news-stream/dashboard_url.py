"""dashboard_url: the absolute link to an item's details page on the YYC monitoring site, for the AI server's data
API (/app/api/v1: tools/data.py) so a model or script can cite the
page a reader opens (Deyao, 2026-10-21: "the news monitor api/mcp should include link to the details page on the
dashboard"). The twin for yyc-mcp (mcp-edge) is web-app/src/lib/dashboard_url.ts; tests/test_dashboard_url.py checks
both give the same links.

Host: the main site (newswatch.yyc-internal.com) serves 串流, the item page and 數據分析 to everyone; 報告 and 搜尋 are on only
on the beta site (yyc-beta.deyaochen.com, its own Access app; flags.json `reports` and `search` target only that host),
so their links go there.
"""
import urllib.parse

MAIN = "https://newswatch.yyc-internal.com"
BETA = "https://yyc-beta.deyaochen.com"
BETA_ONLY = ("reports", "search")  # the sections whose flag is on only on the beta site (deploy/apps/flags/flags.json)
# 數據分析's URL params in the page's own order (web-app/src/routes/-DashboardPage.tsx KEYS)
DASH_KEYS = ("v", "p", "from", "to", "sent", "label", "theme", "type", "site", "author", "q", "page", "top", "by",
             "bucket", "a", "tid", "post")
# 搜尋's URL params in the page's own order (web-app/src/routes/search.tsx KEYS)
SEARCH_KEYS = ("q", "type", "site", "region", "from", "to", "sort", "title", "comments", "page")


def _qs(pairs):
    """?k=v&… with empty values left out, encoded as the browser's URLSearchParams does (space → +, * kept, ~ → %7E)"""
    s = "&".join(f"{urllib.parse.quote_plus(k, safe='*')}={urllib.parse.quote_plus(str(v), safe='*')}"
                 for k, v in pairs if v is not None and v != "").replace("~", "%7E")
    return "?" + s if s else ""


def _host(section):
    return BETA if section in BETA_ONLY else MAIN


def post_url(pid):
    """a post's item page: /item/<id>"""
    return f"{_host('item')}/item/{urllib.parse.quote(str(pid), safe='')}"


def post_by_url(url, stream=None):
    """a post known by its url (串流's items): /item?p=<stream>&url=<url>, which redirects to /item/<id>"""
    return f"{_host('item')}/item{_qs([('p', stream), ('url', url)])}"


def stream_url(stream=None, day=None):
    """串流: a stream's filed items, optionally on one day"""
    return f"{_host('stream')}/{_qs([('p', stream), ('d', day)])}"


def dashboard_url(**p):
    """數據分析 with the given view and filters (from_ for `from`); params in the page's order, empty ones left out"""
    if "from_" in p:
        p["from"] = p.pop("from_")
    unknown = set(p) - set(DASH_KEYS)
    if unknown:
        raise ValueError(f"not a 數據分析 param: {sorted(unknown)}")
    return f"{_host('dashboard')}/dashboard{_qs([(k, p.get(k)) for k in DASH_KEYS])}"


def topic_url(tid, **p):
    """one hot topic (熱門話題 → the topic); the id is only valid with the same stream, period and filters"""
    return dashboard_url(**{**p, "v": "hot", "tid": tid})


def person_url(author, **p):
    """one author (人物)"""
    return dashboard_url(**{**p, "v": "persona", "a": author})


def theme_url(theme, **p):
    """a theme's posts (主題 → 帖子 filtered to it)"""
    return dashboard_url(**{**p, "v": "posts", "theme": theme})


def source_url(site, **p):
    """an outlet's posts in a stream (來源 → 帖子 filtered to it)"""
    return dashboard_url(**{**p, "v": "posts", "site": site})


def search_url(**p):
    """搜尋 (the whole corpus, every stream) with the given query and filters (from_ for `from`)"""
    if "from_" in p:
        p["from"] = p.pop("from_")
    unknown = set(p) - set(SEARCH_KEYS)
    if unknown:
        raise ValueError(f"not a 搜尋 param: {sorted(unknown)}")
    return f"{_host('search')}/search{_qs([(k, p.get(k)) for k in SEARCH_KEYS])}"


def report_url(path=None):
    """one report file (/reports/<day>/<stream>/<file>), or 報告 itself"""
    if path:
        u = urllib.parse.urlsplit(path)
        path = u.path + (f"?{u.query}" if u.query else "")
    return _host("reports") + (path or "/reports/")
