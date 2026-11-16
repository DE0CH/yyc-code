// /version: the build this Worker runs (lib/update.ts compares it with the page's own)
import { createFileRoute } from "@tanstack/react-router";
import { BUILD_ID } from "../lib/build";

export const Route = createFileRoute("/version")({
  server: { handlers: { GET: async () => new Response(BUILD_ID, { headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" } }) } },
});
