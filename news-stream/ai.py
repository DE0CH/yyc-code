"""Model calls on OpenRouter (key yyc-stream) for the stream scorer (streams.py), the daily reports' summaries and themes,
and 數據分析's persona note. Every call's cost is OpenRouter's own figure (usage.cost in
the response), logged per HKT day and purpose (table ai_spend) and per call (table ai_runs). On-demand AI is only for accounts Deyao
approved in Cloudflare Access (web.py), with no daily limit (Deyao, 2026-10-13); the key's monthly cap on OpenRouter
is the only ceiling.

Hard stop, never a plan (Deyao, 2026-10-14: "budget is a safety stop"): nothing here projects spend or decides what to
do by it. When OpenRouter itself refuses for the key's limit or the account's credit (HTTP 402, or a 403 whose message
names the limit or credit), the call raises LimitReached; kv `openrouter_limit` records it (the monitor shows it),
Deyao gets one Discord message per episode (notify.deyao), and the first call that works again clears it.

Model and cost on the pages (Deyao, 2026-10-13): before an AI button is pressed the page shows the model and a
projected cost (estimate(): the prompt's tokens at OpenRouter's listed prices, plus the output tokens the same purpose
used in its recent runs); after it ran, the model and the actual cost OpenRouter charged (every call
an action made, collected through the `sink` list each call takes).
"""
import json, os, re, statistics, time, urllib.error, urllib.request
from datetime import datetime, timedelta

import corpus, costlog, datefmt, notify

# Qwen3.7 Flash (Deyao, 2026-10-28: cost, and OpenRouter refuses Anthropic and Google models to YYC's account with 403
# "violation of provider Terms Of Service"); measured as stage 1 on 591 posts of 27 Oct (tools/stage1_trial.py):
# 93.2% of the filed pairs screened in, USD 0.00078 a call
MODEL = os.environ.get("MODEL", "qwen/qwen3.7-flash")
# the daily report's summary and themes (reports.daily): DeepSeek V4.1 Flash, thinking high, one level below the
# maximum (Deyao, 2026-10-28: "the daily report use flash with high thinking"; OpenRouter refuses Anthropic and Google
# models to YYC's account), on the cheapest fp8-or-better endpoint outside the mainland providers (their content filter
# sits in front of the model); thinking counts against max_tokens
REPORT_MODEL = os.environ.get("REPORT_MODEL", "deepseek/deepseek-v4.1-flash")
OPEN_WEIGHT_ROUTE = {"quantizations": ["fp8", "fp16", "bf16", "fp32", "unknown"], "sort": "price",
                     "ignore": ["streamlake", "alibaba", "siliconflow", "baidu"]}
REPORT_OPTIONS = {"reasoning": {"effort": "high"}, "provider": OPEN_WEIGHT_ROUTE, "timeout": 600}
REPORT_TOKENS = 32000
# 數據分析's persona button: DeepSeek V4.1 Flash 1017, thinking high (Deyao, 2026-10-28), the same route
SMART_MODEL = os.environ.get("SMART_MODEL", "deepseek/deepseek-v4.1-flash-1017")
SMART_OPTIONS = {"reasoning": {"effort": "high"}, "provider": OPEN_WEIGHT_ROUTE}
THINKING_ROOM = 30000  # max_tokens beyond the answer's own length for the thinking (it counts against max_tokens)


def today():
    return datetime.now(corpus.HKT).strftime("%Y-%m-%d")


def spend(day=None):
    """{purpose: USD, "_total": USD} charged on an HKT day"""
    d = {r["purpose"]: round(r["usd"], 6) for r in corpus.db().execute(
        "SELECT purpose, usd FROM ai_spend WHERE day=%s ORDER BY purpose", (day or today(),))}
    if d:
        d["_total"] = round(sum(d.values()), 6)
    return d


def _log(purpose, cost):
    """add a call's cost to the day's spend (one atomic statement: every process logs here)"""
    corpus.db().execute("""INSERT INTO ai_spend (day, purpose, usd) VALUES (%s, %s, %s)
                           ON CONFLICT (day, purpose) DO UPDATE SET usd = ai_spend.usd + excluded.usd""",
                        (today(), purpose, cost))


