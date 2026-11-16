// The router for newswatch.yyc-internal.com and yyc-beta.deyaochen.com, and the redirect of moved names (Cloudflare Worker
// yyc-edge on YYC's account, routes on the SaaS zone yyc-internal.com).
// Cloudflare Access runs before it, so the gates (/app and /app/wisers on the main host, the whole beta host) apply.
//   - The cluster (through the Cloudflare tunnel, fetch(req) to the same host): the AI server's requests (the
//     dashboard's persona button, the cost estimate, the AI data API, the MCP server, the WiseSearch relay's
//     JSON API /app/wisers/api/), the proof view's flag (POST /app/proof/flag)
//     and the Access sign-in hop /app/login.
//   - Everything else: the site, the edge app (Worker yyc-web on YYC's own Cloudflare account, web-app/), reached at
//     its workers.dev address with the X-YYC-Edge secret (env EDGE_SECRET) and the original host in X-Forwarded-Host.
// Moved names (MOVED): a 301 to the new host, same path and query; Access no longer covers the old name, so this answers
// before any sign-in and nothing reaches the origin.
// Deploy: news-stream/edge/deploy.sh. Response header X-Edge: APP | ORIGIN | MOVED.

const APP_ORIGIN = "https://yyc-web.yyc-news.workers.dev";

// old host → new host (Deyao, 2026-10-27: YYC AI moves to ai.yyc-internal.com, the old name redirects permanently)
// yyc.deyaochen.com → newswatch.yyc-internal.com (Deyao, 2026-11-03: the monitoring site moves)
const MOVED = { "ai.yyc.deyaochen.com": "ai.yyc-internal.com", "yyc.deyaochen.com": "newswatch.yyc-internal.com" };

// the cluster's paths: POST to the AI server, and the paths its tunnel routes to other services
const ORIGIN = [
  /^\/app\/ai\//,                         // the persona's AI 分析 (POST, NDJSON)
  /^\/api\/ai\/estimate$/,                // an AI button's model and projected cost
  /^\/app\/api\/v1\//,                    // the AI data API (tools/)
  /^\/app\/wisers\/api\//,                 // the WiseSearch relay's JSON API (its own tunnel ingress); the page is the app's
  /^\/app\/login$/,                       // Access has signed the reader in: back to the page they came from
  /^\/healthz$/,
];
// POST only: the proof view's 標記為有誤 (proof.py; the proof view itself and its reads, GET, are the app's)
const ORIGIN_POST = /^\/app\/proof\/flag$/;

async function toApp(req, env, url) {
  const h = new Headers(req.headers);
  h.set("X-YYC-Edge", env.EDGE_SECRET || "");
  h.set("X-Forwarded-Host", url.host);
  h.delete("Host");
  const r = await fetch(new Request(APP_ORIGIN + url.pathname + url.search, {
    method: req.method, headers: h, body: req.body, redirect: "manual",
  }));
  if (r.status === 101) return r;  // a WebSocket (/live): hand it through as it is
  const out = new Response(r.body, r);
  out.headers.set("X-Edge", "APP");
  return out;
}

async function toOrigin(req) {
  const r = await fetch(req);
  const out = new Response(r.body, r);
  out.headers.set("Cache-Control", "no-store");
  out.headers.set("X-Edge", "ORIGIN");
  return out;
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const to = MOVED[url.hostname];
    if (to) {
      return new Response(null, {
        status: 301, headers: { Location: `https://${to}${url.pathname}${url.search}`, "X-Edge": "MOVED" },
      });
    }
    if (ORIGIN.some(re => re.test(url.pathname)) || (req.method === "POST" && ORIGIN_POST.test(url.pathname))) {
      return toOrigin(req);
    }
    return toApp(req, env, url);
  },
};
