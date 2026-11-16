// AI 觀察's edge. The pages (../static) are served by Cloudflare's assets from the location nearest the reader; this
// Worker sees only /api/*. It forwards every request to the site's origin (the web pods in fsn1, through the tunnel; a
// subrequest to the Worker's own hostname goes to the origin, carrying the reader's Access headers). For the three reads
// a page opens with, it also keeps this location's last answer (the Cache API, per location) and gives it at once to a
// request without X-Fresh, marked X-Edge-Copy (its age in seconds); the page then asks again with X-Fresh: 1, which
// always goes to the origin and replaces the copy (static/app.js `load`). Everyone behind Access sees the same data, so
// one copy serves all; /api/status's `me` (the reader's address) is left out of it.
const COPIED = /^\/api\/(cases|cases\/\d+|status)$/;
const WEEK = 7 * 86400;

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    // `wrangler dev`: ORIGIN=http://localhost:8080 points at a local `python -m aiwatch.web`
    const upstream = env.ORIGIN ? new Request(env.ORIGIN + url.pathname + url.search, req) : req;
    if (req.method !== "GET" || !COPIED.test(url.pathname)) return fetch(upstream);
    const key = new Request(`${url.origin}/__edge-copy${url.pathname}`);
    const cache = caches.default;
    if (req.headers.get("X-Fresh") !== "1") {
      const hit = await cache.match(key);
      if (hit) {
        const age = Math.round((Date.now() - +hit.headers.get("X-Copied-At")) / 1000);
        return new Response(hit.body, { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Edge-Copy": String(age) } });
      }
    }
    const r = await fetch(upstream);
    if (!r.ok) return r;
    const body = await r.text();
    let copy = body;
    if (url.pathname === "/api/status") { const s = JSON.parse(body); delete s.me; copy = JSON.stringify(s); }
    await cache.put(key, new Response(copy, { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": `max-age=${WEEK}`, "X-Copied-At": String(Date.now()) } }));
    return new Response(body, { status: r.status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
  },
};
