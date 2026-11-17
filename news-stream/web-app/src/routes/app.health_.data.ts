// /app/health/data: the system health page's read (-HealthPage.tsx): kv live:sys (monitor.py live_once, every 10 s) and
// Jarvis 4's last push (server/jarvis.ts). Deyao's Access identity only: gates.ts answers 404 to anyone else first, and
// this checks again.
import { createFileRoute } from "@tanstack/react-router";
import { accessUser, ADMIN_EMAIL } from "../server/access";
import { db } from "../server/db";
import { kvGet } from "../server/kv";
import { jarvisLive } from "../server/jarvis";
import type { LiveSys } from "../server/live";

const head = { "Cache-Control": "no-store", "X-Robots-Tag": "noindex" };

export const Route = createFileRoute("/app/health_/data")({
  server: { handlers: { GET: async ({ request }) => {
    if ((await accessUser(request)) !== ADMIN_EMAIL) return new Response("not found", { status: 404, headers: { "Content-Type": "text/plain", ...head } });
    const [sys, jarvis] = await Promise.all([kvGet<LiveSys | null>(await db(), "live:sys", null), jarvisLive()]);
    return new Response(JSON.stringify({ now: Date.now(), sys, jarvis }), { status: 200, headers: { "Content-Type": "application/json; charset=utf-8", ...head } });
  } } },
});
