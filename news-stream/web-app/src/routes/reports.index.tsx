// 報告: /reports/?p=<stream>&all=1: each profile's daily report files, by day, newest first.
// The picker's stream chooses whose reports are listed; 全部設定檔 (?all=1) lists every profile's.
import { createFileRoute } from "@tanstack/react-router";
import { flagsQuery, profilesQuery, reportsQuery } from "../queries";
import { load } from "../lib/load";
import { ReportsPage } from "./-ReportsPage";

export interface ReportsSearch { p?: string; all?: string }
const str = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : typeof v === "number" ? String(v) : undefined);

export const Route = createFileRoute("/reports/")({
  validateSearch: (s: Record<string, unknown>): ReportsSearch => ({ p: str(s.p), all: str(s.all) }),
  loader: ({ context }) => load(context.queryClient, [reportsQuery, profilesQuery, flagsQuery]),
  head: () => ({ meta: [{ title: "報告｜YYC 新聞監察" }] }),
  component: ReportsPage,
});
