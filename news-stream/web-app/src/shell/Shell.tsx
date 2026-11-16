// The app shell (NAVIGATION.md): the top bar of ruled rows (brand, the five sections on desktop, the profile picker, the page's
// tools, the page's own rows) and the phone tab bar.
import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { Link, useLocation } from "@tanstack/react-router";
import { SiteLink } from "../lib/SiteLink";
import { Icon } from "../lib/icons";
import { BRAND } from "../lib/zh";
import { useFlags } from "../queries";
import { useProfile, type Pick } from "./profile";
import { moveFromLocalStorage } from "../lib/remembered";

export type Section = "stream" | "search" | "dashboard" | "reports" | "item" | "proof";

export const SECTIONS = [
  { key: "stream", zh: "串流", to: "/", icon: "stream" },
  { key: "search", zh: "搜尋", to: "/search", icon: "search" },
  { key: "dashboard", zh: "數據分析", to: "/dashboard", icon: "chart" },
  { key: "reports", zh: "報告", to: "/reports", icon: "report" },
] as const;

// sections behind a feature flag (flags.py, GET /api/flags) stay hidden until the flag says on
const FLAGGED: Record<string, string> = { search: "search", reports: "reports" };

function useVisibleSections() {
  const flags = useFlags().data || {};
  return SECTIONS.filter(s => {
    const flag = FLAGGED[s.key];
    return !flag || !!flags[flag];
  });
}

/** on touch screens the tab bar steps aside while a text field has the keyboard (`yyc-typing`):
 *  it comes back only once the tap that left the field has ended, so a button beside the field stays under the finger */
function useTypingHidesTabbar() {
  useEffect(() => {
    if (!matchMedia("(hover: none) and (pointer: coarse)").matches) return;
    const field = (t: Element | null) => !!t && (t.matches("textarea, [contenteditable]") ||
      (t.matches("input") && !/^(checkbox|radio|button|submit|range|color|file|date)$/.test((t as HTMLInputElement).type)));
    const root = document.documentElement;
    let pressing = false, timer = 0;
    const settle = () => { clearTimeout(timer); timer = window.setTimeout(() => { if (!pressing && !field(document.activeElement)) root.classList.remove("yyc-typing"); }, 60); };
    const down = () => { pressing = true; };
    const up = () => { pressing = false; if (root.classList.contains("yyc-typing")) settle(); };
    const fin = (e: FocusEvent) => { if (field(e.target as Element)) { clearTimeout(timer); root.classList.add("yyc-typing"); } };
    addEventListener("pointerdown", down, true); addEventListener("pointerup", up, true); addEventListener("pointercancel", up, true);
    document.addEventListener("focusin", fin); document.addEventListener("focusout", settle);
    return () => {
      removeEventListener("pointerdown", down, true); removeEventListener("pointerup", up, true); removeEventListener("pointercancel", up, true);
      document.removeEventListener("focusin", fin); document.removeEventListener("focusout", settle);
    };
  }, []);
}

/** a notice to staff under the top bar on every page, null when there is none */
const NOTICE: string | null = null;

/** the stream picker (NAVIGATION.md "The stream picker"): one real link per stream (?p=), the
 *  current one marked and scrolled into view; a tap is a new history entry, so Back returns to the stream before */
