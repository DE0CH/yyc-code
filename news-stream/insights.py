"""Profiles and analytics for the dashboard and the reports (K-Matrix's "Big Data Analytics System").

Profiles = the monitoring streams in streams.json (git, read-only here) with their delivery settings (email
recipients, daily report formats, crisis alerts; table profiles, written by infra/control.py; general settings in table
settings). A profile's posts are the posts the stream scorer (streams.py) gave a score at or above the stream's
threshold (table scores; the same scored set the stream page and Telegram file from), and the posts filed in the
stream (stream_items: filed ⇒ member, Deyao 2026-10-15): members(). No keyword query
decides what belongs to a profile (Deyao, 2026-10-14); the text box on 數據分析 only narrows that set.

Analytics (all computed from the corpus on request, HKT days):
  metrics       posts, new posts, topics, new topics, websites, boards, authors, view count, per-day rates,
                website coverage, posts spread, sentiment index and balance
  scores        K-Matrix's daily email scores, YYC's formulas (documented in METHOD): Discussion Volume (DVS),
                Discussion Participation (DPS), Consumer Coverage (CCS), Consumer Sentiment (CSS) and the Social
                Popularity Index (SPI), each 0–100 with a level and the change from the day before
  topics        posts grouped into topics (forum thread / same story across outlets: title similarity)
  hot topics    topics ranked by matched posts, with K-Matrix's columns
  top authors, top sources (websites and boards), trend series, hot terms, persona

The metrics, scores, trend, top authors and sources, hot terms and profile-wide sentiment shares count posts one per
vote, so they depend on YYC's own choices (which sources it watches, the search terms it sends, feeds per outlet,
polling, which comments it fetches): wherever they are shown they carry BIAS_NOTE (Deyao, 2026-10-16; README "Figures
that depend on our selection", ROADMAP.md for weighted measures that correct for it). The web-app's
server/dashboard.ts is the same logic for the site (tests/edge_parity.py analytics).
"""
import json, math, os, re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import corpus, query

HERE = os.path.dirname(os.path.abspath(__file__))

# the 「?」 next to every figure that depends on our selection, the exports' header line, the reports' footnote, the AI
# tools' descriptions and answers (the same words as web-app/src/lib/bias.ts)
BIAS_NOTE = "這些數字取決於我們追蹤哪些來源和關鍵字，未必反映實際情況。"
BIAS_NOTE_EN = ("These figures depend on which sources and keywords we follow, so they may not reflect what is "
                "actually happening (selection bias).")

METHOD = {
    "sentiment_index": "100 × (positive − negative) ÷ (positive + neutral + negative + mixed + irony) posts, in %"
                       " (−100 to +100, as K-Matrix's dashboard); the 0–100 form is 50 + half of it.",
    "DVS": "Discussion Volume Score: the day's matched posts against the profile's average over the 30 days before:"
           " 50 at the average, 100 × r ÷ (r + 1) where r = posts ÷ average (the average counts as at least 3, so a"
           " quiet profile's few posts don't score as a surge).",
    "DPS": "Discussion Participation Score: the same formula on unique authors (posts without an author count as"
           " one author per website).",
    "CCS": "Consumer Coverage Score: the same formula on distinct websites + boards.",
    "CSS": "Consumer Sentiment Score: the 0–100 sentiment index.",
    "SPI": "Social Popularity Index: the mean of DVS, DPS and CCS (volume, people and spread: boards, topics, posts"
           " and authors, as K-Matrix describes its SPI).",
    "levels": "Very Low < 20 ≤ Low < 40 ≤ Medium < 60 ≤ High < 80 ≤ Very High",
}


def now():
    return datetime.now(corpus.HKT)


def today():
    return now().strftime("%Y-%m-%d")


def day_add(day, n):
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def days_between(a, b):
    out, d = [], a
    while d <= b:
        out.append(d)
        d = day_add(d, 1)
    return out


# ---- settings + profiles ----

DEFAULT_SETTINGS = {"report_hour": 9}


def settings():
    return dict(DEFAULT_SETTINGS, **{r["k"]: r["v"] for r in corpus.db().execute("SELECT k, v FROM settings")})


