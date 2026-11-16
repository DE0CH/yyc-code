#!/usr/bin/env python3
"""TV and radio news programmes as searchable text, as Wisers covers 300+ broadcast programmes: each new episode's
audio is fetched (never the video), transcribed by media.py's model (env TRANSCRIBE_MODEL, default Qwen3-ASR-1.7B on
OpenRouter key yyc-stream: the same model and settings as the articles' clips; spend in ai_spend purpose
`transcribe`; each row's text_via is asr:<model>, so the item page names it as 「AI 轉錄（<model>）」) and filed in the corpus as one row per episode: title = programme + episode, url = the broadcaster's
episode page, text = the transcript in Hong Kong Traditional characters (spoken Cantonese as spoken) with a [mm:ss]
mark about once a minute, sid bc_<broadcaster> (corpus.classify: source type "Broadcast", website = the broadcaster,
board = the programme). A transcript longer than PART_CHARS is filed as several rows (url#t=<seconds>, title
"…（第n部分）"), so nothing is cut at corpus.TEXT_MAX.

    python3 broadcast_fetch.py                 one run: every programme's episodes of the last DAYS days not yet filed
    python3 broadcast_fetch.py list            what a run would transcribe (no download, no transcription)
    python3 broadcast_fetch.py test <url>      transcribe one audio/HLS/YouTube URL and print the text (writes nothing)
    python3 broadcast_fetch.py backfill N      like a run, looking back N days (where the archive keeps them)

Sources (PROGRAMMES below):
  RTHK radio: the news programmes' daily archive files (rthkaod2022.akamaized.net …/radio/archive/<channel>/<slug>/
    m4a/<YYYYMMDD>.m4a), aired dates and times from RTHK's own timetable (/radio/getTimetable); current-affairs
    programmes from their programme page's episode list (each episode page has its archive file).
  RTHK TV: the programme page's episodes; each episode page's HLS, read through its audio-only rendition.
  TVB, 有線 (i-Cable), Now: their full bulletins / programmes on their own YouTube channels (the channel's RSS feed,
    titles matched by a pattern), audio only (yt-dlp, format 139, ~20 MB an hour) through YYC's IPRoyal ISP IP
    (media.youtube_clip_audio: the MEDIA_ROUTES youtube route, one client at a time under media.youtube_lock),
    because YouTube refuses datacenter IPs; never the per-GB yycstream pool. No route set: not filed, not retried.
  新城 Metro Finance (FM 104): the half-hour archive MP3s of its news programmes (arch.metroradio.hk).
  商業電台 881903: its archive needs a signed-in 881903 account (playback API answers 10004), so its news programmes
    are filed from the archive's own section titles and summaries (text, no transcript), marked 「節目摘要」.
Resumable: an episode whose URL is already in the corpus is skipped; a failed one is tried again on the next runs, at
most 3 times (kv bc:fail:<url>). No episode starts after RUN_SECONDS (env BC_RUN_SECONDS), so a run ends inside the
CronJob's deadline; the rest wait for the next run, newest first. When OpenRouter refuses for credit or the key's
limit, the run stops there (ai.limit_hit logs it, the monitor shows it, Deyao gets one Discord message). Needs
ffmpeg and yt-dlp on PATH.
"""
import html, json, os, re, sys, tempfile, time, urllib.parse, urllib.request
from datetime import datetime, timedelta

import ai, corpus, datefmt, media, resi

HKT = corpus.HKT
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
RUN_SECONDS = int(os.environ.get("BC_RUN_SECONDS", "2700"))  # no episode starts after this (the job's deadline: 3300 s)
DAYS = 2
PART_CHARS = 15000
MARK_EVERY = 60  # seconds between [mm:ss] marks

