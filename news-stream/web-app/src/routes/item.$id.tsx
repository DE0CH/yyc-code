// The item page: /item/<id>. The post renders from the server; 同一事件的其他報道 (coverage, the slow part) follows.
import { createFileRoute } from "@tanstack/react-router";
import { flagsQuery, itemAIQuery, itemCoverageQuery, itemQuery, itemTextQuery, itemVideoQuery, profilesQuery } from "../queries";
import { load, loadGated } from "../lib/load";
import { ItemPage } from "./-ItemPage";

export const Route = createFileRoute("/item/$id")({
  params: { parse: (p: { id: string }) => ({ id: Number(p.id) }), stringify: (p: { id: number }) => ({ id: String(p.id) }) },
  loader: async ({ context, params }) => {
    if (!Number.isFinite(params.id)) return { title: null };
    const qc = context.queryClient;
    // coverage: streamed, never awaited; started once the post is known to exist (a missing post's failed prefetch
    // would reach the browser as an unhandled error)
    const post = qc.ensureQueryData(itemQuery(params.id));
    post.then(d => { if (d) qc.prefetchQuery(itemCoverageQuery(params.id)).catch(() => {}); }, () => {});
    // approved accounts: the full text and the scores are read with the post, not after it (in the server's HTML, and
    // on a hover); the saved video's links once the post says it has one
    const video = post.then(d => (d?.video?.status === "ok" ? loadGated(qc, [itemVideoQuery(params.id)]) : undefined), () => undefined);
    await Promise.all([load(qc, [itemQuery(params.id), profilesQuery, flagsQuery]),
      loadGated(qc, [itemTextQuery(params.id), itemAIQuery(params.id)]),
      typeof window === "undefined" ? video : undefined]);
    return { title: qc.getQueryData(itemQuery(params.id).queryKey)?.title ?? null };
  },
  head: ({ loaderData }) => ({ meta: [{ title: loaderData?.title ? `${loaderData.title}｜YYC 新聞監察` : "帖子｜YYC 新聞監察" }] }),
  component: ItemPage,
});