def models():
    """OpenRouter's model list (public endpoint): {id: {name, prompt, completion}} with USD per token; a day's cache"""
    d = corpus.kv_get("openrouter_models")
    if not d or time.time() - d.get("at", 0) > 86400:
        req = urllib.request.Request("https://openrouter.ai/api/v1/models?output_modalities=all",  # all: speech-to-text too
                                     headers={"User-Agent": "yyc-news-stream/1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)["data"]
        d = {"at": time.time(), "models": {m["id"]: {"name": m.get("name") or m["id"],
                                                     "prompt": float(m.get("pricing", {}).get("prompt") or 0),
                                                     "completion": float(m.get("pricing", {}).get("completion") or 0)}
                                           for m in data}}
        corpus.kv_set("openrouter_models", d)
    return d["models"]


def model_name(model):
    try:
        return re.sub(r"^[^:]+:\s*", "", models().get(model, {}).get("name") or model)  # "Anthropic: Claude …" → "Claude …"
    except Exception:
        return model


def _runs(purpose, n=30):
    """the last n calls of a purpose, oldest first: {model, usd, input_tokens, output_tokens, chars, calls}"""
    rows = corpus.db().execute("""SELECT model, usd, input_tokens, output_tokens, chars, calls FROM ai_runs
                                  WHERE purpose=%s ORDER BY id DESC LIMIT %s""", (purpose, n)).fetchall()
    return [{k: v for k, v in dict(r).items() if v is not None} for r in reversed(rows)]


def record_run(purpose, rec):
    corpus.db().execute("""INSERT INTO ai_runs (purpose, model, usd, input_tokens, output_tokens, chars, calls, job,
                                                generation) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (purpose, rec.get("model"), rec.get("usd"), rec.get("input_tokens"), rec.get("output_tokens"),
                         rec.get("chars"), rec.get("calls"), costlog.job(), rec.get("generation")))


def _record(purpose, model, messages, u, sink=None, generation=None):
    """after every call: the day's spend (_log), the call in ai_runs (characters sent, tokens, cost: the basis of the
    projections; the job and OpenRouter's generation id: the cost profile), and the call appended to `sink` (the list
    an action collects its calls in)"""
    cost = float(u.get("cost") or 0)
    _log(purpose, cost)
    rec = {"model": model, "usd": cost, "input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens"),
           "chars": sum(len(str(x.get("content") or "")) for x in messages)}
    record_run(purpose, dict(rec, generation=generation))
    if sink is not None:
        sink.append(rec)


def estimate(prompt, purpose, model, max_tokens):
    """projected cost of one call: input tokens (this purpose's measured tokens per character, else a measured
    average) × the listed input price + the median output tokens of its recent runs (else max_tokens) × the output price"""
    m = models().get(model, {"prompt": 0, "completion": 0})
    runs = [r for r in _runs(purpose) if r.get("chars") and r.get("input_tokens")]
    if runs:
        tin = len(prompt) * statistics.median(r["input_tokens"] / r["chars"] for r in runs)
    else:  # 0.92 tokens per character: a 16,405-character summary prompt (Chinese posts + URLs) took 15,096 (2026-10-13)
        tin = len(prompt) * 0.92
    outs = [r["output_tokens"] for r in _runs(purpose) if r.get("output_tokens")]
    tout = statistics.median(outs) if outs else max_tokens  # no history yet: the most it can write
    return {"model": model, "model_name": model_name(model), "usd": round(tin * m["prompt"] + tout * m["completion"], 4),
            "input_tokens": int(tin), "output_tokens": int(tout), "runs": len(outs)}


def usage(calls):
    """what an action cost: the models used and OpenRouter's charged total"""
    ms = list(dict.fromkeys(c["model"] for c in calls))
    return {"usd": round(sum(c["usd"] for c in calls), 6), "calls": len(calls), "models": ms,
            "model_name": " + ".join(model_name(m) for m in ms)}


class LimitReached(RuntimeError):
    """OpenRouter refused the call for the key's limit or the account's credit: a hard stop until Deyao recharges"""


LIMIT_RE = re.compile(r"limit|credit", re.I)


def _is_limit(code, message):
    return code == 402 or (code == 403 and bool(LIMIT_RE.search(message or "")))


def _error_message(err):
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300], err.get("code")
    return str(err)[:300], None


NOTIFY_EVERY = timedelta(hours=6)


def limit_hit(message):
    """record the hard stop and tell Deyao once per episode → the LimitReached to raise. The stage this thread works
    for is blocked: once the limit is lifted it starts from then, its backlog waiting for a manual run (corpus.stage)"""
    now = datetime.now(corpus.HKT).isoformat(timespec="seconds")
    corpus.stage_blocked("OpenRouter refused: " + str(message)[:120])
    lim = corpus.kv_get("openrouter_limit") or {}
    if not lim.get("since"):
        lim = {"since": now, "notified": False}
    lim.update(message=message, at=now)
    told = corpus.kv_get("openrouter_limit_told")  # when Deyao was last told: a stop that flaps (audio refused while
    # small text calls still pass, live 2026-10-14) is one message per NOTIFY_EVERY, not one per return
    recent = told and datetime.fromisoformat(told) > datetime.now(corpus.HKT) - NOTIFY_EVERY
    if not lim.get("notified") and recent:
        lim["notified"] = True
    if not lim.get("notified"):
        print("OpenRouter requests failing (credit / key limit):", message, flush=True)
        corpus.kv_set("openrouter_limit_told", now)
        lim["notified"] = notify.deyao(
            f"Waiting on you: OpenRouter requests on YYC's key (yyc-stream) are failing with \"{message[:300]}\" "
            f"since {datefmt.when(lim['since'])} HKT (the account's credit or the key's limit), so live "
            "scoring, the history scoring and the AI features are paused. Recharge or raise the key's limit on "
            "openrouter.ai; the app tries again every 10 minutes and carries on by itself.")
    corpus.kv_set("openrouter_limit", lim)
    return LimitReached(message)


def limit_clear():
    if corpus.kv_get("openrouter_limit"):
        print("OpenRouter answers again: limit cleared", flush=True)
        corpus.kv_set("openrouter_limit", None)


def _open(req, timeout):
    """urlopen that turns OpenRouter's limit/credit refusal into LimitReached"""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            msg, _ = _error_message(json.loads(e.read().decode("utf-8", "replace")).get("error"))
        except Exception:
            msg = ""
        if _is_limit(e.code, msg):
            raise limit_hit(msg or f"HTTP {e.code}") from None
        raise


# no thinking anywhere unless a caller asks for it (Deyao, 2026-10-28: "turn off thinking entirely"). A model whose
# endpoint refuses to turn it off ("Reasoning is mandatory for this endpoint", 400) goes in THINKING_MANDATORY and gets
# its lowest effort; none of the models in use is one
NO_THINKING = {"enabled": False}
LEAST_THINKING = {"effort": "minimal"}
THINKING_MANDATORY = ()


def chat(messages, purpose, model=None, json_mode=False, tools=None, max_tokens=4000, temperature=0.2, reasoning=None,
         provider=None, timeout=180):
    """one chat completion → the response message (dict). reasoning: OpenRouter's reasoning setting; None = no
    thinking (NO_THINKING, or LEAST_THINKING where it can't be turned off)"""
    body = {"model": model or MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
            "usage": {"include": True}}
    if reasoning is None:
        reasoning = LEAST_THINKING if body["model"].startswith(THINKING_MANDATORY) else NO_THINKING
    body["reasoning"] = reasoning
    if provider is not None:  # OpenRouter's provider routing, e.g. {"order": ["deepseek"], "allow_fallbacks": True}
        body["provider"] = provider
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API"],
                                          "Content-Type": "application/json", "User-Agent": "yyc-news-stream/1",
                                          "X-Title": "YYC news stream"})
    with _open(req, timeout) as r:
        resp = json.load(r)
    if "error" in resp:
        msg, code = _error_message(resp["error"])
        if _is_limit(code, msg):
            raise limit_hit(msg)
        raise RuntimeError(str(resp["error"])[:300])
    limit_clear()
    u = resp.get("usage") or {}
    _record(purpose, body["model"], messages, u, generation=resp.get("id"))
    return resp["choices"][0]["message"]


