// Presigned GET links to YYC's video bucket (Hetzner Object Storage, project yyc; tiktok_video.py): AWS Signature
// Version 4 in the query string, path-style, signed with WebCrypto. The same algorithm as news-stream/s3.py's
// presign (tests/test_tiktok_video.py checks both against AWS's published example and against each other).
// The browser opens the link straight at the bucket: the video plays and downloads from Hetzner, not through us.

export interface S3Config { endpoint: string; region: string; accessKey: string; secretKey: string; bucket: string }

const enc = new TextEncoder();
const hex = (b: ArrayBuffer) => Array.from(new Uint8Array(b), x => x.toString(16).padStart(2, "0")).join("");
const q = (s: string, safe = "-_.~") =>
  Array.from(enc.encode(s), c => {
    const ch = String.fromCharCode(c);
    return /[A-Za-z0-9]/.test(ch) || safe.includes(ch) ? ch : "%" + c.toString(16).toUpperCase().padStart(2, "0");
  }).join("");

async function hmac(key: ArrayBuffer | Uint8Array<ArrayBuffer>, s: string) {
  const k = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return crypto.subtle.sign("HMAC", k, enc.encode(s));
}

const amzDate = (d: Date) => d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");

/** a GET link to `key` valid `expires` seconds from `now`; params: e.g. response-content-disposition */
export async function presign(c: S3Config, key: string, expires = 3600, params: Record<string, string> = {},
  now = new Date()): Promise<string> {
  const ep = c.endpoint.replace(/\/+$/, "");
  const host = new URL(ep).host;
  const amz = amzDate(now), day = amz.slice(0, 8);
  const scope = `${day}/${c.region}/s3/aws4_request`;
  const path = (c.bucket ? "/" + q(c.bucket) : "") + "/" + q(key, "-_.~/");
  const p: Record<string, string> = { ...params, "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
    "X-Amz-Credential": `${c.accessKey}/${scope}`, "X-Amz-Date": amz, "X-Amz-Expires": String(Math.floor(expires)),
    "X-Amz-SignedHeaders": "host" };
  const query = Object.keys(p).sort().map(k => `${q(k)}=${q(p[k])}`).join("&");
  const canon = ["GET", path, query, `host:${host}\n`, "host", "UNSIGNED-PAYLOAD"].join("\n");
  const sts = ["AWS4-HMAC-SHA256", amz, scope, hex(await crypto.subtle.digest("SHA-256", enc.encode(canon)))].join("\n");
  let k: ArrayBuffer = await hmac(enc.encode("AWS4" + c.secretKey), day);
  for (const part of [c.region, "s3", "aws4_request"]) k = await hmac(k, part);
  return `${ep}${path}?${query}&X-Amz-Signature=${hex(await hmac(k, sts))}`;
}

/** for tests (scripts/call.ts passes the database first) */
export const presignForTest = (_sql: unknown, c: S3Config, key: string, expires: number,
  params: Record<string, string>, iso: string) => presign(c, key, expires, params, new Date(iso));
