#!/usr/bin/env python3
"""Titles for posts that have none of their own (Deyao, 2026-10-14). The collectors put the start of a social post's
text in its title (Facebook, Instagram, Threads, Telegram, X, TikTok…): long, cut off, unreadable in
串流, Telegram and 數據分析. For such a post the pipeline asks AI for its title, stored in its own columns; the post's
title and text columns stay as they were collected.

Two kinds (posts.ai_title_kind):
  - "extracted": the post opens with its own headline (a news outlet's Telegram channel or Facebook page: "李家超：
    孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥" then the body). The model surfaces it word for word; extracted()
    checks it is in the post at its start. It is the post's own title: shown with no AI mark, and the body shown is
    the post without it.
  - "written": no headline in the post, so the model writes a short, factual one in the post's language (Traditional
    Chinese with Hong Kong usage for a Chinese post; clean() converts a Simplified answer with OpenCC s2hk, leaves a
    Traditional one as it is, and writes straight double quotes as the title's language does). Marked as AI-written
    wherever it is shown: the site's 「AI 標題」 badge (title_by_ai), MARK before it on Telegram and in the xlsx
    exports (plain()).

  - "linked" (Deyao, 2026-10-15): the post's own text is only a link and links.py appended what it links to
    (〔連結內容：title〕…): its title is the linked page's title, with no model call (ai_title_model empty), marked
    「連結標題」 on the site (title_linked) and LINK_MARK on Telegram and in the exports. A bare URL is never a title.

  - "retitled" (Deyao, 2026-10-20: RTHK's YouTube 「1100 新聞簡報」, true but meaningless): the post HAS a title of its
    own (own()), but it does not say what the post is about. The judgement and the new title come from the model that
    reads the post (stage-2 scoring, which files it with store(p, title, "retitled", model)), not from this module's
    rule (needs()) or its own calls. Shown like a written one (「AI 標題」, MARK), and the post's own title is kept
    and shown as the source's title: 來源標題 on the item page, named in the badge's hover (title_retitled).

Where it is shown (display(), item_display()): `title` is the AI title, `original_title` the collected one, `text`
the post's full content (its text, else the collected title; without the opening headline when extracted),
`title_by_ai` / `title_extracted`, `ai_title_model` / `ai_title_at` for the badge's hover text.

What needs one: needs(). Who titles it: a post with no title of its own (title_from_body; or, outside TYPES, a
collected title that is only a number or punctuation: a sitemap's thread id such as 香港高登's 「8099523」) is titled
by stage-2 scoring (streams.confirm_title), so a post that never reaches stage 2 shows its excerpt and costs no call
(Deyao, 2026-10-20). Stage 2's "title_ok" counts only for a post that has a title of its own (titles.own): a post
shown with none (streams.post_line, NO_TITLE) has nothing to call fine. When stage 2 reached such a post but left it
untitled (title_ok on a post with none, no judgement, or a title clean() or extracted() refused), this module's own
call titles it (by_scorer, reached); so does a post in 串流. The titles thread takes these posts only once they are
scored on their current content, so it never decides before stage 2 has. How: ai.MODEL (the stage-1 scorer's model) on YYC's OpenRouter key, a call of its own per
BATCH posts (PROMPT: one general instruction, as stage 2's streams.TITLE; ai_spend purpose `titles`; pipeline AI,
not behind the user-AI flag). When: the live scorer
(streams.run_once) titles each batch right before filing it, so a stream item and its Telegram message carry the
title; a thread (start()) does everything else, newest first: the last BACKFILL_DAYS days and every post in 串流. A
post is titled again when its title or text change (posts.ai_title_hash = the md5 of title + text it was made from,
like streams.HASH); a post that needs none is marked checked with the same hash. A "checked, none" never overwrites
what another writer stored for the post since it was read (store's compare on ai_title_hash: the thread and the
live scorer work on the same posts). The AI title is in the post's
search columns (corpus.search_cols), so 搜尋 finds it.

    python3 titles.py once [N]     title up to N due posts now (default 50) and print them
    python3 titles.py status       how many posts are due
    python3 titles.py recheck [--dry]   title now the posts with no title of their own that were checked with none
                                   though stage 2 reached them or they are in 串流 (recheck(); a manual run)
"""
import hashlib, json, re, sys, threading, unicodedata
from datetime import datetime, timedelta, timezone

