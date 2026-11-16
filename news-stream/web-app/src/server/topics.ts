// insights.py's topics: posts grouped by the same thread URL or ≥ threshold Jaccard similarity of title bigrams.
import { pubKey } from "./time";

/** insights._norm: no URLs, the part before " - " / " | ", lower case, letters and digits only (Python's \w less _) */
export function norm(t: string | null | undefined): string {
  let s = (t || "").replace(/https?:\/\/\S+/g, "");
  s = s.split(/\s[-|｜]\s/)[0];
  return s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
}

export function grams(t: string | null | undefined): Set<string> {
  const n = Array.from(norm(t)).slice(0, 80);
  const out = new Set<string>();
  for (let i = 0; i < n.length - 1; i++) out.add(n[i] + n[i + 1]);
  if (!out.size) out.add(n.join(""));
  return out;
}

const inter = (a: Set<string>, b: Set<string>) => { let n = 0; for (const x of a) if (b.has(x)) n++; return n; };
export const jaccard = (a: Set<string>, b: Set<string>) => { const i = inter(a, b); return i / Math.max(1, a.size + b.size - i); };

export interface TopicPost { id: number; url: string; title: string; published?: string | null; seen?: string | null;
  title_by_ai?: boolean; title_linked?: boolean; [k: string]: unknown }
export interface Topic<P extends TopicPost> { id: number; title: string; title_by_ai: boolean; title_linked: boolean; url: string;
  posts: P[]; first: string | null; last: string | null }

const stable = <T,>(xs: T[], key: (x: T) => string, reverse = false) =>
  xs.map((x, i) => [x, i] as const).sort((a, b) => {
    const ka = key(a[0]), kb = key(b[0]);
    const c = ka < kb ? -1 : ka > kb ? 1 : 0;
    return (reverse ? -c : c) || a[1] - b[1];
  }).map(p => p[0]);

export function topics<P extends TopicPost>(ps: P[], threshold = 0.5): Topic<P>[] {
  const order = stable(ps, pubKey);
  type T = { i: number; grams: Set<string>; thread: string; posts: P[]; title: string; url: string; title_by_ai: boolean; title_linked: boolean };
  const tops: T[] = [];
  const index = new Map<string, Set<number>>();
  for (const p of order) {
    const g = grams(p.title);
    const cand = new Map<number, number>();
    for (const x of g) for (const ti of index.get(x) || []) cand.set(ti, (cand.get(ti) || 0) + 1);
    // Counter.most_common(8): by count, ties in first-counted order
    const ranked = [...cand.entries()].map((e, k) => [e[0], e[1], k] as const).sort((a, b) => b[1] - a[1] || a[2] - b[2]).slice(0, 8);
    let best: number | null = null, bestS = 0;
    for (const [ti] of ranked) {
      const s = jaccard(g, tops[ti].grams);
      if (s > bestS) { best = ti; bestS = s; }
    }
    const thread = p.url.replace(/\/page\/\d+/g, "").replace(/[?#].*$/s, "");
    let t: T;
    if (best !== null && bestS >= threshold) t = tops[best];
    else {
      const same = tops.find(x => x.thread === thread);
      if (same) t = same;
      else {
        t = { i: tops.length, grams: g, thread, posts: [], title: p.title, url: p.url, title_by_ai: !!p.title_by_ai, title_linked: !!p.title_linked };
        tops.push(t);
        for (const x of g) { let s = index.get(x); if (!s) index.set(x, (s = new Set())); s.add(t.i); }
      }
    }
    t.posts.push(p);
  }
  const out = tops.map(t => {
    const posts = stable(t.posts, pubKey, true);
    const last = posts[0], first = posts[posts.length - 1];
    return { id: first.id, title: t.title, title_by_ai: t.title_by_ai, title_linked: t.title_linked, url: t.url, posts,
      first: first.published || first.seen || null, last: last.published || last.seen || null };
  });
  // sort by (len(posts), last or "") descending; Python's sort is stable
  return out.map((t, i) => [t, i] as const).sort((a, b) => {
    const d = b[0].posts.length - a[0].posts.length;
    if (d) return d;
    const la = a[0].last || "", lb = b[0].last || "";
    return la < lb ? 1 : la > lb ? -1 : a[1] - b[1];
  }).map(x => x[0]);
}
