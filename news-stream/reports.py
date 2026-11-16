#!/usr/bin/env python3
"""K-Matrix's daily reports and AI themes (brochure pp.6–11, 3–5).

  reports.py daily [--day YYYY-MM-DD] [--if-due]
      For every profile (insights.profiles(): the streams + the admin's profiles), build the previous HKT day's
      reports and store them in Postgres (table report_files, served at /reports/<day>/<profile id>/<file> by the edge
      app: 報告). They are not emailed (Deyao, 2026-10-23: the daily email reports are no longer needed):
        format1.html     the "Social Profile" page (pp.7–9): SPI, Sentiment Index, topics/people/sources, the AI
                         summary, every topic grouped by media channel with links, "cached" copy and a 轉寄 link
        format2.html     the summary page (p.10 left): SPI + CSS/DVS/DPS/CCS with levels and day-on-day change,
                         7-day SPI and Consumer Sentiment Score charts (spi.png, css.png), the AI summary and
                         recommended action
        report.pdf       the one-page PDF AI report (p.10 right), HTML printed by headless Google Chrome
        hot_topics.xlsx  "Hot Topic Listing" (p.11), the same shape as the dashboard's export
        posts.xlsx/.docx the post listing in the Galbot sample's table form
        summary.json     the data used (scores, metrics, the AI summary); the AI summary is also kept in
                         corpus kv daily:<pid>:<day> (數據分析's daily summary)
      The scores, counts, charts and the top participants and websites depend on which sources and keywords YYC
      follows: each report carries insights.BIAS_NOTE as a footnote, summary.json as "note" (Deyao, 2026-10-16;
      README "Figures that depend on our selection").
      Before the reports it regenerates each profile's AI themes over the last 7 days (make_themes), when the
      window has posts without a theme.
      --if-due: run only when the HKT hour has reached settings()["report_hour"] and that day isn't done yet
      (the yyc-reports timer runs this hourly, so the admin's report hour takes effect without a unit change).
  reports.py profile PID [--day D]               one profile (build one profile's report by hand)
  reports.py themes PID                          regenerate one profile's themes
  reports.py backfill [DIR]                      store the files of an old reports folder (default
                                                 $DATA_DIR/reports/<day>/<profile>/<file>) in report_files; a file
                                                 already stored is left as it is. Prints what it stored.
"""
import html as H
import io, json, os, re, shutil, subprocess, sys, tempfile, traceback, urllib.parse
from collections import Counter
from datetime import datetime

import ai, corpus, datefmt, insights, titles

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(corpus.DATA_DIR, "reports")  # where the reports were files (before report_files); backfill
# a report file's Content-Type by its extension (the edge app serves only these extensions)
TYPES = {".html": "text/html; charset=utf-8", ".pdf": "application/pdf", ".png": "image/png", ".json": "application/json",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".csv": "text/csv; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8"}
DAY_RE, PID_RE, NAME_RE = r"\d{4}-\d{2}-\d{2}", r"[A-Za-z0-9_-]+", r"[A-Za-z0-9_.-]+"  # what a /reports/ URL can name
BASE = os.environ.get("PUBLIC_BASE", "https://newswatch.yyc-internal.com")
CHROME = os.environ.get("CHROME", "/usr/bin/google-chrome")

TYPE_ZH = {"News": "新聞", "Newspaper": "報章", "Magazine": "雜誌", "Forum": "論壇", "Social Media": "社交媒體", "Video": "影片", "Blog": "網誌", "Official": "官方"}
TYPE_ORDER = ["News", "Newspaper", "Magazine", "Forum", "Social Media", "Video", "Blog", "Official"]
SENT_ZH = {"positive": "正面", "neutral": "中性", "negative": "負面", "mixed": "褒貶參半", "irony": "反諷"}
LEVEL_ZH = {"Very Low": "極低", "Low": "低", "Medium": "中", "High": "高", "Very High": "極高"}
SLEVEL_ZH = {"Very Negative": "非常負面", "Slightly Negative": "略為負面", "Neutral": "中性",
             "Slightly Positive": "略為正面", "Very Positive": "非常正面"}
LEVEL_COLOR = {"Very Low": "#8a8f8c", "Low": "#5d7f6c", "Medium": "#c27c0e", "High": "#1f7a4d", "Very High": "#0d5c36"}
SLEVEL_COLOR = {"Very Negative": "#b42318", "Slightly Negative": "#d9480f", "Neutral": "#6b7280",
                "Slightly Positive": "#2f9e44", "Very Positive": "#1f7a4d"}
SCORE_NAMES = {"CSS": ("Consumer Sentiment Score", "消費者情緒分數"), "DVS": ("Discussion Volume Score", "討論量分數"),
               "DPS": ("Discussion Participation Score", "討論參與分數"), "CCS": ("Consumer Coverage Score", "覆蓋分數"),
               "SPI": ("Social Popularity Index", "社交熱度指數")}
FONT = "'Noto Sans HK','Noto Sans CJK HK','PingFang HK','Microsoft JhengHei',Arial,sans-serif"


def log(*a):
    print(datetime.now(corpus.HKT).strftime("%F %T"), "reports:", *a, flush=True)


def e(s):
    return H.escape(str(s if s is not None else ""))


# Latin-script post text in a report or alert (Deyao, 2026-10-21: "Apostrophe and quotation mark for English sources
# are wrong"): FONT's Noto Sans HK / PingFang draw ‘ ’ “ ” full-width ("Netflix’ s"), so an English title or excerpt
# is set in a Latin font first (the web-app's lib/lang.tsx does the same on the site). Chinese text stays in FONT.
FONT_LATIN = "Arial,Helvetica,'Liberation Sans','DejaVu Sans','Noto Sans HK',sans-serif"


def latin(s):
    """is this Latin-script text (not Chinese, Japanese or Korean)?"""
    s = str(s or "")
    return (len(re.findall(r"[A-Za-z]", s)) >= 2 and not re.search(r"[぀-ヿ가-힯]", s)
            and not titles.chinese(s))


def el(s, by=None):
    """e(s), in a Latin font when s (or by: the post's title) is Latin-script"""
    return (f'<span lang="en" style="font-family:{FONT_LATIN}">{e(s)}</span>' if latin(s if by is None else by)
            else e(s))


def yesterday():
    return insights.day_add(insights.today(), -1)


def score_level(k, v):
    return insights.sentiment_level((v - 50) * 2) if k == "CSS" else insights.level(v)


