"""What the edge app (web-app/, Cloudflare Worker reading Postgres) needs from the Python side, published as one kv
document `edge:config` (ARCHITECTURE.md "The site"). The cluster computes and writes; the Worker only reads.

Python stays the single source of truth for the stream definitions (streams.json), the feature flags (flagd, per site
host), each stream's threshold, the labels and their meanings, the near-empty gate's words and the other constants
the pages show. The poller publishes it at start and every PUBLISH_SECONDS (a flag flip or a threshold change reaches
the site within that), and `python3 edge_config.py` publishes it now (or prints it with `show`).
"""
import json, os, sys, time

import ai, cache_fetch, corpus, flags, fulltext_fetch, images, insights, item, paywall, query, searchtext, sources, streams, thin, twopass

PUBLISH_SECONDS = 60
HOSTS = (flags.MAIN_HOST, flags.BETA_HOST)


def build():
    every = insights.streams()
    ids = [s["id"] for s in every]
    keep = ("id", "name", "title", "categories")
    return {
        "at": insights.now().isoformat(timespec="seconds"),
        # every stream and its threshold; every stream is scored and shown (shown/scored/on: what the edge app reads)
        "streams": [dict({k: s.get(k) for k in keep if k in s}, telegram=bool(s.get("telegram_chat"))) for s in every],
        "shown": ids,
        "scored": ids,
        "on": {i: True for i in ids},
        "thresholds": {s["id"]: streams.threshold(s["id"]) for s in every},
        # the public part of each profile: a stream's brief, never its delivery settings
        "profiles": [{k: p.get(k) for k in ("id", "name", "title", "kind", "brief", "search_terms")} for p in insights.profiles()],
        "flags": {h: flags.public(h) for h in HOSTS},
        "sources": None,  # set by the poller (app.py knows its source list)
        "floor": streams.FLOOR,
        # two-stage scoring (streams.confirm): stage 1's score from which stage 2 scores the pair again, and stage 2's
        # model (the proof view's "Passed stage 1" tab)
        "screen": streams.SCREEN,
        "confirm_model": streams.CONFIRM_MODEL,
        # the two stages' models by display name (the live dashboard's Stage 1 / Stage 2 panels)
        "models": {"stage1": ai.model_name(ai.MODEL), "stage2": ai.model_name(streams.CONFIRM_MODEL)},
        # since when every stream's score is stored, 0–19 too (streams.file; the proof view: older posts have none under it)
        "all_scores_from": corpus.kv_get("scores_all_from"),
        "max_age_s": int(streams.MAX_AGE.total_seconds()),
        "hash_sql": streams.HASH,
        "labels": {k: {"emoji": v[0], "group": v[1], "meaning": v[2]} for k, v in ai.LABELS.items()},
        "label_order": list(ai.LABELS),  # kv is jsonb, which doesn't keep an object's key order
        "sentiments": list(ai.SENTIMENTS),
        "sent_emoji": ai.SENT_EMOJI,
        "label_info": {k: {"zh": item.LABEL_INFO[k][0], "meaning": item.LABEL_INFO[k][1], "group": v[1],
                           "group_zh": item.LABEL_GROUP_ZH[v[1]], "emoji": v[0]} for k, v in ai.LABELS.items()},
        "source_types": list(query.SOURCE_TYPES),
        "regions": ["HK", "Macau", "Mainland", "Taiwan", "Global"],
        "model": ai.MODEL,
        "via": paywall.VIA,
        "images": {"threshold": images.threshold(), "min_side": images.MIN_SIDE, "tries": images.TRIES},
        "thin": {"threshold": thin.THRESHOLD, "wait_s": int(thin.WAIT.total_seconds()), "label": thin.LABEL,
                 "waits_zh": thin.WAITS_ZH},
        "item": {"extract": item.EXTRACT, "comment_extract": item.COMMENT_EXTRACT, "coverage_days": item.COVERAGE_DAYS,
                 "coverage_min": item.COVERAGE_MIN, "coverage_max": item.COVERAGE_MAX, "article_max": item.ARTICLE_MAX},
        # which posts the full-text job reads (fulltext_fetch.will_read) and whose comments a fetcher reads
        # (twopass.UNIT_URLS): the item page's 「全文尚未收集（排隊中）」 and 「留言尚未收集」
        "fulltext": {"types": list(fulltext_fetch.SOURCE_TYPES), "sids": list(fulltext_fetch.PAGE_SIDS),
                     "full_chars": fulltext_fetch.FULL_CHARS,
                     # the stage's backlog is never read (corpus.seen_cond): its start point; first_run posts neither
                     "from_seen": corpus.cutoff("fulltext"),
                     "skip_hosts": list(cache_fetch.SKIP_HOSTS),
                     # a YouTube video's description, read when its collector didn't get it (sources.youtube_due)
                     "youtube_sid": sources.YT_TEXT_SID, "youtube_url": sources.YT_VID.pattern},
        "comment_urls": list(twopass.UNIT_URLS),
        # the catalogue sources (sites.json, in its order: sites_fetch.py / browser_fetch.py read them): the proof
        # view's ?catalogue=1 walks every one of them, and shows each entry's feed URL
        "catalogue": catalogue(),
    }


def catalogue():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites.json"), encoding="utf-8") as f:
        return [{k: s.get(k) for k in ("id", "name", "url", "kind", "type", "region")} for s in json.load(f)]


def publish(sources=None):
    doc = build()
    doc["sources"] = sources
    corpus.kv_set("edge:config", doc)
    try:  # the query dictionary for the edge's 搜尋 (searchtext.publish_edge): rewritten only when it changed
        searchtext.publish_edge()
    except Exception as e:  # noqa: BLE001: the site keeps the last published dictionary
        print("searchtext.publish_edge failed:", repr(e)[:300], flush=True)
    return doc


def loop(sources, stop):
    """the poller's thread: publish now and every PUBLISH_SECONDS until stop is set"""
    while not stop.is_set():
        try:
            publish(sources)
        except Exception as e:  # noqa: BLE001: the site keeps the last published document
            print("edge_config publish failed:", repr(e)[:300], flush=True)
        stop.wait(PUBLISH_SECONDS)


if __name__ == "__main__":
    if sys.argv[1:2] == ["show"]:
        print(json.dumps(build(), ensure_ascii=False, indent=1))
    else:
        old = corpus.kv_get("edge:config") or {}
        print(json.dumps(publish(old.get("sources")), ensure_ascii=False)[:400])
