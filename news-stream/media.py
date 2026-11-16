#!/usr/bin/env python3
"""Video and audio inside articles and posts, as text (Deyao, 2026-10-14): every clip an article or post carries is
found when its page is read, transcribed once, and its transcript joins the post's search text and the scorer's input.

    python3 media.py run                  the transcriber (Deployment `transcribe`): every copy transcribes the
                                          queue, newest first, each clip claimed in the database (a deploy's new pod
                                          works at once beside the old one finishing its clips); one copy at a time
                                          scans pages (the Postgres lock 'yyc transcribe')
    python3 media.py detect <url>         the clips a page carries (fetches the page; writes nothing)
    python3 media.py test <media url>     transcribe one audio/video URL and print the transcript (writes nothing;
                                          costs the model call)
    python3 media.py stats                the queue: clips per status and host, minutes, spend

Transcription off (flagd flag `transcription`, off since 2026-10-20, Deyao: cost): no audio is fetched and nothing
goes to the model; queued clips stay queued (transcribed once it is on again). The worker still reads each queued
YouTube clip's own subtitles once (captions_one: metadata + the caption file through the ISP route, no model), kept in
media.captions with captions_at, and they are the post's transcript until the model's comes.

Detection (detect(url, page), per host; the rules come from saved real pages, tests/test_media.py): TVB (the page's
__NUXT_DATA__ article with the post's id: video_url, vod_video_id; a geo-restricted clip has no URL from outside Hong
Kong, so its page is read again through RESI_PROXY, Hong Kong residential, when it is transcribed), Now (the post's
newsId with videoStatus 2 → news-videos.now.com/nownews/<id>/hls/<id>.m3u8), 有線 (data-url m3u8), RTHK (the article's
own newsstatic videos/*.mp4 and audios/*.mp3), 星島 / 鳳凰網 (ld+json VideoObject contentUrl; 鳳凰網's must be the page's
own @id), 點新聞 / 大公文匯網 / 文匯網 (the article's storyResource m3u8 on dams.dotdotnews.com), 香港01 (the article's
own video: self-hosted m3u8 or YouTube, and its YouTube blocks), 商報 (img.hkcd.com/video mp4), 紫荊 (the player's
res.bauhk.com …/MEDIA/VIDEO mp4), The Standard (the article object's video_path), 政府新聞網 (the article's own
videos/*.mp4), on.cc (the article's vid → video-cdn.on.cc m3u8), 巴士的報 (video.js players' mp4 in the body), and on
every page YouTube embeds (iframe src / data-src, SCMP's body JSON) and Facebook video plugins (am730). HKET's player
carries its programmes (美股FocUS…), repeated across unrelated articles: each programme video becomes a post of its own
(video.hket.com/video/<id>, title and date from the player) instead of joining the article. Text-to-speech readouts of
the article are never taken (SCMP articleSpeeches, 文匯 text2Audio, 巴士的報 article-audio): no detector reads them.

Where pages come from: fulltext_now.py and fulltext_fetch.py hand over the HTML they read (seen_page, via
cache_fetch.last_page()); posts whose page they never read (the feed already had the text: TVB, Now, RTHK…) are read
here (scan_once), one request per host at a time, HOST_GAP apart. Each post is scanned once (table media_scan).

Queue: table media, one row per clip, keyed by the provider's clip id (tvb:, now:, dams:, hkhl:, yt:, fb:, oncc:,
hket:) or else the media URL without its query, so a clip on many articles (the same dams clip on 點新聞, 大公文匯網 and
文匯) is transcribed once; post_media links clips to posts. Newest first (the newest post carrying the clip), up to
TRIES failures each (HTTP 429 / 5xx from the media host wait RETRY_AFTER and do not count).

YouTube through YYC's IPRoyal ISP IP only (Deyao, 2026-10-16: the transcriber's YouTube audio used 5.19 GB of IPRoyal
yycstream, paid per GB, in 6 hours; YouTube refuses the server's IP; then "use the ISP IP for all YouTube" and "run
the transcription for all now"). Every YouTube clip is transcribed from its audio like any other clip, read through the
route env MEDIA_ROUTES names for family youtube (JSON {family: env name}, here {"youtube": "ISP_PROXY"}: the ISP IP
81.181.135.61, IPRoyal order #85121454, Secret isp-proxy). The ISP IP serves one client at a time (a second answers
"Tunnel connection failed: 403"), so every YouTube fetch holds youtube_lock: a process lock and the Postgres advisory
lock 'yyc youtube' (broadcast_fetch.py takes it too); the model's work runs outside it. One locked session per clip:
yt-dlp -J, the subtitles, the audio-only download reusing the metadata (--load-info-json; a 403 tried again), its
bytes measured on the wire (resi.metered_url) into kv isp:bytes:<day> (resi.count_isp; the monitor shows them against
the IP's 100 GB / 30 days Fair Usage allowance), never yycstream's proxy:bytes. The subtitles are kept beside the
transcript (media.captions / captions_model): the uploader's captions in the video's language (「YouTube 上載者字幕」),
else YouTube's auto-generated track of the video's language (its `<lang>-orig` track: the auto-translated ones answer
429; 「YouTube 自動字幕」). A live or upcoming video waits (HostBusy); a private or removed one is given up at once.

No audio through RESI_PROXY: every other clip is transcribed only when its audio downloads directly from the server; a
TikTok / Instagram / Facebook clip yt-dlp can't read from the server's IP, a TVB geo-restricted clip (no URL outside
Hong Kong) and an HLS stream answering only Hong Kong IPs are status no_direct (「無法直接下載，未轉錄」), not retried,
unless MEDIA_ROUTES names a route for that family (tvb, hls, or the site: facebook, instagram, tiktok…).

Transcription: the audio only (ffmpeg, mono 16 kHz, MP3 64 kbit/s; yt-dlp for social clips), cut at the quietest point into pieces (chunks(): under 30 s for a speech-to-text model,
whose provider otherwise cuts every 30 s mid-word; about a minute for a chat model), each piece sent to MODEL on OpenRouter, CONCURRENCY pieces at a time. MODEL is Qwen3-ASR-1.7B (Deyao,
2026-10-15: one cheap model; benchmark of 14 Oct on 10 HK news clips: CER 6.22 %, 59/71 names, USD 0.00045 a minute as
billed), a speech-to-text model on OpenRouter's /audio/transcriptions (the MP3, temperature 0, no prompt: the endpoint
takes none, and the model detects the language); its answer comes with stray Simplified characters (消委会), so it is
converted with OpenCC s2hk. A chat model with audio input (Gemini) still works through the same setting, on
/chat/completions with the benchmark's prompt "verbatim" (temperature 0, max_tokens 16000, reasoning low) and t2hk.
The transcript is the pieces in order, each line starting with its start time [mm:ss] (about once a minute), in Hong
Kong Traditional characters; spoken Cantonese stays as spoken. A piece whose answer is longer than any speech could be
(MAX_CPS characters a second: the model ran on, live once with 67,933 characters for 56 s) is asked again
RETRIES_LONG times, then stands as LOST. CLIPS clips are in hand at once. Spend: ai_spend / ai_runs purpose `transcribe`
(OpenRouter's charged cost per call); the clip's own total is media.cost, its model media.model (the item page labels
a model-made transcript 「AI 轉錄」 with the model's name).

The model is one setting: env TRANSCRIBE_MODEL (default qwen/qwen3-asr-1.7b). No budget logic (Deyao, 2026-10-14:
budgets are safety stops): when OpenRouter refuses for credit or the key's limit, ai.py's stop applies (ai.limit_hit:
kv openrouter_limit, shown on the monitor, one Discord message to Deyao through notify.py); the clip stays queued, no
try counted, and the transcriber tries again every LIMIT_WAIT seconds; the first call that works clears it.
"""
import base64, html, json, os, re, signal, socket, subprocess, sys, tempfile, threading, time, urllib.error, urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import corpus
import resi

HKT = corpus.HKT
MODEL = os.environ.get("TRANSCRIBE_MODEL", "qwen/qwen3-asr-1.7b")
PURPOSE = "transcribe"
# the benchmark's prompt "verbatim", word for word (asr-benchmark/tools/run_openrouter.py PROMPTS["verbatim"])
PROMPT = ("This is a clip of Hong Kong news audio (TV or radio). Transcribe it verbatim, everything that is said, "
          "in the language spoken: Cantonese in Traditional Chinese characters exactly as spoken (use colloquial "
          "Cantonese characters such as 嘅、咗、唔、係、佢 when the speaker says them), Mandarin in Traditional Chinese, "
          "English in English. Write names of people, places and organisations correctly. Output only the transcript "
          "text with punctuation, no timestamps, no speaker labels, no commentary.")
