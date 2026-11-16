// /item?url=<publisher url> (串流 cards, links in sent messages): a 302 to the post's /item/<id>, keeping ?p= (the
// browser keeps a #hash across it, e.g. #text).
import { createFileRoute, redirect } from "@tanstack/react-router";
import { itemByUrlQuery, keys } from "../queries";
import { ItemByUrl } from "./-ItemPage";

const str = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : undefined);

export const Route = createFileRoute("/item/")({
  validateSearch: (s: Record<string, unknown>): { p?: string; url?: string } => ({ p: str(s.p), url: str(s.url) }),
  loaderDeps: ({ search }) => ({ url: search.url, p: search.p }),
  loader: async ({ context, deps }) => {
    if (!deps.url) return;
    const d = await context.queryClient.ensureQueryData(itemByUrlQuery(deps.url)).catch(() => null);
    // the post is the same data as /item/<id>'s: put it there, so the page after the redirect needs no second request
    if (d) context.queryClient.setQueryData(keys.item(d.id), d);
    if (d) throw redirect({ to: "/item/$id", params: { id: d.id }, search: { p: deps.p }, statusCode: 302, replace: true });
  },
  component: ItemByUrl,
});
