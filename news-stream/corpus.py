"""The app's database: every post the stream collects, from every source, in Postgres ($DATABASE_URL), where the
stream scorer (streams.py) scores it and 搜尋 (query.py) searches it, plus every other piece of state the app keeps
(the stream archive, poll state, profiles, settings, AI history and costs, the jobs' state). The corpus keeps
everything; a stream shows the posts scored at or above its threshold. Several pods (web
replicas, the poller, the CronJobs) share it; Postgres transactions and advisory locks (lock()) replace the old file
locks.

posts:  one row per URL (title, text where the source gives one, source, website, board, author, platform,
        source type, region, published/seen times in HKT, the HKT day, engagement counts when known).
        A comment or forum reply is a row too (K-Matrix matches comments: "1 related in 168 post & comments"):
        url <thread url>#c<comment id>, the thread's title and source, the comment as text, the commenter as author,
        the comment's own time and day, and `thread` = the thread's url (add_comments). So an old thread that a new
        comment mentions shows up on the comment's day, and topics group a thread's rows by that url.
tags:   AI tags per (stream, post): sentiment, labels, theme. One row for each post the scorer gave the stream at
        least its threshold (streams.py writes it; streams.sync_tags() keeps the table to exactly that set), so the
        profile's posts (insights.members, from table scores) and their tags agree. A row corrected by hand has
        corrected = TRUE, and no writer changes it (set_tags, set_themes); nothing on the website writes tags.
kv:     small JSON values by key (caches, AI results, the jobs' per-post state). file_get/file_put keep the documents
        the jobs once wrote as files ($DATA_DIR/<name>.json) under kv key "file:<name>".
The other tables (SCHEMA below) belong to app.py (stream_items, poll_seen, poll_state), insights.py (profiles,
settings) and ai.py (ai_spend, ai_runs).

Files stay on the data volume ($DATA_DIR): built reports and browser profiles.
"""
import atexit, contextlib, hashlib, html, json, os, random, re, select, threading, time
from datetime import datetime, timezone, timedelta

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

import canonical, query, searchtext

HKT = timezone(timedelta(hours=8))
# Backlog (Deyao, 2026-10-27: "by default, backlog should not be cleared at all (at any stage of the pipeline), only a
# manual trigger should process them"; 2026-10-20: "just start watching and capturing from this point, ignore the past").
# Each stage of the pipeline (STAGES) works only on posts seen at or after its own start point (kv cutoff:<stage>) and
# never on a backlog post (posts.first_run: a source's first read, or its first read after a gap, COLLECT_GAP). The
# start point moves to now, by itself, whenever the stage starts again after not working: it ran last longer ago than
# its gap (paused, crashed, its job not scheduled), or it said it was blocked (a flag off, OpenRouter's limit, the proxy
# out of traffic: stage_blocked). What piled up before stays in the corpus, 搜尋 and 數據分析, unprocessed, until a
# manual run (infra/backlog.py run STAGE --from TIME [--first-run]) moves the point back.
FLOOR = "2026-10-20T09:15:00+08:00"  # the first start point (the 20 Oct restart): no stage ever goes before it
# stage → how long it may go without a finished round before its next start counts as a restart (seconds): the
# poller's threads 45 min (a rollout's drain holds the old pod's rounds for up to 25 min), a CronJob 2.5 x its period
STAGES = {
    "scoring": 2700,        # streams.loop: stage 1 + stage 2 (poller)
    "titles": 2700,         # titles.loop: AI titles (poller)
    "follow": 2700,         # follow.loop: linked pages of stream posts (poller)
    "links": 2700,          # links.loop: link-only posts' pages (poller)
    "comments": 2700,       # twopass / social_twopass: comments read for scoring (poller; comments, replies jobs)
    "fulltext": 4500,       # fulltext_fetch (CronJob every 30 min) and fulltext_now (poller); YouTube descriptions
    "cache": 4500,          # cache_fetch (CronJob every 30 min)
    "archive": 4500,        # paywall.py archive (CronJob every 30 min)
    "transcription": 2700,  # media.py worker (Deployment transcribe)
    "pictures": 2700,       # images.py worker (Deployment images)
    "tiktok": 2700,         # tiktok_video.py worker (Deployment tiktok-videos)
    "telegram": 2700,       # telegram_alerts (poller): a restart sends nothing filed before it
    "whatsapp": 2700,       # whatsapp_alerts (Deployment whatsapp): the same
}
COLLECT_GAP = timedelta(hours=3)  # a source read again after this long without a read: that read's new posts are backlog
MARK_BACKLOG = True  # ingest marks a source's first read and its first after COLLECT_GAP as backlog; tests about other things turn it off
_cut_cache = {}  # stage → (monotonic time, cutoff, first_run_from)
_CUT_TTL = 30
_local = threading.local()  # the stage the current thread is working for (stage(); stage_blocked() marks it)


def _stage_conf(stage):
    if stage not in STAGES:
        raise KeyError(f"unknown stage {stage!r} (corpus.STAGES)")
    hit = _cut_cache.get(stage)
    if hit and time.monotonic() - hit[0] < _CUT_TTL and hit[3] == FLOOR:
        return hit[1], hit[2]
    v = kv_get("cutoff:" + stage) or {}
    cut = max(v.get("from") or FLOOR, FLOOR, key=datetime.fromisoformat)
    fr = v.get("first_run_from")
    _cut_cache[stage] = (time.monotonic(), cut, fr, FLOOR)
    return cut, fr


def cutoff(stage):
    """the stage's start point (ISO): posts seen before it are its backlog"""
    return _stage_conf(stage)[0]


def seen_cond(alias, stage):
    """SQL: the post `alias` is in `stage`'s work: seen at or after its start point and not a backlog post (first_run),
    unless a manual run let the stage's backlog posts in from a time (first_run_from). Every job that spends work on
    stored posts and every monitor count of waiting work keeps to it (tests/test_cutoff_work.py, test_backlog.py)"""
    cut, fr = _stage_conf(stage)
    keep = f"{alias}.first_run IS NOT TRUE"
    if fr:
        keep = f"({keep} OR {alias}.seen::timestamptz >= '{fr}'::timestamptz)"
    # every seen is HKT text of one length (ingest), whose order is time order: the text comparison lets Postgres use
    # the posts_seen index (a cast can't), the timestamptz one stays exact
    hkt = datetime.fromisoformat(cut).astimezone(HKT).isoformat(timespec="seconds")
    return f"({alias}.seen >= '{hkt}' AND {alias}.seen::timestamptz >= '{cut}'::timestamptz AND {keep})"


def media_cond(alias, stage="transcription"):
    """SQL: the clip `alias` (table media) belongs to a post in `stage`'s work"""
    return (f"EXISTS (SELECT 1 FROM post_media pm_c JOIN posts p_c ON p_c.id = pm_c.post_id "
            f"WHERE pm_c.key = {alias}.key AND {seen_cond('p_c', stage)})")


def in_stage(post, stage):
    """the post (a dict with seen, first_run) is in `stage`'s work (seen_cond in Python); no seen time = not"""
    cut, fr = _stage_conf(stage)
    try:
        seen = datetime.fromisoformat(post.get("seen") or "")
    except (TypeError, ValueError):
        return False
    if seen < datetime.fromisoformat(cut):
        return False
    return not post.get("first_run") or bool(fr and seen >= datetime.fromisoformat(fr))


def _stage_state(stage):
    return kv_get("stage:" + stage) or {}


def _move_cutoff(stage, why, now=None):
    now = now or datetime.now(HKT).isoformat(timespec="seconds")
    v = kv_get("cutoff:" + stage) or {}
    log = (v.get("log") or [])[-19:] + [{"at": now, "from": now, "why": why}]
    kv_set("cutoff:" + stage, {"from": now, "first_run_from": None, "log": log})
    _cut_cache.pop(stage, None)
    print(f"backlog: stage {stage} starts from {now} ({why}); what came before waits for a manual run", flush=True)


@contextlib.contextmanager
def stage(name, now=None):
    """one round of stage `name`: on entry, when the stage last finished a round longer ago than its gap
    (STAGES[name]) or said it was blocked, its start point moves to now (yields True); a round that ends without an
    exception records the stage as working. Rounds that raise leave the record as it was, so a stage failing for longer
    than its gap starts from the moment it works again."""
    gap = STAGES[name]
    st = _stage_state(name)
    t = now or datetime.now(HKT)
    last = st.get("ok")
    why = None
    if st.get("down"):
        why = f"was blocked since {st.get('down_since')}: {st['down']}"
    elif not last:
        why = "no round on record"
    elif (t - datetime.fromisoformat(last)).total_seconds() > gap:
        why = f"no round since {last}"
    if why:
        _move_cutoff(name, why, t.isoformat(timespec="seconds"))
        kv_set("stage:" + name, {"ok": last, "down": None})  # a block from now on is this round's own
    prev = getattr(_local, "stage", None)
    _local.stage = name
    try:
        yield bool(why)
    finally:
        _local.stage = prev
    st = _stage_state(name)  # re-read: stage_blocked may have marked it during the round
    if st.get("down"):
        return  # blocked in this round: its next working round starts afresh
    end = datetime.now(HKT)
    if why or not st.get("ok") or (end - datetime.fromisoformat(st["ok"])).total_seconds() > 60:
        kv_set("stage:" + name, {"ok": end.isoformat(timespec="seconds"), "down": None})


def stage_blocked(why, name=None):
    """the stage (default: the one this thread works for) can't do its work (a flag off, a limit hit): its next
    working round starts from that moment (stage())"""
    name = name or getattr(_local, "stage", None)
    if not name:
        return
    st = _stage_state(name)
    if st.get("down"):
        return
    kv_set("stage:" + name, {**st, "down": why, "down_since": datetime.now(HKT).isoformat(timespec="seconds")})
    print(f"backlog: stage {name} blocked ({why})", flush=True)


def backlog_run(name, frm, first_run=False):
    """the manual trigger (infra/backlog.py): stage `name` works on what was seen from `frm` (ISO) on, the backlog
    posts (first_run) of that time too when first_run; it goes on from there until its next stop"""
    frm = max(frm, FLOOR, key=datetime.fromisoformat)
    v = kv_get("cutoff:" + name) or {}
    now = datetime.now(HKT).isoformat(timespec="seconds")
    log = (v.get("log") or [])[-19:] + [{"at": now, "from": frm, "why": "manual run" + (" with first_run" if first_run else "")}]
    kv_set("cutoff:" + name, {"from": frm, "first_run_from": frm if first_run else None, "log": log})
    _cut_cache.pop(name, None)
    return frm


class DateOnly(datetime):
    """a publish date with no time of day (the source states only the day): midnight HKT of that day, marked so ingest
    stores it as date-only (posts.date_only, on the time it was first seen within the day) and never as 00:00"""

    @classmethod
    def of(cls, d):
        return cls(d.year, d.month, d.day, tzinfo=HKT)


BARE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def within_day(day, seen):
    """a date-only post's time for ordering and age checks: when it was first seen, kept within that day (its last
    minute when first seen later, its first when seen the evening before, as next-day e-papers are) → ISO HKT minutes.
    day: a DateOnly, a date or a "YYYY-MM-DD" string"""
    if isinstance(day, str):
        day = datetime.fromisoformat(day[:10])
    start = datetime(day.year, day.month, day.day, tzinfo=HKT)
    try:
        t = datetime.fromisoformat(seen).astimezone(HKT)
    except (TypeError, ValueError):
        t = start
    return min(max(t, start), start + timedelta(hours=23, minutes=59)).isoformat(timespec="minutes")


def shown_time(p):
    """a post's publish time as pages show it: the bare day "YYYY-MM-DD" when the source gave only a day, else the
    time (or None)"""
    pub = p.get("published") if hasattr(p, "get") else p["published"]
    if pub and p.get("date_only"):
        return pub[:10]
    return pub
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))
# keepalives: an idle connection (a job's lock, held for minutes) isn't cut on the way to Neon (an idle one died mid-run,
# 2026-10-16); the pool's connections and corpus.lock's own ones
CONNECT_KW = {"application_name": "yyc-news-stream", "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10,
              "keepalives_count": 3, "tcp_user_timeout": 30000}
# tcp_user_timeout (ms): a send on a dead network path fails after 30 s instead of TCP's ~16-minute retransmission
# (2026-10-20: the lock keepers' SELECT 1 hung from 14:09 to 14:25 UTC after the pods' paths to Neon went silent)

# Lock connections (lock_connect): Postgres itself ends a lock session that has been idle LOCK_IDLE seconds
# (idle_session_timeout), and every holder speaks at least every LOCK_BEAT seconds. A holder whose network path dies
# without a RST leaves a session Neon would keep for ever, holding its advisory lock (2026-10-20: nine hours of ghost
# 'whatsapp', 'transcribe', 'tikhub' and 'yyc youtube' locks); with the timeout the lock is free within LOCK_IDLE s.
LOCK_IDLE = 90
LOCK_BEAT = 15

