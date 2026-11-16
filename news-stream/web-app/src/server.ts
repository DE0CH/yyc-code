// The Worker: TanStack Start's handler for pages, server functions and /app routes, plus live updates.
//   GET /live?t=<topic>&t=…   (WebSocket) an open page's connection, held by the LiveHub Durable Object
//   POST /live/notify          the cluster: {"topics": ["feed:2026-10-15", "item:123", …]} changed (X-Live-Secret)
//   POST /live/jarvis          Jarvis 4's router: Deyao's sessions for the live dashboard's Jarvis 4 panel
//                              (X-Jarvis-Secret = JARVIS_LIVE_SECRET; Deyao, 2026-11-12). The hub keeps the last one;
//                              /app/live/data reads it back (server/jarvis.ts). Nothing of it reaches Postgres.
// Telling open pages of a deploy (lib/update.ts): the first request each new Worker instance serves sends its build to
// the hub (POST https://hub/build, internal), and the hub tells every socket {"build": <id>, "announce": true} the first
// time it hears an id. Cloudflare releases a new version to the Worker at once and to the Durable Object eventually
// (measured 2026-10-20: minutes), so this works whichever code the hub runs, once the hub has this handler. The hub also
// says its own build when a socket connects and in reply to every ping. Pages take any id only as a hint and ask
// /version (the Worker), so a hub on older code never makes a page think the site went back.
// /assets/* reaches this code only for a file the deploy does not have (static files are served before the Worker):
// a plain 404, never a page.
// Requests reach this Worker through Worker yyc-edge on newswatch.yyc-internal.com, which adds X-YYC-Edge (EDGE_SECRET);
// anything without it is refused, so the workers.dev address is not a way around Cloudflare Access (/live carries only
// topic names, never data, and its notify side needs X-Live-Secret).
import handler, { createServerEntry } from "@tanstack/react-start/server-entry";
import { DurableObject } from "cloudflare:workers";
import { perRequest } from "./server/db";
import { finish, gate, unslash } from "./server/gates";
import { BUILD_ID } from "./lib/build";
import { cleanJarvis } from "./server/jarvis";
import { waitUntil } from "cloudflare:workers";

interface Env { LIVE: DurableObjectNamespace<LiveHub>; EDGE_SECRET?: string; LIVE_SECRET?: string; JARVIS_LIVE_SECRET?: string }

const TOPIC = /^(feed:\d{4}-\d{2}-\d{2}|feed:today|item:\d+|scores:\d{4}-\d{2}-\d{2})$/;

export class LiveHub extends DurableObject<Env> {
  async fetch(req: Request): Promise<Response> {
    const url = new URL(req.url);
    if (url.pathname === "/notify") {
      const { topics = [] } = (await req.json()) as { topics?: string[] };
      let sent = 0;
      for (const t of new Set(topics.filter(t => TOPIC.test(t)))) {
        for (const ws of this.ctx.getWebSockets(t)) {
          try { ws.send(JSON.stringify({ changed: t })); sent++; } catch { /* closing */ }
        }
      }
      return Response.json({ sent });
    }
    if (url.pathname === "/jarvis") {
      if (req.method === "POST") {
        const doc = cleanJarvis(await req.json().catch(() => null), Date.now());
        if (!doc) return Response.json({ error: "bad body" }, { status: 400 });
        await this.ctx.storage.put("jarvis", doc);
        return Response.json({ ok: true, n: doc.sessions.length });
      }
      return Response.json((await this.ctx.storage.get("jarvis")) ?? null);
    }
    if (url.pathname === "/build" && req.method === "POST") {
      const id = (await req.text()).trim();
      if (!/^[\w-]{1,40}$/.test(id)) return Response.json({ sent: 0 });
      const seen = (await this.ctx.storage.get<string[]>("builds")) || [];
      if (seen.includes(id)) return Response.json({ sent: 0 });  // an instance of an older version starting: no news
      await this.ctx.storage.put("builds", [...seen, id].slice(-50));
      let sent = 0;
      for (const ws of this.ctx.getWebSockets()) {
        try { ws.send(JSON.stringify({ build: id, announce: true })); sent++; } catch { /* closing */ }
      }
      return Response.json({ sent });
    }
    const topics = [...new Set(url.searchParams.getAll("t").filter(t => TOPIC.test(t)))].slice(0, 8);
    const pair = new WebSocketPair();
    this.ctx.acceptWebSocket(pair[1], topics);  // hibernation: the object sleeps while sockets are idle
    pair[1].send(JSON.stringify({ build: BUILD_ID }));
    return new Response(null, { status: 101, webSocket: pair[0] });
  }
  async webSocketMessage(ws: WebSocket, msg: string | ArrayBuffer) {
    if (msg === "ping") ws.send(JSON.stringify({ build: BUILD_ID }));
  }
  async webSocketClose(ws: WebSocket, code: number) { try { ws.close(code, "bye"); } catch { /* closed */ } }
}

const hub = (env: Env) => env.LIVE.get(env.LIVE.idFromName("hub"));
let announced = false;  // this Worker instance has told the hub its build

export default createServerEntry({
  async fetch(request, opts) {
    const { env } = await import("cloudflare:workers") as unknown as { env: Env };
    const url = new URL(request.url);
    if (!announced) {
      announced = true;
      waitUntil(hub(env).fetch("https://hub/build", { method: "POST", body: BUILD_ID }).catch(() => undefined));
    }
    if (url.pathname.startsWith("/assets/")) {
      return new Response("not found", { status: 404, headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" } });
    }
    if (env.EDGE_SECRET && request.headers.get("X-YYC-Edge") !== env.EDGE_SECRET && !url.pathname.startsWith("/live")) {
      return new Response("not found", { status: 404 });
    }
    if (url.pathname === "/live" && request.headers.get("Upgrade") === "websocket") {
      return hub(env).fetch(new Request("https://hub/live" + url.search, request));
    }
    if (url.pathname === "/live/jarvis" && request.method === "POST") {
      if (!env.JARVIS_LIVE_SECRET || request.headers.get("X-Jarvis-Secret") !== env.JARVIS_LIVE_SECRET) return new Response("forbidden", { status: 403 });
      return hub(env).fetch(new Request("https://hub/jarvis", { method: "POST", body: await request.text() }));
    }
    if (url.pathname === "/live/notify" && request.method === "POST") {
      if (!env.LIVE_SECRET || request.headers.get("X-Live-Secret") !== env.LIVE_SECRET) return new Response("forbidden", { status: 403 });
      return hub(env).fetch(new Request("https://hub/notify", { method: "POST", body: await request.text() }));
    }
    return perRequest(async () => (await gate(request)) ?? finish(request, await handler.fetch(unslash(request), opts)));
  },
});
