#!/usr/bin/env python3
"""Scored streams (Deyao, 2026-10-14): every new post is scored 0–100 against every stream, and a stream shows the
posts at or above its threshold. Nothing is gated by keywords.

  - What is scored: every post the corpus ingests after the switch (kv `scoring`.from_id: new posts only), on its
    full text, and again whenever its title + text change (table `scored` keeps the hash it was scored on), so a post
    whose article text arrives later (fulltext_fetch.py) is judged on the whole story. Comments and forum replies
    are scored with their thread's headline once kv `scoring`.comments is on (infra/thresholds.py comments on).
  - How: the stage-1 model (ai.MODEL, Qwen3.7 Flash) reads a batch of posts and every stream's brief and returns, per post, the sentiment
    and labels and, for every stream (Deyao, 2026-10-21: "my proof page should show me the exact score even when
    below 20"), the score, a one-line reason and (for a stream with `categories`) its category. RUBRIC below fixes
    what a score means. Every score is stored, 0–19 too (scored.all_scores marks a post scored that way; before
    21 Oct 2026 the model left streams under FLOOR out and nothing under it was stored). A score under FLOOR changes
    nothing a stream does (FLOOR <= SCREEN <= every threshold); the staff pages show it as 「低於 20 分」 as before and
    only the proof view shows the exact number.
  - Two stages (Deyao, 2026-10-20: a cheap screen, then a second model rates again what passes; "this saves cost and
    reduces false positives"; confirm(); the screen is 20 since 23 Oct 2026, Deyao: "make the stage 2 score all above
    20"): every pair stage 1
    scores at or above SCREEN (20; every threshold is at least that) is scored again by CONFIRM_MODEL (DeepSeek V4.1 Flash) with
    the same prompt (brief, rubric, text, comments; the pairs to check listed, and only those answered), in calls
    of CONFIRM_BATCH posts, ai_spend purpose <stage 1's>_verify. Its score is the one stored (scores.score,
    scores.model) and compared with the threshold; stage 1's is kept in scores.screen / screen_reason. Under SCREEN,
    stage 1's score stands. Filing, Telegram and WhatsApp come after both stages; live, units (both passes) and the
    history backfill alike. scored.defs does not cover the stage-2 model, so switching it on re-scored no history.
    Stage 2's answer also gives the post's sentiment and labels (scored.tag_model = the stage-2 model; never stage 1's for a post
    that reached stage 2), re-checks the streams a post is filed in, and judges its title (TITLE, confirm_title).
  - Where it goes: table `scores` (stream, post_id, score, reason, category); a post at or above the stream's
    threshold (kv `stream_thresholds`, default 50, set only with infra/thresholds.py) becomes a stream item
    (stream_items, which the stream page and Telegram read). 數據分析's stream profile is the same scored set at the
    current threshold, read from `scores` (insights.members), with the post's tags in table tags; sync_tags() keeps
    those rows to exactly that set (at every poller start and after a threshold change). A higher score later can
    bring a post in.
  - Filed means member, and nothing is recalled (Deyao, 2026-10-15: "there shouldn't be recalling of messages"): a
    post is a member of a stream when it scores at or above the threshold OR it was filed in that stream
    (stream_items). A filed item stays in 串流, its Telegram message stays as sent, and 數據分析 (insights.members,
    tags, sync_tags) and the item page keep it in the stream, the item page with its current score beside the score
    it was filed on. A rescore never takes a post out of a stream it was filed in; a lower score only stops a post
    that was never filed from becoming a member.
  - Complete data before an alert (Deyao, 2026-10-15): a news post is not scored (so not filed or sent) on its
    headline while its page can still be read: due() holds it until its page has been read (cached_at), by
    fulltext_now.py on sight or by the 30-minute `fulltext` job (proxy and browser hosts), for WAIT_TEXT at most.
    Only a host we never read (cache_fetch.SKIP_HOSTS: the social platforms, LinkedIn, Google News links) is scored on its
    feed text at once.
  - Near-empty posts (Deyao, 2026-10-15; thin.py): a post that says almost nothing once URLs, hashtags, @mentions and
    emoji are taken out (under thin.THRESHOLD units, counting its transcripts and picture text) is not scored while a
    transcript, picture text or a link's content may still come (thin.WAIT at most), then rejected: never scored,
    filed or sent; 「內容不足，未評分」 on the item page. plan() and backfill_once pass the due posts through
    thin.gate; due / unit_due / backfill_due leave out a post held or rejected on its current content.
  - Posts with comments, by unit (Deyao, 2026-10-14; twopass.py, shared by every source; LIHKG threads first): such a
    post is scored as ONE unit, the post and its comments (the newest REPLY_CHARS of them), against every stream
    (ai_spend purpose scoring_lihkg / scoring_discuss / scoring_units; calls of at most BATCH posts and BATCH_CHARS characters), twice
    at most: at capture (its comments read, or no fetcher reading them; FIRST_WAIT at most), and at published +
    30 min once its comments were re-read since (SECOND_WAIT after the mark without one); a post first scored after
    the mark is scored once. The later scoring is the current one; both are kept in score_passes; scored.comments_upto
    marks the comments the latest one saw (the item page's divider). Its comment rows are never scored or filed on
    their own (even with comment scoring on), so a post is one item in 串流 / 數據分析 and its stream channel gets one
    Telegram message: from the first scoring that passes.
  - Runs as a thread of the poller (app.py), stopping after the batch in hand when the poller drains.
  - History (backfill_*; Deyao, 2026-10-14: complete data first): a second thread scores every other non-comment post
    (those from before the switch, and those older than MAX_AGE when they came in, e.g. backfill.py's archive rows),
    seen at or after corpus.FLOOR only (never a post seen before the 20 Oct 2026 cutoff), the backlog posts too (a manual
    run: kv score_backfill.on), newest first, in the same batches with the same model and rubric, into `scores`, `scored` and the stream
    profiles' tags only: never stream_items (so never 串流 or Telegram) and never an alert. It is resumable and
    idempotent: a post is skipped once `scored` holds it with the current stream definitions (DEFS) and content
    hash. It yields to live scoring (it scores only while nothing live is due, and live runs in its own thread) and
    to nothing else: no reserve, no projection (Deyao, 2026-10-14: budgets are safety stops). Its spend is ai_spend
    purpose `scoring_backfill`; switch and state: kv `score_backfill` (infra/thresholds.py backfill [on|off]).
  - Hard stop: when OpenRouter refuses for the key's limit or credit (ai.LimitReached; ai.py records it and tells
    Deyao once), live scoring and the backfill pause (backfill state `limit`) and try again
    every LIMIT_WAIT seconds; nothing falls back to a cheaper path.


    python3 streams.py once        score what is due now (one batch round), print what was filed
"""
import hashlib, json, os, queue, re, sys, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import ai, corpus, links, thin, titles, twopass

HKT = corpus.HKT
DEFAULT_THRESHOLD = 50
FLOOR = 20          # the rubric's "unrelated" band ends here: stored, but the staff pages show only 「低於 20 分」
BATCH = 10          # posts per model call (full texts)
# model calls at once in the live scorer (loop(): continuous, each batch filed when its own calls are done): one at a time scored 2,700-3,700 posts
# an hour, and the 20 Oct 2026 restart's catch-up (3,300 posts an hour coming in) held the queue at 10,460 with its
# oldest ageing out of MAX_AGE unscored. 5 → 10 (20 Oct 18:30 HKT): a round waits for its slowest call (stage 2,
# a thinking model then), so a round of 5 took 40-150 s, 1,500-2,500 posts an hour, with 6,700 due (5,539 of them a
# new catalogue source's first read, 858 rescores after the full-text refill); live posts go first (newest id first)
SCORE_THREADS = 10
TEXT_CHARS = 4000   # of a post's text the model reads
TRANSCRIPT_CHARS = 4000  # of its video/audio transcripts (media.py), after the text
IMAGE_TEXT_CHARS = 2000  # of the text read from its meaningful pictures (images.py); the pictures are attached too
MAX_AGE = timedelta(days=2)  # posts published longer ago are not scored (feeds' first runs, old archive pages)
IDLE = 3            # seconds between checks when nothing is due (event-like: a post is scored seconds after its text lands)
WAIT_TEXT = timedelta(minutes=45)  # a news post whose page hasn't been read by then (fulltext_now.py, or the 30-minute
                                   # job for proxy and browser hosts) is scored on its feed text. From the page reads of
                                   # 14-15 Oct: the 30-minute job's hosts 123 of 125 within 30 min, the last at 47.9;
                                   # every page read later than that was a retry hours on (README: Complete data before AI)
DATE_WAIT = timedelta(minutes=75)  # an undated post whose page read (fulltext_now, the 30-minute job) hasn't happened by
                                   # then is scored and filed on its seen time (not from a source's first run)
