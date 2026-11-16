#!/usr/bin/env python3
"""The catalogue forums' threads with their posts and replies (Deyao, 2026-10-20: "collect each thread's posts and
replies, not only the title"). sites_fetch.py reads the forums of sites.json ("type": "Forum": 親子王國, 教育王國,
Post76, 公仔箱, GeoExpat, U公屋, …) as thread LISTINGS, so each thread comes in as one post with its title only. This
job reads each such thread like replies_fetch.py reads LIHKG and 香港討論區: the opening post's full text becomes the
thread post's text (and its title when the listing gave a placeholder: a sitemap slug, 〔連結〕), every reply a comment
row of its own (corpus.add_comments: author, time, text, likes when shown), and the thread is scored as one unit with
its replies (twopass.py, both scorings).

Which threads: posts (thread IS NULL, seen in the last NEW_DAYS) whose URL matches a forum's "thread" regex in
forum_threads.json (one line of data per forum: software, URL forms, route, pacing). A new thread is read at once
(twopass.reading, then captured: its first scoring waits for the read, streams.FIRST_WAIT at most); a thread whose
second scoring is due (published + 30 min, twopass.due_for_fetch) is read again; a followed thread is read again
REREAD_MIN after its last read, the wait doubling after every read that found no new post (up to REREAD_MAX) and
starting over after one that did; a thread with no new post for FOLLOW_DAYS is no longer followed. A re-read starts at
the page of the first post not yet filed (kv-free: table forum_threads holds each thread's state).

How a thread is read: Discuz's printable view (one request for the first post and up to 99 replies; old Discuz 6/7 and
Discuz X) then the paged view for the rest, vBulletin's printthread, XenForo and Discuz pages, NodeBB's /api/topic,
schema.org DiscussionForumPosting (asiaxpat), WordPress's REST API (post + comments), review33's frames
(forum_message.php). Text only (nomedia: a media answer is refused, pictures stay out of the text). Through the route
the forum's listing uses (sites.json): direct, the ISP IP ("isp", one client at a time under the 'yyc youtube' lock,
bytes counted by resi.count_isp) or yycstream residential ("hk" | "mo", a sticky session per forum per run, bytes
counted per site by resi.count); client "chrome" = curl_cffi as Chrome (sites whose bot check refuses urllib's TLS).
eyny's proof-of-work page (SHA-256 with leading zeros, set as cookies) is solved and its 18+ notice agreed to on the
same sticky session. One request at a time per forum, `gap` seconds apart; forums run in parallel.

Monitor: kv forums:<forum>:status (ok / error, threads read, replies stored, pending, proxy MB today), on the
monitor's Forum replies card (monitor.py pipeline "replies").

    python3 forum_fetch.py run [FORUM…]      one pass (CronJob forums, every 5 min)
    python3 forum_fetch.py run --browser     the "browser" route's forums in one Chrome (CronJob forums-browser, xvfb-run)
    python3 forum_fetch.py test URL          read one thread now and print it (writes nothing)
    python3 forum_fetch.py status            each forum's last status
Env: DATABASE_URL, RESI_PROXY, ISP_PROXY, FORUM_RUN_SECONDS.
"""
import hashlib, html, json, os, re, sys, threading, time, urllib.error, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import corpus, resi, twopass

HKT = corpus.HKT
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.environ.get("FORUM_THREADS_FILE") or os.path.join(HERE, "forum_threads.json")
RUN_SECONDS = int(os.environ.get("FORUM_RUN_SECONDS", 240))  # the CronJob runs every 5 min
NEW_DAYS = 2            # threads filed this recently are read (the poller's window)
FOLLOW_DAYS = 7         # a thread with no new post for this long is no longer followed
REREAD_MIN = 1800       # seconds: a followed thread's first re-read after a read that found new posts
REREAD_MAX = 86400      # ... doubling after each read that found none, up to this
FIRST_MAX = 100         # posts a Discuz printable view holds at most (the opening post + 99 replies)
MAX_PAGES = 5           # pages of one thread in one run: a long thread carries on in the next run, after the new
                        # threads (公仔箱's 2019 check-in thread of 2,668 pages at 10-25 s a page held a whole run, 2026-10-20)
GAP = 1.0               # seconds between two requests to one forum (politeness; a forum's "gap" overrides)
ISP_REFUSED = 6         # tunnel 403s in a row on the ISP IP that move a forum with a fallback to it (one 403 can be
ISP_WAIT = 10           # another client on the IP: 3 in a row moved 親子王國 to yycstream, 2026-10-20), ISP_WAIT s apart
FAILS_STOP = 4          # consecutive failed threads that stop a forum's run (blocked: next run tries again)
PARSERS = {}            # software → parse(text, page, ppp) → (meta, posts)
ROUTES = ("direct", "isp", "hk", "mo", "browser")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"

SCHEMA = """
CREATE TABLE IF NOT EXISTS forum_threads (
  forum TEXT NOT NULL, tid TEXT NOT NULL, url TEXT NOT NULL, post_id BIGINT,
  read INTEGER NOT NULL DEFAULT 0,           -- the highest post number filed (1 = the opening post)
  total INTEGER,                             -- posts in the thread at the last complete read
  status TEXT NOT NULL DEFAULT 'new',        -- new | reading | follow | done | noaccess | gone | error
  every INTEGER, next_at TIMESTAMPTZ, last_new TIMESTAMPTZ, at TIMESTAMPTZ,
  tries INTEGER NOT NULL DEFAULT 0, error TEXT,
  PRIMARY KEY (forum, tid));
CREATE INDEX IF NOT EXISTS forum_threads_due ON forum_threads (next_at) WHERE status IN ('follow', 'reading', 'error');
"""
_schema_done = set()


class Blocked(Exception):
    """the forum refused every try (a wall, a 403 on every session): the forum's run stops, the next run tries again"""


class NoAccess(Exception):
    """the thread is readable only after a login (Discuz 提示信息: 無權訪問 / 閱讀權限): not tried again"""


class Gone(Exception):
    """the thread is deleted or hidden (404, no posts): not tried again until it is filed again"""


def now():
    return datetime.now(HKT)


def schema():
    url = corpus.DATABASE_URL
    if url not in _schema_done:
        corpus.db().execute(SCHEMA)
        _schema_done.add(url)


# ---------------- config ----------------

