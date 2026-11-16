#!/usr/bin/env python3
"""TikTok videos of the posts that pass stage 1, downloaded once and kept (Deyao, 2026-10-22).

Deyao's explicit exception to "text only" (nomedia.py, 2026-10-20), for TikTok videos only: every TikTok post whose
stage-1 score (ai.MODEL, streams.score_posts) reaches streams.SCREEN (20) for any stream gets its video downloaded,
best quality with audio, and kept indefinitely in the private bucket of Hetzner Object Storage (project yyc, fsn1;
EXPENSES.md). Transcription stays off (flag `transcription`): nothing here reads the audio.

    python3 tiktok_video.py run          the worker (Deployment tiktok-videos): every copy downloads the queue, newest
                                         first, each post claimed in the database (a rollout's new pod works at once
                                         beside the old one finishing what it holds); every SWEEP seconds it also
                                         queues any stage-1 TikTok post the hook missed, and measures the bucket
    python3 tiktok_video.py backlog [D]  queue the TikTok posts already at >= SCREEN (seen in the last D days; all when
                                         D is left out): the worker downloads what TikTok still serves, the rest "gone"
    python3 tiktok_video.py test URL     download one TikTok URL into a temp dir and print what it got (no upload)
    python3 tiktok_video.py stats        the queue per status, bytes, the bucket's own figure

Queue (table tiktok_videos, one row per post): queued at the moment stage 1 scores the post (streams.score_posts →
queue_stage1, how 'stage1') or by the sweep/backlog (how 'sweep' / 'backlog'). States: queued → ok (file in the
bucket, key tiktok/<video id>.mp4) | failed (reason; TRIES transient failures, or at once for a post with no video)
| gone (TikTok answers that the video is removed, private or unavailable). A post TikTok shows only to a signed-in
viewer (age-restricted), or refuses to the cluster's IP ("Your IP address is blocked from accessing this post": the
same from other datacenter IPs; no proxy is used), is failed at once with that reason: we hold no TikTok login. A video already saved for another post (the
same video id) is not downloaded again: the row points at the same object.

Download: yt-dlp from the cluster's own IP, never a proxy (no IPRoyal yycstream, no ISP IP: the Deployment carries no
proxy env and the command passes --proxy ""), format "bv*+ba/b" sorted by resolution, then H.264 among equal
resolutions (TikTok's 720p is often HEVC only; such a video is re-encoded to H.264 at the same resolution, libx264
veryfast CRF 23, so it plays in every browser), merged and remuxed to MP4 with the index at the front (faststart, so a browser plays it
while it loads). The file is checked with ffprobe (duration, size, codecs) before the upload. Transient failures (HTTP
429 / 5xx, timeouts, resets) wait RETRY[n] and count as a try.

The edge app's item page (approved accounts, /app/item/video) signs a 1-hour GET link to the object for each page load
(web-app/src/server/s3.ts): the browser plays and downloads straight from the bucket; nothing passes the cluster.
Monitor: panel `videos` (monitor.py) from this table and kv tiktok_video:bucket (the bucket's own object count and
bytes, ListObjectsV2, every SWEEP).
"""
import json, os, re, shutil, signal, socket, subprocess, sys, tempfile, threading, time
from datetime import datetime, timedelta, timezone

import corpus

SCREEN = 20  # = streams.SCREEN (stage 1's floor); tests/test_tiktok_video.py checks they agree
VIDEO_RE = re.compile(r"^https?://(?:www\.|m\.)?tiktok\.com/@[^/?#]+/video/(\d+)", re.I)
PREFIX = "tiktok/"
FORMAT = "bv*+ba/b"
SORT = "res,vcodec:h264,acodec:aac,br"
TRIES = 5
RETRY = [600, 1800, 3600, 6 * 3600]  # seconds before try n+1 after a transient failure
LEASE = 300   # a claim's seconds, renewed while the download is in hand
WORKERS = int(os.environ.get("VIDEO_WORKERS", "2"))
SWEEP = 600   # seconds between sweeps (missed posts) and bucket measurements
SWEEP_DAYS = 3
TIMEOUT = 1800
CRF = 23  # libx264 quality for a re-encoded HEVC video (TikTok's own H.264 is about this)
SAME = "same video as post "  # format of a row whose video was already saved for another post (no download)
MAX_BYTES = 4 * 1024 ** 3  # a single PUT; no TikTok video comes near it
ME = f"{socket.gethostname()}:{os.getpid()}"
STOP = threading.Event()
STARTED, ALIVE = "/tmp/videos-started", "/tmp/videos-alive"

