// 串流: /?p=<stream>&d=<YYYY-MM-DD>&q=<text>&sent=<a,b>&lab=<a,b>
import { createFileRoute } from "@tanstack/react-router";
import { feedQuery, profilesQuery, flagsQuery } from "../queries";
import { isDay } from "../lib/time";
import { load } from "../lib/load";
import { StreamPage } from "./-StreamPage";

const str = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : typeof v === "number" ? String(v) : undefined);
export interface StreamSearch { p?: string; d?: string; q?: string; sent?: string; lab?: string }

export const Route = createFileRoute("/")({
  validateSearch: (s: Record<string, unknown>): StreamSearch => ({
    p: str(s.p), d: isDay(s.d) ? s.d : undefined, q: str(s.q), sent: str(s.sent), lab: str(s.lab),
  }),
  loaderDeps: ({ search }) => ({ d: search.d }),
  loader: ({ context, deps }) => load(context.queryClient, [feedQuery(deps.d ?? null), profilesQuery, flagsQuery]),
  head: () => ({ meta: [{ title: "串流｜YYC 新聞監察" }] }),
  component: StreamPage,
});