STOP = threading.Event()  # the poller's drain: no new batch; the ones in hand finish and are filed
FILED = threading.Event()  # set when file() has committed new stream items (telegram_alerts waits on it)
SECOND_WAIT = timedelta(minutes=15)  # a unit's second scoring without a re-read of its comments: this long after the mark
FIRST_WAIT = timedelta(minutes=40)   # a unit a fetcher is still reading is scored at capture after this anyway
REPLY_CHARS = 20000  # of a unit's comments the model reads (the newest; a context-size limit, not a cost one)
BATCH_CHARS = 60000  # characters of unit posts per model call (the model's context)
LIHKG_PURPOSE = "scoring_lihkg"  # ai_spend purpose of LIHKG threads; other unit posts: UNIT_PURPOSE
DISCUSS_PURPOSE = "scoring_discuss"  # 香港討論區 threads (the whole site, 2026-10-15)
UNIT_PURPOSE = "scoring_units"


def clock():
    """now (HKT); tests move it"""
    return datetime.now(HKT)
BACKFILL_PURPOSE = "scoring_backfill"
# two-stage scoring (Deyao, 2026-10-20: a screen, then a second model rates again what passes): confirm(). 20 since 2026-10-23 (Deyao: "make the stage 2 score all
# above 20 (instead 40 that is now)"); TikTok videos keep their own 40 (tiktok_video.SCREEN)
SCREEN = 20          # stage 1's score from which stage 2 scores the pair again (every stream's threshold is >= this)
# the stage-2 model: DeepSeek V4.1 Flash at low thinking (Deyao, 2026-10-28: "run stage 2 on deepseek v4.1 flash with
# thinking set as low"). Open-weight models only: OpenRouter refuses Google and Anthropic models to YYC's account (403
# "violation of provider Terms Of Service") and restricts it from closed-weight models (Anthropic, Google, OpenAI).
# Env CONFIRM_MODEL also takes DeepSeek V4 Pro (DEEPSEEK); each one's options are below
DEEPSEEK = "deepseek/deepseek-v4-pro-0813"
DEEPSEEK_FLASH = "deepseek/deepseek-v4.1-flash"
CONFIRM_MODEL = os.environ.get("CONFIRM_MODEL", DEEPSEEK_FLASH)
# each stage-2 model's OpenRouter options.
# V4 Pro: DeepSeek's own endpoint is barred by the account's data policy (no
# training on paid data; OpenRouter's 404 "Paid model training violation", 2026-10-20) and the cheapest endpoints are
# fp4 quantisations (the first live calls, DeepSeek first with fallbacks, went to Relace fp4, USD 0.20 / 8.00 per M tokens in / out), so:
# the cheapest endpoint that is not fp4 (Ionstream, USD 0.37 / 2.93, on 2026-10-20), the next ones when it is down.
# Thinking at effort high (OpenRouter's default for the model, and the mode Artificial Analysis scored) is asked for
# explicitly: Ionstream's first answers had 0 reasoning tokens without it. Thinking counts against max_tokens (GLM 5.3,
# also a thinking model, spent all of 4,000 tokens thinking in the 20 Oct pilot), and runs long: live on 20 Oct up to
# 13,089 output tokens and 177 s for one call, against the 180 s every call had. So max_tokens 32,000, timeout 600 s
CONFIRM_OPTIONS = {
    DEEPSEEK: {"provider": {"quantizations": ["fp8", "fp16", "bf16", "fp32", "unknown"],
                            "sort": "price",
                            # mainland content filters (censorship check, 20 Oct: canned
                            # refusals, answers cut off, 2 of 5 batches blocked)
                            "ignore": ["streamlake", "alibaba"]},
               "reasoning": {"effort": "high"}, "max_tokens": 32000, "timeout": 600},
    # open weights: the cheapest fp8-or-better endpoint, never a mainland provider's (their content filter sits in
    # front of the model; Deyao, 2026-10-28), thinking low; thinking counts against max_tokens
    DEEPSEEK_FLASH: {"provider": ai.OPEN_WEIGHT_ROUTE, "reasoning": {"effort": "low"}, "max_tokens": 32000, "timeout": 600},
}
# stage 1's OpenRouter options: no thinking (Deyao, 2026-10-27: "turn off thinking for both models"; ai.chat)
SCORE_OPTIONS = {"reasoning": {"enabled": False}}
CONFIRM_BATCH = 10   # posts per stage-2 call (README "Two-stage scoring": batched vs single-post measured)
CONFIRM_TOKENS = 4000
# stage 1's output limit: every stream of every post in the answer (21 Oct 2026); 3,000 held answers that listed only
# the streams at 20 or more (24 h to 21 Oct: 727 tokens a call on average, 2,910 the most); listing all 7 scored
# streams of 10 posts adds about 10 x 7 x 25 tokens
SCORE_TOKENS = 8000
VERIFY = "_verify"   # stage 2's ai_spend purpose = stage 1's + this (scoring_verify, scoring_lihkg_verify, …)
# stage 2 also judges each post's title (Deyao, 2026-10-20: the AI title from the same stage-2 call); one general
# instruction (Deyao, 2026-10-23: "design the prompt so that it achieves the effect"): a post with no title of its own
# is shown as having none (post_line: NO_TITLE where its title would be), typography is titles.clean's; stored with
# titles.store, kinds per confirm_title()
TITLE = """Titles: for every post below that is not a comment, also judge whether its title says what the post is
about, as a headline: add "title_ok": true when it does; else add "title_ok": false, "title_kind" and "title":
"extracted" with the post's own opening headline copied exactly when it has one, else "written" with a short,
factual headline in the post's language (Traditional Chinese with Hong Kong usage for a post in Chinese).
"""
KEY_TTL = 120             # seconds the OpenRouter key's balance is cached
LIMIT_WAIT = 600          # seconds between tries while OpenRouter refuses for the key's limit or credit

RUBRIC = """Score = how relevant the post is to the topic, 0–100:
  90–100  the topic is the post's main subject
  60–89   the topic's subject acts, is quoted, or is a substantial part of the post
  20–59   a passing mention, or a neighbouring subject without the topic's own
  0–19    unrelated"""
# the rubric as defs_hash knows it (scored.defs): the bands are those of 14 Oct, with 20–59's example dropped on 22 Oct
# (Deyao; that rescored history), worded for topics on 23 Oct (each topic = definition + background); on 21 Oct only the instruction to
# leave 0–19 streams out moved out of it (into PROMPT: every stream is answered now), which changes no score's
# meaning, so no history is rescored for it
DEFS_RUBRIC = RUBRIC.replace("0–19    unrelated", "0–19    unrelated (leave the stream out of your answer)")

PROMPT = """You judge how relevant Hong Kong news, forum and social media posts are to YYC's monitoring topics.

{rubric}

Topics. Each has two parts: its definition (what the topic means) and its background (its names, people, history
and current state); some also list categories:
{topics}

For every post: its sentiment toward its main subject (one of {sentiments}) and zero or more labels from
{labels}
and, for each topic above, a score from 0 to 100 of the post's relevance to that topic (per the rubric), a reason
of at most 25 characters in Traditional Chinese (Hong Kong usage) saying why, and, where the topic lists categories,
exactly one of them.
Answer JSON only:
{{"posts": [{{"i": 0, "sentiment": "neutral", "labels": [], "streams": [{example}]}}]}}

Posts:
{posts}"""
# the answer template names every topic (2026-10-28: Qwen3.7 Flash, given one example entry, answered about one
# topic per post, its main one, and left the rest out: 22 of 224 filed pairs lost)


def answer_example(streams):
    """stage 1's "streams" template: one entry per topic, in the topics' order"""
    return ", ".join('{"id": "%s", "score": …, "reason": "…"%s}' % (st["id"], ', "category": "…"' if st.get("categories") else "")
                     for st in streams)


_defs = {"at": 0, "list": None}


def all_streams():
    """streams.json (reread every minute)"""
    if _defs["list"] is None or time.time() - _defs["at"] > 60:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "streams.json")) as f:
            _defs.update(at=time.time(), list=json.load(f))
    return _defs["list"]


def defs_hash(streams):
    """what a score depends on besides the post: the streams' ids, names, briefs, background briefs and categories, the rubric and the
    model. Stored with each scored post (scored.defs); the backfill re-scores a post stored with other definitions"""
    d = [[st["id"], st.get("name"), st.get("brief"), st.get("categories")] + ([b] if (b := background(st["id"])) else [])
         for st in streams]
    return hashlib.md5(json.dumps([d, DEFS_RUBRIC, ai.MODEL], ensure_ascii=False).encode()).hexdigest()[:16]


# ---- settings (kv; written only by infra/thresholds.py and the first start) ----

def thresholds():
    return corpus.kv_get("stream_thresholds", {}) or {}


def threshold(stream_id):
    """the stream's threshold"""
    return int(thresholds().get(stream_id, DEFAULT_THRESHOLD))


def shown(stream_id, score):
    """does a post with this score belong on the stream (page, Telegram, dashboard)? Items from before scoring have
    no score and stay"""
    return score is None or score >= threshold(stream_id)


