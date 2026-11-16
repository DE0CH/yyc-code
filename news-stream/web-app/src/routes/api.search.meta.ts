// GET /api/search/meta (search.meta): the media types and outlets to filter by
import { createFileRoute } from "@tanstack/react-router";
import { json } from "../server/access";
import { db } from "../server/db";
import { meta } from "../server/search/search";

export const Route = createFileRoute("/api/search/meta")({
  server: { handlers: { GET: async () => json(200, await meta(await db())) } },
});