def load(path=CONFIG):
    """forum_threads.json → {forum: config}, every entry checked (parser known, thread regexes with a tid group and
    no syntax Postgres or JavaScript would read differently, route known, page templates)"""
    with open(path) as f:
        forums = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    for k, f in forums.items():
        if not re.fullmatch(r"[a-z0-9_]+", k):
            raise ValueError(f"forum_threads.json: bad key {k!r}")
        if f.get("route", "direct") not in ROUTES or f.get("client", "urllib") not in ("urllib", "chrome"):
            raise ValueError(f"forum_threads.json {k}: route/client {f.get('route')}/{f.get('client')}")
        for name in (f.get("parser"), f.get("first_parser")):
            if name and name not in PARSERS:
                raise ValueError(f"forum_threads.json {k}: unknown parser {name}")
        if not f.get("thread") or not f.get("page") or not f.get("parser"):
            raise ValueError(f"forum_threads.json {k}: thread, page and parser")
        if f.get("fallback") and (f.get("route") != "isp" or f["fallback"] not in ("hk", "mo")):
            raise ValueError(f"forum_threads.json {k}: fallback hk/mo is for the isp route")
        if f.get("first") and not f.get("first_parser"):
            raise ValueError(f"forum_threads.json {k}: first needs first_parser")
        f["_rx"] = []
        for rx in f["thread"]:
            if not rx.startswith("^") or "\\d" in rx or "\\w" in rx:
                raise ValueError(f"forum_threads.json {k}: thread regex {rx!r} (anchored, [0-9] not \\d)")
            c = re.compile(rx)
            if "tid" not in c.groupindex:
                raise ValueError(f"forum_threads.json {k}: thread regex {rx!r} needs (?P<tid>…)")
            f["_rx"].append(c)
        f["key"] = k
    return forums


def match(forums, url):
    """the forum and the thread's groups ({tid, …}) of a post URL, else (None, None)"""
    for f in forums.values():
        for c in f["_rx"]:
            m = c.match(url or "")
            if m:
                return f, {k: v for k, v in m.groupdict().items() if v is not None}
    return None, None


def fill(template, groups, page=1, ppp=1):
    return template.format(page=page, page0=page - 1, start=(page - 1) * ppp, **groups)


# ---------------- text ----------------

QUOTES = [r"<blockquote\b(?:(?!<blockquote\b).)*?</blockquote>",
          r'<div class="quote">(?:(?!<div class="quote">).)*?</div>',
          r'<div class="bbcode_container">(?:(?!<div class="bbcode_container">).)*?</div>\s*</div>',
          r'<i class="pstatus">.*?</i>', r"<ignore_js_op>.*?</ignore_js_op>", r'<div class="tip tip_4[^"]*".*?</div>\s*</div>', r'<div class="locked">.*?</div>',
          r"<script\b.*?</script>", r"<style\b.*?</style>", r"<!--.*?-->"]
TAG_IN = r"""(?:[^>"']|"[^"]*"|'[^']*')*"""  # inside a tag: a quoted attribute may hold ">" (onload="if(this.width >…")
ATTACH = [r"(?:圖片附件|附件)\s*:.*?下載次數:?\s*[0-9]+", r"\S+\s*\([0-9.]+ [KM]B, 下載次數: [0-9]+\)", r"下載附件",
          r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2} [0-9]{1,2}:[0-9]{2} 上傳", r"(?:[0-9]+\s*(?:秒|分鐘|小時|天)前|(?:昨天|前天)\s*[0-9:]+)\s*上傳", r"https?://\S*attachment\.php\S*", r"\S+\.(?:jpe?g|png|gif|webp)\s*\([^)]*KB\)"]


def text_of(s, drop_quotes=True):
    """post HTML → plain text: quoted posts dropped (each is its own row), scripts and pictures out (an emoji's alt
    text kept), paragraphs kept"""
    s = s or ""
    for _ in range(6):  # nested quotes: innermost first
        s2 = s
        for rx in QUOTES if drop_quotes else QUOTES[3:]:
            s2 = re.sub(rx, " ", s2, flags=re.S | re.I)
        if s2 == s:
            break
        s = s2
    s = re.sub(r"<img\b%s\balt=\"([^\"]{1,12})\"%s>" % (TAG_IN, TAG_IN), r"\1", s)
    s = re.sub(r"<img\b%s>" % TAG_IN, " ", s)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>|</h[1-6]>", "\n", s, flags=re.I)
    s = html.unescape(re.sub(r"<[a-zA-Z/!]%s>" % TAG_IN, "", s))
    for rx in ATTACH:
        s = re.sub(rx, " ", s, flags=re.S)
    s = re.sub(r"[ \t\u3000\xa0\r]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", re.sub(r" *\n *", "\n", s)).strip()


def inner(s, start):
    """the HTML inside the element whose opening tag starts at s[start] (nested elements of the same name counted)"""
    m = re.match(r"<([a-zA-Z0-9]+)\b[^>]*>", s[start:])
    if not m:
        return ""
    tag, i, depth = m.group(1).lower(), start + m.end(), 1
    rx = re.compile(r"<(/?)%s\b[^>]*>" % tag, re.I)
    j = i
    for t in rx.finditer(s, i):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return s[i:t.start()]
        j = t.end()
    return s[i:]


AGO = re.compile(r"(\d+)\s*(秒|分鐘|分钟|小時|小时|天)前")


def when(s, order="ymd", ref=None):
    """a forum's post time → datetime (HKT): 2026-10-20 17:16[:27], 26-10-20 16:51 (two-digit year), 2026-2-18 03:30 AM,
    17-06-2026, 09:04 PM (order dmy), ISO 8601, an epoch in ms, 3 分鐘前 / 昨天 / Today, …; None when unreadable"""
    if s is None or s == "":
        return None
    ref = ref or now()
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s / 1000 if s > 1e11 else s, timezone.utc).astimezone(HKT)
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(s))).replace("\xa0", " ").strip()
    if re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d", s):
        try:
            t = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return (t if t.tzinfo else t.replace(tzinfo=HKT)).astimezone(HKT)
        except ValueError:
            pass
    m = AGO.search(s)
    if m:
        n, u = int(m.group(1)), m.group(2)
        return ref - timedelta(seconds=n * {"秒": 1, "分鐘": 60, "分钟": 60, "小時": 3600, "小时": 3600, "天": 86400}[u])
    day = None
    if re.search(r"昨天|Yesterday", s, re.I):
        day = (ref - timedelta(days=1)).date()
    elif re.search(r"前天", s):
        day = (ref - timedelta(days=2)).date()
    elif re.search(r"今天|Today", s, re.I):
        day = ref.date()
    hm = re.search(r"(\d{1,2}):(\d\d)(?::(\d\d))?\s*([AP]M)?", s, re.I)
    m = re.search(r"(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})", s)
    if m and not day:
        a, b, c = (int(x) for x in m.groups())
        y, mo, d = (c, b, a) if order == "dmy" or len(m.group(3)) == 4 else (a, b, c)
        if y < 100:
            y += 2000
        try:
            day = datetime(y, mo, d).date()
        except ValueError:
            return None
    if not day:
        return None
    h, mi, sec = (int(hm.group(1)), int(hm.group(2)), int(hm.group(3) or 0)) if hm else (0, 0, 0)
    if hm and hm.group(4):
        h = h % 12 + (12 if hm.group(4).upper() == "PM" else 0)
    try:
        return datetime(day.year, day.month, day.day, h, mi, sec, tzinfo=HKT)
    except ValueError:
        return None


