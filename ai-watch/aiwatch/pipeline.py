"""What happens to a case, step by step. Each step is a task kind (db.task); `advance` queues the next step once the
current one has no task left, so a case moves on by itself and a worker restart loses nothing.

  news       (browser) the last 60 days of news about the subject from Google News (HK, Traditional Chinese)
  questions  (model)   the recent issues in that news, and questions about the subject from each stakeholder's side
                       (positive, negative, neutral); the user ticks which to ask  → case status 'ready'
  ask        (browser) one question to one app (engines.py)                       → status 'asking'
  extract    (model)   one answer split into its statements, with tone, the newest date it writes, whether it names
                       the subject
  sources    (fetch + model) one cited page read: what it says, its tone, its kind, and for each statement that cites
                       it whether the page supports it
  group      (model)   one question's statements across the apps: the same statement grouped, agreement and
                       contradiction, risk to the subject's image; then one case-wide merge
  summary    (code)    the overview numbers                                          → status 'done'

All text the model writes for the pages is Traditional Chinese with Hong Kong usage (YYC CLAUDE.md).
"""
import asyncio
import datetime as dt
import json
import re
import urllib.parse

import psycopg
from . import db, engines, llm

HK = "Write every text field in Traditional Chinese with Hong Kong usage (繁體中文，香港用語), never Simplified Chinese."
STAKEHOLDERS = {
    "event": ["記者", "受影響人士及家屬", "一般市民", "立法會議員及政界", "監管及執法機構", "相關業界", "選民"],
    "person": ["記者", "一般市民", "支持者", "批評者", "立法會議員及政界", "商界", "選民"],
    "company": ["記者", "投資者", "客戶", "求職者", "監管機構", "競爭對手", "一般市民"],
}
ENGINES = {  # the apps offered, with the label each has on the pages, in the order they are shown. DeepSeek is left out
    # (Deyao, 2026-10-27): its sign-up is behind hCaptcha (engines.deepseek stays for when that changes)
    "google_aio": "Google AI 概覽", "google_aimode": "Google AI 模式", "copilot": "Microsoft Copilot",
    "perplexity": "Perplexity", "dola": "豆包（Dola）",
}
BROWSER_KINDS = ("news", "ask", "forums")
MODEL_KINDS = ("questions", "extract", "sources", "group", "merge", "summary", "suggest")
CATEGORIES = ["事實", "責任", "聲譽", "回應", "預測", "其他"]
ENTITY_KINDS = ["人物", "機構", "公司", "地點", "法例", "事件", "其他"]


def today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()


# ---------------------------------------------------------------- the steps

async def step_news(task, page, ctx, session):
    """Google News (tbm=nws), past 60 days, HK: titles, outlets, dates → table news; then the questions step"""
    c = db.one("SELECT * FROM case_ WHERE id = %s", task["case_id"])
    lo = (today() - dt.timedelta(days=60)).strftime("%m/%d/%Y")
    hi = today().strftime("%m/%d/%Y")
    found = []
    for start in (0, 10, 20, 30):
        url = "https://www.google.com.hk/search?" + urllib.parse.urlencode(
            {"q": c["subject"], "tbm": "nws", "hl": "zh-HK", "gl": "hk", "tbs": f"cdr:1,cd_min:{lo},cd_max:{hi},sbd:1",
             "start": start})
        await engines._google_open(page, ctx, url, session)
        await asyncio.sleep(2)
        # result links are relative Google redirects (/url?q=...) as often as direct ones
        rows = await page.evaluate("""() => [...document.querySelectorAll('#rso a[href]')].map(a => {
            const t = a.querySelector('[role=heading]') || a.querySelector('div[aria-level]');
            if (!t) return null;
            const lines = a.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
            return {href: a.getAttribute('href'), title: t.innerText.trim(), outlet: lines[0] || '', when: lines[lines.length - 1] || ''} }).filter(Boolean)""")
        if not rows:
            break
        for r in rows:
            r["url"] = await engines._resolve_google(ctx, r["href"])
        found += [r for r in rows if r["url"].startswith("http") and not engines.GOOGLE_HOSTS.match(r["url"])]
    for r in found:
        db.run("INSERT INTO news (case_id, url, title, outlet, published) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
               c["id"], r["url"], r["title"][:500], r["outlet"][:200], _date(r["when"]))
    db.add_task("questions", c["id"])


def _date(s):
    """Google News' date text → date ('3 天前', '2026年10月23日', '10月23日', '5 小時前')"""
    s = s or ""
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"(\d{1,2})月(\d{1,2})日", s)
    if m:
        d = dt.date(today().year, int(m[1]), int(m[2]))
        return d if d <= today() else d.replace(year=d.year - 1)
    m = re.search(r"(\d+)\s*(分鐘|小時|天|週|星期|個月)前", s)
    if m:
        n, u = int(m[1]), m[2]
        days = {"分鐘": 0, "小時": 0, "天": n, "週": 7 * n, "星期": 7 * n, "個月": 30 * n}[u]
        return today() - dt.timedelta(days=days)
    return None