RTHK = "RTHK 香港電台"
PROGRAMMES = [
    # RTHK Radio 1 news programmes: the archive file of each aired date (timetable), linked to news.rthk.hk's page
    {"sid": "bc_rthk", "who": RTHK, "name": "晨早新聞天地", "kind": "rthk_dated", "channel": "radio1", "slug": "hktoday", "cmsid": 90},
    {"sid": "bc_rthk", "who": RTHK, "name": "午間新聞天地", "kind": "rthk_dated", "channel": "radio1", "slug": "12oclocknews", "cmsid": 86},
    {"sid": "bc_rthk", "who": RTHK, "name": "傍晚新聞天地", "kind": "rthk_dated", "channel": "radio1", "slug": "6oclocknews", "cmsid": 87},
    {"sid": "bc_rthk", "who": RTHK, "name": "晚間新聞天地", "kind": "rthk_dated", "channel": "radio1", "slug": "eveningnews", "cmsid": 88},
    # RTHK current affairs (radio and TV): the programme page's episodes
    {"sid": "bc_rthk", "who": RTHK, "name": "千禧年代", "kind": "rthk_page", "path": "radio/radio1/programme/HK2000"},
    {"sid": "bc_rthk", "who": RTHK, "name": "自由風自由PHONE", "kind": "rthk_page", "path": "radio/radio1/programme/openline_openview"},
    {"sid": "bc_rthk", "who": RTHK, "name": "星期六問責", "kind": "rthk_page", "path": "tv/dtt31/programme/accountabilitytv"},
    {"sid": "bc_rthk", "who": RTHK, "name": "新聞天地（電視）", "kind": "rthk_page", "path": "tv/dtt31/programme/hktoday_tv"},
    {"sid": "bc_rthk", "who": RTHK, "name": "鏗鏘集", "kind": "rthk_page", "path": "tv/dtt31/programme/hkcc"},
    # TVB, i-Cable, Now: their own YouTube channels (full bulletins / programmes)
    {"sid": "bc_tvb", "who": "TVB 無綫新聞", "name": "TVB 新聞", "kind": "youtube", "channel": "UCvdc-bj7Onv5XhEC5z8_JUg",
     "match": r"六點半新聞報道|TVB午間新聞"},
    {"sid": "bc_icable", "who": "有線新聞", "name": "有線新聞", "kind": "youtube", "channel": "UC_q7e5XYJB0JDGagcF0KW0w",
     "match": r"六點新聞報道|午間香港新聞"},
    {"sid": "bc_now", "who": "NOW 新聞", "name": "Now 財經 新聞", "kind": "youtube", "channel": "UCChMBgirwM2nnT3Bbe8METQ",
     "match": r"【經緯線】|時事全方位"},
    # 新城 Metro Finance (FM 104): the archive's half-hour files of its news programmes
    {"sid": "bc_metro", "who": "新城電台", "name": "新城財經台", "kind": "metro",
     "page": "https://www.metroradio.com.hk/MetroFinance/Multimedia/ProgramArchive.aspx", "match": r"新聞|中國財經60分"},
    # 商業電台 881903: section titles + summaries of its news programmes (playback needs an 881903 account)
    {"sid": "bc_crhk", "who": "商業電台 881903", "name": "商業電台", "kind": "crhk_text", "programs": [8, 24, 15, 36, 59]},
]


# ---- fetching ----

def get(url, timeout=30, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-HK,zh;q=0.9", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def hkt(day, hm="00:00"):
    return datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=HKT)


def rthk_timetable(channel, day):
    """[(start "HH:MM", slug)] of an RTHK radio channel on a day (YYYY-MM-DD)"""
    d = json.loads(get(f"https://www.rthk.hk/radio/getTimetable?d={day.replace('-', '')}&c={channel}"))
    return [(t["start"], t["programme"]) for t in d.get("timetable", [])]


def rthk_dated(p, days):
    """the news programme on each aired day: its archive file and news.rthk.hk's page for that day"""
    out = []
    today = datetime.now(HKT)
    for i in range(days):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        starts = [s for s, slug in rthk_timetable(p["channel"], day) if slug == p["slug"]]
        if not starts:
            continue
        ymd = day.replace("-", "")
        out.append({"url": f"https://news.rthk.hk/rthk/ch/news-programmes/this-episode.htm?cmsid={p['cmsid']}"
                           f"&livetime={ymd}000000",
                    "title": f"{p['name']} {day}", "published": hkt(day, starts[0]),
                    "media": f"https://rthkaod2022.akamaized.net/m4a/radio/archive/{p['channel']}/{p['slug']}/m4a/{ymd}.m4a/master.m3u8"})
    return out