def save_settings(new):
    s = {}
    if "report_hour" in new:
        s["report_hour"] = max(0, min(23, int(new["report_hour"])))
    if "image_threshold" in new:  # images.py: a picture at or above it is meaningful
        s["image_threshold"] = max(0, min(100, int(new["image_threshold"])))
    corpus.db().executemany("INSERT INTO settings (k, v) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET v=excluded.v",
                            [(k, corpus.J(v)) for k, v in s.items()])
    return settings()


def streams():
    return json.load(open(os.path.join(HERE, "streams.json")))


def profiles():
    """every profile: the streams of streams.json"""
    out = []
    for s in streams():
        p = {"id": s["id"], "name": s["name"], "title": s.get("title", s["name"]), "kind": "stream",
             "brief": s.get("brief", ""), "search_terms": search_terms(s)}
        out.append(p)
    return out


def search_terms(s):
    """the words a stream sends to the platforms that can only be searched (Google News, 香港討論區, the social
    platforms): never used to decide what belongs to it"""
    return list(dict.fromkeys(s.get("news_queries", []) + s.get("news_queries_en", []) + s.get("discuss_queries", [])
                              + s.get("social_queries", [])))


def profile(pid):
    return next((p for p in profiles() if p["id"] == pid), None)


def threshold(pid):
    import streams as scorer
    return scorer.threshold(pid)


def members(pid, start, end):
    """the profile's posts with an HKT day in [start, end]: those the scorer gave the stream at least its threshold,
    and those filed in the stream (filed ⇒ member)"""
    return corpus.member_ids(pid, threshold(pid), start, end)


# ---- after every poll: the corpus ----

def after_poll(got, fresh):
    """called by app.py: got = every item fetched this poll, fresh = its new ones (Google News results decoded to
    the publisher's URL). Everything goes into the corpus, where streams.py scores it"""
    import fulltext_now, streams as scorer
    new = corpus.ingest([it for it in got if "news.google.com" not in it["url"]])  # an undecoded Google News link: skipped
    fulltext_now.enqueue(new)  # each new article's page, read now (the scorer waits for it)
    scorer.note_source_gaps(fresh)


# ---- selection ----

def select(pid, start, end, theme=None, sentiment=None, label=None, source_type=None, website=None, author=None,
           text=None, topic_type=None):
    """the profile's posts in [start, end] (members(): scored at or above the stream's threshold; newest first), with
    their tags, optionally narrowed by the filters and the reader's text search"""
    if not profile(pid):
        raise KeyError(pid)
    ps = corpus.posts(members(pid, start, end), profile=pid)
    if text:  # the posts the words match first, then the substring-only matches (corpus.match_ids), each newest first
        q = query.parse(text)
        order = {i: n for n, i in enumerate(corpus.match_ids(q, start, end))}
        ps = sorted((x for x in ps if x["id"] in order), key=lambda x: order[x["id"]])
        words = corpus.word_matched(q, [x["id"] for x in ps])
        for x in ps:
            x["match"] = "word" if x["id"] in words else "substring"
    if source_type:  # one type or several, comma-separated (e.g. the CGM types)
        types = set(source_type.split(","))
        ps = [x for x in ps if x.get("source_type") in types]
    for key, val in (("theme", theme), ("sentiment", sentiment), ("website", website),
                     ("author", author)):
        if val:
            ps = [x for x in ps if (x.get(key) or "") == val]
    if label:
        ps = [x for x in ps if label in x["labels"]]
    if topic_type == "new":
        tops = topics(ps)
        first = {t["posts"][-1]["id"] for t in tops}
        ps = [x for x in ps if x["id"] in first]
    return ps


# ---- topics ----

def _norm(t):
    t = re.sub(r"https?://\S+", "", t or "")
    t = re.split(r"\s[-|｜]\s", t)[0]
    return re.sub(r"[\s\W_]+", "", t.lower())


def _grams(t):
    n = _norm(t)[:80]
    return {n[i:i + 2] for i in range(len(n) - 1)} or {n}


