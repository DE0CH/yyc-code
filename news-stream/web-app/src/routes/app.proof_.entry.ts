// /app/proof/entry?id= (approved accounts): one entry of the proof view, the post as the pipeline holds it
import { createFileRoute } from "@tanstack/react-router";
import { approved, json } from "../server/access";
import { db } from "../server/db";
import { NotFound } from "../server/item";
import { entry } from "../server/proof";

export const Route = createFileRoute("/app/proof_/entry")({
  server: { handlers: { GET: approved(async (_req, _email, url) => {
    const id = Number(url.searchParams.get("id") || "0");
    if (!Number.isInteger(id) || id <= 0) return json(404, { error: "no such post" });
    try { return json(200, await entry(await db(), id)); }
    catch (e) { if (e instanceof NotFound) return json(404, { error: "no such post" }); throw e; }
  }) } },
});
