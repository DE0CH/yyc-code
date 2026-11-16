"""The AI server: the cluster's `web` Deployment (gunicorn app:application). The site's pages and reads are the edge
app's (web-app/, Cloudflare Worker yyc-web, reading YYC's Postgres through Hyperdrive); the router Worker (edge/worker.js) sends here only what
asks a model (the persona button) or runs the data API's tools, at the URLs the pages call. The site is read-only: no admin page, no edits;
Deyao's control plane is scripts (infra/control.py, infra/thresholds.py, reports.py; Deyao, 2026-10-14).

Public:            GET /api/ai/estimate (the persona button's model and projected cost, no model call), /app/login
                   (Access signs the reader in on the way; back to the page), GET /app/api/v1/openapi.json, /healthz (app.py)
Approved accounts: POST /app/ai/persona (數據分析's persona button, NDJSON), GET+POST /app/api/v1/<tool> (the data API
                   for AI, tools/), behind the Cloudflare Access app on newswatch.yyc-internal.com/app, whose policy lists the
                   accounts Deyao approved one by one (Deyao, 2026-10-13: no AI limit for them), and checked again
                   here: each request must carry Access's signed JWT (Cf-Access-Jwt-Assertion, RS256, this app's
                   audience).
                   POST /app/proof/flag (the proof view's 標記為有誤: a flag with an optional note on one stored post,
                   proof.py; the site's one write outside the AI routes, Deyao 2026-10-20), same accounts and check.
"""
import base64, contextvars, hashlib, json, os, queue, re, threading, time, urllib.request

import ai, corpus, flags, insights

ACCESS_TEAM = "https://yyc-hk.cloudflareaccess.com"
ACCESS_AUD = os.environ.get("ACCESS_AUD", "8b644dde1fadd76a241cace4428d57ebe8fccb6cdfe0af987e5f35bc7e31dd9b")  # the /app Access app
# the beta site's Access app (yyc-beta.deyaochen.com, the whole host: Deyao and the people he adds): signed in there
# counts as approved, like /app on the main site
ACCESS_AUDS = {ACCESS_AUD} | ({os.environ["BETA_ACCESS_AUD"]} if os.environ.get("BETA_ACCESS_AUD") else set())
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "chendeyao000@gmail.com")
ADMIN_DEV = os.environ.get("ADMIN_DEV") == "1"  # local test instance only (never set on the server's service)


# ---- Cloudflare Access JWT (RS256, verified with the standard library) ----

_certs = {"at": 0, "keys": {}}


def _b64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _keys():
    if time.time() - _certs["at"] > 3600 or not _certs["keys"]:
        with urllib.request.urlopen(ACCESS_TEAM + "/cdn-cgi/access/certs", timeout=15) as r:
            d = json.load(r)
        _certs["keys"] = {k["kid"]: (int.from_bytes(_b64(k["n"]), "big"), int.from_bytes(_b64(k["e"]), "big"))
                          for k in d["keys"] if k.get("kty") == "RSA"}
        _certs["at"] = time.time()
    return _certs["keys"]


SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def verify_access(token):
    """→ the JWT's claims if it is a valid token of the /app Access app (any account its policy lets in), else None"""
    try:
        h, p, s = token.split(".")
        head = json.loads(_b64(h))
        if head.get("alg") != "RS256":
            return None
        keys = _keys()
        if head.get("kid") not in keys:
            _certs["at"] = 0
            keys = _keys()
        n, e = keys[head["kid"]]
        k = (n.bit_length() + 7) // 8
        em = pow(int.from_bytes(_b64(s), "big"), e, n).to_bytes(k, "big")
        digest = hashlib.sha256(f"{h}.{p}".encode()).digest()
        expect = b"\x00\x01" + b"\xff" * (k - 3 - len(SHA256_PREFIX) - 32) + b"\x00" + SHA256_PREFIX + digest
        if em != expect:
            return None
        c = json.loads(_b64(p))
        aud = c.get("aud")
        aud = aud if isinstance(aud, list) else [aud]
        if not (set(aud) & ACCESS_AUDS) or c.get("exp", 0) < time.time() or c.get("iss") != ACCESS_TEAM:
            return None
        return c
    except Exception:
        return None


def access_user(h):
    """the signed-in Access account's email, or None. Cloudflare sends the token only on /app paths, and the Access
    policy lets in only the accounts Deyao approved one by one (Deyao, 2026-10-13: AI features, which cost money,
    are for those accounts until a company-wide login exists)"""
    if ADMIN_DEV:
        return ADMIN_EMAIL
    tok = h.headers.get("Cf-Access-Jwt-Assertion")
    c = verify_access(tok) if tok else None
    return ((c.get("email") or "").lower() or None) if c else None


# ---- helpers ----

