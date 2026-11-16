"""One post, everything the system knows about it (NAVIGATION.md, "The item page"), for the agent's tools
(tools/data.py: post_get) and the MCP server. The item page itself is the edge app's (web-app/src/server/item.ts, a port
of this module: its /item/<id>, /app/item/text and /app/item/ai).

    view(pid)   the post (public): origin, times, an extract (never the full text), engagement, the comment/reply
                thread (extracts), per-profile AI tags, the streams and profiles it matched, same-story coverage from
                other outlets (insights.topics), the scorer's tags, the article's other posts, video/audio and pictures
    text(pid)   approved accounts: the full stored text, the thread's comments in full, the transcripts of the post's
                video and audio (media.py) and the text read from its pictures

The labels' meanings in Chinese are LABEL_INFO (sent as vocab.label_info; edge_config.py publishes them for the app).
The full text stays behind the approved-account check (publishers' copyright): view() carries the headline, a short
extract and the publisher's link.
"""
import json, re
from datetime import datetime
import ai, corpus, insights, paywall, titles

# the labels' names and meanings as the page shows them (ai.LABELS has the English meanings the model reads)
LABEL_INFO = {
    "repute": ("聲譽", "影響對象的形象或公信力"), "goods": ("商品", "提及特定產品"), "service": ("服務", "提及特定服務"),
    "rival": ("競爭對手", "提及一個或多個競爭對手"), "issue": ("議題", "可能引起公關問題"), "crisis": ("危機", "嚴重的公關問題"),
    "kol": ("KOL", "由具影響力的用戶發佈，或與其有關"), "engaging": ("熱議", "讚好、分享或留言數量顯著"),
    "trend": ("趨勢", "屬於更大的熱門話題或標籤"), "ask": ("訴求", "有人提出問題，可能需要回應"),
    "review": ("評價", "有人分享意見或經驗"), "accuse": ("指控", "明確表達不滿或作出指控"), "praise": ("讚揚", "明確的正面評價"),
    "rumor": ("傳聞", "未經證實的說法或揣測"), "urgent": ("緊急", "需要即時處理"), "chance": ("機遇", "可正面推廣或互動的機會"),
    "monitor": ("監察", "值得留意，暫不需即時行動"),
}
LABEL_GROUP_ZH = {"relevance": "相關性", "engagement": "互動", "intent": "意圖", "action": "行動"}
ARTICLE_MAX = 50  # other posts of the same article listed

EXTRACT = 200          # characters of the post's text shown to everyone
COMMENT_EXTRACT = 200  # characters of each comment shown to everyone
COVERAGE_DAYS = 3      # same-story candidates: posts within this many days of the post
COVERAGE_MIN = 0.3     # a candidate's title bigrams must overlap this much before insights.topics() groups them
COVERAGE_MAX = 30
# a TIMESTAMPTZ column as HKT ISO minutes (the same string the edge app's item.ts makes)
HKT_MIN = """to_char({} AT TIME ZONE 'Asia/Hong_Kong', 'YYYY-MM-DD"T"HH24:MI') || '+08:00'"""
# a queued clip's place in the transcription queue (media.due's order: the newest post first, then key), 1 = next
QUEUE_POS = """CASE WHEN m.status = 'queued' AND (m.next_try IS NULL OR m.next_try <= {now}) THEN 1 + (
  SELECT count(*) FROM media q WHERE q.status = 'queued' AND (q.next_try IS NULL OR q.next_try <= {now})

   AND (q.newest > m.newest OR (m.newest IS NULL AND q.newest IS NOT NULL)
        OR (q.newest IS NOT DISTINCT FROM m.newest AND q.key < m.key))) END AS queue"""
NOW_HKT = """(to_char(now() AT TIME ZONE 'Asia/Hong_Kong', 'YYYY-MM-DD"T"HH24:MI:SS') || '+08:00')"""


