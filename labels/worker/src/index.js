// Worker yyc-labels (labels/README.md): YYC's labels, on labels.yyc-internal.com behind the Access app "YYC 標籤".
// YYC AI draws labels and sends them to print; staff print them from the print page it links to. Templates (what
// labels are drawn from) are on the templates page.
// Every request's Cf-Access-Jwt-Assertion is checked again here: staff carry an email, YYC AI's egress the service token
// yyc-labels-ai, with the person it acts for in Aviary-Sandbox-Person or X-Jarvis-Person.
//   /                        the templates page (src/pages/app.html, public/app/)
//   /p/<id>                  a print request's page: connect the printer, print or cancel
//   /api/live                WebSocket: template and lock changes as they happen
//   /api/v2/*                the REST API (src/api.js; /api/v2/openapi.json)
//   /api/v1/*                410: replaced by v2
//   /app/*, /lib/*           static assets (public/)
import { verifyAccess } from "../../../nametags/worker/src/auth.js";
import { handleApi, V1_GONE } from "./api.js";
import appHtml from "./pages/app.html";
export { Store } from "./store.js";

const err = (status, error, extra = {}) => Response.json({ error, ...extra }, { status, headers: { "Cache-Control": "no-store" } });
const PAGE_HEADERS = {
  "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "X-Frame-Options": "DENY",
  "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
    "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'",
};

const EMAIL = /^[^\s@]{1,64}@[^\s@]{1,190}$/;
// Who a request acts for: a signed-in staff member, or the person YYC AI's egress names (only egress, behind the
// service token, can set that header: it replaces any copy the sandbox sends). null when there is none.
export function personOf(identity, req) {
  if (identity.kind === "user") return identity.email;
  const p = (req.headers.get("Aviary-Sandbox-Person") || req.headers.get("X-Jarvis-Person") || "").trim().toLowerCase();
  return EMAIL.test(p) ? p : null;
}

async function asset(env, req, cache) {
  const r = await env.ASSETS.fetch(req);
  const h = new Headers(r.headers);
  h.set("Cache-Control", cache);
  return new Response(r.body, { status: r.status, headers: h });
}

export default {
  async fetch(req, env, ctx, { fetcher = fetch } = {}) {
    const url = new URL(req.url);
    const path = url.pathname;
    let identity;
    try {
      identity = await verifyAccess(req.headers.get("Cf-Access-Jwt-Assertion"), { team: env.ACCESS_TEAM, aud: env.ACCESS_AUD, fetcher });
    } catch (e) {
      return err(e.message === "access not configured" ? 500 : 403, `forbidden: ${e.message}`);
    }
    const store = env.STORE.get(env.STORE.idFromName("main"));
    const who = personOf(identity, req);
    const o = { origin: url.origin, env, store, who, caller: identity.kind === "user" ? identity.email : "YYC AI" };

    if (path.startsWith("/api/v1/") || path === "/api/v1") return err(410, "gone", { message: V1_GONE });
    if (path.startsWith("/api/v2/")) return handleApi(req, o, path);
    if (req.method !== "GET") return err(405, "method not allowed");
    if (path === "/" || /^\/p\/[0-9a-f]{16}$/.test(path)) return new Response(appHtml, { headers: PAGE_HEADERS });
    if (path === "/api/live") return store.fetch(req);
    if (path.startsWith("/app/") || path.startsWith("/lib/")) return asset(env, req, "no-cache");
    return err(404, "not found");
  },
};