GONE_RE = re.compile(r"video (is )?(not available|unavailable|has been removed|was removed|is private)|private video|"
                     r"removed|deleted|status code 10(204|216|222)|\b404\b|not found|no longer available|"
                     r"Unable to find video in feed|account is private", re.I)
# TikTok shows the post only to a signed-in viewer (age-restricted: "This post may not be comfortable for some
# audiences. Log in for access."): failed at once with that reason; we hold no TikTok login
LOGIN_RE = re.compile(r"Log in for access|login required|requires? (a )?log ?in|cookies-from-browser", re.I)
NOVIDEO_RE = re.compile(r"No video formats found|photo post|is not a video|Requested format is not available", re.I)
TRANSIENT_RE = re.compile(r"\b(429|5\d\d)\b|timed? ?out|timeout|Connection (reset|refused|aborted)|"
                          r"Temporary failure|Name or service not known|IncompleteRead|EOF occurred|"
                          r"Unable to download webpage|Unable to extract|Remote end closed", re.I)

_inflight, _ilock = set(), threading.Lock()


def _now():
    return datetime.now(timezone.utc)


def video_id(url):
    """the TikTok video id of a post URL (…/@user/video/<id>), or None (photo posts, comments, other sites)"""
    m = VIDEO_RE.match(url or "")
    if not m or "#" in url or "comment" in (url.split("?", 1)[1] if "?" in url else ""):
        return None
    return m.group(1)


def queue(post_id, url, screen, stream, how):
    """queue one post's video → True when a new row was made"""
    vid = video_id(url)
    if not vid or post_id is None:
        return False
    r = corpus.db().execute("""INSERT INTO tiktok_videos (post_id, video_id, url, screen, stream, how)
                               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (post_id) DO NOTHING""",
                            (post_id, vid, url, screen, stream, how))
    return r.rowcount > 0


def queue_stage1(posts, res):
    """streams.score_posts, right after stage 1: every TikTok video post whose best stage-1 score reaches SCREEN.
    Never raises (scoring goes on whatever happens here; the worker's sweep catches what this missed)"""
    n = 0
    try:
        for i, r in (res or {}).items():
            p = posts[i]
            if not video_id(p.get("url")):
                continue
            sc = {k: v.get("score") or 0 for k, v in (r.get("streams") or {}).items()}
            if not sc:
                continue
            best = max(sc, key=lambda k: sc[k])
            if sc[best] >= SCREEN and queue(p.get("id"), p.get("url"), sc[best], best, "stage1"):
                n += 1
    except Exception as e:  # noqa: BLE001
        print("tiktok_video: queueing after stage 1:", repr(e)[:300], flush=True)
    return n


# stage 1's score of a stored pair: scores.screen when stage 2 scored it again; a pair stage 1 left under SCREEN keeps
# stage 1's own score (model = ai.MODEL, or NULL from before the model was stored; screen NULL)
STAGE1 = "COALESCE(s.screen, CASE WHEN s.model IS NULL OR s.model = %s THEN s.score END)"


