// The few HTML pages people see (sign-in consent, no access, personal tokens, the call log, the connect page at /):
// Traditional Chinese with Hong Kong usage, phone-width first, no scripts. The call log (/admin, admins only) is in
// English (Deyao, 2026-10-21; NAVIGATION.md "Languages").
import { dt, dtEn } from "../../web-app/src/lib/dates";
export const esc = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);

const CSS = `:root{--bg:#f6f7f9;--card:#fff;--fg:#16191d;--muted:#5f6670;--line:#e3e6ea;--accent:#0b57d0;--ok:#137333;--warn:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#111316;--card:#1b1e23;--fg:#e9ecef;--muted:#a0a7b0;--line:#2c3138;--accent:#8ab4f8;--ok:#81c995;--warn:#f2b8b5}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 "Noto Sans HK",-apple-system,system-ui,"PingFang HK",sans-serif;padding:16px}
main{max-width:640px;margin:0 auto}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 16px;margin:0 0 14px}
h1{font-size:21px;margin:0 0 8px}h2{font-size:17px;margin:18px 0 6px}h3{font-size:16px;margin:4px 0}ol{padding-left:22px;margin:6px 0}p{margin:8px 0}small,.muted{color:var(--muted)}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;word-break:break-all;overflow-wrap:anywhere}
pre{white-space:pre-wrap;word-break:break-all;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;margin:8px 0}
button,.btn{display:inline-block;font:inherit;font-weight:600;border:0;border-radius:10px;padding:11px 16px;background:var(--accent);color:#fff;text-decoration:none;cursor:pointer}
@media (prefers-color-scheme:dark){button,.btn{color:#0b1220}}
button.secondary{background:transparent;color:var(--accent);border:1px solid var(--line)}button.danger{background:var(--warn)}
form.inline{display:inline}.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
label{display:block;margin:10px 0 4px;color:var(--muted);font-size:14px}input,select{width:100%;font:inherit;padding:10px;border:1px solid var(--line);border-radius:8px;background:transparent;color:var(--fg)}
ul.list{list-style:none;padding:0;margin:0}ul.list li{border-top:1px solid var(--line);padding:10px 0}ul.list li:first-child{border-top:0}
.tag{display:inline-block;font-size:13px;border-radius:999px;padding:1px 9px;border:1px solid var(--line)}.tag.ok{color:var(--ok)}.tag.warn{color:var(--warn)}
a{color:var(--accent)}`;

export function page(title: string, body: string, status = 200, headers: Record<string, string> = {}, lang = "zh-HK") {
  const html = `<!doctype html><html lang="${lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>${esc(title)}</title><style>${CSS}</style></head><body><main>${body}</main></body></html>`;
  return new Response(html, { status, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
    "x-frame-options": "DENY", "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https:; frame-ancestors 'none'", ...headers } });
}

export function denied(email: string) {
  return `<div class="card"><h1>沒有權限</h1><p>你已用 <b>${esc(email)}</b> 登入，但這個電郵不在 YYC 可使用此服務的名單上。</p>
<p class="muted">用錯了電郵？重新登入，選另一個帳戶或電郵。如需加入名單，請聯絡 Deyao。</p></div>`;
}

export function message(title: string, text: string) {
  return `<div class="card"><h1>${esc(title)}</h1><p>${esc(text)}</p></div>`;
}

const fmt = (iso: string | null) => (iso ? dt.dateTime(iso) : "—");
const fmtEn = (iso: string | null) => (iso ? dtEn.dateTime(iso) : "never");