def last_page(s, rx):
    """the highest page number a page links to (pagination), at least 1"""
    return max([int(x) for x in re.findall(rx, s)] + [1])


# ---------------- parsers: (meta {title, pages, board, replies}, posts [{floor, author, published, text, likes}]) ----------------

def parser(name):
    def reg(fn):
        PARSERS[name] = fn
        return fn
    return reg


DISCUZ_NOACCESS = re.compile(r"無權訪問|无权访问|閱讀權限|阅读权限|請先登錄|请先登录|需要登錄|您需要先登錄|沒有權限|没有权限|"
                             r"抱歉，本帖要求|帖子被屏蔽|指定的主題不存在|指定的主题不存在|主題不存在|已被刪除|不存在或已被刪除")


def discuz_message(s):
    """Discuz's 提示信息 page (a message instead of the thread): its text, else None"""
    m = re.search(r'id="messagetext"[^>]*>(.*?)</div>', s, re.S) or \
        re.search(r'class="alert_(?:info|error)"[^>]*>(.*?)</div>', s, re.S)
    if m:
        return text_of(m.group(1))[:200]
    if re.search(r"<title>\s*提示信息", s):
        return text_of(re.sub(r".*<body\b", "<", s, flags=re.S))[:200]
    return None


def check_discuz(s):
    msg = discuz_message(s)
    if msg is not None:
        if re.search(r"不存在|已被刪除|已被删除|已刪除", msg):
            raise Gone(msg)
        raise NoAccess(msg or "提示信息")


@parser("discuz_print")
def discuz_print(s, page=1, ppp=FIRST_MAX):
    """Discuz's printable view (viewthread.php?action=printable / forum.php?mod=viewthread&action=printable): every
    post up to FIRST_MAX, each after a grey <hr>: 作者 / 時間 (or <span title=…>), the body after the header line"""
    check_discuz(s)
    if 'color="#808080"' not in s and 'id="postmessage_' in s:  # the forum served its normal thread page instead
        m, ps = discuz(s, 1, ppp)
        return dict(m, paged=True), ps
    m = re.search(r"<b>(?:標題|标题|Subject): </b>(.*?)<b>", s, re.S)
    title = text_of(m.group(1)) if m else None
    s = re.split(r'<hr noshade size="2" width="100%" color="(?!#808080)[^"]*">', s)[0]
    segs = s.split('<hr noshade size="2" width="100%" color="#808080">')[1:]
    posts = []
    for i, seg in enumerate(segs, 1):
        au = re.search(r"<b>(?:作者|Author): </b>\s*(.*?)\s*(?:&nbsp;|<b>|<br)", seg, re.S)
        tm = re.search(r"<b>(?:時間|时间|Time): </b>\s*(?:<span title=\"([^\"]+)\">)?([^<]*)", seg)
        body = seg
        if tm:
            body = seg[tm.end():]
            body = re.split(r"<br\s*/?>", body, maxsplit=1)[-1]  # the rest of the header line
        body = re.sub(r"^\s*<b>(?:標題|标题|Subject): </b>.*?<br\s*/?>", "", body, count=1, flags=re.S)
        posts.append({"floor": i, "author": text_of(au.group(1)) if au else None,
                      "published": when(tm.group(1) or tm.group(2)) if tm else None, "text": text_of(body)})
    if not posts and title is None:
        raise Gone("no posts on the printable view")
    return {"title": title, "pages": 1}, posts


def _discuz_author(chunk):
    for rx in (r'class="xw1"[^>]*>([^<]+)</a>', r"<cite>\s*(?:<a [^>]*>)?([^<]+?)\s*</a>",
               r'class="postauthor">\s*<div class="postinfo">\s*<a [^>]*>\s*([^<]+?)\s*</a>', r'data-username="([^"]+)"',
               r'href="(?:space-uid-\d+\.html|space\.php\?uid=\d+|home\.php\?mod=space&(?:amp;)?uid=\d+)"[^>]*>\s*([^<]+?)\s*</a>'):
        m = re.search(rx, chunk)
        if m and not re.search(r"\.(?:jpe?g|png|gif)$", m.group(1)):
            return html.unescape(m.group(1)).strip()
    return None


@parser("discuz")
def discuz(s, page=1, ppp=10):
    """a Discuz thread page (Discuz X forum.php?mod=viewthread and Discuz 6/7 viewthread.php): each post's block
    (id="post_<pid>" / id="pid<pid>"): floor (postnum <em>N</em>), author, time (authorposton, a <span title> when the
    page shows "N 分鐘前"), body (postmessage_<pid>, nested tables counted); pages from the pagination"""
    check_discuz(s)
    m = re.search(r'<span id="thread_subject"[^>]*>(.*?)</span>', s, re.S)
    title = text_of(m.group(1)) if m else None
    if not title:  # <title>: "<subject> - <board> - <site> - Powered by Discuz!"
        m = re.search(r"<title>(.*?)</title>", s, re.S)
        title = re.split(r"\s+-\s+", text_of(m.group(1)))[0] if m else None
    board = None
    mb = re.search(r'<div id="pt" class="bm cl">(.*?)</div>', s, re.S)
    if mb:
        crumbs = re.findall(r"<a [^>]*>([^<]+)</a>", mb.group(1))
        board = html.unescape(crumbs[-2]).strip() if len(crumbs) >= 2 else None
    pids = list(dict.fromkeys(re.findall(r'id="postmessage_(\d+)"', s)))
    starts = []
    for pid in pids:
        mm = re.search(r'id="(?:post_|pid)%s"' % pid, s)
        starts.append(mm.start() if mm else s.find('id="postmessage_%s"' % pid))
    posts = []
    for n, (pid, a) in enumerate(zip(pids, starts)):
        b = starts[n + 1] if n + 1 < len(starts) else len(s)
        chunk = s[a:b]
        fl = re.search(r'id="postnum%s"[^>]*>\s*(?:<em>)?\s*(\d+)' % pid, chunk) or re.search(r"<em>(\d+)</em>\s*<sup>#</sup>", chunk)
        tm = re.search(r'id="authorposton%s"[^>]*>(.*?)</em>' % pid, chunk, re.S)
        t = None
        if tm:
            sp = re.search(r'<span title="([^"]+)"', tm.group(1))
            t = when(sp.group(1) if sp else re.sub(r"發表於|发表于|Posted on", "", tm.group(1)))
        i = chunk.find('id="postmessage_%s"' % pid)
        body = inner(chunk, chunk.rfind("<", 0, i)) if i >= 0 else ""
        posts.append({"floor": int(fl.group(1)) if fl else (page - 1) * ppp + n + 1, "author": _discuz_author(chunk),
                      "published": t, "text": text_of(body), "pid": pid})
    pg = re.search(r'<div class="(?:pg|pages)">(.*?)</div>', s, re.S)
    pages = last_page(pg.group(1), r"(?:[?&;]page=|thread-\d+-)(\d+)") if pg else 1
    sp = re.search(r'title="共 (\d+) 頁"|title="共 (\d+) 页"', s)
    if sp:
        pages = max(pages, int(sp.group(1) or sp.group(2)))
    if not posts:
        raise Gone("no posts on the thread page")
    return {"title": title, "pages": max(pages, page), "board": board}, posts


