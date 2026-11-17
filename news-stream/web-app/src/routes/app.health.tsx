// /app/health: YYC system health on a phone (Deyao, 2026-11-17; -HealthPage.tsx), the page the "YYC Systems" iPhone app
// (DE0CH/yyc-health-ios) shows in its WebView. server.ts answers a plain 404 to every identity but Deyao's before this
// route runs. As /app/live: /app/health is a shell (FrameShell) showing the page itself, /app/health?frame=1, in a frame,
// so a new deploy swaps in a frame of the new build without the WebView reloading.
import { createFileRoute, useSearch } from "@tanstack/react-router";
import { FrameShell } from "./-LivePage";
import { HealthPage } from "./-HealthPage";

export const Route = createFileRoute("/app/health")({
  validateSearch: (s: Record<string, unknown>): { frame?: 1 } => (s.frame === 1 || s.frame === "1" ? { frame: 1 as const } : {}),
  head: () => ({ meta: [{ title: "Systems｜YYC" }, { name: "robots", content: "noindex" }] }),
  component: HealthRoute,
});

function HealthRoute() {
  const { frame } = useSearch({ from: "/app/health" });
  return frame ? <HealthPage /> : <FrameShell src="/app/health?frame=1" title="YYC systems" />;
}