import ai, corpus, links

HKT = timezone(timedelta(hours=8))
PURPOSE = "titles"
MARK = "〔AI標題〕"
LINK_MARK = "〔連結標題〕"  # the title is the linked page's (kind "linked")
BATCH = 10
WORKERS = 6            # batches the thread sends at once (the live scorer's own batch is one call)
MAX_TOKENS = 12000     # the model thinks first; 4000 cut a batch's answer off mid-JSON (2026-10-14 trial)
TEXT_CHARS = 3000      # of a post's text the model reads
SHORT = 40             # a post this short (characters of its whole text) reads as it is: no title needed
CUT = 60               # a title at least this long that is the start of the text was cut from it
LEAD = 20              # an extracted headline opens the post: at most a short tag (e.g. 【#港聞小編】) before it
BACKFILL_DAYS = 7
SETTLE = timedelta(minutes=10)  # the thread leaves a post this new and unscored to the live scorer
IDLE = 5
STOP = threading.Event()
HASH = "md5(coalesce(p.title, '') || chr(10) || coalesce(p.text, ''))"  # streams.HASH: what a post's content is
KINDS = ("extracted", "written", "linked", "retitled")

# platforms where the collectors build the title from the post's text (fb_fetch, ig_fetch, sources' Telegram
# channels, tikhub_fetch's desc/text/caption platforms): by sid, and by platform name for rows filed another way
TEXT_SIDS = {"facebook", "instagram", "now_telegram", "tikhub_tiktok", "tikhub_twitter", "tikhub_threads",
             "tikhub_instagram", "tikhub_facebook"}
TEXT_PLATFORMS = {"Facebook", "facebook", "Instagram", "Threads", "Telegram", "X", "TikTok", "LinkedIn"}
TYPES = ("Social Media", "Video")
# a collector's placeholder, or a title with no word in it (only digits and punctuation: a sitemap's slug that is a
# thread id, 香港高登's 「8099523」); WORDLESS_SQL is its second half for due()
PLACEHOLDER = re.compile(r"^\((post without (text|caption))\)$|^[0-9?？.…\s_/#-]*$")
WORDLESS_SQL = "^[0-9?？.…[:space:]_/#-]*$"

# one general instruction (Deyao, 2026-10-23: "design the prompt so that it achieves the effect"), the same as stage
# 2's (streams.TITLE); typography is clean()'s
PROMPT = """For each post below, give a headline that says what the post is about: kind "extracted" with the post's
own opening headline copied exactly when it has one, else kind "written" with a short, factual headline in the
post's language (Traditional Chinese with Hong Kong usage for a post in Chinese).
Answer JSON only: {{"titles": [{{"i": 0, "kind": "extracted", "title": "…"}}]}} with one entry per post.

Posts:
{posts}"""


# ---- what needs one ----

def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def needs(p):
    """does this post need a title from AI? (a dict with thread, source_type, sid, platform, title, text)"""
    if p.get("thread"):
        return False
    title, text = _norm(p.get("title")), _norm(p.get("text"))
    if p.get("source_type") not in TYPES:  # a news, forum, blog… post whose collected title has no word in it
        return bool(text) and bool(PLACEHOLDER.match(title))
    body = text or title
    cut = title.endswith("…")
    if not body or (len(body) <= SHORT and not cut):
        return False
    if own_title(p):
        return False
    sid = p.get("sid") or ""
    if sid in TEXT_SIDS or sid.startswith("tg_") or p.get("platform") in TEXT_PLATFORMS:
        return True
    if not title or PLACEHOLDER.match(title):
        return True
    head = title.rstrip("…").strip()
    return bool(text) and text.startswith(head) and (cut or len(head) >= CUT)


