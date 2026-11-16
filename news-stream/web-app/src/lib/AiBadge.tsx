import { langOf } from "./lang";
import { dt } from "./dates";
/** A title written by AI (news-stream/titles.py: a post with no title of its own, or one whose own title doesn't say
 * what it is about: title_retitled). The badge before the title, with the model and time on hover; nothing for a
 * post's own title, including its own opening headline (title_extracted). */
export interface AiTitled {
  title_by_ai?: boolean; title_linked?: boolean; title_retitled?: boolean; original_title?: string | null;
  ai_title_model?: string | null; ai_title_model_name?: string | null; ai_title_at?: string | null;
}

/** the source's own title of a retitled post (Deyao, 2026-10-20: kept and shown as the source's title) or null */
const sourceTitle = (x: AiTitled) => (x.title_by_ai && x.title_retitled && x.original_title) || null;

export function AiBadge({ x }: { x: AiTitled }) {
  if (x.title_linked) return <span className="ai-t" role="img" aria-label="取自連結頁面的標題"
    title="這個標題取自帖子所連結的頁面：帖子本身只有連結。連結內容見下。">連結標題</span>;
  if (!x.title_by_ai) return null;
  const when = x.ai_title_at
    ? dt.dateTime(x.ai_title_at)
    : "";
  const by = [x.ai_title_model_name || (x.ai_title_model || "").replace(/^.*\//, ""), when].filter(Boolean).join("，");
  const src = sourceTitle(x);
  const why = src ? `來源的標題「${src}」未能說明內容。` : "帖子沒有自己的標題。原文見下。";
  return <span className="ai-t" role="img" aria-label="AI 撰寫的標題"
    title={`這個標題由 AI 撰寫${by ? `（${by}）` : ""}：${why}`}>AI 標題</span>;
}

/** under the headline of a retitled post: the title the source gave it (nothing for any other post) */
export function SourceTitle({ x }: { x: AiTitled }) {
  const src = sourceTitle(x);
  if (!src) return null;
  return <p className="src-title">來源標題：<span lang={langOf(src)}>{src}</span></p>;
}

/** A transcript made by a speech model (news-stream/media.py; Deyao, 2026-10-15: "label it as AI transcribed"): the
 * 「AI 轉錄」 badge, the model and time on hover. Nothing for the uploader's own captions (by_ai false). */
export function AiTranscribedBadge({ m }: { m: { by_ai: boolean | null; model_name: string | null; done_at: string | null } }) {
  if (!m.by_ai) return null;
  const when = m.done_at
    ? dt.dateTime(m.done_at)
    : "";
  const by = [m.model_name, when].filter(Boolean).join("，");
  return <span className="ai-t" role="img" aria-label="AI 轉錄的逐字稿"
    title={`這段逐字稿由 AI 轉錄${by ? `（${by}）` : ""}，或有錯字，以原片為準。`}>AI 轉錄</span>;
}
