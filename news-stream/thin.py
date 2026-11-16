#!/usr/bin/env python3
"""Posts with almost no content (Deyao, 2026-10-15: "our scrape comes up with almost zero data (e.g. just a few hashtag
or a url) and the scorer should reject it"). A TikTok post whose caption is only hashtags, a Facebook photo post
captioned 「好長情！ #魏浚笙 #am730」, a bare link nobody could read: the scorer used to score them on that, and some were
filed in 串流 and sent to Telegram.

What a post says (units): its title and text (the title counted once when the text repeats it), the transcripts of its
video/audio (media.py), the text read from its pictures (images.py) and, for a post scored with its comments
(twopass.py), those comments, after taking out URLs, #hashtags, @mentions, emoji and punctuation, the collectors'
placeholders ("(post without text)", 〔連結〕host) and a transcript's [mm:ss] marks. A CJK (or kana / hangul)
character counts one; a run of Latin letters or digits (a word) counts one. A meaningful picture (images.py score at
or above its threshold) is content too: the scorer is shown it.

THRESHOLD (4, i.e. at most three characters or words) is from the measured distribution of 46,108 posts of 8–15 Oct
(README "Near-empty posts"): under 4 are hashtag-only and placeholder posts, interjections (Haha, 好長情, 開飯嘞) and
names standing in for text (AnyAPI's author name); at 4–5 real headlines start (「關於肺鼠疫」, "Financial imbalances
and AI boom", 「睇樓速遞」).

The gate (gate(), run by streams.run_once and backfill_once on the posts due): a post under THRESHOLD is
  - waiting while content may still come: a clip queued for transcription or for its YouTube subtitles (a clip that
    is no_captions / no_direct / failed brings nothing more, so it is no reason to wait), pictures not read yet (post_images
    without a picture and with tries left, or a page images.py / media.py will still scan), a link links.py has not
    read yet (or will retry). While picture analysis or transcription is off (flags `pictures` / `transcription`,
    Deyao 2026-10-20: cost) it waits for nothing from them but a YouTube clip's own subtitles not read yet, and a
    meaningful picture is no reason to score (the scorer is text-only). It is looked at again every RECHECK and at once when its content changes (the scorer's
    content hash changes, so it is due again). It waits at most WAIT from when it was seen (measured, README: posts of
    15 Oct 07:00–14:00: picture text p99 7.0 h, transcripts p99 4.1 h, a link's last retry 4.7 h after its first);
  - rejected once nothing more can come or WAIT has passed: never scored (no model call), never filed or sent, and
    the item page and 數據分析 say 「內容不足，未評分」 and why. If content still arrives later (a late transcript), its
    content hash changes and it is judged again.
Comments and replies are not gated (each is scored with its thread's headline, where a short reply means something).
A post filed before stays filed (filed means member; sent Telegram messages are never taken back).

Table thin_posts: one row per post the gate held or rejected (hash = the content it was judged on, units, status
waiting | rejected, waits = what it waited for, first_at, check_at, at).

    python3 thin.py stats [DAYS]     near-empty posts of the last DAYS days (default 7): per source, scored, filed
    python3 thin.py show ID          one post's units and what it waits for
"""
import json, re, sys, unicodedata
from datetime import datetime, timedelta

import datefmt
import corpus

HKT = corpus.HKT
THRESHOLD = 4
WAIT = timedelta(hours=8)
RECHECK = timedelta(minutes=5)
LABEL = "內容不足，未評分"
WAITS_ZH = {"transcript": "影片逐字稿", "captions": "YouTube 字幕", "pictures": "圖片文字", "link": "連結內容", "page": "網頁內容"}