def topics(ps, threshold=0.5):
    """group posts into topics: same thread URL or ≥ threshold Jaccard similarity of title bigrams within 3 days.
    → [{id, title, url, posts (newest first), first, last}] sorted by post count"""
    order = sorted(ps, key=lambda x: x.get("published") or x.get("seen") or "")
    tops, index = [], defaultdict(set)
    for p in order:
        g = _grams(p["title"])
        cand = Counter()
        for x in g:
            for ti in index.get(x, ()):
                cand[ti] += 1
        best, best_s = None, 0
        for ti, _ in cand.most_common(8):
            t = tops[ti]
            s = len(g & t["grams"]) / max(1, len(g | t["grams"]))
            if s > best_s:
                best, best_s = ti, s
        thread = re.sub(r"[?#].*$", "", re.sub(r"/page/\d+", "", p["url"]))
        if best is not None and best_s >= threshold:
            t = tops[best]
        else:
            same = next((ti for ti, t in enumerate(tops) if t["thread"] == thread), None)
            if same is not None:
                t = tops[same]
            else:
                t = {"i": len(tops), "grams": g, "thread": thread, "posts": [], "title": p["title"], "url": p["url"],
                     "title_by_ai": bool(p.get("title_by_ai")), "title_linked": bool(p.get("title_linked"))}
                tops.append(t)
                for x in g:
                    index[x].add(t["i"])
        t["posts"].append(p)
    out = []
    for t in tops:
        posts = sorted(t["posts"], key=lambda x: x.get("published") or x.get("seen") or "", reverse=True)
        out.append({"id": posts[-1]["id"], "title": t["title"], "title_by_ai": t["title_by_ai"], "title_linked": t["title_linked"], "url": t["url"],
                    "posts": posts,
                    "first": posts[-1].get("published") or posts[-1].get("seen"),
                    "last": posts[0].get("published") or posts[0].get("seen")})
    out.sort(key=lambda t: (len(t["posts"]), t["last"] or ""), reverse=True)
    return out


# ---- metrics ----

def sentiment_counts(ps):
    c = Counter(x.get("sentiment") for x in ps)
    return {k: c.get(k, 0) for k in ("positive", "neutral", "negative", "mixed", "irony")}


def sentiment_index(ps):
    c = sentiment_counts(ps)
    tot = sum(c.values())
    return round(100 * (c["positive"] - c["negative"]) / tot, 1) if tot else 0.0


def views(ps):
    v = [int((x.get("engagement") or {}).get("views", 0) or 0) for x in ps]
    return sum(v) if any(v) else None


def authors_of(ps):
    return {x.get("author") or ("@" + (x.get("website") or "?")) for x in ps}


def metrics(ps, start, end, prior_ids=None, total=None):
    """total: (websites, posts) of the whole corpus in the period, else counted here"""
    nd = max(1, len(days_between(start, end)))
    tops = topics(ps)
    sites = {x.get("website") for x in ps}
    boards = {(x.get("website"), x.get("board")) for x in ps if x.get("board")}
    auth = authors_of(ps)
    by_type = Counter(x.get("source_type") for x in ps)
    # grouped, not count(DISTINCT website): a hash of the ~2,000 websites, not a sort of every post's website (15 MB
    # of temp files for a week); posts_day_cover answers it from the index alone
    tot = total or corpus.db().execute(
        "SELECT count(website), coalesce(sum(n), 0)::bigint FROM (SELECT website, count(*) AS n FROM posts"
        " WHERE day BETWEEN %s AND %s GROUP BY website) w", (start, end)).fetchone()
    total_sites, total_posts = tot[0] or 1, tot[1] or 1
    c = sentiment_counts(ps)
    si = sentiment_index(ps)
    prior_ids = prior_ids or set()
    new_posts = [x for x in ps if x["id"] not in prior_ids]
    return {"posts": len(ps), "new_posts": len(new_posts), "topics": len(tops),
            "new_topics": sum(1 for t in tops if t["posts"][-1]["id"] not in prior_ids),
            "websites": len(sites), "boards": len(boards), "authors": len(auth), "view_count": views(ps),
            "by_source_type": dict(by_type), "sentiment": c, "sentiment_index": si,
            "sentiment_index_100": round(50 + si / 2, 1), "sentiment_balance": c["positive"] - c["negative"],
            "posts_per_day": round(len(ps) / nd, 1), "authors_per_day": round(len(auth) / nd, 1),
            "topics_per_day": round(len(tops) / nd, 1), "websites_per_day": round(len(sites) / nd, 1),
            "boards_per_day": round(len(boards) / nd, 1),
            "website_coverage": round(100 * len(sites) / total_sites, 1),
            "posts_spread": round(100 * len(ps) / total_posts, 2)}