def chat_stream(messages, purpose, model=None, tools=None, max_tokens=4000, temperature=0.2, sink=None, reasoning=None,
                provider=None):
    """chat() as a stream: yields ("delta", text) as the model writes, then ("message", the full message dict, with
    tool_calls when the model calls tools). OpenRouter's server-sent events; the cost comes in the last chunk's usage."""
    body = {"model": model or MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
            "usage": {"include": True}, "stream": True,
            "reasoning": reasoning or (LEAST_THINKING if (model or MODEL).startswith(THINKING_MANDATORY) else NO_THINKING)}
    if provider is not None:
        body["provider"] = provider
    if tools:
        body["tools"] = tools
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API"],
                                          "Content-Type": "application/json", "User-Agent": "yyc-news-stream/1",
                                          "X-Title": "YYC news stream", "Accept": "text/event-stream"})
    content, calls, usage, gen = [], {}, {}, None
    with _open(req, 180) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue  # blank lines and ": OPENROUTER PROCESSING" keep-alive comments
            data = line[5:].strip()
            if data == "[DONE]":
                break
            ev = json.loads(data)
            if "error" in ev:
                msg, code = _error_message(ev["error"])
                if _is_limit(code, msg):
                    raise limit_hit(msg)
                raise RuntimeError(str(ev["error"])[:300])
            gen = ev.get("id") or gen
            if ev.get("usage"):
                usage = ev["usage"]
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content"):
                    content.append(d["content"])
                    yield "delta", d["content"]
                for tc in d.get("tool_calls") or []:  # tool calls arrive in pieces, keyed by index
                    c = calls.setdefault(tc.get("index", 0), {"id": "", "type": "function",
                                                               "function": {"name": "", "arguments": ""}})
                    c["id"] = tc.get("id") or c["id"]
                    f = tc.get("function") or {}
                    c["function"]["name"] += f.get("name") or ""
                    c["function"]["arguments"] += f.get("arguments") or ""
    limit_clear()
    _record(purpose, body["model"], messages, usage, sink, generation=gen)
    msg = {"role": "assistant", "content": "".join(content)}
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    yield "message", msg