def settings():
    """kv `scoring`: from_id (only posts after it are scored: the switch's 'new posts only'), comments (score
    comment/reply rows too). Made on the first call: from the newest post at that moment"""
    s = corpus.kv_get("scoring")
    if not s:
        top = corpus.db().execute("SELECT coalesce(max(id), 0) FROM posts").fetchone()[0]
        s = {"from_id": top, "comments": False, "since": datetime.now(HKT).isoformat(timespec="seconds")}
        corpus.kv_set("scoring", s)
    return s


# ---- what is due ----

# the content a post was scored on: title + text, and the transcripts of its video/audio once they exist (media.py;
# a post without one hashes as before the transcripts came in, so nothing is rescored for the change of formula)
HASH = ("md5(coalesce(p.title, '') || chr(10) || coalesce(p.text, '') || coalesce(chr(10) || p.transcript, '')"
        " || coalesce(chr(10) || 'img:' || p.image_text, ''))")  # and the text of its meaningful pictures (images.py)


def due(limit, s=None):
    """posts after from_id, fresh enough, never scored or changed since (newest first, so a backlog never delays
    today's news), and READY: the model reads complete data (Deyao, 2026-10-14), so a news post waits for its page
    read (cached_at: fulltext_now.py on sight, or the 30-minute job for proxy and browser hosts; so does a post of
    fulltext_fetch.PAGE_SIDS, a headline linking its story's page) unless its feed
    already carried the article, its host is one we never read (cache_fetch.SKIP_HOSTS), or WAIT_TEXT has passed
    (Deyao, 2026-10-15: no alert on a headline whose page can still be read; a TikHub post, which no page read
    covers, doesn't wait). A post with no publish time waits for its page read (which fills the time the page states)
    unless no page read covers it, or DATE_WAIT has passed: an undated post is never
    taken as fresh on its seen time before its page was tried (the POL incident, 14 Oct 2026). A post whose content
    is only URLs is held until links.py has read what it links to (links.HELD_SQL; Deyao, 2026-10-15)"""
    sql, args = _due_sql(s)
    return [dict(r) for r in corpus.db().execute(sql + " ORDER BY p.id DESC LIMIT %s", args + [limit])]


def due_counts(s=None):
    """the monitor's queue (the same posts as due()): {"live": due posts that can become stream items, "first_run":
    due backlog posts (a source's first read, or its first after a gap: only when a manual run let them in, infra/
    backlog.py --first-run; scored for 數據分析 only, never filed), "oldest_live": the oldest live one's seen time}"""
    sql, args = _due_sql(s)
    r = corpus.db().execute(f"""SELECT count(*) FILTER (WHERE first_run IS NOT TRUE) AS live,
                                       count(*) FILTER (WHERE first_run) AS first_run,
                                       min(seen) FILTER (WHERE first_run IS NOT TRUE) AS oldest_live
                                FROM ({sql}) d""", args).fetchone()
    return {"live": r["live"], "first_run": r["first_run"], "oldest_live": r["oldest_live"]}


def _due_sql(s=None):
    """due()'s query (no order or limit) and its arguments"""
    import fulltext_now
    s = s or settings()
    now = clock()
    cut = (now - MAX_AGE).isoformat(timespec="seconds")
    never = "(^|\\.)(" + "|".join(re.escape(h) for h in fulltext_now.cache_fetch.SKIP_HOSTS) + ")$"
    thread = "" if s.get("comments") else " AND p.thread IS NULL"
    # c: the cheap conditions first, MATERIALIZED so the planner can't run the regexes below over every recent post
    # (due() 7.1 s → 0.69 s live, 29 Oct 2026)
    sql = f"""WITH c AS MATERIALIZED (
                SELECT p.id FROM posts p LEFT JOIN scored s ON s.post_id = p.id
                WHERE p.id > %s AND coalesce(p.published, p.seen) >= %s AND {corpus.seen_cond("p", "scoring")}{thread}
                  AND (s.post_id IS NULL OR s.hash <> {HASH}))
              SELECT p.*, {HASH} AS hash FROM posts p LEFT JOIN scored s ON s.post_id = p.id
              WHERE p.id IN (SELECT id FROM c)
                AND p.id > %s AND coalesce(p.published, p.seen) >= %s AND {corpus.seen_cond("p", "scoring")}
                AND (s.post_id IS NULL OR s.hash <> {HASH}) AND NOT {links.HELD_SQL}
                AND NOT {thin.excluded_sql("p", HASH)}
                AND NOT {twopass.unit_cond("p")} AND NOT {twopass.comment_cond("p")}
                AND (p.thread IS NOT NULL OR (coalesce(p.source_type, '') <> ALL(%s) AND coalesce(p.sid, '') <> ALL(%s))
                     OR length(coalesce(p.text, '')) >= %s OR p.cached_at IS NOT NULL OR p.seen <= %s
                     OR coalesce(p.sid, '') LIKE 'tikhub%%' OR lower(split_part(p.url, '/', 3)) ~ %s)
                AND (p.published IS NOT NULL OR p.cached_at IS NOT NULL OR p.seen <= %s OR p.thread IS NOT NULL
                     OR coalesce(p.source_type, '') <> ALL(%s) OR coalesce(p.sid, '') LIKE 'tikhub%%'
                     OR lower(split_part(p.url, '/', 3)) ~ %s)"""
    types = list(fulltext_now.fulltext_fetch.SOURCE_TYPES)
    # a Forum post here is no thread (unit_cond above): a forum page the full-text job reads, held like news for its text
    args = [s["from_id"], cut, s["from_id"], cut, now.isoformat(timespec="seconds"), types + ["Forum"], list(fulltext_now.fulltext_fetch.PAGE_SIDS), fulltext_now.FULL_CHARS, (now - WAIT_TEXT).isoformat(timespec="seconds"), never,
            (now - DATE_WAIT).isoformat(timespec="seconds"), types, never]
    return sql + thread, args


def unit_due(limit, s=None):
    """posts scored as units with their comments (twopass.UNIT_URLS: LIHKG threads, …) due now: never scored and
    captured (no fetcher reading it, its comments read, or FIRST_WAIT since seen; an old post captured now counts on
    its seen time), or their second scoring is due (twopass: due_at passed, comments re-read since, or SECOND_WAIT
    after due_at). Newest first; each with its comments (comments_text, comments_n, comments_upto)"""
    s = s or settings()
    now = clock()
    # c: as in _due_sql, the cheap conditions first (unit_due 2.59 s → 0.39 s live, 29 Oct 2026), as its two cases: never
    # scored and seen since the cut (posts_live_seen through the text prefilter, a day wider than the cut so it drops
    # nothing), or the second scoring due (from two_pass). One OR over both joined every post since the cut with scored
    # and two_pass, slower than before once the cut aged (2.6-4.8 s at the 10-20 floor)
    sql = f"""WITH c AS MATERIALIZED (
                SELECT p.id FROM posts p LEFT JOIN scored s ON s.post_id = p.id
                WHERE p.id > %(from_id)s AND p.thread IS NULL AND {corpus.seen_cond("p", "scoring")} AND s.post_id IS NULL
                  AND p.seen >= %(cuttext)s AND p.seen::timestamptz >= %(cut)s
                UNION
                SELECT p.id FROM two_pass tp JOIN posts p ON p.url = tp.url JOIN scored s ON s.post_id = p.id
                WHERE tp.first_at IS NOT NULL AND tp.second_at IS NULL AND tp.due_at <= %(now)s
                  AND p.id > %(from_id)s AND p.thread IS NULL AND {corpus.seen_cond("p", "scoring")})
              SELECT p.*, {HASH} AS hash FROM posts p LEFT JOIN scored s ON s.post_id = p.id
              LEFT JOIN two_pass tp ON tp.url = p.url
              WHERE p.id IN (SELECT id FROM c) AND p.id > %(from_id)s AND {twopass.unit_cond("p")} AND {corpus.seen_cond("p", "scoring")} AND (
                (s.post_id IS NULL AND p.seen::timestamptz >= %(cut)s AND NOT {links.HELD_SQL}
                 AND NOT {thin.excluded_sql("p", HASH, "%(nowtext)s")}
                 AND ((tp.reading_at IS NULL AND NOT {twopass.fetcher_cond("p")}) OR tp.captured_at IS NOT NULL
                      OR p.seen::timestamptz <= %(first)s))
                OR (s.post_id IS NOT NULL AND tp.first_at IS NOT NULL AND tp.second_at IS NULL AND tp.due_at <= %(now)s
                    AND (tp.fetched_at IS NOT NULL OR tp.due_at + %(wait)s <= %(now)s)))
              ORDER BY p.id DESC LIMIT %(limit)s"""
    rows = [dict(r) for r in corpus.db().execute(sql, {
        "from_id": s["from_id"], "cut": now - MAX_AGE, "cuttext": (now - MAX_AGE - timedelta(days=1)).astimezone(HKT).isoformat(timespec="seconds"),
        "first": now - FIRST_WAIT, "now": now, "wait": SECOND_WAIT,
        "nowtext": now.isoformat(timespec="seconds"),
        "limit": limit})]
    for p in rows:
        p["unit"] = True
        p["replies_text"], p["comments_n"], p["comments_upto"] = thread_replies(p["url"])
    return rows


