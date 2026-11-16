#!/usr/bin/env python3
"""Images on posts, scored for meaning (Deyao, 2026-10-14): every image an article or post carries (the lead image,
the pictures in the body, a YouTube video's thumbnail, which counts as a photo) gets a 0–100 score from a vision model
for whether it carries information, its reason and the text read from it. Images at or above the threshold
(settings `image_threshold`, default 50; `infra/control.py set image_threshold N`) are the post's meaningful images:
their text joins the post's search text and the scorer's input (the post is scored again), and the item page shows
them first; the rest fold under 「其他圖片（N）」.

    python3 images.py run           the image worker (Deployment `images`): one copy at a time (Postgres lock 'yyc
                                    images'), finding the images of new posts and scoring them, newest first
    python3 images.py extract <url> the images a page carries (fetches the page; writes nothing)
    python3 images.py score <img>   score one image URL and print the answer (costs the model call)
    python3 images.py stats         images per status, spend
    python3 images.py repair        drop the picture rows saved by the extraction bugs fixed 2026-10-15 (once)

Meaningful (score 50+): it carries information — text in the picture, infographics, charts, tables, maps,
screenshots of statements, posts or documents, posters, slides, evidence photos that matter to the story. Not
meaningful: stock photos, decorative images, logos, building exteriors, headshots or portraits of the people in the
story or interviewees, generic crowd / press-conference / meeting shots, a video's still frame of a speaker.

Where images come from: the pages the full-text readers and the transcriber's scan read (media.seen_page /
media.record pass them on: no second request), and for posts nobody read a page of, this worker reads it (one
request per site at a time, 1 s apart). A YouTube post's thumbnail comes from its id (i.ytimg.com). Each post is
looked at once (table image_scan).

Storage: table images, one row per picture, keyed by the sha256 of its bytes (the same picture on many posts, or
under several URLs, is scored once): score, reason, text, model, at, width, height. post_images links posts to
images (by URL first, the hash once it is downloaded). posts.image_text = the text of the post's meaningful images.

Model: one setting, env IMAGE_MODEL (chosen on hand-labelled real images: tests/fixtures/images/, README); unset = the
worker refuses to start. Spend:
ai_spend / ai_runs purpose `images`. No budget logic: OpenRouter's limit/credit refusal is ai.py's safety stop.
"""
import base64, hashlib, html, io, json, os, re, signal, sys, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta

import corpus

HKT = corpus.HKT
MODEL = os.environ.get("IMAGE_MODEL")  # none chosen: the 15 Oct benchmark's models are closed-weight, refused on YYC's account (README "Pictures")
PURPOSE = "images"
THRESHOLD = 50          # default; the live value is settings image_threshold
MAX_IMAGES = 40         # per post (lead image first): a guard against a page whose listing is read as its body
MAX_SIDE = 1024         # pixels: the long side sent to the model
MIN_SIDE = 120          # smaller pictures are icons, buttons and spacers
TRIES = 3
SCAN_DAYS = 7           # posts this recent are looked at (串流's items first)
WORKERS = 6
HOST_GAP = 1.0
# Memory (2026-10-15: OOMKilled 5 times at 768Mi): a newspaper photo can be 9504x6336 (60 megapixels, 180 MB decoded,
# 460 MB with its copies), and six workers decoding such pictures at once need 2.2 GB. A JPEG is decoded at a reduced
# scale (draft: 1/2, 1/4 or 1/8, never below the size sent on), and every decode is held inside this budget of decoded
# bytes shared by the workers (a picture over it waits until it can be decoded alone). Measured: README "Pictures".
DECODE_BUDGET = 256 * 2**20
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
STOP = threading.Event()

PROMPT = """You rate one image from a news article or social-media post for YYC, a Hong Kong news-monitoring service.
Score 0–100 how MEANINGFUL the image is: whether it carries information a reader would lose without it.

90–100: the image is information itself — text-heavy screenshots (statements, posts, letters, documents, notices),
  infographics, charts, tables, maps, slides, posters/flyers with details.
60–89: it carries some information — a photo with readable signs, banners or captions that matter, a photo that is
  evidence of the event (damage, an accident, a protest scene with legible slogans, a product shown in detail).
20–59: a photo of the event that adds little (a generic scene, a speaker at a podium, a press conference, a meeting).
0–19: carries nothing — stock or decorative pictures, logos, building exteriors, headshots or portraits of people in
  the story or interviewees, generic crowd shots, a video's still frame of a presenter, a thumbnail of a talking head.

Also write out the text that is legible in the image (Chinese as written, Traditional stays Traditional; empty if
none), and one short reason in Traditional Chinese (Hong Kong usage).
Answer JSON only: {"score": <0-100>, "reason": "<短句>", "text": "<text in the image>"}"""