def by_scorer(p):
    """a post stage-2 scoring titles (module docstring): one with no title of its own, collected as an excerpt of its
    text (title_from_body), or outside TYPES (needs(): a wordless title)"""
    return bool(p.get("title_from_body")) or p.get("source_type") not in TYPES


def reached(ids):
    """the posts of `ids` that stage 2 scored on their current content (scored.answers stage2, at streams.HASH) or
    that are in 串流 → set of ids"""
    import streams
    if not ids:
        return set()
    return {r[0] for r in corpus.db().execute(f"""
        SELECT p.id FROM posts p WHERE p.id = ANY(%s) AND (
          EXISTS (SELECT 1 FROM scored s WHERE s.post_id = p.id AND s.hash = {streams.HASH} AND s.answers ? 'stage2')
          OR p.url IN (SELECT url FROM stream_items))""", (list(ids),)).fetchall()}


def own_title(p):
    """an X post that came with a title of its own: an X Article (tikhub_fetch.x_tweet gives the article's title, and
    its body as the text), so the title is not the start of the text (links left out of both)"""
    if p.get("platform") != "X" or p.get("thread"):
        return False
    title = _norm(links.URL.sub("", p.get("title") or "")).rstrip("…").strip()
    text = _norm(links.URL.sub("", p.get("text") or ""))
    return bool(title and text) and not title.startswith(corpus.LINK_TITLE) and not text.startswith(title[:20])


def own(p):
    """the post's own title as collected, or "" when it has none: its stored title is an excerpt of its text
    (title_from_body, corpus.body_title), a collector's placeholder, or a link waiting for its page's headline
    (corpus.LINK_TITLE)"""
    t = (p.get("title") or "").strip()
    if p.get("title_from_body") or PLACEHOLDER.match(t) or t.startswith(corpus.LINK_TITLE):
        return ""
    return t


# ---- the post as the model reads it, and its opening headline ----

def body(p):
    return _norm(p.get("text")) or _norm(p.get("title"))


def raw(p):
    """the post's content with its line breaks kept (a first-line headline shows as one), other whitespace collapsed"""
    t = (p.get("text") or "").strip() or (p.get("title") or "").strip()
    t = "\n".join(re.sub(r"[ \t　\xa0]+", " ", ln).strip() for ln in t.splitlines())
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def prompt(posts):
    out = []
    for i, p in enumerate(posts):
        where = p.get("platform") or p.get("website") or ""
        who = p.get("author") or p.get("board") or ""
        out.append(f'<post i="{i}" platform="{where}" author="{who}">\n{raw(p)[:TEXT_CHARS]}\n</post>')
    return PROMPT.format(posts="\n".join(out))


def _skip(ch):
    """characters a headline match ignores: whitespace, emoji and other symbols, variation selectors"""
    return ch.isspace() or unicodedata.category(ch) in ("So", "Sk", "Mn", "Cf", "Co")


def _squash(s):
    """s without the _skip characters, and for each character kept its index in s"""
    idx = [i for i, ch in enumerate(s or "") if not _skip(ch)]
    return "".join(s[i] for i in idx), idx


def find_headline(text, headline):
    """→ (start, end) of the headline in text when it opens the post (whitespace and emoji ignored; at most a short
    tag before it, no sentence), else None"""
    t, idx = _squash(text)
    h, _ = _squash(headline)
    if not h or not t:
        return None
    k = t.find(h)
    if k < 0 or k > LEAD or re.search(r"[。！？!?.]", t[:k]):
        return None
    return idx[k], idx[k + len(h) - 1] + 1