# the whole schema, as an empty database gets it (_apply_schema); an existing database changes only by MIGRATIONS
SCHEMA = r"""
CREATE TABLE IF NOT EXISTS posts (
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, url TEXT UNIQUE NOT NULL, sid TEXT, source TEXT,
  website TEXT, board TEXT, author TEXT, platform TEXT, source_type TEXT, region TEXT, title TEXT, text TEXT, hay TEXT,
  published TEXT, seen TEXT, day TEXT, engagement TEXT, cached_at TEXT, section TEXT, thread TEXT);
CREATE INDEX IF NOT EXISTS posts_day ON posts(day);
CREATE INDEX IF NOT EXISTS posts_author ON posts(author);
CREATE INDEX IF NOT EXISTS posts_thread ON posts(thread);
CREATE INDEX IF NOT EXISTS posts_sid_day ON posts(sid, day);
CREATE TABLE IF NOT EXISTS tags (
  profile TEXT NOT NULL, post_id BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE, relevant INTEGER,
  sentiment TEXT, labels TEXT, theme TEXT, tagged_at TEXT, PRIMARY KEY (profile, post_id));
CREATE INDEX IF NOT EXISTS tags_post ON tags(post_id);
-- a row corrected by hand (corrected = TRUE): set_tags and set_themes never overwrite it
ALTER TABLE tags ADD COLUMN IF NOT EXISTS corrected BOOLEAN;
ALTER TABLE tags ADD COLUMN IF NOT EXISTS corrected_by TEXT;
ALTER TABLE tags ADD COLUMN IF NOT EXISTS corrected_at TEXT;
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v JSONB);
-- search (query.py, searchtext.py; Deyao, 2026-10-14): hay is the hidden search copy (NFKC, OpenCC hk2s → Simplified,
-- lower case; display text is never changed), stitle the same of the title, words its jieba words as a tsvector (title
-- weight A), grams every 2-character sequence of hay (the substring fallback's index). All written by Python
-- (search_cols()); search_v = the searchtext.VERSION a row was indexed with, and reindex() redoes the rows that differ
ALTER TABLE posts ADD COLUMN IF NOT EXISTS grams tsvector;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS stitle TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS words tsvector;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS search_v TEXT;
CREATE INDEX IF NOT EXISTS posts_grams ON posts USING gin (grams);
CREATE INDEX IF NOT EXISTS posts_words ON posts USING gin (words);
-- their pending lists are flushed once a minute by deploy/db/gin-clean.yaml, never by a writer: 256 MB is its safety stop
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = 'posts_grams' AND 'gin_pending_list_limit=262144' = ANY(reloptions)) THEN
    ALTER INDEX posts_grams SET (gin_pending_list_limit = 262144);
    ALTER INDEX posts_words SET (gin_pending_list_limit = 262144);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS posts_nograms ON posts (id) WHERE grams IS NULL;
-- post_subs (Deyao, 2026-10-27): per post, the suffixes (cut to searchtext.SUB_LEN = 32 characters) of each a-z/0-9
-- run of hay, so a Latin term's substring matches come from a GIN prefix lookup ('trump':*) instead of reading every
-- grams candidate's text (query.py). Its own table, written by triggers from posts.hay (every writer covered, and
-- filling it never rewrites a posts row); subs NULL = a text whose list is over 900 kB (a tsvector holds 1 MB): tested
-- by strpos. Backfill: corpus.fill_subs(); the GIN index was built by hand after it (CREATE INDEX CONCURRENTLY).
CREATE TABLE IF NOT EXISTS post_subs (post_id BIGINT PRIMARY KEY, subs tsvector);
CREATE INDEX IF NOT EXISTS post_subs_subs ON post_subs USING gin (subs);
CREATE INDEX IF NOT EXISTS post_subs_null ON post_subs (post_id) WHERE subs IS NULL;
CREATE OR REPLACE FUNCTION subs_of(hay TEXT) RETURNS tsvector LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN sum(length(x) + 4) > 900000 THEN NULL ELSE coalesce(array_to_tsvector(array_agg(x)), ''::tsvector) END
  FROM (SELECT DISTINCT left(substr(r[1], i), 32) AS x
        FROM regexp_matches(coalesce(hay, ''), '[a-z0-9]+', 'g') r, generate_series(1, length(r[1])) i) s
$$;
CREATE OR REPLACE FUNCTION post_subs_write() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    DELETE FROM post_subs WHERE post_id = OLD.id;
    RETURN OLD;
  END IF;
  INSERT INTO post_subs (post_id, subs) VALUES (NEW.id, subs_of(NEW.hay))
    ON CONFLICT (post_id) DO UPDATE SET subs = excluded.subs;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER posts_subs_ins AFTER INSERT ON posts FOR EACH ROW EXECUTE FUNCTION post_subs_write();
CREATE OR REPLACE TRIGGER posts_subs_upd AFTER UPDATE OF hay ON posts FOR EACH ROW
  WHEN (OLD.hay IS DISTINCT FROM NEW.hay) EXECUTE FUNCTION post_subs_write();
CREATE OR REPLACE TRIGGER posts_subs_del AFTER DELETE ON posts FOR EACH ROW EXECUTE FUNCTION post_subs_write();
CREATE INDEX IF NOT EXISTS posts_when ON posts ((coalesce(published, seen)) DESC, id DESC);
CREATE INDEX IF NOT EXISTS posts_website ON posts (website);
-- the monitor's posts per source type (corpus_stats, every 10 min) from this index alone: a few distinct values, so
-- deduplicated it is a few MB, not a scan of every post's page
CREATE INDEX IF NOT EXISTS posts_source_type ON posts (source_type);
-- 數據分析's period totals (outlets and posts) and its hot terms' background (a random 3000 of
-- the period's titles) read only this index, not the posts' pages (16 s for a week on YYC's Postgres, 2026-10-27)
CREATE INDEX IF NOT EXISTS posts_day_cover ON posts (day, website) INCLUDE (title);
-- articles (Deyao, 2026-10-14): posts that are the same content share article_id = the id of the article's first post
-- (a post on its own: its own id). Same content = the same URL after cleanup (url_key), the same full text (text_key),
-- or a social post linking to the article (its link_key = the article's url_key). assign_articles() sets them.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS article_id BIGINT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS url_key TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS text_key TEXT;
-- first_run (Deyao, 2026-10-14; 2026-10-27): the post came in with its source's first posts in the corpus (a new or
-- reset sid) or with its first read after a gap (sid_reads, COLLECT_GAP): a backlog post. No stage works on it
-- (seen_cond) unless a manual run lets it in; never a stream item or a Telegram post unless back-posted on purpose
-- (streams.file backpost)
ALTER TABLE posts ADD COLUMN IF NOT EXISTS first_run BOOLEAN;
-- date_only (Deyao, 2026-10-14): the source gives only the day it was published (a URL's or list's date, a feed time of
-- exactly midnight). published then holds the moment the post was first seen within that day (DateOnly, within_day),
-- for ordering and age checks, and pages show only the day (shown_time). A page read that states a time replaces it.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS date_only BOOLEAN;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS link_key TEXT;
-- canonical_url (Deyao, 2026-10-20; canonical.py): the address the article's page states for itself when it differs from
-- url (the address the stream found it at, the post's identity): what the site's 開啟原文 opens. NULL = url.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS canonical_url TEXT;
CREATE INDEX IF NOT EXISTS posts_article ON posts (article_id);
CREATE INDEX IF NOT EXISTS posts_url_key_idx ON posts (url_key);  -- not posts_url_key: the UNIQUE (url) constraint has that name
CREATE INDEX IF NOT EXISTS posts_text_key ON posts (text_key) WHERE text_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS posts_link_key ON posts (link_key) WHERE link_key IS NOT NULL;
-- indexes for the scorer's queue and Deyao's monitor (EXPLAIN ANALYZE live, 2026-10-15: the monitor's pipeline panel took
-- 43.6 s): posts_live_id lets streams.due walk non-comment posts newest first instead of skipping ~280k comment rows;
-- posts_seen answers "posts seen since …" (monitor sources, watchlist.model_usd, watched posts in 24 h) from the index
-- alone; posts_doc and posts_doc_notext count news/official/blog posts and those still without text (fulltext card)
-- posts' storage (2026-10-27, the database at its disk's I/O limit): 80% fill leaves room on each page, so an UPDATE that
-- changes no indexed column (engagement, ai_title_hash, …) is a HOT update and writes no index entries at all (at 100%,
-- 80% of updates went to a new page, each re-adding the post's hundreds of character pairs to posts_grams); autovacuum
-- at 2% changed or inserted rows (not the default 20%: 450k rows, hours apart) keeps the visibility map current, so the
-- covering indexes (posts_seen, posts_day_cover) answer from the index alone instead of fetching each row's page;
-- statistics after 0.5% changed rows (~20k, about hourly; not 10%: 400k, a day and a half): the search plans a period
-- ending today on the share of posts today has, and planned on the night before's (today: 8.7k posts, really 55k) it
-- read every post of the period instead of the words index (YYC AI's data_posts_search past the 30 s statement timeout,
-- 2026-11-18; with fresh statistics the same search takes the words index)
ALTER TABLE posts SET (fillfactor = 80, autovacuum_vacuum_scale_factor = 0.02, autovacuum_vacuum_insert_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.005);
CREATE INDEX IF NOT EXISTS posts_live_id ON posts (id) WHERE thread IS NULL;
-- posts_live_day_url: a site's threads of a day by URL prefix (replies_fetch.collected; the collation is C, so the LIKE
-- prefix is an index condition), not 442k entries of posts_url_key per call
CREATE INDEX IF NOT EXISTS posts_live_day_url ON posts (day, url) WHERE thread IS NULL;
-- posts_live_stitle: the WiseSearch relay's lookup of a post by its title (wisers/fulltext.py, which repeats the
-- octet_length guard so this partial index applies; a btree entry holds at most ~2700 bytes)
CREATE INDEX IF NOT EXISTS posts_live_stitle ON posts (stitle) WHERE thread IS NULL AND octet_length(stitle) < 2000;
-- posts_id_seen: a join from another table to the posts in a stage's work (seen_cond: seen, first_run) checks them
-- here, not on each post's page (the monitor's pictures count, 2026-10-27)
CREATE INDEX IF NOT EXISTS posts_id_seen ON posts (id) INCLUDE (seen) WHERE first_run IS NOT TRUE;
CREATE INDEX IF NOT EXISTS posts_seen ON posts (seen) INCLUDE (sid, source, published, thread);
-- posts_live_seen: the same for posts that aren't comments (thread IS NULL): 39 MB beside its 540 MB; the
-- monitor's per-source days (monitor.sources, every 2 min) read 220 MB of posts_seen, mostly comments' entries
CREATE INDEX IF NOT EXISTS posts_live_seen ON posts (seen) INCLUDE (sid, published) WHERE thread IS NULL;
CREATE INDEX IF NOT EXISTS posts_doc ON posts (source_type, cached_at) WHERE thread IS NULL;
CREATE INDEX IF NOT EXISTS posts_doc_notext ON posts (source_type, seen) WHERE thread IS NULL AND (text IS NULL OR text = '');
-- titles from AI (titles.py; Deyao, 2026-10-14) for posts with no title of their own: ai_title_kind 'extracted' (the
-- post's own opening headline) or 'written' (by AI, marked as such wherever shown); the model, when, and the md5 of
-- the title + text it was made from (set on posts that need none too: checked)
ALTER TABLE posts ADD COLUMN IF NOT EXISTS ai_title TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS ai_title_kind TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS ai_title_model TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS ai_title_at TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS ai_title_hash TEXT;
-- media.py (Deyao, 2026-10-14): the video and audio clips articles and posts carry, each transcribed once (key = the
-- provider's clip id or the media URL without its query; status queued | done | failed), post_media links them to
-- posts, media_scan marks the posts whose page was looked at (found = clips, NULL = the page could not be read), and
-- posts.transcript is the post's finished transcripts joined, part of its search text and of the scorer's input
CREATE TABLE IF NOT EXISTS media (
  key TEXT PRIMARY KEY, url TEXT NOT NULL, kind TEXT NOT NULL, host TEXT, status TEXT NOT NULL DEFAULT 'queued',
  tries INTEGER NOT NULL DEFAULT 0, error TEXT, next_try TEXT, duration DOUBLE PRECISION, transcript TEXT, model TEXT,
  cost DOUBLE PRECISION, first_seen TEXT, newest TEXT, done_at TEXT);
CREATE INDEX IF NOT EXISTS media_due ON media (status, newest DESC);
ALTER TABLE media SET (autovacuum_vacuum_insert_scale_factor = 0.05, autovacuum_vacuum_scale_factor = 0.05);
-- a YouTube clip's own subtitles, kept beside the model's transcript (media.py; Deyao, 2026-10-16)
ALTER TABLE media ADD COLUMN IF NOT EXISTS captions TEXT;
ALTER TABLE media ADD COLUMN IF NOT EXISTS captions_model TEXT;
-- when a YouTube clip's subtitles were read on their own while transcription is off (media.captions_one; Deyao,
-- 2026-10-20): read once, the clip stays queued for the model
ALTER TABLE media ADD COLUMN IF NOT EXISTS captions_at TEXT;
-- a clip in hand (media.claim): the copy transcribing it and until when its claim holds unrenewed, so several
-- transcriber copies (a rollout's old and new pod) never take the same clip
ALTER TABLE media ADD COLUMN IF NOT EXISTS claimed_by TEXT;
ALTER TABLE media ADD COLUMN IF NOT EXISTS claimed_until TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS post_media (
  post_id BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE, key TEXT NOT NULL REFERENCES media(key) ON DELETE CASCADE,
  PRIMARY KEY (post_id, key));
CREATE INDEX IF NOT EXISTS post_media_key ON post_media (key);
-- tiktok_video.py (Deyao, 2026-10-22): the video of each TikTok post whose stage-1 score reached streams.SCREEN,
-- downloaded once into the bucket (s3_key); status queued | ok | failed | gone; how = stage1 | sweep | backlog
CREATE TABLE IF NOT EXISTS tiktok_videos (
  post_id BIGINT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE, video_id TEXT NOT NULL, url TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'ok', 'failed', 'gone')), screen INTEGER,
  stream TEXT, how TEXT, queued_at TIMESTAMPTZ NOT NULL DEFAULT now(), tries INTEGER NOT NULL DEFAULT 0,
  next_try TIMESTAMPTZ, error TEXT, s3_key TEXT, bytes BIGINT, duration DOUBLE PRECISION, width INTEGER, height INTEGER,
  vcodec TEXT, acodec TEXT, format TEXT, done_at TIMESTAMPTZ, claimed_by TEXT, claimed_until TIMESTAMPTZ);
CREATE INDEX IF NOT EXISTS tiktok_videos_due ON tiktok_videos (queued_at DESC) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS tiktok_videos_done ON tiktok_videos (done_at);
CREATE INDEX IF NOT EXISTS tiktok_videos_vid ON tiktok_videos (video_id);
CREATE TABLE IF NOT EXISTS media_scan (post_id BIGINT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE, at TEXT, found INTEGER);
-- autovacuum after 2% inserted rows on the insert-mostly tables (scores, scored, media_scan, post_images; media 5%, and
-- 5% changed rows on the updated ones), not the default 20%, keeps their visibility maps current for index-only scans
ALTER TABLE media_scan SET (autovacuum_vacuum_insert_scale_factor = 0.02);
-- links.py (Deyao, 2026-10-15): posts whose own text is only a link get the linked page's title and description;
-- one row per post tried: tries, the next try, status pending | done | failed, the link read
CREATE TABLE IF NOT EXISTS link_scan (post_id BIGINT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE, url TEXT,
  tries INTEGER NOT NULL DEFAULT 0, status TEXT, at TEXT, next_try TEXT, error TEXT);
-- follow.py (Deyao, 2026-10-20): the links of each post that reached a stream, handed to ingest. follow_scan: one row
-- per post followed (links found, how its body's links were read); follow_links: one row per link (origin = the
-- linking post's url; post_id NULL for a Reddit mirror bot's post, which is never stored): status pending | new |
-- duplicate | unknown | skipped | failed, reason, the address it led to, the post it became or matched
CREATE TABLE IF NOT EXISTS follow_scan (post_id BIGINT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE, at TEXT,
  links INTEGER, body TEXT);
CREATE TABLE IF NOT EXISTS follow_links (
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, post_id BIGINT REFERENCES posts(id) ON DELETE CASCADE,
  origin TEXT NOT NULL, url TEXT NOT NULL, final_url TEXT, host TEXT, status TEXT NOT NULL, reason TEXT,
  target_id BIGINT REFERENCES posts(id) ON DELETE SET NULL, tries INTEGER NOT NULL DEFAULT 0, next_try TEXT, error TEXT,
  at TEXT NOT NULL, done_at TEXT, UNIQUE (origin, url));
CREATE INDEX IF NOT EXISTS follow_links_post ON follow_links (post_id);
CREATE INDEX IF NOT EXISTS follow_links_due ON follow_links (next_try) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS follow_links_at ON follow_links (at);
CREATE INDEX IF NOT EXISTS follow_links_unknown ON follow_links (host) WHERE status = 'unknown';
-- thin.py (Deyao, 2026-10-15): posts with almost no content, held from the scorer while content may still come
-- (status waiting, looked at again at check_at) or rejected (never scored, filed or sent); hash = the content judged
CREATE TABLE IF NOT EXISTS thin_posts (post_id BIGINT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE, hash TEXT,
  units INTEGER, status TEXT, waits TEXT, first_at TEXT, check_at TEXT, at TEXT);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS transcript TEXT;
-- images.py (Deyao, 2026-10-14): every picture on a post, scored 0–100 for meaning by a vision model, once per picture
-- (images.hash = sha256 of its bytes); post_images links posts to pictures (by URL, the hash once downloaded);
-- image_scan marks the posts looked at; posts.image_text = the text read from the post's meaningful pictures
CREATE TABLE IF NOT EXISTS images (
  hash TEXT PRIMARY KEY, url TEXT, width INTEGER, height INTEGER, score INTEGER, reason TEXT, text TEXT, model TEXT,
  at TEXT);
CREATE TABLE IF NOT EXISTS post_images (
  post_id BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE, url TEXT NOT NULL, pos INTEGER, hash TEXT,
  tries INTEGER NOT NULL DEFAULT 0, error TEXT, PRIMARY KEY (post_id, url));
CREATE INDEX IF NOT EXISTS post_images_url ON post_images (url);
-- tries included: the monitor's count of pictures waiting and failed reads this index alone (with posts_id_seen)
CREATE INDEX IF NOT EXISTS post_images_due ON post_images (post_id) INCLUDE (tries) WHERE hash IS NULL;
CREATE INDEX IF NOT EXISTS post_images_hash ON post_images (hash);
ALTER TABLE post_images SET (autovacuum_vacuum_insert_scale_factor = 0.02);
CREATE TABLE IF NOT EXISTS image_scan (post_id BIGINT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE, at TEXT, found INTEGER);
-- the monitor's posts looked at since a time
CREATE INDEX IF NOT EXISTS image_scan_at ON image_scan (at);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS image_text TEXT;
-- where a post's stored text came from (Deyao, 2026-10-15; paywall.VIA: feed | page | browser | solver | page_data |
-- excerpt | archive; NULL = before it was recorded) and, for an archive.today copy, the snapshot's URL (text_via_url)
ALTER TABLE posts ADD COLUMN IF NOT EXISTS text_via TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS text_via_url TEXT;
CREATE INDEX IF NOT EXISTS posts_excerpt ON posts (id) WHERE text_via = 'excerpt';
-- paywall.py: the archive.today lookups of each excerpt post (status pending | done | none | failed; looks = lookups so
-- far; submitted_at = when the page was submitted for capture), and every paid captcha solve at the provider's own
-- figure (2Captcha's stated cost, CapSolver's balance before − after)
CREATE TABLE IF NOT EXISTS archive_scan (post_id BIGINT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
  status TEXT, looks INTEGER NOT NULL DEFAULT 0, submitted_at TEXT, snapshot TEXT, chars INTEGER, at TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS solver_spend (id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, at TEXT NOT NULL,
  day TEXT NOT NULL, provider TEXT NOT NULL, purpose TEXT NOT NULL, usd DOUBLE PRECISION NOT NULL DEFAULT 0, note TEXT);
CREATE INDEX IF NOT EXISTS solver_spend_day ON solver_spend (day);

-- app.py: the streams' kept items (one row per day, stream and url, as the old archive/<day>.json files)
CREATE TABLE IF NOT EXISTS stream_items (
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, day TEXT NOT NULL, stream TEXT NOT NULL, url TEXT NOT NULL,
  item JSONB NOT NULL, UNIQUE (day, stream, url));
CREATE INDEX IF NOT EXISTS stream_items_url ON stream_items(url);
-- 串流's day = the day the item was filed and sent (item.seen, HKT ISO; Deyao, 2026-10-20), not the publish day `day`
CREATE INDEX IF NOT EXISTS stream_items_sent_day ON stream_items ((left(item->>'seen', 10)));
-- events.py (Deyao, 2026-10-21): the pipeline's event log for the live dashboard (/app/live): kind dup | fulltext |
-- link | comments | stage1 | stage2 | alert | problem, the post it is about, a small payload; kept events.KEEP
CREATE TABLE IF NOT EXISTS events (
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT now(), kind TEXT NOT NULL,
  post_id BIGINT, data JSONB);
CREATE INDEX IF NOT EXISTS events_kind ON events (kind, id);
CREATE INDEX IF NOT EXISTS events_at ON events (at);
CREATE TABLE IF NOT EXISTS poll_seen (url TEXT PRIMARY KEY, at TEXT);
CREATE TABLE IF NOT EXISTS poll_state (k TEXT PRIMARY KEY, v JSONB);
-- sid_reads: when each source (sid) was last read (corpus.ingest); a read after COLLECT_GAP without one is a restart:
-- its new posts are backlog (first_run), like a source's first read (Deyao, 2026-10-27)
CREATE TABLE IF NOT EXISTS sid_reads (sid TEXT PRIMARY KEY, last_read TIMESTAMPTZ NOT NULL);

-- streams.py: each post's relevance to each stream (0–100, YYC's rubric; every stream's score since 21 Oct 2026, kv scores_all_from: before that only 20 or more), and
-- the content (md5 of title + text) each post was last scored on, so a post whose content changes is scored again
CREATE TABLE IF NOT EXISTS scores (
  stream TEXT NOT NULL, post_id BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE, score INTEGER NOT NULL,
  reason TEXT, category TEXT, scored_at TEXT, PRIMARY KEY (stream, post_id));
CREATE INDEX IF NOT EXISTS scores_post ON scores(post_id);
-- a stream's posts at or above a score (follow.due, 數據分析's members, the market backlog) from the index alone
CREATE INDEX IF NOT EXISTS scores_stream_score ON scores (stream, score) INCLUDE (post_id);
ALTER TABLE scores SET (autovacuum_vacuum_insert_scale_factor = 0.02);
-- two-stage scoring (streams.confirm, Deyao 2026-10-20): the model whose score this is, and for a pair stage 2 scored
-- again, stage 1's score and reason
ALTER TABLE scores ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE scores ADD COLUMN IF NOT EXISTS screen INTEGER;
ALTER TABLE scores ADD COLUMN IF NOT EXISTS screen_reason TEXT;
CREATE TABLE IF NOT EXISTS scored (
  post_id BIGINT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE, hash TEXT NOT NULL, sentiment TEXT, labels TEXT,
  at TEXT);
ALTER TABLE scored ADD COLUMN IF NOT EXISTS defs TEXT;
ALTER TABLE scored ADD COLUMN IF NOT EXISTS tag_model TEXT;
ALTER TABLE scored ADD COLUMN IF NOT EXISTS all_scores BOOLEAN;  -- streams.py (21 Oct 2026): every stream's score stored, 0–19 too
ALTER TABLE scored ADD COLUMN IF NOT EXISTS answers JSONB;  -- streams.py (21 Oct 2026): each stage's model answer for the post, as answered ({stage1|stage2: {model, answer}})
-- the monitor's last scoring time (max(at)) and posts scored since a time (catalogue): not a scan of every row
CREATE INDEX IF NOT EXISTS scored_at ON scored (at);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS title_from_body BOOLEAN;  -- corpus.body_title: the post has no title; title = its text's opening  -- the model the sentiment and labels are from (streams.confirm)  -- streams.defs_hash: the stream definitions it was scored on
-- streams.py: the market a post at or above a split stream's threshold reaches (GALBOT → hk | mainland, the binary
-- classifier; how = 'model' or 'fallback' to the source's region, with the model's invalid answers in note)
CREATE TABLE IF NOT EXISTS markets (
  stream TEXT NOT NULL, post_id BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE, market TEXT NOT NULL
  CHECK (market IN ('hk', 'mainland')), how TEXT NOT NULL, note TEXT, model TEXT, at TEXT, PRIMARY KEY (stream, post_id));

-- twopass.py (Deyao, 2026-10-14): posts scored as one unit with their comments (a LIHKG thread; social posts),
-- twice at most: at capture and again at published + 30 min. Keyed by the post's URL (a fetcher marks a post before
-- its row exists). reading_at/captured_at: a fetcher is reading / has read its comments at capture; due_at: the
-- second scoring's time (set at the first scoring when it came before it); fetched_at: comments re-read at or after
-- due_at; first_at/second_at: the two scorings
CREATE TABLE IF NOT EXISTS two_pass (
  url TEXT PRIMARY KEY, reading_at TIMESTAMPTZ, captured_at TIMESTAMPTZ, due_at TIMESTAMPTZ, fetched_at TIMESTAMPTZ,
  first_at TIMESTAMPTZ, second_at TIMESTAMPTZ);
CREATE INDEX IF NOT EXISTS two_pass_due ON two_pass(due_at) WHERE second_at IS NULL;
-- every scoring of such a unit (the latest is the current one, in scores/scored; earlier ones are history)
CREATE TABLE IF NOT EXISTS score_passes (
  post_id BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE, pass INTEGER NOT NULL, at TIMESTAMPTZ NOT NULL,
  comments INTEGER, comments_upto BIGINT, result JSONB, PRIMARY KEY (post_id, pass));
-- the comment rows the post's latest scoring saw: those with posts.id <= comments_upto (the item page's divider)
ALTER TABLE scored ADD COLUMN IF NOT EXISTS comments_upto BIGINT;
ALTER TABLE scored SET (autovacuum_vacuum_insert_scale_factor = 0.02, autovacuum_vacuum_scale_factor = 0.05);

-- insights.py: the general settings (infra/control.py set)
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v JSONB);
-- the e-mail alerts are gone (Deyao, 2026-10-26): their per-stream settings and general settings
DROP TABLE IF EXISTS profiles;
DELETE FROM settings WHERE k IN ('alert_labels', 'burst_posts', 'burst_hours', 'burst_max_si', 'mail_from_name');

-- ai.py: OpenRouter's charged cost per HKT day and purpose, and every call (and agent question) for the projections
CREATE TABLE IF NOT EXISTS ai_spend (day TEXT, purpose TEXT, usd DOUBLE PRECISION NOT NULL DEFAULT 0,
  PRIMARY KEY (day, purpose));
CREATE TABLE IF NOT EXISTS ai_runs (
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT now(), purpose TEXT NOT NULL,
  model TEXT, usd DOUBLE PRECISION, input_tokens INTEGER, output_tokens INTEGER, chars INTEGER, calls INTEGER);
CREATE INDEX IF NOT EXISTS ai_runs_purpose ON ai_runs(purpose, id);
-- costlog.py (the cost profile, Deyao, 2026-10-16): the job each call belongs to and OpenRouter's generation id (its own
-- per-call record); every paid TikHub/AnyAPI request; the providers' own counters every 5 min (the monitor)
ALTER TABLE ai_runs ADD COLUMN IF NOT EXISTS job TEXT;
ALTER TABLE ai_runs ADD COLUMN IF NOT EXISTS generation TEXT;
CREATE INDEX IF NOT EXISTS ai_runs_at ON ai_runs(at);
CREATE TABLE IF NOT EXISTS paid_calls (
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT now(), provider TEXT NOT NULL,
  job TEXT, endpoint TEXT NOT NULL, status INTEGER, ok BOOLEAN NOT NULL DEFAULT TRUE, units INTEGER NOT NULL DEFAULT 1,
  usd DOUBLE PRECISION, basis TEXT);
CREATE INDEX IF NOT EXISTS paid_calls_at ON paid_calls(at);
CREATE TABLE IF NOT EXISTS provider_snapshots (
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT now(), provider TEXT NOT NULL,
  status TEXT, data JSONB NOT NULL);
CREATE INDEX IF NOT EXISTS provider_snapshots_at ON provider_snapshots(provider, at);


-- Live updates (live.py, ARCHITECTURE.md "Live updates"): every change a page shows is announced on channel yyc_live
-- as a topic ("feed:<day>", "scores:<day>", "item:<post id>"); live.py batches them to the site's LiveHub, which tells
-- the open pages showing that topic to read it again. Triggers, so every writer (poller, jobs, scripts) is covered.
CREATE OR REPLACE FUNCTION live_post(pid BIGINT) RETURNS VOID LANGUAGE sql AS $f$
  SELECT pg_notify('yyc_live', 'item:' || pid) WHERE pid IS NOT NULL $f$;
CREATE OR REPLACE FUNCTION live_by_post_id() RETURNS TRIGGER LANGUAGE plpgsql AS $f$
BEGIN
  PERFORM live_post(CASE WHEN TG_OP = 'DELETE' THEN OLD.post_id ELSE NEW.post_id END);
  RETURN NULL;
END $f$;
CREATE OR REPLACE FUNCTION live_stream_item() RETURNS TRIGGER LANGUAGE plpgsql AS $f$
DECLARE d TEXT := left(CASE WHEN TG_OP = 'DELETE' THEN OLD.item ELSE NEW.item END->>'seen', 10);  -- 串流's day (feed.ts)
BEGIN
  PERFORM pg_notify('yyc_live', 'feed:' || d);
  PERFORM pg_notify('yyc_live', 'scores:' || d);
  RETURN NULL;
END $f$;
CREATE OR REPLACE FUNCTION live_posts() RETURNS TRIGGER LANGUAGE plpgsql AS $f$
BEGIN
  IF NEW.thread IS NOT NULL THEN  -- a comment or reply: its thread's page lists it
    PERFORM live_post((SELECT id FROM posts WHERE url = NEW.thread));
  END IF;
  IF TG_OP = 'UPDATE' THEN PERFORM live_post(NEW.id); END IF;
  RETURN NULL;
END $f$;
CREATE OR REPLACE FUNCTION live_media() RETURNS TRIGGER LANGUAGE plpgsql AS $f$
BEGIN
  PERFORM live_post(pm.post_id) FROM post_media pm WHERE pm.key = NEW.key;
  RETURN NULL;
END $f$;
CREATE OR REPLACE FUNCTION live_images() RETURNS TRIGGER LANGUAGE plpgsql AS $f$
BEGIN
  PERFORM live_post(pi.post_id) FROM post_images pi WHERE pi.hash = NEW.hash;
  RETURN NULL;
END $f$;
DROP TRIGGER IF EXISTS live ON stream_items;
CREATE TRIGGER live AFTER INSERT OR UPDATE OR DELETE ON stream_items FOR EACH ROW EXECUTE FUNCTION live_stream_item();
DROP TRIGGER IF EXISTS live_ins ON posts;
CREATE TRIGGER live_ins AFTER INSERT ON posts FOR EACH ROW WHEN (NEW.thread IS NOT NULL) EXECUTE FUNCTION live_posts();
DROP TRIGGER IF EXISTS live_upd ON posts;
CREATE TRIGGER live_upd AFTER UPDATE ON posts FOR EACH ROW WHEN (
  OLD.title IS DISTINCT FROM NEW.title OR OLD.text IS DISTINCT FROM NEW.text OR OLD.cached_at IS DISTINCT FROM NEW.cached_at
  OR OLD.published IS DISTINCT FROM NEW.published OR OLD.date_only IS DISTINCT FROM NEW.date_only
  OR OLD.engagement IS DISTINCT FROM NEW.engagement OR OLD.article_id IS DISTINCT FROM NEW.article_id
  OR OLD.ai_title IS DISTINCT FROM NEW.ai_title OR OLD.transcript IS DISTINCT FROM NEW.transcript
  OR OLD.image_text IS DISTINCT FROM NEW.image_text OR OLD.text_via IS DISTINCT FROM NEW.text_via) EXECUTE FUNCTION live_posts();
DROP TRIGGER IF EXISTS live ON media;
CREATE TRIGGER live AFTER UPDATE ON media FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status
  OR OLD.transcript IS DISTINCT FROM NEW.transcript) EXECUTE FUNCTION live_media();
DROP TRIGGER IF EXISTS live ON images;
CREATE TRIGGER live AFTER INSERT OR UPDATE ON images FOR EACH ROW EXECUTE FUNCTION live_images();
DO $l$ DECLARE t TEXT; BEGIN
  FOREACH t IN ARRAY ARRAY['scored', 'scores', 'tags', 'thin_posts', 'post_media', 'post_images', 'markets', 'score_passes'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS live ON %I', t);
    EXECUTE format('CREATE TRIGGER live AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION live_by_post_id()', t);
  END LOOP;
END $l$;

-- the edge app's query dictionary (searchtext.publish_edge): every word a query can contain, log of its frequency, per
-- stamp (searchtext.VERSION + jieba + OpenCC); the Worker reads the rows for a query's candidate substrings
CREATE TABLE IF NOT EXISTS search_words (v TEXT NOT NULL, w TEXT NOT NULL, lp DOUBLE PRECISION NOT NULL, PRIMARY KEY (v, w));

-- reports.py: every report file it builds (/reports/<day>/<profile>/<name>), served from here by web.py and the edge
-- app (web-app); name's extension picks the Content-Type (ctype, stored as served)
CREATE TABLE IF NOT EXISTS report_files (
  day TEXT NOT NULL, profile TEXT NOT NULL, name TEXT NOT NULL, ctype TEXT NOT NULL, body BYTEA NOT NULL,
  created TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (day, profile, name));

-- NEARBY/n (query.py): a and b both occur in hay within n characters (positions of non-overlapping matches)
CREATE OR REPLACE FUNCTION near(hay TEXT, a TEXT, b TEXT, n INTEGER) RETURNS BOOLEAN
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $f$
DECLARE pa INTEGER[] := '{}'; pb INTEGER[] := '{}'; i INTEGER; j INTEGER; x INTEGER; y INTEGER;
        len INTEGER; lim INTEGER;
BEGIN
  IF hay IS NULL OR a IS NULL OR b IS NULL THEN RETURN FALSE; END IF;
  len := length(hay);
  lim := n + greatest(length(a), length(b));
  i := 0;
  LOOP
    j := strpos(substr(hay, i + 1), a);
    EXIT WHEN j = 0;
    pa := pa || (i + j - 1);
    i := i + j - 1 + greatest(length(a), 1);
    EXIT WHEN i > len;
  END LOOP;
  IF cardinality(pa) = 0 THEN RETURN FALSE; END IF;
  i := 0;
  LOOP
    j := strpos(substr(hay, i + 1), b);
    EXIT WHEN j = 0;
    pb := pb || (i + j - 1);
    i := i + j - 1 + greatest(length(b), 1);
    EXIT WHEN i > len;
  END LOOP;
  FOREACH x IN ARRAY pa LOOP
    FOREACH y IN ARRAY pb LOOP
      IF abs(x - y) <= lim THEN RETURN TRUE; END IF;
    END LOOP;
  END LOOP;
  RETURN FALSE;
END $f$;
"""