def level(x):
    return "Very Low" if x < 20 else "Low" if x < 40 else "Medium" if x < 60 else "High" if x < 80 else "Very High"


def _score(v, avg):
    r = v / max(avg, 3.0)  # the average counts as at least 3, so a quiet profile's 2 posts aren't "Very High"
    return round(100 * r / (r + 1), 1)


def day_counts(pid, start, end):
    """per day: posts, authors, websites+boards, sentiment index (one pass)"""
    ps = corpus.posts(members(pid, start, end), profile=pid)
    by = defaultdict(list)
    for x in ps:
        by[x["day"]].append(x)
    out = {}
    for d in days_between(start, end):
        xs = by.get(d, [])
        out[d] = {"posts": len(xs), "authors": len(authors_of(xs)) if xs else 0,
                  "coverage": len({x.get("website") for x in xs} | {(x.get("website"), x.get("board")) for x in xs if x.get("board")}),
                  "sentiment_index": sentiment_index(xs), "topics": len(topics(xs)) if xs else 0,
                  "views": views(xs) or 0}
    return out


def first_day():
    """the corpus's first day (the scores' 30-day baseline starts no earlier)"""
    return corpus.db().execute("SELECT min(day) FROM posts").fetchone()[0]


def scores(pid, day):
    """K-Matrix's daily email scores for one day, with the change from the day before"""
    start = day_add(day, -31)
    dc = day_counts(pid, start, day)
    out = {}
    for d in (day_add(day, -1), day):
        first = first_day() or d  # no baseline before the corpus began
        base = [dc[x] for x in days_between(max(first, day_add(d, -30)), day_add(d, -1)) if x in dc]
        avg = {k: (sum(b[k] for b in base) / len(base)) if base else 0 for k in ("posts", "authors", "coverage")}
        cur = dc.get(d, {"posts": 0, "authors": 0, "coverage": 0, "sentiment_index": 0})
        dvs, dps, ccs = _score(cur["posts"], avg["posts"]), _score(cur["authors"], avg["authors"]), _score(cur["coverage"], avg["coverage"])
        css = round(50 + cur["sentiment_index"] / 2, 1) if cur["posts"] else 50.0
        out[d] = {"SPI": round((dvs + dps + ccs) / 3, 1), "CSS": css, "DVS": dvs, "DPS": dps, "CCS": ccs}
    t, y = out[day], out[day_add(day, -1)]
    res = {}
    for k in t:
        ch = round(100 * (t[k] - y[k]) / y[k], 1) if y[k] else None
        res[k] = {"value": t[k], "level": level(t[k]), "change_pct": ch}
    res["series"] = {d: {"posts": v["posts"], "sentiment_index": v["sentiment_index"]} for d, v in dc.items()}
    return res


def spi_series(pid, end, n=7):
    out = []
    for d in days_between(day_add(end, -n + 1), end):
        s = scores(pid, d)
        out.append({"day": d, "SPI": s["SPI"]["value"], "CSS": s["CSS"]["value"]})
    return out