SETTINGS = {"max_tokens": 16000, "temperature": 0, "reasoning": {"effort": "low"}}
CHUNK, CHUNK_MIN, CHUNK_MAX = 60, 45, 75   # seconds per piece sent to a chat model; cut at the quietest point in range
STT_CHUNKS = (25, 18, 29)  # a speech-to-text model: under 30 s (its provider cuts longer pieces every 30 s, mid-word)
CONCURRENCY = 4        # pieces of one clip in flight at once
CLIPS = 6              # clips in hand at once (each with its CONCURRENCY pieces)
MAX_CPS = 15           # characters per second of audio no real speech reaches (Cantonese ~5, fast English ~15)
RETRIES_LONG = 2       # a piece whose answer runs past that is asked again this many times, then left out
LOST = "（此段未能轉寫）"  # what stands in the transcript for a piece left out
SR = 16000
TRIES = 3              # failures before a clip is given up (status 'failed')
RETRY_AFTER = 900      # seconds before a clip whose host answered 429 / 5xx is tried again (not counted as a try)
GIVE_UP = timedelta(days=2)  # a clip still refused by its host (or a YouTube video still live) after this long is given up
DOWNLOAD_TRIES = 3     # YouTube audio downloads through the ISP IP: a 403 is tried again (6 of 66 needed one, 2026-10-16)
LIMIT_WAIT = 600       # seconds between tries while OpenRouter refuses for credit / the key's limit
MAX_SECONDS = 4 * 3600  # longer = a live stream or a broken file, not a news clip
SCAN_DAYS = 2          # posts published this recently are scanned for clips (the history: Deyao decides, README)
SCAN_SETTLE = timedelta(minutes=5)  # a new post's page is left to fulltext_now.py this long first
HOST_GAP = 1.0         # seconds between two page reads on one site (registered domain)
BUSY_WAIT = 60         # seconds a site that answered 429 / 5xx is left alone by the scan
SCAN_TRIES = 3         # reads of a post's page before it is given up (a gone page: at once)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
STOP = threading.Event()


class HostBusy(Exception):
    """the media host answered 429 or 5xx: try later, not counted as a try"""


class ProxyOut(HostBusy, resi.ProxyOut):
    """the residential proxy refused for credit (402: IPRoyal's balance ran out, live 2026-10-15): a safety stop, the
    clip waits RETRY_AFTER however old it is, no try counted, until the balance is topped up"""


class NoDirect(Exception):
    """the clip's audio can't be read directly from the server (and no MEDIA_ROUTES route is set for it): status
    no_direct, not retried; never read through RESI_PROXY (Deyao, 2026-10-16)"""


class Gone(Exception):
    """a private, removed or members-only YouTube video: given up at once (status failed)"""


PROXY_OUT = resi.OUT_RE  # every client's wording of IPRoyal's 402 (resi.py), curl's 'CONNECT tunnel failed, response 402' too


# ---- detection ----

def _host(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def _nokey(url):
    """the URL without its query or fragment (the key of a clip with no provider id)"""
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{u.netloc}{u.path}"


def clip(key, url, kind="direct", **kw):
    return dict(key=key, url=url, kind=kind, **kw)


def _ld(page):
    """every JSON-LD object of the page (flattened @graph)"""
    out = []
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            d = json.loads(m.group(1).strip())
        except ValueError:
            continue
        stack = [d]
        while stack:
            o = stack.pop()
            if isinstance(o, list):
                stack.extend(o)
            elif isinstance(o, dict):
                out.append(o)
                stack.extend(v for k, v in o.items() if k == "@graph" or isinstance(v, (list, dict)))
    return out


def _video_objects(page):
    return [o for o in _ld(page) if o.get("@type") == "VideoObject" and isinstance(o.get("contentUrl"), str)]


def tvb(url, page):
    m = re.search(r"/(\d{6,})", urllib.parse.urlsplit(url).path)
    d = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not (m and d):
        return []
    aid = int(m.group(1))
    data = json.loads(d.group(1))

    def val(a, k):
        v = a.get(k)
        return data[v] if isinstance(v, int) and 0 <= v < len(data) else v
    for a in data:
        if isinstance(a, dict) and "video_url" in a and val(a, "id") == aid:
            vurl, vid, geo = val(a, "video_url"), val(a, "vod_video_id"), val(a, "is_geographical_restriction")
            if vurl:
                return [clip("tvb:" + (vid or _nokey(vurl)), vurl)]
            if vid and geo:  # no URL from outside Hong Kong: the page again through the HK proxy at transcription
                return [clip("tvb:" + vid, url, "tvb_geo")]
            return []
    return []


def now(url, page):
    m = re.search(r"newsId=(\d+)", url)
    if not m:
        return []
    nid = m.group(1)
    st = re.search(r'"newsId":"%s"[^{}]*?"videoStatus":"(\d+)"' % nid, page)
    if not st or st.group(1) != "2":
        return []
    return [clip("now:" + nid, f"https://news-videos.now.com/nownews/{nid}/hls/{nid}.m3u8")]


def icable(url, page):
    return [clip(_nokey(u), u) for u in dict.fromkeys(
        html.unescape(x) for x in re.findall(r'data-url="(https?://vod\.i-cable\.com/[^"]+?\.m3u8)"', page))]


def rthk(url, page):
    m = re.search(r"/k2/(\d+)", url)
    if not m:
        return []
    found = re.findall(r"https://newsstatic\.rthk\.hk/(?:videos|audios)/[vm]file_%s_\d+_\d+\.(?:mp4|mp3)" % m.group(1), page)
    return [clip(u, u) for u in dict.fromkeys(found)]


def stheadline(url, page):
    out = []
    for o in _video_objects(page):
        u = o["contentUrl"]
        m = re.match(r"https://video\.hkhl\.hk/([0-9a-f]{16,})/", u)
        if m:
            out.append(clip("hkhl:" + m.group(1), u))
    return list({c["key"]: c for c in out}.values())


def ifeng(url, page):
    base = _nokey(url)
    return [clip(_nokey(o["contentUrl"]), o["contentUrl"]) for o in _video_objects(page)
            if str(o.get("@id", "")).startswith(base + "#")]


def dams(url, page):
    """點新聞, 大公文匯網, 文匯網: the article's own storyResource (the clip's 24-hex id on dams is its key)"""
    m = re.search(r"/(AP[0-9a-f]{20,})\.html", url)
    if not m:
        return []
    out = []
    for r in re.finditer(r"'storyResource':\{'info':'(https://dams\.dotdotnews\.com/dams/[a-z]+/video/[^']+?\.m3u8)'", page):
        if m.group(1) not in page[max(0, r.start() - 8000):r.start()]:
            continue  # another article's resource (a related list)
        u = r.group(1)
        vid = re.search(r"/video/\d{6}/\d\d/([0-9a-f]{24})/", u)
        out.append(clip("dams:" + vid.group(1) if vid else _nokey(u), u))
    return list({c["key"]: c for c in out}.values())


def hk01(url, page):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    aid = re.search(r"/(\d{5,})/", urllib.parse.urlsplit(url).path)
    if not (m and aid):
        return []
    stack, art = [json.loads(m.group(1))], None
    while stack and art is None:
        o = stack.pop()
        if isinstance(o, dict):
            if str(o.get("articleId")) == aid.group(1) and "blocks" in o:
                art = o
            else:
                stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    if not art:
        return []
    out = []
    for v in [art.get("video")] + [b for b in art.get("blocks") or [] if isinstance(b, dict) and b.get("blockType") == "video"]:
        if not isinstance(v, dict):
            continue
        if v.get("type") == "youtube" and v.get("videoId"):
            out.append(youtube_clip(v["videoId"]))
        elif v.get("videoSrc"):
            out.append(clip("hk01:" + str(v.get("videoId") or _nokey(v["videoSrc"])), v["videoSrc"]))
    return out


def hkcd(url, page):
    return [clip(_nokey(u), u) for u in dict.fromkeys(re.findall(r"https?://img\.hkcd\.com/video/[^\"'\s<>]+?\.mp4", page))]


def bauhinia(url, page):
    found = re.findall(r"https://res\.bauhk\.com/contentcloud/[^\"'\s<>&]+?/MEDIA/VIDEO/[^\"'\s<>&]+?\.mp4",
                       urllib.parse.unquote(html.unescape(page)))
    return [clip(_nokey(u), u) for u in dict.fromkeys(found)]


def standard(url, page):
    m = re.search(r"/article/(\d+)/", url)
    if not m:
        return []
    s = page.replace('\\"', '"')
    for u in re.finditer(r'"url":"(/[^"]*/article/%s/[^"]*)"' % m.group(1), s):
        seg = s[u.start():u.start() + 4000]
        nxt = seg.find('"url":"', 10)
        v = re.search(r'"video_path":"(https?://[^"]+)"', seg[:nxt] if nxt > 0 else seg)
        if v:
            return [clip(_nokey(v.group(1)), v.group(1))]
    return []


def newsgov(url, page):
    m = re.search(r"/((?:chi|eng)/\d{4}/\d\d/\d{8}/[^/]+?)\.html", url)
    if not m:
        return []
    found = re.findall(r"/%s/videos/[^\"'\s<>]+?\.mp4" % re.escape(m.group(1)), page)
    # the page's src answers 404 itself: its player (360p.js) plays it from /videodownload/<the same path>
    return [clip("https://www.news.gov.hk" + p, "https://www.news.gov.hk/videodownload" + p) for p in dict.fromkeys(found)]


def oncc(url, page):
    v = re.search(r'"vid"\s*:\s*"([A-Za-z0-9_-]+)"', page)
    t = re.search(r'"vcreateTime"\s*:\s*"(\d+)"', page)
    if not (v and t):
        return []
    ym = datetime.fromtimestamp(int(t.group(1)), HKT).strftime("%Y%m")
    return [clip("oncc:" + v.group(1), f"https://video-cdn.on.cc/Video/{ym}/{v.group(1)}.m3u8")]


def hket(url, page):
    """the player's programme videos: each a post of its own (program), not the article's"""
    out = []
    for tag in re.findall(r"<video\b[^>]*\bdata-m3u8=[^>]*>", page, re.S):
        a = {k: html.unescape(v) for k, v in re.findall(r'\bdata-([a-z0-9-]+)="([^"]*)"', tag)}
        vid, m3u8 = a.get("videoid"), a.get("m3u8")
        if not (vid and m3u8):
            continue
        out.append(clip("hket:" + vid, m3u8, program={
            "url": f"https://video.hket.com/video/{vid}", "title": a.get("videotitle") or a.get("videoprogram") or vid,
            "section": a.get("videoprogram"), "published": a.get("videopublishdate"), "sid": "hket_video",
            "source": "香港經濟日報"}))
    return list({c["key"]: c for c in out}.values())


def bastille(url, page):
    out = []
    for setup in re.findall(r'<video\b[^>]*\bdata-setup="([^"]*)"', page, re.S):
        try:
            d = json.loads(html.unescape(setup))
        except ValueError:
            continue
        for s in d.get("sources") or []:
            if isinstance(s, dict) and re.search(r"\.(mp4|m3u8)(\?|$)", s.get("src") or ""):
                out.append(clip(_nokey(s["src"]), s["src"]))
    return out


YT_HOSTS = ("youtube.com", "youtu.be")
YT_ID = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|live/|embed/)|youtu\.be/)([\w-]{11})")


