"""Facebook post search by keyword through AnyAPI (getanyapi.com, SKU facebook.search_posts) — stdlib only.

Why AnyAPI: Facebook's own post search needs a logged-in account, TikHub has no Facebook endpoints, and in the
2026-10-12 comparison AnyAPI was one of three services that found a post (STEM Work's Galbot post) from a page we
don't follow. Deyao picked it on 2026-10-13 for the daily Galbot search.

Billing: one POST = one request, USD 0.03 flat from its first source (up to 20 posts); when that source fails AnyAPI
fails over to a backup that costs up to USD 0.0661 a request. The response's costUsd is what was charged.
Key: env ANYAPI_API_KEY (never printed): a key from YYC's own AnyAPI account, so AnyAPI's dashboard shows YYC's spend.
Search matching is loose (銀河通用 brings back 銀河 galaxy posts); every post is kept and the stream scorer (streams.py)
decides which stream, if any, it belongs to.
CLI test: python3 fb_search.py <keyword>
"""
import json, os, re, sys, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

URL = "https://api.getanyapi.com/v1/run/facebook.search_posts"
HKT = timezone(timedelta(hours=8))
DAYS = 2  # posts from yesterday and today (HKT): the search runs once a day


class AnyAPIError(Exception):
    pass


VIDEO = re.compile(r"facebook\.com/(?:reel/|watch/?\?v=|[^?#]*/videos/(?:[^/?#]+/)?)(\d+)")


def media(p, url):
    """{"images", "clips"} of one AnyAPI post (Deyao, 2026-10-15: picture and video posts were scored on their few
    words): its image and its album preview's pictures (albumPreview [{id, image}]); a reel or video link is a clip
    (fb:<id>, kind ytdlp: media.py reads it with yt-dlp from the server, the residential proxy only as a fallback)"""
    images = [u for u in [p.get("image")] + [a.get("image") for a in p.get("albumPreview") or [] if isinstance(a, dict)]
              if isinstance(u, str) and u.startswith("http")]
    m = VIDEO.search(url)
    return {"images": list(dict.fromkeys(images)), "clips": [{"key": "fb:" + m.group(1), "url": url, "kind": "ytdlp"}] if m else []}


def search(keyword, days=DAYS):
    """→ [{"url", "text", "author", "created" (epoch s), "engagement", "images", "clips"}], newest first"""
    key = os.environ.get("ANYAPI_API_KEY")
    if not key:
        raise AnyAPIError("ANYAPI_API_KEY is not set")
    start = (datetime.now(HKT) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    body = json.dumps({"query": keyword, "limit": 20, "startDate": start}).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "X-API-Key": key, "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "yyc-news-stream/1"})
    import costlog  # every search in paid_calls with the cost AnyAPI states (the cost profile)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:  # p95 40 s, p99 64 s (AnyAPI's published latency)
            d = json.load(r)
    except urllib.error.HTTPError as e:
        costlog.record("anyapi", "facebook.search_posts", status=e.code, ok=False)
        raise AnyAPIError(f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}") from None
    cost = next((x.get("costUsd") for x in (d, d.get("output") or {}, d.get("usage") or {})
                 if isinstance(x, dict) and isinstance(x.get("costUsd"), (int, float))), None)
    costlog.record("anyapi", "facebook.search_posts", ok=not d.get("error"), usd=cost,
                   basis="provider (costUsd in the response)" if cost is not None else "list price USD 0.03 (no costUsd)")
    if d.get("error"):
        raise AnyAPIError(f"{d.get('error')}: {str(d.get('message'))[:200]}")
    out = []
    for p in ((d.get("output") or {}).get("data") or {}).get("items") or []:
        url = p.get("url") or ""
        if not url or url.rstrip("/").endswith("permalink.php"):  # some results carry no usable link
            continue
        eng = {k: p.get(f) for k, f in (("likes", "reactionCount"), ("comments", "commentCount"), ("shares", "shareCount"))
               if isinstance(p.get(f), int)}
        out.append({"url": url, "text": (p.get("text") or "").strip(), "author": p.get("authorName") or "",
                    "created": p.get("createdUtc"), "engagement": eng or None, **media(p, url)})
    out.sort(key=lambda p: p["created"] or 0, reverse=True)
    return out


if __name__ == "__main__":
    for p in search(" ".join(sys.argv[1:]) or "Galbot"):
        print(datetime.fromtimestamp(p["created"] or 0, HKT).isoformat(), p["author"], p["url"], p["text"][:80].replace("\n", " "))