def thread_replies(url):
    """a unit post's comments as the model reads them: the newest REPLY_CHARS characters' worth, oldest first → (texts,
    how many comment rows the post has, the newest comment row's id: what this scoring saw)"""
    out, n, count, upto = [], 0, 0, None
    full = False
    for r in corpus.db().execute("""SELECT id, text FROM posts WHERE thread=%s
                                    ORDER BY coalesce(published, seen) DESC, id DESC""", (url,)):
        count += 1
        upto = max(upto or 0, r["id"])
        t = re.sub(r"\s+", " ", r["text"] or "").strip()
        if not t or full:
            continue
        if n + len(t) > REPLY_CHARS:
            full = True
            continue
        out.append(t)
        n += len(t) + 3
    return list(reversed(out)), count, upto


def char_batches(posts):
    """LIHKG threads in calls of at most BATCH posts and BATCH_CHARS characters (a thread longer alone gets its own)"""
    out, cur, n = [], [], 0
    for p in posts:
        size = len(post_line(len(cur), p))
        if cur and (len(cur) >= BATCH or n + size > BATCH_CHARS):
            out.append(cur)
            cur, n = [], 0
        cur.append(p)
        n += size
    return out + ([cur] if cur else [])


# ---- the model ----

# each topic is two parts (Deyao, 2026-10-23): its definition (streams.json `brief`: what the topic means) and its
# background (briefs/<id>.md, written from briefs/prompts/TEMPLATE.txt with [n] citations and a source list; the
# scorer reads it with those stripped). A stream with no background file has the definition alone. defs_hash covers
# both, so editing either rescores history.
BRIEFS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "briefs")
_briefs = {}


def strip_citations(md):
    """a sourced brief as the scorer reads it: no [n] markers, no source list (from its "## Sources" heading on)"""
    md = re.split(r"(?m)^#{1,3}\s*(?:Sources|References)\b.*$", md, maxsplit=1)[0]
    md = re.sub(r"[ \t]*(?:\[\d+(?:\s*[,–-]\s*\d+)*\])+", "", md)
    return re.sub(r"\n(?:\s*---\s*\n)+\s*$", "\n", md).strip()


def background(stream_id):
    """the stream's stripped background brief, or None (reread when the file changes)"""
    path = os.path.join(BRIEFS, f"{stream_id}.md")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if _briefs.get(stream_id, (None,))[0] != mtime:
        with open(path, encoding="utf-8") as f:
            _briefs[stream_id] = (mtime, strip_citations(f.read()))
    return _briefs[stream_id][1]


def topic_blocks(streams):
    """each stream as a topic: its definition, its background (when it has one) and its categories"""
    out = []
    for st in streams:
        parts = [f'<topic id="{st["id"]}" name="{st["name"]}">', "<definition>" + (st.get("brief") or st["name"]) + "</definition>"]
        if b := background(st["id"]):
            parts.append(f"<background>\n{b}\n</background>")
        if st.get("categories"):
            parts.append("<categories>" + " · ".join(st["categories"]) + "</categories>")
        out.append("\n".join(parts + ["</topic>"]))
    return "\n\n".join(out)


NO_TITLE = "（無標題）"  # post_line: where a post with no title of its own (titles.own) would show its title


def post_line(i, p):
    where = p.get("website") or p.get("source") or ""
    if p.get("board") or p.get("section"):
        where += "·" + (p.get("board") or p.get("section"))
    text = re.sub(r"\s+", " ", p.get("text") or "").strip()[:TEXT_CHARS]
    said = re.sub(r"\s+", " ", p.get("transcript") or "").strip()[:TRANSCRIPT_CHARS]
    said = f"\n   影片逐字稿：{said}" if said else ""
    seen = re.sub(r"\s+", " ", p.get("image_text") or "").strip()[:IMAGE_TEXT_CHARS]
    said += f"\n   圖片文字：{seen}" if seen else ""
    if p.get("thread"):  # a comment or reply: its thread's headline is its subject
        return f"{i}. [{where}] 留言（帖子：{p.get('title') or ''}）：{text}{said}"
    if p.get("replies_text"):  # a LIHKG thread: scored as one unit with its replies
        said += "\n   回覆：" + " ／ ".join(p["replies_text"])
    # a post with no title of its own is shown as having none (its stored title is an excerpt of its text): with no
    # title in its line the model judged the text's opening as one (title_ok on 75 % of them, 29 Oct 2026)
    title = titles.own(p)
    bare = not text or (title and titles.title_only(text, title))
    # a headline alone gives the model nothing to tell its subject by: the page's address says where the outlet filed it
    # (sports, entertainment, …). 3 Nov 2026: Sina's 「啟程伊斯坦堡」 (Liverpool off to Istanbul), scored on its title
    # before its page was read, went to CEHK as the Chief Executive's trip; its address was …/sports/…/liverpool/…
    where_filed = f"\n   網址：{address(p.get('url'))}" if bare and not said and address(p.get("url")) else ""
    return (f"{i}. [{where}] {title or NO_TITLE}" + ("" if bare else f"\n   {text}") + where_filed + said)


def address(url):
    """a page's address as the model reads it: host and path, percent-decoded, without the query or fragment"""
    m = re.match(r"https?://(?:www\.)?([^/?#]+)([^?#]*)", url or "")
    if not m:
        return ""
    return (m.group(1) + urllib.parse.unquote(m.group(2)).rstrip("/"))[:300]


def cached_chars(streams, prompt):
    """how much of a scoring prompt is the same in every call: up to the end of its topics (ai.jsonc's cached)"""
    t = topic_blocks(streams)
    i = prompt.find(t)
    return i + len(t) if i >= 0 else 0


def score_prompt(streams, posts, titles=False):
    """the scorer's prompt for a batch, the same for both stages: nothing in it says which stage asks, why a post is
    in the batch or what another model answered (Deyao, 2026-11-03: which posts go to stage 2 and which of its scores
    are used is the code's business, never the prompt's; a "Check: post 0: CEHK" list told stage 2 what stage 1 had
    suspected, and on a bare headline that hint was the score). titles: stage 2's own extra task (TITLE)"""
    prompt = PROMPT.format(rubric=RUBRIC, topics=topic_blocks(streams),
                           sentiments=", ".join(ai.SENTIMENTS), labels=ai.label_lines(),
                           example=answer_example(streams),
                           posts="\n".join(post_line(i, p) for i, p in enumerate(posts)))
    if titles:
        head, tail = prompt.rsplit("\nPosts:\n", 1)
        prompt = head + "\n" + TITLE + "\nPosts:\n" + tail
    return prompt


def _ask(streams, posts, purpose, model=None, checks=None):
    """one scoring call (two tries on a non-JSON answer) → {index: {"sentiment", "labels", "streams": {stream id:
    {score, reason, category}}}}, or None when both answers were not JSON. Every score is kept, 0–19 too; a stage-2
    call (checks given) keeps only the pairs in checks (it is asked about every topic, like stage 1). A stage-2 call (model given) that fails for any reason but OpenRouter's limit (an outage, a
    provider's filter error) is None too: its posts stay due, the round's other batches go on"""
    ids = {st["id"]: st for st in streams}
    prompt = score_prompt(streams, posts, titles=checks is not None)
    prompt = ai.Prompt(prompt, cached_chars(streams, prompt))
    try:  # the scorer reads pictures: each post's meaningful ones go with the batch (none while flag pictures is off)
        import images
        pics = images.for_scoring(posts)
    except Exception as e:
        print("scoring: pictures not attached:", repr(e)[:160], flush=True)
        pics = []
    res = None
    for attempt in (1, 2):
        try:
            opts = CONFIRM_OPTIONS.get(model, {}) if model else SCORE_OPTIONS
            res = ai.jsonc(prompt, purpose, max_tokens=opts.get("max_tokens", CONFIRM_TOKENS if model else SCORE_TOKENS),
                           **({"model": model} if model else {}), **({"images": pics} if pics else {}),
                           **{k: opts[k] for k in ("provider", "reasoning", "timeout") if opts.get(k) is not None})
            break
        except ValueError as e:
            print(f"scoring: JSON error ({e}), attempt {attempt}{' (' + model + ')' if model else ''}", flush=True)
            if model and STOP.is_set():  # draining: no second stage-2 call (up to CONFIRM_OPTIONS' 600 s); due again
                return None
        except ai.LimitReached:
            raise
        except Exception as e:  # stage 2 only: this batch fails, not the round
            if not model:
                raise
            print(f"scoring: {model} failed ({str(e)[:200]}); the batch's posts stay due", flush=True)
            return None
    if res is None:
        return None
    out = {}
    for t in res.get("posts", []):
        i = t.get("i")
        if not isinstance(i, int) or not 0 <= i < len(posts):
            continue
        keep = set((checks or {}).get(i) or ())
        got = {}
        for x in t.get("streams") or []:
            st = ids.get(x.get("id"))
            try:
                score = max(0, min(100, int(x.get("score"))))
            except (TypeError, ValueError):
                continue
            if not st or (checks is not None and st["id"] not in keep):
                continue
            cat = x.get("category") if x.get("category") in (st.get("categories") or []) else None
            got[st["id"]] = {"score": score, "reason": str(x.get("reason") or "")[:60], "category": cat}
        out[i] = {"sentiment": t.get("sentiment") if t.get("sentiment") in ai.SENTIMENTS else None,
                  "labels": [l for l in t.get("labels") or [] if l in ai.LABELS], "streams": got,
                  "raw": {k: v for k, v in t.items() if k != "i"}}  # the model's own answer for the post (scored.answers)
        if checks is not None and isinstance(t.get("title_ok"), bool):  # stage 2's title judgement (TITLE)
            out[i]["title"] = {"ok": t["title_ok"], "kind": t.get("title_kind"), "title": t.get("title")}
    return out


