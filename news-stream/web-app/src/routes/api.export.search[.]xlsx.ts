// GET /api/export/search.xlsx (search.export): the search as Excel, the same columns, meta rows and file name
import { createFileRoute } from "@tanstack/react-router";
import { json } from "../server/access";
import { db } from "../server/db";
import { attachment } from "../server/xlsx";
import { errorJson, exportXlsx, qsFrom } from "../server/search/search";

export const Route = createFileRoute("/api/export/search.xlsx")({
  server: { handlers: { GET: async ({ request }) => {
    try {
      const [name, data] = await exportXlsx(await db(), qsFrom(new URL(request.url).searchParams));
      return new Response(data as BodyInit, { status: 200, headers: {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": attachment(name), "Cache-Control": "no-store" } });
    } catch (e) { const r = errorJson(e); return json(r.status, { error: r.error }); }
  } } },
});