# ---- the connection pool ----

class Row(tuple):
    """a result row: by position (r[0], unpacking) and by column name (r["url"], dict(r)), like sqlite3.Row"""
    _keys, _idx = (), {}

    def __getitem__(self, i):
        return tuple.__getitem__(self, self._idx[i] if isinstance(i, str) else i)

    def keys(self):
        return list(self._keys)

    def get(self, k, default=None):
        return tuple.__getitem__(self, self._idx[k]) if k in self._idx else default


def _rows(cursor):
    """psycopg row factory: the Row class for this result (calling it with a row's values makes the row)"""
    names = [d.name for d in cursor.description or ()]
    cls = type("Row", (Row,), {"_keys": tuple(names), "_idx": {n: i for i, n in enumerate(names)}})
    return cls


class Result:
    """what execute() returns: the fetched rows (fetchone, fetchall, iteration) and rowcount"""
    def __init__(self, rows, rowcount):
        self.rows, self.rowcount = rows, rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


_pool, _pool_lock = None, threading.Lock()
CHECK_IDLE = 2.0  # seconds: a connection back in the pool for less is handed out untested
_returned = {}  # id(connection) → when it came back to the pool


def _check(conn):
    """the pool's check: test a connection (a round trip, ~24 ms to Neon) when it sat idle CHECK_IDLE or more, or when
    the server has said something on it since (its socket is readable: an idle connection gets nothing unless the
    server ended it, e.g. a Postgres restart's "terminating connection", or closed it); one used moments ago with a
    quiet socket is handed out untested (tests/test_item_kinds.py DatabaseRestart, tests/test_pool_check.py)"""
    if time.monotonic() - _returned.get(id(conn), 0) < CHECK_IDLE and not _readable(conn):
        return
    ConnectionPool.check_connection(conn)