@parser("vbulletin_print")
def vbulletin_print(s, page=1, ppp=10):
    """vBulletin 4's printthread: <li class="postbit" id="post_N"> with the date (DD-MM-YYYY, 09:04 PM), username and
    <blockquote class="restore"> body; "Results 1 to 10 of 57" gives the pages"""
    m = re.search(r"<title>(.*?)</title>", s, re.S)
    title = text_of(m.group(1)) if m else None
    posts = []
    for blk in re.split(r'<li class="postbit[^"]*" id="post_', s)[1:]:
        n = re.match(r"(\d+)", blk)
        dt = re.search(r'<div class="datetime">(.*?)</div>', blk, re.S)
        au = re.search(r'<span class="username">(.*?)</span>', blk, re.S)
        i = blk.find('<blockquote class="restore">')
        body = inner(blk, i) if i >= 0 else ""
        posts.append({"floor": int(n.group(1)) if n else None, "author": text_of(au.group(1)) if au else None,
                      "published": when(dt.group(1), "dmy") if dt else None, "text": text_of(body)})
    tot = re.search(r"Results \d+ to \d+ of ([\d,]+)", s)
    pages = -(-int(tot.group(1).replace(",", "")) // ppp) if tot else last_page(s, r"printthread\d+-(\d+)\.html")
    if not posts:
        raise Gone("no posts on the printable view")
    return {"title": title, "pages": max(pages, page)}, posts


@parser("xenforo")
def xenforo(s, page=1, ppp=20):
    """a XenForo thread page: <article class="message … message--post" data-author data-content="post-<id>">, the
    first <time datetime>, the post number (#N), the body in <div class="bbWrapper">; pages from pageNav"""
    m = re.search(r'<h1 class="p-title-value">(.*?)</h1>', s, re.S)
    title = text_of(m.group(1)) if m else None
    posts = []
    blocks = re.split(r'<article class="message[^"]*message--post', s)[1:]
    for n, blk in enumerate(blocks):
        au = re.search(r'data-author="([^"]*)"', blk)
        tm = re.search(r'<time[^>]*datetime="([^"]+)"', blk)
        fl = re.search(r">\s*#([\d,]+)\s*<", blk)
        i = blk.find('<div class="bbWrapper">')
        body = inner(blk, i) if i >= 0 else ""
        likes = re.search(r'class="reactionsBar-link"[^>]*>.*?(?:and ([\d,]+) others?)', blk, re.S)
        posts.append({"floor": int(fl.group(1).replace(",", "")) if fl else (page - 1) * ppp + n + 1,
                      "author": html.unescape(au.group(1)) if au else None, "published": when(tm.group(1)) if tm else None,
                      "text": text_of(body)})
    nav = re.search(r'<ul class="pageNav-main">(.*?)</ul>', s, re.S)
    pages = last_page(nav.group(1), r"page-(\d+)") if nav else 1
    if not posts:
        raise Gone("no posts on the thread page")
    return {"title": title, "pages": max(pages, page)}, posts


@parser("nodebb")
def nodebb(s, page=1, ppp=20):
    """NodeBB's /api/topic/<tid>?page=N: posts[] (index 0 = the opening post, user.username, timestamp ms, content
    HTML, upvotes), pagination.pageCount"""
    d = json.loads(s)
    posts = []
    for p in d.get("posts") or []:
        if p.get("deleted"):
            continue
        posts.append({"floor": int(p.get("index") or 0) + 1, "author": (p.get("user") or {}).get("username"),
                      "published": when(p.get("timestamp")), "text": text_of(p.get("content")),
                      "likes": p.get("upvotes") if isinstance(p.get("upvotes"), int) else None})
    return {"title": text_of(d.get("title") or ""), "pages": int((d.get("pagination") or {}).get("pageCount") or 1),
            "board": (d.get("category") or {}).get("name"), "replies": max(int(d.get("postcount") or 1) - 1, 0)}, posts


@parser("ldjson")
def ldjson(s, page=1, ppp=1000):
    """a page's schema.org DiscussionForumPosting (JSON-LD): headline, text, author, datePublished and its comment[]"""
    for blk in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', s, re.S):
        try:
            d = json.loads(blk)
        except ValueError:
            continue
        for x in (d if isinstance(d, list) else [d, d.get("mainEntity") if isinstance(d, dict) else None]):
            if isinstance(x, dict) and x.get("@type") == "DiscussionForumPosting":
                au = lambda o: (o.get("author") or {}).get("name") if isinstance(o.get("author"), dict) else None  # noqa: E731
                posts = [{"floor": 1, "author": au(x), "published": when(x.get("datePublished")), "text": text_of(x.get("text"))}]
                for i, c in enumerate(x.get("comment") or [], 2):
                    posts.append({"floor": i, "author": au(c), "published": when(c.get("datePublished")), "text": text_of(c.get("text"))})
                n = x.get("commentCount")
                return {"title": text_of(x.get("headline") or ""), "pages": 1,
                        "replies": n if isinstance(n, int) else None}, posts
    raise Gone("no DiscussionForumPosting on the page")


@parser("review33")
def review33(s, page=1, ppp=20):
    """review33's message frame (forum_message.php?topic=…&page=N, 0-based): each message a table: [#N] title, the
    body, the author, the time"""
    title, posts = None, []
    for m in re.finditer(r"\[#(\d+)\]\s*([^<]*)</a>(?:&nbsp;)*\s*<BR>(.*?)</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>\s*([^<]*?)\s*<br>"
                         r".*?<td[^>]*>\s*(\d{4}-\d\d-\d\d \d\d:\d\d)\s*</td>", s, re.S | re.I):
        n = int(m.group(1))
        if n == 1:
            title = text_of(m.group(2))
        posts.append({"floor": n, "author": html.unescape(m.group(4)).strip() or None, "published": when(m.group(5)),
                      "text": text_of(m.group(3))})
    pages = max([int(x) + 1 for x in re.findall(r"forum_message\.php\?topic=\d+&(?:amp;)?page=(\d+)", s)] + [1])
    if not posts:
        raise Gone("no messages on the page")
    return {"title": title, "pages": max(pages, page)}, posts


@parser("wp_post")
def wp_post(s, page=1, ppp=1):
    """WordPress's /wp-json/wp/v2/posts/<id>: the post as the opening post"""
    d = json.loads(s)
    if not isinstance(d, dict) or not d.get("id"):
        raise Gone("no post")
    return {"title": text_of((d.get("title") or {}).get("rendered") or ""), "pages": 1}, [
        {"floor": 1, "author": None, "published": when((d.get("date_gmt") or "") + "+00:00") if d.get("date_gmt") else None,
         "text": text_of((d.get("content") or {}).get("rendered") or "")}]


@parser("wp_comments")
def wp_comments(s, page=1, ppp=100):
    """WordPress's /wp-json/wp/v2/comments?post=<id>&order=asc: the replies (floor 2 on); a full page means more"""
    d = json.loads(s)
    if isinstance(d, dict):  # {"code": "rest_…"}: comments closed or not public
        return {"pages": page}, []
    posts = [{"floor": (page - 1) * ppp + i + 2, "author": c.get("author_name"),
              "published": when((c.get("date_gmt") or "") + "+00:00") if c.get("date_gmt") else None,
              "text": text_of((c.get("content") or {}).get("rendered") or "")} for i, c in enumerate(d)]
    return {"pages": page + 1 if len(d) >= ppp else page}, posts


@parser("phpbb")
def phpbb(s, page=1, ppp=20):
    """a phpBB 3 topic page (viewtopic.php?t=…&start=N): <div id="p<id>" class="post">, the author line (<p
    class="author">… 發表人 <strong>name</strong> 發表於 2026-09-02, 18:33, or <time datetime> in 3.1+), <div
    class="content">; "N 篇文章" / "N posts" gives the pages"""
    m = re.search(r"<h2[^>]*>(.*?)</h2>", s, re.S)
    posts = []
    for n, blk in enumerate(re.split(r'<div id="p\d+" class="post\b', s)[1:]):
        au = re.search(r'class="author".*?<strong>\s*(?:<a [^>]*>)?([^<]+)', blk, re.S) or \
            re.search(r'class="username(?:-coloured)?"[^>]*>([^<]+)</a>', blk)
        tm = re.search(r'<time datetime="([^"]+)"', blk)
        t = when(tm.group(1)) if tm else None
        if not t:
            line = re.search(r'class="author"(.*?)</p>', blk, re.S)
            t = when(re.split(r"發表於|发表于|»", text_of(line.group(1)))[-1]) if line else None
        i = blk.find('<div class="content">')
        posts.append({"floor": (page - 1) * ppp + n + 1, "author": html.unescape(au.group(1)).strip() if au else None,
                      "published": t, "text": text_of(inner(blk, i) if i >= 0 else "")})
    tot = re.search(r"([\d,]+)\s*(?:篇文章|posts?\b)", s)
    pages = -(-int(tot.group(1).replace(",", "")) // ppp) if tot else page
    if not posts:
        raise Gone("no posts on the topic page")
    return {"title": text_of(m.group(1)) if m else None, "pages": max(pages, page),
            "replies": int(tot.group(1).replace(",", "")) - 1 if tot else None}, posts


# ---------------- the client: one forum's requests, on its route ----------------

class Client:
    """requests to one forum on its route, `gap` seconds apart, cookies kept (curl_cffi client) and one sticky
    residential session per run (hk/mo); bytes counted (resi.count per site, resi.count_isp)"""

    def __init__(self, forum):
        self.f = forum
        self.route = forum.get("route", "direct")
        self.gap = float(forum.get("gap", GAP))
        self.timeout = float(forum.get("timeout", 40))
        self.refused = 0  # tunnel 403s on the ISP IP in a row
        self.last = 0.0
        self.sticky = None
        self.cookies = {}
        self.cc = None
        self.requests = 0
        self.deadline = None  # the run's (forum_run): no try starts past it, no ISP lock wait runs over it

    def proxy(self):
        if self.route in ("hk", "mo"):
            if not resi.configured():
                raise RuntimeError("RESI_PROXY is not set")
            if not self.sticky:
                self.sticky = resi.session_id(10)
            return resi.url(self.route, session=self.sticky, lifetime="30m")
        if self.route == "isp":
            p = os.environ.get("ISP_PROXY")
            if not p:
                raise RuntimeError("ISP_PROXY is not set (Secret isp-proxy)")
            return p
        return None

    def rotate(self):
        self.sticky, self.cc, self.cookies = None, None, {}

    def _pace(self):
        w = self.last + self.gap - time.time()
        if w > 0:
            time.sleep(w)
        self.last = time.time()
        self.requests += 1

    def _isp(self):
        import contextlib
        if self.route != "isp":
            return contextlib.nullcontext()
        import sites_fetch
        if self.deadline:
            return sites_fetch.isp_lock(wait=max(1, min(sites_fetch.ISP_WAIT, self.deadline - time.time())))
        return sites_fetch.isp_lock()

    def fetch(self, url, data=None):
        """one request → (status, text). urllib through sources.fetch (its charset, walls, counting) or curl_cffi"""
        self._pace()
        if self.f.get("client") == "chrome":
            return self._chrome(url, data)
        import sources
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())} if self.cookies else {}
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7"
        body = urllib.parse.urlencode(data).encode() if data else None
        if body:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            with self._isp():
                text = sources.fetch(url, data=body, headers=headers, proxy=self.proxy(), timeout=self.timeout, charset="auto",
                                     isp=self.route == "isp")
            return 200, text
        except urllib.error.HTTPError as e:
            return e.code, ""

    def _chrome(self, url, data=None):
        import nomedia, sources
        px = self.proxy()
        if self.cc is None:
            if self.route in ("hk", "mo"):
                self.cc = resi.session(proxy=px)  # counted per site
            else:
                from curl_cffi import requests as cr
                self.cc = cr.Session(impersonate="chrome", proxies={"http": px, "https": px} if px else None)
        with self._isp():
            r = self.cc.post(url, data=data, timeout=self.timeout) if data else self.cc.get(url, timeout=self.timeout)
        if self.route == "isp":
            resi.count_isp(resi.wire(r))
        if nomedia.media_type(r.headers.get("Content-Type")):
            raise nomedia.refuse(url)
        return r.status_code, r.content.decode(sources.body_charset(r.content, r.headers.get("Content-Type")), "replace")

    def get(self, url):
        """the page's text through the forum's checks (eyny's proof of work and 18+ notice); a 403/429/5xx or a
        connection error moves a proxied forum to a new sticky session (3 tries); direct: Blocked at once"""
        err = None
        for attempt in range(ISP_REFUSED + 1):
            if self.deadline and time.time() > self.deadline:
                raise Blocked(f"{url[:100]}: the run's time is up" + (f" ({err})" if err else ""))
            try:
                status, text = self.fetch(url)
            except resi.ProxyOut:
                raise
            except Exception as e:  # noqa: BLE001: a connection error: another session
                if resi.is_out(e):
                    raise resi.mark(repr(e)[:300])
                err = repr(e)[:200]
                if self.route == "isp" and re.search(r"Tunnel connection failed: 403|CONNECT tunnel failed, response 403", err):
                    # another client on the ISP IP gets the same answer: wait and try again; ISP_REFUSED in a row = IPRoyal
                    # refuses this host on the ISP IP (伊莉, 2026-10-20), yycstream for the rest of the run
                    self.refused += 1
                    if self.f.get("fallback") and self.refused >= ISP_REFUSED:
                        print(self.f["key"], "ISP IP refused (tunnel 403): route", self.f["fallback"], "this run", flush=True)
                        self.route = self.f["fallback"]
                        self.rotate()
                        continue
                    time.sleep(ISP_WAIT)
                    continue
                self.rotate()
                continue
            self.refused = 0
            if self.f.get("pow") and "solvePoW" in text:
                self.cookies.update(pow_cookies(text))
                if self.cc is not None:
                    for k, v in pow_cookies(text).items():
                        self.cc.cookies.set(k, v, domain="." + ".".join(urllib.parse.urlsplit(url).hostname.split(".")[-2:]))
                time.sleep(1)
                status, text = self.fetch(url)
                if "solvePoW" in text:  # the first answer after the cookies can still be the check: once more
                    time.sleep(1)
                    status, text = self.fetch(url)
            gate = self.f.get("agree")
            if gate and gate["match"] in text:
                status, text = self.fetch(url, data=gate["data"])
                if gate["match"] in text:
                    status, text = self.fetch(url)
            if status == 404 or status == 410:
                raise Gone(f"HTTP {status}")
            if status in (200, 201) and text and "solvePoW" not in text and not CHALLENGE.search(text[:3000]):
                return text
            if CHALLENGE.search(text[:3000]):
                status = f"{status}, a bot check"
            err = f"HTTP {status}" + (" (empty)" if not text else "")
            if self.route in ("hk", "mo"):
                self.rotate()
                continue
            break
        raise Blocked(f"{url[:100]}: {err}")


