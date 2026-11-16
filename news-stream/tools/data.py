"""The free tools: YYC's own data (the corpus, profiles, statistics, reports, the WiseSearch log, the social search).
The statistics (stats_overview, authors_top, sources_top, trend) say in their description and in every answer that
they depend on which sources and keywords YYC follows (insights.BIAS_NOTE_EN), so an AI answer built on them says so.
Descriptions are written for a model: what the tool returns, when to use it, how its arguments read."""
import re

import ai, corpus, insights, query
import dashboard_url as dl
from . import tool

DATE = {"type": "string", "description": "YYYY-MM-DD (Hong Kong time)"}
PROFILE = {"type": "string", "description": "profile id from profiles_list, e.g. CEHK"}
# what the descriptions tell the model about dashboard_url
CITE = (" Results carry dashboard_url: the item's details page on YYC's monitoring site (https://newswatch.yyc-internal.com); "
        "link it when you cite or mention the item.")
FORMAT = {"type": "string", "enum": ["concise", "detailed"], "default": "concise",
          "description": "concise (default): titles, sources, times, links; detailed: also text extracts, labels, engagement"}


def _period(a, default=7):
    end = a.get("to") or insights.today()
    start = a.get("from") or insights.day_add(end, -default + 1)
    for k, v in (("from", start), ("to", end)):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ValueError(f"{k} must be YYYY-MM-DD, e.g. {insights.today()}")
    if start > end:
        raise ValueError(f"from ({start}) is after to ({end})")
    return start, end


def _profile(pid):
    p = insights.profile(pid)
    if not p:
        raise ValueError(f"no profile {pid!r}; profiles: " + ", ".join(f"{x['id']} ({x.get('title') or x['name']})" for x in insights.profiles()))
    return p


def post_view(x, detailed=False):
    """a post as the model needs it: readable fields and the links to cite (its item page and the original)"""
    v = {"id": x["id"], "title": (x.get("title") or "")[:200], "source": x.get("website"), "author": x.get("author"),
         "time": x.get("published") or x.get("seen"), "sentiment": x.get("sentiment"), "item": f"/item/{x['id']}",
         "dashboard_url": dl.post_url(x["id"]), "url": x.get("url")}
    if detailed:
        v.update(text=(x.get("text") or "")[:400] or None, board=x.get("board"), type=x.get("source_type"),
                 labels=x.get("labels") or None, engagement=x.get("engagement") or None)
    return {k: w for k, w in v.items() if w not in (None, "", [])}


@tool("profiles_list",
      "The monitoring profiles (subjects YYC tracks): id, title, what each covers and the score threshold its posts "
      "passed (an AI scores every post 0-100 against each profile's scope; no keyword query). Use it to map a "
      "person, company or topic the user names to a profile id; a subject no profile covers can still be searched with "
      "posts_search without a profile. dashboard_url: the profile's 數據分析 page.")
def profiles_list(a, ctx):
    return {"profiles": [{"id": p["id"], "title": p.get("title") or p["name"], "kind": p.get("kind"),
                          "covers": re.sub(r"\s+", " ", p.get("brief") or "")[:400] or None,
                          "threshold": insights.threshold(p["id"]), "dashboard_url": dl.dashboard_url(p=p["id"])}
                         for p in insights.profiles()]}


@tool("posts_search",
      "Search the posts YYC collected (news, forums, social media). With a profile: that profile's posts (scored at or above its threshold), "
      "optionally narrowed by query and filters. Without a profile: keyword search over every collected post. Newest "
      "first, paged (limit/offset); 'total' says how many match. Cite posts by their item link or url." + CITE,
      {"profile": PROFILE,
       "query": {"type": "string", "description": "keywords: words or \"phrases\"; whitespace = OR; AND, NOT, ( ) "
                                                 "allowed. Required when there is no profile."},
       "from": DATE, "to": DATE,
       "sentiment": {"type": "string", "enum": list(ai.SENTIMENTS)},
       "label": {"type": "string", "enum": list(ai.LABELS)},
       "source_type": {"type": "string", "enum": list(query.SOURCE_TYPES)},
       "website": {"type": "string", "description": "a source's name as it appears in results, e.g. LIHKG討論區"},
       "author": {"type": "string"},
       "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
       "offset": {"type": "integer", "minimum": 0, "default": 0},
       "response_format": FORMAT})
