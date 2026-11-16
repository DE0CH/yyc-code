"""For every Instagram handle in the list, check the same handle on Threads, TikTok and X (TikHub, YYC key); keep
the accounts whose profile name matches the Instagram account's owner."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from discover import tik, walk, spent, B
WL = json.load(open(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "/watchlist.json")) + json.load(open(B + "/rows.json"))
igs = {}
for r in WL:
    if r["platform"] == "instagram":
        igs.setdefault(r["handle"].lower(), r)
have = {(r["platform"], (r.get("handle") or "").lower()) for r in WL}
def keys(r):
    ks = set(re.findall(r"[一-鿿]{2,}", r["name"] + " " + r.get("owner", "")))
    ks |= {w.lower() for w in re.findall(r"[A-Za-z]{4,}", r["name"]) if w.lower() not in ("hong", "kong", "official", "news", "department", "bureau", "chan", "wong", "lee")}
    return ks
def ok(name, r):
    n = (name or "").lower()
    return any(k.lower() in n for k in keys(r))
out = []
for h, r in igs.items():
    base = {k: r[k] for k in ("group", "owner", "role", "why") if k in r}
    if ("threads", h) not in have:
        d = tik("/api/v1/threads/web/fetch_user_info", {"username": h}, 0.002)
        u = next((x for x in walk(d) if x.get("username", "").lower() == h), None)
        if u and ok(u.get("full_name"), r):
            out.append(dict(base, platform="threads", name=u.get("full_name") or r["name"], handle=h, url=f"https://www.threads.com/@{h}",
                            since="2026-10-14", evidence=f"same handle as the Instagram account {h} (Threads profile '{u.get('full_name')}', {u.get('follower_count')} followers)"))
    if ("tiktok", h) not in have and "." not in h:
        d = tik("/api/v1/tiktok/app/v3/handler_user_profile", {"unique_id": h}, 0.001)
        u = (d or {}).get("user") if isinstance(d, dict) else None
        if u and ok(u.get("nickname"), r) and (u.get("follower_count") or 0) >= 500:
            out.append(dict(base, platform="tiktok", name=u.get("nickname"), handle=h, url=f"https://www.tiktok.com/@{h}", id=u.get("sec_uid"),
                            since="2026-10-14", evidence=f"same handle as the Instagram account {h} (TikTok '{u.get('nickname')}', {u.get('follower_count')} followers)"))
    if ("x", h) not in have and "." not in h:
        d = tik("/api/v1/twitter/web/fetch_user_profile", {"screen_name": h}, 0.001)
        u = d if isinstance(d, dict) else {}
        if u.get("name") and ok(u.get("name"), r) and (u.get("sub_count") or 0) >= 200:
            out.append(dict(base, platform="x", name=u.get("name"), handle=h, url=f"https://x.com/{h}",
                            since="2026-10-14", evidence=f"same handle as the Instagram account {h} (X '{u.get('name')}', {u.get('sub_count')} followers)"))
    json.dump(out, open(B + "/samehandle.json", "w"), ensure_ascii=False, indent=1)
print(len(igs), "handles", len(out), "found; TikHub USD", round(spent[0], 3))
import collections; print(collections.Counter(o["platform"] for o in out))
for o in out: print(o["platform"], o["handle"], "|", o["name"], "|", o["owner"][:20])
