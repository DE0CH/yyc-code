// yyc-mcp: YYC's news-monitoring data as a REST API (/api/v1/<tool>, OpenAPI at /api/v1/openapi.json), read-only, for
// YYC AI (its tools role, a service token) and scripts (personal tokens made at /tokens). The gate is the bearer token
// plus YYC's access catalogue (catalogue.ts) on every call; the web pages (/tokens, /admin) sign in through Cloudflare
// Access (signin.ts). It runs on Node in YYC's cluster beside Postgres (node/server.ts); README.md has the design.
import type { Sql } from "../../web-app/src/server/db";
import { isAdmin, isUser } from "./catalogue";
import type { Env, Props } from "./env";
import { LOGIN, SESSION, authorizeUrl, cookie, exchange, randomToken, s256, seal, setCookie, unseal } from "./signin";
import * as P from "./pages";
import { registry, sha256 } from "./registry";
import { coerce, runTool, TOOLS, ToolError } from "./tools";
import { timed, timing } from "./timing";
import { failure } from "./failure";

async function withTiming(f: () => Promise<Response>): Promise<Response> {
  const marks: [string, number][] = [];
  const t = Date.now();
  const r = await timing.run(marks, f);
  const h = [...marks, ["total", Date.now() - t] as [string, number]].map(([n, d]) => `${n};dur=${d}`).join(", ");
  const out = new Response(r.body, r);
  out.headers.set("server-timing", h);
  return out;
}

const json = (v: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers } });

/** tokens in Secret mcp-env (SERVICE_TOKENS: {"<sha256 of the token>": "<email>"}): YYC AI's YYC_JARVIS_MCP_TOKEN and
 *  Deyao's Claude sessions' YYC_MCP_TOKEN (store yyc). A machine identity: outside the catalogue, the same rate limit
 *  and audit as a personal token. */
async function serviceToken(env: Env, token: string): Promise<{ email: string; service: true } | null> {
  if (!env.SERVICE_TOKENS) return null;
  try {
    const m = JSON.parse(env.SERVICE_TOKENS) as Record<string, string>;
    const email = m[await sha256(token)];
    return email ? { email: email.toLowerCase(), service: true } : null;
  } catch { return null; }
}

/** who a bearer token belongs to: a service token (SERVICE_TOKENS), else a personal token (yyc_pat_…) */
async function bearer(req: Request, env: Env): Promise<Props | null> {
  const tok = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "").trim();
  if (!tok) return null;
  const r = await timed("token", async () => await serviceToken(env, tok) ?? (tok.startsWith("yyc_pat_") ? await registry(env).resolveToken(tok) : null));
  return r ? { email: r.email, ...("service" in r ? { service: true } : {}) } : null;
}

// ---- a call with a valid token: on the catalogue? within the rate limit? then the tool, logged ----

type Ctx = ExecutionContext & { props: Props };

async function protectedCall(req: Request, env: Env, ctx: Ctx): Promise<Response> {
  const email = ctx.props.email;
  const gate = await timed("admit", () => registry(env).admit(email, ctx.props.service));
  if (!gate.ok) {
    if (gate.retryAfter) {
      return json({ error: `rate limit: at most ${env.RATE_PER_MIN} calls a minute per account; try again in ${gate.retryAfter} s` }, 429, { "retry-after": String(gate.retryAfter) });
    }
    return json({ error: `${email} is not on YYC's list of people who may use this (ask Deyao to add it)` }, 403);
  }
  return restCall(req, env, ctx, env.DB, email);
}

function logCall(env: Env, ctx: ExecutionContext, c: { email: string; tool: string; via: string; client?: string; status: number; t0: number; args?: unknown }) {
  ctx.waitUntil(registry(env).log({ ...c, ms: Date.now() - c.t0 }).catch(e => console.error("audit log failed", e)));
  console.log(JSON.stringify({ audit: true, email: c.email, tool: c.tool, via: c.via, status: c.status, ms: Date.now() - c.t0 }));
}

/** the call log's client: "personal-token", or for a service token used by a multi-user app (YYC AI) the staff member
 *  it acted for, from the X-YYC-User header that app sets. Only a service token may name someone. */
function clientLabel(req: Request, ctx: Ctx): string | undefined {
  const who = req.headers.get("x-yyc-user");
  return ctx.props.service ? (who ? `service token for ${who.slice(0, 200)}` : "service token") : "personal-token";
}

/** whom a call is for, as the WiseSearch relay logs it: the staff member a service token's app names in X-YYC-User
 *  (YYC AI), else the account the token belongs to */
function actingFor(req: Request, ctx: Ctx, email: string): string {
  const who = (req.headers.get("x-yyc-user") || "").trim().toLowerCase();
  return who && ctx.props.service && /^[^@\s]{1,64}@[a-z0-9.-]{1,190}\.[a-z]{2,24}$/.test(who) ? who : email;
}