def _readable(conn):
    try:
        return bool(select.select([conn.fileno()], [], [], 0)[0])
    except Exception:  # noqa: BLE001  closed or broken: the real check replaces it
        return True


def _reset(conn):
    _returned[id(conn)] = time.monotonic()


def _configure(conn):
    conn.autocommit = True
    # no server-side prepared statements (psycopg prepares a query after 5 runs by default): a prepared `SELECT p.*`
    # keeps its result type, and once a rollout's new pod adds a column, Postgres refuses it on the outgoing pod's
    # connections ("cached plan must not change result type"). Unprepared, every statement is planned against the
    # schema of the moment; tests/test_prepared_plans.py
    conn.prepare_threshold = None


class Index:
    """a migration that builds one index with CREATE INDEX CONCURRENTLY, outside a transaction: reads and writes go on
    while it builds (a plain CREATE INDEX blocks every write to the table for the whole build)"""
    def __init__(self, name, on):
        self.name, self.on = name, on

    def apply(self, c, version):
        for attempt in range(MIGRATE_TRIES):
            # a CONCURRENTLY build that failed leaves an invalid index behind, which IF NOT EXISTS would keep
            if c.execute("SELECT 1 FROM pg_index WHERE indexrelid = to_regclass(%s) AND NOT indisvalid",
                         (self.name,)).fetchone():
                c.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {self.name}")
            try:
                # no lock_timeout: its SHARE UPDATE EXCLUSIVE lock queues no reader or writer, and its waits for the
                # transactions already running are lock waits that 2 s would cut short
                c.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {self.name} ON {self.on}")
                break
            except (psycopg.errors.DeadlockDetected, psycopg.errors.LockNotAvailable):
                if attempt == MIGRATE_TRIES - 1:
                    raise
                time.sleep(random.uniform(*MIGRATE_SLEEP))
        _set_version(c, version)


class Change:
    """a migration of transactional DDL: one short transaction that first locks its tables in a fixed order (sorted by
    name) in the strongest mode it needs, so it never holds a weaker lock while waiting to upgrade it (the deadlocks of
    the whole SCHEMA against live traffic); lock_timeout 2 s, so a wait never queues the table's readers behind it for
    longer, then a jittered retry"""
    def __init__(self, tables, mode, sql):
        self.tables, self.mode, self.sql = sorted(tables), mode, sql

    def apply(self, c, version):
        for attempt in range(MIGRATE_TRIES):
            try:
                with c.transaction():
                    c.execute("SET LOCAL lock_timeout = '2s'")
                    c.execute(f"LOCK TABLE {', '.join(self.tables)} IN {self.mode} MODE")
                    c.execute(self.sql)
                    _set_version(c, version)
                return
            except (psycopg.errors.DeadlockDetected, psycopg.errors.LockNotAvailable):
                if attempt == MIGRATE_TRIES - 1:
                    raise
                time.sleep(random.uniform(*MIGRATE_SLEEP))


# Changes to an existing database, numbered: the last one applied is kv 'schema_version' (version 0 = SCHEMA as it was
# on 29 Oct 2026). A schema change is a new entry here AND the same object in SCHEMA (an empty database gets SCHEMA and
# the latest version; tests/test_schema.py checks that both end the same)
MIGRATIONS = (
    (1, Index("scores_stream_score", "scores (stream, score) INCLUDE (post_id)")),
    (2, Index("posts_live_day_url", "posts (day, url) WHERE thread IS NULL")),
    (3, Index("posts_live_stitle", "posts (stitle) WHERE thread IS NULL AND octet_length(stitle) < 2000")),
    (4, Index("image_scan_at", "image_scan (at)")),
    # reloptions take SHARE UPDATE EXCLUSIVE: no reader or writer waits on them
    (5, Change(("post_images", "media_scan", "scores", "scored", "media"), "SHARE UPDATE EXCLUSIVE", """
ALTER TABLE post_images SET (autovacuum_vacuum_insert_scale_factor = 0.02);
ALTER TABLE media_scan SET (autovacuum_vacuum_insert_scale_factor = 0.02);
ALTER TABLE scores SET (autovacuum_vacuum_insert_scale_factor = 0.02);
ALTER TABLE scored SET (autovacuum_vacuum_insert_scale_factor = 0.02, autovacuum_vacuum_scale_factor = 0.05);
ALTER TABLE media SET (autovacuum_vacuum_insert_scale_factor = 0.05, autovacuum_vacuum_scale_factor = 0.05)""")),
    # data only: YYC AI's status-change events were what 'verdict' until monitor e6f658b0 named them 'status' (Deyao,
    # 2026-11-02: "verdict" is confusing); ROW EXCLUSIVE lets readers and the monitor's inserts carry on
    (6, Change(("events",), "ROW EXCLUSIVE", """
UPDATE events SET data = jsonb_set(data, '{what}', '"status"') WHERE kind = 'ai' AND data->>'what' = 'verdict'""")),
    # posts' statistics after 0.5% changed rows (SCHEMA's posts comment)
    (7, Change(("posts",), "SHARE UPDATE EXCLUSIVE", """
ALTER TABLE posts SET (autovacuum_analyze_scale_factor = 0.005)""")),
)
assert [n for n, _ in MIGRATIONS] == list(range(1, len(MIGRATIONS) + 1))
SCHEMA_VERSION = len(MIGRATIONS)
V0_HASH = "924bdb91086cd9a3"  # sha256(SCHEMA)[:16] of version 0 (commit 2d3731f8: tests/schema_v0.sql), as the old applier stored it
MIGRATE_TRIES = 20
MIGRATE_SLEEP = (2, 10)  # seconds between tries, jittered so several waiting appliers spread out


def _set_version(c, version):
    c.execute("INSERT INTO kv (k, v) VALUES ('schema_version', %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
              (Jsonb(version),))


def _apply_schema(p):
    """bring the database to SCHEMA_VERSION: an empty one gets SCHEMA in one transaction; an existing one gets each
    migration after its kv 'schema_version' (none yet = version 0, recorded without any DDL), each on its own.
    The whole SCHEMA on a live database deadlocked against its traffic (175 deadlocks and 57 lock-timeout cancels on
    27-28 Oct 2026: CREATE INDEX IF NOT EXISTS took SHARE on posts, then its ALTER TABLEs waited for ACCESS EXCLUSIVE).
    Several pods start at once: the session advisory lock 'yyc schema migrations' lets one apply them while the others
    poll for it outside any statement (one waiting inside pg_advisory_lock() holds a snapshot that CREATE INDEX
    CONCURRENTLY waits for: a deadlock). Its own key, not the old applier's 'yyc schema': a pod of the image before
    this, starting during a migration, takes its old key at once, sees its own schema_hash and returns, instead of
    waiting with a snapshot that the index build waits for (a deadlock that cancelled every try).
    Version 0 is recorded only for the SCHEMA it stands for (kv schema_hash V0_HASH): any other existing database
    stops here with an error, rather than skipping the DDL it lacks."""
    with p.connection() as c:
        # a read-only role (the K-Matrix watch's kmatrix_ro) can't create anything: it only reads
        if not c.execute("SELECT has_schema_privilege('public', 'CREATE')").fetchone()[0]:
            return
        while not c.execute("SELECT pg_try_advisory_lock(hashtext('yyc schema migrations'))").fetchone()[0]:
            time.sleep(random.uniform(0.5, 1.5))
        try:
            row = None
            if c.execute("SELECT to_regclass('public.kv') IS NOT NULL").fetchone()[0]:
                row = c.execute("SELECT v FROM kv WHERE k = 'schema_version'").fetchone()
            if row:
                version = row[0]
            elif c.execute("SELECT to_regclass('public.posts') IS NULL").fetchone()[0]:
                with c.transaction():
                    c.execute(SCHEMA)
                    _set_version(c, SCHEMA_VERSION)
                return
            else:
                old = c.execute("SELECT v FROM kv WHERE k = 'schema_hash'").fetchone()
                if not old or old[0] != V0_HASH:
                    raise RuntimeError(f"corpus: this database's schema_hash is {old[0] if old else None!r}, not version 0's "
                                       f"{V0_HASH!r}: bring it to that SCHEMA first (tests/schema_v0.sql), then start again")
                version = 0
                _set_version(c, version)
            for n, m in MIGRATIONS:
                if n > version:
                    m.apply(c, n)
        finally:
            with contextlib.suppress(psycopg.Error):
                c.execute("SELECT pg_advisory_unlock(hashtext('yyc schema migrations'))")


# Planned primary moves (a CloudNativePG switchover, db/README "Postgres high availability") cut every connection and
# refuse new ones for a few seconds. Statements and transactions caught by it are run again on the new primary, so a
# request waits instead of failing (Deyao, 2026-11-09: switchovers with zero downtime). Running again is always safe:
# the switchover script fences writes first (news-stream/deploy/clusters/talos/db/switchover.py: every table locked
# EXCLUSIVE, so no write transaction is open when the old primary stops), so a cut session was reading or waiting
# before its first write, and nothing of it was committed.
RETRY_FOR = 30.0  # seconds a statement or transaction keeps trying while the database is away
RETRY_GAP = 0.2   # seconds between tries
# a lost connection: the server's own (class 08, 57P01 admin shutdown, 57P02 crash, 57P03 still starting) and the
# client's (no SQLSTATE); 25006: a write that reached a primary just demoted to a standby
_TRANSIENT_STATES = {"57P01", "57P02", "57P03", "25006"}

# the pool opens a lost connection again every RETRY_GAP seconds, not with its doubling backoff (1, 2, 4 s…: the
# requests waiting for it would wait seconds after the database was back)
from psycopg_pool.base import AttemptWithBackoff  # noqa: E402
AttemptWithBackoff.INITIAL_DELAY, AttemptWithBackoff.DELAY_BACKOFF = RETRY_GAP, 1.0


def transient(e):
    """is `e` the database being away (a switchover, a restart), so the same work can run again shortly?"""
    from psycopg_pool import PoolTimeout
    if isinstance(e, PoolTimeout):
        return True
    if not isinstance(e, psycopg.Error):
        return False
    st = getattr(e, "sqlstate", None)
    if st:
        return st in _TRANSIENT_STATES or st.startswith("08")
    return isinstance(e, psycopg.OperationalError)  # connect failures and lost connections carry no SQLSTATE


def retrying(work, what="statement"):
    """work() → its value; while the database is away (transient), again every RETRY_GAP seconds for up to RETRY_FOR"""
    end = None
    while True:
        try:
            return work()
        except Exception as e:  # noqa: BLE001: only transient errors are kept
            if not transient(e):
                raise
            now = time.monotonic()
            if end is None:
                end = now + RETRY_FOR
                print(f"database away ({what}: {str(e).strip().splitlines()[0][:160] if str(e).strip() else type(e).__name__}); "
                      f"trying again for up to {RETRY_FOR:.0f} s", flush=True)
            if now >= end:
                raise
            time.sleep(RETRY_GAP)


def pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                if not DATABASE_URL:
                    raise RuntimeError("DATABASE_URL is not set")
                # check: a connection is tested before it is handed out, so one that died with a Postgres restart
                # or failover is replaced instead of failing its request (each answered 500 once: 15 Oct 2026);
                # only after CHECK_IDLE idle (_check: a test per checkout doubled every statement on Neon)
                p = ConnectionPool(DATABASE_URL, min_size=1, max_size=POOL_MAX, configure=_configure, open=True,
                                   check=_check, reset=_reset, kwargs=CONNECT_KW)
                _apply_schema(p)
                _pool = p
    return _pool


class DB:
    """corpus.db(): execute(sql, params) runs one statement (Postgres SQL, %s placeholders) and returns a Result.
    Outside a `with db():` block every statement commits by itself; inside one, the thread's statements share one
    connection and transaction, committed at the end of the block (rolled back on an exception). db().run(fn) is that
    block as a function, run again from the start when the database goes away under it (a switchover): use it for every
    transaction; statements outside one are run again by themselves."""
    _tx = threading.local()

    def _run(self, conn, sql, params, many=False):
        with conn.cursor(row_factory=_rows) as cur:
            if many:
                cur.executemany(sql, params)
                return Result([], cur.rowcount)
            cur.execute(sql, params)
            return Result(cur.fetchall() if cur.description else [], cur.rowcount)

    def execute(self, sql, params=()):
        conn = getattr(self._tx, "conn", None)
        if conn is not None:
            return self._run(conn, sql, params)  # inside a transaction: run() runs it all again

        def once():
            with pool().connection() as conn:
                return self._run(conn, sql, params)
        return retrying(once)

    def executemany(self, sql, seq):
        seq = list(seq)
        if not seq:
            return Result([], 0)
        conn = getattr(self._tx, "conn", None)
        if conn is not None:
            return self._run(conn, sql, seq, many=True)

        def once():
            with pool().connection() as conn, conn.transaction():
                return self._run(conn, sql, seq, many=True)
        return retrying(once)

    def savepoint(self):
        """inside a transaction: `with db().savepoint():` rolls back only its own statements when it raises"""
        return self._tx.conn.transaction()

    def run(self, fn):
        """fn(db) in one transaction → its value: committed when it returns, rolled back when it raises, and run again
        from the start when the database went away under it (retrying). fn must only touch the database, so that
        running it twice is the same as once. Inside another transaction it simply joins it."""
        if getattr(self._tx, "depth", 0):
            with self:
                return fn(self)

        def once():
            with self:
                return fn(self)
        return retrying(once, "transaction")

    def __enter__(self):
        t = self._tx
        if getattr(t, "depth", 0):
            t.depth += 1
            return self
        t.cm = pool().connection()
        t.conn = t.cm.__enter__()
        t.txcm = t.conn.transaction()
        t.txcm.__enter__()
        t.depth = 1
        return self

    def __exit__(self, *exc):
        t = self._tx
        t.depth -= 1
        if t.depth:
            return False
        try:
            t.txcm.__exit__(*exc)
        finally:
            cm = t.cm
            t.conn = t.cm = t.txcm = None
            cm.__exit__(*exc)
        return False


_db = DB()


def db():
    return _db


def lock_connect(app_name="yyc-news-stream"):
    """a connection of its own for a session advisory lock (corpus.lock, the singletons' locks in app.py and
    podlock.py): the server ends it after LOCK_IDLE idle seconds, so its holder must speak every LOCK_BEAT seconds"""
    return psycopg.connect(DATABASE_URL, autocommit=True, prepare_threshold=None, application_name=app_name,
                           connect_timeout=10, keepalives=1, keepalives_idle=30, keepalives_interval=10,
                           keepalives_count=3, tcp_user_timeout=30000,
                           options=f"-c idle_session_timeout={int(LOCK_IDLE * 1000)}ms -c tcp_keepalives_idle=30 "
                                   "-c tcp_keepalives_interval=10 -c tcp_keepalives_count=3")


class LockTimeout(Exception):
    """lock(name, timeout=S): another holder kept the lock for S seconds"""


def _beat(conn, stop):
    """the holder's heartbeat: keeps the lock's session from being ended as idle while the block runs"""
    while not stop.wait(LOCK_BEAT):
        try:
            conn.execute("SELECT 1")
        except psycopg.Error:
            return  # the session is gone, and its lock with it


