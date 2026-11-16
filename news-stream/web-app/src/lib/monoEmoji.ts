/** Emoji drawn in the text colour on the pages where colour means a state (the monitor, the live dashboard; Deyao,
 *  2026-10-30): the fonts put Noto Emoji (monochrome) before the system's colour emoji (house.css .mono-emoji,
 *  live.css), but an emoji variation selector (U+FE0F, "❤️", "⚠️") makes the browser take the colour font whatever
 *  the font list says, so it is dropped from the text; the emoji stays, in Noto Emoji. Also drops its JSON escape. */
export const monoEmoji = (s: string) => s.replace(/️|\\u[fF][eE]0[fF]/g, "");