URL = re.compile(r"(?:https?://|www\.)\S+")
_STOP = r"\s#＃@＠，。：；！？、,:;!?「」『』【】()（）\[\]<>《》\"'“”"
TAG = re.compile(rf"[#＃][^{_STOP}]+")
MENTION = re.compile(rf"[@＠][^{_STOP}]+")
PLACEHOLDER = re.compile(r"\((?:post without (?:text|caption))\)|^〔連結〕\S*")
STAMP = re.compile(r"\[\d{1,2}:\d\d(?::\d\d)?\]")
WIDE = ("CJK", "HIRAGANA", "KATAKANA", "HANGUL")


def clock():
    """now (HKT); tests move it"""
    return datetime.now(HKT)


def strip(s):
    """the text without URLs, hashtags, @mentions, placeholders and [mm:ss] marks"""
    s = s or ""
    for rx in (PLACEHOLDER, URL, STAMP, TAG, MENTION):
        s = rx.sub(" ", s)
    return s


def units(s):
    """what a text says: CJK characters one each, Latin/digit words one each (emoji and punctuation nothing)"""
    n, word = 0, False
    for c in strip(s):
        if unicodedata.category(c)[0] in "LN":
            if "㐀" <= c <= "鿿" or unicodedata.name(c, "").startswith(WIDE):
                n += 1
                word = False
            elif not word:
                n += 1
                word = True
        else:
            word = False
    return n


def _norm(s):
    return re.sub(r"\W", "", strip(s))


def post_units(p):
    """a post's units: title and text (the title once when the text holds it, or the other way), transcript, picture
    text and (a unit post) its comments"""
    title, text = p.get("title") or "", p.get("text") or ""
    a, b = _norm(title), _norm(text)
    if not a or a in b:
        n = units(text)
    elif not b or b in a:
        n = units(title)
    else:
        n = units(title) + units(text)
    n += units(p.get("transcript")) + units(p.get("image_text"))
    for r in p.get("replies_text") or []:
        n += units(r)
    return n


# ---- what may still come ----

def _pending(posts):
    """{post id: [what may still come]} (transcript | captions | pictures | link) for these posts, from the workers' own
    tables. While a flag is off (images.on / media.on: Deyao, 2026-10-20, cost) nothing is waited for from it: with
    transcription off only a YouTube clip's own subtitles still come (captions: not read yet), with pictures off no
    picture text"""
    import cache_fetch, images, links, media
    ids = [p["id"] for p in posts]
    if not ids:
        return {}
    db = corpus.db()
    out = {i: [] for i in ids}
    pics_on, talk_on = images.on(), media.on()
    if talk_on:
        for r in db.execute("""SELECT DISTINCT pm.post_id FROM post_media pm JOIN media m ON m.key = pm.key
                               WHERE pm.post_id = ANY(%s) AND m.status = 'queued'""", (ids,)):
            out[r["post_id"]].append("transcript")
    else:
        for r in db.execute("""SELECT DISTINCT pm.post_id FROM post_media pm JOIN media m ON m.key = pm.key
                               WHERE pm.post_id = ANY(%s) AND m.status = 'queued' AND m.captions_at IS NULL
                                 AND m.key LIKE 'yt:%%'""", (ids,)):
            out[r["post_id"]].append("captions")
    pics = {r["post_id"] for r in db.execute(
        "SELECT DISTINCT post_id FROM post_images WHERE post_id = ANY(%s) AND hash IS NULL AND tries < %s",
        (ids, images.TRIES))} if pics_on else set()
    seen_img = {r["post_id"] for r in db.execute("SELECT post_id FROM image_scan WHERE post_id = ANY(%s)", (ids,))}
    seen_media = {r["post_id"] for r in db.execute("SELECT post_id FROM media_scan WHERE post_id = ANY(%s)", (ids,))}
    link = {r["post_id"]: r["status"] for r in db.execute(
        "SELECT post_id, status FROM link_scan WHERE post_id = ANY(%s)", (ids,))}
    skip = cache_fetch.SKIP_HOSTS + cache_fetch.PROXY_HOSTS + cache_fetch.BROWSER_HOSTS + cache_fetch.SOLVER_HOSTS
    for p in posts:
        host = (re.match(r"https?://([^/]+)", p.get("url") or "") or [None, ""])[1].lower()
        youtube = host.endswith(("youtube.com", "youtu.be"))
        # pictures: links not read yet, or a page images.py will still scan (images.scan_due's rule)
        if pics_on and (p["id"] in pics or (p["id"] not in seen_img and not (p.get("sid") or "").startswith("bc_") and (
                youtube or (p.get("source_type") in images.SCAN_TYPES and not host.endswith(skip))))):
            out[p["id"]].append("pictures")
        # a clip media.py's scan may still find on the page (media.scan_due's rule); transcription off: only a YouTube
        # post's own clip (its subtitles still come)
        if p["id"] not in seen_media and not {"transcript", "captions"} & set(out[p["id"]]) \
                and host.endswith(media.SCAN_HOSTS) and not host.endswith(media.NO_SCAN) \
                and not (p.get("sid") or "").startswith("bc_") and (talk_on or youtube):
            out[p["id"]].append("transcript" if talk_on else "captions")
        st = link.get(p["id"])
        content = links.content(p)
        if st == "pending" or (st is None and links.mostly_link(content)):
            out[p["id"]].append("link")
    return out