def youtube_id(url):
    m = YT_ID.search(url or "")
    return m.group(1) if m else None


def youtube_clip(vid):
    return clip("yt:" + vid, f"https://www.youtube.com/watch?v={vid}", "proxy")


EMBED_YT = re.compile(r'(?:data-src|src)(?:\\?"\s*:\s*\\?"|\s*=\s*\\?["\'])https?://(?:www\.)?youtube(?:-nocookie)?\.com/embed/([\w-]{11})')
EMBED_FB = re.compile(r'(?:data-src|src)\s*=\s*\\?["\']https?://(?:www\.)?facebook\.com/plugins/video\.php\?([^"\'\\]+)')


def embeds(url, page):
    """YouTube embeds and Facebook video plugins in the page (iframes and SCMP's body JSON)"""
    out = [youtube_clip(v) for v in EMBED_YT.findall(page)]
    for q in EMBED_FB.findall(page):
        href = urllib.parse.parse_qs(html.unescape(q)).get("href", [""])[0]
        m = re.search(r"facebook\.com/(?:.*/videos/|reel/|watch/?\?v=)(\d+)", href)
        if m:
            out.append(clip("fb:" + m.group(1), href, "ytdlp"))  # from the server, the proxy only as a fallback
    return out


DETECTORS = (  # host suffix → detector (the page's own clips)
    ("news.tvb.com", tvb), ("news.now.com", now), ("i-cable.com", icable), ("news.rthk.hk", rthk),
    ("stheadline.com", stheadline), ("dotdotnews.com", dams), ("tkww.hk", dams), ("wenweipo.com", dams),
    ("hk01.com", hk01), ("hkcd.com", hkcd), ("bau.com.hk", bauhinia), ("thestandard.com.hk", standard),
    ("news.gov.hk", newsgov), ("ifeng.com", ifeng), ("on.cc", oncc), ("hket.com", hket),
    ("bastillepost.com", bastille))
EMBED_HOSTS = ("scmp.com", "am730.com.hk")  # only embeds (YouTube, Facebook)
SCAN_HOSTS = tuple(h for h, _ in DETECTORS) + EMBED_HOSTS + YT_HOSTS
NO_SCAN = ("invest.hket.com",)  # built in the browser (fulltext_fetch.RENDER_HOSTS): its HTML has no player


def detect(url, page):
    """→ the clips the page carries: [{key, url, kind (direct | proxy | tvb_geo), program?}], each key once"""
    h = _host(url)
    if h.endswith(YT_HOSTS):  # a YouTube post is its own clip (the same key as an article's embed of it)
        vid = youtube_id(url)
        return [youtube_clip(vid)] if vid else []
    out = []
    for suffix, fn in DETECTORS:
        if h == suffix or h.endswith("." + suffix):
            try:
                out += fn(url, page)
            except (ValueError, KeyError, TypeError) as e:
                print(f"media detect {suffix}: {e!r}"[:200], flush=True)
            break
    out += embeds(url, page)
    return list({c["key"]: c for c in out}.values())


# ---- the queue ----

def _now():
    return datetime.now(HKT).isoformat(timespec="seconds")


def record(post_id, url, page):
    """a post's page was read: file its clips (and HKET's programme posts) and mark the post scanned → clips found"""
    return file_clips(post_id, detect(url, page))


def file_clips(post_id, clips):
    """queue these clips for the post (a page's, or a platform's own video list: X) and mark it scanned → clips"""
    c = corpus.db()
    with c:
        r = c.execute("SELECT coalesce(published, seen) AS t FROM posts WHERE id=%s", (post_id,)).fetchone()
        t = r["t"] if r else _now()
        for cl in clips:
            owner, when = post_id, t
            if cl.get("program"):
                p = cl["program"]
                pub = p.get("published")
                if pub and re.fullmatch(r"\d{4}-\d\d-\d\d", pub):  # the player states only the day
                    pub = corpus.DateOnly.of(datetime.fromisoformat(pub))
                corpus.ingest([{**{k: p[k] for k in ("url", "title", "section", "sid", "source")}, "published": pub}])
                row = c.execute("SELECT id, coalesce(published, seen) AS t FROM posts WHERE url=%s", (p["url"],)).fetchone()
                if not row:
                    continue
                # its place in the queue: the programme's own date (the player shows days-old programmes on today's
                # articles)
                owner, when = row["id"], row["t"]
                # the programme's own page lists more programmes: it is not scanned (its clip is this one)
                c.execute("INSERT INTO media_scan (post_id, at, found) VALUES (%s, %s, 1) ON CONFLICT DO NOTHING",
                          (owner, _now()))
            c.execute("""INSERT INTO media (key, url, kind, host, status, first_seen, newest) VALUES (%s,%s,%s,%s,'queued',%s,%s)
                         ON CONFLICT (key) DO UPDATE SET newest = greatest(media.newest, excluded.newest)""",
                      (cl["key"], cl["url"], cl["kind"], _host(cl["url"]), _now(), when))
            c.execute("INSERT INTO post_media (post_id, key) VALUES (%s, %s) ON CONFLICT DO NOTHING", (owner, cl["key"]))
        c.execute("""INSERT INTO media_scan (post_id, at, found) VALUES (%s, %s, %s)
                     ON CONFLICT (post_id) DO UPDATE SET at=excluded.at, found=excluded.found""",
                  (post_id, _now(), len(clips)))
    for cl in clips:  # a clip transcribed before (another article): this post gets its transcript now
        r = c.execute("SELECT status FROM media WHERE key=%s", (cl["key"],)).fetchone()
        if r and r["status"] == "done":
            for p in c.execute("SELECT post_id FROM post_media WHERE key=%s", (cl["key"],)).fetchall():
                refresh_post(p["post_id"])
    return clips


def pending(post_ids):
    """the posts among post_ids still waiting for a transcript: a clip linked to it (post_media) is queued (not done,
    not given up) → set of ids. The signal that content may still come (a post with little text of its own)"""
    if not post_ids:
        return set()
    rows = corpus.db().execute("""SELECT DISTINCT pm.post_id FROM post_media pm JOIN media m ON m.key = pm.key
                                  WHERE pm.post_id = ANY(%s) AND m.status = 'queued'""", (list(post_ids),)).fetchall()
    return {r["post_id"] for r in rows}


def seen_page(post_id, url, page, pictures=True):
    """the full-text readers' hook: a page they read (never raises: their own work comes first). Its pictures go to
    images.py from the same HTML (pictures=False: images.py is the caller)"""
    if pictures and page:
        import images
        images.seen_page(post_id, url, page)
    if not page or not _host(url).endswith(SCAN_HOSTS) or _host(url).endswith(NO_SCAN):
        return
    try:
        record(post_id, url, page)
    except Exception as e:
        print("media: record failed:", url[:100], repr(e)[:160], flush=True)


def on():
    """transcription on (flagd flag `transcription`; off since 2026-10-20, Deyao: cost)"""
    import flags
    return flags.on("transcription")