def text_stream(prompt, purpose, model=None, max_tokens=4000, sink=None, **opts):
    """text() as a stream: yields the text pieces; the generator's return value is the whole text. opts: chat_stream's
    reasoning and provider"""
    full = ""
    for kind, v in chat_stream([{"role": "user", "content": prompt}], purpose, model=model, sink=sink,
                               max_tokens=max_tokens, **opts):
        if kind == "delta":
            full += v
            yield v
    return full.strip()


def text(prompt, purpose, model=None, max_tokens=4000):
    return (chat([{"role": "user", "content": prompt}], purpose, model=model, max_tokens=max_tokens)
            .get("content") or "").strip()


class Prompt(str):
    """a prompt whose first `cached` characters are the same in every call of its kind (jsonc marks them for the
    provider's prompt cache); a plain str everywhere else"""
    def __new__(cls, text, cached=0):
        o = super().__new__(cls, text)
        o.cached = cached
        return o


def jsonc(prompt, purpose, model=None, max_tokens=4000, images=None, reasoning=None, provider=None, timeout=180):
    """one JSON answer. images: [(caption, JPEG bytes)] attached after the prompt, each after its caption (the
    scorer reads a post's meaningful pictures itself: images.for_scoring). reasoning: OpenRouter's reasoning setting
    and provider: OpenRouter's provider routing (chat), e.g. stage 2's streams.CONFIRM_OPTIONS; timeout: seconds. A
    Prompt's cached part (the scorer's rubric and topics, 2026-10-28: ~27k characters of background briefs) is
    marked for the provider's prompt cache, which bills it at a tenth of the input price on the next call within
    minutes"""
    cached = getattr(prompt, "cached", 0)
    prompt = content = str(prompt)
    if cached or images:
        content = ([{"type": "text", "text": prompt[:cached], "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": prompt[cached:]}] if cached else [{"type": "text", "text": prompt}])
    if images:
        import base64
        for caption, jpeg in images:
            content += [{"type": "text", "text": caption},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]
    t = chat([{"role": "user", "content": content}], purpose, model=model, json_mode=True,
             max_tokens=max_tokens, **({"reasoning": reasoning} if reasoning is not None else {}),
             **({"provider": provider} if provider is not None else {}),
             **({"timeout": timeout} if timeout != 180 else {})).get("content") or "{}"
    i = t.find("{")  # the first JSON object is the answer: a model sometimes writes more after it ("Extra data")
    return json.JSONDecoder().raw_decode(t[i:])[0] if i >= 0 else json.loads(t)


# ---- tagging (K-Matrix's full tag set, brochure p.5) ----

