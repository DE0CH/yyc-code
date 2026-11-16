// /app/live/data?c=<panel:id,…>[&cfg=1][&rates=1]: the live dashboard's read (server/live.ts). Deyao's Access identity only:
// gates.ts answers 404 to anyone else first, and this checks again.
import { createFileRoute } from "@tanstack/react-router";
import { accessUser, ADMIN_EMAIL } from "../server/access";
import { db } from "../server/db";
import { liveData, parseCursors } from "../server/live";
import { jarvisLive } from "../server/jarvis";

const head = { "Cache-Control": "no-store", "X-Robots-Tag": "noindex" };

export const Route = createFileRoute("/app/live_/data")({
  server: { handlers: { GET: async ({ request }) => {
    if ((await accessUser(request)) !== ADMIN_EMAIL) return new Response("not found", { status: 404, headers: { "Content-Type": "text/plain", ...head } });
    const u = new URL(request.url);
    const [data, jarvis] = await Promise.all([liveData(await db(), parseCursors(u.searchParams.get("c")),
      { config: u.searchParams.get("cfg") === "1", rates: u.searchParams.get("rates") === "1" }), jarvisLive()]);
    const body = { ...data, jarvis };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json; charset=utf-8", ...head } });
  } } },
});