CHALLENGE = re.compile(r"<title>\s*(?:Just a moment|Attention Required|One moment, please|Checking your browser)", re.I)


class BrowserClient(Client):
    """a forum on the "browser" route (Cloudflare's managed challenge on every other route): pages loaded in one real
    Chrome (patchright under Xvfb, the CronJob forums-browser), the challenge's checkbox ticked when it stalls
    (browser_fetch.cleared), no pictures, fonts or media (nomedia.block). One browser for the whole run"""
    _pw = _ctx = None

    @classmethod
    def page(cls):
        if cls._ctx is None:
            from patchright.sync_api import sync_playwright
            import nomedia
            cls._pw = sync_playwright().start()
            import tempfile  # a fresh profile each run: a kept one got an empty page on the next run (AV100fun, 2026-10-20)
            cls._dir = tempfile.mkdtemp(prefix="forum-browser-")
            cls._ctx = cls._pw.chromium.launch_persistent_context(cls._dir, channel="chrome", headless=False, no_viewport=True)
            nomedia.block(cls._ctx)
        return cls._ctx.pages[0] if cls._ctx.pages else cls._ctx.new_page()

    @classmethod
    def close(cls):
        if cls._ctx is not None:
            cls._ctx.close()
            cls._pw.stop()
            cls._ctx = cls._pw = None
            import shutil
            shutil.rmtree(cls._dir, ignore_errors=True)

    def fetch(self, url, data=None):
        import browser_fetch
        self._pace()
        pg = self.page()
        r = pg.goto(url, wait_until="domcontentloaded", timeout=90000)
        if not browser_fetch.cleared(pg):
            return 403, ""
        if r is not None and not r.ok and pg.url == url:
            r = pg.goto(url, wait_until="domcontentloaded", timeout=90000)  # the page after the challenge
        ctype = (r.headers.get("content-type", "") if r is not None else "")
        if r is not None and "json" in ctype:
            return r.status, r.text()
        return 200, pg.content()


