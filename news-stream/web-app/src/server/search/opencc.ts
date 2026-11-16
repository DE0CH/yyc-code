// OpenCC (the C++ library, 1.4.x) as norm() and variants() in news-stream/searchtext.py run it: the config's
// normalization chain, then max-match segmentation, then each conversion in the chain over each segment, with OpenCC's
// own dictionaries dumped by the Python side (kv search:opencc, searchtext.publish_edge) so both run the same data.
// Ported from OpenCC's src/ (SingleStageConverter, ConfigBasedConverter, MaxMatchSegmentation, Conversion, DictGroup,
// PrefixMatch, UTF8Util's ideographic description sequences). Text is handled as code points, as OpenCC's UTF-8 is.

type Match = { len: number; value: string } | null;
interface Matcher { match(cps: string[], at: number): Match }

class Leaf implements Matcher {
  map = new Map<string, string>();
  max = 0;
  constructor(text: string) {
    for (const line of text.split("\n")) {
      if (!line) continue;
      const tab = line.indexOf("\t");
      if (tab < 0) continue;
      const key = line.slice(0, tab), value = line.slice(tab + 1).split(" ")[0];
      if (!key || this.map.has(key)) continue;  // a repeated key: the first entry stays (LeafMatcher::AddEntry)
      this.map.set(key, value);
      this.max = Math.max(this.max, Array.from(key).length);
    }
  }
  /** the longest key that is a prefix of cps[at…] */
  match(cps: string[], at: number): Match {
    for (let n = Math.min(this.max, cps.length - at); n > 0; n--) {
      const v = this.map.get(cps.slice(at, at + n).join(""));
      if (v !== undefined) return { len: n, value: v };
    }
    return null;
  }
}

class Group implements Matcher {
  constructor(private children: Matcher[], private union: boolean) {}
  match(cps: string[], at: number): Match {
    let best: Match = null;
    for (const c of this.children) {
      const m = c.match(cps, at);
      if (!m) continue;
      if (!this.union) return m;  // short_circuit: the first dictionary with any match
      if (!best || m.len > best.len) best = m;  // union: the longest, the earlier dictionary on a tie
    }
    return best;
  }
}

// ---- ideographic description sequences (UTF8Util): an unmatched IDS is kept whole ----
function arity(cp: number): number {
  if (cp === 0x2ff2 || cp === 0x2ff3) return 3;
  if (cp === 0x2ffe || cp === 0x2fff) return 1;
  if (cp >= 0x2ff0 && cp <= 0x2ffd) return 2;
  return 0;
}
type IDS = { status: "complete" | "incomplete" | "invalid"; consumed: number };
function consumeIds(cps: string[], at: number, depthLeft: number, count: { n: number }): IDS {
  if (at >= cps.length) return { status: "incomplete", consumed: 0 };
  if (depthLeft === 0 || count.n >= 64) return { status: "invalid", consumed: 0 };
  count.n++;
  const a = arity(cps[at].codePointAt(0)!);
  if (!a) return { status: "complete", consumed: 1 };
  let off = 1;
  for (let i = 0; i < a; i++) {
    if (at + off >= cps.length) return { status: "incomplete", consumed: 0 };
    const r = consumeIds(cps, at + off, depthLeft - 1, count);
    if (r.status !== "complete") return r;
    off += r.consumed;
  }
  return { status: "complete", consumed: off };
}
function idsLength(cps: string[], at: number): number {
  if (!arity(cps[at].codePointAt(0)!)) return 0;
  const r = consumeIds(cps, at, 16, { n: 0 });
  return r.status === "complete" ? r.consumed : 0;
}

function convertWith(m: Matcher, s: string): string {
  const cps = Array.from(s);
  let out = "";
  for (let i = 0; i < cps.length;) {
    const hit = m.match(cps, i);
    if (hit) { out += hit.value; i += hit.len; continue; }
    const n = idsLength(cps, i) || 1;
    out += cps.slice(i, i + n).join("");
    i += n;
  }
  return out;
}

function segment(m: Matcher, s: string): string[] {
  const cps = Array.from(s), segs: string[] = [];
  let buf = "";
  for (let i = 0; i < cps.length;) {
    const hit = m.match(cps, i);
    if (hit) {
      if (buf) { segs.push(buf); buf = ""; }
      segs.push(cps.slice(i, i + hit.len).join(""));
      i += hit.len;
      continue;
    }
    const n = idsLength(cps, i) || 1;
    buf += cps.slice(i, i + n).join("");
    i += n;
  }
  if (buf) segs.push(buf);
  return segs;
}

export interface OpenccData { hash: string; configs: Record<string, unknown>; dicts: Record<string, string> }
type DictDoc = { type: string; file?: string; dicts?: DictDoc[]; match_policy?: string; may_output_tofu?: boolean };
type ConfigDoc = { normalization?: { dict: DictDoc }[]; segmentation?: { type: string; dict: DictDoc }; conversion_chain: { dict: DictDoc }[] };

export class Converter {
  private norm: Matcher[];
  private seg: Matcher | null;
  private chain: Matcher[];
  constructor(cfg: ConfigDoc, dict: (d: DictDoc) => Matcher | null) {
    const chain = (xs: { dict: DictDoc }[] | undefined) => (xs || []).map(x => dict(x.dict)).filter((x): x is Matcher => !!x);
    this.norm = chain(cfg.normalization);
    if (cfg.segmentation && cfg.segmentation.type !== "mmseg") throw new Error("OpenCC segmentation " + cfg.segmentation.type);
    this.seg = cfg.segmentation ? dict(cfg.segmentation.dict) : null;
    this.chain = chain(cfg.conversion_chain);
  }
  private run(chain: Matcher[], s: string) { for (const m of chain) s = convertWith(m, s); return s; }
  convert(text: string): string {
    if (!text) return "";
    let s = this.norm.length ? this.run(this.norm, text) : text;
    const segs = this.seg ? segment(this.seg, s) : [s];
    s = "";
    for (const seg of segs) s += this.run(this.chain, seg);
    return s;
  }
}

/** the converters of the published configs (hk2s, s2hk, s2t), sharing their dictionaries */
export function converters(data: OpenccData): Record<string, Converter> {
  const leaves = new Map<string, Leaf>();
  const dict = (d: DictDoc): Matcher | null => {
    // may_output_tofu dictionaries are in: the Python binding's include_tofu_risk_dictionaries defaults to True
    if (d.type === "group") {
      const kids = (d.dicts || []).map(dict).filter((x): x is Matcher => !!x);
      return new Group(kids, d.match_policy === "union");
    }
    const f = d.file!;
    let l = leaves.get(f);
    if (!l) {
      if (data.dicts[f] == null) throw new Error("OpenCC dictionary not published: " + f);
      l = new Leaf(data.dicts[f]);
      leaves.set(f, l);
    }
    return l;
  };
  return Object.fromEntries(Object.entries(data.configs).map(([k, c]) => [k, new Converter(c as ConfigDoc, dict)]));
}
