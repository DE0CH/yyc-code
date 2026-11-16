// WiseSearch's shared pieces (Deyao, 2026-10-29: the full WiseSearch, WisersOne's layout in his house style —
// claude-env deyao-web-design: square edges, colour only for meaning, inline SVG icons, no motion): the bar, the icons,
// state kept for the life of the page (Back shows a panel as it was left), and helpers.
import { useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";

export const head = (title: string) => () => ({ meta: [{ title: `${title}｜WiseSearch 轉接` }, { name: "robots", content: "noindex" }] });

const P: Record<string, ReactNode> = {
  text: <><path d="M6 3h9l4 4v14H6z" /><path d="M9 11h7M9 15h7M9 7h3" /></>,
  img: <><path d="M4 4h16v16H4z" /><path d="M4 16l5-5 4 4 2-2 5 5" /><path d="M15 8h1" /></>,
  website: <><path d="M3 4h18v16H3z" /><path d="M3 9h18" /><path d="M6 6.5h1M9 6.5h1" /></>,
  video: <><path d="M3 5h18v14H3z" /><path d="M10 9v6l5-3z" /></>,
  audio: <><path d="M9 18V6l10-2v12" /><path d="M6 15h3v4H6zM16 13h3v4h-3z" /></>,
  pdf: <><path d="M6 3h9l4 4v14H6z" /><path d="M9 13h2v4H9zM9 13h2" /><path d="M14 13v4M14 13h2M14 15h2" /></>,
  search: <><path d="M4 4h10v10H4z" /><path d="M14 14l6 6" /></>,
  close: <path d="M5 5l14 14M19 5L5 19" />,
  prev: <path d="M15 5l-7 7 7 7" />,
  next: <path d="M9 5l7 7-7 7" />,
  down: <path d="M5 9l7 7 7-7" />,
  up: <path d="M5 15l7-7 7 7" />,
  print: <><path d="M6 9V3h12v6" /><path d="M3 9h18v8H3z" /><path d="M7 14h10v7H7z" /></>,
  download: <><path d="M12 3v12" /><path d="M7 10l5 5 5-5" /><path d="M4 21h16" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  ban: <><path d="M4 4h16v16H4z" /><path d="M4 20L20 4" /></>,
  check: <path d="M4 12l5 5L20 6" />,
  cross: <path d="M6 6l12 12M18 6L6 18" />,
  help: <><path d="M3 3h18v18H3z" /><path d="M9 9a3 3 0 1 1 3 3v2" /><path d="M12 17v1" /></>,
  filter: <path d="M3 4h18l-7 8v7l-4 2v-9z" />,
  link: <><path d="M10 14l4-4" /><path d="M8 11l-3 3 5 5 3-3" /><path d="M16 13l3-3-5-5-3 3" /></>,
  list: <path d="M4 6h16M4 12h16M4 18h16" />,
  cards: <><path d="M3 4h18v7H3z" /><path d="M3 14h18v7H3z" /></>,
  grid: <><path d="M3 3h8v8H3zM13 3h8v8h-8zM3 13h8v8H3zM13 13h8v8h-8z" /></>,
  reset: <><path d="M4 4v6h6" /><path d="M5 10a8 8 0 1 1 2 7" /></>,
};
/** one icon of the set: viewBox 24, stroke 2, square caps, no fill */
export function I({ n, label }: { n: string; label?: string }) {
  return (
    <svg className="wi" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square"
      strokeLinejoin="miter" aria-hidden={label ? undefined : true} role={label ? "img" : undefined} aria-label={label}>{P[n]}</svg>
  );
}

export function Bar() {
  return (
    <header className="wbar yyc-bar">
      <div className="row yyc-row"><h1>WiseSearch 轉接</h1></div>
      <nav className="wnav yyc-sub" aria-label="WiseSearch">
        <Link to="/app/wisers" activeOptions={{ exact: true }}>搜尋</Link>
        <Link to="/app/wisers/log">紀錄</Link>
        <Link to="/app/wisers/saved">已儲存的回應</Link>
      </nav>
    </header>
  );
}

export function Frame({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return <div className="page-wisers"><Bar /><main className={"wmain" + (wide ? " wide" : "")}>{children}</main></div>;
}

// state kept for the life of the page (a panel's open/closed, a tree's expanded nodes): Back shows it as it was left,
// at the same height, so the router's restored scroll position lands where it was
export const kept = new Map<string, unknown>();
export function useKept<T>(key: string, init: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => (kept.has(key) ? kept.get(key) as T : init));
  return [v, (x: T) => { kept.set(key, x); setV(x); }];
}
/** a <details> that stays as the reader left it (kept by key) */
export function Keep({ k, open, className, summary, children, onOpen }: { k: string; open: boolean; className: string; summary: ReactNode;
  children: ReactNode; onOpen?: () => void }) {
  const [first] = useState(() => (kept.has("d:" + k) ? kept.get("d:" + k) as boolean : open));
  return (
    <details className={className} data-key={k} open={first || undefined} onToggle={e => {
      const o = (e.target as HTMLDetailsElement).open; kept.set("d:" + k, o); if (o) onOpen?.();
    }}>
      <summary><span>{summary}</span><I n="down" /></summary>{children}
    </details>
  );
}

/** a row of ruled cells, the chosen one ink-filled (WisersOne's segmented choices) */
export function Seg<T extends string>({ value, options, set, label }: { value: T; options: [T, ReactNode][]; set: (v: T) => void; label: string }) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map(([v, t]) => (
        <button type="button" key={v} role="radio" aria-checked={value === v} className={value === v ? "on" : ""} onClick={() => set(v)}>{t}</button>
      ))}
    </div>
  );
}

/** the marked excerpt / headline: Wisers' hits as <mark> */
export function Marked({ parts }: { parts: [string, boolean][] }) {
  return <>{parts.map(([t, hit], j) => (hit ? <mark key={j}>{t}</mark> : <span key={j}>{t}</span>))}</>;
}

export const secs = (n: number) => (n < 90 ? `${Math.round(n)} 秒` : `${Math.floor(n / 60)} 分 ${Math.round(n % 60)} 秒`);
export const CHARGED: Record<string, string> = { yes: "已計 Wisers 額度。", maybe: "Wisers 可能已計算額度。", no: "沒有送到 Wisers，不計額度。",
  unknown: "Wisers 是否計額度未知。" };
