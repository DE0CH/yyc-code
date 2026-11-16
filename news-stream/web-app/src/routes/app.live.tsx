// /app/live[?half=left|right]: Deyao's live dashboard (server/live.ts, -LivePage.tsx). server.ts answers a plain 404 to
// every identity but his before this route runs. The page reads its data in the browser (/app/live/data), every few
// seconds; the server renders only the empty frame.
import { createFileRoute } from "@tanstack/react-router";
import { LivePage } from "./-LivePage";

export interface LiveSearch { half?: "left" | "right" }

export const Route = createFileRoute("/app/live")({
  validateSearch: (s: Record<string, unknown>): LiveSearch => (s.half === "left" || s.half === "right" ? { half: s.half } : {}),
  head: () => ({ meta: [{ title: "Live｜YYC" }, { name: "robots", content: "noindex" }] }),
  component: LivePage,
});
