// 數據分析: /dashboard?v=<summary|posts|hot|authors|sources|trend|themes|persona>&p=<profile>&from=&to=&sent=&label=&theme=&type=&site=&author=&q=
// &page=&top=&by=&bucket=&a=&tid=&post= (NAVIGATION.md "Drill-downs and Back"); no ?v= opens 摘要. The view's data is loaded on the server (SSR) and dehydrated into the page.
import { createFileRoute } from "@tanstack/react-router";
import { flagsQuery, profilesQuery } from "../queries";
import { metaQuery } from "../queries-dashboard";
import { load } from "../lib/load";
import { DashboardPage, effective, KEYS, viewQueries, type DashSearch } from "./-DashboardPage";

const str = (v: unknown) => (typeof v === "string" && v.trim() ? v : typeof v === "number" ? String(v) : undefined);

export const Route = createFileRoute("/dashboard")({
  validateSearch: (s: Record<string, unknown>): DashSearch => {
    const o: DashSearch = {};
    for (const k of KEYS) { const v = str(s[k]); if (v !== undefined) o[k] = v; }
    return o;
  },
  loaderDeps: ({ search }) => search,
  loader: async ({ context, deps }) => {
    const qc = context.queryClient;
    const [meta, flags] = await Promise.all([qc.ensureQueryData(metaQuery).catch(() => null), qc.ensureQueryData(flagsQuery).catch(() => ({})),
      qc.ensureQueryData(profilesQuery).catch(() => null)]);
    if (!meta || !meta.profiles.length) return;
    await load(qc, viewQueries(effective(deps, meta, null), !!(flags as Record<string, boolean>).ai));
  },
  head: () => ({ meta: [{ title: "數據分析｜YYC 新聞監察" }] }),
  component: DashboardPage,
});
