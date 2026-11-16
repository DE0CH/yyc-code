// Latin-script text in a Chinese page (Deyao, 2026-10-21: "Apostrophe and quotation mark for English sources are
// wrong"). The page is lang="zh-HK" in Noto Sans HK, whose ‘ ’ “ ” – — … are full-width CJK glyphs: "Netflix’s" showed
// as "Netflix’ s", "“AI Gone Rogue”" with a wide gap at each quote. Latin-script text (a post's title, text, excerpt)
// is marked lang="en"; under :lang(en) those punctuation marks come from Noto Sans (a subset of only them, loaded in
// __root.tsx; styles/index.css), every letter and Han character still Noto Sans HK. Chinese text keeps the CJK
// punctuation it is written with (「」, full-width “”).
import type { ReactNode } from "react";

const HAN = /[㐀-鿿豈-﫿]/g;
const LATIN = /[A-Za-zÀ-ɏ]/g;
const KANA_HANGUL = /[぀-ヿ가-힯]/;

/** "en" for Latin-script text (as news-stream titles.chinese counts, a word ≈ 4 letters ≈ 1 Han character, but Han
 * characters under 35 % rather than a fifth, so an English sentence naming 李家超 stays English; no kana/hangul), else
 * undefined (the page's zh-HK) */
export function langOf(s?: string | null): "en" | undefined {
  const t = (s || "").replace(/https?:\/\/\S+|[#@]\S+/g, "");
  const latin = (t.match(LATIN) || []).length;
  if (latin < 2 || KANA_HANGUL.test(t)) return undefined;
  const han = (t.match(HAN) || []).length;
  return han < 0.35 * (han + latin / 4) ? "en" : undefined;
}

/** a piece of a post's text, in a lang="en" span when it is Latin-script */
export function Txt({ s, children }: { s?: string | null; children?: ReactNode }) {
  const body = children ?? s;
  return langOf(s) ? <span lang="en">{body}</span> : <>{body}</>;
}

/** the marks Noto Sans HK draws full-width that Latin-script text takes from Noto Sans (the Google Fonts subset) */
export const LATIN_PUNCT = "‘’“”‚„–—…";
