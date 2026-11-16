// /app/item/video?id= (approved accounts): the post's saved TikTok video as presigned links to YYC's bucket (1 hour,
// made per request; server/video.ts)
import { createFileRoute } from "@tanstack/react-router";
import { approved, json } from "../server/access";
import { db } from "../server/db";
import { s3Config, videoLinks } from "../server/video";

export const Route = createFileRoute("/app/item/video")({
  server: { handlers: { GET: approved(async (_req, _email, url) => {
    const id = Number(url.searchParams.get("id") || "0");
    if (!Number.isFinite(id) || id <= 0) return json(404, { error: "no such post" });
    const { env } = await import("cloudflare:workers");
    const v = await videoLinks(await db(), id, s3Config(env as unknown as Record<string, unknown>));
    return v ? json(200, v) : json(404, { error: "no saved video" });
  }) } },
});
