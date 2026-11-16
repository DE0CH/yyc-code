"""Two-pass scoring of posts with comments (Deyao, 2026-10-14), shared by every source: a post whose comments matter
(a LIHKG or 香港討論區 thread, a catalogue forum's thread (forum_fetch.py); Facebook, Instagram, Threads and YouTube posts through social_twopass.py) is scored as ONE unit, the post with its
comments, twice at most:

  1. at capture: once the fetcher has read its comments (captured()), or at once when no fetcher is reading them (a
     collector filed the post); a post a fetcher is still reading (reading()) waits up to streams.FIRST_WAIT;
  2. again at published + MARK (due_at, set at the first scoring when that came before it; a post first scored
     after the mark has no second scoring), once its comments were re-read at or after due_at (fetched() /
     fetched_all()) or streams.SECOND_WAIT after due_at without a re-read.

The LATER scoring is the post's current score (scores, scored); every scoring is kept in score_passes (history).
scored.comments_upto = the newest comment row (posts.id) the latest scoring saw: the item page draws its divider
there. Notification stays once per post: a unit is one stream item (串流, Telegram), filed by whichever scoring first
passes the threshold; its comment rows are never scored or filed on their own.

A source joins by (1) adding its post URL pattern to UNIT_URLS, (2) filing comments with corpus.add_comments (rows
with thread = the post's URL), (3) calling reading()/captured() around the capture read and fetched() for each re-read,
and pulling due_for_fetch() often so the second scoring sees fresh comments (whichever read lands first is used).
Table two_pass (corpus.SCHEMA), keyed by URL so a fetcher can mark a post before its row exists.
"""
import json, os, re
from datetime import datetime, timedelta

import corpus

MARK = timedelta(minutes=30)
LIHKG_THREAD = r"^https://lihkg\.com/thread/[0-9]+/page/1$"
DISCUSS_THREAD = r"^https://www\.discuss\.com\.hk/archiver/\?tid-[0-9]+\.html$"  # 香港討論區 (replies_fetch.py, the whole site)
# social posts (every collector's: the followed pages and channels, the social searches), their comments read by
# social_twopass.py at capture and for the second scoring (comments_fetch.py's readers)
SOCIAL_URLS = [r"^https://www\.facebook\.com/[^#]+$", r"^https://www\.instagram\.com/(p|reel)/[A-Za-z0-9_-]+/?$",
               r"^https://www\.threads\.(net|com)/@[^/]+/post/[A-Za-z0-9_-]+/?$",
               r"^https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}$"]


def forum_units(path=None, forums=None):
    """the catalogue forums' thread posts that forum_fetch.py reads with their replies (forum_threads.json: every
    forum not "off"): its "thread" regexes with the group names taken out (they read the
    same in Postgres, JavaScript and Python)"""
    if forums is None:
        p = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "forum_threads.json")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            forums = json.load(f)
    return [re.sub(r"\(\?P<\w+>", "(", rx) for k, f in forums.items() if not k.startswith("_") and isinstance(f, dict)
            and not f.get("off") for rx in f.get("thread") or [] if "'" not in rx]


# the catalogue forums' threads (forum_fetch.py, CronJob forums): read at capture and for the second scoring
FORUM_URLS = forum_units()
UNIT_URLS = [LIHKG_THREAD, DISCUSS_THREAD] + SOCIAL_URLS + FORUM_URLS  # POSIX regexes of the posts scored as units with their comments
# units whose comments a fetcher reads at capture (social_twopass.py, forum_fetch.py): the first scoring waits for
# captured() even before the fetcher has called reading() (it comes round within minutes; streams.FIRST_WAIT at most)
FETCHER_URLS = SOCIAL_URLS + FORUM_URLS


def _any(rxs):
    """one POSIX regex matching what any of rxs matches, each keeping its own anchors: a statement with one regex per
    condition stays inside Postgres's cache of compiled regexes (32), where one per pattern recompiled on every row"""
    return "(?:" + "|".join(f"(?:{r})" for r in rxs) + ")"


def hosts_rx(rxs):
    """one regex matching the hosts (split_part(url, '/', 3)) of the URLs rxs match, from each pattern's
    ^https?://<host>/ part: a cheap prefilter before the URL regexes"""
    hosts = {}
    for r in rxs:
        m = re.match(r"\^https\??://([^/]+)/", r)
        if not m:  # a pattern of another shape would make the prefilter drop its URLs: refuse it by name
            raise ValueError(f"twopass: URL pattern not of the form ^https?://<host>/…: {r!r}")
        hosts[m.group(1)] = True
    return "^(?:" + "|".join(f"(?:{h})" for h in hosts) + ")$"