def level_zh(k, lv):
    return SLEVEL_ZH.get(lv, lv) if k == "CSS" else LEVEL_ZH.get(lv, lv)


def level_color(k, lv):
    return SLEVEL_COLOR.get(lv, "#333") if k == "CSS" else LEVEL_COLOR.get(lv, "#333")


def change(c):
    if c is None:
        return "—"
    return f"{'▲' if c > 0 else '▼' if c < 0 else '＝'} {'+' if c > 0 else ''}{c}%"


def change_color(c):
    return "#6b7280" if not c else "#1f7a4d" if c > 0 else "#b42318"


def footnote():
    """the emails' footnote under the scores, counts and rankings: they depend on which sources and keywords we follow"""
    return (f'<tr><td style="padding:10px 16px 0;font-size:11.5px;line-height:1.5;color:#7a8780">'
            f'註：{e(insights.BIAS_NOTE)}</td></tr>')


# ---- data ----

def collect(p, day):
    """everything the reports for one profile and day need (no model call)"""
    pid = p["id"]
    ps = insights.select(pid, day, day)
    prior = set(insights.members(pid, insights.day_add(day, -30), insights.day_add(day, -1)))
    m = insights.metrics(ps, day, day, prior)
    sc = insights.scores(pid, day)
    sc.pop("series", None)
    for k in sc:
        sc[k]["level"] = score_level(k, sc[k]["value"])
    ws, we, wps = window(pid, day)
    return {"p": p, "day": day, "posts": ps, "metrics": m, "scores": sc, "series": insights.spi_series(pid, day, 7),
            "topics": insights.topics(ps), "hot": insights.hot_topics(pid, day, day, 5, ps),
            "authors": insights.top_authors(pid, day, day, 5, ps), "sites": insights.top_sources(pid, day, day, 5, ps),
            "window": (ws, we), "wposts": wps, "wtopics": insights.topics(wps),
            "wmetrics": insights.metrics(wps, day, insights.day_add(day, 1), prior)}


def window(pid, day):
    """the Social Profile email's period, as K-Matrix's ("Report Date: 2026-10-12 to 2026-10-13", sent 09:10 with
    items up to that morning): the 24 hours up to the report hour on the next day → (start, end, posts)"""
    h = int(insights.settings().get("report_hour", 9))
    start, end = f"{day}T{h:02d}:00", f"{insights.day_add(day, 1)}T{h:02d}:00"
    ps = insights.select(pid, day, insights.day_add(day, 1))
    return start, end, [x for x in ps if start <= (x.get("published") or x.get("seen") or "")[:16] < end]


def daily_context(p, day, ps, md):
    """the posts the daily summary read, listed under it on 數據分析 (insights.make_context)"""
    return insights.make_context("daily", p["id"], f"AI 每日摘要：{p.get('title') or p['name']}（{datefmt.date_year(day)}）", f"{datefmt.date_year(day)} 的每日報告",
                              md, ps[:ai.DAILY_POSTS], len(ps))


def ai_summary(p, day, ps, refresh=False):
    """one ai.daily_summary call per profile and day (kept in kv daily:<pid>:<day>; re-made only when the day's
    post count changed, e.g. a "run now" during the day)"""
    key = f"daily:{p['id']}:{day}"
    hit = corpus.kv_get(key)
    if hit and not refresh and hit.get("posts") == len(ps):
        return hit
    try:
        r = ai.daily_summary(p.get("title") or p["name"], day, ps)
    except ValueError:  # malformed JSON from the model (an unescaped quote): ask once more
        try:
            r = ai.daily_summary(p.get("title") or p["name"], day, ps)
        except ValueError as ex:
            log(f"{p['id']} {day}: AI summary failed twice ({ex!r}); report without it")
            return {"heading": "（今日 AI 摘要未能產生）", "points": [], "action": "", "posts": -1}
    pts = [{"topic": str(x.get("topic", "")).strip(), "text": str(x.get("text", "")).strip()}
           for x in (r.get("points") or []) if isinstance(x, dict)]
    out = {"heading": str(r.get("heading", "")).strip(), "points": pts, "action": re.sub(r"^\s*建議行動\s*[：:]\s*", "", str(r.get("action", ""))).strip(),
           "posts": len(ps), "at": insights.now().isoformat(timespec="seconds")}
    out["markdown"] = "\n".join([out["heading"]] + [f"- **{x['topic']}**：{x['text']}" for x in pts] +
                                ([f"\n**建議行動：** {out['action']}"] if out["action"] else []))
    out["html"] = ai.md_to_html(out["markdown"])
    out["context"] = daily_context(p, day, ps, out["markdown"])
    corpus.kv_set(key, out)
    return out


# ---- format 1: the Social Profile email ----

def _mq(s):
    """percent-encode only what a mailto: URL needs (raw UTF-8 stays: keeps the email small; mail apps accept it)"""
    return "".join(c if c not in ' %&#?=+"<>\'\r\n\t' else "%%%02X" % ord(c) for c in s)


def mailto(t, lead):
    body = f"原文：{t['url']}\n詳情：{BASE}/item/{lead['id']}"
    return f"mailto:?subject={_mq(t['title'][:80])}&body={_mq(body)}"


CHANNELS = [("Forum & Blog", "論壇及網誌", ("Forum", "Blog")), ("Social Media", "社交媒體", ("Social Media", "Video")),
            ("News", "新聞", corpus.NEWS_TYPES)]  # K-Matrix's three sections of the Social Profile email


def topic_tags(t):
    """K-Matrix's "(😐neutral 👀monitor 📈trend)": the topic's most common sentiment and labels, three at most"""
    sent = Counter(x.get("sentiment") for x in t["posts"] if x.get("sentiment")).most_common(1)
    labs = Counter(l for x in t["posts"] for l in x.get("labels") or [] if l in ai.LABELS)
    tags = ([ai.SENT_EMOJI[sent[0][0]] + sent[0][0]] if sent and sent[0][0] in ai.SENT_EMOJI else []) + \
           [ai.LABELS[l][0] + l for l, _ in labs.most_common(2)]
    return f"({' '.join(tags)}) " if tags else ""


def topic_meta(t, threads):
    """→ (thread row or the topic's first post, related count, posts + comments in the thread)"""
    key = t["posts"][-1].get("thread") or re.sub(r"#.*$", "", t["posts"][-1]["url"])
    th = threads.get(key) or t["posts"][-1]
    eng = th.get("engagement") or {}
    total = 1 + max(int(eng.get("comments") or 0), int(eng.get("replies") or 0))
    return th, len(t["posts"]), max(total, len(t["posts"]))


