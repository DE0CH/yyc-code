// The selection-bias note, once per view (Deyao, 2026-10-20: "too many question marks"): one quiet line at the top of
// each 數據分析 view whose figures count posts one per vote, and one at the foot of 串流's 摘要 column (lib/bias.ts;
// README "Figures that depend on our selection"). No figure carries its own 「?」. A view whose figures need their own
// definitions (摘要's metrics and scores) passes them as `defs`; they sit behind the line's single 「指標說明」 toggle.
import { BIAS_NOTE } from "./bias";

const INFO = <svg className="bias-i" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3h18v18H3z" /><path d="M12 11v6M12 7.5h.01" /></svg>;

export function BiasNote({ defs }: { defs?: [string, string][] }) {
  return (
    <div className="bias-note" data-bias-note role="note">
      <p>{INFO}<span>{BIAS_NOTE}</span></p>
      {defs && defs.length ? (
        <details>
          <summary>指標說明</summary>
          <dl>{defs.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
        </details>
      ) : null}
    </div>
  );
}
