// /api/reports (public): the stored report files of the newest 60 days. server.ts answers
// 404 first when the reports flag is off for this site.
import { createFileRoute } from "@tanstack/react-router";
import { db } from "../server/db";
import { listing } from "../server/reports";

export const Route = createFileRoute("/api/reports")({
  server: { handlers: { GET: async () => new Response(JSON.stringify({ reports: await listing(await db()) }), {
    status: 200, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } }) } },
});