def format1(d, summ):
    p, day, m, sc = d["p"], d["day"], d["wmetrics"], d["scores"]
    title = p.get("title") or p["name"]
    spi = sc["SPI"]
    ws, we = d["window"]
    turls = {x.get("thread") for x in d["wposts"] if x.get("thread")}
    threads = {x["url"]: x for x in corpus.posts(corpus.ids_for_urls(turls).values())} if turls else {}
    points = "".join(f'<p style="margin:0 0 6px"><b style="color:#0d5c36">{e(x["topic"])}：</b>{e(x["text"])}</p>'
                     for x in summ.get("points", []))
    action = (f'<p style="margin:10px 0 0"><b style="color:#0d5c36">建議行動：</b>{e(summ["action"])}</p>'
              if summ.get("action") else "")
    sections = []
    for en, zh, types in CHANNELS:
        ts = [t for t in d["wtopics"] if (t["posts"][-1].get("source_type") or "News") in types]
        if not ts:
            continue
        rows_ps = [x for t in ts for x in t["posts"]]
        si100 = round(50 + insights.sentiment_index(rows_ps) / 2, 1)
        ranked = sorted(((t,) + topic_meta(t, threads) for t in ts), key=lambda r: (r[2], r[0]["last"] or ""), reverse=True)
        rows = []
        for t, th, n, total in ranked:
            lead = t["posts"][0]  # the newest matching post or comment: K-Matrix's excerpt
            url = th.get("url") or t["url"]
            meta = " - ".join(x for x in (th.get("website"), th.get("board"), th.get("region") or "HK",
                                          datefmt.date(th.get("published") or th.get("seen"))) if x)
            news = th.get("source_type") in corpus.NEWS_TYPES
            ex = re.sub(r"\s+", " ", (lead.get("text") or "")).strip()
            ex = "" if news or not ex else ex[:200] + ("…" if len(ex) > 200 else "")
            related = "" if news else f" - {n} 則相關，共 {total} 則帖子及留言"
            tt = th.get("title") or t["title"]
            body = re.sub(r"\s+", " ", th.get("text") or "")
            if not news and len(tt) > 28 and body[:20] and body[:20] in re.sub(r"\s+", " ", tt):
                tt = tt[:25] + "..."  # a post without a title of its own (Facebook, Telegram): K-Matrix's short form
            rows.append(
                f'<tr><td class="t">{e(topic_tags(t))}<a class="h" href="{e(url)}">{el(tt)}</a>'
                f'<div class="m">{e(meta)}{e(related)} - <a class="c" href="{BASE}/item/{th.get("id") or lead["id"]}">details</a>'
                f' - <a class="l" href="{e(mailto(dict(t, url=url), lead))}">轉寄</a></div>'
                + (f'<div class="x">{el(ex)}</div>' if ex else "") + "</td></tr>")
        sections.append(
            f'<tr><td style="background:#1f7a4d;color:#fff;padding:10px 16px;font-size:16px">{e(p["name"])} - {zh} {en}'
            f'<div style="font-size:13px;margin-top:3px;color:#d6f5e3"><b style="color:#fff">{si100}</b> 情緒分數 · '
            f'<b style="color:#fff">{len(ts)}</b> 個話題</div></td></tr>' + "".join(rows))
    lv = spi["level"]
    return f"""<!doctype html><html lang="zh-Hant-HK"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} 社交概況 {datefmt.date_year(day)}</title>
<style>td.t{{padding:12px 16px;border-bottom:1px solid #e3e8e5;font-size:13px;color:#5d6b64}} a.h{{color:#1f7a4d;font-size:17px;text-decoration:none}}
div.m{{color:#5d6b64;font-size:12px;margin-top:3px}} a.c{{color:#1f7a4d;font-weight:bold}} a.l{{color:#1f7a4d}}
div.x{{color:#333;font-size:13px;margin-top:4px}}</style></head>
<body style="margin:0;padding:0;background:#eef1ef;font-family:{FONT};word-break:break-word;overflow-wrap:anywhere">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#eef1ef"><tr><td align="center" style="padding:12px 8px">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="max-width:600px;background:#ffffff;border-collapse:collapse;table-layout:fixed">
<tr><td style="background:#1d2a24;color:#e6ece8;padding:14px 16px;font-size:13px">YYC Social Profile 社交概況<br>報告時段：{datefmt.date_time_year(ws)} 至 {datefmt.date_time_year(we)}（香港時間）</td></tr>
<tr><td align="center" style="background:#26352e;color:#fff;padding:22px 16px">
<div style="font-size:22px;font-weight:bold">{e(title)}</div>
<div style="font-size:17px;color:#7ee2a8;margin-top:6px">社交熱度指數 Social Popularity Index</div>
<div style="font-size:18px;margin-top:6px">{datefmt.date_year(day)}</div>
<div style="font-size:34px;font-weight:bold;margin-top:2px">{spi['value']}</div>
<div style="font-size:13px">SPI 級別：<b style="color:#7ee2a8">{LEVEL_ZH.get(lv, lv)} {lv}</b> · 較前一日 {change(spi['change_pct'])}</div></td></tr>
<tr><td style="padding:12px 16px;border-bottom:1px solid #e3e8e5">
<table width="100%" cellpadding="4" cellspacing="0" role="presentation" style="font-size:15px;color:#1d2a24"><tr>
<td width="50%" align="center"><b>{m['sentiment_index_100']}</b> 情緒分數</td><td width="50%" align="center"><b>{m['topics']}</b> 個話題</td></tr><tr>
<td align="center"><b>{m['authors']}</b> 人參與</td><td align="center"><b>{m['websites']}</b> 個來源</td></tr></table>
<div style="font-size:11px;color:#7a8780;text-align:center">情緒分數 0 = 非常負面，50 = 中性，100 = 非常正面 · 共 {m['posts']} 則帖子及留言</div></td></tr>
<tr><td style="padding:14px 16px;font-size:15px;line-height:1.6;color:#1d2a24;background:#f6faf7">
<p style="margin:0 0 8px;font-weight:bold;color:#0d5c36">{e(summ.get('heading'))}</p>{points}{action}
<p style="margin:8px 0 0;font-size:11px;color:#7a8780">以上摘要由 AI 根據當日帖子生成。</p></td></tr>
{''.join(sections)}
{footnote()}
<tr><td style="padding:14px 16px;font-size:12px;color:#5d6b64">網上版本及其他報告：<a href="{BASE}/reports/{day}/{e(p['id'])}/format1.html" style="color:#1f7a4d">{BASE}/reports/</a>
 · 計算方法：<a href="{BASE}/dashboard" style="color:#1f7a4d">儀表板</a></td></tr>
</table></td></tr></table></body></html>"""