def _unwrap(t):
    """a title without the 【】/[] brackets wrapping all of it (《》 and 「」 stay: they are part of a headline)"""
    t = _norm(t)
    for a, b in (("【", "】"), ("[", "]")):
        if t.startswith(a) and t.endswith(b) and b not in t[1:-1]:
            t = t[1:-1].strip()
        elif t.startswith(a) and b not in t:
            t = t[1:].strip()
        elif t.endswith(b) and a not in t:
            t = t[:-1].strip()
    return t


def strip_headline(text, headline):
    """the post's text without its opening headline (and the brackets, colon or line break after it); the whole text
    when the headline isn't at its start"""
    at = find_headline(text or "", headline)
    if not at:
        return text
    rest = (text or "")[at[1]:]
    i = 0
    while i < len(rest) and (_skip(rest[i]) or rest[i] in "】」』]）):：｜|-–—"):
        i += 1
    return rest[i:] or text


def _no_emoji(t):
    """t without emoji and other pictographs (a headline's 📢 🛗 ⚠️), whitespace collapsed"""
    return _norm("".join(" " if unicodedata.category(ch) in ("So", "Cf", "Co") or 0xFE00 <= ord(ch) <= 0xFE0F
                         else ch for ch in t))


def extracted(title, text):
    """the model's 'extracted' headline, checked to be in the post word for word at its start (whitespace and emoji
    aside) → the headline without emoji, or None (then the model's title counts as written)"""
    t = _no_emoji(_unwrap(str(title or "")))
    t = _unwrap(t)
    if not t or not find_headline(text or "", t):
        return None
    return t


def chinese(s):
    """is this text Chinese? (Han characters at least a fifth of its letters)"""
    s = re.sub(r"https?://\S+|[#@]\S+", "", s or "")
    han = len(re.findall(r"[㐀-鿿豈-﫿]", s))
    latin = len(re.findall(r"[A-Za-z]", s)) / 4  # ~4 letters per word ~ 1 Han character
    kana = len(re.findall(r"[぀-ヿ가-힯]", s))
    return han > 0 and han >= 0.2 * (han + latin + kana) and kana < han


def _hk(s):
    """a Simplified Chinese title in Traditional Chinese (OpenCC s2hk). Only a title with no Traditional-only character
    is converted: s2hk on a Traditional title turns names wrong (范徐麗泰 → 範徐麗泰)"""
    import searchtext
    if searchtext._opencc("t2s").convert(s) != s:  # it has Traditional-only characters: already Traditional
        return s
    return searchtext._opencc("s2hk").convert(s)


def clean(title, text):
    """the model's written title made safe to show → str, or None when it isn't a usable title (empty, or far over the
    length: a cut-off headline is what this replaces). Chinese titles in Traditional Chinese (s2hk); straight double
    quotes as the title's language writes them (「」 in Chinese, “” in English; latin_quotes for the Chinese marks)"""
    t = _unwrap(str(title or ""))
    for a, b in (("「", "」"), ("“", "”"), ('"', '"'), ("'", "'")):
        if len(t) > 2 and t.startswith(a) and t.endswith(b) and b not in t[1:-1]:
            t = t[1:-1].strip()
    t = re.sub(r"\s*#\S+", "", t).strip()
    t = re.sub(r"[。．.!！?？,，;；:：、\s]+$", "", t).strip()
    if not t:
        return None
    if chinese(t):
        t = _STRAIGHT.sub(r"「\1」", t)
        return _hk(t) if len(t) <= 45 else None
    t = _STRAIGHT.sub(r"“\1”", latin_quotes(t))
    return t if len(t.split()) <= 25 and len(t) <= 200 else None


_STRAIGHT = re.compile(r'["＂]([^"＂\n]+)["＂]')  # a pair of straight double quotes (in a written title)


# Chinese quotation marks in an English title (Deyao, 2026-10-21: "Apostrophe and quotation mark for English sources
# are wrong"): the prompts asked for 「」 instead of a straight double quote in every title, so a CGTN post's "face
# wash" came back as 「face wash」. A Latin-script title takes English marks: 「」 → “”, 『』 → ‘’, full-width ＂＇ → " '.
# Chinese and Japanese titles keep their 「」.
_LATIN_QUOTES = str.maketrans({"「": "“", "」": "”", "『": "‘", "』": "’", "＂": '"', "＇": "'"})


