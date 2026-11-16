// /app/item/ai?id= (approved accounts): the stream scorer's score, reason and threshold per stream
import { createFileRoute } from "@tanstack/react-router";
import { approved, json } from "../server/access";
import { db } from "../server/db";
import { NotFound, aiScores } from "../server/item";

export const Route = createFileRoute("/app/item/ai")({
  server: { handlers: { GET: approved(async (_req, _email, url) => {
    const id = Number(url.searchParams.get("id") || "0");
    try { return json(200, await aiScores(await db(), id)); }
    catch (e) { if (e instanceof NotFound || !Number.isFinite(id)) return json(404, { error: "no such post" }); throw e; }
  }) } },
});