# ---- format 2: email summary + charts ----

def charts(d, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    f = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    if os.path.exists(f):
        font_manager.fontManager.addfont(f)
    plt.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]
    days = [datefmt.axis(x["day"]) for x in d["series"]]
    for key, name, color, fname in (("SPI", "社交熱度指數 Social Popularity Index（過去 7 日）", "#2563c9", "spi.png"),
                                    ("CSS", "消費者情緒分數 Consumer Sentiment Score（過去 7 日）", "#c92a2a", "css.png")):
        ys = [x[key] for x in d["series"]]
        fig, ax = plt.subplots(figsize=(6, 2.6), dpi=150)
        ax.plot(days, ys, color=color, marker="o", markersize=4, linewidth=2)
        for i, y in enumerate(ys):
            ax.annotate(f"{y:g}", (i, y), textcoords="offset points", xytext=(0, 6), ha="center", fontsize=7, color="#333")
        if key == "CSS":
            ax.set_ylim(0, 105)
            ax.axhline(50, color="#9aa5a0", linewidth=0.8, linestyle=":")
        else:
            lo, hi = min(ys), max(ys)
            pad = max(5, (hi - lo) * 0.25)
            ax.set_ylim(max(0, lo - pad), min(105, hi + pad))
        ax.grid(axis="y", color="#e3e8e5", linestyle="--", linewidth=0.7)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color("#9aa5a0")
        ax.tick_params(colors="#4a5550", labelsize=8, length=0)
        ax.set_title(name, fontsize=9, color="#1d2a24", loc="left")
        fig.tight_layout()
        fig.savefig(os.path.join(out, fname))
        plt.close(fig)


def interpretation(sc):
    """a plain reading of the scores (rule-based, from the numbers)"""
    spi = sc["SPI"]
    c = spi["change_pct"]
    if c is None:
        trend = f"社交熱度指數為 {spi['value']}（{LEVEL_ZH[spi['level']]}）。"
    elif c >= 10:
        trend = f"社交熱度指數較前一日上升 {c}%，"
    elif c <= -10:
        trend = f"社交熱度指數較前一日下降 {abs(c)}%，"
    else:
        trend = f"社交熱度指數與前一日相若（{'+' if c > 0 else ''}{c}%），"
    if c is not None:
        ups = [SCORE_NAMES[k][1] for k in ("DVS", "DPS", "CCS") if (sc[k]["change_pct"] or 0) >= 10]
        downs = [SCORE_NAMES[k][1] for k in ("DVS", "DPS", "CCS") if (sc[k]["change_pct"] or 0) <= -10]
        if c >= 10 and ups:
            trend += "主要由" + "、".join(ups) + "上升帶動。"
        elif c <= -10 and downs:
            trend += "主要因" + "、".join(downs) + "下降。"
        else:
            trend += "各項分數變化不大。" if not (ups or downs) else "各項分數升跌互見。"
    rng = {"Very High": "遠高於", "High": "高於", "Medium": "處於", "Low": "低於", "Very Low": "遠低於"}[spi["level"]]
    return trend + f"目前熱度{rng}過去 30 日的正常範圍。"


def score_cell(k, s):
    lv = s["level"]
    return (f'<td width="50%" valign="top" style="padding:10px 8px;font-size:13px;color:#1d2a24">{SCORE_NAMES[k][1]}<br>'
            f'<span style="color:#5d6b64;font-size:11px">{SCORE_NAMES[k][0]} ({k})</span><br>'
            f'<b style="font-size:20px">{s["value"]}</b> <span style="color:{change_color(s["change_pct"])}">({change(s["change_pct"])})</span><br>'
            f'級別：<b style="color:{level_color(k, lv)}">{level_zh(k, lv)} {lv}</b></td>')


def format2(d, summ):
    p, day, sc = d["p"], d["day"], d["scores"]
    url = f"{BASE}/reports/{day}/{p['id']}"
    spi = sc["SPI"]
    title = p.get("title") or p["name"]
    points = "".join(f'<p style="margin:0 0 6px"><b style="color:#0d5c36">{e(x["topic"])}：</b>{e(x["text"])}</p>'
                     for x in summ.get("points", []))
    return f"""<!doctype html><html lang="zh-Hant-HK"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} 每日監察報告 {datefmt.date_year(day)}</title></head>
<body style="margin:0;padding:0;background:#eef1ef;font-family:{FONT};word-break:break-word;overflow-wrap:anywhere">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#eef1ef"><tr><td align="center" style="padding:12px 8px">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="max-width:600px;background:#fff;border-collapse:collapse;table-layout:fixed">
<tr><td style="background:#1f7a4d;color:#fff;padding:12px 16px;font-size:14px"><b>YYC 社交公關監察服務</b><br>{datefmt.date_year(day)} 社交公關監察報告 - 每日</td></tr>
<tr><td style="padding:16px 16px 4px;font-size:14px;color:#1d2a24">你好，<br>以下是 <b>{e(title)}</b> 的每日社交公關監察報告（{datefmt.date_year(day)}，香港時間）。</td></tr>
<tr><td style="padding:8px 16px;font-size:20px;font-weight:bold;color:#1d2a24;border-bottom:2px solid #1f7a4d">{e(title)}{' (' + e(p['name']) + ')' if p['name'] != title else ''}</td></tr>
<tr><td style="padding:10px 16px;font-size:14px;line-height:1.6;color:#1d2a24">{e(interpretation(sc))}</td></tr>
<tr><td style="padding:6px 16px;color:#1d2a24"><div style="font-size:14px">社交熱度指數 Social Popularity Index (SPI)</div>
<b style="font-size:26px">{spi['value']}</b> <span style="color:{change_color(spi['change_pct'])}">({change(spi['change_pct'])})</span><br>
<span style="font-size:13px">級別：<b style="color:{level_color('SPI', spi['level'])}">{LEVEL_ZH[spi['level']]} {spi['level']}</b></span></td></tr>
<tr><td style="padding:4px 8px"><table width="100%" cellpadding="0" cellspacing="0" role="presentation">
<tr>{score_cell('CSS', sc['CSS'])}{score_cell('DVS', sc['DVS'])}</tr><tr>{score_cell('DPS', sc['DPS'])}{score_cell('CCS', sc['CCS'])}</tr></table></td></tr>
<tr><td style="padding:8px 16px"><img src="{url}/spi.png" width="568" alt="過去 7 日社交熱度指數" style="width:100%;max-width:568px;height:auto;display:block"></td></tr>
<tr><td style="padding:8px 16px"><img src="{url}/css.png" width="568" alt="過去 7 日消費者情緒分數" style="width:100%;max-width:568px;height:auto;display:block"></td></tr>
<tr><td style="padding:14px 16px;font-size:15px;line-height:1.6;color:#1d2a24;background:#f6faf7">
<p style="margin:0 0 8px;font-weight:bold;color:#0d5c36">{e(summ.get('heading'))}</p>{points}
<p style="margin:10px 0 0"><b style="color:#0d5c36">建議行動：</b>{e(summ.get('action') or '—')}</p>
<p style="margin:8px 0 0;font-size:11px;color:#7a8780">以上摘要由 AI 根據當日帖子生成。</p></td></tr>
{footnote()}
<tr><td style="padding:12px 16px;font-size:12px;color:#5d6b64">附件：一頁 PDF AI 報告（report.pdf）、熱門話題列表（hot_topics.xlsx）、帖子列表（posts.xlsx）。
網上版本：<a href="{url}/format2.html" style="color:#1f7a4d">{BASE}/reports/</a></td></tr>
</table></td></tr></table></body></html>"""