def _meaningful(ids):
    """posts with a meaningful picture (images.py: score at or above its threshold): the scorer is shown it"""
    import images
    if not ids or not images.on():  # picture analysis off: no picture goes to the scorer
        return set()
    return {r["post_id"] for r in corpus.db().execute(
        """SELECT DISTINCT pi.post_id FROM post_images pi JOIN images i ON i.hash = pi.hash
           WHERE pi.post_id = ANY(%s) AND i.score >= %s""", (ids, images.threshold()))}


def gate(posts):
    """the posts to score now; near-empty ones are held (waiting) or rejected, recorded in thin_posts → [posts]"""
    cand = [p for p in posts if not p.get("thread") and post_units(p) < THRESHOLD]
    if not cand:
        return posts
    good = _meaningful([p["id"] for p in cand])
    cand = [p for p in cand if p["id"] not in good]
    pend = _pending(cand)
    now = clock()
    held = set()
    c = corpus.db()
    with c:
        for p in cand:
            prev = c.execute("SELECT first_at FROM thin_posts WHERE post_id=%s", (p["id"],)).fetchone()
            first = prev["first_at"] if prev and prev["first_at"] else now.isoformat(timespec="seconds")
            seen = _when(p.get("seen")) or now
            deadline = seen + WAIT
            waits = pend.get(p["id"]) or []
            status = "waiting" if waits and now < deadline else "rejected"
            check = min(now + RECHECK, deadline).isoformat(timespec="seconds") if status == "waiting" else None
            c.execute("""INSERT INTO thin_posts (post_id, hash, units, status, waits, first_at, check_at, at)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (post_id) DO UPDATE SET hash=excluded.hash, units=excluded.units,
                           status=excluded.status, waits=excluded.waits, check_at=excluded.check_at, at=excluded.at""",
                      (p["id"], p["hash"], post_units(p), status, json.dumps(waits), first, check,
                       now.isoformat(timespec="seconds")))
            held.add(p["id"])
    if held:
        n = sum(1 for p in cand if p["id"] in held)
        print(f"thin: {n} near-empty post(s) held or rejected", flush=True)
    return [p for p in posts if p["id"] not in held]


def _when(s):
    try:
        t = datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=HKT)
    except (TypeError, ValueError):
        return None


def excluded_sql(p="p", hash_expr=None, now_param="%s"):
    """SQL: the post is held or rejected for its current content (thin_posts row with the same hash, rejected or not
    due for its next check yet)"""
    return (f"EXISTS (SELECT 1 FROM thin_posts tp WHERE tp.post_id = {p}.id AND tp.hash = {hash_expr} "
            f"AND (tp.status = 'rejected' OR tp.check_at > {now_param}))")


# ---- the item page and 數據分析 ----