FETCHER_HOSTS_RX = hosts_rx(FETCHER_URLS)


def unit_cond(alias="p"):
    """SQL: the row is a unit post (not a comment row)"""
    return f"({alias}.thread IS NULL AND {alias}.url ~ '{_any(UNIT_URLS)}')"


def fetcher_cond(alias="p"):
    """SQL: the row is a unit whose comments a fetcher reads at capture"""
    return f"({alias}.thread IS NULL AND {alias}.url ~ '{_any(FETCHER_URLS)}')"


def comment_cond(alias="p"):
    """SQL: the row is a comment of a unit post (never scored on its own)"""
    return f"(coalesce({alias}.thread, '') ~ '{_any(UNIT_URLS)}')"


def _now():
    return datetime.now(corpus.HKT)


def reading(url, at=None):
    """a fetcher starts reading the comments of a post it captures (the first scoring waits for captured())"""
    corpus.db().execute("""INSERT INTO two_pass (url, reading_at) VALUES (%s, %s)
                           ON CONFLICT (url) DO UPDATE SET reading_at = coalesce(two_pass.reading_at, excluded.reading_at)""",
                        (url, at or _now()))


def captured(url, at=None):
    """the post's comments at capture are read"""
    at = at or _now()
    corpus.db().execute("""INSERT INTO two_pass (url, captured_at, fetched_at) VALUES (%s, %s, NULL)
                           ON CONFLICT (url) DO UPDATE SET captured_at = coalesce(two_pass.captured_at, excluded.captured_at)""",
                        (url, at))
    fetched(url, at)


def fetched(url, at=None):
    """the post's comments were read at `at`: counts for the second scoring when at >= due_at"""
    corpus.db().execute("UPDATE two_pass SET fetched_at = %s WHERE url = %s AND due_at <= %s AND second_at IS NULL",
                        (at or _now(), url, at or _now()))


def fetched_all(url_like, at):
    """a fetcher read every post of its own (URL LIKE url_like) that had new comments, in a run that started at `at`"""
    corpus.db().execute("""UPDATE two_pass SET fetched_at = %s WHERE url LIKE %s AND due_at <= %s
                           AND fetched_at IS NULL AND second_at IS NULL""", (at, url_like, at))


def due_for_fetch(url_like, now=None, limit=500):
    """posts of a fetcher (URL LIKE url_like) whose second scoring is due and whose comments weren't re-read since"""
    return [r[0] for r in corpus.db().execute(
        f"""SELECT url FROM two_pass tp WHERE url LIKE %s AND due_at <= %s AND fetched_at IS NULL AND second_at IS NULL
           AND NOT EXISTS (SELECT 1 FROM posts p WHERE p.url = tp.url AND p.thread IS NULL AND NOT {corpus.seen_cond("p", "comments")})
           ORDER BY due_at LIMIT %s""", (url_like, now or _now(), limit))]


def scored(c, p, at, res, upto, comments):
    """the scorer filed a unit post's scoring (inside its transaction c): first → due_at when before the mark;
    second → done. Kept in score_passes"""
    pub = p.get("published")
    try:
        pub = datetime.fromisoformat(pub) if pub else None
    except ValueError:
        pub = None
    row = c.execute("SELECT first_at FROM two_pass WHERE url=%s", (p["url"],)).fetchone()
    if not row or row[0] is None:
        due = pub + MARK if pub and at < pub + MARK else None
        c.execute("""INSERT INTO two_pass (url, first_at, due_at) VALUES (%s, %s, %s)
                     ON CONFLICT (url) DO UPDATE SET first_at = excluded.first_at, due_at = excluded.due_at""",
                  (p["url"], at, due))
        n = 1
    else:
        c.execute("UPDATE two_pass SET second_at = %s WHERE url = %s", (at, p["url"]))
        n = 2
    c.execute("""INSERT INTO score_passes (post_id, pass, at, comments, comments_upto, result) VALUES (%s,%s,%s,%s,%s,%s)
                 ON CONFLICT (post_id, pass) DO UPDATE SET at=excluded.at, comments=excluded.comments,
                 comments_upto=excluded.comments_upto, result=excluded.result""",
              (p["id"], n, at, comments, upto, corpus.J(res)))
    c.execute("UPDATE scored SET comments_upto = %s WHERE post_id = %s", (upto, p["id"]))