def due(limit=1):
    """clips to transcribe, the newest post first (those another copy has in hand left out)"""
    return [dict(r) for r in corpus.db().execute(
        f"""SELECT * FROM media m WHERE status='queued' AND (next_try IS NULL OR next_try <= %s)
             AND (claimed_until IS NULL OR claimed_until < now()) AND {corpus.media_cond("m", "transcription")}
           ORDER BY newest DESC NULLS LAST, key LIMIT %s""", (_now(), limit))]


# clips in hand, claimed in the database: every transcriber copy works (a rollout's new pod at once, beside the old one
# finishing its clips), none takes a clip another has; a claim lasts LEASE seconds and is renewed while the clip is in
# hand, so a killed copy's clip goes back to the queue on its own
LEASE = 120
ME = f"{socket.gethostname()}:{os.getpid()}"
_inflight, _ilock = set(), threading.Lock()


def claim():
    """the newest due clip no copy has in hand → its row (now claimed by this copy), or None"""
    r = corpus.db().execute(
        f"""UPDATE media SET claimed_by=%s, claimed_until=now() + make_interval(secs => %s)
           WHERE key = (SELECT key FROM media m WHERE status='queued' AND (next_try IS NULL OR next_try <= %s)
                          AND (claimed_until IS NULL OR claimed_until < now()) AND {corpus.media_cond("m", "transcription")}
                        ORDER BY newest DESC NULLS LAST, key LIMIT 1 FOR UPDATE SKIP LOCKED)
           RETURNING *""", (ME, LEASE, _now())).fetchall()
    if not r:
        return None
    with _ilock:
        _inflight.add(r[0]["key"])
    return dict(r[0])


def claim_captions():
    """transcription off: the newest queued YouTube clip whose subtitles were not read yet → its row (claimed), or None"""
    r = corpus.db().execute(
        f"""UPDATE media SET claimed_by=%s, claimed_until=now() + make_interval(secs => %s)
           WHERE key = (SELECT key FROM media m WHERE status='queued' AND key LIKE 'yt:%%' AND captions_at IS NULL
                          AND (next_try IS NULL OR next_try <= %s) AND (claimed_until IS NULL OR claimed_until < now())
                          AND {corpus.media_cond("m", "transcription")}
                        ORDER BY newest DESC NULLS LAST, key LIMIT 1 FOR UPDATE SKIP LOCKED)
           RETURNING *""", (ME, LEASE, _now())).fetchall()
    if not r:
        return None
    with _ilock:
        _inflight.add(r[0]["key"])
    return dict(r[0])


def release(key):
    with _ilock:
        _inflight.discard(key)
    corpus.db().execute("UPDATE media SET claimed_by=NULL, claimed_until=NULL WHERE key=%s AND claimed_by=%s", (key, ME))


def renew_loop():
    """keep this copy's claims alive while their clips are in hand (until the process ends: a draining copy too)"""
    while True:
        time.sleep(LEASE / 4)
        with _ilock:
            keys = list(_inflight)
        if keys:
            try:
                corpus.db().execute("""UPDATE media SET claimed_until=now() + make_interval(secs => %s)
                                       WHERE key = ANY(%s) AND claimed_by=%s""", (LEASE, keys, ME))
            except Exception as e:  # noqa: BLE001: the database away for a moment; the next renewal is in time
                print("media: renewing claims:", repr(e)[:200], flush=True)


def refresh_post(post_id):
    """posts.transcript = the post's finished clips' transcripts, and for a clip still queued its YouTube subtitles
    (read while transcription is off) (the search text and the scorer read it)"""
    rows = corpus.db().execute("""SELECT CASE WHEN m.status='done' THEN m.transcript ELSE m.captions END AS t
                                  FROM post_media pm JOIN media m ON m.key = pm.key
                                  WHERE pm.post_id=%s AND ((m.status='done' AND m.transcript <> '')
                                                           OR (m.status='queued' AND m.captions <> ''))
                                  ORDER BY m.first_seen, m.key""", (post_id,)).fetchall()
    corpus.set_transcript(post_id, "\n\n".join(r["t"] for r in rows) or None)


def finish(key, transcript, seconds, cost, model, captions=None):
    """a clip transcribed; captions = (text, label model) of its YouTube subtitles, kept beside the transcript"""
    c = corpus.db()
    with c:
        c.execute("""UPDATE media SET status='done', transcript=%s, duration=%s, cost=%s, model=%s, done_at=%s, error=NULL,
                     captions=%s, captions_model=%s WHERE key=%s""",
                  (transcript, seconds, cost, model, _now(), captions[0] if captions else None,
                   captions[1] if captions else None, key))
    for r in c.execute("SELECT post_id FROM post_media WHERE key=%s", (key,)).fetchall():
        refresh_post(r["post_id"])


def final(key, status, err=None):
    """a clip that will get no transcript: no_direct (its audio can't be read directly) or failed (a YouTube video
    that is private or removed) → never tried again"""
    corpus.db().execute("UPDATE media SET status=%s, error=%s, next_try=NULL, done_at=%s WHERE key=%s",
                        (status, str(err)[:500] if err else None, _now(), key))


def fail(key, err, busy=False):
    """a failed try: TRIES of them give the clip up; a busy host waits RETRY_AFTER and is not counted (until GIVE_UP);
    the proxy out of credit (ProxyOut) waits however old the clip is"""
    c = corpus.db()
    m = c.execute("SELECT tries, first_seen FROM media WHERE key=%s", (key,)).fetchone()
    old = (m and m["first_seen"] and datetime.fromisoformat(m["first_seen"]) < datetime.now(HKT) - GIVE_UP
           and not isinstance(err, ProxyOut))
    if busy and not old:
        c.execute("UPDATE media SET error=%s, next_try=%s WHERE key=%s",
                  (str(err)[:500], (datetime.now(HKT) + timedelta(seconds=RETRY_AFTER)).isoformat(timespec="seconds"), key))
        return
    tries = (m["tries"] if m else 0) + 1
    c.execute("UPDATE media SET tries=%s, error=%s, status=%s, next_try=NULL WHERE key=%s",
              (tries, str(err)[:500], "failed" if tries >= TRIES or busy else "queued", key))


# ---- audio ----

# the smallest audio-only format first, never video: YouTube 600 (opus) / 599 (m4a) ~32 kbit/s, then 139 (48.8) before
# 249 (54.3; measured on one video: 709 KB vs 788 KB for 116 s); others (Facebook) their smallest audio. Over a
# configured route (AUDIO_ROUTES), which is paid traffic too
AUDIO_FORMAT = "600/599/139/249/worstaudio[abr<=96]/bestaudio[abr<=96]/worstaudio"
NO_PROXY_FORMAT = AUDIO_FORMAT + "/worst"  # TikTok has no audio-only format; read directly, so its smallest video


def routes():
    """env MEDIA_ROUTES: {family: name of the env var holding the proxy URL} → {family: proxy URL} for the ones set
    (empty: every clip's audio is read directly, or not at all). Never RESI_PROXY's per-GB pool"""
    try:
        r = json.loads(os.environ.get("MEDIA_ROUTES") or "{}")
    except ValueError:
        print("media: MEDIA_ROUTES is not JSON; no routes", flush=True)
        return {}
    if not isinstance(r, dict):
        return {}
    return {k: os.environ[v] for k, v in r.items() if v and v != "RESI_PROXY" and os.environ.get(v)}


def family(m):
    """the clip's route family: youtube, tvb (geo-restricted TVB), hls (kind proxy: a stream answering only Hong Kong
    IPs), else the site's short name (facebook, instagram, tiktok…)"""
    if str(m.get("key", "")).startswith("yt:") or youtube_id(m.get("url") or "") and _host(m.get("url") or "").endswith(YT_HOSTS):
        return "youtube"
    if m.get("kind") == "tvb_geo":
        return "tvb"
    if m.get("kind") == "proxy":
        return "hls"
    return resi.site(m.get("url") or "")


def route(m):
    return routes().get(family(m))


_ylock = threading.Lock()


@__import__("contextlib").contextmanager
def youtube_lock():
    """one YouTube client at a time on the ISP IP: this process's lock, then the Postgres advisory lock 'yyc youtube'
    (corpus.lock: its own connection, freed within corpus.LOCK_IDLE s if this process vanishes), shared with
    broadcast_fetch.py and sites_fetch.isp_lock"""
    with _ylock, corpus.lock("yyc youtube", app_name="yyc-youtube-lock"):
        yield


def run(cmd, timeout):
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace")[-600:]
        if PROXY_OUT.search(err):
            resi.mark(err[-300:])  # one record and one message per outage (resi.py)
            raise ProxyOut(err)
        if re.search(r"\b(429|50[0234])\b", err) and re.search(r"HTTP|Server returned", err):
            raise HostBusy(err)
        raise RuntimeError(f"{cmd[0]} failed: {err}")
    return p.stdout