# ---- the one-page PDF ----

def pct(n, tot):
    return f"{n} ({round(100 * n / max(1, tot), 1)}%)"


def slv(si):
    lv = insights.sentiment_level(si)
    return f'<span class="sl" style="background:{SLEVEL_COLOR[lv]}"></span>{SLEVEL_ZH[lv]}'


def pdf_html(d, summ):
    p, day, sc, m = d["p"], d["day"], d["scores"], d["metrics"]
    regions = "全部"  # a stream's posts come from every region and source type: the scorer decides, not a filter
    types = ", ".join(TYPE_ZH.get(x, x) for x in TYPE_ORDER)
    lvls = ["Very High", "High", "Medium", "Low", "Very Low"]
    slvls = ["Very Positive", "Slightly Positive", "Neutral", "Slightly Negative", "Very Negative"]
    rows = []
    for k in ("CSS", "DVS", "DPS", "CCS"):
        cur = sc[k]["level"]
        rows.append(f"<tr><td><b>{SCORE_NAMES[k][1]}</b> {k}</td>" + "".join(
            f'<td class="{"on" if n == cur else ""}" style="{"color:" + level_color(k, n) if n == cur else ""}">{level_zh(k, n)}</td>'
            for n in (slvls if k == "CSS" else lvls)) + "</tr>")
    tot, auth = max(1, m["posts"]), max(1, m["authors"])
    hot = "".join(f'<tr><td class="t">{el(t["title"][:60])}</td><td>{e(t["website"])}</td><td>{slv(t["sentiment_index"])}</td>'
                  f'<td>{pct(t["matched_post_count"], tot)}</td><td>{pct(t["participants"], auth)}</td><td>{t["day_span"]}</td></tr>'
                  for t in d["hot"]) or '<tr><td colspan="6">—</td></tr>'
    au = "".join(f'<tr><td class="t">{e(a["author"][:30])}</td><td>{e(a["website"])}</td><td>{slv(a["sentiment_index"])}</td>'
                 f'<td>{a["topics"]}</td><td>{pct(a["posts"], tot)}</td><td>{e((a["most_active_board"] or "")[:20])}</td></tr>'
                 for a in d["authors"]) or '<tr><td colspan="6">當日帖子沒有作者資料（新聞來源多數不註明作者）。</td></tr>'
    st = "".join(f'<tr><td class="t">{e(s["website"])}</td><td>{TYPE_ZH.get(s["source_type"], s["source_type"])}</td><td>{slv(s["sentiment_index"])}</td>'
                 f'<td>{s["topics"]}</td><td>{pct(s["posts"], tot)}</td><td>{s["participants"]}</td></tr>'
                 for s in d["sites"]) or '<tr><td colspan="6">—</td></tr>'
    views = f"超過 {m['view_count']} 人次瀏覽了相關討論。" if m["view_count"] else "瀏覽次數：N/A（來源沒有提供瀏覽數字）。"
    box = lambda k: (f'<div class="sc"><div class="n">{SCORE_NAMES[k][1]} ({k})</div><div class="v">{sc[k]["value"]}'
                     f' <small style="color:{change_color(sc[k]["change_pct"])}">({change(sc[k]["change_pct"])})</small></div>'
                     f'<div class="l" style="color:{level_color(k, sc[k]["level"])}">{level_zh(k, sc[k]["level"])} {sc[k]["level"]}</div></div>')
    spi = sc["SPI"]
    return f"""<!doctype html><html lang="zh-Hant-HK"><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 9mm 10mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: {FONT}; font-size: 8.6pt; color: #1d2a24; margin: 0; zoom: ZOOM; }}
.top {{ display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 2.5px solid #1f7a4d; padding-bottom: 4px; }}
.brand {{ font-size: 15pt; font-weight: 700; color: #1f7a4d; }} .ttl {{ text-align: right; font-size: 12pt; font-weight: 700; }}
.ttl small {{ display: block; font-size: 7.5pt; font-weight: 400; color: #5d6b64; }}
.grid {{ display: grid; grid-template-columns: 34% 66%; gap: 8px; margin-top: 7px; }}
table {{ width: 100%; border-collapse: collapse; }}
.info td {{ border: 1px solid #cfd8d3; padding: 3px 5px; }} .info td:first-child {{ background: #eef4f0; width: 42%; font-weight: 700; }}
.big {{ font-size: 15pt; font-weight: 700; color: #1f7a4d; }}
.scores {{ display: grid; grid-template-columns: 1fr 1fr; gap: 5px; }}
.sc {{ border: 1px solid #cfd8d3; border-top: 3px solid #1f7a4d; padding: 4px 6px; }} .sc .n {{ font-size: 7.6pt; color: #4a5550; }}
.sc .v {{ font-size: 15pt; font-weight: 700; }} .sc .v small {{ font-size: 8pt; }} .sc .l {{ font-weight: 700; font-size: 8pt; }}
h2 {{ font-size: 10pt; margin: 9px 0 3px; padding-bottom: 2px; border-bottom: 2px solid #1f7a4d; }}
.lv td {{ padding: 2.5px 5px; border-bottom: 1px solid #e3e8e5; color: #9aa5a0; text-align: center; }} .lv td:first-child {{ text-align: left; color: #1d2a24; }}
.lv td.on {{ font-weight: 700; }}
.add {{ margin: 5px 0 0; line-height: 1.45; }}
.lst {{ table-layout: fixed; }} .lst th {{ background: #eef4f0; text-align: left; padding: 3px 4px; font-size: 7.8pt; white-space: nowrap; }}
.lst td {{ padding: 2.6px 4px; border-bottom: 1px solid #e3e8e5; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.sl {{ display: inline-block; width: 7px; height: 7px; border-radius: 2px; margin-right: 3px; }}
.ai p {{ margin: 2px 0; line-height: 1.4; }}
.foot {{ margin-top: 7px; font-size: 7pt; color: #7a8780; display: flex; justify-content: space-between; }}
</style></head><body>
<div class="top"><div class="brand">YYC 新聞監察</div><div class="ttl">社交公關監察報告 - 每日 Social PR Monitoring Report - Daily
<small>報告產生日期：{datefmt.date_time_year(insights.now())}（香港時間）</small></div></div>
<div class="grid"><table class="info">
<tr><td>監察項目</td><td>{e(p.get('title') or p['name'])}</td></tr>
<tr><td>社交熱度指數 (SPI)</td><td><span class="big">{spi['value']}</span> <span style="color:{change_color(spi['change_pct'])}">({change(spi['change_pct'])})</span></td></tr>
<tr><td>熱度級別</td><td style="color:{level_color('SPI', spi['level'])};font-weight:700">{LEVEL_ZH[spi['level']]} {spi['level']}</td></tr>
<tr><td>來源地區</td><td>{e(regions)}</td></tr><tr><td>報告日期</td><td>{datefmt.date_year(day)}</td></tr>
<tr><td>來源類型</td><td>{e(types)}</td></tr></table>
<div class="scores">{box('CSS')}{box('DVS')}{box('DPS')}{box('CCS')}</div></div>
<h2>AI 摘要 AI Summary</h2>
<table class="lv">{''.join(rows)}</table>
<p class="add"><b>其他資料：</b>{m['authors']} 人在 {m['websites']} 個網站參與了 {m['topics']} 個話題 / {m['posts']} 篇帖子的討論。{views}</p>
<p class="add"><b>分數解讀：</b>{e(interpretation(sc))}</p>
<div class="ai add"><p><b>{e(summ.get('heading'))}</b></p>{''.join(f'<p><b>{e(x["topic"])}：</b>{e(x["text"])}</p>' for x in summ.get('points', [])[:5])}</div>
<p class="add"><b>建議行動：</b>{e(summ.get('action') or '—')}</p>
<h2>五大熱門話題 Top 5 Hot Topics</h2>
<table class="lst"><colgroup><col style="width:44%"><col style="width:15%"><col style="width:12%"><col style="width:11%"><col style="width:11%"><col style="width:7%"></colgroup><tr><th>標題</th><th>網站</th><th>情緒級別</th><th>帖子</th><th>參與者</th><th>日數</th></tr>{hot}</table>
<h2>五大最活躍參與者 Top 5 Most Active Participants</h2>
<table class="lst"><colgroup><col style="width:28%"><col style="width:15%"><col style="width:12%"><col style="width:8%"><col style="width:12%"><col style="width:25%"></colgroup><tr><th>名稱</th><th>網站</th><th>情緒級別</th><th>話題</th><th>帖子</th><th>最活躍版面</th></tr>{au}</table>
<h2>五大最活躍網站 Top 5 Most Active Websites</h2>
<table class="lst"><colgroup><col style="width:44%"><col style="width:15%"><col style="width:12%"><col style="width:11%"><col style="width:11%"><col style="width:7%"></colgroup><tr><th>名稱</th><th>類別</th><th>情緒級別</th><th>話題</th><th>帖子</th><th>參與者</th></tr>{st}</table>
<p class="add note">註：{e(insights.BIAS_NOTE)}</p>
<div class="foot"><span>分數計算方法見 {BASE}/dashboard（YYC 自訂公式）</span><span>YYC</span></div>
</body></html>"""