@contextlib.contextmanager
def lock(name, wait=True, app_name="yyc-news-stream", timeout=None):
    """an exclusive lock shared by every process and pod (web replicas, the poller, the CronJobs): a Postgres session
    advisory lock on `name`, held on its own connection while the block runs. wait=False: yields False at once when
    another holder has it (the block still runs; check the value).
    The lock's connection is its own, outside the pool: threads waiting for the lock never hold pool connections the
    holder's own statements need (20 Oct 2026: 16 fulltext threads waited on 'proxy_out' with the pool's 10, the
    holder's kv read found none, the run died on PoolTimeout; tests/test_lock_waiters_pool.py).
    The connection is lock_connect's: a holder that vanishes frees the lock within LOCK_IDLE seconds, and a thread
    sends the heartbeat while the block runs (tests/test_lock_ghost.py).
    timeout=S: wait at most S seconds (trying every half second), then raise LockTimeout: a job's wait stays under its
    deadline"""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    with retrying(lambda: lock_connect(app_name), "lock connection") as conn:
        if wait and timeout is not None:
            end = time.monotonic() + timeout
            while not conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (name,)).fetchone()[0]:
                if time.monotonic() >= end:
                    raise LockTimeout(f"{name}: held by another for {timeout} s")
                time.sleep(0.5)
            got = True
        elif wait:
            conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (name,))
            got = True
        else:
            got = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (name,)).fetchone()[0]
        stop = threading.Event()
        if got:
            threading.Thread(target=_beat, args=(conn, stop), daemon=True, name=f"beat-{name}").start()
        try:
            yield got
        finally:
            stop.set()
            if got:
                try:
                    conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (name,))
                except psycopg.OperationalError:
                    pass  # the connection died during the block, and its session's lock with it


def locked(name):
    """is lock(name) held by anyone right now?"""
    with lock(name, wait=False) as got:
        return not got


PEAK_FILE = "/sys/fs/cgroup/memory.peak"  # the container's highest memory use so far (cgroup v2), Chrome's included
PEAK_KEEP = 20  # runs kept per CronJob in kv jobpeak:<cronjob>


def record_peak(path=PEAK_FILE):
    """at a CronJob process's exit: the container's memory peak into kv jobpeak:<cronjob> (the monitor's jobs panel
    shows the highest beside the limit; sizing from measurements, tests/test_job_peak.py). Nothing for other
    processes; never raises"""
    try:
        import costlog
        host = os.environ.get("HOSTNAME", "")
        m = costlog.CRONJOB_POD.match(host) if os.environ.get("KUBERNETES_SERVICE_HOST") else None
        if not m or not DATABASE_URL:
            return
        mib = round(int(open(path).read().strip()) / 1048576)
        k = f"jobpeak:{m.group(1)}"
        runs = (kv_get(k) or [])[-(PEAK_KEEP - 1):]
        runs.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "peak_mib": mib, "pod": host})
        kv_set(k, runs)
    except Exception as e:  # noqa: BLE001: a measurement never fails the job
        print(f"record_peak: {e!r}"[:200], flush=True)


atexit.register(record_peak)


def _dumps(o):
    return json.dumps(o, ensure_ascii=False, default=str).replace("\\u0000", "")  # Postgres JSON can't hold NUL


def J(obj):
    """a value for a JSONB column"""
    return Jsonb(obj, dumps=_dumps)


def kv_get(k, default=None):
    r = db().execute("SELECT v FROM kv WHERE k=%s", (k,)).fetchone()
    return r[0] if r else default


def kv_set(k, v):
    db().execute("INSERT INTO kv (k, v) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET v=excluded.v", (k, J(v)))


def kv_add(k, n):
    """atomically add n to a numeric kv value (0 when missing) → the new value"""
    return db().execute("""INSERT INTO kv (k, v) VALUES (%s, to_jsonb(%s::numeric)) ON CONFLICT (k) DO UPDATE
                           SET v = to_jsonb(coalesce((kv.v #>> '{}')::numeric, 0) + %s::numeric)
                           RETURNING v""", (k, n, n)).fetchone()[0]


def file_get(name, default=LookupError):
    """a document a job writes whole and another reads (once $DATA_DIR/<name>); raises LookupError when it was
    never written, unless a default is given"""
    r = db().execute("SELECT v FROM kv WHERE k=%s", ("file:" + name,)).fetchone()
    if r is None:
        if default is LookupError:
            raise LookupError(f"{name}: not written yet")
        return default
    return r[0]


def file_put(name, obj):
    kv_set("file:" + name, obj)


# ---- classification of a source ----

GLOBAL = {"tiktok": "TikTok", "twitter": "X", "x": "X", "youtube": "YouTube", "reddit": "Reddit", "threads": "Threads",
          "lemon8": "Lemon8", "instagram": "Instagram"}
OFFICIAL = ("newsgov", "infogov", "fso_", "fstb_", "hkma", "sfc", "legco", "gov_")
FORUM = ("discuss", "lihkg", "gnews_discuss", "baby", "hkgolden")
GLOBAL_SIDS = {"nikkei_asia", "ft_hk", "mingtiandi", "reuters", "bloomberg"}  # outlets outside HK and the Mainland
VIDEO_PLATFORMS = {"tiktok", "youtube"}
# Google News publishers: government sites are Official, forums Forum; Taiwan's outlets are Taiwan, other publishers
# outside Hong Kong, Macau and the Mainland are Global (the regions are HK / Macau / Mainland / Taiwan / Global)
GOV_PUBLISHER = re.compile(r"gov\.hk|政府新聞|政府資訊|新聞公報|info\.gov|立法會")
FORUM_PUBLISHER = re.compile(r"討論區|LIHKG|discuss\.com\.hk|高登|HKGolden", re.I)
# forums by their host, whatever name the item carries (Google News gives HKGolden threads as "HKGolden": Deyao,
# 2026-10-20, an HKGolden thread tagged 新聞)
FORUM_HOST = re.compile(r"(^|\.)(hkgolden\.com|lihkg\.com|discuss\.com\.hk|baby-kingdom\.com)$", re.I)
MAINLAND_PUBLISHER = re.compile(r"鳳凰|新华|新華|人民网|人民網|央视|央視|中新网|中新網|环球|環球時報|澎湃|新浪|网易|搜狐|騰訊|腾讯|观察者|觀察者")
TAIWAN_PUBLISHER = re.compile(r"UDN|聯合新聞|联合新闻|經濟日報|自由時報|自由財經|中時|ETtoday|TVBS|三立|中央社|鏡週刊|鏡新聞|鏡報|"
                              r"民視|公視|華視|台視|中天|東森|風傳媒|新頭殼|Newtalk|NOWnews|今日新聞|上報|太報|蕃新聞|"
                              r"Yahoo奇摩|奇摩新聞|PChome|鉅亨|動區|區塊客", re.I)
GLOBAL_PUBLISHER = re.compile(r"世界新聞|世界日報|加西|"
                              r"BBC|Reuters|路透|美國之音|VOA|RFI|法廣|德國之聲|DW|"
                              r"Bloomberg|彭博|紐約時報|New York Times|聯合早報|联合早报|Nikkei|日經", re.I)


# the catalogue sites (sites.json, sites_fetch.py): sid → (type, region, name). Their type may be Newspaper or Magazine
# (報章 / 雜誌) and their region Macau (澳門)
def _site_meta():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return {s["id"]: (s["type"], s["region"], s["name"]) for s in json.load(f)}


SITE_META = _site_meta()
NEWS_TYPES = ("News", "Newspaper", "Magazine", "Official")  # K-Matrix's News side (the rest is CGM)
MACAU_PUBLISHER = re.compile(r"澳門|澳门|TDM|Plataforma|Macao|Macau|濠江|華僑報|力報|正報|市民日報|大眾報|新華澳報|訊報", re.I)


GOV_HOST = re.compile(r"(^|\.)gov(\.[a-z]{2})?$|(^|\.)(gov|govt|gouv)\.[a-z]{2}$|(^|\.)elections\.gov\.hk$")


def link_meta(url):
    """(source_type, region) of a linked page of a site no source reads: Official on a government host (gov.hk, va.gov,
    gov.sg …), else Blog (a website: K-Matrix's CGM side); the region from the host's country domain (.hk HK, .mo
    Macau, .cn Mainland, .tw Taiwan), else Global"""
    h = _host(url)
    stype = "Official" if GOV_HOST.search(h) else "Blog"
    region = ("HK" if h.endswith(".hk") else "Macau" if h.endswith(".mo") else "Mainland" if h.endswith(".cn")
              else "Taiwan" if h.endswith(".tw") else "Global")
    return stype, region


def _host(url):
    m = re.match(r"^[a-z]+://([^/:?#]+)", url or "", re.I)
    return m.group(1).lower() if m else ""


# ---- site names: never a post's title (Deyao, 2026-10-20: an HKGolden thread titled 「香港高登討論區」) ----

# the names a site's pages carry where its headline should be (a JavaScript page's <title>, an e-paper's frame title,
# Google News's title for such a page), for sites whose pages state no og:site_name; by host (subdomains included)
SITE_NAMES = {
    "hkgolden.com": ("香港高登討論區", "高登討論區", "HKGolden", "高登"),
    "discuss.com.hk": ("香討．香港 No.1", "香港討論區", "discuss.com.hk"),
    "lihkg.com": ("LIHKG討論區", "LIHKG"),
    "macaodaily.com": ("澳門日報電子版", "澳門日報"),
    "vakiodaily.com": ("華僑報",),
    "bastillepost.com": ("巴士的報", "Bastille Post"),
}
_GENERIC = re.compile(r"(電子版|電子報|官方網站|網站|官網|首頁|主頁|新聞網|online|home|homepage|首页)$", re.I)
_NAME_JUNK = re.compile(r"[\s　.．·・,，:：|｜/\\\-–—_()（）「」『』【】《》]+")


def _name_key(s):
    k = _NAME_JUNK.sub("", (s or "").lower())
    for _ in range(2):
        k = _GENERIC.sub("", k)
    return k


def site_names(url):
    """the known names of url's site (SITE_NAMES)"""
    h = _host(url)
    return tuple(n for d, names in SITE_NAMES.items() if h == d or h.endswith("." + d) for n in names)


def site_named(title, *names, url=None):
    """is the title only a site's name: one of names (the source's, the website's, a page's og:site_name), or a known
    name of url's site (SITE_NAMES)? Case, spaces, punctuation and generic suffixes (電子版, 網站, 首頁…) don't count.
    Such a title says nothing about the post: it is treated as missing (corpus.ingest, links.py, fulltext_fetch.py)"""
    k = _name_key(title)
    if not k:
        return False
    return any(k == _name_key(n) for n in list(names) + list(site_names(url)) if n and _name_key(n))


SOCIAL_PLATFORMS = ("Facebook", "Instagram", "Threads")  # the platforms of sids facebook / instagram (classify)


def classify(it):
    """→ website, platform, author, board, source_type, region for a fetched item"""
    sid = it.get("sid") or ""
    source = it.get("source") or ""
    section = it.get("section") or ""
    author = it.get("author")
    platform = None
    m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", source)  # "author (platform)" from the social jobs
    if sid.startswith("bc_"):  # TV/radio programmes transcribed by broadcast_fetch.py; the programme is the board
        website = platform = source
        stype, region = "Broadcast", "HK"
    elif sid.startswith("tikhub_"):
        key = sid[len("tikhub_"):]
        platform = GLOBAL.get(key) or key
        author = author or (m.group(1) if m else source)
        website = platform
        stype = "Video" if key in VIDEO_PLATFORMS else "Social Media"
        region = "Global"
    elif sid.startswith("watch_"):  # an account watched directly (watch_fetch.py): the account is the board
        key = sid[len("watch_"):]
        platform = GLOBAL.get(key) or key
        author = author or (m.group(1) if m else source)
        website, section = platform, author
        stype = "Video" if key in VIDEO_PLATFORMS else "Social Media"
        region = "HK"
    elif sid in ("instagram", "facebook"):
        # "account (Instagram|Threads)" from ig_fetch; a page name may hold its owner in brackets too (「胡‧說樓市
        # (胡偉雄)」, a Facebook page): that is part of the name, not a platform
        m = m if m and m.group(2) in SOCIAL_PLATFORMS else None
        platform = m.group(2) if m else sid.capitalize()
        author = author or (m.group(1) if m else source)
        website, stype, region = platform, "Social Media", "HK"
        section = author  # the page/account is the "board", as K-Matrix lists Facebook pages
    elif sid.startswith("tg_") or sid == "now_telegram" or (m and m.group(2) == "Telegram"):
        platform = website = "Telegram"
        author = author or (m.group(1) if m else source)
        section = m.group(1) if m else source  # the channel is the board
        stype, region = "Social Media", "HK"
    elif sid.startswith("yt_") or sid.endswith("_youtube"):
        platform, stype, region = "YouTube", "Video", "HK"
        website = "YouTube"
        author = author or (m.group(1) if m else source)
        section = m.group(1) if m else source  # the channel is the board
    elif "linkedin" in sid or "LinkedIn" in source:
        platform = website = "LinkedIn"
        author = author or re.sub(r"\s*LinkedIn$", "", source).strip()
        section = author
        stype, region = "Social Media", "HK"
    elif sid in SITE_META:
        stype, region, _ = SITE_META[sid]
        website = source
    elif sid.startswith("link_"):  # a linked page of a site no source reads (follow.py's generic reader)
        website = source
        stype, region = link_meta(it.get("url"))
    elif any(sid.startswith(f) for f in FORUM) or FORUM_PUBLISHER.search(source) or FORUM_HOST.search(_host(it.get("url"))):
        h = _host(it.get("url"))
        website = ("高登討論區" if h.endswith("hkgolden.com") or re.search(r"高登|HKGolden", source, re.I)
                   else "LIHKG討論區" if "lihkg" in sid + h or "LIHKG" in source
                   else "香港討論區" if "discuss" in sid + source + h else source)
        stype, region = "Forum", "HK"
    elif any(sid.startswith(f) for f in OFFICIAL):
        website, stype, region = source, "Official", "HK"
    elif sid.startswith("col_") or it.get("column"):  # the political columns: K-Matrix's Blog type
        website, stype, region = source, "Blog", "HK"
    elif sid.startswith("gnews"):
        website = source  # Google News: sources.fetch_source puts the publisher in source
        stype = "Official" if GOV_PUBLISHER.search(source) else "News"
        region = ("Mainland" if MAINLAND_PUBLISHER.search(source) else "Macau" if MACAU_PUBLISHER.search(source)
                  else "Taiwan" if TAIWAN_PUBLISHER.search(source) else "Global" if GLOBAL_PUBLISHER.search(source) else "HK")
    else:
        website = re.sub(r"\s*\((YouTube|Telegram)\)$", "", source)
        stype = "News"
        region = "Global" if sid in GLOBAL_SIDS else "Mainland" if MAINLAND_PUBLISHER.search(source) else "HK"
    return {"website": website, "platform": platform or website, "author": author, "board": section or None,
            "source_type": stype, "region": region}