def tvb_geo_url(page_url, proxy):
    """a geo-restricted TVB clip: its article page read through a Hong Kong route carries the video_url"""
    import sources
    page_url = urllib.parse.quote(page_url, safe=":/?&=%#+,;@!$'()*~[]")  # the path carries the Chinese headline
    page = sources.fetch(page_url, proxy=proxy, timeout=40)
    for _ in range(3):  # TVB answers a short URL with a meta refresh to the full one
        if "__NUXT_DATA__" in page:
            break
        m = re.search(r'url=(/tc/[^"]+)"', page)
        if not m:
            break
        page = sources.fetch("https://news.tvb.com" + urllib.parse.quote(m.group(1), safe="/%"), proxy=proxy, timeout=40)
    got = tvb(urllib.parse.unquote(page_url), page)
    if not got or got[0]["kind"] != "direct":
        raise RuntimeError("TVB: no video_url through the Hong Kong route")
    return got[0]["url"]


def fetch_audio(m, tmp):
    """→ a local 16 kHz mono WAV of the clip's audio, read directly from the server; never through RESI_PROXY. YouTube:
    youtube_clip_audio (the ISP route, locked). A clip that needs a Hong Kong IP (TVB's geo-restricted clips, kind
    proxy) is read only over its MEDIA_ROUTES route, else NoDirect; a social clip (kind ytdlp) yt-dlp can't read
    directly is NoDirect, or tried once over its family's route when one is set"""
    if family(m) == "youtube":
        return youtube_clip_audio(m["url"], tmp)["wav"]
    wav = os.path.join(tmp, "a.wav")
    src, rt = m["url"], route(m)
    needs_route = m["kind"] in ("tvb_geo", "proxy")
    if needs_route and not rt:
        raise NoDirect({"tvb": "TVB 的地區限制片段在香港以外沒有網址", "hls": "串流只回應香港 IP"}.get(family(m), "需要香港 IP"))
    proxy = rt if needs_route else None
    if m["kind"] == "tvb_geo":
        src = tvb_geo_url(m["url"], rt)
    if proxy or m["kind"] == "ytdlp":  # yt-dlp: TikTok, Instagram, Facebook (directly); YouTube over a route
        out = os.path.join(tmp, "dl.%(ext)s")
        try:
            run(["yt-dlp", "-q", "--no-warnings", "--no-playlist", *(["--proxy", proxy] if proxy else []), "-f",
                 AUDIO_FORMAT if proxy else NO_PROXY_FORMAT, "-o", out, src], timeout=1800)
        except RuntimeError as e:
            if proxy:
                raise
            if not rt:  # refused from the server's IP: not transcribed, not retried (no RESI_PROXY)
                raise NoDirect(str(e)[-300:])
            proxy = rt
            run(["yt-dlp", "-q", "--no-warnings", "--no-playlist", "--proxy", proxy, "-f", AUDIO_FORMAT,
                 "-o", out, src], timeout=1800)
        got = [f for f in os.listdir(tmp) if f.startswith("dl.")]
        if not got:
            raise RuntimeError("yt-dlp wrote nothing")
        src = os.path.join(tmp, got[0])
    return to_wav(src, wav)


def to_wav(src, wav):
    """a local file or a URL → wav (16 kHz mono)"""
    run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-rw_timeout", "30000000", "-user_agent", UA, "-i", src,
         "-vn", "-ac", "1", "-ar", str(SR), "-t", str(MAX_SECONDS), wav] if not os.path.exists(src) else
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", str(SR),
         "-t", str(MAX_SECONDS), wav], timeout=1800)
    return wav


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], 60).strip() or 0)


def silences(path):
    """[(start, end)] of the quiet stretches (ffmpeg silencedetect, -30 dB for 0.2 s: a newsreader's pauses between
    phrases; at -35 dB / 0.3 s a TVB clip had none between 15 and 39 s and was cut mid-word)"""
    p = subprocess.run(["ffmpeg", "-nostdin", "-i", path, "-af", "silencedetect=noise=-30dB:d=0.2", "-f", "null", "-"],
                       capture_output=True, timeout=600)
    err = p.stderr.decode("utf-8", "replace")
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return list(zip(starts, ends))


def chunks(model=None):
    """(about, at least, at most) seconds per piece for a model: STT_CHUNKS for a speech-to-text model (live,
    2026-10-15: OpenRouter's Qwen3-ASR provider cut a 60 s piece at 30 s mid-word, "聯儲局發表。 反映經濟情況嘅學皮書"),
    else CHUNK, CHUNK_MIN, CHUNK_MAX"""
    return STT_CHUNKS if is_stt(model or MODEL) else (CHUNK, CHUNK_MIN, CHUNK_MAX)


def cuts(total, quiet, sizes=None):
    """where to cut a clip of `total` seconds: about every `size` seconds, at the middle of the quiet stretch nearest
    to it between `lo` and `hi` after the last cut (else at `size`) → [(start, end)]. sizes = (size, lo, hi), default
    (CHUNK, CHUNK_MIN, CHUNK_MAX)"""
    size, lo, hi = sizes or (CHUNK, CHUNK_MIN, CHUNK_MAX)
    out, at = [], 0.0
    while total - at > hi:
        mids = [(a + b) / 2 for a, b in quiet if at + lo <= (a + b) / 2 <= at + hi]
        nxt = min(mids, key=lambda x: abs(x - (at + size))) if mids else at + size
        out.append((at, nxt))
        at = nxt
    out.append((at, total))
    return out


def mp3_piece(wav, start, end, path):
    run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", wav,
         "-ac", "1", "-ar", str(SR), "-b:a", "64k", path], 300)
    with open(path, "rb") as f:
        return f.read()


OPENROUTER = "https://openrouter.ai/api/v1/"


def is_stt(model):
    """a speech-to-text model (OpenRouter modality audio->transcription: Qwen3-ASR, Whisper, Scribe…), called on
    /audio/transcriptions; any other model is a chat model with audio input"""
    return bool(re.search(r"asr|whisper|scribe|transcribe", model or ""))


def request(mp3, model=None):
    """→ (url, body). A speech-to-text model: the MP3, temperature 0, no prompt (the endpoint takes none; the model
    detects the language). A chat model: the benchmark's request (run_openrouter.py: prompt "verbatim", temperature 0,
    max_tokens 16000, reasoning low)"""
    model = model or MODEL
    audio = {"data": base64.b64encode(mp3).decode(), "format": "mp3"}
    if is_stt(model):
        return OPENROUTER + "audio/transcriptions", {"model": model, "temperature": 0, "input_audio": audio}
    return OPENROUTER + "chat/completions", {"model": model, "usage": {"include": True}, **SETTINGS, "messages": [
        {"role": "user", "content": [{"type": "text", "text": PROMPT}, {"type": "input_audio", "input_audio": audio}]}]}