async function restCall(req: Request, env: Env, ctx: Ctx, sql: Sql, email: string) {
  const name = new URL(req.url).pathname.replace(/^\/api\/v1\/?/, "").replace(/\/$/, "");
  const t = TOOLS.find(x => x.name === name);
  if (!t) return json({ error: `no such endpoint; tools: ${TOOLS.map(x => x.name).join(", ")} (spec: /api/v1/openapi.json)` }, 404);
  if (req.method !== "GET" && req.method !== "POST") return json({ error: "use GET (query string) or POST (JSON body)" }, 405, { allow: "GET, POST" });
  const t0 = Date.now();
  let args: Record<string, unknown>;
  try {
    const body = req.method === "POST" ? await req.text() : "";
    args = req.method === "GET" ? coerce(t.schema, new URL(req.url).searchParams) : body.trim() ? JSON.parse(body) : {};
    if (typeof args !== "object" || args === null || Array.isArray(args)) throw new Error("not an object");
  } catch { return json({ error: "the body must be a JSON object of the tool's arguments" }, 400); }
  try {
    const out = await timed("tool", () => runTool(sql, name, args, { env, who: actingFor(req, ctx, email) }));
    logCall(env, ctx, { email, tool: name, via: "rest", client: clientLabel(req, ctx), status: 200, t0, args });
    return json(out);
  } catch (e) {
    const known = e instanceof ToolError;
    logCall(env, ctx, { email, tool: name, via: "rest", client: clientLabel(req, ctx), status: known ? 400 : 500, t0, args });
    if (!known) console.error("tool failed", name, e);
    return json({ error: known ? (e as Error).message : failure(e) }, known ? 400 : 500);
  }
}

// ---- OpenAPI 3.1 from the same registry ----

function openapi(env: Env) {
  const paths: Record<string, unknown> = {};
  for (const t of TOOLS) {
    const props = (t.schema.properties || {}) as Record<string, Record<string, unknown>>;
    const resp = { "200": { description: "the tool's answer", content: { "application/json": { schema: { type: "object" } } } },
      "400": { description: "bad arguments; `error` says what to change" }, "401": { description: "no or invalid token" },
      "403": { description: "account not on YYC's list" }, "429": { description: "rate limit; see Retry-After" } };
    paths[`/api/v1/${t.name}`] = {
      get: { operationId: t.name, summary: t.title, description: t.description, parameters: Object.entries(props).map(([k, p]) => ({
        name: k, in: "query", required: (t.schema.required || []).includes(k), description: (p.description as string) || "",
        schema: Object.fromEntries(Object.entries(p).filter(([x]) => x !== "description")), ...(p.type === "array" ? { style: "form", explode: false } : {}) })), responses: resp },
      post: { operationId: t.name + "_post", summary: t.title, description: t.description,
        requestBody: { required: false, content: { "application/json": { schema: t.schema } } }, responses: resp },
    };
  }
  return { openapi: "3.1.0",
    info: { title: "YYC news monitoring data", version: "1.0.0",
      description: "Read-only access to what YYC's news stream collects: posts (full text, comments, transcripts), streams and the AI's per-stream scores, analysis views and report files; and WiseSearch (Wisers' news archive) on YYC's account (wisers_*: searches and document opens cost Wisers quota). Authorization: Bearer with a personal access token (yyc_pat_…, made at /tokens; YYC staff only)." },
    servers: [{ url: env.BASE_URL }], paths,
    components: { securitySchemes: {
      bearer: { type: "http", scheme: "bearer", description: "a personal access token from /tokens" } } },
    security: [{ bearer: [] }] };
}

// ---- the web pages' own sign-in (/tokens, /admin) ----

interface Session { email: string; name: string | null }

async function session(req: Request, env: Env) { return unseal<Session>(env, "session", cookie(req, SESSION)); }

async function login(req: Request, env: Env) {
  const next = new URL(req.url).searchParams.get("next") || "/tokens";
  const safe = next.startsWith("/") && !next.startsWith("//") ? next : "/tokens";
  const state = "w." + randomToken(18), verifier = randomToken(48);
  const c = await seal(env, "login", { state, verifier, next: safe }, 600);
  return new Response(null, { status: 302, headers: { location: authorizeUrl(env, state, await s256(verifier)), "set-cookie": setCookie(LOGIN, c, 600), "cache-control": "no-store" } });
}

async function callback(req: Request, env: Env) {
  const state = new URL(req.url).searchParams.get("state") || "";
  const l = await unseal<{ state: string; verifier: string; next: string }>(env, "login", cookie(req, LOGIN));
  if (!l || l.state !== state) return P.page("無法登入", P.message("登入已過期", "請重新開始登入。"), 400);
  const q = new URL(req.url).searchParams;
  if (q.get("error") || !q.get("code")) return P.page("無法登入", P.message("無法登入", "登入已取消或未獲允許。請重新開始登入。"), 400);
  let user;
  try { user = await exchange(env, q.get("code")!, l.verifier); }
  catch (e) { return P.page("無法登入", P.message("無法登入", (e as Error).message), 502); }
  const s = await seal(env, "session", { email: user.email, name: user.name } satisfies Session, 12 * 3600);
  const h = new Headers({ location: env.BASE_URL + l.next, "cache-control": "no-store" });
  h.append("set-cookie", setCookie(SESSION, s, 12 * 3600));
  h.append("set-cookie", setCookie(LOGIN, "", 0));
  return new Response(null, { status: 302, headers: h });
}

