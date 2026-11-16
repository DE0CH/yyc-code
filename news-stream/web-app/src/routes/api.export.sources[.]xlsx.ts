// GET /api/export/sources.xlsx: 數據分析's Excel export (server/dashboard-export.ts), with its Content-Disposition
import { createFileRoute } from "@tanstack/react-router";
import { db } from "../server/db";
import { exportResponse } from "../server/dashboard-export";

export const Route = createFileRoute("/api/export/sources.xlsx")({
  server: { handlers: { GET: async ({ request }) => {
    const q: Record<string, string> = {};
    for (const [k, v] of new URL(request.url).searchParams) if (v !== "" && !(k in q)) q[k] = v;
    return exportResponse(await db(), "sources", q);
  } } },
});