def step_questions(task):
    c = db.one("SELECT * FROM case_ WHERE id = %s", task["case_id"])
    news = db.q("SELECT title, outlet, published FROM news WHERE case_id = %s ORDER BY published DESC NULLS LAST LIMIT 60", c["id"])
    newslist = "\n".join(f"- {n['published'] or ''} {n['outlet']}: {n['title']}" for n in news) or "（找不到近期新聞）"
    kind = {"event": "事件", "person": "人物", "company": "公司"}[c["kind"]]
    r = llm.ask_json(
        "You are a Hong Kong PR analyst. " + HK + " Reply with JSON only.",
        f"""Subject ({kind}): {c['subject']}
Context from the user: {c['context'] or '(none)'}
Today: {today()}
News headlines about it from the last 60 days (Google News, Hong Kong):
{newslist}

1. "issues": the 4-8 distinct issues about the subject in this news, most recent first. Each: "title" (short),
   "summary" (one sentence of what happened, with the date), "latest" (YYYY-MM-DD of the newest headline on it).
   Only issues the headlines show: with no headlines, an empty list (never an issue about the lack of news).
2. "questions": about 18 questions a real person in Hong Kong would type into an AI chatbot about the subject, spread
   over these stakeholders: {', '.join(STAKEHOLDERS[c['kind']])}. Mix positive, negative and neutral (fact-checking)
   angles, and cover the issues. Plus one "base" question that simply asks what the subject is / what happened.
   With no headlines, ask about what is generally known of the subject (its business, record, reputation, risks).
   Each: "text" (the question as a Hong Kong person types it into a chatbot: written Chinese (書面語) in Traditional
   characters, Cantonese words only where people really type them; no stakeholder label in it, no figures or
   headline wording copied from the news: what a person would ask after hearing about it),
   "stakeholder", "angle" (positive | negative | neutral | base), "issue" (index into issues, or null).""",
        "questions", c["id"], reasoning="medium")
    ids = []
    for i in r.get("issues", []):
        row = db.one("INSERT INTO issue (case_id, title, summary, latest) VALUES (%s, %s, %s, %s) RETURNING id",
                     c["id"], i.get("title", "")[:300], i.get("summary", "")[:1000], _iso(i.get("latest")))
        ids.append(row["id"])
    for qn in r.get("questions", []):
        ix = qn.get("issue")
        db.run("INSERT INTO question (case_id, text, stakeholder, angle, issue_id, selected, round) VALUES (%s, %s, %s, %s, %s, %s, %s)",
               c["id"], qn["text"][:500], qn.get("stakeholder", "")[:50], qn.get("angle", "neutral"),
               ids[ix] if isinstance(ix, int) and 0 <= ix < len(ids) else None, qn.get("angle") == "base", c["round"])
    db.run("UPDATE case_ SET status = 'ready' WHERE id = %s", c["id"])


