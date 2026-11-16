// /reports/<day>/<profile>/<file>: one report file reports.py built (format1.html, report.pdf, posts.xlsx …), from
// Postgres (report_files), at the URL the sent emails link to, with the file type's Content-Type. server.ts
// answers 404 first when the reports flag is off for this site.
import { createFileRoute } from "@tanstack/react-router";
import { db } from "../server/db";
import { FILE_URL, stored } from "../server/reports";

const notFound = () => new Response("not found", { status: 404, headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" } });

export const Route = createFileRoute("/reports/$day/$profile/$file")({
  server: { handlers: { GET: async ({ request }) => {
    const m = FILE_URL.exec(new URL(request.url).pathname);
    if (!m) return notFound();
    const hit = await stored(await db(), m[1], m[2], m[3]);
    if (!hit) return notFound();
    return new Response(hit.body as Uint8Array<ArrayBuffer>, { status: 200, headers: { "Content-Type": hit.ctype, "Cache-Control": "no-store" } });
  } } },
});
