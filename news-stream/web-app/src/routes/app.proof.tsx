// /app/proof?sid=a,b,c (one or more sources, posts.sid) or ?catalogue=1 (every sites.json source in turn) or
// ?stream=ID&above=N (every post whose score for that stream, at either stage, is over N; default 10); &at=<post
// id> = the entry shown. Approved accounts (Cloudflare Access on /app, the token checked again by the data reads).
// The sources page links each title's card here as /app/proof?sid=<its sids>: keep that form.
import { createFileRoute } from "@tanstack/react-router";
import { load } from "../lib/load";
import { listQS, pickIndex, inView, proofEntryQuery, proofListQuery } from "../proofData";
import { ProofPage } from "./-ProofPage";

export interface ProofSearch { sid?: string; catalogue?: string; stream?: string; above?: string; at?: string }
const str = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : typeof v === "number" ? String(v) : undefined);

export const Route = createFileRoute("/app/proof")({
  validateSearch: (s: Record<string, unknown>): ProofSearch =>
    Object.fromEntries((["sid", "catalogue", "stream", "above", "at"] as const).map(k => [k, str(s[k])]).filter(([, v]) => v !== undefined)),
  loaderDeps: ({ search }) => ({ qs: listQS(search), at: Number(search.at) || null }),
  loader: async ({ context, deps }) => {
    await load(context.queryClient, [proofListQuery(deps.qs)]);
    // the server renders the entry shown too (proofData.pickIndex)
    if (typeof window === "undefined") {
      const L = context.queryClient.getQueryData(proofListQuery(deps.qs).queryKey);
      const i = L ? pickIndex(L, "stream", deps.at) : -1;  // the default view (a viewer's own choice is read after hydration)
      if (L && i >= 0) await load(context.queryClient, [proofEntryQuery(inView(L, "stream")[i].id)]);
    }
  },
  head: () => ({ meta: [{ title: "Proof｜YYC 新聞監察" }, { name: "robots", content: "noindex" }] }),
  component: ProofPage,
});