def pdf_pages(path):
    return len(re.findall(rb"/Type\s*/Page[^s]", open(path, "rb").read()))


def print_pdf(html_text, out_pdf):
    """headless Chrome → PDF; shrinks the page (CSS zoom) until it fits one A4 page"""
    tmp = tempfile.mkdtemp(prefix="yyc-pdf-")
    try:
        for zoom in ("1", "0.92", "0.85", "0.78", "0.7", "0.62"):
            src = os.path.join(tmp, "report.html")
            open(src, "w").write(html_text.replace("zoom: ZOOM;", f"zoom: {zoom};"))
            cmd = [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                   "--no-first-run", "--no-pdf-header-footer", f"--user-data-dir={tmp}/profile",
                   f"--print-to-pdf={out_pdf}", "file://" + src]
            if os.path.exists(out_pdf):
                os.remove(out_pdf)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=dict(os.environ, HOME=tmp))
            if not os.path.isfile(out_pdf):
                raise RuntimeError("chrome print failed: " + (r.stderr or "")[-300:])
            if pdf_pages(out_pdf) <= 1:
                break
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- post listing (Galbot sample) ----

GALBOT_COLS = ["Date", "Media outlet", "Category", "Sentiment", "Format", "Topic", "Full text", "Link"]


def galbot_row(x, text_chars=2000):
    try:
        dt = datetime.fromisoformat(x.get("published") or x.get("seen"))
        date = f"{dt.month}/{dt.day}/{dt.year}"
    except Exception:
        date = x.get("day")
    outlet = x.get("author") if x.get("source_type") in ("Social Media", "Video") and x.get("author") else x.get("website")
    return [date, outlet or "", x.get("source_type") or "", SENT_ZH.get(x.get("sentiment"), "—"), "Online",
            x["title"], (x.get("text") or "")[:text_chars], x["url"]]


