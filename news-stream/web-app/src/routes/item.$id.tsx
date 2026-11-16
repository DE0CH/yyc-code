// The item page: /item/<id>. The post renders from the server; 同一事件的其他報道 (coverage, the slow part) follows.
import { createFileRoute } from "@tanstack/react-router";
import { flagsQuery, itemCoverageQuery, itemQuery, profilesQuery } from "../queries";
import { load } from "../lib/load";
import { ItemPage } from "./-ItemPage";

export const Route = createFileRoute("/item/$id")({
  params: { parse: (p: { id: string }) => ({ id: Number(p.id) }), stringify: (p: { id: number }) => ({ id: String(p.id) }) },
  loader: async ({ context, params }) => {
    if (!Number.isFinite(params.id)) return { title: null };
    // coverage: streamed, never awaited; started once the post is known to exist (a missing post's failed prefetch
    // would reach the browser as an unhandled error)
    context.queryClient.ensureQueryData(itemQuery(params.id))
      .then(d => { if (d) context.queryClient.prefetchQuery(itemCoverageQuery(params.id)).catch(() => {}); }, () => {});
    await load(context.queryClient, [itemQuery(params.id), profilesQuery, flagsQuery]);
    return { title: context.queryClient.getQueryData(itemQuery(params.id).queryKey)?.title ?? null };
  },
  head: ({ loaderData }) => ({ meta: [{ title: loaderData?.title ? `${loaderData.title}｜YYC 新聞監察` : "帖子｜YYC 新聞監察" }] }),
  component: ItemPage,
});