def pow_cookies(text):
    """eyny's 瀏覽器安全檢查 page: sha256(challenge|ts|nonce) with `diff` leading zeros → its three cookies"""
    ch = re.search(r'var challenge = "(\w+)"', text).group(1)
    ts = re.search(r'var ts = "(\d+)"', text).group(1)
    diff = int(re.search(r"var diff = (\d+)", text).group(1))
    pre = re.search(r'document\.cookie = "(\w+)_n="', text).group(1)
    n = 0
    while not hashlib.sha256(f"{ch}|{ts}|{n}".encode()).hexdigest().startswith("0" * diff):
        n += 1
    return {f"{pre}_n": str(n), f"{pre}_ts": ts, f"{pre}_ch": ch}


# ---------------- reading a thread ----------------

def ppp_of(f):
    return int(corpus.kv_get(f"forums:{f['key']}:ppp") or f.get("ppp") or 10)


def read(cl, f, groups, since=0, allowed=MAX_PAGES):
    """read a thread from post number since+1 → (meta, posts, complete, requests). Discuz's printable view first
    (posts 1-100 in one request) while since < first_max, then the paged view from the page of post since+1"""
    meta, posts, reqs = {}, [], 0
    first_max = int(f.get("first_max") or FIRST_MAX)
    page = None
    if f.get("first") and since < first_max:
        m, ps = PARSERS[f["first_parser"]](cl.get(fill(f["first"], groups)), 1, first_max)
        reqs += 1
        meta.update({k: v for k, v in m.items() if v and k not in ("pages", "paged")})
        posts += ps
        if m.get("paged"):  # page 1 of the paged view: go on from page 2
            if (m.get("pages") or 1) <= 1:
                return meta, posts, True, reqs
            since = max([since] + [p["floor"] for p in ps])
        elif len(ps) < first_max:
            return meta, posts, True, reqs
        else:
            since = max(since, len(ps))
    ppp = ppp_of(f)
    page = max(since - int(f.get("offset") or 0), 0) // ppp + 1
    last = page
    while reqs < allowed:
        m, ps = PARSERS[f["parser"]](cl.get(fill(f["page"], groups, page, ppp)), page, ppp)
        reqs += 1
        meta.update({k: v for k, v in m.items() if v and k != "pages"})
        last = m.get("pages") or page
        if last > page and len(ps) > 1 and f["parser"] in ("discuz",) and len(ps) != ppp:  # learn the forum's page size
            corpus.kv_set(f"forums:{f['key']}:ppp", len(ps))
            ppp = len(ps)
        posts += ps
        if page >= last or not ps:
            return meta, posts, True, reqs
        page += 1
    return meta, posts, False, reqs


PLACEHOLDER = re.compile(r"^(?:forum|viewthread|index|showthread|thread|read)\.php$|^thread [0-9]+\b|^[0-9]+$", re.I)


