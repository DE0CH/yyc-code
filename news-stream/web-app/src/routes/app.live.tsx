// /app/live[?half=left|right]: Deyao's live dashboard (server/live.ts, -LivePage.tsx). server.ts answers a plain 404 to
// every identity but his before this route runs. /app/live is a shell (LiveShell) that holds full screen and the wake
// lock and shows the dashboard itself, /app/live?frame=1[&half=…], in a frame; a new deploy swaps the frame for one of
// the new build, so the page is updated without the browser reloading it (Deyao, 2026-11-17: "a pushed update can
// update the page (for example, an embedded frame) without causing a refresh on the browser (which makes the tab exit
// full screen)"). The dashboard reads its data in the browser (/app/live/data), every few seconds; the server renders
// only the empty frame.
import { createFileRoute, useSearch } from "@tanstack/react-router";
import { LivePage, LiveShell } from "./-LivePage";

export interface LiveSearch { half?: "left" | "right"; frame?: 1 }

export const Route = createFileRoute("/app/live")({
  validateSearch: (s: Record<string, unknown>): LiveSearch => ({
    ...(s.half === "left" || s.half === "right" ? { half: s.half } : {}), ...(s.frame === 1 || s.frame === "1" ? { frame: 1 as const } : {}) }),
  head: () => ({ meta: [{ title: "Live｜YYC" }, { name: "robots", content: "noindex" }] }),
  component: LiveRoute,
});

function LiveRoute() {
  const { frame } = useSearch({ from: "/app/live" });
  return frame ? <LivePage /> : <LiveShell />;
}