def _iso(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:  # noqa: BLE001
        return None


def start_asking(case_id):
    """the ticked questions × the case's apps → asks and their tasks (a question already asked is not asked again)"""
    c = db.one("SELECT * FROM case_ WHERE id = %s", case_id)
    n = 0
    for qn in db.q("SELECT id FROM question WHERE case_id = %s AND selected", case_id):
        for e in c["engines"]:
            r = db.one("INSERT INTO ask (case_id, question_id, engine) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
                       case_id, qn["id"], e)
            if r:
                db.add_task("ask", case_id, r["id"], e)
                n += 1
    if n:
        db.run("UPDATE case_ SET status = 'asking' WHERE id = %s", case_id)
    return n


async def step_ask(task, page, ctx, session):
    a = db.one("SELECT a.*, q.text AS question FROM ask a JOIN question q ON q.id = a.question_id WHERE a.id = %s", task["ref"])
    if a["status"] in ("done", "none"):
        # answered by an earlier try that failed after saving: never ask an app twice, only finish what was skipped
        if a["status"] == "done" and not db.one("SELECT 1 AS x FROM task WHERE kind = 'extract' AND ref = %s", a["id"]):
            db.add_task("extract", a["case_id"], a["id"])
        return
    try:
        r = await getattr(engines, a["engine"])(page, ctx, a["question"], session=session)
        db.run("UPDATE ask SET status = 'done', answer = %s, citations = %s, meta = %s, error = NULL, asked_at = now() WHERE id = %s",
               r["text"], db.J(r["citations"]), db.J(r.get("meta") or {}), a["id"])
        db.engine_status(a["engine"], "ok")
        db.add_task("extract", a["case_id"], a["id"])
    except engines.NoAnswer as e:
        db.run("UPDATE ask SET status = 'none', error = %s, asked_at = now() WHERE id = %s", str(e), a["id"])
    except engines.LoginNeeded as e:
        db.run("UPDATE ask SET status = 'login_needed', error = %s WHERE id = %s", str(e), a["id"])
        db.engine_status(a["engine"], "login_needed", str(e))
        raise


def step_extract(task):
    a = db.one("SELECT a.*, q.text AS question, c.subject FROM ask a JOIN question q ON q.id = a.question_id "
               "JOIN case_ c ON c.id = a.case_id WHERE a.id = %s", task["ref"])
    cites = "\n".join(f"[{i + 1}] {c['url']}  {c.get('title', '')[:120]}" for i, c in enumerate(a["citations"]))
    r = llm.ask_json(
        "You analyse what AI chatbots tell people about a subject, for a Hong Kong PR team. " + HK + " Reply with JSON only.",
        f"""Subject: {a['subject']}
Question asked: {a['question']}
The app's answer:
<<<
{a['answer'][:12000]}
>>>
Its cited sources:
{cites or '(none)'}

Return:
"mentions": true if the answer is about / names the subject;
"tone": the answer's overall tone towards the subject, -1 negative, 0 neutral, 1 positive;
"latest_date": the newest calendar date written in the answer (YYYY-MM-DD) or null;
"claims": every distinct factual or evaluative statement the answer makes that bears on the subject (not advice to
the reader, not filler), each: "text" (one self-contained sentence, so it can be compared with other apps' answers),
"tone" (-1/0/1 towards the subject), "cites" (the [n] numbers of the sources that back it in the answer, if the
answer shows which; else all of them).""",
        "extract", a["case_id"])
    db.run("UPDATE ask SET tone = %s, mentions = %s, latest_date = %s WHERE id = %s",
           _int(r.get("tone")), bool(r.get("mentions")), _iso(r.get("latest_date")), a["id"])
    db.run("DELETE FROM claim WHERE ask_id = %s", a["id"])
    for cl in r.get("claims", []):
        urls = [a["citations"][n - 1]["url"] for n in cl.get("cites") or [] if isinstance(n, int) and 0 < n <= len(a["citations"])]
        db.run("INSERT INTO claim (case_id, ask_id, text, tone, cited) VALUES (%s, %s, %s, %s, %s)",
               a["case_id"], a["id"], cl["text"][:1000], _int(cl.get("tone")), db.J(urls))
    for cit in a["citations"]:
        u = cit["url"]
        row = db.one("""INSERT INTO source (case_id, url, domain, title, engines, cited_by) VALUES (%s, %s, %s, %s, ARRAY[%s], 1)
                        ON CONFLICT (case_id, url) DO UPDATE SET cited_by = source.cited_by + 1,
                          engines = (SELECT array_agg(DISTINCT e) FROM unnest(source.engines || EXCLUDED.engines) e)
                        RETURNING id, (xmax = 0) AS fresh""",
                     a["case_id"], u, _domain(u), cit.get("title", "")[:300], a["engine"])
        if row["fresh"]:
            db.add_task("sources", a["case_id"], row["id"])


def _int(x):
    try:
        return max(-1, min(1, int(x)))
    except Exception:  # noqa: BLE001
        return 0


def _domain(u):
    h = urllib.parse.urlsplit(u).hostname or ""
    return h[4:] if h.startswith("www.") else h


OFFICIAL = re.compile(r"(\.gov\.hk|\.gov|\.gov\.cn|\.edu\.hk|legco\.gov\.hk|info\.gov\.hk|\.org\.hk)$")
FORUM = re.compile(r"(lihkg\.com|discuss\.com\.hk|hkgolden\.com|baby-kingdom\.com|reddit\.com|zhihu\.com|threads\.(net|com)|"
                   r"facebook\.com|instagram\.com|youtube\.com|x\.com|twitter\.com|weibo\.com|xiaohongshu\.com|tiktok\.com|douyin\.com)$")
WIKI = re.compile(r"(wikipedia\.org|baike\.baidu\.com|baike\.com|wikiwand\.com)$")


def kind_of(domain):
    if OFFICIAL.search(domain):
        return "official"
    if WIKI.search(domain):
        return "wiki"
    if FORUM.search(domain):
        return "forum"
    return None  # the model decides between media, business and other


def fetch_text(url):
    """the page's readable text (curl_cffi as Chrome, direct; then through YYC's HK proxy) → (title, text) or raises"""
    from curl_cffi import requests as cr
    from lxml import html as lh
    from . import proxy
    last = None
    for via in (None, "resi"):
        kw = {}
        if via:
            p = proxy.parts("hk")
            if not p:
                break
            host, port, user, pw = p
            kw["proxies"] = {"https": f"http://{urllib.parse.quote(user)}:{urllib.parse.quote(pw)}@{host}:{port}",
                             "http": f"http://{urllib.parse.quote(user)}:{urllib.parse.quote(pw)}@{host}:{port}"}
        try:
            r = cr.get(url, impersonate="chrome", timeout=30, allow_redirects=True, **kw)
            if r.status_code >= 400:
                last = f"HTTP {r.status_code}"
                continue
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype:
                last = f"not a page ({ctype[:40]})"
                break
            doc = lh.fromstring(r.content)
            for bad in doc.xpath("//script|//style|//noscript|//nav|//footer|//header|//form|//svg"):
                bad.drop_tree()
            title = (doc.findtext(".//title") or "").strip()
            text = re.sub(r"\n\s*\n+", "\n", doc.text_content())
            text = re.sub(r"[ \t　]+", " ", text).strip()
            if len(text) < 200:
                last = "too little text"
                continue
            return title, text
        except Exception as e:  # noqa: BLE001
            last = str(e)[:200]
    raise RuntimeError(last or "unreadable")


def step_sources(task):
    s = db.one("SELECT s.*, c.subject FROM source s JOIN case_ c ON c.id = s.case_id WHERE s.id = %s", task["ref"])
    claims = db.q("SELECT id, text FROM claim WHERE case_id = %s AND cited ? %s", s["case_id"], s["url"])
    try:
        title, text = fetch_text(s["url"])
    except Exception as e:  # noqa: BLE001
        db.run("UPDATE source SET status = 'unreadable', summary = %s, kind = COALESCE(kind, %s) WHERE id = %s",
               f"未能讀取：{str(e)[:150]}", kind_of(s["domain"]) or "other", s["id"])
        return
    listed = "\n".join(f"{c['id']}: {c['text']}" for c in claims[:25])
    r = llm.ask_json(
        "You check what a web page says, for a Hong Kong PR team. " + HK + " Reply with JSON only.",
        f"""Subject: {s['subject']}
Page: {s['url']}
Title: {title}
Text (may be cut):
<<<
{text[:15000]}
>>>
Statements AI apps made while citing this page (id: text):
{listed or '(none)'}

Return "summary" (two sentences: what the page says about the subject), "tone" (-1/0/1 towards the subject),
"kind" (official | media | wiki | forum | social | business | other: a government or regulator page is official, a
news outlet media, a company's own site or a registry business), "date" (the page's publication date YYYY-MM-DD or
null), and "checks": for each statement id, "supported" (the page says it), "partial" (says part of it, or something
close) or "absent" (the page does not say it).""",
        "sources", s["case_id"])
    db.run("UPDATE source SET status = 'read', title = %s, summary = %s, tone = %s, kind = %s, text_chars = %s WHERE id = %s",
           title[:300] or s["title"], (r.get("summary") or "")[:1500], _int(r.get("tone")),
           kind_of(s["domain"]) or r.get("kind") or "other", len(text), s["id"])
    checks = r.get("checks") or {}
    if isinstance(checks, list):
        checks = {str(x.get("id")): x.get("verdict") or x.get("result") for x in checks if isinstance(x, dict)}
    rank = {"supported": 3, "partial": 2, "absent": 1}
    for cid, verdict in checks.items():
        if verdict in rank:
            # a statement keeps the best verdict any of its pages gave it
            db.run("""UPDATE claim SET support = %s WHERE id = %s AND case_id = %s AND
                        (support IS NULL OR array_position(ARRAY['absent','partial','supported'], support) < %s)""",
                   verdict, int(cid), s["case_id"], rank[verdict])


def step_group(task):
    """one question's statements across the apps → claim groups"""
    qn = db.one("SELECT q.*, c.subject, c.kind FROM question q JOIN case_ c ON c.id = q.case_id WHERE q.id = %s", task["ref"])
    claims = db.q("SELECT cl.id, cl.text, cl.tone, cl.support, a.engine FROM claim cl JOIN ask a ON a.id = cl.ask_id "
                  "WHERE a.question_id = %s ORDER BY cl.id", qn["id"])
    db.run("UPDATE claim SET group_id = NULL WHERE ask_id IN (SELECT id FROM ask WHERE question_id = %s)", qn["id"])
    db.run("DELETE FROM claim_group WHERE id IN (SELECT DISTINCT group_id FROM claim WHERE group_id IS NOT NULL AND ask_id IN "
           "(SELECT id FROM ask WHERE question_id = %s))", qn["id"])
    if not claims:
        return
    issues = db.q("SELECT id, title FROM issue WHERE case_id = %s", qn["case_id"])
    # the apps by the names the pages show (the model repeats what it is given in its notes)
    listed = "\n".join(f"{c['id']} [{ENGINES.get(c['engine'], c['engine'])}]: {c['text']}" for c in claims)
    r = llm.ask_json(
        "You compare what different AI apps say about a subject, for a Hong Kong PR team. " + HK + " Reply with JSON only.",
        f"""Subject: {qn['subject']}
Question the apps were asked: {qn['text']}
Statements from their answers (id [app]: text):
{listed}
Recent issues (id: title): {'; '.join(f"{i['id']}: {i['title']}" for i in issues) or '(none)'}

Group statements that say the same thing (across apps or within one). Return "groups", each:
"ids" (the statement ids in it), "text" (the statement in one clear sentence), "tone" (-1/0/1 towards the subject),
"contradiction" (null, or one sentence on how some apps say something incompatible with it: name the apps),
"impact" (0-100: how much this statement, if people believe it, affects the subject's image, positively or
negatively), "actionable" (0-100: how much a PR team can do about it: correct an error, add a source, give context),
"issue" (the issue id it belongs to, or null), "category" (one of: {'/'.join(CATEGORIES)}: a fact, who is responsible,
reputation, a response or measure, a prediction, other), "entities" (the people, organisations, companies, places, laws
and events it names, each {{"name": the usual full name in Traditional Chinese, "kind": one of {'/'.join(ENTITY_KINDS)}}}).
Every id in exactly one group.""",
        "group", qn["case_id"])
    by_id = {c["id"]: c for c in claims}
    for g in r.get("groups", []):
        ids = [i for i in g.get("ids") or [] if i in by_id]
        if not ids:
            continue
        eng = sorted({by_id[i]["engine"] for i in ids})
        sup = [by_id[i]["support"] for i in ids]
        support = ("supported" if "supported" in sup else "partial" if "partial" in sup
                   else "unsupported" if "absent" in sup else "unread")
        tone, impact, act = _int(g.get("tone")), _pct(g.get("impact")), _pct(g.get("actionable"))
        row = db.one("""INSERT INTO claim_group (case_id, text, tone, engines, contradiction, support, impact, actionable, risk, issue_id, category)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                     qn["case_id"], g.get("text", "")[:1000], tone, eng, (g.get("contradiction") or None),
                     support, impact, act, risk(tone, impact, act, support, g.get("contradiction")),
                     g.get("issue") if g.get("issue") in {i["id"] for i in issues} else None,
                     g.get("category") if g.get("category") in CATEGORIES else "其他")
        db.run("UPDATE claim SET group_id = %s WHERE id = ANY(%s)", row["id"], ids)
        for e in g.get("entities") or []:
            name = (e.get("name") or "").strip()[:120] if isinstance(e, dict) else ""
            if not name:
                continue
            ent = db.one("""INSERT INTO entity (case_id, name, kind) VALUES (%s, %s, %s)
                            ON CONFLICT (case_id, name) DO UPDATE SET kind = entity.kind RETURNING id""",
                         qn["case_id"], name, e.get("kind") if e.get("kind") in ENTITY_KINDS else "其他")
            db.run("INSERT INTO group_entity (group_id, entity_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", row["id"], ent["id"])


def _pct(x):
    try:
        return max(0, min(100, int(x)))
    except Exception:  # noqa: BLE001
        return 0


def risk(tone, impact, actionable, support, contradiction):
    """the ranking of 需要處理: a negative statement with a big effect on the image that PR can act on comes first;
    a statement the cited pages don't back, or that apps contradict, is more worth acting on"""
    base = 0.7 * impact + 0.3 * actionable
    base *= {-1: 1.0, 0: 0.55, 1: 0.25}[tone]
    if support in ("unsupported", "unread"):
        base += 8
    if contradiction:
        base += 6
    return int(min(100, round(base)))


def level(r):
    return "高" if r >= 65 else "中" if r >= 40 else "低"


def step_merge(task):
    """case-wide: the same statement grouped under different questions becomes one group"""
    case_id = task["case_id"]
    groups = db.q("SELECT g.id, g.text, g.engines FROM claim_group g WHERE g.case_id = %s ORDER BY g.id", case_id)
    if len(groups) < 2:
        return
    listed = "\n".join(f"{g['id']}: {g['text']}" for g in groups)
    r = llm.ask_json("You de-duplicate statements. Reply with JSON only.",
                     f"""These statements were grouped question by question. List the sets that state the same fact or
judgement (wording aside). Return "same": a list of id lists, each with 2 or more ids; leave out statements with no twin.

{listed}""", "merge", case_id)
    known = {g["id"]: g for g in groups}
    for ids in r.get("same") or []:
        ids = [i for i in ids if i in known]
        if len(ids) < 2:
            continue
        keep, rest = ids[0], ids[1:]
        db.run("UPDATE claim SET group_id = %s WHERE group_id = ANY(%s)", keep, rest)
        db.run("""INSERT INTO group_entity (group_id, entity_id) SELECT %s, entity_id FROM group_entity WHERE group_id = ANY(%s)
                  ON CONFLICT DO NOTHING""", keep, rest)
        db.run("""UPDATE claim_group g SET engines = COALESCE((SELECT array_agg(DISTINCT a.engine) FROM claim c JOIN ask a ON a.id = c.ask_id
                    WHERE c.group_id = g.id), g.engines),
                    impact = (SELECT max(impact) FROM claim_group WHERE id = ANY(%s)),
                    risk = (SELECT max(risk) FROM claim_group WHERE id = ANY(%s)),
                    contradiction = COALESCE(g.contradiction, (SELECT max(contradiction) FROM claim_group WHERE id = ANY(%s)))
                  WHERE g.id = %s""", ids, ids, ids, keep)
        db.run("DELETE FROM claim_group WHERE id = ANY(%s)", rest)


def step_summary(task):
    case_id = task["case_id"]
    s = {}
    a = db.q("SELECT engine, status, tone, mentions, latest_date, jsonb_array_length(citations) AS ncit FROM ask WHERE case_id = %s", case_id)
    done = [x for x in a if x["status"] == "done"]
    s["answers"] = len(done)
    s["asked"] = len(a)
    s["questions"] = db.one("SELECT count(DISTINCT question_id) AS n FROM ask WHERE case_id = %s", case_id)["n"]
    s["engines"] = sorted({x["engine"] for x in a})
    s["mention_rate"] = round(100 * sum(1 for x in done if x["mentions"]) / len(done)) if done else 0
    s["cited_rate"] = round(100 * sum(1 for x in done if x["ncit"]) / len(done)) if done else 0
    src = db.q("SELECT kind, status, domain, engines FROM source WHERE case_id = %s", case_id)
    s["sources"] = len(src)
    s["sources_read"] = sum(1 for x in src if x["status"] == "read")
    s["official_share"] = round(100 * sum(1 for x in src if x["kind"] == "official") / len(src)) if src else 0
    # how much the apps cite the same sites: the mean over app pairs of |shared domains| / |either's domains|
    doms = {}
    for x in src:
        for e in x["engines"]:
            doms.setdefault(e, set()).add(x["domain"])
    pairs = [(p, q) for i, p in enumerate(sorted(doms)) for q in sorted(doms)[i + 1:]]
    s["overlap"] = round(100 * sum(len(doms[p] & doms[q]) / max(1, len(doms[p] | doms[q])) for p, q in pairs) / len(pairs)) if pairs else 0
    g = db.q("SELECT tone, risk, contradiction, cardinality(engines) AS n FROM claim_group WHERE case_id = %s", case_id)
    s["claims"] = len(g)
    s["agree"] = sum(1 for x in g if x["n"] >= 2 and not x["contradiction"])
    s["contradict"] = sum(1 for x in g if x["contradiction"])
    s["risk_levels"] = {lv: sum(1 for x in g if level(x["risk"] or 0) == lv) for lv in ("高", "中", "低")}
    s["net_tone_claims"] = round(100 * (sum(1 for x in g if x["tone"] > 0) - sum(1 for x in g if x["tone"] < 0)) / len(g)) if g else 0
    s["net_tone_answers"] = round(100 * (sum(1 for x in done if (x["tone"] or 0) > 0) - sum(1 for x in done if (x["tone"] or 0) < 0)) / len(done)) if done else 0
    rs = [x for x in src if x["status"] == "read"]
    tones = db.q("SELECT tone FROM source WHERE case_id = %s AND status = 'read'", case_id)
    s["net_tone_sources"] = round(100 * (sum(1 for x in tones if (x["tone"] or 0) > 0) - sum(1 for x in tones if (x["tone"] or 0) < 0)) / len(tones)) if tones else 0
    db.run("UPDATE case_ SET summary = %s, status = 'done', analysed_at = now() WHERE id = %s", db.J(s), case_id)


SPANS = {"week": "qdr:w", "month": "qdr:m", "year": "qdr:y", "any": ""}
FORUM_SITES = {"lihkg.com": "LIHKG", "threads.com": "Threads", "threads.net": "Threads"}


async def step_forums(task, page, ctx, session):
    """搜尋 LIHKG／Threads: Google, site:lihkg.com OR site:threads.com, the subject, in the span asked (task.arg)"""
    c = db.one("SELECT * FROM case_ WHERE id = %s", task["case_id"])
    span = task.get("arg") if task.get("arg") in SPANS else "month"
    # one query per platform: Google ignores "site:a OR site:b" in a query with other words (2026-10-27).
    # udm=14 (the 網頁 tab): the blended 全部 tab with no time filter drops site: and shows Facebook/YouTube/news
    # instead, all of it then thrown away by the host check below (2026-10-27)
    rows = []
    for site in ("lihkg.com", "threads.com"):
        params = {"q": f'site:{site} {c["subject"]}', "hl": "zh-HK", "gl": "hk", "num": "20", "udm": "14"}
        # Google keeps site: only with a time filter (with none it answered from Facebook, YouTube and news, also on
        # the 網頁 tab, from the cluster's IPs, 2026-10-27): "any" is a date range from 2005 to today
        params["tbs"] = SPANS[span] or f"cdr:1,cd_min:1/1/2005,cd_max:{today().strftime('%m/%d/%Y')}"
        await engines._google_open(page, ctx, "https://www.google.com.hk/search?" + urllib.parse.urlencode(params), session)
        await asyncio.sleep(2)
        found = await page.evaluate("""() => [...document.querySelectorAll('#rso a[href]')].map(a => {
            const h = a.querySelector('h3'); if (!h) return null;
            const box = a.closest('[data-hveid]') || a.parentElement;
            const sn = box ? (box.querySelector('[data-sncf], .VwiC3b, [style*="-webkit-line-clamp"]') || {}).innerText || '' : '';
            return {href: a.getAttribute('href'), title: h.innerText.trim(), snippet: sn.trim()} }).filter(Boolean)""")
        if not found:  # what Google showed instead (a wall, a consent page, a changed layout), for the operator
            seen = (await page.evaluate("() => document.body ? document.body.innerText.slice(0, 300) : ''")).replace("\n", " ")
            db.engine_status("forums", "empty", f"{site}: {page.url[:100]} | {seen}")
        rows += found
    db.run("DELETE FROM forum_hit WHERE case_id = %s AND span = %s", c["id"], span)
    off = []
    for r in rows:
        url = await engines._resolve_google(ctx, r["href"])
        site = next((v for k, v in FORUM_SITES.items() if (urllib.parse.urlsplit(url).hostname or "").endswith(k)), None)
        if not site:
            off.append(urllib.parse.urlsplit(url).hostname or url[:60])
            continue
        db.run("""INSERT INTO forum_hit (case_id, span, site, url, title, snippet, posted) VALUES (%s, %s, %s, %s, %s, %s, %s)
                  ON CONFLICT DO NOTHING""", c["id"], span, site, url, r["title"][:300], r["snippet"][:600], _date(r["snippet"][:40]))
    if rows and len(off) == len(rows):  # Google answered, but not from the forums (site: ignored), for the operator
        db.engine_status("forums", "off-site", f"{span}: {len(off)} results, none on LIHKG/Threads: {', '.join(sorted(set(off)))[:200]}")


def step_suggest(task):
    """AI 建議下一輪問題: what the case hasn't asked yet, from its issues, its risky and contested statements"""
    c = db.one("SELECT * FROM case_ WHERE id = %s", task["case_id"])
    asked = db.q("SELECT text FROM question WHERE case_id = %s AND EXISTS (SELECT 1 FROM ask a WHERE a.question_id = question.id)", c["id"])
    groups = db.q("SELECT text, contradiction, support FROM claim_group WHERE case_id = %s ORDER BY risk DESC NULLS LAST LIMIT 25", c["id"])
    issues = db.q("SELECT title, summary FROM issue WHERE case_id = %s", c["id"])
    r = llm.ask_json(
        "You are a Hong Kong PR analyst planning the next round of questions to put to AI chatbots. " + HK + " Reply with JSON only.",
        f"""Subject: {c['subject']}
Issues: {'; '.join(i['title'] for i in issues) or '(none)'}
Already asked: {chr(10).join('- ' + q['text'] for q in asked)}
What the apps said that matters most (risky, contested or unbacked):
{chr(10).join('- ' + g['text'] + (' [contested: ' + g['contradiction'] + ']' if g['contradiction'] else '') + (' [the cited pages do not say it]' if g['support'] == 'unsupported' else '') for g in groups)}

Suggest 5 next questions that would show the most about what people are told: a contested point to settle, a risky
statement to test, an issue not yet asked about. Return "questions": each {{"text": the question as a Hong Kong person
types it (written Chinese), "why": one sentence on what it would show}}.""", "suggest", c["id"])
    db.run("DELETE FROM suggestion WHERE case_id = %s", c["id"])
    for qn in (r.get("questions") or [])[:8]:
        if isinstance(qn, dict) and qn.get("text"):
            db.run("INSERT INTO suggestion (case_id, text, why) VALUES (%s, %s, %s)", c["id"], qn["text"][:500], (qn.get("why") or "")[:500])


def reanalyse(case_id):
    """重新分析: the statements grouped again across the apps (and the summary redone); answers and sources are kept"""
    db.run("UPDATE case_ SET status = 'analysing', summary = COALESCE(summary, '{}'::jsonb) - 'stage' WHERE id = %s", case_id)
    advance(case_id)


def ask_again(case_id, user):
    """用這題重新提問: a new case with the same subject, kind, context and apps"""
    c = db.one("SELECT * FROM case_ WHERE id = %s", case_id)
    return new_case(c["subject"], c["kind"], c["context"], c["engines"], user)


# ---------------------------------------------------------------- moving a case on

def advance(case_id):
    """queue the case's next step when the current one has nothing left (one worker at a time: an advisory lock)"""
    if not case_id:
        return
    def locked():  # the lock's connection, waiting while the database is away (db.retrying)
        cm = db.conn()
        c = cm.__enter__()
        try:
            ok = c.execute("SELECT pg_try_advisory_lock(7100, %s) AS ok", (case_id,)).fetchone()["ok"]
        except BaseException as e:
            cm.__exit__(type(e), e, e.__traceback__)
            raise
        return cm, c, ok
    cm, c, ok = db.retrying(locked)
    try:
        if ok:
            _advance(case_id)
    finally:
        try:
            if ok:
                c.execute("SELECT pg_advisory_unlock(7100, %s)", (case_id,))
        except psycopg.OperationalError:
            pass  # the connection went (a switchover), and its lock with it
        cm.__exit__(None, None, None)


def _advance(case_id):
    c = db.one("SELECT status FROM case_ WHERE id = %s", case_id)
    if not c or c["status"] not in ("asking", "analysing"):
        return
    if db.pending(case_id, ["ask", "extract"]):
        return
    if c["status"] == "asking":
        db.run("UPDATE case_ SET status = 'analysing' WHERE id = %s", case_id)
    if db.pending(case_id, ["sources"]):
        return
    if db.pending(case_id, ["group", "merge", "summary"]):
        return
    stage = db.one("SELECT summary->>'stage' AS s FROM case_ WHERE id = %s", case_id)["s"]
    # group → merge → summary, each once per analysis (stage in summary marks what was queued)
    if stage is None:
        for qn in db.q("SELECT DISTINCT question_id FROM ask WHERE case_id = %s AND status = 'done'", case_id):
            db.add_task("group", case_id, qn["question_id"])
        db.run("UPDATE case_ SET summary = COALESCE(summary, '{}'::jsonb) || '{\"stage\": \"group\"}' WHERE id = %s", case_id)
    elif stage == "group":
        db.add_task("merge", case_id)
        db.run("UPDATE case_ SET summary = summary || '{\"stage\": \"merge\"}' WHERE id = %s", case_id)
    elif stage == "merge":
        db.add_task("summary", case_id)
        db.run("UPDATE case_ SET summary = summary || '{\"stage\": \"summary\"}' WHERE id = %s", case_id)


def follow_up(case_id, texts):
    """追問: new questions on the same case, asked with the same apps, analysed again with the rest"""
    db.run("UPDATE case_ SET round = round + 1, summary = summary - 'stage' WHERE id = %s", case_id)
    rnd = db.one("SELECT round FROM case_ WHERE id = %s", case_id)["round"]
    for t in texts:
        t = t.strip()
        if t:
            db.run("INSERT INTO question (case_id, text, stakeholder, angle, selected, round) VALUES (%s, %s, '追問', 'neutral', true, %s)",
                   case_id, t[:500], rnd)
    return start_asking(case_id)


def rerun_questions(case_id):
    """用這題重新提問 / 展開問題樹 again: a fresh set of questions (the old unasked ones go)"""
    db.run("DELETE FROM question q WHERE case_id = %s AND NOT EXISTS (SELECT 1 FROM ask a WHERE a.question_id = q.id)", case_id)
    db.run("UPDATE case_ SET status = 'questions' WHERE id = %s", case_id)
    db.add_task("questions", case_id)


def new_case(subject, kind, context, engines_, user):
    r = db.one("INSERT INTO case_ (subject, kind, context, engines, status, created_by) VALUES (%s, %s, %s, %s, 'news', %s) RETURNING id",
               subject.strip()[:200], kind, (context or "").strip()[:2000], engines_, user)
    db.add_task("news", r["id"])
    return r["id"]


def dumps(x):
    return json.dumps(x, ensure_ascii=False, default=str)