SENTIMENTS = ("positive", "neutral", "negative", "mixed", "irony")
LABELS = {  # label: (emoji, group, meaning)
    "repute": ("👑", "relevance", "impacts the subject's image or credibility"),
    "goods": ("🛍", "relevance", "mentions a specific product"),
    "service": ("🤝", "relevance", "mentions a specific service"),
    "rival": ("⚔", "relevance", "references one or more competitors or rivals"),
    "issue": ("⚠", "relevance", "signals a potential PR issue"),
    "crisis": ("🚨", "relevance", "signals a severe PR issue"),
    "kol": ("👸", "relevance", "posted by or about a user with significant reach"),
    "engaging": ("🔥", "engagement", "notable amount of likes, shares or comments"),
    "trend": ("📈", "engagement", "part of a larger trending topic or hashtag"),
    "ask": ("❓", "engagement", "someone asking something that may need a response"),
    "review": ("📝", "intent", "someone sharing an opinion or experience"),
    "accuse": ("😡", "intent", "explicit dissatisfaction or accusation"),
    "praise": ("🤩", "intent", "explicit positive feedback"),
    "rumor": ("🗣", "intent", "unverified claim or speculation"),
    "urgent": ("⏰", "action", "requires immediate attention"),
    "chance": ("💡", "action", "a chance to amplify or engage positively"),
    "monitor": ("👀", "action", "worth watching, no immediate action"),
}
SENT_EMOJI = {"positive": "😄", "neutral": "😐", "negative": "😣", "mixed": "🤔", "irony": "😏"}


def label_lines():
    return "\n".join(f"    {k} ({v[2]})" for k, v in LABELS.items())


# ---- summaries and themes (brochure pp.24–31) ----

def post_lines(posts, n=150, text_chars=200):
    out = []
    for p in posts[:n]:
        t = (p.get("text") or "")[:text_chars].replace("\n", " ")
        out.append(f"- [{p.get('published') or p.get('seen') or ''}] [{p.get('website')}{'·' + p['board'] if p.get('board') else ''}]"
                   f"{' (' + p['author'] + ')' if p.get('author') else ''} {p['title'][:200]}"
                   f"{' — ' + t if t and t[:40] not in p['title'] else ''} <{p['url']}>")
    return "\n".join(out)


PERSONA_POSTS = 80
DAILY_POSTS = 120
PERSONA_MAX = 1500


DAILY_PROMPT = """Write the AI part of a daily social-listening email for "{name}" ({day}), in Traditional Chinese with
Hong Kong usage. Format exactly like this example (one heading line, then 3–6 points, each a short topic name and one
sentence on what people said):
政府財政預算案 2025昨天引發下列討論:
私家車陸路離境稅：網民批200元費用增出行成本，影響大灣區融合，促調整收費方式。
物業印花稅：降至100元獲讚，網民認同中原低息定按計劃助置業。

Then a line "建議行動：" with one recommended action for a PR team, based only on the posts.
Answer JSON only: {{"heading": "…", "points": [{{"topic": "…", "text": "…"}}], "action": "…"}}

Posts ({count}):
{lines}"""


def daily_summary(name, day, posts):
    return jsonc(DAILY_PROMPT.format(name=name, day=day, count=len(posts), lines=post_lines(posts, DAILY_POSTS, 150)),
                 "report_summary", model=REPORT_MODEL, max_tokens=REPORT_TOKENS, **REPORT_OPTIONS)


THEME_PROMPT = """Read these posts collected for the monitoring profile "{name}" and create the 3–7 themes that best
divide them (no fixed list: pick what this data is actually about), each named in Traditional Chinese with a short
English gloss, e.g. "系統穩定性與登入問題 (Stability & Login)". Always add "其他 (General/Others)" last. Then assign every
post to one theme by the theme's number (1 = the first theme).
Answer JSON only: {{"themes": ["…"], "assign": {{"<post index>": <theme number>}}}}

Posts:
{lines}"""


def _assign(r, chunk, names):
    out = {}
    for i, t in (r.get("assign") or {}).items():
        if not str(i).isdigit() or int(i) >= len(chunk):
            continue
        if isinstance(t, str) and t.strip().isdigit():
            t = int(t)
        if isinstance(t, int) and 1 <= t <= len(names):
            out[chunk[int(i)]["id"]] = names[t - 1]
        elif t in names:
            out[chunk[int(i)]["id"]] = t
    return out