def score_posts(streams, posts, purpose="scoring"):
    """both stages → {index: {"sentiment", "labels", "streams": {stream id: {score, reason, category, model, screen,
    screen_reason}}}}. Stage 1: ai.MODEL scores every post against every stream. Stage 2 (confirm): every pair
    stage 1 scored at or above SCREEN is scored again by CONFIRM_MODEL, whose score is the one kept. A batch whose
    stage-1 answer is not JSON twice is left out, and so is a post stage 2 could not score (those posts stay due and
    are tried on the next round)"""
    res = _ask(streams, posts, purpose)
    if not res:
        return {}
    for r in res.values():
        r["tag_model"] = ai.MODEL
        # what each stage's model answered for the post, as it answered (Deyao, 2026-10-21: "i want to see the tags
        # etc that the model returns"): scored.answers, shown on the proof view
        r["answers"] = {"stage1": {"model": ai.MODEL, "answer": r.pop("raw", None)}}
        for sc in r["streams"].values():
            sc.update(model=ai.MODEL, screen=None, screen_reason=None)
    note_stage1(posts, res, purpose)
    import tiktok_video  # Deyao, 2026-10-22: a TikTok post at or above SCREEN gets its video downloaded and kept
    tiktok_video.queue_stage1(posts, res)
    return confirm(streams, posts, res, purpose)


def note_stage1(posts, res, purpose):
    """the live dashboard's Stage 1 panel (events.py): every scored post, every stream's score and reason"""
    import events
    for i, r in res.items():
        sc = r.get("streams") or {}
        order = sorted(sc, key=lambda k: -(sc[k].get("score") or 0))
        best = order[0] if order else None
        events.emit("stage1", posts[i].get("id"), model=ai.MODEL, purpose=purpose,
                    scores={k: v.get("score") for k, v in sc.items()}, best=best,
                    reason=sc[best].get("reason") if best else None,
                    reasons={k: (sc[k].get("reason") or "")[:200] for k in order})  # (JSONB keeps no order: the page sorts by score)


def note_stage2(p, r, checked, purpose, failed=None):
    """the live dashboard's Stage 2 panel (events.py): per checked stream stage 1's and stage 2's score and reason"""
    import events
    if failed:
        events.emit("stage2", p.get("id"), model=CONFIRM_MODEL, purpose=purpose, failed=failed)
        return
    sc = r.get("streams") or {}
    events.emit("stage2", p.get("id"), model=CONFIRM_MODEL, purpose=purpose, sentiment=r.get("sentiment"),
                labels=r.get("labels"),
                checks={k: {"s1": (sc.get(k) or {}).get("screen"), "s2": (sc.get(k) or {}).get("score"),
                            "reason": (sc.get(k) or {}).get("reason"),
                            "s1reason": (sc.get(k) or {}).get("screen_reason")} for k in checked})


def confirm(streams, posts, res, purpose):
    """stage 2 (Deyao, 2026-10-20): the pairs stage 1 scored at or above SCREEN, scored again by CONFIRM_MODEL with
    the same prompt (score_prompt: every topic of every post, nothing about stage 1; the posts to check only,
    CONFIRM_BATCH a call; the code keeps the checked pairs), ai_spend purpose <purpose>_verify. Its score, reason and
    category replace stage 1's (stage 1's kept as screen / screen_reason), and its sentiment and labels the post's. A
    checked stream it leaves out scored under the floor (0, said in the reason). A post it gives no answer for (a
    non-JSON answer twice, or the post missing from it) is taken out of res: not stored, not filed, due again"""
    filed = filed_streams(streams, [posts[i] for i in res])
    checks = {i: sorted({sid for sid, sc in r["streams"].items() if sc["score"] >= SCREEN}
                        | filed.get(posts[i].get("url"), set())) for i, r in res.items()}
    order = sorted(i for i, v in checks.items() if v)
    for k in range(0, len(order), CONFIRM_BATCH):
        part = order[k:k + CONFIRM_BATCH]
        if STOP.is_set():  # the poller's drain: no new stage-2 call (one runs up to 6 min); these posts stay due and
            # the next pod scores them, so the drain stays inside deploy/apps/poller.yaml's grace period
            for i in order[k:]:
                res.pop(i, None)
            print(f"scoring: draining; {len(order) - k} post(s) left for the next poller's stage 2", flush=True)
            break
        got = _ask(streams, [posts[i] for i in part], purpose + VERIFY, model=CONFIRM_MODEL,
                   checks={j: checks[i] for j, i in enumerate(part)}) or {}
        for j, i in enumerate(part):
            o = got.get(j)
            if o is None:
                print(f"scoring: {CONFIRM_MODEL} gave no answer for post {posts[i].get('id')}; it stays due", flush=True)
                note_stage2(posts[i], res[i], checks[i], purpose, failed="no answer from the model; scored again next round")
                res.pop(i)
                continue
            for sid in checks[i]:
                h = res[i]["streams"].get(sid)
                if h is None or h["score"] < SCREEN:  # checked only because the post is filed in it
                    if sid in o["streams"]:
                        res[i]["streams"][sid] = dict(o["streams"][sid], model=CONFIRM_MODEL,
                                                      screen=h and h["score"], screen_reason=h and h["reason"])
                    continue  # stage 2 under the floor: stage 1's entry (or none) stands, as before
                x = o["streams"].get(sid) or {"score": 0, "reason": f"覆核：低於 {FLOOR} 分", "category": None}
                res[i]["streams"][sid] = dict(x, model=CONFIRM_MODEL, screen=h["score"], screen_reason=h["reason"])
            # the post's tags are stage 2's (Deyao, 2026-10-20), never stage 1's: an invalid sentiment is stored as none
            res[i].update(sentiment=o["sentiment"], labels=o["labels"], tag_model=CONFIRM_MODEL)
            res[i].setdefault("answers", {})["stage2"] = {"model": CONFIRM_MODEL, "answer": o.get("raw")}
            note_stage2(posts[i], res[i], checks[i], purpose)
            try:
                confirm_title(posts[i], o.get("title"))
            except Exception as e:
                print(f"scoring: title from {CONFIRM_MODEL} not stored for post {posts[i].get('id')}: {e!r}"[:300], flush=True)
    return res


def filed_streams(streams, posts):
    """{url: {stream id}} the posts are filed in (stream_items): stage 2
    scores those pairs again whatever stage 1 says, so a filed post's tags stay stage 2's when it is rescored"""
    ids = {st["id"] for st in streams}
    urls = [p.get("url") for p in posts if p.get("url")]
    out = {}
    if not urls:
        return out
    for url, sid in corpus.db().execute("SELECT DISTINCT url, stream FROM stream_items WHERE url = ANY(%s)", (urls,)).fetchall():
        if sid in ids:
            out.setdefault(url, set()).add(sid)
    return out


def confirm_title(p, t):
    """stage 2's title judgement for a post (TITLE) → stored with titles.store, model CONFIRM_MODEL: title_ok → no AI
    title (checked on this content), for a post with a title of its own only (titles.own; one shown with none has
    nothing to call fine); else the stage-2 model's title: "extracted" (the post's own opening headline, checked to be
    there), "retitled" (the post has a title of its own: kept and shown as 來源標題) or "written" (it has none). No
    judgement, or no usable title: left to titles.ensure, which titles a post stage 2 reached (score_job). A comment
    and a post that is only a link (its linked page's title, titles.linked) are titles.py's"""
    if not t or p.get("thread") or titles.linked(p):
        return
    if t["ok"]:
        if titles.own(p):
            titles.store(p, None, force=True)
        return
    text = titles.raw(p)
    if t.get("kind") == "extracted":
        e = titles.extracted(t.get("title"), text)
        if e:
            titles.store(p, e, "extracted", CONFIRM_MODEL)
            return
    c = titles.clean(t.get("title"), text)
    if c:
        titles.store(p, c, "retitled" if titles.own(p) else "written", CONFIRM_MODEL)


# ---- filing ----

def norm_title(t):
    t = re.split(r"\s[-|｜]\s", t or "")[0]
    return re.sub(r"[\s\W_]+", "", t.lower())[:24]