def call_model(mp3, model=None, tries=3):
    """one piece → (text in Hong Kong Traditional, cost USD). OpenRouter refusing for the key's limit or the account's
    credit raises ai.LimitReached (ai.py's safety stop: kv openrouter_limit, the monitor, one Discord message to Deyao)."""
    import ai
    url, body = request(mp3, model)
    stt = is_stt(body["model"])
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, json.dumps(body).encode(),
                                     {"Authorization": "Bearer " + os.environ["OPENROUTER_API"],
                                      "Content-Type": "application/json", "User-Agent": "yyc-news-stream/1",
                                      "X-Title": "YYC news stream"})
        try:
            with ai._open(req, 300) as r:
                resp = json.load(r)
        except urllib.error.HTTPError as e:
            last = RuntimeError(f"OpenRouter HTTP {e.code}")
            time.sleep(5 * (attempt + 1))
            continue
        except (TimeoutError, urllib.error.URLError, ConnectionError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
            continue
        if "error" in resp or not (resp.get("text") is not None if stt else resp.get("choices")):
            msg, code = ai._error_message(resp.get("error") or "no transcript")
            if ai._is_limit(code, msg):
                raise ai.limit_hit(msg)
            last = RuntimeError(msg)
            time.sleep(5 * (attempt + 1))
            continue
        ai.limit_clear()
        u = resp.get("usage") or {}
        if stt:  # {text, usage: {seconds, cost}}; the text comes with stray Simplified characters (消委会): s2hk
            ai._record(PURPOSE, body["model"], [{"content": ""}], {"cost": u.get("cost"), "prompt_tokens": u.get("input_tokens"),
                                                                   "completion_tokens": u.get("output_tokens")},
                       generation=resp.get("id"))
            return to_hk(resp["text"] or "", simplified=True).strip(), float(u.get("cost") or 0)
        ai._record(PURPOSE, body["model"], [{"content": PROMPT}], u, generation=resp.get("id"))
        return (resp["choices"][0]["message"].get("content") or "").strip(), float(u.get("cost") or 0)
    raise last


_cc = {}


def to_hk(s, simplified=False):
    """Hong Kong Traditional characters: s2hk for a model that writes (partly) Simplified (Qwen3-ASR), else t2hk (the
    model writes Traditional; t2hk settles the variants, e.g. 裡 → 裏). Latin text is left as it is"""
    cfg = "s2hk" if simplified else "t2hk"
    if cfg not in _cc:
        import opencc
        _cc[cfg] = opencc.OpenCC(cfg)
    return _cc[cfg].convert(s)


def mark(sec):
    sec = int(sec)
    return f"[{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}]" if sec >= 3600 else f"[{sec // 60:02d}:{sec % 60:02d}]"


def clean(text):
    """a piece's answer as one paragraph: no blank lines, no stray timestamps or speaker labels the model added"""
    lines = [re.sub(r"^\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*", "", x.strip()) for x in (text or "").splitlines()]
    out = ""
    for x in (x for x in lines if x):
        out += (" " if out and re.search(r"[A-Za-z0-9.,!?]$", out) and re.match(r"[A-Za-z0-9]", x) else "") + x
    return out


def assemble(pieces):
    """[(start seconds, text)] → the transcript: one line per piece, '[mm:ss] text', Hong Kong Traditional"""
    return "\n".join(f"{mark(st)} {to_hk(clean(t))}" for st, t in pieces if clean(t))


def runaway(text, seconds):
    """an answer longer than any speech of that length: the model ran on (a loop, or text that was never said; live,
    2026-10-14: 67,933 characters of English about Saturn's moon for 56 s of a Cantonese finance show)"""
    return len(text or "") > MAX_CPS * seconds + 200


def transcribe_wav(wav, model=None):
    """→ ([(start, text)], seconds of audio, USD)"""
    total = duration(wav)
    if total <= 0.5:
        return [], total, 0.0
    spans = cuts(total, silences(wav), chunks(model))
    tmp = os.path.dirname(wav)

    def one(i):
        st, en = spans[i]
        mp3 = mp3_piece(wav, st, en, os.path.join(tmp, f"p{i}.mp3"))
        cost = 0.0
        for attempt in range(1 + RETRIES_LONG):
            text, c = call_model(mp3, model)
            cost += c
            if not runaway(text, en - st):
                return st, text, cost
            print(f"media: piece at {st:.0f} s answered {len(text)} characters for {en - st:.0f} s (try {attempt + 1})",
                  flush=True)
        return st, LOST, cost
    with ThreadPoolExecutor(CONCURRENCY) as ex:
        got = list(ex.map(one, range(len(spans))))
    return [(st, t) for st, t, _ in got], total, round(sum(c for _, _, c in got), 6)


CAPTION_LANGS = ("zh-HK", "yue", "zh-Hant", "zh-TW", "zh", "zh-Hans", "zh-CN", "en")
CAPTIONS, AUTO_CAPTIONS = "youtube-captions:", "youtube-auto-captions:"  # media.captions_model / its label


def caption_label(lang):
    """a caption track's name without YouTube's '-orig' (the track of what was said)"""
    return lang[:-len("-orig")] if lang.endswith("-orig") else lang


def pick_captions(info):
    """the video's subtitles in its language → (lang, vtt url, auto?), or None: the uploader's own first, then
    YouTube's auto-generated track of what was said (`<lang>-orig`, or the one track named as the video's language when
    YouTube lists no translations). Never a track in another language (a translation, not what was said)"""
    lang = (info.get("language") or "").lower()
    chinese = lang[:2] in ("zh", "yu") or not lang

    def vtt(tracks):
        return next((f["url"] for f in tracks or [] if f.get("ext") == "vtt" and f.get("url")), None)
    subs = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}

    def fits(k):
        k = k.lower()
        if lang and (k == lang or k.split("-")[0] == lang.split("-")[0]):
            return True
        return chinese and (k.startswith("zh") or k.startswith("yue"))
    for k in sorted(subs, key=lambda k: CAPTION_LANGS.index(k) if k in CAPTION_LANGS else 99):
        if fits(k) and vtt(subs[k]):
            return k, vtt(subs[k]), False
    auto = info.get("automatic_captions") or {}
    if not lang:
        return None
    by_lower = {k.lower(): k for k in auto}
    orig = by_lower.get(lang + "-orig")
    if orig and vtt(auto[orig]):  # the -orig track's own URL (the auto-translated tracks answer 429)
        return caption_label(orig), vtt(auto[orig]), True
    if lang in by_lower and not any(k.endswith("-orig") for k in auto) and vtt(auto[by_lower[lang]]):
        return by_lower[lang], vtt(auto[by_lower[lang]]), True
    return None


def yt_info(url, proxy):
    """yt-dlp's metadata of a YouTube video (no download), on `proxy` (the ISP route: YouTube refuses datacenter IPs,
    the server's and a datacenter proxy's alike, measured 2026-10-15)"""
    return json.loads(run(["yt-dlp", "-J", "--no-warnings", "--no-playlist", "--skip-download", "--proxy", proxy, url],
                          timeout=180))