def posts_search(a, ctx):
    start, end = _period(a)
    lim, off = int(a.get("limit") or 20), int(a.get("offset") or 0)
    if a.get("profile"):
        _profile(a["profile"])
        ps = insights.select(a["profile"], start, end, sentiment=a.get("sentiment"), label=a.get("label"),
                             source_type=a.get("source_type"), website=a.get("website"), author=a.get("author"),
                             text=a.get("query"))
    else:
        if not (a.get("query") or "").strip():
            raise ValueError("give a profile or a query (a search over every post needs keywords)")
        ids = corpus.match_ids(query.parse(a["query"]), start, end,
                               {"source_types": [a["source_type"]] if a.get("source_type") else None,
                                "websites": [a["website"]] if a.get("website") else None}, limit=(off + lim) * 3 + 50)
        ps = corpus.posts(ids)
        if a.get("author"):
            ps = [x for x in ps if x.get("author") == a["author"]]
        if a.get("sentiment") or a.get("label"):
            ps = [x for x in ps if (not a.get("sentiment") or x.get("sentiment") == a["sentiment"])
                  and (not a.get("label") or a["label"] in (x.get("labels") or []))]
    page = ps[off:off + lim]
    out = {"total": len(ps), "period": {"from": start, "to": end}, "showing": f"{off + 1}-{off + len(page)}" if page else "none",
           "posts": [post_view(x, a.get("response_format") == "detailed") for x in page]}
    if off + lim < len(ps):
        out["more"] = f"{len(ps) - off - lim} more: call again with offset={off + lim}"
    return out


@tool("posts_get",
      "One post in full: its whole stored text, source, author, time, sentiment and labels per profile, engagement, the "
      "comments YYC collected and which streams carried it. Use it to read a post before quoting it." + CITE,
      {"id": {"type": "integer", "description": "the post's id (from posts_search; the number in /item/<id>)"}}, ["id"])
def posts_get(a, ctx):
    import item
    try:
        v, t = item.view(a["id"]), item.text(a["id"])
    except LookupError:
        raise ValueError(f"no post {a['id']}; take ids from posts_search")
    keep = ("id", "title", "url", "website", "board", "author", "source_type", "region", "published", "engagement",
            "streams", "thread_url")
    out = {k: v.get(k) for k in keep if v.get(k) not in (None, "", [], {})}
    out.update(item=f"/item/{v['id']}", dashboard_url=dl.post_url(v["id"]), text=(t.get("text") or "")[:8000] or None,
               tags=[{"profile": r.get("profile"), "relevant": r.get("relevant"), "sentiment": r.get("sentiment"),
                      "labels": r.get("labels")} for r in v.get("tags") or []],
               comments=[(c or "")[:500] for c in list((t.get("comments") or {}).values())[:30]] or None)
    return {k: w for k, w in out.items() if w not in (None, "", [])}


@tool("stats_overview",
      "A profile's numbers for a period: posts, new posts, authors, websites, topics, the sentiment index and balance, "
      "per-day rates, news vs user-generated content, and the K-Matrix-style daily scores (SPI, CSS, DVS, DPS, CCS) "
      "on the last day. " + insights.BIAS_NOTE_EN + " Say so whenever you use them.",
      {"profile": PROFILE, "from": DATE, "to": DATE}, ["profile"])
