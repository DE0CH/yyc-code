// /app/proof/data?sid=a,b|catalogue=1|stream=ID&above=N (approved accounts): the proof view's list (server/proof.ts list)
import { createFileRoute } from "@tanstack/react-router";
import { approved, json } from "../server/access";
import { db } from "../server/db";
import { list, parseSids } from "../server/proof";

export const Route = createFileRoute("/app/proof_/data")({
  server: { handlers: { GET: approved(async (_req, _email, url) =>
    json(200, await list(await db(), parseSids(url.searchParams.get("sid")), url.searchParams.get("catalogue") === "1",
      { id: url.searchParams.get("stream"), above: url.searchParams.get("above") }))) } },
});