def _row(pid):
    r = corpus.db().execute("SELECT * FROM posts WHERE id=%s", (pid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("hay", "stitle", "words", "grams", "search_v", "transcript", "image_text"):  # search; text: media(), pictures()
        d.pop(k, None)
    d["engagement"] = json.loads(d["engagement"]) if d.get("engagement") else {}
    return d


def _short(text, n):
    t = re.sub(r"\s+", " ", text or "").strip()
    return (t[:n] + "…", True) if len(t) > n else (t, False)


def _comment(r, n=COMMENT_EXTRACT):
    text, cut = _short(r["text"], n)
    eng = json.loads(r["engagement"]) if r.get("engagement") else {}
    return {"id": r["id"], "author": r["author"], "time": r["published"] or r["seen"], "text": text, "cut": cut,
            "likes": eng.get("likes"), "dislikes": eng.get("dislikes")}


def thread(x, full=False):
    """the post's comment/reply thread: its own comments, or (for a comment row) the thread it belongs to. Every
    comment filed so far, no cap (Deyao, 2026-10-14: a LIHKG thread's page shows all its replies read so far, each
    with its likes and dislikes, growing as replies_fetch.py reads more)"""
    base = x["thread"] or re.sub(r"#.*$", "", x["url"])
    parent = None
    if x["thread"]:
        p = corpus.db().execute("""SELECT id, title, website, author, published, seen, ai_title, ai_title_kind FROM posts
                                   WHERE url=%s""", (base,)).fetchone()
        parent = ({k: v for k, v in titles.display(dict(p)).items() if k not in ("ai_title", "ai_title_kind", "text")}
                  if p else None)
    total = corpus.db().execute("SELECT count(*) FROM posts WHERE thread=%s", (base,)).fetchone()[0]
    rows = corpus.db().execute("""SELECT id, author, text, published, seen, engagement FROM posts WHERE thread=%s
                                  ORDER BY coalesce(published, seen), id""", (base,)).fetchall()
    # a post scored with its comments (twopass.py): which comments its latest scoring saw (the page's divider)
    sc = corpus.db().execute("""SELECT s.comments_upto, s.at FROM scored s JOIN posts p ON p.id = s.post_id
                                WHERE p.url=%s""", (base,)).fetchone()
    upto = sc["comments_upto"] if sc else None
    comments = [_comment(r, 10 ** 6 if full else COMMENT_EXTRACT) for r in rows]
    if upto is not None:
        for c, r in zip(comments, rows):
            c["scored"] = r["id"] <= upto
    return {"parent": parent, "total": total, "stated": (x["engagement"] or {}).get("comments") if not x["thread"] else None,
            "comments": comments, "scored_at": sc["at"] if upto is not None else None}


def coverage(x):
    """the same story from other outlets: the post's topic (insights.topics, title similarity) among the posts within
    COVERAGE_DAYS days, other websites only"""
    if x["thread"]:
        return []
    day = x["day"]
    rows = corpus.db().execute("""SELECT id, url, title, website, board, author, source_type, published, seen, day,
                                  date_only, ai_title, ai_title_kind FROM posts
                                  WHERE thread IS NULL AND day BETWEEN %s AND %s AND id <> %s""",
                               (insights.day_add(day, -COVERAGE_DAYS), insights.day_add(day, COVERAGE_DAYS), x["id"])).fetchall()
    rows = [titles.display(dict(r)) for r in rows]  # a title from AI is the title compared and shown
    g = insights._grams(x["title"])
    near = []
    for r in rows:
        h = insights._grams(r["title"])
        if len(g & h) / max(1, len(g | h)) >= COVERAGE_MIN:
            near.append(dict(r))
    me = {k: x.get(k) for k in ("id", "url", "title", "website", "board", "author", "source_type", "published", "seen", "day")}
    for t in insights.topics([me] + near):
        if any(p["id"] == x["id"] for p in t["posts"]):
            others = [p for p in t["posts"] if p["id"] != x["id"] and (p.get("website") or "") != (x.get("website") or "")]
            others.sort(key=lambda p: p.get("published") or p.get("seen") or "")
            return [{k: p.get(k) for k in ("id", "title", "website", "board", "author", "source_type", "title_by_ai",
                                                                    "title_linked")}
                    | {"time": corpus.shown_time(p) or p.get("seen")} for p in others[:COVERAGE_MAX]]
    return []


def tags(x):
    """every profile's tags for the post, with the profile's name; matched = the profiles that found it relevant"""
    names = {p["id"]: p for p in insights.profiles()}
    rows = corpus.db().execute("""SELECT profile, relevant, sentiment, labels, theme, tagged_at, corrected, corrected_by,
                                         corrected_at FROM tags WHERE post_id=%s""", (x["id"],)).fetchall()
    out = []
    for r in rows:
        p = names.get(r["profile"])
        if not p:
            continue  # a deleted profile's leftover row
        out.append({"profile": r["profile"], "name": p["name"], "title": p.get("title") or p["name"], "kind": p["kind"],
                    "relevant": bool(r["relevant"]), "sentiment": r["sentiment"],
                    "labels": json.loads(r["labels"]) if r["labels"] else [], "theme": r["theme"],
                    "tagged_at": r["tagged_at"], "corrected": bool(r["corrected"]), "corrected_by": r["corrected_by"],
                    "corrected_at": r["corrected_at"]})
    order = list(names)
    out.sort(key=lambda t: order.index(t["profile"]))
    return out


def scoring(x):
    """the stream scorer's own tags of the post (public, like the profiles' tags): {at, sentiment, labels, rescoring}
    or None when it never scored it"""
    import streams
    r = corpus.db().execute(f"""SELECT s.sentiment, s.labels, s.at, s.hash <> {streams.HASH} AS changed
                                FROM scored s JOIN posts p ON p.id = s.post_id WHERE s.post_id=%s""", (x["id"],)).fetchone()
    if not r:
        return None
    return {"at": r["at"], "sentiment": r["sentiment"], "labels": json.loads(r["labels"]) if r["labels"] else [],
            "rescoring": bool(r["changed"]), "second_due": second_due(x)}


def second_due(x):
    """a unit post's second scoring (twopass.py) still to come → when it is due (HKT, minutes), else None"""
    r = corpus.db().execute(f"""SELECT {HKT_MIN.format("due_at")} FROM two_pass
                                WHERE url=%s AND due_at IS NOT NULL AND second_at IS NULL""", (x["url"],)).fetchone()
    return r[0] if r else None


def not_scored(x):
    """why the scorer has not scored the post (streams.due's rules): "before" (collected before scoring started),
    "comment" (comment scoring is off), "old" (published more than streams.MAX_AGE before it was collected), "thin"
    (near-empty: 「內容不足，未評分」, thin.py), "thin_wait" (near-empty, waiting for a transcript, picture text or
    a link's content) or "due" (it will be: waiting for its full text or its turn)"""
    import streams, thin
    s = corpus.kv_get("scoring") or {}
    if not s or x["id"] <= int(s.get("from_id") or 0):
        return "before"
    if x["thread"] and not s.get("comments"):
        return "comment"
    try:
        when = datetime.fromisoformat(x["published"] or x["seen"])
        if when.tzinfo is None:
            when = when.replace(tzinfo=corpus.HKT)
        if datetime.fromisoformat(x["seen"]).astimezone(corpus.HKT) - when > streams.MAX_AGE:
            return "old"
    except (TypeError, ValueError):
        pass
    t = thin.status(x["id"])
    if t:
        return "thin" if t["status"] == "rejected" else "thin_wait"
    return "due"


def _thin(x):
    """the near-empty gate's verdict on the post's current content (thin.status) or None"""
    import thin
    return thin.status(x["id"])


LINK_RE = re.compile(r"https?://[^\s<>\"'）)」』，。、；！？\]]+")  # = follow.URL_RE
LINK_TRAIL = ".,;:!?…'\"”’"
LINKS_HIDDEN = ("share", "not_web", "media", "home")  # follow.skip_reason's buttons, pictures, front pages: not shown


def links(x):
    """the post's links (Deyao, 2026-10-20: a post's link is shown on its page): once link following has read them
    (follow.py: the post reached a stream), each as it resolved (the real address, the outlet that published it,
    our own copy's id); else the links in its text as they stand → [{url, host, name, item_id, status}]"""
    if x["thread"]:
        return []
    rows = corpus.db().execute("""SELECT f.url, f.final_url, f.host, f.status, f.reason, f.target_id, p.website
                                  FROM follow_links f LEFT JOIN posts p ON p.id = f.target_id
                                  WHERE f.post_id = %s ORDER BY f.id""", (x["id"],)).fetchall()
    scanned = corpus.db().execute("SELECT 1 FROM follow_scan WHERE post_id=%s", (x["id"],)).fetchone()
    if scanned:
        return [{"url": r["final_url"] or r["url"], "host": r["host"], "name": r["website"], "item_id": r["target_id"],
                 "status": r["status"]} for r in rows if not (r["status"] == "skipped" and r["reason"] in LINKS_HIDDEN)]
    out = []
    for m in LINK_RE.finditer(f"{x.get('text') or ''}\n{x.get('title') or ''}"):
        u = m.group(0).rstrip(LINK_TRAIL)
        if u != x["url"] and u not in [o["url"] for o in out]:
            out.append({"url": u, "host": re.sub(r"^www\.", "", (re.match(r"https?://([^/?#:]+)", u).group(1)).lower()),
                        "name": None, "item_id": None, "status": None})
    return out


def article(x):
    """the post's article (posts.article_id: posts that are the same content) → {id, posts: [the other posts]} or
    None when the post is alone in it"""
    if not x.get("article_id"):
        return None
    rows = corpus.db().execute("""SELECT id, title, website, board, author, source_type, published, seen, date_only,
                                  ai_title, ai_title_kind FROM posts
                                  WHERE article_id=%s AND id <> %s ORDER BY coalesce(published, seen), id LIMIT %s""",
                               (x["article_id"], x["id"], ARTICLE_MAX)).fetchall()
    if not rows:
        return None
    rows = [titles.display(dict(r)) for r in rows]
    return {"id": x["article_id"],
            "posts": [{k: r.get(k) for k in ("id", "title", "website", "board", "author", "source_type", "title_by_ai",
                                                                    "title_linked")}
                      | {"time": corpus.shown_time(r) or r["seen"]} for r in rows]}


def via_label(via):
    """where the text came from, in words: paywall.VIA, 'asr:<model>' (a broadcast episode's transcript,
    broadcast_fetch.py) → 「AI 轉錄（<model name>）」, or a YouTube bulletin's subtitles (youtube-captions:<lang> /
    youtube-auto-captions:<lang>) → 「YouTube 上載者字幕（lang）」 / 「YouTube 自動字幕（lang）」"""
    if (via or "").startswith("asr:"):
        return f"AI 轉錄（{ai.model_name(via[4:])}）"
    if captions_label(via):
        return captions_label(via)
    return paywall.VIA.get(via or "")


def captions_label(model):
    """a transcript read from YouTube's subtitles → its label, else None (the model's name is used)"""
    import media
    for prefix, word in ((media.CAPTIONS, "YouTube 上載者字幕"), (media.AUTO_CAPTIONS, "YouTube 自動字幕")):
        if (model or "").startswith(prefix):
            return f"{word}（{model[len(prefix):]}）"
    return None


def media(x, full=False):
    """the post's video and audio clips (media.py) → [{key, host, kind, status, duration, model, chars, error?, done_at,
    transcript (full=True only: approved accounts, like the full text)}], in the order they were found"""
    rows = corpus.db().execute("""SELECT m.key, m.url, m.host, m.kind, m.status, m.duration, m.model, m.error, m.done_at,
                                         m.transcript, m.tries, m.captions, m.captions_model, """ + QUEUE_POS.format(now=NOW_HKT) + """
                                  FROM post_media pm JOIN media m ON m.key = pm.key
                                  WHERE pm.post_id=%s ORDER BY m.first_seen, m.key""", (x["id"],)).fetchall()
    import media as md
    talk_on = md.on()
    out = []
    for r in rows:
        # transcription off (flag `transcription`, Deyao 2026-10-20: cost): a turned-off feature is absent, never announced
        # (NAVIGATION.md): a queued clip is left out, or shown with only its YouTube subtitles (status captions)
        st = r["status"]
        if st == "queued" and not talk_on:
            if not r["captions"]:
                continue
            st = "captions"
        d = {"key": r["key"], "host": r["host"], "kind": r["kind"], "status": st,
             "duration": round(r["duration"]) if r["duration"] is not None else None, "model": r["model"],
             "model_name": captions_label(r["model"]) or (ai.model_name(r["model"]) if r["model"] else None),
             "done_at": r["done_at"],
             # 「AI 轉錄」 on a model-made transcript (Deyao, 2026-10-15); YouTube's subtitles (the uploader's or its
             # auto-generated ones) are not our AI's
             "by_ai": (not captions_label(r["model"])) if r["model"] else None,
             # a YouTube clip's own subtitles, kept beside the transcript (media.captions)
             "captions_name": captions_label(r["captions_model"]), "captions_chars": len(r["captions"] or ""),
             "chars": len(r["transcript"] or ""), "error": (r["error"] or "")[:200] if r["status"] in ("failed", "no_direct") else None,
             # a queued clip's place in the transcription queue (1 = next); None while it waits to retry
             "queue": r["queue"] if st == "queued" else None}
        if full:
            d["transcript"] = r["transcript"] or ""
            d["captions"] = r["captions"] or ""
        out.append(d)
    return out


def video(x):
    """the post's saved TikTok video (tiktok_video.py; Deyao, 2026-10-22) → {status, bytes, duration, width, height} or
    None; the file itself only through /app/item/video (approved accounts: presigned links, web-app server/video.ts)"""
    r = corpus.db().execute("SELECT status, bytes, duration, width, height FROM tiktok_videos WHERE post_id=%s",
                            (x["id"],)).fetchone()
    if not r:
        return None
    return {"status": r["status"], "bytes": r["bytes"], "duration": round(r["duration"]) if r["duration"] is not None else None,
            "width": r["width"], "height": r["height"]}


def pictures(x, full=False):
    """the post's pictures (images.py), each once (by its hash), in page order → {meaningful_from, items: [{url, hash,
    status (scored | queued | failed; while picture analysis is off only scored ones), score, meaningful, reason, width, height, model, text? (full=True: approved
    accounts, like the full text)}]}"""
    import images
    th = images.threshold()
    pics_on = images.on()  # picture analysis off (flag `pictures`, Deyao 2026-10-20: cost)
    rows = corpus.db().execute("""SELECT pi.url, pi.pos, pi.hash, pi.tries, i.score, i.reason, i.text, i.width, i.height,
                                         i.model FROM post_images pi LEFT JOIN images i ON i.hash = pi.hash
                                  WHERE pi.post_id=%s ORDER BY pi.pos, pi.url""", (x["id"],)).fetchall()
    out, seen = [], set()
    for r in rows:
        if r["hash"] and r["hash"] in seen:
            continue
        seen.add(r["hash"])
        if r["width"] is not None and min(r["width"], r["height"] or 0) < images.MIN_SIDE:
            continue  # icons and spacers
        st = "scored" if r["score"] is not None else "failed" if r["tries"] >= images.TRIES else "queued"
        if st != "scored" and not pics_on:
            continue  # picture analysis off: a picture not analysed is left out (absent, never announced)
        d = {"url": r["url"], "hash": r["hash"], "status": st, "score": r["score"],
             "meaningful": r["score"] is not None and r["score"] >= th, "reason": r["reason"], "width": r["width"],
             "height": r["height"], "model": r["model"], "chars": len(r["text"] or "")}
        if full:
            d["text"] = r["text"] or ""
        out.append(d)
    return {"meaningful_from": th, "items": out}


def view(pid):
    """the post, public (no full text)"""
    import fulltext_fetch, twopass
    x = _row(pid)
    if not x:
        raise LookupError("no such post")
    text_due = fulltext_fetch.will_read(x)  # on the stored row (a title from AI moves the old title into the text)
    titles.display(x)  # a title from AI (titles.py) is the title; the post's content the text
    text = titles.body(x)  # only the title again is no text (titles.title_only)
    ex = _short(text, EXTRACT)[0] if text else ""
    streams = corpus.db().execute("""SELECT DISTINCT stream, day, item->>'category' AS category FROM stream_items
                                     WHERE url=%s ORDER BY day, stream""", (x["url"],)).fetchall()
    t = tags(x)
    th = thread(x)
    sc = scoring(x)
    sname = {p["id"]: p["name"] for p in insights.streams()}
    return {
        "id": x["id"], "url": x["url"], "title": x["title"], "website": x["website"], "board": x["board"],
        "author": x["author"], "platform": x["platform"], "source_type": x["source_type"], "region": x["region"],
        "sid": x["sid"], "source": x["source"], "published": corpus.shown_time(x), "date_only": bool(x.get("date_only")),
        "seen": x["seen"], "day": x["day"],
        "cached_at": x["cached_at"], "comment": bool(x["thread"]), "thread_url": x["thread"],
        "title_by_ai": bool(x.get("title_by_ai")), "title_extracted": bool(x.get("title_extracted")),
        "title_linked": bool(x.get("title_linked")), "title_retitled": bool(x.get("title_retitled")),
        "original_title": x.get("original_title"), "ai_title_model": x.get("ai_title_model"),
        "ai_title_model_name": ai.model_name(x["ai_title_model"]) if x.get("ai_title_model") else None,
        "ai_title_at": x.get("ai_title_at"),
        "extract": ex, "text_chars": len(text.strip()), "text_state": titles.text_state(x, text, text_due),
        "engagement": x["engagement"],
        # where the stored text came from (Deyao, 2026-10-15): paywall.VIA's label, and an archive.today copy's link
        "text_via": x.get("text_via"), "text_via_label": via_label(x.get("text_via")),
        "text_via_url": x.get("text_via_url"),
        # the full-text job (fulltext_fetch.py) will read the page: 「全文尚未收集（排隊中）」, never a spinner
        "text_due": text_due,
        # a post whose comments a fetcher reads (twopass.UNIT_URLS): no comments yet on a new one = not read yet
        "comments_read": not x["thread"] and any(re.match(u, x["url"]) for u in twopass.UNIT_URLS),
        "tags": t, "matched": [r["profile"] for r in t if r["relevant"]],
        "streams": [{"stream": r["stream"], "name": sname.get(r["stream"], r["stream"]), "day": r["day"],
                     "category": r["category"]} for r in streams],
        "scoring": sc, "not_scored": None if sc else not_scored(x), "article": article(x),
        "thin": None if sc else _thin(x),
        "thread": th, "coverage": coverage(x), "media": media(x), "pictures": pictures(x), "links": links(x),
        "video": video(x),
        "vocab": {"sentiments": list(ai.SENTIMENTS), "labels": list(ai.LABELS),
                  "label_info": {k: {"zh": LABEL_INFO[k][0], "meaning": LABEL_INFO[k][1], "group": v[1],
                                     "group_zh": LABEL_GROUP_ZH[v[1]], "emoji": v[0]} for k, v in ai.LABELS.items()}},
    }


def text(pid):
    """approved accounts: the full stored text and the full comments"""
    x = _row(pid)
    if not x:
        raise LookupError("no such post")
    titles.display(x)
    return {"id": x["id"], "text": titles.body(x), "cached_at": x["cached_at"],
            "comments": {c["id"]: c["text"] for c in thread(x, full=True)["comments"]},
            "media": {m["key"]: m["transcript"] for m in media(x, full=True) if m["transcript"]},
            "media_captions": {m["key"]: m["captions"] for m in media(x, full=True) if m["captions"]},
            "pictures": {p["hash"]: p["text"] for p in pictures(x, full=True)["items"] if p.get("text")}}
