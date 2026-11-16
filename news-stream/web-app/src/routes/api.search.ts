// GET /api/search (search.run): the search's JSON, for clients and links
import { createFileRoute } from "@tanstack/react-router";
import { json } from "../server/access";
import { db } from "../server/db";
import { errorJson, qsFrom, run } from "../server/search/search";

export const Route = createFileRoute("/api/search")({
  server: { handlers: { GET: async ({ request }) => {
    try { return json(200, await run(await db(), qsFrom(new URL(request.url).searchParams))); }
    catch (e) { const r = errorJson(e); return json(r.status, { error: r.error }); }
  } } },
});