def placeholder(title, url):
    """a title the listing made up: 〔連結〕…, a URL, a script name (forum.php), a sitemap slug (thread 3887450 …), or
    every word of it in the URL"""
    import links
    t = (title or "").strip()
    if not t or links.linkish(t) or PLACEHOLDER.search(t):
        return True
    if t.endswith(("...", "…")):  # a listing that cut the title short
        return True
    flat = re.sub(r"[\s\-_./]+", "", urllib.parse.unquote(url or "")).lower()
    return len(t) >= 3 and re.sub(r"[\s\-_./]+", "", t).lower() in flat


def file_thread(f, post, meta, posts, st):
    """the opening post into the thread's row (text; title when the listing's is a placeholder; author, time, the
    reply count) and every reply after st["read"] a comment row → new reply rows"""
    first = next((p for p in posts if p["floor"] == 1), None)
    title = meta.get("title")
    if first:
        own = placeholder(post["title"], post["url"])
        eng = {"replies": meta["replies"]} if isinstance(meta.get("replies"), int) else None
        corpus.set_content(post["id"], title if own and title else None, first["text"] or title or post["title"], eng)
        if first.get("author"):
            corpus.db().execute("UPDATE posts SET author=%s WHERE id=%s AND author IS NULL", (first["author"][:200], post["id"]))
        if first.get("published"):
            corpus.set_published(post["id"], first["published"].isoformat(timespec="seconds"))
    item = {"sid": post["sid"], "source": post["source"], "section": post["section"] or meta.get("board"),
            "title": post["title"], "url": post["url"]}
    comments = [{"id": p["floor"], "author": p.get("author"), "text": p["text"], "likes": p.get("likes"),
                 "published": p["published"].isoformat(timespec="seconds") if p.get("published") else None}
                for p in posts if p["floor"] > max(st["read"], 1) and p.get("text")]
    total = meta.get("replies")
    if total is None and posts:
        total = max(p["floor"] for p in posts) - 1
    new = corpus.add_comments(item, comments, total=total) if comments or total is not None else []
    return new


def state(forum, tid):
    r = corpus.db().execute("SELECT * FROM forum_threads WHERE forum=%s AND tid=%s", (forum, tid)).fetchone()
    return dict(r) if r else None


def save(forum, tid, st):
    corpus.db().execute("""INSERT INTO forum_threads (forum, tid, url, post_id, read, total, status, every, next_at, last_new,
                                                     at, tries, error)
                           VALUES (%(forum)s, %(tid)s, %(url)s, %(post_id)s, %(read)s, %(total)s, %(status)s, %(every)s,
                                   %(next_at)s, %(last_new)s, %(at)s, %(tries)s, %(error)s)
                           ON CONFLICT (forum, tid) DO UPDATE SET url=excluded.url, post_id=excluded.post_id,
                             read=excluded.read, total=excluded.total, status=excluded.status, every=excluded.every,
                             next_at=excluded.next_at, last_new=excluded.last_new, at=excluded.at, tries=excluded.tries,
                             error=excluded.error""", dict(st, forum=forum, tid=tid))


def thread(cl, f, post, groups, deadline):
    """read one thread (its new posts) and file them → (new reply rows, requests). Two-pass: a first read marks it
    captured (twopass), a complete re-read fetched; a failure still marks a first read captured (the score never
    waits on a broken forum)"""
    tid = "/".join(groups[k] for k in sorted(groups))
    st = state(f["key"], tid) or {"url": post["url"], "post_id": post["id"], "read": 0, "total": None, "status": "new",
                                  "every": None, "next_at": None, "last_new": None, "at": None, "tries": 0, "error": None}
    capture = st["read"] == 0
    if capture:
        twopass.reading(post["url"])
    t = now()
    try:
        allowed = max(1, min(MAX_PAGES, int((deadline - time.time()) / max(cl.gap, 0.5))))
        meta, posts, complete, reqs = read(cl, f, groups, st["read"], allowed)
    except resi.ProxyOut:
        raise  # IPRoyal out of traffic: a pause, the thread keeps its place (no try, no back-off); the run stops
    except (NoAccess, Gone) as e:
        st.update(status="noaccess" if isinstance(e, NoAccess) else "gone", error=str(e)[:200], at=t, next_at=None)
        save(f["key"], tid, st)
        if capture:
            twopass.captured(post["url"])
        return 0, cl.requests
    except Exception as e:
        st.update(status="error", error=repr(e)[:200], at=t, tries=st["tries"] + 1,
                  next_at=t + timedelta(seconds=REREAD_MIN * min(2 ** st["tries"], 16)))
        if st["tries"] >= 6:
            st["status"] = "done"
        save(f["key"], tid, st)
        if capture and (st["tries"] >= 2 or not isinstance(e, (Blocked, resi.ProxyOut))):
            twopass.captured(post["url"])
        raise
    new = file_thread(f, post, meta, posts, st)
    top = max([p["floor"] for p in posts] + [st["read"]])
    fresh = [p for p in posts if p["floor"] > st["read"]]
    found = bool(fresh) and not capture  # a re-read that found new posts (the first read finds the whole thread)
    latest = max([p["published"] for p in fresh if p.get("published")], default=None)
    st.update(read=top, at=t, error=None, tries=0, post_id=post["id"])
    if complete:
        st["total"] = top
        st["every"] = REREAD_MIN if found or not st["every"] else min(st["every"] * 2, REREAD_MAX)
        if fresh:  # the thread's newest post: its own time, else when it was found
            st["last_new"] = min(latest or t, t)
        st["last_new"] = st["last_new"] or t
        quiet = t - st["last_new"]
        st["status"] = "done" if quiet > timedelta(days=FOLLOW_DAYS) else "follow"
        st["next_at"] = None if st["status"] == "done" else t + timedelta(seconds=st["every"])
        (twopass.captured if capture else twopass.fetched)(post["url"])
    else:
        st.update(status="reading", next_at=t)  # the rest in the next run
    save(f["key"], tid, st)
    return len(new), reqs


def candidates(forums, days=NEW_DAYS):
    """{forum: [(post, groups)]}: the posts of the last `days` whose URL is a thread of a forum read here"""
    cut = (now() - timedelta(days=days)).isoformat(timespec="seconds")
    rx = "|".join(twopass.forum_units(forums=forums))
    out = {}
    if not rx:
        return out
    for r in corpus.db().execute(f"""SELECT id, url, sid, source, section, title, published, seen FROM posts
                                     WHERE seen >= %s AND thread IS NULL AND url ~ %s ORDER BY id DESC""", (cut, rx)):
        f, g = match(forums, r["url"])
        if f:
            out.setdefault(f["key"], []).append((dict(r), g))
    return out


