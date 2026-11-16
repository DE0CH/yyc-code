"""The paid live tools (docs/agent-platform.md step 4), each on a YYC-only account so the spend is claimable: TikHub
(social platforms, TIKHUB_API), AnyAPI (Facebook, ANYAPI_API_KEY), Exa (web search and page text, EXA_API_KEY). Every
one states its price; tools/spend.py records it against the caller (the provider's own figure when it reports one)."""
import json, os, urllib.request

from . import tool

EXA = "https://api.exa.ai"


def _need(env, what):
    v = os.environ.get(env)
    if not v:
        raise ValueError(f"{what} is not set up yet (no {env} on the server); answer from the free data instead")
    return v


def _tikhub():
    import tikhub_fetch
    return tikhub_fetch


def _platforms():
    t = _tikhub()
    return sorted(k for k in t.platforms_on() if k in t.COST and k != "facebook")


def _social_price(a):
    return _tikhub().COST.get(a.get("platform"), 0.002)


@tool("live_social_search",
      "Search one social platform live for a keyword (via TikHub): the newest posts there now, not just what YYC "
      "already collected. Platforms: tiktok, twitter (X), reddit (USD 0.001); youtube, threads, instagram "
      "(USD 0.002).",
      {"platform": {"type": "string", "description": "one of: tiktok, twitter, reddit, youtube, threads, instagram"},
       "query": {"type": "string", "description": "the keyword, as people would write it on that platform"}},
      ["platform", "query"],
      price={"usd": _social_price, "text": "USD 0.001-0.002 per search depending on the platform (listed above)"})
def live_social_search(a, ctx):
    _need("TIKHUB_API", "TikHub")
    if a["platform"] not in _platforms():
        raise ValueError(f"platform must be one of {', '.join(_platforms())}")
    items, err = _tikhub().search(a["platform"], a["query"])
    if err:
        raise ValueError(f"{a['platform']} search failed ({err[:160]}); not charged. Try again later or another platform.")
    return {"platform": a["platform"], "query": a["query"],
            "posts": [{k: x.get(k) for k in ("source", "title", "url", "published")} for x in items[:30]]}


@tool("live_facebook_search",
      "Search Facebook posts live for a keyword (via AnyAPI): public posts of the last month that contain it.",
      {"query": {"type": "string", "description": "the keyword or phrase"}}, ["query"],
      price={"usd": 0.03, "text": "USD 0.03 per search"})
def live_facebook_search(a, ctx):
    _need("ANYAPI_API_KEY", "AnyAPI (Facebook search)")
    import fb_search
    items = fb_search.search(a["query"])
    return {"query": a["query"], "posts": [{k: x.get(k) for k in ("source", "title", "url", "published")} for x in items[:30]]}


def _exa(path, body):
    req = urllib.request.Request(EXA + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"x-api-key": _need("EXA_API_KEY", "Exa (web search)"), "Content-Type": "application/json",
                                          "User-Agent": "yyc-news-stream/1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise ValueError(f"Exa refused ({e.code}: {e.read()[:200]!r}); not charged")


def _web_price(a):
    return round(0.007 + 0.001 * int(a.get("num_results") or 5), 4)


@tool("live_web_search",
      "Search the web and news live (Exa): pages beyond what YYC collects, e.g. overseas coverage, company sites, "
      "official statements. Returns titles, links, dates and a text extract of each.",
      {"query": {"type": "string", "description": "what to find, in plain words (Exa understands meaning, not just keywords)"},
       "num_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
       "category": {"type": "string", "enum": ["news", "company", "research paper", "pdf", "personal site"],
                    "description": "optional: only this kind of page"},
       "since": {"type": "string", "description": "optional YYYY-MM-DD: pages published on or after this day"}},
      ["query"],
      price={"usd": _web_price, "text": "USD 0.007 per search + USD 0.001 per result (5 results: USD 0.012)"})
def live_web_search(a, ctx):
    body = {"query": a["query"], "type": "auto", "numResults": int(a.get("num_results") or 5),
            "contents": {"text": {"maxCharacters": 800}}}
    if a.get("category"):
        body["category"] = a["category"]
    if a.get("since"):
        body["startPublishedDate"] = a["since"] + "T00:00:00.000Z"
    d = _exa("/search", body)
    out = {"query": a["query"], "results": [{"title": r.get("title"), "url": r.get("url"), "published": (r.get("publishedDate") or "")[:10] or None,
                                             "extract": (r.get("text") or "")[:800]} for r in d.get("results") or []]}
    cost = (d.get("costDollars") or {}).get("total")
    if isinstance(cost, (int, float)):
        out["_cost_usd"] = cost
    return out


@tool("live_page_fetch",
      "Read a web page's full text live (Exa): an article YYC only has the headline of, or a page found by "
      "live_web_search.",
      {"url": {"type": "string", "description": "the page's address (http/https)"},
       "max_chars": {"type": "integer", "minimum": 500, "maximum": 20000, "default": 8000}},
      ["url"],
      price={"usd": 0.001, "text": "USD 0.001 per page"})
def live_page_fetch(a, ctx):
    if not a["url"].startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    d = _exa("/contents", {"urls": [a["url"]], "text": {"maxCharacters": int(a.get("max_chars") or 8000)}})
    r = (d.get("results") or [{}])[0]
    if not r.get("text"):
        st = (d.get("statuses") or [{}])[0]
        raise ValueError(f"could not read that page ({st.get('status') or 'no text'}); not charged")
    out = {"url": r.get("url") or a["url"], "title": r.get("title"), "published": (r.get("publishedDate") or "")[:10] or None,
           "text": r["text"]}
    cost = (d.get("costDollars") or {}).get("total")
    if isinstance(cost, (int, float)):
        out["_cost_usd"] = cost
    return out
