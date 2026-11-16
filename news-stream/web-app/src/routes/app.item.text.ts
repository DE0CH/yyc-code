// /app/item/text?id= (approved accounts): the full stored text, comments, transcripts and picture text
import { createFileRoute } from "@tanstack/react-router";
import { approved, json } from "../server/access";
import { db } from "../server/db";
import { NotFound, text } from "../server/item";

export const Route = createFileRoute("/app/item/text")({
  server: { handlers: { GET: approved(async (_req, _email, url) => {
    const id = Number(url.searchParams.get("id") || "0");
    try { return json(200, await text(await db(), id)); }
    catch (e) { if (e instanceof NotFound || !Number.isFinite(id)) return json(404, { error: "no such post" }); throw e; }
  }) } },
});