def latin_quotes(t):
    """a Latin-script title with English quotation marks (a Chinese or Japanese one, whose marks 「」 are, as it is)"""
    if not t or chinese(t) or re.search(r"[぀-ヿ가-힯]", t) or len(re.findall(r"[A-Za-z]", t)) < 2:
        return t
    return t.translate(_LATIN_QUOTES)


# ---- the model ----

def _ask(batch):
    """one call for the batch → {index in batch: (title, kind)}; ValueError when the answer isn't JSON"""
    res = ai.jsonc(prompt(batch), PURPOSE, max_tokens=MAX_TOKENS)
    out = {}
    for t in (res or {}).get("titles") or []:
        j = t.get("i") if isinstance(t, dict) else None
        if not isinstance(j, int) or not 0 <= j < len(batch) or j in out:
            continue
        text = raw(batch[j])
        if t.get("kind") == "extracted":
            e = extracted(t.get("title"), text)
            if e:
                out[j] = (e, "extracted")
                continue
            print(f"titles: an 'extracted' headline not in the post, counted as written: {str(t.get('title'))[:80]!r}",
                  flush=True)
        c = clean(t.get("title"), text)
        if c:
            out[j] = (c, "written")
    return out


def generate(posts):
    """→ {index: (title, kind)}: one call for the posts; those the answer leaves out or gets wrong (or all, when it
    isn't JSON) are asked again, one call each"""
    out = {}
    try:
        out = _ask(posts)
    except ValueError as e:
        print(f"titles: JSON error ({e}); asking post by post", flush=True)
    for i, p in enumerate(posts):
        if i in out:
            continue
        try:
            got = _ask([p])
        except ValueError as e:
            print(f"titles: JSON error ({e}) for post {p.get('id')}", flush=True)
            continue
        if 0 in got:
            out[i] = got[0]
    return out


# ---- storing ----

def content_hash(p):
    return hashlib.md5(((p.get("title") or "") + "\n" + (p.get("text") or "")).encode()).hexdigest()


def store(p, title, kind=None, model=None, force=False):
    """write a post's title from AI and its kind (title None: checked, needs none or none could be made) with the hash
    of the content it was made from, and the post's search columns. model: the model that wrote it (default ai.MODEL,
    this module's own calls; stage-2 scoring passes its own for a "retitled" one). A "checked, none" (title None) is
    not written when the row's ai_title_hash changed since p was read (another writer stored for it meanwhile: the
    thread's decision came from a stale read, 7761895 lost stage 2's title that way on 29 Oct 2026); force: write
    whatever the row holds (stage 2's judgement, made on the post just scored)"""
    at = datetime.now(HKT).isoformat(timespec="seconds")
    h = p.get("hash") or content_hash(p)
    title = corpus.plain(title)  # a linked page's or the model's &amp;/&#8217; as text (corpus.plain)
    kind = kind if title else None
    model = (model or ai.MODEL) if title and kind != "linked" else None
    c = corpus.db()
    with c:
        r = c.execute("""SELECT title, text, author, source, thread, ai_title, ai_title_kind, ai_title_hash, transcript,
                                image_text FROM posts WHERE id=%s FOR UPDATE""", (p["id"],)).fetchone()
        if not r:
            return
        if not title and not force and "ai_title_hash" in p and r["ai_title_hash"] != p["ai_title_hash"]:
            p.update(ai_title=r["ai_title"], ai_title_kind=r["ai_title_kind"], ai_title_hash=r["ai_title_hash"])
            return
        if (r["ai_title"], r["ai_title_kind"]) == (title, kind):
            c.execute("UPDATE posts SET ai_title_hash=%s WHERE id=%s", (h, p["id"]))
        else:
            c.execute(f"""UPDATE posts SET ai_title=%s, ai_title_kind=%s, ai_title_model=%s, ai_title_at=%s,
                          ai_title_hash=%s, {corpus.SEARCH_SET} WHERE id=%s""",
                      (title, kind, model, at if title else None, h)
                      + corpus.search_cols(r["title"], r["text"], r["author"], r["source"], r["thread"], title,
                                           r["transcript"], r["image_text"])
                      + (p["id"],))
    p.update(ai_title=title, ai_title_kind=kind, ai_title_model=model,
             ai_title_at=at if title else None, ai_title_hash=h)


