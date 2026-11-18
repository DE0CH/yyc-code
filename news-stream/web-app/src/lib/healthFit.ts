// The system health page's fitting (/app/health, -HealthPage.tsx; Deyao, 2026-11-17: "the screen space should inform the
// level of details that can be shown"). Pure, so tests/test_health_page.py checks it in Node too.
//
// A column is a stack of sections, each a heading and fixed 14 px lines. Every line has a level: 0 always shows; 1, 2
// and 3 are details, added level by level, in order within a level, while they fit the column's height, so nothing is
// cut off and no space is left over that a detail could use. Lines of one level that share a `group` (a host list, the
// pods) go in whole or not at all. When the level-0 lines alone don't fit, the oldest idle Jarvis 4 sessions fold into
// one "+N idle" line (jarvisFold); past that, the last lines give way to "N more lines".

export const ROW = 14;        // a line (health.css .hp-l)
export const HEAD_FIRST = 19; // a column's first heading (18 px and its rule below)
export const HEAD = 20;       // a later heading (a rule above as well)
export const PAD = 3;         // a section's padding (1 above, 2 below)
export const COL_RULE = 1;    // the rule between the two columns

export interface FitLine { level: 0 | 1 | 2 | 3; group?: string }
export interface FitSection<L extends FitLine> { key: string; lines: L[] }

/** the two columns' widths in whole px for a window `w` wide: the left one rounded down, the rule, the rest */
export function columns(w: number): [number, number] {
  const left = Math.floor((w - COL_RULE) / 2);
  return [left, Math.max(0, w - COL_RULE - left)];
}

/** the px the sections' headings and padding take */
export const frame = (n: number) => (n ? HEAD_FIRST + (n - 1) * HEAD + n * PAD : 0);

/** which lines show, per section (true = shown), for a column `height` px tall; `more` = level-0 lines left out at the
 * end (shown as one "N more lines" line, which takes the last of them) */
export function fit<L extends FitLine>(sections: FitSection<L>[], height: number): { keep: boolean[][]; used: number; more: number } {
  const keep = sections.map(s => s.lines.map(l => l.level === 0));
  let used = frame(sections.length) + ROW * keep.flat().filter(Boolean).length;
  let more = 0;
  if (used > height) {
    // level 0 alone doesn't fit: drop level-0 lines from the end, one more for the "N more lines" line
    const room = Math.max(0, Math.floor((height - frame(sections.length)) / ROW) - 1);
    let seen = 0;
    for (let i = 0; i < sections.length; i++)
      for (let j = 0; j < sections[i].lines.length; j++) {
        if (!keep[i][j]) continue;
        if (seen >= room) { keep[i][j] = false; more++; } else seen++;
      }
    return { keep, used: frame(sections.length) + ROW * (seen + 1), more };
  }
  for (const level of [1, 2, 3] as const)
    sections.forEach((s, i) => {
      let j = 0;
      while (j < s.lines.length) {
        const l = s.lines[j];
        if (l.level !== level) { j++; continue; }
        let k = j + 1;
        if (l.group) while (k < s.lines.length && s.lines[k].level === level && s.lines[k].group === l.group) k++;
        if (used + ROW * (k - j) <= height) {
          for (let x = j; x < k; x++) keep[i][x] = true;
          used += ROW * (k - j);
        }
        j = k;
      }
    });
  return { keep, used, more };
}

/** Jarvis 4's order on the page: needs you, idle (most recent first), working, background, frozen, scheduled, booting */
export const JARVIS_ORDER = ["waiting", "idle", "working", "background", "frozen", "scheduled", "booting"] as const;

/** how many of the idle sessions fold (the oldest) so the sessions' level-0 lines take at most `rows` lines, the fold's
 * own line included; 0 when they all fit */
export function jarvisFold(n: number, idle: number, rows: number): number {
  if (n <= rows) return 0;
  return Math.min(idle, n - rows + 1);
}