def slot(dt):
    """the half-hour batch an item is posted in (the next :00 or :30)"""
    dt = dt.astimezone(HKT).replace(second=0, microsecond=0)
    return (dt.replace(minute=0) + timedelta(minutes=30 if dt.minute < 30 else 60)).isoformat(timespec="minutes")


def item_day(p):
    return datetime.fromisoformat(p.get("published") or p["seen"]).astimezone(HKT).strftime("%Y-%m-%d")


def stream_item(p, sid, sc, tag, now):
    return {"stream": sid, "post_id": p["id"], "sid": p.get("sid"), "source": p.get("source"), "website": p.get("website"),
            "section": p.get("section") or p.get("board"), "title": p.get("title"), "url": p["url"],
            "text": (p.get("text") or "")[:2000] or None, "published": p.get("published"),
            "seen": now.isoformat(timespec="seconds"), "slot": slot(now), "sentiment": tag["sentiment"],
            "labels": tag["labels"], "score": sc["score"], "reason": sc["reason"], "category": sc["category"],
            "model": sc.get("model"), "screen": sc.get("screen"), "tag_model": tag.get("tag_model"), "relevant": True}


def fileable(p, now):
    """may this post become a stream item (串流, Telegram)? Not when it came in with its source's first run (a new or
    reset sid: scored for 數據分析 only), nor when its publish time is older than MAX_AGE (a date its page gave after
    ingest). An undated post reaches here only after its page was tried (due())"""
    if p.get("first_run"):
        return False
    if p.get("published"):
        try:
            return now - datetime.fromisoformat(p["published"]) <= MAX_AGE
        except ValueError:
            return False
    return True


_all_from = {"set": False}  # kv scores_all_from (written once): when every stream's score started being stored