def themes(name, posts):
    """→ (themes, {post_id: theme}) for up to 300 posts (in 100-post chunks after the first defines the themes)"""
    first = posts[:100]
    lines = "\n".join(f"{i}. [{p.get('website')}] {p['title'][:160]}" for i, p in enumerate(first))
    try:
        r = jsonc(THEME_PROMPT.format(name=name, lines=lines), "themes", model=REPORT_MODEL, max_tokens=REPORT_TOKENS,
                  **REPORT_OPTIONS)
    except ValueError:  # malformed JSON once: try again
        r = jsonc(THEME_PROMPT.format(name=name, lines=lines), "themes", model=REPORT_MODEL, max_tokens=REPORT_TOKENS,
                  **REPORT_OPTIONS)
    names = [str(t) for t in (r.get("themes") or [])] or ["其他 (General/Others)"]
    assign = _assign(r, first, names)
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(names))
    for s in range(100, min(len(posts), 300), 100):
        chunk = posts[s:s + 100]
        lines = "\n".join(f"{i}. [{p.get('website')}] {p['title'][:160]}" for i, p in enumerate(chunk))
        r2 = jsonc(f"Assign each post to exactly one of these themes, by the theme's number:\n{numbered}\n"
                   f'Answer JSON only: {{"assign": {{"<post index>": <theme number>}}}}\n\nPosts:\n{lines}', "themes",
                   model=REPORT_MODEL, max_tokens=REPORT_TOKENS, **REPORT_OPTIONS)
        assign.update(_assign(r2, chunk, names))
    return names, assign


PERSONA_PROMPT = """You profile an online author for a Hong Kong PR team (K-Matrix "persona analysis": brand advocates,
critics and micro-influencers). Using only the posts below by "{author}" (newest first) and the statistics, write in
Traditional Chinese with Hong Kong usage: 1) 身份/類型 (advocate, critic, neutral reporter, KOL…) with the reason,
2) 最常出沒的平台/版面, 3) 關注的議題 (the 3–5 topics they post about most), 4) 立場與情緒 toward the subject "{subject}",
5) 對公關的建議 (one line). Cite posts as markdown links [website](url). Short markdown, no preamble.

Statistics: {stats}
Posts:
{lines}"""


def persona_prompt(author, subject, stats, posts):
    return PERSONA_PROMPT.format(author=author, subject=subject, stats=json.dumps(stats, ensure_ascii=False),
                                 lines=post_lines(posts, PERSONA_POSTS, 200))


def persona(author, subject, stats, posts, sink=None):
    """streams the persona note (a generator; its return value is the whole text)"""
    return (yield from text_stream(persona_prompt(author, subject, stats, posts), "persona", model=SMART_MODEL,
                                   max_tokens=PERSONA_MAX + THINKING_ROOM, sink=sink, **SMART_OPTIONS))


def md_to_html(md):
    """tiny markdown → HTML (headings, bullets, bold, links) for summaries shown on pages and in emails"""
    import html as H
    out, in_ul, table = [], False, []

    def flush_table():
        if not table:
            return
        rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in table
                if not re.fullmatch(r"\|?[\s:|-]+\|?", r.strip())]
        if rows:
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(H.escape(c))}</th>" for c in rows[0]) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(H.escape(c))}</td>" for c in r) + "</tr>" for r in rows[1:])
                       + "</tbody></table>")
        table.clear()

    for line in (md or "").splitlines():
        s = line.rstrip()
        if s.strip().startswith("|"):
            table.append(s)
            continue
        flush_table()
        m = re.match(r"^\s*[-*•]\s+(.*)", s)
        if m:
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append("<li>" + inline(H.escape(m.group(1))) + "</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        h = re.match(r"^(#{1,4})\s+(.*)", s)
        if h:
            out.append(f"<h{len(h.group(1)) + 2}>" + inline(H.escape(h.group(2))) + f"</h{len(h.group(1)) + 2}>")
        elif s.strip():
            out.append("<p>" + inline(H.escape(s)) + "</p>")
    flush_table()
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def inline(s):
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    # site-relative: only a post's item page
    s = re.sub(r"\[([^\]]+)\]\((/item/\d+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\[([^\]]+)\]\((/[^)\s]*)\)", r"\1", s)
    s = re.sub(r"&lt;(https?://[^&\s]+)&gt;", r'<a href="\1" target="_blank" rel="noopener">link</a>', s)
    return s