def posts_docx(p, day, ps, path):
    import docx
    from docx.oxml.ns import qn
    from docx.shared import Pt, Mm
    d = docx.Document()
    s = d.sections[0]
    s.page_width, s.page_height = Mm(210), Mm(297)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(s, side, Mm(12))
    st = d.styles["Normal"]
    st.font.name = "Arial"  # Latin text and its ‘ ’ “ ” (Noto Sans HK draws them full-width; FONT_LATIN)
    st.font.size = Pt(8)
    st.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Noto Sans HK")
    run = d.add_paragraph().add_run(f"{p.get('title') or p['name']} Daily News Monitoring")
    run.bold, run.font.size = True, Pt(14)
    d.add_paragraph(f"{datefmt.date_year(day)}（香港時間）· {len(ps)} 篇")
    t = d.add_table(rows=1, cols=len(GALBOT_COLS))
    t.style = "Table Grid"
    for i, c in enumerate(GALBOT_COLS):
        t.rows[0].cells[i].text = c
        t.rows[0].cells[i].paragraphs[0].runs[0].bold = True
    for x in ps:
        cells = t.add_row().cells
        for i, v in enumerate(galbot_row(x, 600)):
            cells[i].text = str(v)
    widths = [Mm(16), Mm(22), Mm(18), Mm(13), Mm(13), Mm(38), Mm(46), Mm(20)]
    for row in t.rows:
        for i, w in enumerate(widths):
            row.cells[i].width = w
    d.save(path)


# ---- one profile ----

def build(p, day, use_ai=True):
    """build every report file for one profile and day in a new temporary folder and store them in report_files (the
    day's earlier files of that profile are replaced) → {"dir", "files", "summary"}. The folder is only for sending
    the same files by email: the caller removes it (done(res))."""
    out = tempfile.mkdtemp(prefix=f"yyc-report-{day}-{p['id']}-")
    try:
        res = _build(p, day, out, use_ai)
        store(day, p["id"], out)
        return res
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise


def done(res):
    """remove a build's temporary folder"""
    if res and res.get("dir"):
        shutil.rmtree(res["dir"], ignore_errors=True)


def _write(path, data):
    """a file of the build, closed (so complete) before store() reads it"""
    with open(path, "wb") as f:
        f.write(data.encode() if isinstance(data, str) else data)


def xlsx(sheets, meta=None):
    """sheets: [(name, header, rows)] → bytes of an .xlsx (openpyxl)"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    wb.remove(wb.active)
    for name, header, rows in sheets:
        ws = wb.create_sheet(name[:31])
        r0 = 1
        if meta:
            for k, v in meta:
                ws.append([k, v])
                ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
            ws.append([])  # an empty row: no cells, so max_row stays on the last meta row while append() moves on
            r0 = ws.max_row + 2
        ws.append(header)
        for c in ws[r0]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F7A4D")
        for row in rows:
            ws.append([("" if v is None else v if isinstance(v, (int, float)) else str(v)) for v in row])
            for ci, v in enumerate(row, 1):
                if isinstance(v, str) and v.startswith("http"):
                    ws.cell(row=ws.max_row, column=ci).hyperlink = v
        for ci, h in enumerate(header, 1):
            ws.column_dimensions[ws.cell(row=r0, column=ci).column_letter].width = min(60, max(10, len(str(h)) + 4))
        ws.freeze_panes = ws.cell(row=r0 + 1, column=1)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


HOT_COLS = ["Top", "Title", "Web Site", "Board", "Sentiment Level", "Sentiment Index (%)", "Sentiment Score",
            "Contribution (%)", "All Post Count", "Period Post Count", "Matched Post Count", "Participant Count",
            "View Count", "Correlation Index (%)", "Initial Date", "End Date", "Discussion Days", "Original Link", "Topic ID"]


def hot_row(i, t):
    return [i, t["title"], t["website"], t.get("board"), t["sentiment_level"], t["sentiment_index"], t["sentiment_score"],
            t["contribution"], t["all_post_count"], t["period_post_count"], t["matched_post_count"], t["participants"],
            "N/A" if t["view_count"] is None else t["view_count"], t["correlation_index"], t["initial_date"], t["end_date"],
            t["day_span"], t["url"], t["id"]]


def hot_topics_xlsx(p, day, ps, top=200):
    """the day's hot-topic listing as Excel, as 數據分析's export (web-app/src/server/dashboard-export.ts) makes it"""
    meta = [("Profile Name", p["name"]),
            ("Posts", f"AI score {insights.threshold(p['id'])} or more (0-100) against the profile's scope"),
            ("Scope", p.get("brief") or ""), ("Search Period", f"{datefmt.date_year(day)} to {datefmt.date_year(day)}"),
            ("Report Generation Date", insights.today())]
    rows = [hot_row(i + 1, t) for i, t in enumerate(insights.hot_topics(p["id"], day, day, top, ps))]
    return xlsx([("Hot Topic Listing", HOT_COLS, rows)], meta)


def _build(p, day, out, use_ai):
    ps = insights.select(p["id"], day, day)
    if not ps and not window(p["id"], day)[2]:
        js = {"profile": p["id"], "name": p["name"], "day": day, "posts": 0, "note": "當日沒有相關帖子，沒有產生報告。"}
        _write(os.path.join(out, "summary.json"), json.dumps(js, ensure_ascii=False, indent=1))
        return {"dir": out, "files": ["summary.json"], "summary": js}
    d = collect(p, day)
    summ = dict(ai_summary(p, day, ps or d["wposts"]) if use_ai else (corpus.kv_get(f"daily:{p['id']}:{day}") or {}))
    summ["action"] = re.sub(r"^\s*建議行動\s*[：:]\s*", "", summ.get("action") or "")
    _write(os.path.join(out, "format1.html"), format1(d, summ))
    charts(d, out)
    _write(os.path.join(out, "format2.html"), format2(d, summ))
    print_pdf(pdf_html(d, summ), os.path.join(out, "report.pdf"))
    _write(os.path.join(out, "hot_topics.xlsx"), hot_topics_xlsx(p, day, ps))
    meta = [("Profile Name", p["name"]), ("Date", day), ("Posts", len(ps))]
    _write(os.path.join(out, "posts.xlsx"), xlsx([("Post Listing", GALBOT_COLS, [galbot_row(x) for x in ps])], meta))
    posts_docx(p, day, ps, os.path.join(out, "posts.docx"))
    js = {"profile": p["id"], "name": p["name"], "title": p.get("title"), "day": day, "posts": len(ps),
          "scores": d["scores"], "spi_series": d["series"], "metrics": d["metrics"], "ai_summary": summ,
          "note": insights.BIAS_NOTE,
          "interpretation": interpretation(d["scores"]),
          "hot_topics": [{k: t[k] for k in ("title", "url", "website", "matched_post_count", "participants", "sentiment_level")} for t in d["hot"]],
          "top_authors": d["authors"], "top_websites": d["sites"], "method": insights.METHOD,
          "built": insights.now().isoformat(timespec="seconds")}
    _write(os.path.join(out, "summary.json"), json.dumps(js, ensure_ascii=False, indent=1))
    return {"dir": out, "files": sorted(os.listdir(out)), "summary": js}


# ---- the stored files (report_files): what /reports/ lists and serves (web-app/src/server/reports.ts) ----

def store(day, pid, folder):
    """the report files of one profile and day = the files in `folder` (one transaction: the day's earlier set of
    that profile is replaced, so a file the new build no longer makes is gone, as when the folder was the volume)"""
    rows = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and re.fullmatch(NAME_RE, name):
            with open(path, "rb") as f:
                rows.append((day, pid, name, TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream"), f.read()))
    c = corpus.db()
    with c:
        c.execute("DELETE FROM report_files WHERE day = %s AND profile = %s", (day, pid))
        c.executemany("INSERT INTO report_files (day, profile, name, ctype, body) VALUES (%s, %s, %s, %s, %s)", rows)
    return [r[2] for r in rows]


def backfill(folder=None):
    """store an old reports folder (<day>/<profile>/<file>) in report_files; files already stored stay as they are
    (a newer build wins) → [(day, profile, name)] stored"""
    folder = folder or REPORTS_DIR
    out = []
    if not os.path.isdir(folder):
        return out
    c = corpus.db()
    for day in sorted(os.listdir(folder)):
        if not re.fullmatch(DAY_RE, day) or not os.path.isdir(os.path.join(folder, day)):
            continue
        for pid in sorted(os.listdir(os.path.join(folder, day))):
            d = os.path.join(folder, day, pid)
            if not re.fullmatch(PID_RE, pid) or not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                path = os.path.join(d, name)
                if not re.fullmatch(NAME_RE, name) or not os.path.isfile(path):
                    continue
                with open(path, "rb") as f:
                    body = f.read()
                r = c.execute("""INSERT INTO report_files (day, profile, name, ctype, body, created)
                                 VALUES (%s, %s, %s, %s, %s, to_timestamp(%s)) ON CONFLICT DO NOTHING""",
                              (day, pid, name, TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream"),
                               body, os.path.getmtime(path)))
                if r.rowcount:
                    out.append((day, pid, name))
    return out


# ---- themes ----

def make_themes(pid, days=7, max_posts=300):
    """K-Matrix's Dynamic AI Theme Generation: themes over the profile's last 7 days (≤300 posts, newest first),
    stored in kv themes:<pid>, every post's theme set in the tags table"""
    p = insights.profile(pid)
    if not p:
        return {"error": "no such profile"}
    end = insights.today()
    ps = insights.select(pid, insights.day_add(end, -days + 1), end)[:max_posts]
    if not ps:
        return {"themes": corpus.kv_get("themes:" + pid, {}).get("themes", []), "posts": 0,
                "note": "no posts in the last 7 days"}
    names, assign = ai.themes(p.get("title") or p["name"], ps)
    other = next((n for n in names if n.startswith("其他")), names[-1])
    pairs = [(x["id"], assign.get(x["id"], other)) for x in ps]
    corpus.set_themes(pid, pairs)
    at = insights.now().isoformat(timespec="seconds")
    corpus.kv_set("themes:" + pid, {"themes": names, "at": at, "posts": len(ps)})
    counts = Counter(t for _, t in pairs)
    return {"themes": names, "at": at, "posts": len(ps), "counts": [{"theme": n, "posts": counts.get(n, 0)} for n in names]}


