// The stream picker's state (NAVIGATION.md "The stream picker"): ?p= in the URL is the single source of truth; the
// remembered stream (cookie `yyc-profile`, lib/remembered.ts) is only the default when the URL has none. The first
// render reads the remembered stream from the root loader (the server's reading), so server and browser agree. The
// root route keeps ?p= on every link (retainSearchParams), so the stream stays selected between sections.
import { useEffect, useMemo } from "react";
import { useLoaderData, useNavigate, useSearch } from "@tanstack/react-router";
import { useProfiles } from "../queries";
import { remember } from "../lib/remembered";
import type { Profile } from "../api";

// "all": the bar's links; "side": 串流, whose desktop side list is the picker; "page": the page writes its own default
// into ?p= (the item page: the stream that kept the post); "none": no picker
export type Pick = "all" | "side" | "page" | "none";

/** the remembered stream as the server read it for this page (the root loader; re-read on every navigation) */
export function useRemembered(): string | null {
  return useLoaderData({ from: "__root__" }).remembered;
}

export function useProfile() {
  const { p } = useSearch({ strict: false }) as { p?: string };
  const navigate = useNavigate();
  const profilesQ = useProfiles();
  const all = profilesQ.data;
  const remembered = useRemembered();

  useEffect(() => { if (p) remember(p); }, [p]);
  const stored = p || remembered;

  const { list, effective } = useMemo(() => {
    const list: Profile[] = all || [];  // the monitoring streams: every profile is one
    const effective = stored && list.some(x => x.id === stored) ? stored : list[0]?.id ?? null;
    return { list, effective };
  }, [all, stored]);

  // a remembered profile that no longer exists is replaced by the first stream
  useEffect(() => {
    if (!all || !effective || effective === stored) return;
    remember(effective);
  }, [all, effective, stored]);

  /** show stream `id`; `drop`: the page's drill-down params that belong to one stream (data-pick-drop) */
  function set(id: string, push = false, drop: readonly string[] = []) {
    remember(id);
    navigate({ to: ".", search: (prev: Record<string, unknown>) => {
      const next: Record<string, unknown> = { ...prev, p: id };
      for (const k of drop) delete next[k];
      return next;
    }, replace: !push });
  }

  return { list, effective, stored, fromURL: !!p, set, loading: profilesQ.isPending, error: profilesQ.isError };
}

/** the item page's stream when the link carries none (?p=): the remembered one when it kept or matched this post, else
 *  the first stream that kept it, else the first stream it matched, else the remembered or first stream. A pure
 *  function of what the server also sees, so the server renders it and the page only writes it into the URL. */
export function itemDefault(D: { streams: { stream: string }[]; matched: string[] }, profiles: Profile[], remembered: string | null): string | null {
  const ids = profiles.map(p => p.id);
  const cand = D.streams.map(s => s.stream).concat(D.matched).filter(x => ids.includes(x));
  return remembered && cand.includes(remembered) ? remembered : cand[0] || (remembered && ids.includes(remembered) ? remembered : ids[0] ?? null);
}
