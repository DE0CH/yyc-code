// /app/monitor/data: the snapshots monitor.py published into Postgres (kv monitor:data), each panel with its age now.
// Deyao's Access identity only: server.ts answers 404 to anyone else first, and this checks again.
import { createFileRoute } from "@tanstack/react-router";
import { accessUser, ADMIN_EMAIL } from "../server/access";
import { db } from "../server/db";
import { published } from "../server/monitor";

const head = { "Cache-Control": "no-store", "X-Robots-Tag": "noindex" };

export const Route = createFileRoute("/app/monitor_/data")({
  server: { handlers: { GET: async ({ request }) => {
    if ((await accessUser(request)) !== ADMIN_EMAIL) return new Response("not found", { status: 404, headers: { "Content-Type": "text/plain", ...head } });
    return new Response(JSON.stringify(await published(await db())), { status: 200, headers: { "Content-Type": "application/json; charset=utf-8", ...head } });
  } } },
});
