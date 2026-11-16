// The saved TikTok video of a post (tiktok_video.py, Deyao 2026-10-22: every TikTok post passing stage 1). Public: its
// state on the item page (item.view's `video`, = item.py video()); approved accounts (/app/item/video): a presigned
// 1-hour link to the file in YYC's bucket for playing, and one that downloads it (Content-Disposition attachment),
// made for each page load. The browser reads the file straight from Hetzner Object Storage.
import type { Sql } from "./db";
import { presign, type S3Config } from "./s3";

export const LINK_SECONDS = 3600;

export interface ItemVideo {
  status: "queued" | "ok" | "failed" | "gone"; bytes: number | null; duration: number | null;
  width: number | null; height: number | null;
}

/** the post's video state, or null (not a TikTok post that passed stage 1) */
export async function video(sql: Sql, pid: number): Promise<ItemVideo | null> {
  const [r] = await sql`SELECT status, bytes, duration, width, height FROM tiktok_videos WHERE post_id = ${pid}`;
  if (!r) return null;
  return { status: r.status, bytes: r.bytes == null ? null : Number(r.bytes),
    duration: r.duration == null ? null : Math.round(Number(r.duration)), width: r.width ?? null, height: r.height ?? null };
}

/** approved accounts: the links (status ok), else the state alone */
export async function videoLinks(sql: Sql, pid: number, cfg: S3Config | null, nowMs = Date.now()) {
  const [r] = await sql`SELECT post_id, video_id, status, s3_key, bytes, duration, width, height FROM tiktok_videos
    WHERE post_id = ${pid}`;
  if (!r) return null;
  const v = await video(sql, pid);
  if (r.status !== "ok" || !r.s3_key) return { ...v, url: null, download: null, expires_at: null };
  if (!cfg) return { ...v, url: null, download: null, expires_at: null, error: "video storage not configured" };
  const now = new Date(nowMs);
  const name = `tiktok-${r.video_id}.mp4`;
  const [url, download] = await Promise.all([
    presign(cfg, r.s3_key, LINK_SECONDS, { "response-content-type": "video/mp4" }, now),
    presign(cfg, r.s3_key, LINK_SECONDS, { "response-content-disposition": `attachment; filename="${name}"`,
      "response-content-type": "video/mp4" }, now),
  ]);
  return { ...v, url, download, filename: name, expires_at: new Date(nowMs + LINK_SECONDS * 1000).toISOString() };
}

/** the bucket from the Worker's env (vars VIDEO_S3_ENDPOINT / _REGION / _BUCKET in wrangler.jsonc, secrets
 *  VIDEO_S3_ACCESS_KEY / _SECRET_KEY), or null */
export function s3Config(env: Record<string, unknown>): S3Config | null {
  const g = (k: string) => (typeof env[k] === "string" ? (env[k] as string) : "");
  if (!g("VIDEO_S3_ENDPOINT") || !g("VIDEO_S3_ACCESS_KEY") || !g("VIDEO_S3_SECRET_KEY") || !g("VIDEO_S3_BUCKET")) return null;
  return { endpoint: g("VIDEO_S3_ENDPOINT"), region: g("VIDEO_S3_REGION") || "fsn1", bucket: g("VIDEO_S3_BUCKET"),
    accessKey: g("VIDEO_S3_ACCESS_KEY"), secretKey: g("VIDEO_S3_SECRET_KEY") };
}
