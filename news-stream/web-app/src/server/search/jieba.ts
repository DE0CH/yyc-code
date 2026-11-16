// jieba 0.42.1's Tokenizer.cut(sentence, HMM=False) (accurate mode), as news-stream/searchtext.tokens() calls it: the
// sentence is split into blocks of jieba's re_han_default characters, each block cut along the most probable route
// through its dictionary DAG (jieba's get_DAG + calc + __cut_DAG_NO_HMM), and every other character yielded alone.
// The dictionary is the Python tokenizer's own (jieba's dict.txt + search_dict.txt + the streams' queries, after
// add_word), published to table search_words with each word's math.log(frequency) (searchtext.publish_edge), so the
// route sums here are bit-for-bit Python's. Only the words a query can contain are read: its candidate substrings.

const BLOCK = /[一-鿕a-zA-Z0-9+#&._%-]+/g;  // re_han_default
const ENG = /^[a-zA-Z0-9]$/;                         // re_eng, on a one-character word

/** every substring of a block that can be a dictionary word (1 … maxlen characters) */
export function candidates(text: string, maxlen: number, out = new Set<string>()): Set<string> {
  for (const m of text.matchAll(BLOCK)) {
    const b = m[0];
    for (let k = 0; k < b.length; k++) for (let n = 1; n <= maxlen && k + n <= b.length; n++) out.add(b.slice(k, k + n));
  }
  return out;
}

/** words: dictionary word → log(frequency), at least for every candidate of the text */
function cutBlock(b: string, words: Map<string, number>, logtotal: number, maxlen: number): string[] {
  const N = b.length;  // a block holds only BMP characters: code units are characters
  const dag: number[][] = [];
  for (let k = 0; k < N; k++) {
    const xs: number[] = [];
    for (let i = k; i < N && i - k < maxlen; i++) if (words.has(b.slice(k, i + 1))) xs.push(i);
    dag.push(xs.length ? xs : [k]);
  }
  const route: [number, number][] = new Array(N + 1);
  route[N] = [0, 0];
  for (let idx = N - 1; idx >= 0; idx--) {
    let best: [number, number] | null = null;
    for (const x of dag[idx]) {
      const v = (words.get(b.slice(idx, x + 1)) ?? 0) - logtotal + route[x + 1][0];  // log(FREQ.get(w) or 1)
      if (!best || v > best[0] || (v === best[0] && x > best[1])) best = [v, x];  // max((p, x)): the later end on a tie
    }
    route[idx] = best!;
  }
  const out: string[] = [];
  let x = 0, buf = "";
  while (x < N) {
    const y = route[x][1] + 1, w = b.slice(x, y);
    if (w.length === 1 && ENG.test(w)) buf += w;
    else { if (buf) { out.push(buf); buf = ""; } out.push(w); }
    x = y;
  }
  if (buf) out.push(buf);
  return out;
}

/** Tokenizer.cut(s, HMM=False) as a list. Text outside the blocks comes out one character at a time (whitespace too,
 *  where jieba keeps "\r\n" and each whitespace character whole: tokens() drops them all, as having no letter) */
export function cut(s: string, words: Map<string, number>, logtotal: number, maxlen: number): string[] {
  const out: string[] = [];
  let last = 0;
  const other = (t: string) => {
    for (let i = 0; i < t.length;) {
      if (t[i] === "\r" && t[i + 1] === "\n") { out.push("\r\n"); i += 2; continue; }
      const ch = String.fromCodePoint(t.codePointAt(i)!);
      out.push(ch);
      i += ch.length;
    }
  };
  for (const m of s.matchAll(BLOCK)) {
    other(s.slice(last, m.index));
    out.push(...cutBlock(m[0], words, logtotal, maxlen));
    last = m.index! + m[0].length;
  }
  other(s.slice(last));
  return out;
}