def forum_run(f, posts, deadline):
    """one forum's pass: new threads (newest first), then the second scorings due, then the followed threads due"""
    key = f["key"]
    cl = (BrowserClient if f.get("route") == "browser" else Client)(f)
    cl.deadline = deadline
    by_tid, order = {}, []
    for p, g in posts:
        tid = "/".join(g[k] for k in sorted(g))
        if tid not in by_tid:  # one thread filed under several URLs: read under its first (newest) post's
            by_tid[tid] = (p, g)
    known = {r["tid"]: dict(r) for r in corpus.db().execute(
        "SELECT * FROM forum_threads WHERE forum=%s AND tid = ANY(%s)", (key, list(by_tid)))} if by_tid else {}
    new = [t for t in by_tid if t not in known or known[t]["status"] == "new"]
    due2 = set()
    for u in twopass.due_for_fetch(f"%{f['hosts'][0]}%"):
        ff, g = match({key: f}, u)
        if ff:
            due2.add("/".join(g[k] for k in sorted(g)))
    t = now()
    follow = [r["tid"] for r in corpus.db().execute(
        """SELECT tid FROM forum_threads WHERE forum=%s AND status IN ('follow', 'reading', 'error') AND next_at <= %s
           ORDER BY next_at LIMIT 3000""", (key, t))]
    order = list(dict.fromkeys(new + [x for x in due2 if x in known] + follow))
    stats = {"threads": 0, "new_replies": 0, "requests": 0, "errors": 0, "error": None, "noaccess": 0}
    fails = 0
    for tid in order:
        if time.time() > deadline:
            break
        if tid in by_tid:
            p, g = by_tid[tid]
        else:
            st = known.get(tid) or state(key, tid)
            r = corpus.db().execute("SELECT id, url, sid, source, section, title, published, seen FROM posts WHERE id=%s",
                                    (st["post_id"],)).fetchone() if st and st.get("post_id") else None
            if not r:
                continue
            p = dict(r)
            ff, g = match({key: f}, p["url"])
            if not ff:
                continue
        before = cl.requests
        try:
            n, _ = thread(cl, f, p, g, deadline)
            stats["threads"] += 1
            stats["new_replies"] += n
            fails = 0
            st = state(key, tid)
            if st and st["status"] == "noaccess":
                stats["noaccess"] += 1
        except resi.ProxyOut as e:
            stats.update(error=f"proxy out of traffic: {e}"[:300], off=resi.STATUS)
            break
        except Exception as e:  # noqa: BLE001: one thread's failure; FAILS_STOP in a row stop the forum's run
            stats["errors"] += 1
            stats["error"] = f"{p['url'][:90]}: {e!r}"[:300]
            print(key, p["url"][:90], repr(e)[:200], flush=True)
            fails += 1
            if fails >= FAILS_STOP:
                break
        finally:
            stats["requests"] += cl.requests - before
    stats["pending"] = max(len(order) - stats["threads"] - stats["errors"], 0)
    stats["ok"] = stats["errors"] == 0 or stats["threads"] > stats["errors"]
    return stats


def mb_today(f):
    if f.get("route") not in ("hk", "mo") and f.get("fallback") not in ("hk", "mo"):
        return None
    return round(float(corpus.kv_get(f"proxy:bytes:{resi.site('https://' + f['hosts'][0])}:{now():%Y-%m-%d}", 0)) / 1e6, 2)


def run(only=None, forums=None, deadline=None, browser=False):
    """one pass over every forum (or `only`) in parallel → {forum: status}; each status into kv forums:<forum>:status.
    browser=True: the forums on the "browser" route, one after another in one Chrome (CronJob forums-browser)"""
    schema()
    forums = forums or load()
    on = {k: f for k, f in forums.items() if not f.get("off") and (f.get("route") == "browser") == browser
          and (not only or k in only)}
    deadline = deadline or time.time() + RUN_SECONDS
    posts = candidates(on)
    res = {}

    def one(f):
        t0 = time.time()
        try:
            s = forum_run(f, posts.get(f["key"], []), deadline)
        except Exception as e:  # noqa: BLE001
            s = {"ok": False, "error": repr(e)[:300], "threads": 0, "new_replies": 0}
        s.update(at=now().isoformat(timespec="seconds"), seconds=round(time.time() - t0), name=f["name"],
                 route=f.get("route", "direct"), proxy_mb_today=mb_today(f), candidates=len(posts.get(f["key"], [])))
        if not s.get("threads") and not s.get("errors") and not s.get("error"):
            prev = corpus.kv_get(f"forums:{f['key']}:status") or {}
            s["last_read"] = prev.get("last_read") if not s.get("threads") else s["at"]
        else:
            s["last_read"] = s["at"] if s.get("threads") else (corpus.kv_get(f"forums:{f['key']}:status") or {}).get("last_read")
        corpus.kv_set(f"forums:{f['key']}:status", s)
        return f["key"], s

    if browser:  # one browser, used from this thread only
        try:
            for f in on.values():
                k, s = one(f)
                res[k] = s
        finally:
            BrowserClient.close()
    else:
        with ThreadPoolExecutor(max(1, len(on))) as ex:
            for k, s in ex.map(one, on.values()):
                res[k] = s
    for k, f in forums.items():  # the forums not read: why, on the monitor
        if f.get("off") and (not only or k in only):
            corpus.kv_set(f"forums:{k}:status", {"ok": None, "off": f.get("off") or f"route {f.get('route')}",
                                                 "name": f["name"], "at": now().isoformat(timespec="seconds")})
    return res


def statuses(forums=None):
    """every forum's last status: {forum: status} (the monitor's Forum replies card)"""
    forums = forums or load()
    return {k: corpus.kv_get(f"forums:{k}:status") for k in forums}


def test(url):
    forums = load()
    f, g = match(forums, url)
    if not f:
        sys.exit(f"no forum of forum_threads.json matches {url}")
    cl = (BrowserClient if f.get("route") == "browser" else Client)(f)
    try:
        meta, posts, complete, reqs = read(cl, f, g, 0, 5)
    finally:
        BrowserClient.close()
    print(json.dumps({"forum": f["key"], "groups": g, "meta": meta, "complete": complete, "requests": reqs}, ensure_ascii=False))
    for p in posts[:40]:
        print(p["floor"], p.get("published"), p.get("author"), "|", (p["text"] or "")[:150].replace("\n", " / "))


def main(argv):
    if argv[:1] == ["run"]:
        with corpus.lock("forums-browser" if "--browser" in argv else "forums", wait=False) as got:
            if not got:
                print("another forum_fetch run is in progress", flush=True)
                return
            br = "--browser" in argv
            for k, s in run([a for a in argv[1:] if a != "--browser"] or None, browser=br).items():
                print(k, json.dumps(s, ensure_ascii=False, default=str), flush=True)
    elif argv[:1] == ["test"] and len(argv) == 2:
        test(argv[1])
    elif argv[:1] == ["status"]:
        for k, s in statuses().items():
            print(k, json.dumps(s, ensure_ascii=False, default=str))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
