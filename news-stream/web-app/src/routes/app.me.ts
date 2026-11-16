// /app/me (behind Access): the approved account's email; pages ask it to know whether to show approved-only parts
import { createFileRoute } from "@tanstack/react-router";
import { accessUser, json } from "../server/access";

export const Route = createFileRoute("/app/me")({
  server: { handlers: { GET: async ({ request }) => json(200, { email: await accessUser(request) }) } },
});
