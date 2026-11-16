// /app/monitor: Deyao's monitor. server.ts answers a plain 404 to every identity but his
// before this route runs; the response carries X-Robots-Tag: noindex.
import { createFileRoute } from "@tanstack/react-router";
import { load } from "../lib/load";
import { monitorQuery } from "../monitorData";
import { MonitorPage } from "./-MonitorPage";

export const Route = createFileRoute("/app/monitor")({
  loader: ({ context }) => load(context.queryClient, [monitorQuery]),
  head: () => ({ meta: [{ title: "Monitor｜YYC" }, { name: "robots", content: "noindex" }] }),
  component: MonitorPage,
});