EP_MEDIA = re.compile(r"https://rthk[av]od2022\.akamaized\.net/(?:m4a/radio/archive|mp4/tv)/[^'\"\s<]+?master\.m3u8")


def rthk_episode(url):
    """an RTHK episode page → title, published, media (its full archive file / HLS), or None"""
    s = get(url)
    t = re.search(r"<title>([^<]*)", s)
    title = html.unescape(t.group(1)).split("|")[-1].strip() if t else ""
    m = EP_MEDIA.search(s)
    d = re.search(r'<div class="vDate">(\d\d)/(\d\d)/(\d{4})', s)
    if not (m and d):
        return None
    day = f"{d.group(3)}-{d.group(2)}-{d.group(1)}"
    hm = re.search(r"HKT (\d\d:\d\d) -", s)
    return {"title": title, "published": hkt(day, hm.group(1) if hm else "00:00"), "media": m.group(0)}


def rthk_page(p, days):
    s = get("https://www.rthk.hk/" + p["path"])
    since = (datetime.now(HKT) - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    links = list(dict.fromkeys(re.findall(r'href="(https://www\.rthk\.hk/' + re.escape(p["path"]) + r'/episode/\d+)"', s)))
    for url in links[:max(3, days * 2)]:
        if url in done_urls([url]):
            continue
        try:
            ep = rthk_episode(url)
        except Exception as e:
            print("rthk episode:", url, repr(e)[:120], flush=True)
            continue
        if ep and ep["published"].strftime("%Y-%m-%d") >= since:
            title = f"{p['name']}｜{ep['title']}" if ep["title"] and ep["title"] != p["name"] else f"{p['name']} {ep['published']:%Y-%m-%d}"
            out.append({"url": url, "title": title, "published": ep["published"], "media": ep["media"]})
    return out


def youtube(p, days):
    s = get(f"https://www.youtube.com/feeds/videos.xml?channel_id={p['channel']}")
    since = datetime.now(HKT) - timedelta(days=days)
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", s, re.S):
        vid = re.search(r"<yt:videoId>([^<]+)", e).group(1)
        title = html.unescape(re.search(r"<title>([^<]*)", e).group(1))
        pub = datetime.fromisoformat(re.search(r"<published>([^<]+)", e).group(1)).astimezone(HKT)
        if pub >= since and re.search(p["match"], title):
            out.append({"url": f"https://www.youtube.com/watch?v={vid}", "title": title, "published": pub,
                        "media": f"https://www.youtube.com/watch?v={vid}"})
    return out


def metro(p, days):
    """today's archive page: one row per half-hour file of a matching programme (the page lists today's files)"""
    s = get(p["page"])
    out = []
    blocks = re.split(r'rptProgram_ctl\d+_pnlProgram', s)[1:]
    for b in blocks:
        txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", b[:1500])))
        name = re.search(r"\d{4} - \d{4} (.+?)(?: [A-Z][A-Za-z ]+)?(?: \(| $|\s{2})", txt.split(");\">")[-1] + "  ")
        name = re.sub(r"\s+[A-Za-z0-9 &'.-]+$", "", name.group(1).strip()) if name else ""
        if not re.search(p["match"], name):
            continue
        for url, stamp in dict.fromkeys(re.findall(r"(https://arch\.metroradio\.hk/\w+/\d{8}/\w+_(\d{12})\.mp3)", b)):
            pub = datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=HKT)
            out.append({"url": url, "title": f"{p['who']}｜{name} {datefmt.date_time_year(pub)}", "published": pub, "media": url,
                        "section": name})
    return out