def period(q, default_days=7):
    today = insights.today()
    end = (q.get("to") or [today])[0]
    start = (q.get("from") or [insights.day_add(end, -default_days + 1)])[0]
    for d in (start, end):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise ValueError("dates are YYYY-MM-DD")
    if start > end:
        start, end = end, start
    if len(insights.days_between(start, end)) > 400:
        raise ValueError("at most 400 days")
    return start, end


def one(q, k, default=None):
    v = q.get(k)
    return v[0] if v else default


def ai_cache_key(*parts):
    return "ai:" + hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:24]


# ---- routes ----

# AI behind a feature flag (flags.py): path (or path prefix) → flag. Off answers 404; the request's Host decides
# (the main site is the simple monitor, the beta site has every flag on)
GATES = [("/app/ai", "ai"), ("/api/ai", "ai")]


def gated(h, path):
    """the flag that keeps this path off for this request, or None"""
    p = path.rstrip("/") or "/"
    for prefix, flag in GATES:
        if (p == prefix or p.startswith(prefix + "/")) and not flags.on(flag, h.headers.get("Host", "")):
            return flag
    return None


# where /app/login may send the reader back to: the pages that link it (the edge app's), never another host
LOGIN_NEXT = re.compile(r"/(dashboard|item/\d+)[^\s]*")


def handle_get(h, path, q):
    """→ True when handled"""
    if gated(h, path):
        return h.json(404, {"error": "not found"}) or True
    if path == "/api/ai/estimate":  # projected model and cost of an AI button, before it is pressed (no model call)
        body = {k: v[0] for k, v in q.items()}
        try:
            return h.json(200, ai_post(body.pop("kind", ""), body, dry=True)) or True
        except (KeyError, TypeError):
            return h.json(404, {"error": "no such profile"}) or True
        except ValueError as e:
            return h.json(400, {"error": str(e)}) or True
    if path == "/app/login":  # Access has signed the reader in on the way here: back to the page they came from
        nxt = one(q, "next", "/dashboard")
        nxt = nxt if LOGIN_NEXT.fullmatch(nxt) else "/dashboard"
        return h.send(302, "text/plain", b"", extra={"Location": nxt}) or True
    if path.startswith("/app/api/v1/"):  # the data API for AI (tools/, docs/agent-platform.md)
        return api_v1(h, path, {k: v[0] if len(v) == 1 else v for k, v in q.items()}) or True
    return False


NOT_APPROVED = "只供 Deyao 批准的帳戶使用"


def api_v1(h, path, args, post=False):
    """GET (query string) or POST (JSON body) /app/api/v1/<tool>: one tool of the shared registry, as the signed-in
    account (paid tools' cost is recorded against it); /app/api/v1/openapi.json describes them all"""
    import tools
    name = path[len("/app/api/v1/"):]
    if name == "openapi.json":
        return h.json(200, tools.openapi())
    email = access_user(h)
    if not email:
        return h.json(403, {"error": NOT_APPROVED})
    try:
        t = next((x for x in tools.tools() if x.name == name), None)
        if t and not post:
            args = tools.coerce(t.schema, args)
        return h.json(200, tools.call(name, args, {"email": email}))
    except (ValueError, LookupError) as e:
        return h.json(400, {"error": str(e)})


def handle_post(h, path, body):
    if path == "/internal/web_fetch":  # YYC AI's web_fetch, from inside the cluster (webfetch.py)
        import webfetch
        if not webfetch.token_ok(h.headers.get("Authorization")):
            return h.json(403, {"error": "forbidden"}) or True
        try:
            return h.json(200, webfetch.web_fetch((body or {}).get("url"))) or True
        except ValueError as e:
            return h.json(400, {"error": str(e)}) or True
    if gated(h, path):
        return h.json(404, {"error": "not found"}) or True
    if path.startswith("/app/api/v1/"):
        return api_v1(h, path, body if isinstance(body, dict) else {}, post=True) or True
    if path == "/app/proof/flag":  # the proof view (web-app -ProofPage.tsx): this entry looks wrong (proof.py)
        email = access_user(h)
        if not email:
            return h.json(403, {"error": NOT_APPROVED}) or True
        import proof
        try:
            return h.json(200, {"flag": proof.add(body.get("id"), body.get("note"), email)}) or True
        except ValueError as e:
            return h.json(400, {"error": str(e)}) or True
    if path.startswith("/app/ai/") and not access_user(h):
        return h.json(403, {"error": NOT_APPROVED}) or True
    if path.startswith("/app/ai/"):
        try:
            out = ai_post(path[len("/app/ai/"):], body)
            return (h.stream(out) if isinstance(out, Stream) else h.json(200, out)) or True
        except (ValueError, KeyError) as e:
            return h.json(400, {"error": str(e)}) or True
    return False