def themes_due(pid, days=7):
    """regenerate only when the window has posts without a theme (saves model calls on quiet profiles)"""
    end = insights.today()
    return any(not x.get("theme") for x in insights.select(pid, insights.day_add(end, -days + 1), end))


# ---- entry points ----

def run_profile(pid, day, use_ai=True):
    """build one profile's reports by hand → file URLs; use_ai=False makes no model call and
    uses the day's saved AI summary, if any"""
    p = insights.profile(pid)
    if not p:
        return {"error": "no such profile"}
    res = None
    try:
        res = build(p, day, use_ai=use_ai)
        return {"profile": pid, "day": day, "posts": res["summary"].get("posts", 0),
                "files": [f"/reports/{day}/{pid}/{f}" for f in res["files"]]}
    except Exception as ex:
        traceback.print_exc()
        return {"error": repr(ex)[:300]}
    finally:
        done(res)


def daily(day=None, if_due=False):
    day = day or yesterday()
    done_key = f"daily_done:{day}"
    if if_due and (insights.now().hour < int(insights.settings().get("report_hour", 9)) or corpus.kv_get(done_key)):
        return
    log(f"daily run for {day}")
    for p in insights.profiles():
        try:
            if themes_due(p["id"]):
                r = make_themes(p["id"])
                log(f"{p['id']}: themes {r.get('themes')} over {r.get('posts')} posts")
        except Exception as ex:
            log(f"{p['id']}: themes failed: {ex!r}"[:300])
        res = None
        try:
            res = build(p, day)
            log(f"{p['id']} {day}: {res['summary'].get('posts', 0)} posts, files {res['files']}")
        except Exception as ex:
            log(f"{p['id']} {day}: report failed: {ex!r}"[:300])
            traceback.print_exc()
        finally:
            done(res)
    corpus.kv_set(done_key, insights.now().isoformat(timespec="seconds"))
    log(f"AI spend today so far: {ai.spend()}")


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["daily", "profile", "themes", "backfill"])
    ap.add_argument("pid", nargs="?", help="profile id (profile, themes) or the folder (backfill)")
    ap.add_argument("--day")
    ap.add_argument("--if-due", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "daily":
        daily(a.day, a.if_due)
    elif a.cmd == "backfill":
        got = backfill(a.pid)
        for x in got:
            print("/reports/" + "/".join(x))
        print(f"stored {len(got)} file(s)", file=sys.stderr)
    elif a.cmd == "profile":
        print(json.dumps(run_profile(a.pid, a.day or yesterday()), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(make_themes(a.pid), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
