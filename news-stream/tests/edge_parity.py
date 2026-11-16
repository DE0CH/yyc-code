"""Parity of the edge app's data functions (web-app/src/server/*.ts, run in Node by web-app/scripts/call.ts) with the
Python modules the cluster keeps using, on a copy of real data: PARITY_DATABASE_URL (e.g. postgresql:///yyc_dev_spa).

    python3 tests/edge_parity.py item [n]            item.view (no coverage), coverage and item.text for n posts (stream
                                                     items' posts, comments, social posts): the item page's port
                                                     against what the agent's tools and the MCP server read
    python3 tests/edge_parity.py searchtext [n]      searchtext norm / variants / tokens / tsquery of n query terms
                                                     (streams' queries, search_dict.txt, title substrings, English,
                                                     mixed, Simplified, HK variants, odd characters)
    python3 tests/edge_parity.py search [n] [days]   搜尋's split statement against the one query (page, total, facets,
                                                     export) for n terms and more over the last [days] days: one and
                                                     two terms, Latin, type/site/region filters, oldest, past the end
    python3 tests/edge_parity.py monitor             /app/monitor/data (kv monitor:data; ages within the call's time)
    python3 tests/edge_parity.py analytics [days]    數據分析's figures that depend on our selection (摘要 metrics and
                                                     trend, the daily scores and their 7-day series, 趨勢 by day and
                                                     week, 作者, 來源 by website and board, hot terms, a topic's top
                                                     authors and sources, persona's hot terms) against insights.py,
                                                     which the daily reports and the data API's tools keep using: every
                                                     profile, the last 1, 7 and 30 days and a period ending [days] ago

The pages' other data (串流's feed, 數據分析, 搜尋, 報告) exist only as the edge app's TypeScript.
Prints each difference (path: python ≠ edge) and exits 1 when any. Run while changing either side.
"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "web-app")
os.environ["DATABASE_URL"] = os.environ.get("PARITY_DATABASE_URL", "postgresql:///yyc_dev_spa")
os.environ.setdefault("FLAGS_FILE", os.path.join(HERE, "..", "deploy", "apps", "flags", "flags.json"))
sys.path.insert(0, os.path.join(HERE, ".."))


def edge(fn, *args):
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql:///"):
        url = "postgres://claude:dev@127.0.0.1:5432/" + url.rsplit("/", 1)[1]
    r = subprocess.run(["npx", "tsx", "scripts/call.ts", fn, json.dumps(list(args))], cwd=APP, capture_output=True,
                       text=True, env=dict(os.environ, DATABASE_URL=url), timeout=900)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout)


def edge_big(fn, *args):
    """edge() for arguments too long for a command line: through call.ts --batch on stdin"""
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql:///"):
        url = "postgres://claude:dev@127.0.0.1:5432/" + url.rsplit("/", 1)[1]
    r = subprocess.run(["npx", "tsx", "scripts/call.ts", "--batch"], cwd=APP, capture_output=True, text=True,
                       input=json.dumps([[fn, list(args)]]), env=dict(os.environ, DATABASE_URL=url), timeout=900)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout)[0]


def norm(v):
    return json.loads(json.dumps(v, ensure_ascii=False, default=str))


def diff(a, b, path="", out=None, limit=40):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: {'missing in python' if k not in a else 'missing in edge'}")
            else:
                diff(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} items ≠ {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b and not (isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) < 1e-9):
        out.append(f"{path}: {str(a)[:120]!r} ≠ {str(b)[:120]!r}")
    return out


def sample(n):
    import corpus
    c = corpus.db()
    ids = [r[0] for r in c.execute("SELECT p.id FROM posts p JOIN stream_items si ON si.url = p.url ORDER BY p.id DESC LIMIT %s", (n,))]
    ids += [r[0] for r in c.execute("SELECT id FROM posts WHERE thread IS NOT NULL ORDER BY id DESC LIMIT %s", (max(2, n // 4),))]
    ids += [r[0] for r in c.execute("SELECT id FROM posts WHERE source_type IN ('Social Media','Video','Forum') AND thread IS NULL ORDER BY id DESC LIMIT %s", (max(2, n // 4),))]
    ids += [r[0] for r in c.execute("SELECT post_id FROM post_media LIMIT 3")] + [r[0] for r in c.execute("SELECT post_id FROM post_images LIMIT 3")]
    ids += [r[0] for r in c.execute("SELECT post_id FROM thin_posts LIMIT 3")]
    return list(dict.fromkeys(ids))


def item(n=20):
    import item as it
    out = []
    for pid in sample(int(n)):
        py = norm(it.view(pid))
        cov = py.pop("coverage")
        out += diff(py, edge("item.view", pid), f"item[{pid}]")
        out += diff(cov, edge("item.itemCoverage", pid), f"coverage[{pid}]")
        out += diff(norm(it.text(pid)), edge("item.text", pid), f"text[{pid}]")
    return out


def search_terms(n=3000, seed=7):
    """query terms the way people type them: the streams' queries, search_dict.txt entries (as written, Simplified, HK
    variants), random substrings of real titles, English, mixed, punctuation and odd characters"""
    import random, re, corpus, searchtext, opencc
    rnd = random.Random(seed)
    out = []
    streams = json.load(open(searchtext.STREAMS, encoding="utf-8"))
    for s in (streams if isinstance(streams, list) else streams.get("streams", [])):
        for key in ("news_queries", "discuss_queries", "social_queries", "keywords"):
            for q in s.get(key) or []:
                if isinstance(q, str):
                    out.append(q)
    words = [w for w, _ in searchtext._dict_words()]
    out += words
    s2s, t2s = opencc.OpenCC("t2s"), opencc.OpenCC("s2hk")
    out += [s2s.convert(w) for w in rnd.sample(words, min(150, len(words)))]
    out += [t2s.convert(s2s.convert(w)) for w in rnd.sample(words, min(100, len(words)))]
    out += ["裡", "裏", "綫", "線", "衞", "衛", "着", "著", "爲", "為", "温", "溫", "峯", "峰", "麪", "麵", "啓", "啟"]
    titles = [r[0] for r in corpus.db().execute("SELECT title FROM posts WHERE title IS NOT NULL ORDER BY id DESC LIMIT 12000")]
    texts = [r[0] for r in corpus.db().execute("SELECT left(text, 400) FROM posts WHERE text IS NOT NULL ORDER BY id DESC LIMIT 3000")]
    for t in rnd.sample(titles, min(len(titles), 3500)) + rnd.sample(texts, min(len(texts), 1200)):
        cps = list(t)
        if not cps:
            continue
        a = rnd.randrange(len(cps))
        out.append("".join(cps[a:a + rnd.randint(1, 12)]))
    out += rnd.sample(titles, min(len(titles), 400)) + rnd.sample(texts, min(len(texts), 100))  # whole titles and texts
    en = [w for t in titles for w in re.findall(r"[A-Za-z][A-Za-z0-9'.-]*", t)]
    out += rnd.sample(en, min(300, len(en)))
    out += [" ".join(rnd.sample(en, 2)) for _ in range(60)]
    out += ["HSBC 滙豐", "iPhone17 發佈", "Ｈｏｎｇ Ｋｏｎｇ", "ＡＩ人工智能", "5G網絡", "C++", "#港投", "100%", "e-sports",
            "COVID-19", "Ⅻ", "①②", "ΑΣ", "İstanbul", "Straße", "𠝹", "𡃁妹", "⿰木木", "café", "naïve", "“引號”", "a\tb",
            "it's", "O'Reilly", "  空格  ", "　全形空格", "中​文", "😀表情", "１２３", "ＡＢＣ", "ｶﾀｶﾅ", "㍿", "㈱",
            "港投公司", "新資本投資者入境計劃", "李家超施政報告", "立法會選舉2025", "東涌綫", "将军澳线", "x" * 70, "選" * 70]
    out = [t for t in dict.fromkeys(out) if t]
    rnd.shuffle(out)
    return out[:int(n)] if int(n) < len(out) else out


def searchtext_parity(n=3000):
    import searchtext
    terms = search_terms(n)
    py = []
    for t in terms:
        nm = searchtext.norm(t)
        py.append({"term": t, "norm": nm, "variants": searchtext.variants(t), "tokens": searchtext.tokens(nm),
                   "tsquery": searchtext.tsquery(nm), "tsquery_a": searchtext.tsquery(nm, title_only=True)})
    out = []
    for i in range(0, len(terms), 1000):
        out += diff(norm(py[i:i + 1000]), edge("search/searchtext.parity", terms[i:i + 1000]), f"terms[{i}:]")
    print(f"{len(terms)} terms", file=sys.stderr)
def search_split(n=40, days=30):
    """搜尋's split statement (web-app/src/server/search/split.ts) against the one query on real data: page, total and
    facets of one and two terms, Latin terms (post_subs or the grams test, as kv search:subs says), type/site/region
    filters, oldest first, a page past the end and no match; the export's rows for the first few"""
    import random, re, corpus, insights
    rnd = random.Random(11)
    end = insights.today()
    start = insights.day_add(end, -int(days) + 1)
    terms = [t for t in search_terms(3000) if re.fullmatch(r"[\w㐀-鿿]{2,8}", t)]
    cjk = [t for t in terms if not re.search(r"[A-Za-z]", t)]
    latin = [t for t in terms if re.fullmatch(r"[A-Za-z0-9]{3,12}", t)]
    site, stype, region = corpus.db().execute("SELECT website, source_type, region FROM posts WHERE day BETWEEN %s AND %s "
                                              "AND website IS NOT NULL AND region IS NOT NULL ORDER BY id DESC LIMIT 1",
                                              (start, end)).fetchone()
    base = {"from": [start], "to": [end]}
    qs = [dict(base, q=[t]) for t in rnd.sample(cjk, min(len(cjk), int(n)))]
    qs += [dict(base, q=[f"{a} {b}"]) for a, b in zip(rnd.sample(cjk, 10), rnd.sample(cjk, 10))]
    qs += [dict(base, q=[t]) for t in rnd.sample(latin, min(len(latin), 10))]
    one = cjk[0]
    qs += [dict(base, q=[one], type=[stype]), dict(base, q=[one], site=[site]), dict(base, q=[one], region=[region]),
           dict(base, q=[one], sort=["oldest"]), dict(base, q=[one], page=["9999"]), dict(base, q=["冇呢個詞zzqx"]),
           dict(base, q=[one], comments=["1"])]
    out = []
    for i in range(0, len(qs), 20):
        part = qs[i:i + 20]
        split, single = edge("search/search.runMany", part, True), edge("search/search.runMany", part, False)
        for q, a, b in zip(part, split, single):
            out += diff(b, a, f"search[{q}]")
    for q in qs[:5]:
        out += diff(edge("search/search.exportRows", q, False)["rows"], edge("search/search.exportRows", q, True)["rows"], f"export[{q}]")
    print(f"{len(qs)} searches", file=sys.stderr)
    return out


