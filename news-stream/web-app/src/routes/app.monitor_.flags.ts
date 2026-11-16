// /app/monitor/flags: the open proof flags (proof.py; server/proof.ts openFlags) for Deyao's monitor. His Access
// identity only: server.ts answers 404 to anyone else first, and this checks again.
import { createFileRoute } from "@tanstack/react-router";
import { accessUser, ADMIN_EMAIL } from "../server/access";
import { db } from "../server/db";
import { openFlags } from "../server/proof";

const head = { "Cache-Control": "no-store", "X-Robots-Tag": "noindex" };

export const Route = createFileRoute("/app/monitor_/flags")({
  server: { handlers: { GET: async ({ request }) => {
    if ((await accessUser(request)) !== ADMIN_EMAIL) return new Response("not found", { status: 404, headers: { "Content-Type": "text/plain", ...head } });
    return new Response(JSON.stringify(await openFlags(await db())), { status: 200, headers: { "Content-Type": "application/json; charset=utf-8", ...head } });
  } } },
});