def status(pid, hash_now=None):
    """{status (waiting | rejected), units, waits, until, label, why} for a post the gate held or rejected on its
    current content, else None"""
    import streams
    r = corpus.db().execute(f"""SELECT t.*, {streams.HASH} AS now_hash FROM thin_posts t JOIN posts p ON p.id = t.post_id
                                WHERE t.post_id=%s""", (pid,)).fetchone()
    if not r or r["hash"] != r["now_hash"]:
        return None
    waits = json.loads(r["waits"] or "[]")
    until = None
    if r["status"] == "waiting":
        p = corpus.db().execute("SELECT seen FROM posts WHERE id=%s", (pid,)).fetchone()
        s = _when(p["seen"]) if p else None
        until = (s + WAIT).isoformat(timespec="minutes") if s else None
    return {"status": r["status"], "units": r["units"], "threshold": THRESHOLD, "waits": waits, "until": until,
            "label": LABEL, "at": r["at"], "why": why(r["status"], r["units"], waits, until)}


def why(st, n, waits, until=None):
    base = (f"這則帖子除連結、#標籤、@提及和表情符號外只有 {n} 個字（少於 {THRESHOLD} 個），"
            "也沒有影片逐字稿、圖片文字或有意義的圖片。")
    if st == "waiting":
        what = "、".join(WAITS_ZH.get(w, w) for w in waits)
        return base + f"正等待{what}，" + (f"最遲 {datefmt.when(until)} " if until else "") + "再判斷；未有內容不作串流評分。"
    return base + "內容不足，不作串流評分，不會列入串流或發送 Telegram；之後若補回內容（例如逐字稿），會再評分。"


# ---- measuring ----

def stats(days=7):
    """near-empty posts (title + text + transcript + picture text under THRESHOLD) of the last DAYS days, per source"""
    since = (clock() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = corpus.db().execute(
        """SELECT p.id, p.sid, p.url, p.title, p.text, p.transcript, p.image_text,
                  EXISTS (SELECT 1 FROM scored s WHERE s.post_id = p.id) AS scored,
                  EXISTS (SELECT 1 FROM stream_items i WHERE i.url = p.url) AS filed,
                  (SELECT status FROM thin_posts t WHERE t.post_id = p.id) AS thin
           FROM posts p WHERE p.thread IS NULL AND coalesce(p.published, p.seen) >= %s
             AND char_length(coalesce(p.title, '')) + char_length(coalesce(p.text, '')) < 2000""",
        (since,)).fetchall()
    per, out = {}, []
    for r in rows:
        if post_units(dict(r)) >= THRESHOLD:
            continue
        k = (r["sid"] or "?").split("_")[0] if not (r["sid"] or "").startswith("tikhub") else r["sid"]
        d = per.setdefault(k, {"posts": 0, "scored": 0, "filed": 0, "rejected": 0, "waiting": 0})
        d["posts"] += 1
        d["scored"] += r["scored"]
        d["filed"] += r["filed"]
        if r["thin"]:
            d[r["thin"]] += 1
        out.append(r)
    return per, out


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["stats"]:
        per, rows = stats(int(a[1]) if a[1:] else 7)
        for k, d in sorted(per.items(), key=lambda kv: -kv[1]["posts"]):
            print(f"{k:28} {d['posts']:6} scored {d['scored']:5} filed {d['filed']:4} rejected {d['rejected']:5} waiting {d['waiting']:4}")
        print("total", len(rows), "scored", sum(r["scored"] for r in rows), "filed", sum(r["filed"] for r in rows))
    elif a[:1] == ["show"] and a[1:]:
        r = corpus.db().execute("SELECT * FROM posts WHERE id=%s", (int(a[1]),)).fetchone()
        print(json.dumps({"units": post_units(dict(r)), "pending": _pending([dict(r)]).get(r["id"]),
                          "status": status(r["id"])}, ensure_ascii=False, indent=1))
    else:
        print(__doc__)
