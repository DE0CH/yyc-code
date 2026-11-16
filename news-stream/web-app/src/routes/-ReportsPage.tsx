// 報告: a card per profile and day with links to its files, grouped by day, newest first.
// 此設定檔 lists the picker's stream; 全部設定檔 (?all=1, replacing the URL, not a new history entry) every profile.
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Shell } from "../shell/Shell";
import { useProfile } from "../shell/profile";
import { reportsQuery, useProfiles } from "../queries";
import { NotFound } from "./-NotFound";
import { dt } from "../lib/dates";

const LABELS: Record<string, string> = {
  "format1.html": "社交概況（格式一）", "format2.html": "電郵摘要（格式二）", "report.pdf": "PDF 報告",
  "hot_topics.xlsx": "熱門話題 Excel", "posts.xlsx": "帖子列表 Excel", "posts.docx": "帖子列表 Word", "summary.json": "數據 JSON",
};
const ORDER = Object.keys(LABELS);

export function ReportsPage() {
  const search = useSearch({ from: "/reports/" });
  const navigate = useNavigate({ from: "/reports/" });
  const prof = useProfile();
  const reportsQ = useQuery(reportsQuery);
  const profilesQ = useProfiles();
  const all = search.all === "1";
  const names: Record<string, string> = Object.fromEntries((profilesQ.data || []).map(p => [p.id, p.title || p.name]));

  if (reportsQ.isSuccess && reportsQ.data === null) return <NotFound />;  // the 報告 flag is off for this site

  const setAll = (v: boolean) => navigate({ to: ".", search: (prev: Record<string, unknown>) => ({ ...prev, all: v ? "1" : undefined }), replace: true });

  let body;
  if (reportsQ.isError) body = <p className="note">未能載入報告列表。</p>;
  else if (!reportsQ.data || profilesQ.isPending) body = <p className="note">載入中…</p>;
  else {
    const pid = prof.effective;
    const shown = reportsQ.data.filter(x => all || x.profile === pid);
    const byDay: Record<string, typeof shown> = {};
    for (const x of shown) (byDay[x.day] ||= []).push(x);
    const days = Object.keys(byDay).sort().reverse();
    body = !days.length
      ? <p className="note">{all ? "暫時未有報告。" : `「${names[pid || ""] || pid || ""}」暫時未有報告。報告只為開了每日報告的設定檔產生。`}</p>
      : days.map(d => (
        <div key={d}>
          <div className="day">{dt.date(d)}</div>
          <div className="grid">
            {byDay[d].map(x => {
              const files = x.files.map(f => [f.split("/").pop() as string, f] as const).filter(([n]) => LABELS[n])
                .sort((a, b) => ORDER.indexOf(a[0]) - ORDER.indexOf(b[0]));
              const real = files.filter(([n]) => n !== "summary.json");
              return (
                <div className="card" key={x.profile}>
                  <h3>{names[x.profile] || x.profile}</h3>
                  <div className="sub">{x.profile}</div>
                  {real.length
                    ? <div className="links">{files.map(([n, f]) => <a key={n} href={f} target="_blank" rel="noopener">{LABELS[n]}</a>)}</div>
                    : <div className="empty">當日沒有相關帖子。</div>}
                </div>
              );
            })}
          </div>
        </div>
      ));
  }

  return (
    <Shell section="reports" title="報告">
      <main>
        <div className="head"><h1>報告</h1>
          <div className="seg" role="group" aria-label="顯示範圍">
            <button type="button" aria-pressed={!all} onClick={() => setAll(false)}>此設定檔</button>
            <button type="button" aria-pressed={all} onClick={() => setAll(true)}>全部設定檔</button>
          </div>
        </div>
        <p className="lead">每個監察項目每日的報告（香港時間前一日的帖子）：社交概況電郵（格式一）、電郵摘要（格式二）、一頁 PDF AI 報告、熱門話題列表及帖子列表（Excel / Word）。</p>
        <div id="list">{body}</div>
      </main>
    </Shell>
  );
}