def monitor_data():
    import time, monitor
    t0 = time.time()
    py, ts = norm(monitor.published()), edge("monitor.published")
    took = time.time() - t0
    for k in monitor.PANELS:  # the ages grow between the two reads (the Node call takes seconds to start)
        a, b = py[k].pop("age_seconds", None), ts[k].pop("age_seconds", None)
        if (a is None) != (b is None) or (a is not None and not (a - 0.2 <= b <= a + took + 0.2)):
            return [f"monitor.{k}.age_seconds: {a} ≠ {b}"]
    py.pop("at"), ts.pop("at")
    return diff(py, ts, "monitor")


def analytics(back=3):
    """數據分析's restored figures: the edge app's dashboard.ts / analytics.ts against insights.py on the same copy of the
    data. Hot terms compare on one background sample given to both (each reads its own random 3000 titles otherwise),
    with the texts the edge reads checked on the first 50 posts."""
    import corpus, insights
    out, today = [], insights.today()
    periods = [(today, today), (insights.day_add(today, -6), today), (insights.day_add(today, -29), today),
               (insights.day_add(today, -int(back) - 6), insights.day_add(today, -int(back)))]
    for p in insights.profiles():
        pid = p["id"]
        for start, end in periods:
            q = {"p": pid, "from": start, "to": end}
            at = f"{pid}[{start}..{end}]"
            ps = insights.select(pid, start, end)
            sv = norm(insights.summary_view(pid, start, end))
            ed = edge("dashboard.summaryApi", q)
            out += diff({k: sv[k] for k in ("start", "end", "metrics", "cgm", "news", "trend", "method")},
                        {k: ed.get(k) for k in ("start", "end", "metrics", "cgm", "news", "trend", "method")}, f"summary{at}")
            out += diff(norm({"scores": insights.scores(pid, end), "spi_series": insights.spi_series(pid, end, 7)}),
                        {k: v for k, v in edge("dashboard.scoresApi", {"p": pid, "day": end, "n": "7"}).items() if k != "note"},
                        f"scores{at}")
            for bucket in ("day", "week"):
                out += diff(norm(insights.trend(pid, start, end, bucket)),
                            edge("dashboard.trendApi", dict(q, bucket=bucket))["trend"], f"trend.{bucket}{at}")
            out += diff(norm(insights.top_authors(pid, start, end, 50, ps)), edge("dashboard.authorsApi", q)["authors"], f"authors{at}")
            for by in ("website", "board"):
                out += diff(norm(insights.top_sources(pid, start, end, 50, ps, by=by)),
                            edge("dashboard.sourcesApi", dict(q, by=by))["sources"], f"sources.{by}{at}")
            # hot terms: both sides on the same background sample (insights.hot_terms reads a random 3000 titles)
            bg = [r[0] for r in corpus.db().execute(
                "SELECT title FROM posts WHERE day BETWEEN %s AND %s ORDER BY random() LIMIT 3000", (start, end))]
            cfg = {"profiles": [{"id": pid, "search_terms": p["search_terms"]}]}
            rows = [{"title": x["title"], "text": x.get("text")} for x in ps]
            out += diff(norm(insights.hot_terms(pid, start, end, 60, ps, background=bg)),
                        edge_big("analytics.hotTerms", cfg, pid, start, end, 60, rows, bg), f"terms{at}")
            page = edge("dashboard.postsApi", dict(q, n="50"))["posts"]  # and the texts the edge reads for them
            texts = {x["id"]: x.get("text") for x in ps}
            out += diff([texts.get(x["id"]) for x in page], [x["text"] for x in page], f"texts{at}")
            tops = insights.topics(ps)
            for t in tops[:2]:
                xs = t["posts"]
                et = edge("dashboard.topicApi", dict(q, id=str(t["id"])))
                out += diff(norm(insights.top_authors(pid, start, end, 10, xs)), et["top_authors"], f"topic{t['id']}.authors{at}")
                out += diff(norm(insights.top_sources(pid, start, end, 10, xs)), et["top_sources"], f"topic{t['id']}.sources{at}")
            if (start, end) == periods[1]:
                authors = [a["author"] for a in insights.top_authors(pid, start, end, 3, ps)]
                for a in authors:
                    rows = corpus.db().execute("SELECT id FROM posts WHERE author=%s AND day BETWEEN %s AND %s",
                                               (a, insights.day_add(start, -365), end)).fetchall()
                    allp = corpus.posts([r[0] for r in rows], profile=pid)
                    out += diff(norm(insights.hot_terms(pid, start, end, 30, allp, background=bg)),
                                edge_big("analytics.hotTerms", cfg, pid, start, end, 30,
                                     [{"title": x["title"], "text": x.get("text")} for x in allp], bg), f"persona[{a}].terms{at}")
        print(pid, len(out), file=sys.stderr)
    return out


if __name__ == "__main__":
    what, *args = sys.argv[1:] or ["item"]
    d = {"item": item, "searchtext": searchtext_parity, "search": search_split, "monitor": monitor_data,
         "analytics": analytics}[what](*args)
    print("\n".join(d) or "same")
    sys.exit(1 if d else 0)