def stats_overview(a, ctx):
    _profile(a["profile"])
    start, end = _period(a)
    s = insights.summary_view(a["profile"], start, end)
    sc = insights.scores(a["profile"], end)
    sc.pop("series", None)
    return {"period": {"from": start, "to": end}, "dashboard_url": dl.dashboard_url(p=a["profile"], from_=start, to=end),
            "metrics": s["metrics"],
            "user_generated": {k: s["cgm"][k] for k in ("posts", "authors", "topics", "websites")},
            "news": {k: s["news"][k] for k in ("posts", "websites")}, "scores_on_last_day": sc,
            "note": insights.BIAS_NOTE_EN}


@tool("topics_hot",
      "A profile's hot topics in a period: posts grouped by story or thread, biggest first, with post count, people "
      "taking part, sentiment index and dates." + CITE,
      {"profile": PROFILE, "from": DATE, "to": DATE, "top": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10}},
      ["profile"])
def topics_hot(a, ctx):
    _profile(a["profile"])
    start, end = _period(a)
    rows = insights.hot_topics(a["profile"], start, end, int(a.get("top") or 10))
    return {"period": {"from": start, "to": end},
            "topics": [{**{k: r.get(k) for k in ("title", "url", "website", "matched_post_count", "participants",
                                                  "sentiment_index", "initial_date", "end_date")},
                        **({"dashboard_url": dl.topic_url(r["id"], p=a["profile"], from_=start, to=end)} if r.get("id") is not None else {})}
                       for r in rows]}


@tool("authors_top",
      "The most active authors on a profile in a period (KOLs, forum users, pages) with post counts and sentiment. "
      + insights.BIAS_NOTE_EN + " Say so whenever you use them." + CITE,
      {"profile": PROFILE, "from": DATE, "to": DATE, "top": {"type": "integer", "minimum": 1, "maximum": 50, "default": 15}},
      ["profile"])
def authors_top(a, ctx):
    _profile(a["profile"])
    start, end = _period(a)
    rows = insights.top_authors(a["profile"], start, end, int(a.get("top") or 15))
    return {"period": {"from": start, "to": end},
            "authors": [{**r, "dashboard_url": dl.person_url(r["author"], p=a["profile"], from_=start, to=end)} if r.get("author") else r
                        for r in rows],
            "note": insights.BIAS_NOTE_EN}


@tool("sources_top",
      "The channels of a profile's posts in a period: posts per website (or per board/section) with sentiment. "
      + insights.BIAS_NOTE_EN + " Say so whenever you use them." + CITE,
      {"profile": PROFILE, "from": DATE, "to": DATE, "by": {"type": "string", "enum": ["website", "board"], "default": "website"}},
      ["profile"])
def sources_top(a, ctx):
    _profile(a["profile"])
    start, end = _period(a)
    return {"period": {"from": start, "to": end},
            "sources": [{**r, "dashboard_url": dl.source_url(r["website"], p=a["profile"], from_=start, to=end)} if r.get("website") else r
                        for r in insights.top_sources(a["profile"], start, end, 20, by=a.get("by") or "website")],
            "note": insights.BIAS_NOTE_EN}


@tool("trend",
      "A profile's posts and sentiment index per day or per week over a period (default the last 30 days). "
      + insights.BIAS_NOTE_EN + " Say so whenever you use them.",
      {"profile": PROFILE, "from": DATE, "to": DATE, "bucket": {"type": "string", "enum": ["day", "week"], "default": "day"}},
      ["profile"])
def trend(a, ctx):
    _profile(a["profile"])
    start, end = _period(a, 30)
    return {"period": {"from": start, "to": end},
            "dashboard_url": dl.dashboard_url(v="trend", p=a["profile"], from_=start, to=end,
                                              bucket="week" if a.get("bucket") == "week" else None),
            "trend": insights.trend(a["profile"], start, end, a.get("bucket") or "day"),
            "note": insights.BIAS_NOTE_EN}


@tool("reports_daily",
      "The daily AI summary of a profile (the morning report's summary: heading, points, recommended action) for a day; "
      "today's report covers yesterday, so a missing day falls back to the day before and says which day it is.",
      {"profile": PROFILE, "day": DATE}, ["profile"])