function StreamPicker({ pick, drop = [], pickDefault = null }: { pick: Pick; drop?: readonly string[]; pickDefault?: string | null }) {
  const prof = useProfile();
  const loc = useLocation();
  const strip = useRef<HTMLDivElement>(null);
  // "page": the page's default, marked from the server's render on, until the page writes it into ?p=
  const current = pick === "page" && !prof.fromURL ? pickDefault : prof.effective;
  const href = (id: string) => {
    const q = new URLSearchParams(loc.searchStr);
    for (const k of drop) q.delete(k);
    q.set("p", id);
    return loc.pathname + "?" + q.toString();
  };
  useLayoutEffect(() => {
    const s = strip.current, a = s?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!s || !a || !s.clientWidth) return;
    const pad = 28, l = a.offsetLeft, r = l + a.offsetWidth;
    if (l < s.scrollLeft + pad) s.scrollLeft = Math.max(0, l - pad);
    else if (r > s.scrollLeft + s.clientWidth - pad) s.scrollLeft = r - s.clientWidth + pad;
  }, [current, prof.list.length]);
  return (
    <nav className={"yyc-pick" + (pick === "none" ? " yyc-nopick" : "")} aria-label="監察串流">
      <div className="yyc-streams" ref={strip}>
        {prof.loading ? <span className="yyc-wait">載入中…</span> : prof.error ? <span className="yyc-wait">未能載入串流</span>
          : pick === "none" ? null : prof.list.map(p => (
            <a key={p.id} data-p={p.id} href={href(p.id)} title={p.title || undefined} aria-current={p.id === current ? "page" : undefined}
              onClick={e => {
                if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;  // a new tab: the plain link
                e.preventDefault();
                prof.set(p.id, true, drop);
              }}>{p.name || p.id}</a>
          ))}
      </div>
    </nav>
  );
}

/** a section link carries the stream shown (?p=), so it stays selected between sections */
function SecLink({ s, current, children }: { s: (typeof SECTIONS)[number]; current: boolean; children: ReactNode }) {
  const prof = useProfile();
  return <SiteLink to={s.to} search={{ p: prof.effective }} aria-current={current ? "page" : undefined}>{children}</SiteLink>;
}

export function Shell(props: {
  section: Section; title: string; pick?: Pick;
  /** pick "page": the stream the page shows while the URL has no ?p= (it writes it in after hydration) */
  pickDefault?: string | null;
  /** the page's drill-down params a stream switch drops (data-pick-drop: 數據分析's tid page post) */
  pickDrop?: readonly string[];
  tools?: ReactNode; rows?: ReactNode; children: ReactNode;
}) {
  const { section, pick = "all" } = props;
  const sections = useVisibleSections();
  const bar = useRef<HTMLElement>(null);
  useTypingHidesTabbar();

  useEffect(() => { document.title = props.title.endsWith(BRAND) ? props.title : `${props.title}｜${BRAND}`; }, [props.title]);
  useEffect(() => { document.documentElement.dataset.hydrated = "1"; }, []);  // the page is live (tests wait for it)
  useEffect(() => { moveFromLocalStorage(); }, []);

  // the bar's height, for sticky parts under it
  useLayoutEffect(() => {
    const el = bar.current; if (!el) return;
    const setH = () => document.documentElement.style.setProperty("--bar-h", el.offsetHeight + "px");
    setH();
    const ro = new ResizeObserver(setH); ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div className={`page-${section}`}>
      <div className="yyc-topfill" aria-hidden="true" />{/* iOS 26 Safari's top colour (shell.css) */}
      <header className="yyc-bar" data-pick={pick} ref={bar}>
        <div className="yyc-row">
          <Link className="yyc-brand" to="/">YYC<span> 新聞監察</span></Link>
          <nav className="yyc-secs" aria-label="主要部分">
            {sections.map(s => (
              <SecLink key={s.key} s={s} current={s.key === section}><Icon name={s.icon} />{s.zh}</SecLink>
            ))}
          </nav>
          <StreamPicker pick={pick} drop={props.pickDrop} pickDefault={props.pickDefault} />
          {props.tools && <div className="yyc-tools">{props.tools}</div>}
        </div>
        {props.rows}
        {NOTICE && <p className="yyc-notice" role="status">{NOTICE}</p>}
      </header>
      {props.children}
      <nav className="yyc-tabbar" aria-label="主要部分">
        {sections.map(s => (
          <SecLink key={s.key} s={s} current={s.key === section}><Icon name={s.icon} /><span>{s.zh}</span></SecLink>
        ))}
      </nav>
    </div>
  );
}