def linked(p):
    """the linked page's title for a post whose own text is only a link (links.py appended what it links to) → str
    or None"""
    return None if p.get("thread") else links.linked_title(raw(p))


def title_posts(posts, scored=()):
    """title the posts that need one (in batches) and mark the rest checked → how many got a title. A post that is
    only a link gets the linked page's title (kind "linked"), with no model call. A post stage 2 titles (by_scorer) is
    titled here only when stage 2 reached it (`scored`: ids the live scorer's stage 2 just scored, else reached())
    or it is in 串流; otherwise it is checked and shows its excerpt"""
    n = 0
    rest = []
    for p in posts:
        lt = linked(p)
        if lt:
            store(p, lt, "linked")
            n += 1
        else:
            rest.append(p)
    posts = rest
    # a post with no title of its own is titled by stage-2 scoring; one stage 2 never reached shows the excerpt of its
    # opening (Deyao, 2026-10-20), one it reached but left untitled is titled here
    ask = [p for p in posts if needs(p)]
    near = set(scored) | reached([p["id"] for p in ask if by_scorer(p) and p["id"] not in scored])
    want = [p for p in ask if not by_scorer(p) or p["id"] in near]
    for p in posts:
        if p not in want:
            store(p, None)
    batches = [want[k:k + BATCH] for k in range(0, len(want), BATCH)]
    if len(batches) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(min(WORKERS, len(batches))) as ex:
            answers = list(ex.map(generate, batches))
    else:
        answers = [generate(b) for b in batches]
    for batch, got in zip(batches, answers):
        for i, p in enumerate(batch):
            store(p, *(got.get(i) or (None, None)))
            n += i in got
        if len(got) < len(batch):
            print(f"titles: {len(batch) - len(got)} of {len(batch)} post(s) got no usable title", flush=True)
    return n


def ensure(posts, scored=()):
    """the live scorer's batch (rows with `hash`): title the posts whose title is missing or stale, before filing.
    scored: the ids stage 2 scored in this batch; such a post that needs a title and has none is titled too, whatever
    its hash says"""
    stale = [p for p in posts if p.get("ai_title_hash") != (p.get("hash") or content_hash(p))
             or (p["id"] in scored and not p.get("ai_title") and needs(p))]
    if stale:
        title_posts(stale, scored)


def due(limit):
    """posts not titled (or checked) on their current content: social and video posts (and, of other types, those
    whose title has no word in it: needs()) of the last BACKFILL_DAYS days and every post in 串流 (those first),
    newest first. A post newer than SETTLE and not scored yet is the live scorer's; a post stage 2 titles (by_scorer)
    is the live scorer's until it is scored on its current content (one already in 串流: until SETTLE)"""
    import streams
    now = datetime.now(HKT)
    cut = (now - timedelta(days=BACKFILL_DAYS)).isoformat(timespec="seconds")
    settle = (now - SETTLE).isoformat(timespec="seconds")
    scored = f"EXISTS (SELECT 1 FROM scored s WHERE s.post_id = p.id AND s.hash = {streams.HASH})"
    return [dict(r) for r in corpus.db().execute(f"""
        SELECT p.*, {HASH} AS hash FROM posts p
        WHERE p.thread IS NULL AND (p.source_type = ANY(%s) OR coalesce(p.title, '') ~ %s)
          AND p.ai_title_hash IS DISTINCT FROM {HASH}
          AND {corpus.seen_cond("p", "titles")}
          AND (coalesce(p.published, p.seen) >= %s OR p.url IN (SELECT url FROM stream_items))
          AND CASE WHEN p.title_from_body OR NOT p.source_type = ANY(%s)
                   THEN {scored} OR (p.seen <= %s AND p.url IN (SELECT url FROM stream_items))
                   ELSE p.seen <= %s OR {scored} END
        ORDER BY EXISTS (SELECT 1 FROM stream_items si WHERE si.url = p.url) DESC, coalesce(p.published, p.seen) DESC,
                 p.id DESC LIMIT %s""", (list(TYPES), WORDLESS_SQL, cut, list(TYPES), settle, settle, limit))]


