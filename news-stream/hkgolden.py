#!/usr/bin/env python3
"""高登討論區 (HKGolden, forum.hkgolden.com) threads through HKGolden's own API (Deyao, 2026-10-20: an HKGolden post
titled 「香港高登討論區」). The thread pages are a JavaScript app: the server's HTML carries only the site's name
(<title> and og:site_name 香港高登討論區), so reading the page gives no title and no text. The web app
(forum.hkgolden.com/static/js/main.*.js) fills the thread in from https://api.hkgolden.com/v1/view/<id>/<page>
(sensormode, hideblock), a plain JSON GET with no login, which answers the cluster's IP: the thread's title, its first
post (content, HTML), author, date and counts.

HKGolden threads reach the corpus through Google News (site: results, which list a thread with its URL as the
title); links.py and cache_fetch.py read them here.

    python3 hkgolden.py <thread url or id>…   print what the API gives (test; writes nothing)
"""
import json, re, sys, urllib.request
from datetime import datetime, timezone

API = "https://api.hkgolden.com/"
THREAD = re.compile(r"^https?://(?:forum|m|www)\.hkgolden\.com/(?:thread|amp|view)/(\d+)|"
                    r"^https?://(?:forum|m|www)\.hkgolden\.com/view\.aspx\?(?:[^#]*&)?message=(\d+)", re.I)
TIMEOUT = 20
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"


def thread_id(url):
    """the thread id of an HKGolden thread URL (/thread/<id>, /amp/<id>, view.aspx?message=<id>), else None"""
    m = THREAD.match(url or "")
    return (m.group(1) or m.group(2)) if m else None


def get(path, timeout=TIMEOUT):
    req = urllib.request.Request(API + path, headers={"User-Agent": UA, "Origin": "https://forum.hkgolden.com",
                                                      "Referer": "https://forum.hkgolden.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def thread(tid):
    """→ {"title", "text" (the first post, paragraphs kept), "author", "published" (ISO, HKT), "engagement"} for
    thread tid; raises when the API answers without one"""
    import sources
    d = json.loads(get(f"v1/view/{tid}/1?sensormode=N&hideblock=N"))
    data = d.get("data") if isinstance(d, dict) else None
    if not d.get("result") or not isinstance(data, dict) or not (data.get("title") or "").strip():
        raise RuntimeError(f"hkgolden {tid}: no thread in the answer")
    pub = None
    if isinstance(data.get("messageDate"), (int, float)):
        pub = datetime.fromtimestamp(data["messageDate"] / 1000, timezone.utc).astimezone(
            sources.HKT).isoformat(timespec="minutes")
    eng = {k: data[f] for k, f in (("replies", "totalReplies"), ("likes", "marksGood"), ("dislikes", "marksBad"))
           if isinstance(data.get(f), int)}
    return {"title": re.sub(r"\s+", " ", data["title"]).strip(), "text": sources.paragraphs(data.get("content") or ""),
            "author": (data.get("authorName") or "").strip() or None, "published": pub, "engagement": eng}


if __name__ == "__main__":
    for a in sys.argv[1:]:
        t = thread(thread_id(a) or a)
        print(json.dumps(dict(t, text=t["text"][:300]), ensure_ascii=False, indent=1))
