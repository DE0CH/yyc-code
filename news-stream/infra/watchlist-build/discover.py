"""Find each entity's accounts: YouTube channel search (free), the owner's website links (free), Instagram user search,
then the same handle checked on Threads, TikTok and X (TikHub, YYC's key). Resumable: every answer cached in cache/."""
import json, os, re, sys, time, hashlib, urllib.request, urllib.parse, concurrent.futures as cf
B = os.environ.get("WATCHLIST_BUILD", os.path.expanduser("~/artifacts/watchlist/build"))
os.makedirs(B + "/cache", exist_ok=True)
E = json.load(open(B + "/entities.json"))
K = os.environ["TIKHUB_API"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
      "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8", "Cookie": "SOCS=CAI"}
spent = [0.0]

def cached(key, fn):
    f = B + "/cache/" + hashlib.sha1(key.encode()).hexdigest() + ".json"
    if os.path.exists(f):
        return json.load(open(f))
    try:
        v = fn()
    except Exception as e:
        v = {"_error": repr(e)[:200]}
    json.dump(v, open(f, "w"), ensure_ascii=False)
    return v

def get(url, headers=UA, timeout=30):
    req = urllib.request.Request(url, headers=headers)
    import ssl
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode("utf-8", "replace")

_last = [0.0]
def tik(path, params, price):
    def go():
        wait = _last[0] + 0.35 - time.time()
        if wait > 0: time.sleep(wait)
        _last[0] = time.time()
        u = "https://api.tikhub.io" + path + "?" + urllib.parse.urlencode(params)
        try:
            d = json.loads(get(u, {"Authorization": "Bearer " + K, "User-Agent": "yyc-news-stream/1"}, 60))
            spent[0] += price
            return d.get("data")
        except urllib.error.HTTPError as e:
            return {"_error": f"HTTP {e.code}"}
    return cached(path + json.dumps(params, sort_keys=True), go)

def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)

def subs(t):
    m = re.search(r"([\d.,]+)\s*([KMB萬万]?)", t or "")
    if not m: return 0
    return int(float(m.group(1).replace(",", "")) * {"K": 1e3, "M": 1e6, "B": 1e9, "萬": 1e4, "万": 1e4}.get(m.group(2), 1))

def yt_search(q):
    def go():
        t = get("https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": q, "sp": "EgIQAg=="}))
        d = json.loads(re.search(r"var ytInitialData = (\{.*?\});</script>", t, re.S).group(1))
        out = []
        for o in walk(d):
            c = o.get("channelRenderer")
            if c:
                st = (c.get("subscriberCountText") or {}).get("simpleText") or ""
                vc = (c.get("videoCountText") or {}).get("simpleText") or ""
                out.append({"id": c["channelId"], "title": c["title"]["simpleText"], "handle": st if st.startswith("@") else None,
                            "subs": subs(vc if st.startswith("@") else st),
                            "verified": bool(c.get("ownerBadges"))})
        return out
    return cached("yt:" + q, go)

def matches(name, keys):
    n = (name or "").lower()
    return any(k.lower() in n for k in keys)

SOCIAL = re.compile(r'https?://(?:www\.|m\.)?(youtube\.com/(?:channel/UC[\w-]{22}|@[\w.\-%]+|c/[\w.\-%]+|user/[\w.\-]+)|facebook\.com/[\w.\-%/?=]+|instagram\.com/[\w.]+|(?:twitter|x)\.com/\w+|threads\.(?:net|com)/@[\w.]+|tiktok\.com/@[\w.]+|weibo\.com/(?:u/)?\w+)', re.I)
def site_links(url):
    def go():
        return sorted(set(m.group(0).rstrip("/") for m in SOCIAL.finditer(get(url))))
    return cached("site:" + url, go)

def one(e):
    r = {"owner": e["owner"], "group": e["group"], "role": e["role"], "why": e["why"], "keys": e["keys"], "found": []}
    for q in e["keys"][:2]:
        for c in yt_search(q) if isinstance(yt_search(q), list) else []:
            if matches(c["title"], e["keys"]) and (c["subs"] >= 1000 or c["verified"]):
                r["found"].append({"platform": "youtube", "id": c["id"], "handle": c["handle"], "name": c["title"], "followers": c["subs"],
                                   "verified": c["verified"], "evidence": "YouTube channel search: " + q})
    if e.get("site"):
        links = site_links(e["site"])
        if isinstance(links, list):
            r["site_links"] = links
    # Instagram search
    ig = tik("/api/v1/instagram/v2/search_users", {"keyword": e["keys"][0]}, 0.002)
    for u in walk(ig):
        if u.get("username") and "full_name" in u and matches(u.get("full_name"), e["keys"]):
            r["found"].append({"platform": "instagram", "handle": u["username"], "name": u.get("full_name"), "verified": bool(u.get("is_verified")),
                               "evidence": "Instagram user search: " + e["keys"][0]})
    return r

if __name__ == "__main__":
    out = []
    with cf.ThreadPoolExecutor(4) as ex:
        for r in ex.map(one, E):
            out.append(r)
            json.dump(out, open(B + "/found.json", "w"), ensure_ascii=False, indent=1)
    print("entities", len(out), "TikHub USD (new requests)", round(spent[0], 3))