def file(streams, posts, res, history=False, backpost=False):
    """write the scores, the stream items and the stream profiles' tags → [items filed]. history (the backfill):
    scores and tags only; no stream item (串流, Telegram), so []. backpost: an explicit back-post Deyao asked for (e.g. a new stream's history): its
    posts are filed even from a source's first run or older than MAX_AGE (fileable), marked "backpost" on the item;
    nothing else files those (tools/stream_history.py is the other explicit path)"""
    ids = [st["id"] for st in streams]
    now = clock()
    defs = defs_hash(streams)
    at = now.isoformat(timespec="seconds")
    c = corpus.db()
    items = []
    if not _all_from["set"]:  # the first scoring that stores every stream's score: the proof view's cutoff
        c.execute("INSERT INTO kv (k, v) VALUES ('scores_all_from', %s) ON CONFLICT (k) DO NOTHING", (corpus.J(at),))
        _all_from["set"] = True
    with c:
        recent = {}  # per stream: normalised headlines of its last 2 days (Google News and a direct feed carry the same story)
        for st in streams:
            rows = c.execute("SELECT item->>'title' FROM stream_items WHERE stream=%s AND day >= %s",
                             (st["id"], (now - timedelta(days=1)).strftime("%Y-%m-%d"))).fetchall()
            recent[st["id"]] = {norm_title(r[0]) for r in rows}
        for i, p in enumerate(posts):
            if i not in res:
                continue
            tag = res[i]
            c.execute("""INSERT INTO scored (post_id, hash, sentiment, labels, at, defs, tag_model, all_scores, answers)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s)
                         ON CONFLICT (post_id) DO UPDATE SET hash=excluded.hash, sentiment=excluded.sentiment,
                         labels=excluded.labels, at=excluded.at, defs=excluded.defs, tag_model=excluded.tag_model,
                         all_scores=TRUE, answers=excluded.answers""",
                      (p["id"], p["hash"], tag["sentiment"], json.dumps(tag["labels"]), at, defs, tag.get("tag_model"),
                       corpus.J(tag.get("answers")) if tag.get("answers") else None))
            if p.get("unit") and not history:  # a post scored with its comments: the two-pass record (twopass.py)
                twopass.scored(c, p, now, tag, p.get("comments_upto"), p.get("comments_n"))
            c.execute("DELETE FROM scores WHERE post_id=%s", (p["id"],))
            keep = [sid for sid, sc in tag["streams"].items() if shown(sid, sc["score"])]
            c.execute("""DELETE FROM tags t WHERE t.post_id=%s AND t.profile = ANY(%s) AND t.profile <> ALL(%s)
                         AND t.corrected IS NOT TRUE AND NOT EXISTS (SELECT 1 FROM stream_items si
                         WHERE si.stream = t.profile AND si.url = %s)""",
                      (p["id"], ids, keep, p["url"]))  # rescored lower; a filed post stays a member
            for psid, sc in tag["streams"].items():
                c.execute("""INSERT INTO scores (stream, post_id, score, reason, category, scored_at, model, screen,
                             screen_reason) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (psid, p["id"], sc["score"], sc["reason"], sc["category"], at, sc.get("model"),
                           sc.get("screen"), sc.get("screen_reason")))
                if shown(psid, sc["score"]):
                    self_file(c, p, psid, sc, tag, now, history, recent, items, backpost)
    if items:  # committed: the senders in this process (telegram_alerts) look now, not at their next check
        FILED.set()
    return items


def self_file(c, p, sid, sc, tag, now, history, recent, items, backpost=False):
    """a shown stream's member: its tags row, and (live, not a comment, a new headline) its stream item"""
    corpus.set_tags(sid, [(p["id"], True, tag["sentiment"], tag["labels"])])
    key = norm_title(p.get("title"))
    if history or p.get("thread") or key in recent[sid]:
        return  # comments feed the dashboard, not the stream page; one item per headline
    if not backpost and not fileable(p, now):
        return  # a first run's or an old post: 數據分析 only
    it = stream_item(p, sid, sc, tag, now)
    if backpost:
        it["backpost"] = True
    got = c.execute("""INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (day, stream, url) DO NOTHING RETURNING id""",
                    (item_day(p), sid, p["url"], corpus.J(it))).fetchone()
    if got:
        recent[sid].add(key)
        items.append(it)


# ---- source gaps: outlets Google News shows that we don't watch (Deyao, 2026-10-14) ----

_watched = {"at": 0, "hosts": set()}


def host(url):
    h = (url.split("/")[2] if url.count("/") >= 2 else "").lower()
    return h[4:] if h.startswith("www.") else h


def watched_hosts():
    """the hosts of every post a watched (non-Google-News) source brought in; refreshed hourly"""
    if time.time() - _watched["at"] > 3600:
        rows = corpus.db().execute("SELECT DISTINCT split_part(url, '/', 3) FROM posts WHERE coalesce(sid, '') NOT LIKE 'gnews%%'")
        _watched.update(at=time.time(), hosts={host("https://" + (r[0] or "") + "/") for r in rows})
    return _watched["hosts"]


def note_source_gaps(items):
    """record each decoded Google News result whose outlet we don't watch in kv `source_gaps` ({host: {name, count,
    first, last, examples}}). Nothing changes automatically: Deyao reads it (infra/thresholds.py gaps) and decides.
    (Links to sites no source reads, from posts that reached a stream and from Reddit's mirror bots, are follow.py's
    list on the monitor)"""
    gn = [it for it in items if (it.get("sid") or "").startswith("gnews") and "news.google.com" not in it["url"]]
    if not gn:
        return
    have = watched_hosts()
    gaps = corpus.kv_get("source_gaps", {}) or {}
    now = datetime.now(HKT).isoformat(timespec="seconds")
    changed = False
    for it in gn:
        h = host(it["url"])
        if not h or h in have:
            continue
        g = gaps.setdefault(h, {"name": it.get("section") or it.get("source"), "count": 0, "first": now, "examples": []})
        g["count"] += 1
        g["last"] = now
        if it["url"] not in g["examples"]:
            g["examples"] = (g["examples"] + [it["url"]])[-5:]
        changed = True
    if changed:
        corpus.kv_set("source_gaps", gaps)


def plan(streams, limit=BATCH * SCORE_THREADS, busy=frozenset(), slots=None):
    """the next model-call batches: up to `limit` due posts (BATCH a call) and up to `limit` due threads (units with
    their replies, char_batches by site), newest first, none of the posts in `busy` (in a call already); live posts'
    and threads' batches alternate so neither waits behind the other; at most `slots` batches (None: all)"""
    s = settings()
    posts = [p for p in thin.gate(due(limit + len(busy), s)) if p["id"] not in busy][:limit]
    threads = [p for p in thin.gate(unit_due(limit + len(busy), s)) if p["id"] not in busy][:limit]
    live = [(posts[k:k + BATCH], "scoring") for k in range(0, len(posts), BATCH)]
    lihkg = [p for p in threads if p["url"].startswith("https://lihkg.com/")]
    discuss = [p for p in threads if p["url"].startswith("https://www.discuss.com.hk/")]
    units = [(b, LIHKG_PURPOSE) for b in char_batches(lihkg)]
    units += [(b, DISCUSS_PURPOSE) for b in char_batches(discuss)]
    units += [(b, UNIT_PURPOSE) for b in char_batches([p for p in threads if p not in lihkg and p not in discuss])]
    out = [j for pair in zip(live, units) for j in pair] + live[len(units):] + units[len(live):]
    return out if slots is None else out[:slots]


def score_job(streams, job):
    """one batch's model calls (both stages) and its AI titles, in a scorer thread → res (None: the drain began first)"""
    batch, purpose = job
    if STOP.is_set():
        return None
    res = score_posts(streams, batch, purpose)
    try:  # the title from AI (titles.py) before filing: the stream item and its Telegram message show it; a post stage 2
        # scored but left untitled gets titles.py's own call
        titles.ensure(batch, {batch[i]["id"] for i, r in res.items() if "stage2" in (r.get("answers") or {})})
    except Exception as e:
        print("scoring: titles failed:", repr(e)[:200], flush=True)
    return res


def file_job(streams, job, res):
    """file one scored batch (stream items, Telegram, the alerts) → how many posts it scored. Only one thread files
    (the loop's or run_once's), so a headline's duplicates are matched against everything filed before"""
    batch, purpose = job
    items = file(streams, batch, res)
    if items:
        print(f"scoring: {len(items)} new stream item(s): " + ", ".join(f"{x['stream']} {x['score']}" for x in items), flush=True)
    return len(res)


def run_once(streams, limit=BATCH * SCORE_THREADS):
    """score up to `limit` due posts (and up to `limit` due LIHKG threads, as units with their replies), each batch
    filed as soon as its own calls are done → how many were scored (the CLI and tests; the live scorer is loop())"""
    jobs = plan(streams, limit)
    done = 0
    with ThreadPoolExecutor(min(SCORE_THREADS, len(jobs) or 1)) as ex:
        running = {ex.submit(score_job, streams, j): j for j in jobs}
        for f in as_completed(running):
            res = f.result()
            if res is not None:
                done += file_job(streams, running[f], res)
    return done


def loop(streams):
    """the live scorer, continuous (Deyao, 2026-10-23: "the pipeline time could be shortened"): SCORE_THREADS
    model-call slots; the planner thread gives a free slot the next due batch (plan(); due() can take 30 s when few
    posts are due, so it runs beside the filing, never in its way), and this thread files each batch the moment its
    own calls are done, so a post never waits for another batch's call, a round or the next due() query. A failure
    pauses new batches (LIMIT_WAIT for OpenRouter's refusal, 60 s otherwise) while the calls in hand finish. On STOP
    (the drain) no new batch starts; the ones in hand finish and are filed"""
    running, lock, finished, freed = {}, threading.Lock(), queue.Queue(), threading.Event()
    pause = [0.0]  # time.monotonic() before which no new batch starts

    def done(f):
        finished.put(f)
        freed.set()

    def planner():
        while not STOP.is_set():
            with lock:
                free = SCORE_THREADS - len(running)
                busy = {p["id"] for batch, _ in running.values() for p in batch}
            if not free or time.monotonic() < pause[0]:
                freed.wait(1)
                freed.clear()
                continue
            try:
                with corpus.stage("scoring"):  # a restart (paused, blocked) starts from now: the backlog waits
                    jobs = plan(streams, limit=free * BATCH, busy=busy, slots=free)
            except Exception as e:
                print("scoring failed:", repr(e)[:300], flush=True)
                pause[0] = time.monotonic() + 60
                continue
            with lock:
                for j in jobs:
                    f = ex.submit(score_job, streams, j)
                    running[f] = j
                    f.add_done_callback(done)
            if not jobs:  # nothing due: look again in a second while calls run (as before), else after IDLE
                STOP.wait(1 if free < SCORE_THREADS else IDLE)

    ex = ThreadPoolExecutor(SCORE_THREADS)
    t = threading.Thread(target=planner, name="score-planner", daemon=True)
    t.start()
    try:
        while True:
            try:
                f = finished.get(timeout=1)
            except queue.Empty:
                with lock:
                    if STOP.is_set() and not t.is_alive() and not running:
                        return
                continue
            try:
                res = f.result()
                if res is not None:
                    file_job(streams, running[f], res)
            except ai.LimitReached:
                print("scoring: OpenRouter refused (key limit / credit); trying again later", flush=True)
                corpus.stage_blocked("OpenRouter refused (key limit / credit)", "scoring")
                pause[0] = time.monotonic() + LIMIT_WAIT
            except Exception as e:
                print("scoring failed:", repr(e)[:300], flush=True)
                pause[0] = time.monotonic() + 60
            with lock:  # after the pause is set: the planner never sees the slot free without it
                running.pop(f)
            freed.set()
    finally:
        ex.shutdown(wait=False)


# ---- history: the backfill (Deyao, 2026-10-14: complete data first; cost cut only after measuring) ----

_key = {"at": 0, "data": None}


def backfill_settings():
    """kv `score_backfill`: on (default off: it scores a backlog, so only a manual run turns it on; Deyao, 2026-10-27),
    posts (scored so far), and the last state the thread wrote"""
    return corpus.kv_get("score_backfill") or {"on": False, "posts": 0}


def backfill_due(limit, streams, s=None):
    """non-comment posts the live scorer never takes (from before the switch, or older than MAX_AGE), never scored,
    scored on other stream definitions, or changed since; newest first (publish time). Only posts seen at or after
    corpus.FLOOR (Deyao, 2026-10-20), so switching the backfill on never re-scores a post seen before it, whatever its
    stored definitions. The backfill is a manual run (kv score_backfill.on): it takes the scoring stage's backlog too
    (corpus.seen_cond's start point and first_run posts; Deyao, 2026-10-27: only a manual trigger processes a backlog)"""
    s = s or settings()
    cut = (datetime.now(HKT) - MAX_AGE).isoformat(timespec="seconds")
    sql = f"""SELECT p.*, {HASH} AS hash FROM posts p LEFT JOIN scored s ON s.post_id = p.id
              WHERE p.thread IS NULL AND p.seen::timestamptz >= '{corpus.FLOOR}'::timestamptz
                AND NOT (p.id > %s AND coalesce(p.published, p.seen) >= %s)
                AND NOT (p.id > %s AND {twopass.unit_cond("p")})
                AND (s.post_id IS NULL OR s.defs IS DISTINCT FROM %s OR s.hash <> {HASH}) AND NOT {links.HELD_SQL}
                AND NOT {thin.excluded_sql("p", HASH)}
              ORDER BY coalesce(p.published, p.seen) DESC, p.id DESC LIMIT %s"""
    return [dict(r) for r in corpus.db().execute(sql, (s["from_id"], cut, s["from_id"], defs_hash(streams),
                                                       clock().isoformat(timespec="seconds"), limit))]


def backfill_left(streams):
    """how many posts backfill_due has left (the same conditions but the held ones; seen since the cutoff only)"""
    s = settings()
    cut = (datetime.now(HKT) - MAX_AGE).isoformat(timespec="seconds")
    return corpus.db().execute(f"""SELECT count(*) FROM posts p LEFT JOIN scored s ON s.post_id = p.id
              WHERE p.thread IS NULL AND p.seen::timestamptz >= '{corpus.FLOOR}'::timestamptz
                AND NOT (p.id > %s AND coalesce(p.published, p.seen) >= %s)
                AND NOT (p.id > %s AND {twopass.unit_cond("p")})
                AND (s.post_id IS NULL OR s.defs IS DISTINCT FROM %s OR s.hash <> {HASH})""",
                                   (s["from_id"], cut, s["from_id"], defs_hash(streams))).fetchone()[0]


def key_info(fresh=False):
    """OpenRouter's own record of the key (GET /api/v1/key): limit, limit_remaining, effective_budget.resets_at"""
    if fresh or time.time() - _key["at"] > KEY_TTL:
        req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={
            "Authorization": "Bearer " + os.environ["OPENROUTER_API"], "User-Agent": "yyc-news-stream/1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            _key.update(at=time.time(), data=json.load(r)["data"])
    return _key["data"]


def per_post():
    """USD per post: the backfill's own (ai_spend scoring_backfill / posts it scored) once it has 50, else live
    scoring's today (ai_spend scoring / posts scored today)"""
    c = corpus.db()
    b = backfill_settings()
    if b.get("posts", 0) >= 50:
        usd = c.execute("SELECT coalesce(sum(usd), 0) FROM ai_spend WHERE purpose = ANY(%s)",
                        ([BACKFILL_PURPOSE, BACKFILL_PURPOSE + VERIFY],)).fetchone()[0]  # both stages
        return float(usd) / b["posts"]
    day = datetime.now(HKT).strftime("%Y-%m-%d")
    usd = c.execute("SELECT coalesce(sum(usd), 0) FROM ai_spend WHERE purpose IN ('scoring', 'scoring_verify') AND day=%s",
                    (day,)).fetchone()[0]
    n = c.execute("SELECT count(*) FROM scored WHERE at >= %s", (day,)).fetchone()[0]
    return float(usd) / n if n else 0.0005


def _note(**kw):
    b = backfill_settings()
    b.pop("budget", None)  # figures of the cap reserve, which is gone
    b.update(kw, at=datetime.now(HKT).isoformat(timespec="seconds"))
    corpus.kv_set("score_backfill", b)
    return b


def backfill_once(streams):
    """one backfill batch, if live scoring has nothing due and the switch is on → the state ('live' | 'off' |
    'done' | 'scored' | 'limit': OpenRouter refused for the key's limit or credit)"""
    if not backfill_settings().get("on"):
        return "off"
    s = settings()
    if due(1, s):  # live scoring first: the backfill waits for an empty live queue
        if backfill_settings().get("state") != "live":  # and says so (not a stale 'limit' once OpenRouter answers)
            _note(state="live", error=None)
        return "live"
    got = backfill_due(BATCH, streams, s)
    posts = thin.gate(got)
    if got and not posts:
        return "scored"  # all near-empty: recorded by the gate, the next batch at once
    if not posts:
        if backfill_settings().get("state") != "done":
            _note(state="done", left=0)
        return "done"
    try:
        res = score_posts(streams, posts, BACKFILL_PURPOSE)
    except ai.LimitReached as e:
        _note(state="limit", left=backfill_left(streams), error=str(e)[:300])
        print("score backfill: OpenRouter refused (key limit / credit); trying again later", flush=True)
        return "limit"
    file(streams, posts, res, history=True)
    st = backfill_settings()
    n = st.get("posts", 0) + len(res)
    extra = {"left": backfill_left(streams)} if n // (BATCH * 20) != st.get("posts", 0) // (BATCH * 20) else {}
    _note(state="running", posts=n, error=None, **extra)  # posts left recounted every 20 batches (the monitor's figure)
    return "scored"


def backfill_loop(streams):
    while not STOP.is_set():
        try:
            state = backfill_once(streams)
            STOP.wait({"scored": 0, "live": IDLE, "limit": LIMIT_WAIT}.get(state, 300))
        except Exception as e:
            print("score backfill failed:", repr(e)[:300], flush=True)
            STOP.wait(60)


def sync_tags(streams):
    """make table tags hold, for each stream, exactly its members: the posts scored at or above its current threshold
    and the posts filed in it (stream_items; a hand-corrected row stays): drops the rows of the keyword-matching era and of other profiles, adds members without a
    row (sentiment and labels from the scorer). → {stream: [rows dropped, rows added]}"""
    now = datetime.now(HKT).isoformat(timespec="seconds")
    ids = [st["id"] for st in streams]
    out = {}
    c = corpus.db()
    with c:
        c.execute("DELETE FROM tags WHERE profile <> ALL(%s) AND corrected IS NOT TRUE", (ids,))
        for sid in ids:
            t = threshold(sid)
            gone = c.execute("""DELETE FROM tags t WHERE t.profile=%s AND t.corrected IS NOT TRUE AND NOT EXISTS
                                (SELECT 1 FROM scores s WHERE s.stream=t.profile AND s.post_id=t.post_id AND s.score >= %s)
                                AND NOT EXISTS (SELECT 1 FROM stream_items si WHERE si.stream=t.profile
                                                AND (si.item->>'post_id')::bigint = t.post_id)
                                RETURNING post_id""", (sid, t)).fetchall()
            added = c.execute("""INSERT INTO tags (profile, post_id, relevant, sentiment, labels, tagged_at)
                                 SELECT %s, m.post_id, 1, sc.sentiment, sc.labels, %s FROM (
                                   SELECT s.post_id FROM scores s WHERE s.stream=%s AND s.score >= %s
                                   UNION SELECT p.id FROM stream_items si JOIN posts p
                                         ON p.id = (si.item->>'post_id')::bigint WHERE si.stream=%s) m
                                 LEFT JOIN scored sc ON sc.post_id=m.post_id
                                 ON CONFLICT (profile, post_id) DO UPDATE SET relevant=1
                                 WHERE tags.corrected IS NOT TRUE AND tags.relevant IS DISTINCT FROM 1
                                 RETURNING post_id""", (sid, now, sid, t, sid)).fetchall()
            out[sid] = [len(gone), len(added)]
    return out


SPLIT_CHILDREN = ("GALBOT_HK", "GALBOT_CN")  # one-off: fold_galbot_split


def fold_galbot_split():
    """One-off (Deyao, 2026-11-11: there is one Galbot channel): Galbot 香港 and Galbot 內地 (GALBOT_HK / GALBOT_CN, the
    market split of 2026-10-14) fold back into GALBOT. Their stream items, hand-corrected tags and report files move to
    GALBOT (a (day, url) GALBOT already holds stays GALBOT's); their score rows were copies of GALBOT's and go; the
    Telegram and WhatsApp cursors of GALBOT become the furthest of the three, so nothing already sent is sent again.
    Run by the poller after it holds the poller lock (start), before the tag sync: the old poller, the only writer of
    stream items, has stopped by then. Idempotent; kv migrated:galbot_one_stream records it."""
    if corpus.kv_get("migrated:galbot_one_stream"):
        return None
    kids = list(SPLIT_CHILDREN)
    c = corpus.db()
    with c:
        moved = c.execute("""UPDATE stream_items si SET stream = 'GALBOT', item = jsonb_set(item, '{stream}', '"GALBOT"')
                             WHERE si.stream = ANY(%s) AND NOT EXISTS (SELECT 1 FROM stream_items g
                             WHERE g.stream = 'GALBOT' AND g.day = si.day AND g.url = si.url)""", (kids,)).rowcount
        c.execute("DELETE FROM stream_items WHERE stream = ANY(%s)", (kids,))
        c.execute("DELETE FROM scores WHERE stream = ANY(%s)", (kids,))
        c.execute("""UPDATE tags t SET profile = 'GALBOT' WHERE t.profile = ANY(%s) AND t.corrected IS TRUE
                     AND NOT EXISTS (SELECT 1 FROM tags g WHERE g.profile = 'GALBOT' AND g.post_id = t.post_id)""", (kids,))
        c.execute("DELETE FROM tags WHERE profile = ANY(%s)", (kids,))
        c.execute("""UPDATE report_files r SET profile = 'GALBOT' WHERE r.profile = ANY(%s) AND NOT EXISTS
                     (SELECT 1 FROM report_files g WHERE g.profile = 'GALBOT' AND g.day = r.day AND g.name = r.name)""",
                  (kids,))
    for key in ("telegram_sent", "whatsapp_sent"):  # stream_items ids: the furthest one sent
        d = corpus.kv_get(key) or {}
        got = [d.pop(k) for k in ["GALBOT"] + kids if d.get(k) is not None]
        if got:
            d["GALBOT"] = max(got)
        corpus.kv_set(key, d)
    for key in ("telegram_sent_at", "whatsapp_sent_at", "telegram_error", "whatsapp_error", "whatsapp_alerted",
                "stream_thresholds"):
        d = corpus.kv_get(key)
        if isinstance(d, dict) and any(k in d for k in kids):
            if "GALBOT_HK" in d and key.endswith("_sent_at"):
                d["GALBOT"] = d["GALBOT_HK"]
            for k in kids:
                d.pop(k, None)
            corpus.kv_set(key, d)
    if corpus.kv_get("themes:GALBOT_HK") and not corpus.kv_get("themes:GALBOT"):
        corpus.kv_set("themes:GALBOT", corpus.kv_get("themes:GALBOT_HK"))
    corpus.kv_set("migrated:galbot_one_stream", {"at": datetime.now(HKT).isoformat(timespec="seconds"), "items": moved})
    print(f"scoring: Galbot 香港 / 內地 folded into GALBOT ({moved} stream items moved)", flush=True)
    return moved


def start(streams):
    try:
        fold_galbot_split()
    except Exception as e:
        print("scoring: Galbot fold failed:", repr(e)[:300], flush=True)
    try:
        print("scoring: tags synced to the scored sets (dropped, added):", sync_tags(streams), flush=True)
    except Exception as e:
        print("scoring: tag sync failed:", repr(e)[:300], flush=True)
    try:  # rows scored after the 02:56 UTC deploy of POL's current brief, before scored.defs existed (one-off)
        corpus.db().execute("UPDATE scored SET defs=%s WHERE defs IS NULL AND at >= '2026-10-14T11:10:00+08:00'",
                            (defs_hash(streams),))
    except Exception as e:
        print("scoring: defs fill failed:", repr(e)[:300], flush=True)
    ts = [threading.Thread(target=loop, args=(streams,), daemon=True),
          threading.Thread(target=backfill_loop, args=(streams,), daemon=True)]
    for t in ts:
        t.start()
    # titles from AI for what the live scorer doesn't title (titles.py); posts that are only a link read (links.py);
    # the links of posts that reached a stream collected (follow.py)
    import follow
    return ts + [titles.start(STOP), links.start(STOP), follow.start(STOP)]


if __name__ == "__main__" and sys.argv[1:2] == ["once"]:
    import os
    st = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "streams.json")))
    print(run_once(st), "post(s) scored")