def reports_daily(a, ctx):
    _profile(a["profile"])
    day = a.get("day") or insights.today()
    for d in (day, insights.day_add(day, -1)):
        hit = corpus.kv_get(f"daily:{a['profile']}:{d}")
        if isinstance(hit, dict):
            return {"day": d, "summary": hit.get("markdown") or hit.get("heading"), "posts_read": hit.get("posts"),
                    "made_at": hit.get("at")}
    return {"day": day, "summary": None, "note": "no daily summary for this day (or the day before)"}


@tool("wisers_results",
      "Articles from past WiseSearch (WisersOne) searches YYC made: headline, outlet, date, link, and the searches "
      "that found them. It only reads the log; it never searches Wisers. Use it for print and paywalled outlets the "
      "corpus may lack.",
      {"query": {"type": "string", "description": "words to find in headlines or search keywords"},
       "from": DATE, "to": DATE, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}})
def wisers_results(a, ctx):
    db = corpus.db()
    try:
        db.execute("SELECT 1 FROM wisers_articles LIMIT 1")
    except Exception:
        return {"articles": [], "note": "no WiseSearch results have been logged yet"}
    cond, args = ["TRUE"], []
    if a.get("query"):
        cond.append("(a.headline ILIKE %s OR EXISTS (SELECT 1 FROM wisers_search_articles l JOIN wisers_searches s ON "
                    "s.id = l.search_id WHERE l.doc_id = a.doc_id AND s.keyword ILIKE %s))")
        args += [f"%{a['query']}%"] * 2
    if a.get("from"):
        cond.append("a.publish_date >= %s"); args.append(a["from"])
    if a.get("to"):
        cond.append("a.publish_date < %s"); args.append(insights.day_add(a["to"], 1))
    rows = db.execute(f"""SELECT a.headline, a.source, a.type_id, a.publish_date, a.url, a.doc_id, a.seen_count,
                          (SELECT string_agg(DISTINCT s.keyword, ' | ') FROM wisers_search_articles l JOIN wisers_searches s
                           ON s.id = l.search_id WHERE l.doc_id = a.doc_id) AS keywords
                          FROM wisers_articles a WHERE {' AND '.join(cond)} ORDER BY a.publish_date DESC LIMIT %s""",
                      args + [int(a.get("limit") or 20)]).fetchall()
    return {"articles": [{"headline": r[0], "source": r[1], "type": r[2], "date": r[3], "url": r[4], "found_by": r[7]}
                         for r in rows]}


@tool("social_results",
      "What YYC's daily social-platform search found (X, TikTok, YouTube, Threads, Instagram, Reddit, Facebook): "
      "posts per platform and query for a stream, newest first, plus each platform's last check.",
      {"profile": {"type": "string", "description": "a stream's profile id (e.g. GALBOT); omit for all"},
       "platform": {"type": "string", "description": "e.g. X, Instagram, Facebook"},
       "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30}})
def social_results(a, ctx):
    import tikhub_fetch
    d = corpus.file_get("tikhub.json", {}) or {}
    items = d.get("items") or []
    if a.get("profile"):
        _profile(a["profile"])
        keys = {f"{p}|{q}" for p, q in tikhub_fetch.queries(a["profile"])}
        items = [x for x in items if x.get("key") in keys]
    if a.get("platform"):
        items = [x for x in items if a["platform"].lower() in (x.get("platform") or "").lower()]
    items.sort(key=lambda x: x.get("published") or "", reverse=True)
    checks = {}
    for k, c in (d.get("checks") or {}).items():
        p = k.split("|")[0]
        checks.setdefault(p, {"ok": 0, "failed": 0, "last": None})
        checks[p]["ok" if c.get("ok") else "failed"] += 1
        checks[p]["last"] = max(checks[p]["last"] or "", c.get("checked") or "")
    return {"fetched": d.get("fetched"), "posts": [{k: x.get(k) for k in ("platform", "source", "title", "url", "published", "section")}
                                                   for x in items[:int(a.get("limit") or 30)]],
            "total": len(items), "platform_checks": checks}
