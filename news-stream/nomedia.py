"""Text only: no job downloads pictures, video, audio or fonts (Deyao, 2026-10-20: "images and audio shouldn't be
downloaded either, because we want to optimise IPRoyal bandwidth as well").

Every job reads text. A picture or a clip is kept as its URL (a string: images.py's post_images, media.py's media
queue), never fetched, except by the two workers whose job is to read them, and only while their flag is on (flags
`pictures`: images.py; `transcription`: media.py, broadcast_fetch.py). This holds on every route: IPRoyal's yycstream
(per GB), the ISP IP and the server's own IP.

The exceptions: TikTok videos (Deyao, 2026-10-22), and the WiseSearch relay's page images and downloads (2026-10-29). tiktok_video.py downloads the video (best quality, with audio)
of every TikTok post whose stage-1 score reaches streams.SCREEN and keeps it in YYC's bucket, from the cluster's own
IP only (never yycstream or the ISP IP). Only TikTok video URLs (tiktok_video.VIDEO_RE) are queued; nothing else is
downloaded under it. EXCEPTIONS names it; tests/test_nomedia.py fails if any other module runs a downloader.

  block(ctx, allow=None)   a Playwright / Patchright browser context (or page) that needs only text: every request
                           for a picture, a video or audio, a font, or an embedded video player is aborted before it
                           leaves the browser (resource types image / media / font; media files by extension; the
                           media CDNs: googlevideo, ytimg, Facebook's scontent / video hosts, Instagram's, WhatsApp's;
                           YouTube / Vimeo / Facebook video player frames). Cloudflare's challenge
                           (challenges.cloudflare.com) is never blocked: its checkbox needs it. allow(frame URL) →
                           True keeps the requests of a page at that URL (Wisers' login page: its captcha). Returns a counter {"blocked": n, by type}.
  blocked(type, url)       the rule itself (tests/test_nomedia.py)
  media_url(url)           a URL whose path is a media file (.jpg, .mp4, .m3u8, .mp3 …)
  media_type(content_type) a response's Content-Type is a picture, video, audio or font
  refuse(url)              the HTTPError (415) a page reader raises instead of reading a media body
"""
import re
import urllib.error
import urllib.parse

# the modules allowed to download video or audio, and why (tests/test_nomedia.py: no other module runs yt-dlp)
EXCEPTIONS = {
    "tiktok_video.py": "TikTok videos of posts passing stage 1, kept in the bucket (Deyao, 2026-10-22)",
    "media.py": "clips' audio for transcription, only while flag `transcription` is on (off since 2026-10-20)",
}
# the modules allowed to fetch pictures (not video or audio), and why
PICTURES = {
    "wisers/relay.py": "WisersOne's page images and download files, only when a person opens or downloads them on the "
                       "WiseSearch page (Deyao, 2026-10-29: the full WiseSearch), from the cluster's own IP, each kept once",
}
BLOCK_TYPES = {"image", "media", "font"}
MEDIA_EXT = re.compile(r"\.(jpe?g|png|gif|webp|avif|bmp|ico|heic|tiff?|mp4|m4v|m4s|m4a|mov|webm|mkv|avi|flv|ts|m3u8|"
                       r"mpd|mp3|aac|ogg|oga|opus|wav|flac|woff2?|ttf|otf|eot)$", re.I)
# hosts that serve only pictures / video / audio (static JS and CSS of these platforms are on other hosts:
# static.xx.fbcdn.net, static.cdninstagram.com, web.whatsapp.com)
MEDIA_HOST = re.compile(r"(^|\.)(googlevideo\.com|ytimg\.com|ggpht\.com|vimeocdn\.com|"
                        r"(scontent|video|external|instagram)[^.]*\.([a-z0-9-]+\.)*fbcdn\.net|"
                        r"(scontent|video|instagram)[^.]*\.([a-z0-9-]+\.)*cdninstagram\.com|"
                        r"mmg\.whatsapp\.net|pps\.whatsapp\.net|media[^.]*\.cdn\.whatsapp\.net)$", re.I)
# embedded video players (frames): their scripts and the video they start are not text
PLAYER = re.compile(r"^https?://([a-z0-9-]+\.)*(youtube(-nocookie)?\.com/embed/|player\.vimeo\.com/|"
                    r"facebook\.com/plugins/video)", re.I)
NEVER = re.compile(r"^https?://challenges\.cloudflare\.com/", re.I)
MEDIA_TYPES = ("image/", "video/", "audio/", "font/", "application/vnd.apple.mpegurl", "application/x-mpegurl",
               "application/dash+xml", "application/font", "application/x-font")


def media_url(url):
    try:
        path = urllib.parse.urlsplit(url or "").path
    except ValueError:
        return False
    return bool(MEDIA_EXT.search(path))


def media_type(content_type):
    ct = (content_type or "").split(";")[0].strip().lower()
    return ct.startswith(MEDIA_TYPES)


def blocked(resource_type, url):
    """True: a request a text-only browser never makes"""
    url = url or ""
    if NEVER.match(url):
        return False
    if resource_type in BLOCK_TYPES or media_url(url) or PLAYER.match(url):
        return True
    host = (urllib.parse.urlsplit(url).hostname or "") if "://" in url else ""
    return bool(MEDIA_HOST.search(host))


def block(ctx, allow=None):
    """abort every media request of this browser context (or page) → the counter {"blocked": n, <type>: n}"""
    n = {"blocked": 0}

    def handle(route):
        req = route.request
        try:
            if blocked(req.resource_type, req.url) and not (allow and allow(req.frame.url)):
                n["blocked"] += 1
                n[req.resource_type] = n.get(req.resource_type, 0) + 1
                return route.abort()
        except Exception:  # noqa: BLE001: a rule that can't decide lets the request go (the page must still load)
            pass
        return route.continue_()
    ctx.route("**/*", handle)
    return n


def refuse(url, why="a media file, not read (text only: nomedia.py)"):
    return urllib.error.HTTPError(url, 415, why, {}, None)