def backlog(days=None, how="backlog"):
    """queue every stored TikTok video post whose stage-1 score reaches SCREEN (posts seen in the last `days` days, or
    all) → rows made. how="backlog" (the CLI) is a manual run: the stage's backlog too; the sweep keeps to the stage's
    work (corpus.seen_cond)"""
    import ai
    since = (datetime.now(corpus.HKT) - timedelta(days=days)).isoformat(timespec="seconds") if days else None
    rows = corpus.db().execute(f"""
        SELECT p.id, p.url, s.stream, {STAGE1} AS s1 FROM posts p JOIN scores s ON s.post_id = p.id
        WHERE p.url ~* 'tiktok\\.com/@[^/?#]+/video/[0-9]+' AND {STAGE1} >= %s
          AND NOT EXISTS (SELECT 1 FROM tiktok_videos t WHERE t.post_id = p.id)
          {"AND p.seen >= %s" if since else ""}
          {"AND " + corpus.seen_cond("p", "tiktok") if how == "sweep" else ""}  -- the sweep never takes the backlog
        ORDER BY p.id DESC""", (ai.MODEL, ai.MODEL, SCREEN) + ((since,) if since else ())).fetchall()
    best = {}
    for r in rows:
        if r["id"] not in best or r["s1"] > best[r["id"]]["s1"]:
            best[r["id"]] = r
    return sum(queue(r["id"], r["url"], r["s1"], r["stream"], how) for r in best.values())


# ---- the download ----

class Gone(Exception):
    """TikTok no longer serves the video"""


class NoVideo(Exception):
    """the post has no video to download (a photo post): failed at once"""


class Transient(Exception):
    """try again later"""


def classify(err):
    s = str(err)
    if NOVIDEO_RE.search(s):
        return NoVideo(s)
    if re.search(r"IP address is blocked from accessing this post", s):  # TikTok refuses this post to our region
        return NoVideo("TikTok refuses this post to the cluster's IP (region; no proxy is used): " + s)
    if LOGIN_RE.search(s):
        return NoVideo("TikTok shows it only when signed in (age-restricted): " + s)
    if TRANSIENT_RE.search(s):
        return Transient(s)
    if GONE_RE.search(s):
        return Gone(s)
    return Transient(s)


def _run(cmd, timeout=TIMEOUT):
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}  # never a proxy
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout or f"exit {p.returncode}").strip().splitlines()[-1][:500])
    return p.stdout


def probe(path):
    j = json.loads(_run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size:stream=codec_type,codec_name,width,height",
                         "-of", "json", path], timeout=120))
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        raise NoVideo("the file has no video stream")
    return {"duration": float(j["format"].get("duration") or 0), "bytes": int(j["format"].get("size") or 0),
            "width": v.get("width"), "height": v.get("height"), "vcodec": v.get("codec_name"),
            "acodec": a.get("codec_name") if a else None}


def download(url, tmp):
    """→ (path of the MP4, info): yt-dlp directly (no proxy), best quality with audio, faststart MP4"""
    try:
        out = _run(["yt-dlp", "--no-playlist", "--no-warnings", "--proxy", "", "--retries", "3", "--fragment-retries", "3",
                    "-f", FORMAT, "-S", SORT, "--merge-output-format", "mp4", "-o", os.path.join(tmp, "v.%(ext)s"),
                    "--print", "after_move:%(filepath)s\t%(format_id)s", url])
    except subprocess.TimeoutExpired:
        raise Transient("yt-dlp timed out")
    except RuntimeError as e:
        raise classify(e)
    line = [x for x in out.splitlines() if "\t" in x]
    if not line:
        raise Transient("yt-dlp wrote nothing")
    src, fmt = line[-1].split("\t", 1)
    dst = os.path.join(tmp, "out.mp4")
    _run(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0", "-c", "copy", "-movflags", "+faststart", "-f", "mp4", dst],
         timeout=600)
    info = probe(dst)
    info["format"] = fmt
    if info["vcodec"] != "h264":  # HEVC (TikTok's 720p): plays in Safari, not in Chrome / Firefox without a hardware
        # decoder (headless Chrome on Linux played its sound with no picture, 2026-10-22), so it is re-encoded to H.264
        # at the same resolution; the audio is kept when it is AAC
        h264 = os.path.join(tmp, "h264.mp4")
        _run(["ffmpeg", "-v", "error", "-y", "-threads", "2", "-i", dst, "-map", "0:v:0", "-map", "0:a?",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", str(CRF), "-pix_fmt", "yuv420p",
              *(["-c:a", "copy"] if info["acodec"] in ("aac", None) else ["-c:a", "aac", "-b:a", "128k"]),
              "-movflags", "+faststart", "-f", "mp4", h264], timeout=TIMEOUT)
        src_codec = info["vcodec"]
        info = probe(h264)
        info["format"] = f"{fmt} ({src_codec} → h264)"
        dst = h264
    if info["acodec"] is None:
        print(f"tiktok_video: {url}: no audio stream (a silent video)", flush=True)
    return dst, info