def _t(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(HKT).isoformat(timespec="minutes")
    return str(v)


FUTURE = timedelta(hours=12)  # a publish time this far after the post was seen is not kept (next-day e-papers come ~5 h early)


def _plausible(published, seen):
    try:
        return datetime.fromisoformat(published) <= datetime.fromisoformat(seen) + FUTURE
    except (TypeError, ValueError):
        return False


def set_published(post_id, published, replace=False):
    """the publish time a page states (cache_fetch.page_date), for a post that has none, only a day (date_only), or
    (replace=True) a wrong one; the HKT day follows. A bare day "YYYY-MM-DD" (the page states no time) makes an
    undated post date-only (within_day) and leaves a dated one alone. A time more than FUTURE after the post was
    first seen is refused (HKET's displayDate can be an update time). → whether it changed. Scores, stream items and
    Telegram are left alone: a date doesn't change what was scored (scored.hash is the title and text)"""
    if not published:
        return False
    c = db()
    with c:
        if BARE_DATE.fullmatch(published):
            r = c.execute("SELECT seen FROM posts WHERE id=%s AND (published IS NULL OR %s)", (post_id, replace)).fetchone()
            if not r:
                return False
            t = within_day(published, r["seen"])
            r = c.execute("""UPDATE posts SET published=%s, day=%s, date_only=TRUE WHERE id=%s
                             AND (published IS DISTINCT FROM %s OR date_only IS NOT TRUE) RETURNING id""",
                          (t, published, post_id, t)).fetchone()
            return bool(r)
        seen = c.execute("SELECT seen FROM posts WHERE id=%s", (post_id,)).fetchone()
        if not seen or not _plausible(published, seen[0]):
            return False  # a time more than FUTURE after the post was first seen is an update time, not its publish time
        r = c.execute("""UPDATE posts SET published=%s, day=%s, date_only=NULL WHERE id=%s
                         AND (published IS NULL OR date_only OR %s)
                         AND (published IS DISTINCT FROM %s OR date_only) RETURNING id""",
                      (published, day_of(published, None), post_id, replace, published)).fetchone()
    return bool(r)


def sibling_published(post_id):
    """the earliest publish time of another post of the same article (articles: same URL after cleanup, same text, or
    a social post linking to it); a sibling that has only a day gives the bare day, and only when no sibling has a
    time; or None"""
    r = db().execute("""SELECT min(q.published) FILTER (WHERE q.date_only IS NOT TRUE) AS t,
                               min(left(q.published, 10)) FILTER (WHERE q.date_only) AS d
                        FROM posts p JOIN posts q ON q.article_id = p.article_id AND q.id <> p.id
                        WHERE p.id = %s AND p.article_id IS NOT NULL AND q.published IS NOT NULL""", (post_id,)).fetchone()
    return (r["t"] or r["d"]) if r else None


def day_of(published, seen):
    t = published or seen
    try:
        return datetime.fromisoformat(t).astimezone(HKT).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(HKT).strftime("%Y-%m-%d")


TEXT_MAX = 20000  # characters of a post's text kept in the corpus


def _fields(title, text, author, source, thread=None, ai_title=None, transcript=None, image_text=None):
    """(title part, rest) of the text a keyword query searches. A comment/reply row is searched by its own text and
    commenter only, not its thread's title (else every reply in a thread titled with a name would match it; K-Matrix:
    "3 related in 283"). A post's title from AI (titles.py) is part of its title; the transcripts of its video and
    audio (media.py) and the text read from its meaningful pictures (images.py) are part of the rest"""
    if thread:
        return "", "\n".join(x for x in (text or "", transcript or "", image_text or "", author or "") if x)
    return ("\n".join(x for x in (title or "", ai_title or "") if x),
            "\n".join(x for x in (text or "", transcript or "", image_text or "", author or "", source or "") if x))


def grams(hay):
    """the character pairs of a post's search text, and its last character alone (the posts.grams index;
    query.prefilter looks terms up by them: a one-character term c is in the text exactly when a lexeme starts with c,
    the tsquery prefix 'c':*)"""
    h = hay or ""
    return sorted({h[i:i + 2] for i in range(len(h) - 1)} | ({h[-1]} if h else set()))


SEARCH_SET = "hay=%s, stitle=%s, words=%s::tsvector, grams=array_to_tsvector(%s::text[]), search_v=%s"


def search_cols(title, text, author, source, thread=None, ai_title=None, transcript=None, image_text=None):
    """the search columns of a post, in SEARCH_SET's order: hay, stitle, words, grams, search_v (searchtext.py)"""
    t, rest = _fields(title, text, author, source, thread, ai_title, transcript, image_text)
    nt, nr = searchtext.norm(t), searchtext.norm(rest)
    hay = "\n".join(x for x in (nt, nr) if x)
    stitle = searchtext.norm(title if thread else "\n".join(x for x in (title or "", ai_title or "") if x))
    return hay, stitle, searchtext.tsvector(nt, nr), grams(hay), searchtext.VERSION


def _clean(s):
    """Postgres text can't hold NUL characters (SQLite could)"""
    return s.replace("\x00", "") if isinstance(s, str) else s


# Text as text (Deyao, 2026-10-21: "Apostrophe and quotation mark for English sources are wrong"). Some collectors
# hand over HTML-escaped text: X's API (S&amp;P), Bloomberg's and PR Newswire's double-escaped feeds (M&amp;amp;A),
# SCMP's photo pages (China&rsquo;s), the Macau Cultural Affairs Bureau (&ldquo;10-Minute Reading Corners&rdquo;), The
# Beat's &amp;#8217;; a few feeds (Blogspot) hand over the post's HTML itself. The site, Telegram and WhatsApp showed
# the entities as typed. plain() is applied to every title and text a post is stored with (ingest, set_content,
# set_text; titles.store for AI titles): HTML (POST_HTML_TAGS of its tags) becomes text with its paragraph breaks,
# and named or numeric entities ending in ";" are decoded until none is left (html.unescape on the whole string would
# also take "&not" in "&notes" as ¬). tools/fix_entities.py repaired the rows stored before.
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
_HTML_TAG = re.compile(
    r"</?(?:p|div|br|span|a|img|header|footer|section|article|h[1-6]|li|ul|ol|table|tbody|tr|td|th|strong|em|b|i|u|"
    r"figure|figcaption|iframe|blockquote|font|center|hr|wbr|small|sup|sub|q|cite|abbr|time|mark|del|ins|s|strike|nav|"
    r"main|aside|picture|source|video|audio|noscript|svg|path|g|meta|link)\b[^<>]*>", re.I)
POST_HTML_TAGS = 3  # tags in a text for it to be HTML (a forum post quoting one tag stays as typed)


def _html_text(s):
    """HTML → plain text, paragraph breaks kept (sources.paragraphs, without the entity pass: plain() does that)"""
    s = re.sub(r"<(script|style)\b.*?</\1>", "", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</(?:p|div|li|h\d|tr|blockquote|header|section|article|figure)>", "\n", s, flags=re.I)
    s = _HTML_TAG.sub("", s)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    return "\n".join(ln for ln in (re.sub(r"[ \t\u3000]+", " ", x).strip() for x in s.split("\n")) if ln)


def plain(s):
    """a post's title or text as text: HTML made text, HTML entities decoded (repeatedly: &amp;#8217; → ’)"""
    if not isinstance(s, str) or ("&" not in s and "<" not in s):
        return s
    markup = len(_HTML_TAG.findall(s)) >= POST_HTML_TAGS
    if markup:
        s = _html_text(s)
    for _ in range(4):
        t = _ENTITY.sub(lambda m: html.unescape(m.group(0)), s)
        if t == s:
            break
        s = t
    if markup:  # the &nbsp; paragraphs that were all the HTML had between its blocks
        s = "\n".join(ln for ln in (re.sub(r"[ \t\u3000\xa0]+", " ", x).strip() for x in s.split("\n")) if ln)
    return s


# A title never starts or ends with a URL (Deyao, 2026-10-15: an X post titled "https://t.co/…" was "wrong"). The
# collectors build social posts' titles from their text, so a post that opens with a link (an X Article's
# x.com/i/article link, a t.co, an outlet sharing its own article) got the link as its title, and a post that is only
# a link got nothing else. tidy_title drops the links at either end; a title that is only links becomes
# LINK_TITLE + the host until links.py has read what it links to (then the linked page's title). The link itself stays
# in the post's text: that is what links.py reads and what holds the post from scoring (links.HELD_SQL).
LINK_TITLE = "〔連結〕"
_EDGE_URLS = re.compile(r"^(?:\s*https?://\S+)+|(?:\s*https?://\S+)+\s*$")
_FIRST_URL = re.compile(r"https?://([^/\s?#]+)")
_WORDLESS = re.compile(r"^[\s　.,:;!?…。，：；！？、|/#@~<>_*\-—–]*$")


def tidy_title(title):
    """a post's title without the URLs at its start and end; only links → LINK_TITLE + the first link's host"""
    t = (title or "").strip()
    m = _FIRST_URL.search(t)
    if not m:
        return t
    rest = _EDGE_URLS.sub("", t).strip()
    rest = re.sub(r"[\s　:：|｜\-—–]+$", "", rest).strip()
    if rest and not _WORDLESS.match(rest):
        return rest
    return LINK_TITLE + m.group(1).lower()


# Posts with no title of their own (Deyao, 2026-10-20: 行政長官's Facebook post showed its first paragraph as its
# title, 7 lines on a phone): Facebook, Instagram, Threads, X, Telegram channels, TikTok captions… — the
# collectors put the start of the text in the title field. ingest() stores the full text as the text, the title as a
# short excerpt of its opening (the post's own words, no badge: titles.display shows it as it is) and
# title_from_body = TRUE. Stage-2 scoring writes such a post an AI title (kind "written"); a post that doesn't reach
# stage 2 shows the excerpt. Which posts: titles.TEXT_SIDS / tg_* / titles.TEXT_PLATFORMS, not a comment, not an X
# Article (titles.own_title). tools/fix_body_titles.py corrects the posts stored before.
HEAD_CJK = 40     # an excerpt's length: Han characters (a Chinese post) …
HEAD_LATIN = 90   # … or characters (any other)


def from_body(sid, platform, thread=None, title=None, text=None):
    """has this post no title of its own (its collector built the title from its text)?"""
    import titles
    if thread:
        return False
    sid = sid or ""
    if not (sid in titles.TEXT_SIDS or sid.startswith("tg_") or platform in titles.TEXT_PLATFORMS):
        return False
    return not titles.own_title({"platform": platform, "title": title, "text": text})


def excerpt(text):
    """the opening of a post's text as its title: the first line, else up to the first sentence end, cut at
    HEAD_CJK Han characters (HEAD_LATIN characters for a non-Chinese post) with …"""
    import titles
    t = _EDGE_URLS.sub("", (text or "").strip()).strip()
    line = next((ln.strip() for ln in t.splitlines() if ln.strip() and not _WORDLESS.match(ln)), "")
    m = re.search(r"[。！？!?](?=\s|$|[^」』”’)）])", line)
    first = line[:m.end()] if m and m.end() >= 6 else line
    first = re.sub(r"\s+", " ", first).strip()
    if titles.chinese(first):
        n = 0
        for k, ch in enumerate(first):
            n += bool(re.match(r"[㐀-鿿豈-﫿]", ch))
            if n > HEAD_CJK:
                return first[:k].rstrip(" ，,、：:；;") + "…"
        return first
    if len(first) <= HEAD_LATIN:
        return first
    cut = first[:HEAD_LATIN].rsplit(" ", 1)[0] if " " in first[:HEAD_LATIN] else first[:HEAD_LATIN]
    return cut.rstrip(" ,;:") + "…"


def body_title(title, text):
    """(title, text) for a post with no title of its own: the full text kept (the collected title when there is no
    text), the title its opening (excerpt)"""
    t = (title or "").strip()
    head = re.sub(r"\s+", "", t.rstrip("…"))[:20]
    if t and text and head not in re.sub(r"\s+", "", text):
        text = t + "\n" + text  # a collected title that is not the text's opening is part of the post: kept in it
    full = text or t or None
    return (excerpt(full) or t), full


SITE_TITLE_TYPES = ("News", "Newspaper", "Magazine", "Official", "Blog", "Forum")  # social posts' titles are their text: never a site name


INSERT_POST = """INSERT INTO posts (url,sid,source,section,website,board,author,platform,source_type,region,title,text,
                 published,seen,day,engagement,thread,hay,stitle,words,grams,search_v,first_run,date_only,canonical_url,
                 title_from_body)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::tsvector,array_to_tsvector(%s::text[]),%s,%s,%s,%s,%s)
                 ON CONFLICT (url) DO NOTHING RETURNING id"""


INGEST_CHUNK = 100  # posts per write transaction in ingest()
_EXISTING = ("SELECT id, url, title, text, author, source, engagement, thread, ai_title, transcript, image_text "
             "FROM posts WHERE url = ANY(%s)")


def ingest(items, seen=None):
    """add new posts (by URL); returns the ids of the posts that were new. A post seen again gets its engagement
    counts refreshed, and its text and author filled in when it had none.
    All the batch's work (classification, search columns) is done before any write, and the writes are short
    transactions of INGEST_CHUNK posts: one transaction for a whole poll held refreshed rows locked for minutes on
    Neon (2026-10-16), stalling set_transcript, set_text and other ingests behind it."""
    # always HKT to the second ("2026-10-27T13:09:47+08:00"): seen_cond compares the text with the posts_seen index
    seen = (datetime.fromisoformat(seen).astimezone(HKT) if seen else datetime.now(HKT)).isoformat(timespec="seconds")
    new, filled, attach = [], [], []
    c = db()
    sids = sorted({it["sid"] for it in items if it.get("sid")})
    known = {r[0] for r in c.execute("SELECT s FROM unnest(%s::text[]) s WHERE EXISTS (SELECT 1 FROM posts WHERE sid = s)",
                                     (sids,))} if sids else set()
    # a source read again after COLLECT_GAP without a read (or with none on record): this read's new posts are backlog
    restarted = set()
    if sids:
        last = dict(c.execute("SELECT sid, last_read FROM sid_reads WHERE sid = ANY(%s)", (sids,)).fetchall())
        missing = [sd for sd in known if sd not in last]  # no read on record: its newest post's seen time stands in
        if missing:
            last.update(c.execute("SELECT sid, max(seen::timestamptz) FROM posts WHERE sid = ANY(%s) GROUP BY sid",
                                  (missing,)).fetchall())
        edge = datetime.now(HKT) - COLLECT_GAP
        restarted = {sd for sd in known if sd in last and last[sd] < edge}
        c.execute("""INSERT INTO sid_reads (sid, last_read) SELECT unnest(%s::text[]), now()
                     ON CONFLICT (sid) DO UPDATE SET last_read = excluded.last_read""", (sids,))
    urls = list({it["url"] for it in items if it.get("url")})
    have = {r["url"]: dict(r) for r in c.execute(_EXISTING, (urls,))} if urls else {}
    inserts, updates = [], []  # (row params, it, local row) / (url, eng, text, author)
    planned = {}  # url → the row this batch inserts (a later duplicate in the batch refreshes it)
    for it in items:
        url = it.get("url")
        if not url or not it.get("title"):
            continue
        title = tidy_title(plain(it["title"]))
        cl = classify(it)
        if it.get("board"):  # a comment keeps its thread's board (a Facebook page's comment isn't the commenter's)
            cl["board"] = it["board"]
        raw = it.get("published")
        donly = isinstance(raw, DateOnly)
        pub = within_day(raw, it.get("seen") or seen) if donly else _t(raw)
        if pub and not _plausible(pub, it.get("seen") or seen):
            pub, donly = None, False  # a time after the post was seen is a misread (熱血時報's MM-DD read as DD-MM): undated
        text = (plain(it.get("text") or "").strip()[:TEXT_MAX] or None)
        if title != plain(it["title"]).strip() and not text:
            text = plain(it["title"]).strip()  # the links taken out of the title stay in the text
        if cl["source_type"] in SITE_TITLE_TYPES and not it.get("thread") and site_named(
                title, it.get("source"), cl["website"], url=url):
            # the site's name, not the post's title (Google News's 「澳門日報電子版」, 「華僑報」): no title yet; the link
            # in the text holds it from scoring until links.py reads the page's own headline
            title, text = LINK_TITLE + _host(url), text or url
        fb = (not title.startswith(LINK_TITLE)
              and from_body(it.get("sid"), cl["platform"], it.get("thread"), title, text))
        if fb:
            title, text = body_title(title, text)
        eng = json.dumps(it["engagement"]) if it.get("engagement") else None
        row = tuple(_clean(v) for v in (
            url, it.get("sid"), it.get("source"), it.get("section"), cl["website"], cl["board"], cl["author"],
            cl["platform"], cl["source_type"], cl["region"], title, text, pub, it.get("seen") or seen,
            day_of(pub, it.get("seen") or seen), eng, it.get("thread")))
        # a source's first read, its first read after a gap (restarted), or a page/account's first read in a shared
        # sid (it["first_run"]: the collector marks the posts published before it started watching that account): a
        # backlog, not new posts
        first = bool(it.get("sid")) and not it.get("followed") and (  # followed: one linked page, never a backlog
            (not it.get("thread") and ((MARK_BACKLOG and it["sid"] not in known) or bool(it.get("first_run"))))
            # the first read after a gap: its replies too (a forum's replies written meanwhile)
            or (MARK_BACKLOG and it["sid"] in restarted))
        if url not in have and url not in planned:
            # search columns (jieba) only for a post to insert: a poll re-fetches thousands of known items
            params = row + search_cols(row[10], text, row[6], row[2], row[16]) + (first, donly or None,
                                                                                  canonical.rehost(url), fb or None)
            planned[url] = {"title": row[10], "text": text, "author": row[6], "source": row[2], "engagement": eng,
                            "thread": row[16], "ai_title": None, "transcript": None, "image_text": None}
            inserts.append((params, it))
            continue
        if eng or text or cl["author"] or not row[10].startswith(LINK_TITLE):
            updates.append((url, it.get("engagement") if eng else None, row[11], row[6], row[10]))
    for k in range(0, len(inserts), INGEST_CHUNK):
        with c:
            for params, it in inserts[k:k + INGEST_CHUNK]:
                got = c.execute(INSERT_POST, params).fetchone()
                if got:
                    new.append(got[0])
                    planned[params[0]]["id"] = got[0]
                    if it.get("images") or it.get("clips"):
                        attach.append((got[0], it))
                else:
                    planned.pop(params[0], None)  # another writer inserted it meanwhile: refreshed below
    for url, r in planned.items():
        if "id" in r:
            have[url] = dict(r, url=url)
    late = [u for u, *_ in updates if u not in have]
    if late:  # inserted by another writer while this batch ran
        have.update({r["url"]: dict(r) for r in c.execute(_EXISTING, (late,))})
    writes = []  # (sql, params, the expected values, filled post id or None)
    for url, eng, text, author, title in updates:
        r = have.get(url)
        if not r:
            continue
        if (r["title"] or "").startswith(LINK_TITLE) and title and not title.startswith(LINK_TITLE):
            # a post stored before its headline was known (follow.py collected a link whose page no reader in the
            # poller could open, 〔連結〕host) takes the headline a listing of its source gives
            writes.append((f"UPDATE posts SET title=%s, {SEARCH_SET} WHERE id=%s AND title = %s",
                           (title,) + search_cols(title, r["text"], r["author"], r["source"], r["thread"], r["ai_title"],
                                                  r["transcript"], r["image_text"]) + (r["id"], r["title"]), None))
            r["title"] = title
        if eng:  # refresh the counts of a re-seen post (keeping counts only the cached page gave)
            merged = json.dumps(dict(json.loads(r["engagement"]) if r["engagement"] else {}, **eng))
            if merged != r["engagement"]:
                writes.append(("UPDATE posts SET engagement=%s WHERE id=%s AND engagement IS NOT DISTINCT FROM %s",
                               (merged, r["id"], r["engagement"]), None))
                r["engagement"] = merged
        if (text and not r["text"]) or (author and not r["author"]):
            t2, a2 = r["text"] or text, r["author"] or author
            writes.append((f"UPDATE posts SET text=%s, author=%s, {SEARCH_SET} WHERE id=%s "
                           "AND text IS NOT DISTINCT FROM %s AND author IS NOT DISTINCT FROM %s",
                           (t2, a2) + search_cols(r["title"], t2, a2, r["source"], r["thread"], r["ai_title"], r["transcript"],
                                                  r["image_text"]) + (r["id"], r["text"], r["author"]),
                           r["id"] if t2 != r["text"] else None))
            r["text"], r["author"] = t2, a2
    # each write only applies when the row still holds what was read (another writer, e.g. a page read, may have
    # changed it meanwhile: then it keeps that writer's values)
    for k in range(0, len(writes), INGEST_CHUNK):
        with c:
            for sql, params, fid in writes[k:k + INGEST_CHUNK]:
                if c.execute(sql, params).rowcount and fid:
                    filled.append(fid)
    assign_articles(new + filled)  # after the posts are committed: a short transaction of its own
    for pid, it in attach:
        attach_media(pid, it.get("images"), it.get("clips"))
    return new


def attach_media(post_id, image_urls=None, clips=None):
    """a post's own photos and videos as its platform gives them (X): the photos to images.py, the videos to the
    transcription queue (media.py), with no page read (never raises: the post is filed either way)"""
    try:
        if image_urls:
            import images
            images.link(post_id, image_urls)
        if clips:
            import media
            media.file_clips(post_id, clips)
    except Exception as e:  # noqa: BLE001
        print("attach_media:", post_id, repr(e)[:200], flush=True)


def set_content(post_id, title, text, engagement=None):
    """replace a post's collected title and text (a link resolved, an X post read again: links.py) and refresh its
    engagement counts; the search columns follow and the scorer sees the change (streams.HASH) → changed?"""
    text = (plain(text) or "").strip()[:TEXT_MAX] or None
    c = db()
    with c:
        r = c.execute("SELECT title, text, author, source, thread, ai_title, transcript, image_text, engagement FROM posts "
                      "WHERE id=%s FOR UPDATE", (post_id,)).fetchone()
        if not r:
            return False
        title = tidy_title(plain(_clean(title)) or r["title"])
        text = _clean(text)
        eng = r["engagement"]
        if engagement:
            eng = json.dumps(dict(json.loads(r["engagement"]) if r["engagement"] else {}, **engagement))
        if (title, text, eng) == (r["title"], r["text"], r["engagement"]):
            return False
        c.execute(f"UPDATE posts SET title=%s, text=%s, engagement=%s, {SEARCH_SET} WHERE id=%s",
                  (title, text, eng) + search_cols(title, text, r["author"], r["source"], r["thread"], r["ai_title"],
                                                   r["transcript"], r["image_text"]) + (post_id,))
    if (title, text) != (r["title"], r["text"]):
        assign_articles([post_id])
    return True


def add_comments(thread, comments, total=None, seen=None):
    """file a thread's comments/replies as rows of their own. thread: the parent item as the collectors give it
    (url, title, sid, source, section; ingested first if new). comments: [{id, author, text, published (ISO/datetime),
    likes, dislikes (optional)}]; the thread's own opening post is not a comment. total: the thread's comment count as the
    platform states it (stored as engagement.comments of the thread, for "N related in M post & comments").
    → ids of the comment rows that were new"""
    ingest([thread], seen)
    if total is not None:
        c = db()
        with c:
            r = c.execute("SELECT id, engagement FROM posts WHERE url=%s FOR UPDATE", (thread["url"],)).fetchone()
            if r:
                eng = dict(json.loads(r["engagement"]) if r["engagement"] else {}, comments=int(total))
                c.execute("UPDATE posts SET engagement=%s WHERE id=%s", (json.dumps(eng), r["id"]))
    base = re.sub(r"#.*$", "", thread["url"])
    board = classify(thread)["board"]
    # the comments carry their post's title as stored: for a post with no title of its own its excerpt (body_title),
    # never the post's first paragraph
    got = db().execute("SELECT id, title FROM posts WHERE url=%s", (thread["url"],)).fetchone()
    ttitle = got["title"] if got and got["title"] else thread["title"]
    rows = []
    for cm in comments:
        text = re.sub(r"\s+", " ", str(cm.get("text") or "")).strip()
        if not text or not cm.get("id"):
            continue
        rows.append({"url": f"{base}#c{cm['id']}", "title": ttitle, "text": text, "sid": thread.get("sid"),
                     "source": thread.get("source"), "section": thread.get("section"), "author": cm.get("author"),
                     "published": cm.get("published"), "thread": base, "board": board,
                     "engagement": {k: cm[k] for k in ("likes", "dislikes") if isinstance(cm.get(k), int)} or None})
    fresh = ingest(rows, seen)
    if fresh and got:  # the live dashboard's Comments panel (events.py): how many came in, and the most liked one
        import events
        tops = sorted(rows, key=lambda x: -(x["engagement"] or {}).get("likes", -1))[:3]  # stable: the first read on ties
        events.emit("comments", got["id"], new=len(fresh), read=len(rows), total=total,
                    top=[{"text": x["text"][:240], "author": x["author"], "likes": (x["engagement"] or {}).get("likes")}
                         for x in tops])
    return fresh


def reclassify():
    """re-run classify() on every stored post (after a change to it); → number of rows changed"""
    c = db()
    changed = 0
    with c:
        for r in c.execute("SELECT * FROM posts").fetchall():
            cl = classify({"sid": r["sid"], "source": r["source"], "section": r["section"] or r["board"],
                           "author": r["author"]})
            new = (cl["website"], cl["platform"], cl["board"], cl["author"], cl["source_type"], cl["region"])
            if new != (r["website"], r["platform"], r["board"], r["author"], r["source_type"], r["region"]):
                c.execute(f"UPDATE posts SET website=%s, platform=%s, board=%s, author=%s, source_type=%s, region=%s, {SEARCH_SET} WHERE id=%s",
                          new + search_cols(r["title"], r["text"], cl["author"], r["source"], r["thread"], r["ai_title"], r["transcript"], r["image_text"]) + (r["id"],))
                changed += 1
    return changed


def set_text(post_id, text, author=None, engagement=None, via=None, via_url=None):
    """the cached copy of a post (cache_fetch.py): its text ("" = the page was tried and gave nothing), plus the author
    when the post had none and engagement counts when the page gives them; via: where the text came from
    (paywall.VIA; kept as it was when None), via_url: the copy's own URL (an archive.today snapshot)"""
    text = plain(_clean(text))
    c = db()
    with c:
        r = c.execute("SELECT title, author, source, engagement, thread, ai_title, transcript, image_text FROM posts WHERE id=%s FOR UPDATE",
                      (post_id,)).fetchone()
        a = r["author"] or _clean(author)
        eng = dict(json.loads(r["engagement"]) if r["engagement"] else {}, **(engagement or {}))
        c.execute(f"UPDATE posts SET text=%s, author=%s, engagement=%s, {SEARCH_SET}, cached_at=%s, "
                  "text_via=coalesce(%s::text, text_via), text_via_url=CASE WHEN %s::text IS NULL THEN text_via_url ELSE %s::text END WHERE id=%s",
                  (text, a, json.dumps(eng) if eng else None) + search_cols(r["title"], text, a, r["source"], r["thread"], r["ai_title"], r["transcript"], r["image_text"])
                  + (datetime.now(HKT).isoformat(timespec="seconds"), via, via, via_url, post_id))
    assign_articles([post_id])  # the full text can join it to an article


def set_image_text(post_id, image_text):
    """the text read from the post's meaningful pictures (images.py; None = none): stored, and searched with its text.
    The scorer sees the change (streams.HASH covers it) and scores the post again."""
    image_text = _clean(image_text) or None
    c = db()
    with c:
        r = c.execute("SELECT title, text, author, source, thread, ai_title, transcript, image_text FROM posts "
                      "WHERE id=%s FOR UPDATE", (post_id,)).fetchone()
        if not r or r["image_text"] == image_text:
            return False
        c.execute(f"UPDATE posts SET image_text=%s, {SEARCH_SET} WHERE id=%s",
                  (image_text,) + search_cols(r["title"], r["text"], r["author"], r["source"], r["thread"], r["ai_title"],
                                              r["transcript"], image_text) + (post_id,))
    return True


def set_transcript(post_id, transcript):
    """the post's video/audio transcripts (media.py; None = none yet): stored, and searched with its text. The
    scorer sees the change (streams.HASH covers it) and scores the post again."""
    transcript = plain(_clean(transcript)) or None
    c = db()
    with c:
        r = c.execute("SELECT title, text, author, source, thread, ai_title, transcript, image_text FROM posts WHERE id=%s FOR UPDATE",
                      (post_id,)).fetchone()
        if not r or r["transcript"] == transcript:
            return False
        c.execute(f"UPDATE posts SET transcript=%s, {SEARCH_SET} WHERE id=%s",
                  (transcript,)
                  + search_cols(r["title"], r["text"], r["author"], r["source"], r["thread"], r["ai_title"], transcript,
                                r["image_text"])
                  + (post_id,))
    return True



# ---- articles (Deyao, 2026-10-14): posts that are the same content. Comments and replies are never grouped. ----

# tr_h: uwants' per-read tracking hash on its home page's thread links (2026-10-21: a new value on every read, so one
# thread was stored ~20 times)
TRACKING = re.compile(r"^(utm_\w*|fbclid|gclid|dclid|msclkid|igshid|igsh|mc_cid|mc_eid|ref_src|spm|ocid|cmpid|_ga|"
                      r"__twitter_impression|tr_h|mtc)$", re.I)  # mtc: HKET's (?mtc=20023)


def strip_tracking(url):
    """url without its tracking parameters (TRACKING), everything else as it was; the address a post is stored under
    (Deyao, 2026-10-21: uwants' utm_*/tr_h links)"""
    import urllib.parse as up
    if not url or "?" not in url:
        return url
    try:
        u = up.urlsplit(url)
    except ValueError:
        return url
    parts = [x for x in u.query.split("&") if x and not TRACKING.match(up.unquote_plus(x.split("=", 1)[0]))]
    q = "&".join(parts)
    return url if q == u.query else up.urlunsplit((u.scheme, u.netloc, u.path, q, u.fragment))
SOCIAL_HOSTS = re.compile(r"(^|\.)(facebook\.com|fb\.com|fb\.me|fb\.watch|instagram\.com|threads\.net|threads\.com|t\.me|telegram\.me|"
                          r"youtube\.com|youtu\.be|linkedin\.com|lnkd\.in|x\.com|twitter\.com|t\.co|tiktok\.com|"
                          r"linktr\.ee|wa\.me|whatsapp\.com)$", re.I)
TEXT_KEY_MIN = 200  # characters of letters/digits: shorter texts (captions, one-liners) are too generic to match on


# sites whose query string never names the story (SCMP: ?module=…&pgtype=… say where on its pages the link sat)
PATH_ONLY_HOSTS = {"scmp.com", "youngpostclub.com", "straitstimes.com"}  # ST: ?ai-allowed=1 (sitemap), ?ref=inline-article
# the same story under another site: SCMP's sitemaps list Young Post's stories on scmp.com, Young Post serves them
SAME_STORY = ((re.compile(r"^scmp\.com(/(?:yp|spark|posties)/)"), r"youngpostclub.com\1"),)


def url_key(url):
    """the URL after cleanup: no scheme, www./m./amp. or fragment, no tracking parameters, the rest sorted, no trailing
    /; PATH_ONLY_HOSTS without their query; SAME_STORY's copies under the site that serves them"""
    import urllib.parse as up
    try:
        u = up.urlsplit((url or "").strip())
    except ValueError:
        return None
    if not u.netloc:
        return None
    host = re.sub(r"^(www|m|amp)\.", "", u.netloc.lower().split("@")[-1].split(":")[0])
    q = sorted((k, v) for k, v in up.parse_qsl(u.query, keep_blank_values=True) if not TRACKING.match(k))
    if host in PATH_ONLY_HOSTS:
        q = []
    path = re.sub(r"/+$", "", u.path) or "/"
    for pat, to in SAME_STORY:
        hp = pat.sub(to, host + path)
        if hp != host + path:
            host, path = hp.split("/", 1)[0], "/" + hp.split("/", 1)[1]
    key = host + path + ("?" + up.urlencode(q) if q else "")
    for pat, to in STORY_KEYS:
        if pat.match(key):
            return pat.sub(to, key)
    return key


# one story under several addresses on its own site (2026-10-21: headline4hk's 原文 links name these forms, and our
# feeds the others): the story's id is the key. 星島 /<section>/<id> with or without its title slug, NOW's /mobile/ and
# /home/ players, 有線 ?p=<id> and /<category>/<id>/<slug>, HKET /article/<id> with or without its title (encoded or not)
STORY_KEYS = (
    (re.compile(r"^(stheadline\.com/[^/?]+/\d{5,})(?:/.*)?$"), r"\1"),
    (re.compile(r"^news\.now\.com/(?:home|mobile)/([^/?]+)/player\?newsId=(\d+)$"), r"news.now.com/home/\1/player?newsId=\2"),
    (re.compile(r"^i-cable\.com/(?:[^/?]+/)?(\d{5,})(?:/[^?]*)?$"), r"i-cable.com/p/\1"),
    (re.compile(r"^i-cable\.com/\?p=(\d+)$"), r"i-cable.com/p/\1"),
    (re.compile(r"^([a-z]+\.hket\.com/article/\d+)(?:/.*)?$"), r"\1"),
)


def rekey(host_re, since):
    """posts seen since `since` on hosts matching host_re (SQL regex on the URL's host) get url_key again (after a
    STORY_KEYS / TRACKING change) and their articles reassigned → posts whose key changed"""
    rows = db().execute("""SELECT id, url, url_key FROM posts WHERE seen::timestamptz >= %s::timestamptz
                           AND substring(url from '://([^/:?#]+)') ~ %s""", (since, host_re)).fetchall()
    changed = [(url_key(r["url"]), r["id"]) for r in rows if url_key(r["url"]) != r["url_key"]]
    for k in range(0, len(changed), 500):
        db().executemany("UPDATE posts SET url_key=%s WHERE id=%s", changed[k:k + 500])
    ids = sorted(i for _, i in changed)
    for k in range(0, len(ids), 200):
        assign_articles(ids[k:k + 200])
    return len(ids)


def text_key(text):
    """a hash of the full text's letters and digits; None when it is too short to tell content apart"""
    t = re.sub(r"[\W_]+", "", (text or "").lower())
    return hashlib.sha256(t.encode()).hexdigest()[:32] if len(t) >= TEXT_KEY_MIN else None


def link_key(title, text):
    """the url_key of the first outside link in a social post (Facebook's l.facebook.com wrapper unwrapped)"""
    import urllib.parse as up
    for m in re.finditer(r"https?://[^\s<>\"'）)」』，。、]+", f"{text or ''} {title or ''}"):
        link = m.group(0).rstrip(".,;:!?")
        u = up.urlsplit(link)
        if u.netloc.lower().endswith("facebook.com") and u.path == "/l.php":
            link = dict(up.parse_qsl(u.query)).get("u") or link
            u = up.urlsplit(link)
        if u.netloc and not SOCIAL_HOSTS.search(u.netloc.lower().split(":")[0]):
            return url_key(link)
    return None


def _keys(r):
    """(url_key, text_key, link_key) of a posts row; comments and replies (thread set) get none"""
    if r["thread"]:
        return None, None, None
    lk = link_key(r["title"], r["text"]) if r["source_type"] == "Social Media" else None
    return url_key(r["url"]), text_key(r["text"]), lk


def _same_by_text(a, b):
    """two posts with the same text are one article, except the same site under different headlines: that is the
    site's boilerplate (a cookie wall, a login page) read as the text, not one article"""
    return a["website"] != b["website"] or (a["title"] or "") == (b["title"] or "")


def clean_tracking_urls():
    """posts stored under an address with tracking parameters move to it without them (strip_tracking; Deyao,
    2026-10-21), so the poll (which strips them) finds them again; copies of one post under several such addresses
    (uwants' tr_h) are merged into the first stored one (the others deleted, their scores and rows with them); their
    stream_items and two_pass rows follow; poll_seen learns the clean addresses. → {"renamed", "merged"}"""
    c = db()
    # every row with a query string whose parameter names match TRACKING (filtered exactly below)
    rows = c.execute(r"SELECT id, url FROM posts WHERE url ~ '[?&](utm_|fbclid|gclid|dclid|msclkid|igsh|mc_[ce]id|ref_src|"
                     r"spm|ocid|cmpid|_ga|__twitter|tr_h)'").fetchall()
    groups = {}
    for r in rows:
        u = strip_tracking(r["url"])
        if u != r["url"]:
            groups.setdefault(u, []).append(r)
    renamed = merged = 0
    for clean, rs in groups.items():
        def move(c, clean=clean, rs=rs):
            n = {"renamed": 0, "merged": 0, "keep": None}
            have = c.execute("SELECT id FROM posts WHERE url=%s", (clean,)).fetchone()
            rs = sorted(rs, key=lambda r: r["id"])
            keep = have["id"] if have else rs[0]["id"]
            drop = [r["id"] for r in rs if r["id"] != keep]
            if drop:
                c.execute("UPDATE follow_links SET target_id=%s WHERE target_id = ANY(%s)", (keep, drop))
                c.execute("DELETE FROM posts WHERE id = ANY(%s)", (drop,))
                n["merged"] += len(drop)
            if not have:
                c.execute("UPDATE posts SET url=%s WHERE id=%s", (clean, keep))
                n["renamed"] += 1
            for r in rs:
                for t in ("stream_items", "two_pass"):
                    try:
                        with c.savepoint():
                            c.execute(f"UPDATE {t} SET url=%s WHERE url=%s", (clean, r["url"]))
                    except psycopg.errors.UniqueViolation:  # the clean address has its row already: the copy's goes
                        c.execute(f"DELETE FROM {t} WHERE url=%s", (r["url"],))
            c.execute("INSERT INTO poll_seen (url, at) SELECT %s, min(at) FROM poll_seen WHERE url = ANY(%s) "
                      "HAVING min(at) IS NOT NULL ON CONFLICT (url) DO NOTHING", (clean, [r["url"] for r in rs]))
            n["keep"] = keep
            return n
        n = c.run(move)
        renamed, merged, keep = renamed + n["renamed"], merged + n["merged"], n["keep"]
        assign_articles([keep])
    return {"renamed": renamed, "merged": merged}


def assign_articles(ids):
    """set the keys and article_id of these posts: each joins the article of any earlier post it matches (the lowest
    article id wins; matches in two articles merge them). Indexed lookups only: it runs on the alert path."""
    ids = list(ids)
    if not ids:
        return
    c = db()
    with c:
        rows = c.execute("SELECT id, url, title, text, thread, website, source_type, article_id FROM posts WHERE id = ANY(%s) ORDER BY id",
                         (ids,)).fetchall()
        for r in rows:
            uk, tk, lk = _keys(r)
            arts = {r["id"]}
            if uk or tk or lk:
                for m in c.execute("""SELECT id, article_id, url_key, text_key, link_key, website, title FROM posts
                                      WHERE id <> %s AND (url_key = %s OR link_key = %s OR text_key = %s OR url_key = %s)""",
                                   (r["id"], uk, uk, tk, lk)).fetchall():
                    by_text_only = not ((uk and uk in (m["url_key"], m["link_key"])) or (lk and lk == m["url_key"]))
                    if by_text_only and not _same_by_text(r, m):
                        continue
                    arts.add(m["article_id"] or m["id"])
            art = min(arts)
            # only when something changed: an unchanged row rewritten is a new row version all the same (each one
            # re-adding the post's character pairs to posts_grams when it lands on another page)
            c.execute("""UPDATE posts SET url_key=%s, text_key=%s, link_key=%s, article_id=%s WHERE id=%s
                         AND (url_key, text_key, link_key, article_id) IS DISTINCT FROM (%s, %s, %s, %s)""",
                      (uk, tk, lk, art, r["id"], uk, tk, lk, art))
            others = sorted(arts - {art})
            if others:
                c.execute("UPDATE posts SET article_id=%s WHERE article_id = ANY(%s)", (art, others))


def backfill_articles(batch=5000):
    """keys and article_id for every post, from scratch (corpus.py articles): the same rules as assign_articles, run as
    one union-find in memory → posts whose article_id changed"""
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    keys, info, last = {}, {}, 0
    while True:
        rows = db().execute("""SELECT id, url, title, text, thread, website, source_type FROM posts WHERE id > %s
                               ORDER BY id LIMIT %s""", (last, batch)).fetchall()
        if not rows:
            break
        for r in rows:
            keys[r["id"]] = _keys(r)
            info[r["id"]] = {"website": r["website"], "title": r["title"]}
        last = rows[-1]["id"]
    by_url, by_text = {}, {}
    for i, (uk, tk, lk) in keys.items():
        if uk:
            by_url.setdefault(uk, []).append(i)
        if tk:
            by_text.setdefault(tk, []).append(i)
    for ids in by_url.values():
        for i in ids[1:]:
            union(ids[0], i)
    for i, (uk, tk, lk) in keys.items():
        if lk and lk in by_url:
            union(i, by_url[lk][0])
    for ids in by_text.values():
        for n, a in enumerate(ids):
            for b in ids[n + 1:]:
                if _same_by_text(info[a], info[b]):
                    union(a, b)
    rows = [(*keys[i], find(i), i) for i in sorted(keys)]
    changed = 0
    for n in range(0, len(rows), batch):
        part = rows[n:n + batch]
        db().executemany("UPDATE posts SET url_key=%s, text_key=%s, link_key=%s, article_id=%s WHERE id=%s", part)
        changed += len(part)
    return changed


def set_tags(profile, rows):
    """rows: [(post_id, relevant, sentiment, labels)]. A row a person corrected (tags.corrected) is left as it is, for
    every writer (the stream tagging, the profiles' tagging, the archive backfill)"""
    now = datetime.now(HKT).isoformat(timespec="seconds")
    db().executemany("""INSERT INTO tags (profile,post_id,relevant,sentiment,labels,tagged_at) VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (profile,post_id) DO UPDATE SET relevant=excluded.relevant,
                        sentiment=excluded.sentiment, labels=excluded.labels, tagged_at=excluded.tagged_at
                        WHERE tags.corrected IS NOT TRUE""",
                     [(profile, pid, int(bool(rel)), sent, json.dumps(labels or []), now) for pid, rel, sent, labels in rows])


def set_themes(profile, pairs):
    db().executemany("UPDATE tags SET theme=%s WHERE profile=%s AND post_id=%s AND corrected IS NOT TRUE",
                     [(t, profile, pid) for pid, t in pairs])


def ids_for_urls(urls):
    rows = db().execute("SELECT id, url FROM posts WHERE url = ANY(%s)", (list(urls),))
    return {r["url"]: r["id"] for r in rows}


def match_ids(q, start, end, filters=None, limit=None):
    """ids of posts whose HKT day is in [start, end] matching parsed query q (query.parse) and the profile's filters
    (regions, source_types, websites, exclude_websites; comments_only: only comment/reply rows): the posts it matches
    by words first, then those it matches only as substrings, each newest first"""
    where, params = query.to_sql(q)
    sql = f"SELECT p.id FROM posts p WHERE p.day BETWEEN %s AND %s AND ({where})"
    args = [start, end] + params
    f = filters or {}
    for key, col in (("regions", "region"), ("source_types", "source_type"), ("websites", "website")):
        if f.get(key):
            sql += f" AND p.{col} = ANY(%s)"
            args.append(list(f[key]))
    if f.get("comments_only"):
        sql += " AND p.thread IS NOT NULL"
    if f.get("exclude_websites"):  # NOT IN: a post without a website is left out too (SQL's NULL rule, as before)
        sql += " AND p.website <> ALL(%s)"
        args.append(list(f["exclude_websites"]))
    rank, rargs = query.rank_sql(q)  # word matches first, then substring-only matches; newest first in each
    sql += f" ORDER BY {rank} DESC, coalesce(p.published, p.seen) DESC, p.id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r[0] for r in db().execute(sql, args + rargs)]


def word_matched(q, ids):
    """the ids among `ids` that parsed query q matches by words (query.rank_sql), the rest matched only as substrings"""
    if not ids:
        return set()
    rank, rargs = query.rank_sql(q)
    return {r[0] for r in db().execute(f"SELECT p.id FROM posts p WHERE p.id = ANY(%s) AND {rank} = 1", [list(ids)] + rargs)}


def posts(ids, profile=None):
    """full rows (with the profile's tags when given; sentiment and labels from the scorer when the profile has no row
    yet), newest first"""
    import titles
    ids = list(ids)
    if not ids:
        return []
    if profile:
        rows = db().execute("""SELECT p.*, t.relevant, coalesce(t.sentiment, sc.sentiment) AS sentiment,
                               coalesce(t.labels, sc.labels) AS labels, t.theme FROM posts p
                               LEFT JOIN tags t ON t.post_id=p.id AND t.profile=%s
                               LEFT JOIN scored sc ON sc.post_id=p.id
                               WHERE p.id = ANY(%s) ORDER BY p.id""", (profile, ids))
    else:  # no profile: the tags of any profile that tagged the post (the most recent)
        rows = db().execute("""SELECT p.*, t.relevant, t.sentiment, t.labels, t.theme FROM posts p
                               LEFT JOIN LATERAL (SELECT * FROM tags WHERE post_id=p.id
                                                  ORDER BY tagged_at DESC, profile LIMIT 1) t ON TRUE
                               WHERE p.id = ANY(%s) ORDER BY p.id""", (ids,))
    out = []
    for r in rows:
        d = dict(r)
        for k in ("hay", "stitle", "words", "grams", "search_v", "transcript", "image_text"):  # search; text: item page
            d.pop(k, None)
        d["labels"] = json.loads(d["labels"]) if d.get("labels") else []
        d["engagement"] = json.loads(d["engagement"]) if d.get("engagement") else {}
        out.append(titles.display(d))  # a title from AI (titles.py) is the title shown
    out.sort(key=lambda d: d.get("published") or d.get("seen") or "", reverse=True)
    return out


def member_ids(stream, threshold, start, end):
    """ids of the posts with an HKT day in [start, end] that are members of `stream`: the scorer (streams.py) gave them
    at least `threshold`, or they were filed in it (stream_items: filed ⇒ member, Deyao 2026-10-15). A stream
    profile's posts (insights.members)"""
    return [r[0] for r in db().execute("""SELECT s.post_id FROM scores s JOIN posts p ON p.id=s.post_id
                                          WHERE s.stream=%s AND s.score >= %s AND p.day BETWEEN %s AND %s
                                          UNION SELECT p.id FROM stream_items si JOIN posts p ON p.url=si.url
                                          WHERE si.stream=%s AND p.day BETWEEN %s AND %s ORDER BY 1""",
                                       (stream, threshold, start, end, stream, start, end))]


def reindex(batch=500, pause=0.3, log=False):
    """(re)write the search columns of every row indexed with another searchtext.VERSION (or never: the history before
    2026-10-14, rows from migrate_to_postgres.py); → rows done. Resumable and safe beside the live pipeline and other
    runs: short transactions of `batch` rows in id order, rows another writer holds skipped (SKIP LOCKED, taken by the
    next pass), `pause` seconds between batches so the poller and the web keep their share of the database and CPU.
    Searches stay right meanwhile: query.py also tests the old (unconverted) form of each term. The poller runs it at
    every start; also `corpus.py reindex`."""
    done, last, v = 0, 0, searchtext.VERSION
    searchtext.tokenizer()
    while True:
        def batch_tx(c):
            rows = c.execute("""SELECT id, title, text, author, source, thread, ai_title, transcript, image_text FROM posts
                                WHERE id > %s AND search_v IS DISTINCT FROM %s ORDER BY id LIMIT %s
                                FOR UPDATE SKIP LOCKED""", (last, v, batch)).fetchall()
            if rows:
                c.executemany(f"UPDATE posts SET {SEARCH_SET} WHERE id=%s",
                              [search_cols(r["title"], r["text"], r["author"], r["source"], r["thread"], r["ai_title"], r["transcript"], r["image_text"]) + (r["id"],)
                               for r in rows])
            return rows
        rows = db().run(batch_tx)
        if not rows:
            return done
        done += len(rows)
        last = rows[-1]["id"]
        if log:
            print(f"reindex: {done} rows, at id {last}", flush=True)
        time.sleep(pause)


def fill_subs(batch=5000, pause=0.5, log=False):
    """post_subs for every post that has no row there yet (the triggers write the new and changed ones); → rows done.
    Inserts only: no posts row is rewritten. Batches of `batch` posts in id order, each its own transaction, `pause`
    seconds between them; resumable (a post that has its row is skipped before its text is read). At the end it sets kv
    search:subs, from which query.py and the Worker look Latin terms up in post_subs. `corpus.py fill-subs`."""
    done, last = 0, 0
    while True:
        r = db().execute("""WITH b AS (SELECT id FROM posts WHERE id > %s ORDER BY id LIMIT %s),
                              ins AS (INSERT INTO post_subs (post_id, subs)
                                      SELECT p.id, subs_of(p.hay) FROM b JOIN posts p ON p.id = b.id
                                      WHERE NOT EXISTS (SELECT 1 FROM post_subs s WHERE s.post_id = b.id)
                                      ON CONFLICT (post_id) DO NOTHING RETURNING 1)
                         SELECT (SELECT max(id) FROM b) AS last, (SELECT count(*) FROM ins) AS n""", (last, batch)).fetchone()
        if r["last"] is None:
            kv_set(query.SUBS_KEY, True)
            return done
        done += r["n"]
        last = r["last"]
        if log:
            print(f"fill-subs: {done} rows written, at post {last}", flush=True)
        time.sleep(pause)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["reclassify"]:
        print(f"reclassified: {reclassify()} posts changed")
    elif sys.argv[1:] == ["reindex"]:
        os.nice(10)  # below the live pipeline
        print(f"search index rewritten: {reindex(log=True)} rows (version {searchtext.VERSION})")
    elif sys.argv[1:] == ["fill-subs"]:
        os.nice(10)  # below the live pipeline
        print(f"post_subs written: {fill_subs(log=True)} rows")
    elif sys.argv[1:] == ["articles"]:
        print(f"articles set: {backfill_articles()} posts")
    elif sys.argv[1:] == ["schema"]:
        pool()
        print("schema ok")
    else:
        print("usage: corpus.py reclassify|reindex|fill-subs|articles|schema")