def trend(pid, start, end, bucket="day"):
    """exposure & mentioning trend: posts, views and sentiment index per day or ISO week"""
    dc = day_counts(pid, start, end)
    if bucket == "day":
        return [{"period": d, **v} for d, v in dc.items()]
    weeks = defaultdict(lambda: {"posts": 0, "views": 0, "pos": 0, "authors": 0})
    ps = corpus.posts(members(pid, start, end), profile=pid)
    by = defaultdict(list)
    for x in ps:
        d = datetime.strptime(x["day"], "%Y-%m-%d")
        by[(d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")].append(x)
    out = []
    for d in days_between(start, end):
        dt = datetime.strptime(d, "%Y-%m-%d")
        if dt.weekday() == 0 or d == start:
            wk = (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
            xs = by.get(wk, [])
            out.append({"period": wk, "posts": len(xs), "views": views(xs) or 0, "authors": len(authors_of(xs)) if xs else 0,
                        "sentiment_index": sentiment_index(xs), "topics": len(topics(xs)) if xs else 0})
    return out


def hot_topics(pid, start, end, top=10, ps=None):
    """K-Matrix's Hot Topic Listing"""
    ps = ps if ps is not None else select(pid, start, end)
    tops = topics(ps)
    total = max(1, len(ps))
    rows = []
    for t in tops[:top]:
        xs = t["posts"]
        all_posts = _all_post_count(t)
        si = sentiment_index(xs)
        c = sentiment_counts(xs)
        first, last = t["first"] or "", t["last"] or ""
        try:
            span = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).days + 1
        except Exception:
            span = 1
        lead = xs[-1]
        rows.append({"id": t["id"], "title": t["title"], "title_by_ai": t.get("title_by_ai"),
                     "title_linked": t.get("title_linked"), "url": t["url"],
                     "website": lead.get("website"),
                     "board": lead.get("board"), "author": lead.get("author"), "source_type": lead.get("source_type"),
                     "region": lead.get("region"), "sentiment_index": si, "sentiment_score": c["positive"] - c["negative"],
                     "sentiment_level": sentiment_level(si), "contribution": round(100 * len(xs) / total, 1),
                     "all_post_count": all_posts, "period_post_count": len(xs), "matched_post_count": len(xs),
                     "participants": len(authors_of(xs)), "view_count": views(xs),
                     "correlation_index": round(100 * len(xs) / max(1, all_posts), 1),
                     "initial_date": first[:10], "start_date": first[:10], "end_date": last[:10], "day_span": span,
                     "websites": sorted({x.get("website") or "" for x in xs}), "post_ids": [x["id"] for x in xs]})
    return rows


_ASCII_WORD = re.compile(r"[a-z0-9]+")


def _all_post_lookup(title):
    """the index lookup that narrows a topic's all-posts count, from the title in its search form → (sql, [tsquery]).
    The grams index (every character pair of the title's first 12 characters), as query.prefilter does. An English
    title's pairs are in nearly every English post (the lookup of "speech by ce" alone ran past 30 s, 13 Nov 2026), so
    a prefix of only ASCII is looked up by its whole words instead (the words index): each run of letters and digits
    that is not cut by the 12 characters. Such a run is one word of any post holding the prefix, except a post where
    the prefix starts mid-word: the count may differ by a post or two (Deyao, 2026-11-13)."""
    term = title[:12]
    if term.isascii():
        ws = [m.group() for m in _ASCII_WORD.finditer(term)
              if m.end() < len(term) or not _ASCII_WORD.match(title[len(term):len(term) + 1])]
        if ws:
            return "(words @@ %s::tsquery OR grams IS NULL)", [" & ".join(query._tsq(w) for w in dict.fromkeys(ws))]
    return "(grams @@ %s::tsquery OR grams IS NULL)", [" & ".join(query._tsq(g) for g in query._pairs(term))]


def _all_post_count(t):
    """every post in the corpus on this topic (not only the profile's matches): same normalised title prefix"""
    key = _norm(t["title"])[:12]
    title = query.searchtext.norm(t["title"])
    term = title[:12]
    if len(key) < 6 or len(term) < 2:
        return len(t["posts"])
    # hay is the search form (searchtext.norm: half-width, Simplified, lower case), and an index narrows the substring
    # test (_all_post_lookup): a plain strpos read all of posts, 20-40 s a topic on 23 Oct 2026, so the daily reports
    # ran past their CronJob's hour
    where, params = _all_post_lookup(title)
    n = corpus.db().execute(f"SELECT count(*) FROM posts WHERE {where} AND strpos(hay, %s) > 0", (*params, term)).fetchone()[0]
    return max(n, len(t["posts"]))


def sentiment_level(si):
    return ("Very Negative" if si <= -50 else "Slightly Negative" if si < -5 else "Neutral" if si <= 5
            else "Slightly Positive" if si < 50 else "Very Positive")


def top_authors(pid, start, end, top=20, ps=None):
    ps = ps if ps is not None else select(pid, start, end)
    by = defaultdict(list)
    for x in ps:
        if x.get("author"):
            by[(x["author"], x.get("website"))].append(x)
    rows = []
    for (a, site), xs in by.items():
        boards = Counter(x.get("board") or "" for x in xs)
        rows.append({"author": a, "website": site, "posts": len(xs), "topics": len(topics(xs)),
                     "sentiment_index": sentiment_index(xs), "sentiment_level": sentiment_level(sentiment_index(xs)),
                     "contribution": round(100 * len(xs) / max(1, len(ps)), 1), "view_count": views(xs),
                     "most_active_board": boards.most_common(1)[0][0] if boards else ""})
    rows.sort(key=lambda r: r["posts"], reverse=True)
    return rows[:top]


def top_sources(pid, start, end, top=20, ps=None, by="website"):
    ps = ps if ps is not None else select(pid, start, end)
    groups = defaultdict(list)
    for x in ps:
        key = x.get("website") if by == "website" else (x.get("website"), x.get("board") or "")
        groups[key].append(x)
    rows = []
    for k, xs in groups.items():
        site, board = (k, None) if by == "website" else k
        rows.append({"website": site, "board": board, "source_type": xs[0].get("source_type"), "region": xs[0].get("region"),
                     "posts": len(xs), "topics": len(topics(xs)), "participants": len(authors_of(xs)),
                     "sentiment_index": sentiment_index(xs), "sentiment_level": sentiment_level(sentiment_index(xs)),
                     "contribution": round(100 * len(xs) / max(1, len(ps)), 1)})
    rows.sort(key=lambda r: r["posts"], reverse=True)
    return rows[:top]


STOP = set("的了是在和與及也都就而及其為以於之或但這那有一個我們你他她它不人會要說對到被將從並等中上下來去".strip())
CJK = re.compile(r"[一-鿿]{2,}")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{2,}")
LATIN_STOP = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "has", "have", "will", "https", "http",
              "www", "com", "not", "you", "your", "its", "our", "but", "all", "more"}


def hot_terms(pid, start, end, top=60, ps=None, background=None):
    """K-Matrix's Smart Hot Terms / word cloud: the profile's most frequent 2–4 character Chinese terms and words,
    relative to their frequency in the whole corpus over the same days (so common words drop out): a random 3000 of
    the period's titles, or `background` (titles; tests/edge_parity.py gives both sides the same sample)"""
    ps = ps if ps is not None else select(pid, start, end)
    p = profile(pid)
    qterms = {t.lower() for w in (p["search_terms"] if p else []) for t in w.split()}
    def grams(text):
        out = set()
        for run in CJK.findall(text):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    g = run[i:i + n]
                    if not (set(g) & STOP and n == 2):
                        out.add(g)
        out |= {w.lower() for w in LATIN.findall(text) if w.lower() not in LATIN_STOP}
        return out
    mine = Counter()
    for x in ps:
        mine.update(grams(x["title"] + " " + (x.get("text") or "")[:500]))
    if not mine:
        return []
    bg = Counter()
    rows = [(t,) for t in background] if background is not None else corpus.db().execute(
        "SELECT title FROM posts WHERE day BETWEEN %s AND %s ORDER BY random() LIMIT 3000", (start, end)).fetchall()
    for r in rows:
        bg.update(grams(r[0]))
    nbg = max(1, len(rows))
    scored = []
    for g, n in mine.items():
        if n < 2 or any(g.lower() in t for t in qterms):  # the stream's own search terms and their pieces
            continue
        lift = (n / len(ps)) / ((bg.get(g, 0) + 1) / nbg)
        if lift < 2:  # about as common everywhere: not a hot term for this profile
            continue
        scored.append((n * math.log(lift), g, n))
    scored.sort(reverse=True)
    picked = []
    for s, g, n in scored:
        if any(g in q and len(q) > len(g) and mine[q] >= n * 0.8 for _, q, _ in picked):
            continue  # a shorter piece of a longer term that has about the same count
        picked = [x for x in picked if not (x[1] in g and mine[g] >= x[2] * 0.8)]
        picked.append((s, g, n))
        if len(picked) >= top:
            break
    return [{"term": g, "count": n, "weight": round(s, 2)} for s, g, n in picked]


def persona(pid, author, start, end):
    """an author's history: every corpus post by them (not only matches), their boards, topics and sentiment"""
    rows = corpus.db().execute("SELECT id FROM posts WHERE author=%s AND day BETWEEN %s AND %s ORDER BY coalesce(published,seen) DESC, id DESC",
                               (author, day_add(start, -365), end)).fetchall()
    allp = corpus.posts([r[0] for r in rows], profile=pid)
    matched = [x for x in allp if x.get("relevant")]
    boards = Counter(f"{x.get('website')}{' - ' + x['board'] if x.get('board') else ''}" for x in allp)
    sites = Counter(x.get("website") for x in allp)
    return {"author": author, "posts": len(allp), "matched_posts": len(matched),
            "sentiment_index_on_profile": sentiment_index(matched), "sentiment": sentiment_counts(matched),
            "boards": [{"board": k, "posts": v} for k, v in boards.most_common(10)],
            "websites": [{"website": k, "posts": v} for k, v in sites.most_common(10)],
            "first_seen": (allp[-1].get("published") or allp[-1].get("seen")) if allp else None,
            "last_seen": (allp[0].get("published") or allp[0].get("seen")) if allp else None,
            "hot_terms": hot_terms(pid, start, end, top=30, ps=allp) if allp else [],
            "recent": allp[:100]}

def summary_view(pid, start, end):
    """the dashboard's Summary tab: trend + metrics split CGM / News / Analysis"""
    ps = select(pid, start, end)
    prior = set(members(pid, day_add(start, -30), day_add(start, -1)))
    m = metrics(ps, start, end, prior)
    cgm = [x for x in ps if x.get("source_type") in ("Forum", "Social Media", "Video", "Blog")]
    news = [x for x in ps if x.get("source_type") in corpus.NEWS_TYPES]
    mc = metrics(cgm, start, end, prior)
    mn = metrics(news, start, end, prior)
    return {"profile": profile(pid), "start": start, "end": end, "metrics": m, "cgm": mc, "news": mn,
            "trend": trend(pid, start, end, "day" if len(days_between(start, end)) <= 45 else "week"),
            "method": METHOD}



# ---- what an AI summary read (the daily summary, a persona), listed under it on 數據分析 ----
# kv "ctx:<id>": {kind, profile, title, label, summary (markdown), ids (the posts the model was shown, newest first),
# total (posts in the scope), note (the plain-language line shown to users), at}; the edge app reads it
# (web-app/src/server/dashboard.ts). The id is a hash of the content, so the same summary always gets the same id.

def read_note(n, total):
    return f"AI 讀了全部 {total} 篇帖子" if n >= total else f"AI 讀了其中 {n} 篇（共 {total} 篇，最新的優先）"


def make_context(kind, pid, title, label, summary, read, total, note=None):
    import hashlib
    ids = [p["id"] for p in read]
    cid = hashlib.sha256(json.dumps([kind, pid, label, ids, summary], ensure_ascii=False).encode()).hexdigest()[:24]
    if not corpus.kv_get("ctx:" + cid):
        corpus.kv_set("ctx:" + cid, {"kind": kind, "profile": pid, "title": title, "label": label, "summary": summary,
                                     "ids": ids, "total": total, "note": note or read_note(len(ids), total),
                                     "at": now().isoformat(timespec="seconds")})
    return cid