const csrfOf = (env: Env, s: Session) => seal(env, "csrf", s.email, 12 * 3600);
async function csrfOk(env: Env, s: Session, form: FormData) { return (await unseal<string>(env, "csrf", String(form.get("csrf") || ""))) === s.email; }
const toLogin = (env: Env, path: string) => new Response(null, { status: 302, headers: { location: env.BASE_URL + "/login?next=" + encodeURIComponent(path), "cache-control": "no-store" } });

async function tokensRoute(req: Request, env: Env, path: string) {
  const s = await session(req, env);
  if (!s) return req.method === "GET" ? toLogin(env, "/tokens") : P.page("登入已過期", P.message("登入已過期", "請重新開啟此頁。"), 401);
  if (!isUser(env, s.email)) return P.page("沒有權限", P.denied(s.email), 403);
  const reg = registry(env);
  const csrf = await csrfOf(env, s);
  let fresh;
  if (req.method === "POST") {
    const form = await req.formData();
    if (!(await csrfOk(env, s, form))) return P.page("無效要求", P.message("無效要求", "請重新開啟此頁再試。"), 403);
    if (path === "/tokens/revoke") await reg.revokeToken(s.email, String(form.get("id") || ""));
    else {
      const days = Number(form.get("days") || 365);
      const made = await reg.createToken(s.email, String(form.get("label") || "token"), [30, 90, 365].includes(days) ? days : null);
      fresh = { token: made.token, label: String(form.get("label") || "") };
    }
    if (!fresh) return new Response(null, { status: 303, headers: { location: env.BASE_URL + "/tokens" } });
  }
  return P.page("個人存取權杖", P.tokensPage(s.email, csrf, await reg.tokens(s.email), fresh));
}

/** the call log, for the catalogue's admins: /admin (page) and /admin/api/audit (JSON; also with an admin's token) */
async function adminRoute(req: Request, env: Env, path: string) {
  const url = new URL(req.url);
  let email: string | null = null;
  const s = await session(req, env);
  if (s) email = s.email;
  else if (path.startsWith("/admin/api/")) {  // Deyao's script: his personal token
    const tok = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
    if (tok.startsWith("yyc_pat_")) email = (await registry(env).resolveToken(tok) ?? await serviceToken(env, tok))?.email ?? null;
    if (!email) return json({ error: "sign in, or send an admin's personal token" }, 401);
  } else return toLogin(env, path);
  if (!isAdmin(env, email)) {
    return path.startsWith("/admin/api/") ? json({ error: "admins only" }, 403) : P.page("No access", P.message("No access", `${email} is not an admin.`), 403, {}, "en");
  }
  const reg = registry(env);
  if (path === "/admin/api/audit") {
    return json({ calls: await reg.audit(Number(url.searchParams.get("limit") || 200), url.searchParams.get("email") || undefined) });
  }
  if (path !== "/admin") return P.page("Not found", P.message("Not found", "There is no such page."), 404, {}, "en");
  return P.page("Call log", P.callsPage(await reg.audit(100)), 200, {}, "en");
}

// ---- routing ----

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url), path = url.pathname;
    if (path === "/api/v1/openapi.json") return json(openapi(env), 200, { "access-control-allow-origin": "*" });
    if (path.startsWith("/api/v1/")) {
      return withTiming(async () => {
        const props = await bearer(req, env);
        if (!props) return json({ error: "send Authorization: Bearer <a personal access token from /tokens>" }, 401, { "www-authenticate": "Bearer" });
        return protectedCall(req, env, Object.assign(ctx, { props }) as Ctx);
      });
    }
    if (path === "/callback") return callback(req, env);
    if (path === "/login") return login(req, env);
    if (path === "/logout") return new Response(null, { status: 302, headers: { location: "/", "set-cookie": setCookie(SESSION, "", 0) } });
    if (path === "/tokens" || path === "/tokens/revoke") return tokensRoute(req, env, path);
    if (path === "/admin" || path.startsWith("/admin/")) return adminRoute(req, env, path);
    if (path === "/") return P.page("YYC 新聞監察資料 API", P.landing(env.BASE_URL));
    if (path === "/privacy") return P.page("私隱及條款", P.privacy());
    if (path === "/healthz") return json({ ok: true });
    return json({ error: "not found" }, 404);
  },

  /** daily, CronJob mcp-cleanup (node/cron.ts) */
  async scheduled(env: Env) {
    await registry(env).cleanup();
  },
};