# ---- streamed AI answers: NDJSON lines {"t":"delta","text"} as the model writes, then {"t":"done", …the answer}
# ({"t":"error","error"} if it fails midway). A cached answer is still a plain JSON reply.

class Stream:
    """a streamed reply: app.py sends it as application/x-ndjson, flushing each line (nginx: X-Accel-Buffering: no).
    A heartbeat line {"t":"ping"} goes first and then every HEARTBEAT seconds while the events are silent (a model call
    or a tool can run for a minute): the pages treat a stream with no line for 15 s as broken and stop their spinner
    (Deyao, 2026-10-16: a spinner is for a fetch that answers within seconds). The events run in a thread of their own
    (with the request's context); when the reader goes away the thread stops at the next event."""
    HEARTBEAT = 5.0

    def __init__(self, events):
        self.events = events

    @staticmethod
    def _line(ev):
        return (json.dumps(ev, ensure_ascii=False, default=str) + "\n").encode()

    def lines(self):
        q, stop = queue.Queue(), threading.Event()

        def pump():
            try:
                for ev in self.events:
                    if stop.is_set():
                        break
                    q.put(("ev", ev))
            except Exception as e:  # noqa: BLE001 — the status line is already sent: report it in the stream
                q.put(("err", e))
            finally:
                close = getattr(self.events, "close", None)
                if close:
                    try:
                        close()
                    except Exception:  # noqa: BLE001
                        pass
                q.put(("end", None))

        ctx = contextvars.copy_context()
        threading.Thread(target=ctx.run, args=(pump,), daemon=True, name="stream").start()
        try:
            yield self._line({"t": "ping"})
            while True:
                try:
                    kind, v = q.get(timeout=self.HEARTBEAT)
                except queue.Empty:
                    yield self._line({"t": "ping"})
                    continue
                if kind == "end":
                    break
                if kind == "err":
                    print("stream failed:", repr(v)[:300], flush=True)
                    yield self._line({"t": "error", "error": str(v)[:300]})
                    break
                yield self._line(v)
        finally:
            stop.set()


def stream_text(gen, done, sink):
    """gen: an ai text generator (returns the whole text); done(text) → the final object, sent as {"t":"done", …} with
    "usage" (the model and OpenRouter's charged cost of the calls in sink)"""

    def events():
        while True:
            try:
                d = next(gen)
            except StopIteration as e:
                full = (e.value or "").strip()
                break
            yield {"t": "delta", "text": d}
        yield dict(done(full), t="done", usage=ai.usage(sink))
    return Stream(events())


def cached_estimate():
    """a result made earlier is shown again from the cache: no model call, no cost"""
    return {"model": ai.SMART_MODEL, "model_name": ai.model_name(ai.SMART_MODEL), "usd": 0.0}


def ai_post(name, body, dry=False):
    """run an AI action (streamed; a cached result as plain JSON). dry=True: nothing is called, → the projection
    instead (model, projected cost; ai.estimate), and a cached result costs nothing"""
    q = {k: [str(v)] for k, v in body.items() if v not in (None, "")}
    if name == "persona":
        pid, start, end = one(q, "p"), *period(q, 90)
        author = body.get("author")
        key = ai_cache_key("persona", pid, author, end)
        hit = corpus.kv_get(key)
        if hit and dry:
            return dict(cached_estimate(), cached=True)
        per = insights.persona(pid, author, start, end)
        read = per["recent"][:ai.PERSONA_POSTS]
        ctx = lambda md: insights.make_context("persona", pid, f"AI 人物分析：{author}", f"{author} 的帖子（截至 {end}）", md, read,
                                            per["posts"], f"AI 讀了 {author} 最近的 {len(read)} 篇帖子" +
                                            (f"（共 {per['posts']} 篇）" if per["posts"] > len(read) else ""))
        if hit:
            if not hit.get("context"):
                hit = dict(hit, context=ctx(hit["markdown"]), posts=len(read))
                corpus.kv_set(key, hit)
            return dict(hit, cached=True)
        stats = {k: per[k] for k in ("posts", "matched_posts", "sentiment_index_on_profile", "boards", "websites")}
        subject = insights.profile(pid)["name"]
        if dry:
            return ai.estimate(ai.persona_prompt(author, subject, stats, per["recent"]), "persona", ai.SMART_MODEL, ai.PERSONA_MAX)
        sink = []
        def done(md):
            out = {"markdown": md, "html": ai.md_to_html(md), "posts": len(read), "context": ctx(md),
                   "at": insights.now().isoformat(timespec="seconds"), "usage": ai.usage(sink)}
            corpus.kv_set(key, out)
            return out
        return stream_text(ai.persona(author, subject, stats, per["recent"], sink=sink), done, sink)
    raise ValueError("unknown AI endpoint")
