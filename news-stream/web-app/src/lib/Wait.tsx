// The site's only spinner (Deyao, 2026-10-16; NAVIGATION.md "Spinners"): a spinner means "the page is fetching this
// and it will appear in seconds". It is shown only while a request is in flight; after SLOW_MS without an answer it
// turns into a static note with 重試. Data the pipeline has not produced yet (full text, transcript, scores…) is never
// a spinner: the page says in words what is pending (<Pending>), and live updates redraw it when the data lands.
import { useEffect, useState, type ReactNode } from "react";

export const SLOW_MS = 15_000;

/** a request in flight: spinner + what is being fetched; after SLOW_MS (or `failed`) a static note with 重試 */
export function Wait({ what, label, failed, onRetry, className, inline, slowMs = SLOW_MS, en }: {
  /** what is fetched, as a verb phrase (載入評分; English: "load the scores"); label: the busy text when not `${what}…`;
   *  slowMs: when the spinner turns into the note (a request known to take longer, e.g. one that may need a WisersOne
   *  login, waits longer); en: Deyao's own pages, in English (the proof view) */
  what: string; label?: string; failed?: boolean; onRetry?: () => void; className?: string; inline?: boolean; slowMs?: number; en?: boolean;
}) {
  const Tag = inline ? "span" : "p";
  const [round, setRound] = useState(0);
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    setSlow(false);
    const t = setTimeout(() => setSlow(true), slowMs);
    return () => clearTimeout(t);
  }, [round, slowMs]);
  const retry = onRetry && (() => { onRetry(); setRound(r => r + 1); });
  if (failed || slow) return (
    <Tag className={"wait-note" + (className ? " " + className : "")} role="status" data-wait="slow">
      {en ? (failed ? `Could not ${what}. ` : `Still trying to ${what} after ${Math.round(slowMs / 1000)} s; the connection may be down. `)
        : failed ? `未能${what}。` : `${what}超過 ${Math.round(slowMs / 1000)} 秒仍未完成，連線可能有問題。`}
      {retry && <button type="button" className="linkbtn" onClick={retry}>{en ? "Retry" : "重試"}</button>}
    </Tag>
  );
  return (
    <Tag className={"wait" + (className ? " " + className : "")} role="status" data-wait="busy">
      <span className="spin" aria-hidden="true" />{label ?? what + "…"}
    </Tag>
  );
}

/** pipeline data not produced yet: a static line in words (never animated) */
export const Pending = ({ children }: { children: ReactNode }) => <p className="pending" data-pending="">{children}</p>;