def caption_get(url, proxy):
    """a caption file through `proxy` (the metered ISP session: counted there, not here)"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    try:
        with op.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        resi.check(e)  # IPRoyal out of traffic → resi.ProxyOut
        raise


def _vtt_time(t):
    h, m, sec = (["0"] + t.replace(",", ".").split(":"))[-3:]
    return int(h) * 3600 + int(m) * 60 + float(sec)


VTT_TIMING = re.compile(r"^[ \t]*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})[ \t]+-->[ \t]+((?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})[^\n]*$",
                        re.M)  # the timing line, with its cue settings (align:start position:0% line:72% …)
NO_SPACE = re.compile(r"[\u2e80-\u9fff\u3000-\u303f\u3040-\u30ff\uf900-\ufaff\uff00-\uffef]")  # CJK, kana, fullwidth


def _join(a, b):
    """two caption lines as one text: a space between them unless either side is Chinese/Japanese or the second starts
    with punctuation"""
    if not a or not b:
        return a or b
    return a + ("" if NO_SPACE.match(a[-1]) or NO_SPACE.match(b[0]) or b[0] in ",.;:!?%)]}" else " ") + b


def vtt_cues(vtt):
    """a WebVTT file → [(start seconds, [text lines])]: the cue identifier, timing line and its cue settings left out,
    inline timestamps and styling tags (<00:00:01.234>, <c>…</c>, <i>…) stripped, entities decoded, blank lines
    dropped. A cue ends at an empty line only (YouTube's cues hold lines of a single space)"""
    cues = []
    for block in re.split(r"\n{2,}", vtt.replace("\r\n", "\n").replace("\r", "\n")):
        m = VTT_TIMING.search(block)
        if not m or block.lstrip().startswith(("NOTE", "STYLE", "REGION")):
            continue
        lines = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", x))).strip() for x in block[m.end():].split("\n")]
        cues.append((_vtt_time(m.group(1)), [x for x in lines if x]))
    return cues


def vtt_pieces(vtt, lang="", auto=False):
    """a WebVTT caption file → [(start seconds, text)] about once a minute, each line once: a rolling caption
    (YouTube's auto-generated tracks, a broadcaster's roll-up captions) shows the lines of the cue before it again
    above its new line, so a cue's leading lines that repeat the previous cue's last lines are left out. Chinese in Hong
    Kong Traditional characters: Simplified tracks (zh-Hans, zh-CN) and YouTube's auto-generated Chinese / Cantonese
    through OpenCC s2hk"""
    new, prev = [], []
    for st, lines in vtt_cues(vtt):
        k = next((k for k in range(min(len(lines), len(prev)), 0, -1) if lines[:k] == prev[-k:]), 0)
        new += [(st, x) for x in lines[k:]]
        prev = lines
    pieces, cur, start = [], "", None
    for st, text in new:
        if start is None:
            start = st
        elif st - start >= CHUNK:
            pieces.append((start, cur))
            cur, start = "", st
        cur = _join(cur, text)
    if cur:
        pieces.append((start, cur))
    lg = caption_label((lang or "").lower())
    if re.match(r"zh-(hans|cn|sg)", lg) or (lg[:2] in ("zh", "yu") and (auto or simplified("".join(t for _, t in pieces)))):
        pieces = [(st, to_hk(t, simplified=True)) for st, t in pieces]
    return pieces


SIMPLIFIED_SHARE, SIMPLIFIED_MIN = 0.15, 10  # live tracks (2026-10-20): Traditional ones at most 5%, Simplified 25-38%
_simp = {}


def simplified(text):
    """Chinese written in Simplified characters, whatever the track's label says (plain zh, even zh-Hant): of its Han
    characters, the share that s2hk changes and t2hk leaves (characters only Simplified writes) is SIMPLIFIED_SHARE or
    more. Traditional and Cantonese text stays below it (the few characters s2hk would also change, 后 干 台, are rare)"""
    han = re.findall(r"[\u4e00-\u9fff]", text or "")
    if len(han) < SIMPLIFIED_MIN:
        return False
    for c in set(han) - _simp.keys():
        _simp[c] = to_hk(c, simplified=True) != c and to_hk(c) == c
    return sum(_simp[c] for c in han) >= SIMPLIFIED_SHARE * len(han)


CUE_SETTINGS = re.compile(r"\s*align:(?:start|center|middle|end|left|right) position:\d+(?:\.\d+)?%"
                          r"(?:,(?:line-left|center|line-right|auto))?(?: line:-?\d+(?:\.\d+)?%?(?:,(?:start|center|end))?)?"
                          r"(?: size:\d+(?:\.\d+)?%)?\s*")  # what the old parser kept of the timing line (only these, live)
OLD_MARK = re.compile(r"^(\[(?:\d+:)?\d\d:\d\d\]) ?")
MIN_OVERLAP = 6  # characters (spaces aside) a rolling caption's repeat must share with the text before it


def reclean_captions(text, model=""):
    """subtitles the parser before 2026-10-20 stored ('[mm:ss] ' lines holding the cue settings and each rolling line
    two or three times) → clean text, marks where they were. Each cue's text (between the settings the old parser
    kept) loses its start where that repeats the end of the text so far. A Chinese track (model youtube-captions:zh…,
    yue…) written in Simplified goes through s2hk. Other text is returned as is"""
    lg = (model or "").split(":", 1)[-1].lower()
    if text and lg[:2] in ("zh", "yu") and simplified(text):
        text = to_hk(text, simplified=True)
    if not text or not CUE_SETTINGS.search(text):
        return text
    out_lines, tail, starts = [], "", set()  # the text so far without spaces (its end), where each added piece began

    def fits(ns, j):  # the cue's first j characters repeat the end of the text: a long run, or a whole earlier piece
        return tail.endswith(ns[:j]) and (j >= MIN_OVERLAP or j == len(ns) or len(tail) - j in starts)
    for line in text.split("\n"):
        m = OLD_MARK.match(line)
        mk, rest = (m.group(1), line[m.end():]) if m else ("", line)
        cur = ""
        for seg in (x.strip() for x in CUE_SETTINGS.split(rest)):
            if not seg:
                continue
            idx = [i for i, ch in enumerate(seg) if not ch.isspace()]
            ns = "".join(seg[i] for i in idx)
            j = next((j for j in range(min(len(ns), len(tail)), 0, -1) if fits(ns, j)), 0)
            if j == len(ns):
                continue
            add = seg[idx[j]:].strip()
            cur = _join(cur, add)
            starts.add(len(tail))
            tail += re.sub(r"\s+", "", add)
        if cur:
            out_lines.append(f"{mk} {cur}" if mk else cur)
    return "\n".join(out_lines)


def vtt_lines(vtt, lang="", auto=False):
    """a WebVTT caption file → the transcript format: '[mm:ss] text' about once a minute (vtt_pieces)"""
    return assemble(vtt_pieces(vtt, lang, auto))


def _yt_err(e):
    """yt-dlp's error for a YouTube video → the exception that says what to do"""
    if YT_UPCOMING.search(str(e)):  # a premiere / live event not started yet: later
        return HostBusy(f"YouTube upcoming: {str(e)[-200:]}")
    if YT_GONE.search(str(e)):  # private / removed / members-only: nothing to read, not asked again
        return Gone(str(e)[-300:])
    return e


YT_UPCOMING = re.compile(r"live event will begin|Premieres in|This live event|is_upcoming|will begin in", re.I)
YT_GONE = re.compile(r"Private video|Video unavailable|has been removed|members-only|This video is not available|"
                     r"account associated with this video has been terminated", re.I)


def youtube_captions(url):
    """a YouTube video's own subtitles through the ISP route, one locked session, no audio → (text, model) | None.
    Raises NoDirect (no route), HostBusy (live, upcoming, 429), Gone"""
    rt = routes().get("youtube")
    if not rt:
        raise NoDirect("YouTube 不接受伺服器的 IP，未設定 YouTube 路線")
    n = {"bytes": 0}
    try:
        with youtube_lock(), resi.metered_url(rt) as (px, n):
            try:
                info = yt_info(url, proxy=px)
            except RuntimeError as e:
                raise _yt_err(e)
            if info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
                raise HostBusy(f"YouTube {info.get('live_status')}")
            c = pick_captions(info)
            if not c:
                return None
            text = assemble(vtt_pieces(caption_get(c[1], px), c[0], c[2]))
            return (text, (AUTO_CAPTIONS if c[2] else CAPTIONS) + caption_label(c[0])) if text else None
    finally:
        resi.count_isp(n["bytes"])


def youtube_clip_audio(url, tmp):
    """a YouTube video through the ISP route, one locked session: its metadata, its subtitles (kept), its audio only →
    {"wav", "captions": (text, model) | None, "info"}. Raises NoDirect (no route), HostBusy (live, upcoming, 429), Gone"""
    rt = routes().get("youtube")
    if not rt:
        raise NoDirect("YouTube 不接受伺服器的 IP，未設定 YouTube 路線")
    cap, n = None, {"bytes": 0}
    try:
        with youtube_lock(), resi.metered_url(rt) as (px, n):
            try:
                info = yt_info(url, proxy=px)
            except RuntimeError as e:
                raise _yt_err(e)
            if info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
                raise HostBusy(f"YouTube {info.get('live_status')}")
            c = pick_captions(info)
            if c:
                try:
                    text = assemble(vtt_pieces(caption_get(c[1], px), c[0], c[2]))
                    cap = (text, (AUTO_CAPTIONS if c[2] else CAPTIONS) + caption_label(c[0])) if text else None
                except Exception as e:  # the subtitles are extra: the transcript goes on without them
                    print(f"media: {url} subtitles not read: {e!r}"[:200], flush=True)
            with open(os.path.join(tmp, "info.json"), "w") as f:
                json.dump(info, f)
            out = os.path.join(tmp, "dl.%(ext)s")
            for attempt in range(DOWNLOAD_TRIES):
                try:
                    run(["yt-dlp", "-q", "--no-warnings", "--no-playlist", "--proxy", px, "-f", AUDIO_FORMAT, "-o", out,
                         "--load-info-json", os.path.join(tmp, "info.json")], timeout=1800)
                    break
                except RuntimeError as e:
                    if "403" not in str(e) or attempt == DOWNLOAD_TRIES - 1:
                        raise _yt_err(e)
                    time.sleep(5)
    finally:
        resi.count_isp(n["bytes"])  # after the session closed: every byte to and from the ISP IP
    got = [f for f in os.listdir(tmp) if f.startswith("dl.")]
    if not got:
        raise RuntimeError("yt-dlp wrote nothing")
    return {"wav": to_wav(os.path.join(tmp, got[0]), os.path.join(tmp, "a.wav")), "captions": cap, "info": info}


def transcribe_source(m, model=None):
    """a clip (row of media, or {url, kind}) → (transcript, seconds, USD, model, captions): its audio, read directly
    (YouTube: through the ISP route, its subtitles kept), through the model"""
    cap = None
    with tempfile.TemporaryDirectory() as tmp:
        if family(m) == "youtube":
            got = youtube_clip_audio(m["url"], tmp)
            wav, cap = got["wav"], got["captions"]
        else:
            wav = fetch_audio(m, tmp)
        pieces, secs, cost = transcribe_wav(wav, model)
    return assemble(pieces), secs, cost, model or MODEL, cap


# ---- the scan: pages no full-text reader read ----

def scan_due(limit=200):
    now = datetime.now(HKT)
    cut = (now - timedelta(days=SCAN_DAYS)).isoformat(timespec="seconds")
    settle = (now - SCAN_SETTLE).isoformat(timespec="seconds")
    hosts = "(^|\\.)(" + "|".join(re.escape(h) for h in SCAN_HOSTS) + ")$"
    rows = corpus.db().execute(
        """SELECT p.id, p.url FROM posts p LEFT JOIN media_scan s ON s.post_id = p.id
           WHERE s.post_id IS NULL AND p.thread IS NULL AND coalesce(p.published, p.seen) >= %s AND p.seen <= %s
             AND lower(split_part(p.url, '/', 3)) ~ %s AND coalesce(p.sid, '') NOT LIKE 'bc\\_%%'
           ORDER BY coalesce(p.published, p.seen) DESC, p.id DESC LIMIT %s""", (cut, settle, hosts, limit)).fetchall()
    return [(r["id"], r["url"]) for r in rows if not _host(r["url"]).endswith(NO_SCAN)]


_hosts, _hlock = {}, threading.Lock()
_scan_tries = {}  # post id → failed reads (429 / 5xx / network): tried again up to SCAN_TRIES, in this process's life


def site(url):
    """the politeness unit: the registered domain (HKET's inews/china/wealth… share one backend and one 429)"""
    h = _host(url)
    parts = h.split(".")
    n = 3 if len(parts) >= 3 and parts[-2] in ("com", "org", "gov", "edu", "net") and len(parts[-1]) == 2 else 2
    return ".".join(parts[-n:])


def _slot(h):
    while True:
        with _hlock:
            at, now = _hosts.get(h, 0), time.monotonic()
            if at <= now:
                _hosts[h] = now + HOST_GAP
                return
        time.sleep(min(at - now, HOST_GAP))


def _back_off(h, seconds):
    with _hlock:
        _hosts[h] = max(_hosts.get(h, 0), time.monotonic() + seconds)


def scan_one(pid, url):
    """read one post's page and file its clips. A page that is gone (404/410) is marked looked-at with found NULL; a
    busy site (429, 5xx, network) backs that site off BUSY_WAIT and the post is tried again, SCAN_TRIES times in all"""
    import cache_fetch
    if _host(url).endswith(YT_HOSTS):  # the clip is the post itself: nothing to read
        return len(record(pid, url, ""))
    s = site(url)
    _slot(s)
    try:
        page = cache_fetch.get(url)
    except Exception as e:
        code = getattr(e, "code", None) or (int(m.group(1)) if (m := re.search(r"HTTP (\d{3})", str(e))) else None)
        gone = code in (400, 401, 403, 404, 410, 415)  # 415: a media file, never read (nomedia.py)
        if not gone:
            _back_off(s, BUSY_WAIT)
            _scan_tries[pid] = _scan_tries.get(pid, 0) + 1
            if _scan_tries[pid] < SCAN_TRIES:
                return 0
        print("media scan:", url[:100], repr(e)[:120], flush=True)
        corpus.db().execute("INSERT INTO media_scan (post_id, at, found) VALUES (%s, %s, NULL) ON CONFLICT DO NOTHING",
                            (pid, _now()))
        return 0
    try:  # its pictures from the same HTML
        import images
        images.seen_page(pid, url, page)
    except Exception:
        pass
    return len(record(pid, url, page))


def scan_once(limit=200, threads=6):
    todo = scan_due(limit)
    if not todo:
        return 0
    with ThreadPoolExecutor(threads) as ex:
        found = sum(ex.map(lambda t: scan_one(*t), todo))
    print(f"media scan: {len(todo)} page(s), {found} clip(s)", flush=True)
    return len(todo)


# ---- the transcriber ----

def work_one():
    """transcribe the newest due clip no other worker has → True when there was one. Transcription off: only a
    queued YouTube clip's subtitles (captions_one)"""
    if not on():  # once on again the stage starts from then (corpus.stage_blocked): what queued meanwhile waits
        corpus.stage_blocked("flag transcription off")
        return captions_one()
    m = claim()
    if not m:
        return False
    try:
        return _work(m)
    finally:
        release(m["key"])


def _work(m):
    t0 = time.time()
    import ai
    try:
        transcript, secs, cost, model, cap = transcribe_source(m)
    except ai.LimitReached:  # a safety stop (ai.limit_hit logged it, the monitor shows it, Deyao was told): not a try
        STOP.wait(LIMIT_WAIT)
        return True
    except NoDirect as e:  # its audio can't be read directly: not transcribed, never retried (no RESI_PROXY)
        final(m["key"], "no_direct", e)
        print(f"media: {m['key']} not read directly, not transcribed: {e}"[:300], flush=True)
        return True
    except Gone as e:  # a private or removed YouTube video: not asked again
        final(m["key"], "failed", e)
        print(f"media: {m['key']} gone: {e}"[:300], flush=True)
        return True
    except HostBusy as e:
        fail(m["key"], e, busy=True)
        print(f"media: {m['key']} {'proxy out of credit' if isinstance(e, ProxyOut) else 'host busy'}, later", flush=True)
        return True
    except Exception as e:
        if isinstance(e, resi.ProxyOut) or resi.is_out(e):  # a page read through the proxy (TVB's geo clips): the same stop
            fail(m["key"], ProxyOut(repr(e)), busy=True)
            print(f"media: {m['key']} proxy out of credit, later", flush=True)
            return True
        fail(m["key"], repr(e))
        print(f"media: {m['key']} failed: {e!r}"[:300], flush=True)
        return True
    finish(m["key"], transcript, secs, cost, model, cap)
    print(f"media: {m['key']} {secs / 60:.1f} min, {len(transcript)} chars, USD {cost:.5f}, {time.time() - t0:.0f} s",
          flush=True)
    return True


def _later(key, err):
    corpus.db().execute("UPDATE media SET error=%s, next_try=%s WHERE key=%s",
                        (str(err)[:500], (datetime.now(HKT) + timedelta(seconds=RETRY_AFTER)).isoformat(timespec="seconds"), key))


def captions_one():
    """transcription off: read one queued YouTube clip's own subtitles (no audio, no model) → True when there was one.
    The clip stays queued; its subtitles are kept (captions, captions_model, captions_at) and are the post's transcript
    until the model's comes. A busy host (live, 429) is tried again later; no subtitles, no route or a gone video: the
    clip is marked read (captions_at) and waits, queued, for the model"""
    m = claim_captions()
    if not m:
        return False
    try:
        try:
            cap = youtube_captions(m["url"])
        except HostBusy as e:  # later, never counted as a try: the clip stays queued for the model
            _later(m["key"], e)
            return True
        except (NoDirect, Gone) as e:
            print(f"media: {m['key']} subtitles not read: {e}"[:200], flush=True)
            cap = None
        except Exception as e:
            if isinstance(e, resi.ProxyOut) or resi.is_out(e):
                _later(m["key"], e)
                return True
            print(f"media: {m['key']} subtitles failed: {e!r}"[:200], flush=True)
            cap = None
        corpus.db().execute("UPDATE media SET captions=%s, captions_model=%s, captions_at=%s WHERE key=%s",
                            (cap[0] if cap else None, cap[1] if cap else None, _now(), m["key"]))
        if cap:
            for r in corpus.db().execute("SELECT post_id FROM post_media WHERE key=%s", (m["key"],)).fetchall():
                refresh_post(r["post_id"])
        print(f"media: {m['key']} subtitles only (transcription off): {len(cap[0]) if cap else 0} chars", flush=True)
        return True
    finally:
        release(m["key"])


def work_loop():
    while not STOP.is_set():
        try:
            with corpus.stage("transcription"):  # a restart (paused, the flag off) starts from now: the backlog waits
                busy = work_one()
        except Exception as e:  # the database went away, …: wait and go on
            print("media work loop:", repr(e)[:300], flush=True)
            busy = False
        if not busy:
            STOP.wait(10)


# ---- the Deployment: every copy transcribes, one scans ----

STARTED, ALIVE = "/tmp/transcribe-started", "/tmp/transcribe-alive"
SCAN_LOCK = None  # podlock.Lock('transcribe'): the page scan in one copy at a time


def scan_leader():
    """the scan, in whichever copy holds the lock 'yyc transcribe' (a copy that loses it waits for it again)"""
    import podlock
    global SCAN_LOCK
    SCAN_LOCK = podlock.Lock("transcribe", STOP, on_lost=lambda: None)
    while not STOP.is_set() and SCAN_LOCK.take():
        while SCAN_LOCK.held.is_set() and not STOP.is_set():
            try:
                n = scan_once()
            except Exception as e:
                print("media scan loop:", repr(e)[:300], flush=True)
                n = 0
            STOP.wait(5 if n else 60)
        SCAN_LOCK.release()


def on_term(*_):
    STOP.set()
    print("SIGTERM: finishing the clips in hand", flush=True)


def heartbeat():
    while True:
        open(ALIVE, "w").close()
        time.sleep(30)


def main():
    signal.signal(signal.SIGTERM, on_term)
    open(STARTED, "w").close()
    threading.Thread(target=heartbeat, daemon=True).start()
    print(f"transcriber: model {MODEL}", flush=True)
    threading.Thread(target=renew_loop, daemon=True, name="renew").start()
    ts = [threading.Thread(target=scan_leader)] + [threading.Thread(target=work_loop) for _ in range(CLIPS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    print("drained", flush=True)
    os._exit(0)


def stats():
    for r in corpus.db().execute("""SELECT status, host, count(*) n, round(sum(duration)::numeric / 60, 1) min,
                                           round(sum(cost)::numeric, 4) usd FROM media GROUP BY 1, 2 ORDER BY 1, 3 DESC"""):
        print(f"{r['status']:8} {r['host'] or '':32} {r['n']:6} {r['min'] or 0:>8} min  USD {r['usd'] or 0}")
    lim = corpus.kv_get("openrouter_limit")
    if lim:
        print("OpenRouter refused since", lim.get("since"), str(lim.get("message"))[:200])


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["run"]:
        main()
    elif a[:1] == ["detect"] and len(a) == 2:
        import cache_fetch
        for c in detect(a[1], cache_fetch.get(a[1])):
            print(json.dumps(c, ensure_ascii=False))
    elif a[:1] == ["test"] and len(a) == 2:
        kind = "ytdlp" if re.search(r"facebook\.com|instagram\.com|tiktok\.com", a[1]) else "direct"
        key = "yt:" + youtube_id(a[1]) if youtube_id(a[1]) else a[1]
        t, s, usd, model, cap = transcribe_source({"url": a[1], "kind": kind, "key": key})
        print(t)
        if cap:
            print(f"--- {cap[1]}\n{cap[0]}")
        print(f"{s:.0f} s of audio, USD {usd:.5f}", file=sys.stderr)
    elif a == ["stats"]:
        stats()
    else:
        print(__doc__)
        sys.exit(2)
