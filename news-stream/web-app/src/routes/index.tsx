// 串流: /?p=<stream>&d=<YYYY-MM-DD>&q=<text>&sent=<a,b>&lab=<a,b>
import { createFileRoute } from "@tanstack/react-router";
import { feedQuery, profilesQuery, flagsQuery, scoresQuery } from "../queries";
import { isDay } from "../lib/time";
import { load, loadGated } from "../lib/load";
import { StreamPage } from "./-StreamPage";

const str = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : typeof v === "number" ? String(v) : undefined);
export interface StreamSearch { p?: string; d?: string; q?: string; sent?: string; lab?: string }

export const Route = createFileRoute("/")({
  validateSearch: (s: Record<string, unknown>): StreamSearch => ({
    p: str(s.p), d: isDay(s.d) ? s.d : undefined, q: str(s.q), sent: str(s.sent), lab: str(s.lab),
  }),
  loaderDeps: ({ search }) => ({ d: search.d }),
  // approved accounts: the day's scores and reasons come with the feed (in the server's HTML, and on a hover)
  loader: async ({ context, deps }) => {
    await Promise.all([load(context.queryClient, [feedQuery(deps.d ?? null), profilesQuery, flagsQuery]),
      loadGated(context.queryClient, [scoresQuery(deps.d ?? null)])]);
  },
  head: () => ({ meta: [{ title: "串流｜YYC 新聞監察" }] }),
  component: StreamPage,
});