# ---- finding the images ----

def _host(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower()


SKIP = re.compile(r"(logo|icon|avatar|favicon|sprite|spacer|placeholder|blank|loading|default[-_]?(img|image)|"
                  r"/ads?/|adserver|doubleclick|banner|badge|emoji|qr[-_]?code|share|social|weixin|wechat|appstore|"
                  r"googleplay|button|arrow|1x1|pixel)", re.I)


def _abs(base, u):
    # quotes around the value: HTML inside a JSON string read as HTML (HKET's content API: src=\"https://…\"), which
    # urljoin would otherwise glue onto the page's URL
    u = html.unescape((u or "").strip())
    if m := re.match(r"""^\\?["'](.*?)\\?["']""", u):
        u = m.group(1)
    u = u.strip("\\\"' ")
    if not u or u.startswith("data:"):
        return None
    u = _canon(urllib.parse.urljoin(base, u))
    return u if u.startswith("http") else None


def _canon(u):
    """a Qiniu image-processing URL (…/a.jpg?imageMogr2/thumbnail/x320, 橙新聞) → the picture itself (it is shrunk for
    the model anyway), so the thumbnail and the og:image are one picture"""
    return re.sub(r"\?(imageMogr2|imageView2)/.*$", "", u)


def _ok(u):
    if not u:
        return False
    path = urllib.parse.urlsplit(u).path.lower()
    if re.search(r"\.(svg|gif|ico)(\?|$)", path) or SKIP.search(u):
        return False
    return True


def youtube_thumb(url):
    import media
    vid = media.youtube_id(url)
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else None


def extract(url, page):
    """→ the image URLs of the page's post, lead image first, at most MAX_IMAGES, each once"""
    if _host(url).endswith(("youtube.com", "youtu.be")):
        t = youtube_thumb(url)
        return [t] if t else []
    out = []
    import lxml.html
    if page.lstrip()[:1] in ("{", "["):  # a site's content API (HKET's JavaScript-only sites: cache_fetch.hket_api)
        try:
            return _done(url, from_json(url, json.loads(page)))
        except ValueError:
            pass
    try:
        doc = lxml.html.fromstring(page)
    except Exception:
        return []
    for xp in ('//meta[@property="og:image"]/@content', '//meta[@name="twitter:image"]/@content',
               '//meta[@property="og:image:url"]/@content'):
        out += [_abs(url, x) for x in doc.xpath(xp)][:1]
    for s in doc.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            d = json.loads(s)
        except ValueError:
            continue
        for o in d if isinstance(d, list) else d.get("@graph", [d]) if isinstance(d, dict) else []:
            if isinstance(o, dict) and "Article" in str(o.get("@type", "")):
                im = o.get("image")
                for x in im if isinstance(im, list) else [im]:
                    out.append(_abs(url, x.get("url") if isinstance(x, dict) else x))
    for bad in doc.xpath("//script|//style|//noscript|//nav|//footer|//header|//aside|//form"):
        bad.drop_tree()
    scope = body_scope(doc)
    for el in ([scope, scope.getparent()] if scope is not None else []):
        if el is None:
            continue
        found = list(_imgs(url, el))
        if found:  # the story's own block; one level up only when it holds no picture itself (figures beside it)
            out += found
            break
    return _done(url, out)


def _done(url, out):
    """each once, at most MAX_IMAGES, never the page itself (橙新聞's empty og:image + twitter:image
    "?imageMogr2/thumbnail/x320" joined to the page's URL is the article, not a picture)"""
    me = urllib.parse.urlsplit(url)
    seen, res = set(), []
    for u in out:
        if not _ok(u) or u in seen:
            continue
        seen.add(u)
        p = urllib.parse.urlsplit(u)
        if (p.hostname, urllib.parse.unquote(p.path)) == (me.hostname, urllib.parse.unquote(me.path)):
            continue
        res.append(u)
    return res[:MAX_IMAGES]


IMG_PATH = re.compile(r"(^|\.)(thumbnails?|og|image|images|cover)(\.|$)", re.I)
IMG_URL = re.compile(r"^https?://\S+\.(jpe?g|png|webp|avif)(\?\S*)?$", re.I)


def from_json(url, d):
    """a content API's JSON → the post's pictures: the first picture URL under a thumbnail / og / image / cover key is
    the lead, then the <img> of the HTML strings it carries (the body), in order"""
    import lxml.html
    lead, body, stack = [], [], [("", d)]
    while stack:
        path, o = stack.pop(0)
        if isinstance(o, dict):
            stack[:0] = [(f"{path}.{k}".strip("."), v) for k, v in o.items()]
        elif isinstance(o, list):
            stack[:0] = [(path, x) for x in o]
        elif isinstance(o, str):
            if "<img" in o:
                try:
                    body += list(_imgs(url, lxml.html.fragment_fromstring(o, create_parent="div")))
                except Exception:
                    pass
            elif not lead and IMG_PATH.search(path) and IMG_URL.match(o.strip()):
                lead.append(_abs(url, o))
    return lead + body


def _imgs(url, el):
    for img in el.iter("img"):
        w, h = img.get("width"), img.get("height")
        if (w and w.isdigit() and int(w) < MIN_SIDE) or (h and h.isdigit() and int(h) < MIN_SIDE):
            continue
        src = img.get("data-src") or img.get("data-original") or img.get("data-lazy-src") or img.get("src")
        ss = img.get("srcset") or img.get("data-srcset")
        if ss:  # the largest candidate
            cands = [c.strip().split(" ") for c in ss.split(",") if c.strip()]
            cands.sort(key=lambda c: int(re.sub(r"\D", "", c[1]) or 0) if len(c) > 1 else 0)
            src = cands[-1][0] if cands else src
        u = _abs(url, src)
        if u:
            yield u


def body_scope(doc):
    """the element holding the story: the parent of its largest paragraph cluster (cache_fetch.article_text's rule)"""
    from collections import defaultdict
    groups = defaultdict(int)
    for p in doc.iter("p"):
        t = " ".join(p.itertext()).strip()
        if len(t) >= 12:
            groups[p.getparent()] += len(t)
    if not groups:
        arts = doc.xpath("//article")
        return arts[0] if arts else None
    return max(groups, key=groups.get)


# ---- the records ----

def _now():
    return datetime.now(HKT).isoformat(timespec="seconds")


def record(post_id, url, page):
    """a post's page was read (or it is a YouTube post): link its images, mark it looked at → the image URLs"""
    return link(post_id, extract(url, page or ""))


def link(post_id, urls):
    """link the post to these pictures (lead first) and mark it looked at → the URLs kept (a platform's own photo
    list, X: no page read)"""
    seen = set()
    urls = [u for u in urls if u and _ok(u) and not (u in seen or seen.add(u))][:MAX_IMAGES]
    c = corpus.db()
    with c:
        for i, u in enumerate(urls):  # a picture already given up on another post is not fetched again
            c.execute("""INSERT INTO post_images (post_id, url, pos, tries, error)
                         SELECT %s, %s, %s, coalesce(max(tries), 0), max(error) FROM post_images
                          WHERE url = %s AND hash IS NULL AND tries >= %s
                         ON CONFLICT (post_id, url) DO NOTHING""", (post_id, u, i, u, TRIES))
        c.execute("""INSERT INTO image_scan (post_id, at, found) VALUES (%s, %s, %s)
                     ON CONFLICT (post_id) DO UPDATE SET at=excluded.at, found=excluded.found""", (post_id, _now(), len(urls)))
    return urls


def seen_page(post_id, url, page):
    """the page readers' hook (never raises)"""
    if not page and not _host(url).endswith(("youtube.com", "youtu.be")):
        return
    try:
        record(post_id, url, page)
    except Exception as e:
        print("images: record failed:", url[:100], repr(e)[:160], flush=True)


def threshold():
    r = corpus.db().execute("SELECT v FROM settings WHERE k='image_threshold'").fetchone()
    try:
        return int(r[0]) if r and r[0] is not None else THRESHOLD
    except (TypeError, ValueError):
        return THRESHOLD


def refresh_post(post_id):
    """posts.image_text = the text read from the post's meaningful images (search and the scorer read it)"""
    rows = corpus.db().execute("""SELECT DISTINCT i.hash, i.text, min(pi.pos) AS pos FROM post_images pi
                                  JOIN images i ON i.hash = pi.hash WHERE pi.post_id=%s AND i.score >= %s
                                  AND coalesce(i.text, '') <> '' GROUP BY i.hash, i.text ORDER BY pos""",
                               (post_id, threshold())).fetchall()
    corpus.set_image_text(post_id, "\n\n".join(r["text"] for r in rows) or None)


def refresh_all():
    """after a threshold change: every post with a scored picture gets its picture text again → posts redone"""
    ids = [r[0] for r in corpus.db().execute("SELECT DISTINCT post_id FROM post_images WHERE hash IS NOT NULL")]
    for pid in ids:
        refresh_post(pid)
    return len(ids)


def due(limit=20):
    """image URLs to score: not yet linked to a scored picture, fewer than TRIES failures, 串流 items first, then
    the newest posts"""
    return [dict(r) for r in corpus.db().execute(
        """SELECT pi.post_id, pi.url, pi.tries FROM post_images pi JOIN posts p ON p.id = pi.post_id
           WHERE pi.hash IS NULL AND pi.tries < %s AND """ + corpus.seen_cond("p", "pictures") + """
           ORDER BY (EXISTS (SELECT 1 FROM stream_items s WHERE s.url = p.url)) DESC,
                    coalesce(p.published, p.seen) DESC, pi.pos LIMIT %s""", (TRIES, limit))]


def for_scoring(posts, per_post=2, side=768):
    """the stream scorer's attachments for a batch: each post's meaningful pictures (best first, at most per_post: 10
    posts × 2 stays inside the 20 pictures a request keeps at full detail) → [(caption, JPEG)]"""
    ids = [p["id"] for p in posts if p.get("id")]
    if not ids or not on():  # picture analysis off: the scorer is text-only (no image tokens)
        return []
    rows = corpus.db().execute("""SELECT DISTINCT ON (pi.post_id, i.hash) pi.post_id, pi.url, i.score FROM post_images pi
                                  JOIN images i ON i.hash = pi.hash WHERE pi.post_id = ANY(%s) AND i.score >= %s
                                  ORDER BY pi.post_id, i.hash, pi.pos""", (ids, threshold())).fetchall()
    best = {}
    for r in sorted(rows, key=lambda r: -r["score"]):
        best.setdefault(r["post_id"], [])
        if len(best[r["post_id"]]) < per_post:
            best[r["post_id"]].append(r["url"])
    out = []
    for i, p in enumerate(posts):
        for u in best.get(p.get("id"), []):
            try:
                im = shrink(download(u), side)[0]
                b = io.BytesIO()
                im.save(b, "JPEG", quality=80)
                out.append((f"帖子 {i} 的圖片：", b.getvalue()))
            except Exception as e:
                print("images: not attached:", u[:100], repr(e)[:100], flush=True)
    return out


# ---- scoring ----

def download(url):
    """→ the image's bytes; the image's own site as referer (hotlink protection)"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": f"https://{_host(url)}/",
                                               "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read(20_000_000)
    except Exception:
        from curl_cffi import requests as cr
        r = cr.get(url, impersonate="chrome", timeout=30, headers={"Referer": f"https://{_host(url)}/"})
        if r.status_code >= 400:
            raise urllib.error.HTTPError(url, r.status_code, f"HTTP {r.status_code}", {}, None)
        return r.content


class Budget:
    """bytes of decoded pictures held at once by the workers; a picture over the whole budget waits to be alone"""

    def __init__(self, total):
        self.total, self.used, self.cv = total, 0, threading.Condition()

    @contextmanager
    def hold(self, n):
        n = min(n, self.total)
        with self.cv:
            while self.used + n > self.total:
                self.cv.wait()
            self.used += n
        try:
            yield
        finally:
            with self.cv:
                self.used -= n
                self.cv.notify_all()


BUDGET = Budget(DECODE_BUDGET)


def shrink(data, side):
    """the picture's bytes → (RGB image with long side ≤ side, its own width, height); (None, w, h) when it is under
    MIN_SIDE (nothing decoded). A JPEG decodes at the smallest of 1/1–1/8 that still covers side (draft); the
    decoding is held inside BUDGET"""
    from PIL import Image
    im = Image.open(io.BytesIO(data))  # reads the header only
    w, ht = im.size
    if min(w, ht) < MIN_SIDE:
        return None, w, ht
    jpeg = im.format == "JPEG"
    if jpeg:
        im.draft("RGB", (side, side))
    alpha = im.mode in ("RGBA", "LA", "PA", "P", "La", "RGBa") or "transparency" in im.info
    dw, dh = im.size
    # measured peaks (tests/test_images.py Memory, README "Pictures"): the decoded picture plus thumbnail()'s copies
    # (an RGBA picture is premultiplied into a second full copy first), a converted copy when its mode isn't
    # RGB/L/RGBA; a progressive JPEG also keeps the whole picture's coefficients (~3 bytes a pixel at full size,
    # whatever the draft scale)
    # (WebP and AVIF: ~17 bytes a pixel, the decoder's own RGBA buffer and Pillow's copies)
    if im.format in ("WEBP", "AVIF"):
        cost = dw * dh * 17
    else:
        cost = dw * dh * len(im.getbands()) * (5 if not jpeg else 3) // 2
    if im.mode not in ("RGB", "L", "RGBA"):
        cost += dw * dh * 4
    if jpeg and (im.info.get("progressive") or im.info.get("progression")):
        cost += w * ht * 3
    with BUDGET.hold(cost):
        im.load()
        if im.mode not in ("RGB", "L", "RGBA"):
            im = im.convert("RGBA" if alpha else "RGB")
        im.thumbnail((side, side))
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    return im.convert("RGB") if im.mode != "RGB" else im, w, ht


def prepare(data):
    """→ (sha256 of the bytes, JPEG for the model (long side ≤ MAX_SIDE), width, height); None for a picture too
    small to carry anything"""
    h = hashlib.sha256(data).hexdigest()
    im, w, ht = shrink(data, MAX_SIDE)
    if im is None:
        return h, None, w, ht
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85)
    return h, out.getvalue(), w, ht


def parse(answer):
    """the model's JSON → {score 0–100, reason, text}; raises ValueError when it is not that"""
    s = (answer or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        raise ValueError("no JSON")
    d = json.loads(m.group(0))
    score = int(round(float(d["score"])))
    return {"score": max(0, min(100, score)), "reason": str(d.get("reason") or "")[:200],
            "text": str(d.get("text") or "").strip()[:4000]}


def ask(jpeg, model=None):
    """one image → {score, reason, text}"""
    import ai
    url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    msgs = [{"role": "user", "content": [{"type": "text", "text": PROMPT}, {"type": "image_url", "image_url": {"url": url}}]}]
    last = None
    for attempt in range(2):  # cost: ai.chat logs OpenRouter's charged cost under PURPOSE
        msg = ai.chat(msgs, PURPOSE, model=model or MODEL, json_mode=True, max_tokens=1500, temperature=0)
        try:
            return parse(msg.get("content") or "")
        except (ValueError, KeyError, TypeError) as e:
            last = e
    raise RuntimeError(f"not JSON twice: {last}")


def score_one(row):
    """download, hash, score (or reuse the score of the same picture) → the hash"""
    data = download(row["url"])
    h, jpeg, w, ht = prepare(data)
    c = corpus.db()
    have = c.execute("SELECT hash FROM images WHERE hash=%s", (h,)).fetchone()
    if not have:
        if jpeg is None:
            res, model = {"score": 0, "reason": "圖片太小", "text": ""}, None
        else:
            res, model = ask(jpeg), MODEL
        c.execute("""INSERT INTO images (hash, url, width, height, score, reason, text, model, at)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (hash) DO NOTHING""",
                  (h, row["url"], w, ht, res["score"], res["reason"], res["text"], model, _now()))
    posts = [r[0] for r in c.execute("UPDATE post_images SET hash=%s, error=NULL WHERE url=%s RETURNING post_id",
                                     (h, row["url"]))]
    for p in set(posts):
        refresh_post(p)
    return h


def permanent(e):
    """an error no later try can change: the server refuses the URL (4xx but timeouts / rate limits), the bytes are not
    a picture (a page: UnidentifiedImageError), or a decompression bomb"""
    from PIL import Image, UnidentifiedImageError
    if isinstance(e, urllib.error.HTTPError):
        return 400 <= e.code < 500 and e.code not in (408, 425, 429)
    return isinstance(e, (UnidentifiedImageError, Image.DecompressionBombError))


def fail(row, err):
    """the try is already counted (claim); a permanent error gives the picture up at once, on every post carrying it"""
    corpus.db().execute("UPDATE post_images SET error=%s, tries = CASE WHEN %s THEN greatest(tries, %s) ELSE tries END "
                        "WHERE url=%s AND hash IS NULL", (repr(err)[:300], permanent(err), TRIES, row["url"]))


_inflight, _ilock = set(), threading.Lock()


def claim():
    """the next picture, its try counted before the work: a picture whose download or decoding kills the worker (the
    OOM kill, 2026-10-15) runs out of tries instead of killing every restart"""
    with _ilock:
        for r in due(WORKERS * 3):
            if r["url"] not in _inflight:
                _inflight.add(r["url"])
                corpus.db().execute("UPDATE post_images SET tries = tries + 1 WHERE url=%s AND hash IS NULL", (r["url"],))
                return r
    return None


def on():
    """picture analysis on (flagd flag `pictures`; off since 2026-10-20, Deyao: cost): off, nothing is scored, the
    queued pictures stay queued, the scorer gets no picture"""
    import flags
    return flags.on("pictures")


def work_one():
    import ai
    if not on():  # off: claim nothing; once on again the stage starts from then (corpus.stage_blocked)
        corpus.stage_blocked("flag pictures off")
        return False
    r = claim()
    if not r:
        return False
    try:
        score_one(r)
    except ai.LimitReached:  # ai.py's safety stop (kv openrouter_limit, the monitor, Deyao told): not a try
        corpus.db().execute("UPDATE post_images SET tries = greatest(tries - 1, 0) WHERE url=%s AND hash IS NULL",
                            (r["url"],))
        STOP.wait(600)
    except Exception as e:
        fail(r, e)
        print("images:", r["url"][:100], repr(e)[:160], flush=True)
    finally:
        with _ilock:
            _inflight.discard(r["url"])
    return True


def work_loop():
    while not STOP.is_set():
        try:
            with corpus.stage("pictures"):  # a restart (paused, the flag off) starts from now: the backlog waits
                busy = work_one()
        except Exception as e:
            print("images work loop:", repr(e)[:300], flush=True)
            busy = False
        if not busy:
            STOP.wait(10)


# ---- the scan: posts whose page nobody handed over ----

SCAN_TYPES = ("News", "Newspaper", "Magazine", "Official", "Blog", "Video")


def scan_due(limit=200):
    now = datetime.now(HKT)
    cut = (now - timedelta(days=SCAN_DAYS)).isoformat(timespec="seconds")
    settle = (now - timedelta(minutes=10)).isoformat(timespec="seconds")
    import cache_fetch
    rows = corpus.db().execute(
        """SELECT p.id, p.url FROM posts p LEFT JOIN image_scan s ON s.post_id = p.id
           WHERE s.post_id IS NULL AND p.thread IS NULL AND coalesce(p.published, p.seen) >= %s AND p.seen <= %s
             AND (p.source_type = ANY(%s) OR lower(split_part(p.url, '/', 3)) ~ '(^|\\.)(youtube\\.com|youtu\\.be)$')
             AND coalesce(p.sid, '') NOT LIKE 'bc\\_%%'
           ORDER BY (EXISTS (SELECT 1 FROM stream_items i WHERE i.url = p.url)) DESC,
                    coalesce(p.published, p.seen) DESC, p.id DESC LIMIT %s""",
        (cut, settle, list(SCAN_TYPES), limit)).fetchall()
    skip = cache_fetch.SKIP_HOSTS + cache_fetch.PROXY_HOSTS + cache_fetch.BROWSER_HOSTS + cache_fetch.SOLVER_HOSTS
    out = []
    for r in rows:
        if _host(r["url"]).endswith(skip):
            corpus.db().execute("INSERT INTO image_scan (post_id, at, found) VALUES (%s,%s,NULL) ON CONFLICT DO NOTHING",
                                (r["id"], _now()))
            continue
        out.append((r["id"], r["url"]))
    return out


_hosts, _hlock = {}, threading.Lock()


def scan_one(pid, url):
    import cache_fetch, media
    if _host(url).endswith(("youtube.com", "youtu.be")):
        return len(record(pid, url, ""))
    s = media.site(url)
    while True:
        with _hlock:
            at, now = _hosts.get(s, 0), time.monotonic()
            if at <= now:
                _hosts[s] = now + HOST_GAP
                break
        time.sleep(min(at - now, HOST_GAP))
    try:
        api = cache_fetch.hket_api(url)  # an empty JavaScript shell: its content API carries the pictures
        page = cache_fetch.get(api[0], headers=api[1]) if api else cache_fetch.get(url)
    except Exception as e:
        print("images scan:", url[:100], repr(e)[:120], flush=True)
        corpus.db().execute("INSERT INTO image_scan (post_id, at, found) VALUES (%s,%s,NULL) ON CONFLICT DO NOTHING",
                            (pid, _now()))
        return 0
    media.seen_page(pid, url, page, pictures=False)  # clips the transcriber has not seen (filed once; never raises)
    return len(record(pid, url, page))


def scan_loop():
    while not STOP.is_set():
        try:
            todo = scan_due()
            if todo:
                with ThreadPoolExecutor(6) as ex:
                    n = sum(ex.map(lambda t: scan_one(*t), todo))
                print(f"images scan: {len(todo)} post(s), {n} image(s)", flush=True)
        except Exception as e:
            print("images scan loop:", repr(e)[:300], flush=True)
            todo = []
        STOP.wait(5 if todo else 60)


# ---- the Deployment ----

STARTED, ALIVE = "/tmp/images-started", "/tmp/images-alive"
LOCK = None  # podlock.Lock('images'): one copy at a time; a copy that loses it to another stops (drains, exits)


def on_term(*_):
    STOP.set()
    if LOCK is not None and LOCK.held.is_set():
        print("SIGTERM: finishing the images in hand", flush=True)


def heartbeat():
    while True:
        open(ALIVE, "w").close()
        time.sleep(30)


def main():
    signal.signal(signal.SIGTERM, on_term)
    open(STARTED, "w").close()
    threading.Thread(target=heartbeat, daemon=True).start()
    global LOCK
    import podlock
    LOCK = podlock.Lock("images", STOP)
    if not LOCK.take():  # SIGTERM while waiting: nothing in hand
        os._exit(0)
    if not MODEL:
        sys.exit("images: no IMAGE_MODEL set (README \"Pictures\": choose an open-weight model on the labelled set first)")
    print(f"images: model {MODEL}, threshold {threshold()}", flush=True)
    ts = [threading.Thread(target=scan_loop)] + [threading.Thread(target=work_loop) for _ in range(WORKERS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    print("drained; releasing images lock", flush=True)
    os._exit(0)


def repair():
    """drop the picture rows the extraction got wrong before 2026-10-15 (HKET's escaped quotes joined to the page's URL,
    橙新聞's article page taken for its picture) and let the scan read those posts again → rows dropped"""
    c = corpus.db()
    with c:
        rows = c.execute("""DELETE FROM post_images pi USING posts p WHERE p.id = pi.post_id AND pi.hash IS NULL
                              AND (strpos(pi.url, '"') > 0 OR split_part(pi.url, '?', 1) = split_part(p.url, '?', 1))
                            RETURNING pi.post_id""").fetchall()
        ids = sorted({r[0] for r in rows})
        c.execute("DELETE FROM image_scan WHERE post_id = ANY(%s)", (ids,))
    return len(rows)


def stats():
    for r in corpus.db().execute("""SELECT count(*) n, count(*) FILTER (WHERE score >= %s) meaningful,
                                           count(DISTINCT model) models FROM images""", (threshold(),)):
        print(dict(r))
    for r in corpus.db().execute("""SELECT count(*) FILTER (WHERE hash IS NULL AND tries < %s) due,
                                           count(*) FILTER (WHERE hash IS NULL AND tries >= %s) failed,
                                           count(*) FILTER (WHERE hash IS NOT NULL) linked FROM post_images""",
                                 (TRIES, TRIES)):
        print(dict(r))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["run"]:
        main()
    elif a[:1] == ["extract"] and len(a) == 2:
        import cache_fetch
        api = cache_fetch.hket_api(a[1])
        page = ("" if _host(a[1]).endswith(("youtube.com", "youtu.be")) else
                cache_fetch.get(api[0], headers=api[1]) if api else cache_fetch.get(a[1]))
        for u in extract(a[1], page):
            print(u)
    elif a[:1] == ["score"] and len(a) == 2:
        h, jpeg, w, ht = prepare(download(a[1]))
        print(json.dumps(ask(jpeg), ensure_ascii=False))
    elif a == ["stats"]:
        stats()
    elif a == ["repair"]:
        print(f"images: {repair()} bad picture row(s) dropped; their posts are scanned again")
    else:
        print(__doc__)
        sys.exit(2)