# ---- the queue ----

def claim():
    r = corpus.db().execute(f"""
        UPDATE tiktok_videos SET claimed_by=%s, claimed_until=now() + make_interval(secs => %s)
        WHERE post_id = (SELECT post_id FROM tiktok_videos t WHERE status='queued' AND (next_try IS NULL OR next_try <= now())
                           AND (claimed_until IS NULL OR claimed_until < now())
                           AND EXISTS (SELECT 1 FROM posts p WHERE p.id = t.post_id
                                       AND {corpus.seen_cond("p", "tiktok")})  -- the stage's backlog waits
                         ORDER BY queued_at DESC, post_id DESC LIMIT 1 FOR UPDATE SKIP LOCKED)
        RETURNING *""", (ME, LEASE)).fetchall()
    if not r:
        return None
    with _ilock:
        _inflight.add(r[0]["post_id"])
    return dict(r[0])


def release(pid):
    with _ilock:
        _inflight.discard(pid)
    corpus.db().execute("UPDATE tiktok_videos SET claimed_by=NULL, claimed_until=NULL WHERE post_id=%s AND claimed_by=%s",
                        (pid, ME))


def renew_loop():
    while True:
        time.sleep(LEASE / 4)
        with _ilock:
            ids = list(_inflight)
        if ids:
            try:
                corpus.db().execute("""UPDATE tiktok_videos SET claimed_until=now() + make_interval(secs => %s)
                                       WHERE post_id = ANY(%s) AND claimed_by=%s""", (LEASE, ids, ME))
            except Exception as e:  # noqa: BLE001
                print("tiktok_video: renewing claims:", repr(e)[:200], flush=True)


def done(pid, key, info):
    corpus.db().execute("""UPDATE tiktok_videos SET status='ok', s3_key=%s, bytes=%s, duration=%s, width=%s, height=%s,
                           vcodec=%s, acodec=%s, format=%s, done_at=now(), error=NULL, next_try=NULL, tries=tries+1
                           WHERE post_id=%s""",
                        (key, info.get("bytes"), info.get("duration"), info.get("width"), info.get("height"),
                         info.get("vcodec"), info.get("acodec"), info.get("format"), pid))


def failed(pid, err):
    """a try failed: Gone → gone, NoVideo → failed at once, else a counted try (failed after TRIES)"""
    c = corpus.db()
    msg = str(err)[:500]
    if isinstance(err, Gone):
        c.execute("UPDATE tiktok_videos SET status='gone', error=%s, done_at=now(), next_try=NULL, tries=tries+1 WHERE post_id=%s",
                  (msg, pid))
        return "gone"
    tries = (c.execute("SELECT tries FROM tiktok_videos WHERE post_id=%s", (pid,)).fetchone() or {"tries": 0})["tries"] + 1
    if isinstance(err, NoVideo) or tries >= TRIES:
        c.execute("UPDATE tiktok_videos SET status='failed', error=%s, done_at=now(), next_try=NULL, tries=%s WHERE post_id=%s",
                  (msg, tries, pid))
        return "failed"
    wait = RETRY[min(tries - 1, len(RETRY) - 1)]
    c.execute("UPDATE tiktok_videos SET error=%s, tries=%s, next_try=now() + make_interval(secs => %s) WHERE post_id=%s",
              (msg, tries, wait, pid))
    return "retry"


