"""Merge new rows into news-stream/watchlist.json (dedupe by watchlist.key; existing rows win)."""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "")
import watchlist
B = os.environ.get("WATCHLIST_BUILD", os.path.expanduser("~/artifacts/watchlist/build"))
P = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "/watchlist.json"
rows = json.load(open(P))
seen = {watchlist.key(r) for r in rows}
fbseen = {r["url"].rstrip("/").lower() for r in rows if r["platform"] == "facebook"}
added = 0
for f in sys.argv[1:]:
    for r in json.load(open(f)):
        r = {k: v for k, v in r.items() if v not in (None, "", [])}
        if r["platform"] == "youtube" and not r.get("id"):
            continue
        k = watchlist.key(r)
        if k in seen or (r["platform"] == "facebook" and r["url"].rstrip("/").lower() in fbseen):
            continue
        seen.add(k); added += 1
        order = ["group", "name", "owner", "role", "platform", "handle", "url", "id", "why", "evidence", "since"]
        rows.append({k2: r[k2] for k2 in order if k2 in r} | {k2: v for k2, v in r.items() if k2 not in order})
rows.sort(key=lambda r: (watchlist.GROUPS.index(r["group"]), r.get("owner") or r["name"], watchlist.PLATFORMS.index(r["platform"])))
probs = watchlist.problems(rows)
if probs:
    print("\n".join(probs[:20])); sys.exit(1)
json.dump(rows, open(P, "w"), ensure_ascii=False, indent=1); open(P, "a").write("\n")
import collections
print("added", added, "total", len(rows))
for g in watchlist.GROUPS:
    print(g, dict(collections.Counter(r["platform"] for r in rows if r["group"] == g)))