def recheck(dry=False):
    """title now the posts stage 2 titles (by_scorer) that have no title (checked with none, or never taken) though
    stage 2 reached them on their current content or they are in 串流 (reached(); the last BACKFILL_DAYS days and 串流, whatever the stage's start point: a manual run)
    → how many were taken (dry: counted only)"""
    cut = (datetime.now(HKT) - timedelta(days=BACKFILL_DAYS)).isoformat(timespec="seconds")
    rows = [dict(r) for r in corpus.db().execute(f"""
        SELECT p.*, {HASH} AS hash FROM posts p
        WHERE p.thread IS NULL AND (p.source_type = ANY(%s) OR coalesce(p.title, '') ~ %s)
          AND p.ai_title IS NULL AND (p.ai_title_hash IS NULL OR p.ai_title_hash = {HASH})
          AND (coalesce(p.published, p.seen) >= %s OR p.url IN (SELECT url FROM stream_items))
        ORDER BY p.id DESC""", (list(TYPES), WORDLESS_SQL, cut))]
    rows = [p for p in rows if needs(p) and by_scorer(p)]
    near = reached([p["id"] for p in rows])
    rows = [p for p in rows if p["id"] in near]
    if not dry:
        for k in range(0, len(rows), BATCH * WORKERS):
            n = title_posts(rows[k:k + BATCH * WORKERS], near)
            print(f"titles recheck: {k + min(BATCH * WORKERS, len(rows) - k)} of {len(rows)} taken, {n} titled", flush=True)
    return len(rows)


def run_once(limit=BATCH * WORKERS):
    """one round of the thread → how many posts it took (titled or checked)"""
    posts = due(limit)
    if posts:
        title_posts(posts)
    return len(posts)


def loop():
    while not STOP.is_set():
        try:
            with corpus.stage("titles"):  # a restart starts from now (corpus.STAGES): the backlog waits
                n = run_once()
            STOP.wait(0 if n else IDLE)
        except Exception as e:
            print("titles failed:", repr(e)[:300], flush=True)
            STOP.wait(60)


def start(stop=None):
    """the titles thread (the poller's; stop = the scorer's drain event)"""
    global STOP
    STOP = stop or STOP
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


# ---- display ----

def shown(ai_title, kind, title, text):
    """the fields a post or stream item is shown with (module docstring)"""
    full = (text or "").strip() or title or ""
    ext, lk = kind == "extracted", kind == "linked"
    return {"title": ai_title, "title_by_ai": not (ext or lk), "title_extracted": ext, "title_linked": lk,
            "title_retitled": kind == "retitled", "original_title": title, "text": strip_headline(full, ai_title) if ext else full}


TITLE_SLACK = 15  # characters a body may have beside the title and still be only the title (「詳情」, a hashtag)


def _letters(s):
    import unicodedata
    s = re.sub(r"https?://\S+", "", s or "")
    return "".join(ch for ch in unicodedata.normalize("NFKC", s).lower() if ch.isalnum())