def work(row, client):
    pid, vid = row["post_id"], row["video_id"]
    key = PREFIX + vid + ".mp4"
    same = corpus.db().execute("""SELECT * FROM tiktok_videos WHERE video_id=%s AND status='ok' AND post_id<>%s LIMIT 1""",
                               (vid, pid)).fetchone()
    if same:  # the same video saved for another post
        done(pid, same["s3_key"], dict(same, format=f"{SAME}{same['post_id']}"))
        return "ok (same video as post %s)" % same["post_id"]
    tmp = tempfile.mkdtemp(prefix="tt-")
    try:
        path, info = download(row["url"], tmp)
        if info["bytes"] > MAX_BYTES:
            raise NoVideo(f"{info['bytes']} bytes: larger than one upload")
        info["bytes"] = client.put_file(key, path, "video/mp4",
                                        {"Cache-Control": "private, max-age=31536000, immutable"})
        done(pid, key, info)
        return f"ok {info['bytes']} bytes {info['width']}x{info['height']} {info['vcodec']}/{info['acodec']} {info['duration']:.0f}s"
    except (Gone, NoVideo, Transient) as e:
        return failed(pid, e) + ": " + str(e)[:200]
    except Exception as e:  # noqa: BLE001: an upload error, ffmpeg …: a counted try
        return failed(pid, Transient(repr(e))) + ": " + repr(e)[:200]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def work_loop(client):
    while not STOP.is_set():
        row = None
        try:
            with corpus.stage("tiktok"):  # a restart starts from now (corpus.STAGES): the backlog waits
                row = claim()
            if row:
                print(f"tiktok_video: post {row['post_id']} ({row['url']}): {work(row, client)}", flush=True)
        except Exception as e:  # noqa: BLE001: the database away for a moment
            print("tiktok_video work loop:", repr(e)[:300], flush=True)
        finally:
            if row:
                release(row["post_id"])
        if not row:
            STOP.wait(15)


def measure(client):
    u = client.usage(PREFIX)
    corpus.kv_set("tiktok_video:bucket", dict(u, bucket=client.bucket, at=datetime.now(corpus.HKT).isoformat(timespec="seconds")))
    return u


def sweep_loop(client):
    while not STOP.is_set():
        try:
            n = backlog(SWEEP_DAYS, how="sweep")
            if n:
                print(f"tiktok_video: sweep queued {n} post(s) the stage-1 hook missed", flush=True)
            measure(client)
        except Exception as e:  # noqa: BLE001
            print("tiktok_video sweep:", repr(e)[:300], flush=True)
        STOP.wait(SWEEP)


def heartbeat():
    while True:
        open(ALIVE, "w").close()
        time.sleep(30)


def main():
    import s3
    client = s3.from_env()
    if not client:
        sys.exit("VIDEO_S3_ENDPOINT / _ACCESS_KEY / _SECRET_KEY / _BUCKET are not set (Secret video-s3)")
    signal.signal(signal.SIGTERM, lambda *_: (STOP.set(), print("SIGTERM: finishing the downloads in hand", flush=True)))
    corpus.db().execute("SELECT 1")
    open(STARTED, "w").close()
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=renew_loop, daemon=True).start()
    ts = [threading.Thread(target=sweep_loop, args=(client,))] + \
         [threading.Thread(target=work_loop, args=(client,)) for _ in range(WORKERS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    print("drained", flush=True)
    os._exit(0)


def stats():
    c = corpus.db()
    for r in c.execute("""SELECT status, count(*) n, sum(bytes) b FROM tiktok_videos GROUP BY 1 ORDER BY 1""").fetchall():
        print(f"{r['status']:8} {r['n']:6} {(r['b'] or 0) / 1e9:8.3f} GB")
    print("bucket:", corpus.kv_get("tiktok_video:bucket"))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["run"]:
        main()
    elif a[:1] == ["backlog"]:
        print(backlog(float(a[1]) if len(a) > 1 else None), "queued")
    elif a[:1] == ["test"] and len(a) > 1:
        d = tempfile.mkdtemp()
        print(download(a[1], d))
    elif a[:1] == ["stats"]:
        stats()
    else:
        sys.exit(__doc__)
