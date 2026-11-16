// /post/<id>: a post's cached copy is the item page's text section: 301 to /item/<id>#text, so links in sent
// reports and AI answers keep working.
import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/post/$id")({
  server: {
    handlers: {
      GET: async ({ params }) => {
        if (!/^\d+$/.test(params.id)) return new Response("not found", { status: 404, headers: { "Content-Type": "text/plain" } });
        return new Response("", { status: 301, headers: { Location: `/item/${params.id}#text`, "Content-Type": "text/plain" } });
      },
    },
  },
});