export function tokensPage(email: string, csrf: string, rows: { id: string; label: string; created_at: string; expires_at: string | null; last_used_at: string | null; revoked_at: string | null }[], fresh?: { token: string; label: string }) {
  const shown = fresh ? `<div class="card"><h1>新的個人存取權杖</h1><p>只會顯示這一次，請立即複製並妥善保存（例如放在密碼管理器）：</p>
<pre class="mono">${esc(fresh.token)}</pre><p class="muted">用法：HTTP 標頭 <code>Authorization: Bearer &lt;權杖&gt;</code>，適用於 REST API（/api/v1）和 MCP（/mcp）。</p></div>` : "";
  const items = rows.map(r => `<li><div class="row"><b>${esc(r.label)}</b>
${r.revoked_at ? '<span class="tag warn">已撤銷</span>' : r.expires_at && Date.parse(r.expires_at) < Date.now() ? '<span class="tag warn">已過期</span>' : '<span class="tag ok">有效</span>'}</div>
<small>建立 ${fmt(r.created_at)} · 到期 ${r.expires_at ? fmt(r.expires_at) : "永不"} · 最後使用 ${fmt(r.last_used_at)}</small>
${r.revoked_at ? "" : `<form class="inline" method="post" action="/tokens/revoke"><input type="hidden" name="csrf" value="${esc(csrf)}"><input type="hidden" name="id" value="${esc(r.id)}"> <button class="secondary">撤銷</button></form>`}</li>`).join("");
  return `${shown}<div class="card"><h1>個人存取權杖</h1><p class="muted">${esc(email)} · 給程式及指令碼使用，毋須瀏覽器登入。</p>
<form method="post" action="/tokens"><input type="hidden" name="csrf" value="${esc(csrf)}">
<label for="label">名稱（方便辨認）</label><input id="label" name="label" maxlength="80" placeholder="例如：每日匯出指令碼" required>
<label for="days">有效期</label><select id="days" name="days"><option value="30">30 日</option><option value="90">90 日</option><option value="365" selected>1 年</option><option value="0">永不過期</option></select>
<p><button>建立權杖</button></p></form></div>
<div class="card"><h2>你的權杖</h2>${items ? `<ul class="list">${items}</ul>` : '<p class="muted">未有權杖。</p>'}</div>`;
}

export function callsPage(calls: Record<string, unknown>[]) {
  const log = calls.map(c => `<li><small>${fmtEn(String(c.at))} · ${esc(c.email)} · ${esc(c.via)} · <b>${esc(c.tool)}</b> · ${esc(c.status)} · ${esc(c.ms)} ms${c.client ? " · " + esc(c.client) : ""}</small></li>`).join("");
  return `<div class="card"><h1>YYC data API: call log</h1><p class="muted">Every MCP and REST call, newest first. Who may call is YYC's access catalogue (its <code>users</code>); a removal there stops that person's tokens within a minute or two.</p></div>
<div class="card"><h2>Recent calls</h2>${log ? `<ul class="list">${log}</ul>` : '<p class="muted">None yet.</p>'}<p><a href="/admin/api/audit?limit=500">All (JSON)</a></p></div>`;
}

/** the front page (/): the REST API, for scripts and tools, with a personal token */
export function landing(base: string) {
  return `<div class="card"><h1>YYC 新聞監察資料 API</h1>
<p>以程式讀取 YYC 新聞監察收集的帖子、串流、數據分析、報告，以及慧科（WiseSearch）。全部唯讀。YYC AI 已內置這些資料，毋須另外設定。</p>
<ol><li>在 <a href="/tokens">個人存取權杖</a> 建立一個權杖（只限 YYC 同事）。</li>
<li>每個工具都是 <code>GET</code> 或 <code>POST ${esc(base)}/api/v1/&lt;工具&gt;</code>，用標頭 <code>Authorization: Bearer yyc_pat_…</code>；說明見 <a href="/api/v1/openapi.json">OpenAPI</a>。</li></ol>
<pre class="mono">curl -H "Authorization: Bearer yyc_pat_…" "${esc(base)}/api/v1/posts_search?query=施政報告"</pre>
<p class="muted">每次使用都會記錄（帳戶、工具、時間），作審計之用。慧科每次搜尋會用 YYC 的配額。</p></div>
<div class="card"><p class="muted"><a href="/privacy">私隱及條款</a></p></div>`;
}

export function privacy() {
  return `<div class="card"><h1>私隱及使用條款</h1>
<p>此服務屬 YYC，只供 YYC 同事使用。</p>
<p>登入時（Google 或電郵驗證碼），我們只取得你的電郵地址和名稱，用來判斷你是否獲准使用。我們記錄每次呼叫（帳戶、工具、時間、參數），作審計之用，保存一年。我們不會把這些資料交給第三方。</p>
<p>服務全部唯讀：只提供 YYC 新聞監察已收集的資料，不會修改任何資料。</p>
<p>如要刪除你的使用記錄，請聯絡 Deyao（chendeyao000@gmail.com）。</p></div>`;
}