def title_only(text, *heads):
    """True when text says nothing beyond a title (Deyao, 2026-10-20: Dimsum Daily's post showed its headline as
    全文): empty, a title again, a title plus links and at most TITLE_SLACK letters, or a part of a title. Letters and
    digits only are compared (NFKC, lower case; links, spaces and punctuation left out)"""
    t = _letters(text)
    if not t:
        return True
    for h in heads:
        h = _letters(h)
        if h and (t in h or (h in t and len(t) - len(h) <= TITLE_SLACK)):
            return True
    return False


# a social post's, a forum post's (a thread's opening post; a forum page's read keeps no title-only text: fulltext_fetch.
# save) or a comment's text is its collector's whole text, kept even when it is only its title (香港討論區's 「史卓豐…」)
WHOLE_TYPES = ("Social Media", "Forum")


def body(d):
    """a shown post's (display()) text when it is more than its titles, else "" (the item page: no 全文). A social
    post's or a comment's text is its collector's whole text, kept even when it is only its title"""
    t = d.get("text") or ""
    if d.get("source_type") in WHOLE_TYPES or d.get("thread"):
        return t
    return "" if title_only(t, d.get("title"), d.get("original_title")) else t


def text_state(d, text, due):
    """what the item page's 內容 card says (Deyao, 2026-10-20: never "the source gives only a title"): "full" (text),
    "due" (a reader will still read it), "whole" (a social post or comment whose whole text is its title), "failed"
    (read, nothing kept), "none" (no reader covers it). From recorded state only (cached_at, text_due), never age"""
    if (text or "").strip():
        return "full"
    if due:
        return "due"
    if (d.get("source_type") in WHOLE_TYPES and not (d.get("source_type") == "Forum" and d.get("cached_at"))) or d.get("thread"):
        return "whole"  # (a forum PAGE read with nothing kept has cached_at: failed)
    return "failed" if d.get("cached_at") else "none"


def display(d):
    """a post row (dict) as shown; a post with no title from AI unchanged"""
    if d.get("ai_title"):
        d.update(shown(d["ai_title"], d.get("ai_title_kind"), d.get("title"), d.get("text")))
    return d


def item_display(it, ai_title, kind=None, model=None, at=None, ptitle=None, ptext=None):
    """a filed stream item (stream_items.item) as shown, from its post's current title from AI, title and text"""
    if ai_title:
        it.update(shown(ai_title, kind, ptitle or it.get("title"), ptext or it.get("text")))
        it["text"] = (it["text"] or "")[:2000] or None
        it["ai_title_model"], it["ai_title_at"] = model, at
    return it


ITEM_JOIN = "LEFT JOIN posts p ON p.url = si.url"
ITEM_COLS = "p.ai_title, p.ai_title_kind, p.ai_title_model, p.ai_title_at, p.title AS p_title, p.text AS p_text"


def item_row(r):
    """a row of a query selecting si.item and ITEM_COLS → the item as shown"""
    return item_display(r["item"], r["ai_title"], r["ai_title_kind"], r["ai_title_model"], r["ai_title_at"],
                        r["p_title"], r["p_text"])


def plain(x):
    """the title as text (Telegram, exports): MARK before an AI-written title, LINK_MARK before the linked page's
    title, nothing before the post's own headline"""
    return (MARK if x.get("title_by_ai") else LINK_MARK if x.get("title_linked") else "") + (x.get("title") or "")


if __name__ == "__main__":
    if sys.argv[1:2] == ["once"]:
        posts = due(int(sys.argv[2]) if sys.argv[2:] else 50)
        title_posts(posts)
        for p in posts:
            if p.get("ai_title"):
                print(f"{p['id']}\t{p.get('platform')}\t{p['ai_title_kind']}\t{p['ai_title']}\t← {body(p)[:80]}")
    elif sys.argv[1:2] == ["status"]:
        print(json.dumps({"due": len(due(1000000))}))
    elif sys.argv[1:2] == ["recheck"]:
        print(json.dumps({"rechecked": recheck(dry="--dry" in sys.argv), "dry": "--dry" in sys.argv}))
    else:
        print(__doc__)