def crhk_text(p, days):
    """881903 news programmes: each archive section's title and summary (the audio needs an 881903 account)"""
    since = (datetime.now(HKT) - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for pid in p["programs"]:
        s = get(f"https://www.881903.com/program/{pid}")
        i = s.find("VueApp.main(")
        if i < 0:
            continue
        d, _ = json.JSONDecoder().raw_decode(s[i + 12:])
        pname = (d.get("program_details") or {}).get("program_title") or str(pid)
        for a in d.get("program_archives") or []:
            for sec in a.get("program_archive_sections") or []:
                if (sec.get("section_date") or "") < since or not sec.get("caption"):
                    continue
                desc = re.sub(r"\s*\n\s*", "\n", html.unescape(re.sub(r"<[^>]+>", "\n", sec.get("description") or ""))).strip()
                pub = datetime.strptime(sec["section_start_datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=HKT)
                out.append({"url": f"https://www.881903.com/program/{pid}#s{sec['program_archive_section_id']}",
                            "title": f"{pname}｜{sec['caption']}"[:300], "published": pub, "section": pname,
                            "text": "節目摘要（非逐字稿）\n" + (sec["caption"] + "\n" + desc).strip()})
    return out


KINDS = {"rthk_dated": rthk_dated, "rthk_page": rthk_page, "youtube": youtube, "metro": metro, "crhk_text": crhk_text}


def done_urls(urls):
    return set(corpus.ids_for_urls(urls))


# ---- audio → text ----

def audio_url(url):
    """an HLS master with an audio-only rendition → that rendition (so no video is downloaded)"""
    if ".m3u8" not in url:
        return url
    try:
        m = re.search(r'#EXT-X-MEDIA:TYPE=AUDIO[^\n]*URI="([^"]+)"', get(url))
    except Exception:
        return url
    return urllib.parse.urljoin(url, m.group(1)) if m else url


def transcribe(ep, kind):
    """an episode's audio → ([(start seconds, text in Hong Kong Traditional)], seconds, text_via), with media.py's
    model and settings (the same transcription as the articles' clips; spend: ai_spend purpose transcribe; text_via
    asr:<model>): a YouTube episode's through the ISP route (media.youtube_clip_audio), any other's read directly"""
    with tempfile.TemporaryDirectory() as tmp:
        if kind == "youtube":
            wav = media.youtube_clip_audio(ep["media"], tmp)["wav"]
        else:
            wav = media.fetch_audio({"url": audio_url(ep["media"]), "kind": "direct"}, tmp)
        pieces, secs, cost = media.transcribe_wav(wav)
    return [(st, media.to_hk(media.clean(t))) for st, t in pieces if media.clean(t)], secs, "asr:" + media.MODEL


def parts(segs):
    """[(start, text)] → [(part start seconds, text with [mm:ss] marks)], each at most PART_CHARS characters"""
    out, cur, start, last_mark = [], [], None, -MARK_EVERY
    size = 0
    for st, t in segs:
        if cur and size + len(t) > PART_CHARS:
            out.append((start, "".join(cur).strip()))
            cur, size, start, last_mark = [], 0, None, -MARK_EVERY
        if start is None:
            start = st
        if st - last_mark >= MARK_EVERY:
            mark = f"\n[{int(st // 3600)}:{int(st % 3600 // 60):02d}:{int(st % 60):02d}] " if st >= 3600 else \
                   f"\n[{int(st // 60):02d}:{int(st % 60):02d}] "
            cur.append(mark)
            size += len(mark)
            last_mark = st
        cur.append(t + "，" if not re.search(r"[。！？，、；：…]$", t) else t)
        size += len(t) + 1
    if cur:
        out.append((start or 0, "".join(cur).strip()))
    return out


def file_episode(p, ep):
    """transcribe one episode and add its row(s) → minutes of audio"""
    if "text" in ep:  # 881903: summary text only
        corpus.ingest([{"url": ep["url"], "title": ep["title"], "text": ep["text"], "sid": p["sid"], "source": p["who"],
                        "section": ep.get("section") or p["name"], "published": ep["published"]}])
        return 0
    t0 = time.time()
    segs, secs, via = transcribe(ep, p["kind"])
    text_parts = parts(segs)
    if not text_parts:
        raise RuntimeError("no speech found")
    rows = []
    for i, (st, text) in enumerate(text_parts):
        rows.append({"url": ep["url"] if i == 0 else f"{ep['url']}#t={int(st)}",
                     "title": ep["title"] if len(text_parts) == 1 else f"{ep['title']}（第{i + 1}部分）",
                     "text": text, "sid": p["sid"], "source": p["who"], "section": ep.get("section") or p["name"],
                     "published": ep["published"] + timedelta(seconds=int(st))})
    corpus.ingest(rows)
    corpus.db().execute("UPDATE posts SET text_via=%s WHERE url = ANY(%s)",  # the model or subtitles, named on the item page
                        (via, [r["url"] for r in rows]))
    print(f"  {ep['title'][:60]}: {secs / 60:.0f} min audio, {time.time() - t0:.0f} s, {len(rows)} row(s), "
          f"{sum(len(r['text']) for r in rows)} chars", flush=True)
    return secs / 60


AIRED_H = {"rthk_dated": 3, "rthk_page": 3, "metro": 0.75}  # hours after the start before an episode is filed
ONLY = [x for x in os.environ.get("BC_ONLY", "").split(",") if x]  # test runs: only these programme names


def todo(days):
    eps = []
    for p in PROGRAMMES:
        if ONLY and p["name"] not in ONLY:
            continue
        try:
            got = KINDS[p["kind"]](p, days)
        except Exception as e:
            print(f"{p['who']} {p['name']}: listing failed: {e!r}"[:200], flush=True)
            continue
        have = done_urls([e["url"] for e in got])
        ready = datetime.now(HKT) - timedelta(hours=AIRED_H.get(p["kind"], 0))  # the archive file is whole
        for e in got:
            if e["published"] <= ready and e["url"] not in have and int(corpus.kv_get("bc:fail:" + e["url"], 0) or 0) < 3:
                eps.append((p, e))
    eps.sort(key=lambda pe: pe[1]["published"], reverse=True)  # newest first
    return eps


def run(days=DAYS):
    if not media.on():  # transcription off (flag `transcription`; Deyao, 2026-10-20: cost): no episode is filed
        print("broadcast: transcription is off (flag transcription): nothing filed", flush=True)
        return
    eps = todo(days)
    print(f"broadcast: {len(eps)} episode(s) to file", flush=True)
    minutes, n, t0 = 0.0, 0, time.monotonic()
    for p, ep in eps:
        if time.monotonic() - t0 >= RUN_SECONDS:
            print(f"broadcast: {RUN_SECONDS} s used (the job's deadline is near); the rest next run", flush=True)
            break
        try:
            minutes += file_episode(p, ep)
            n += 1
        except ai.LimitReached:  # OpenRouter refused (credit / the key's limit): ai.limit_hit logged and told Deyao
            break
        except resi.ProxyOut as e:  # IPRoyal out of traffic (resi.py): a pause, not a try; the rest wait for the next run
            print(f"broadcast: proxy out of traffic, the rest next run: {e!r}"[:200], flush=True)
            break
        except (media.NoDirect, media.Gone) as e:  # no YouTube route, or a private video: never tried again
            corpus.kv_add("bc:fail:" + ep["url"], 3)
            print(f"  not filed: {ep['title'][:60]} {ep['url']}: {e}"[:300], flush=True)
        except media.HostBusy as e:  # a live / upcoming bulletin, YouTube's 429: the next run, no try counted
            print(f"  later: {ep['title'][:60]}: {e}"[:200], flush=True)
        except Exception as e:
            corpus.kv_add("bc:fail:" + ep["url"], 1)
            print(f"  failed: {ep['title'][:60]} {ep['url']}: {e!r}"[:300], flush=True)
    print(f"broadcast: filed {n} episode(s), {minutes:.0f} min of audio", flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["list"]:
        for p, ep in todo(int(a[1]) if len(a) > 1 else DAYS):
            print(f"{ep['published']:%Y-%m-%d %H:%M}  {p['sid']:10} {ep['title'][:70]}  {ep['url']}")
    elif a[:1] == ["test"] and len(a) == 2:
        t0 = time.time()
        segs, secs, _ = transcribe({"media": a[1]}, "youtube" if "youtube.com" in a[1] else "")
        for st, text in parts(segs):
            print(f"== part at {st:.0f}s\n{text}\n")
        print(f"{secs:.0f} s of audio in {time.time() - t0:.0f} s", file=sys.stderr)
    elif a[:1] == ["backfill"] and len(a) == 2:
        run(int(a[1]))
    elif not a:
        run()
    else:
        print(__doc__)
        sys.exit(2)
