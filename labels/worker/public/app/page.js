// The staff pages (labels/README.md "Pages"): the templates (each with its four example labels) and a print request's
// page, where staff connect the NIIMBOT M3 over Web Bluetooth (lib/print.js) and print the labels YYC AI sent, or
// cancel and tell YYC AI in the chat what to change.
import { Printer } from "/lib/print.js";

const W = 848, H = 590, BYTES = 62540;
const $ = (q) => document.querySelector(q);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const when = (iso) => {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString("zh-HK", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
};
const BACK_TO_CHAT = "請返回與 YYC AI 的對話，告訴它你想修改甚麼。";

async function api(path, init) {
  const r = await fetch(`/api/v2/${path}`, { cache: "no-store", ...init });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).message || `${r.status}`);
  return r;
}

// one label's dots → a PNG URL to show
function picture(bits) {
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const g = c.getContext("2d");
  const img = g.createImageData(W, H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const on = bits[y * 106 + (x >> 3)] & (0x80 >> (x & 7));
    const i = (y * W + x) * 4;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = on ? 0 : 255;
    img.data[i + 3] = 255;
  }
  g.putImageData(img, 0, 0);
  return new Promise((ok) => c.toBlob((b) => ok(URL.createObjectURL(b)), "image/png"));
}

async function bitsOf(path) {
  const b = new Uint8Array(await (await api(path)).arrayBuffer());
  if (b.byteLength !== BYTES) throw new Error("標籤圖像大小不符");
  return b;
}

function live(onChange) {
  let ws, timer;
  const open = () => {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/live`);
    ws.onmessage = (m) => { try { onChange(JSON.parse(m.data)); } catch { /* pong */ } };
    ws.onclose = () => { clearInterval(timer); setTimeout(open, 3000); };
    timer = setInterval(() => { try { ws.send("ping"); } catch { /* closed */ } }, 30000);
  };
  open();
}

// ---- the templates ------------------------------------------------------------------------------------------------
const pics = new Map(); // "<template>@<version>/<n>" → URL

async function templates() {
  document.title = "標籤範本";
  let list = [], error = null;
  try { list = await (await api("templates")).json(); } catch (e) { error = e.message; }
  $("#app").innerHTML = `<header class="bar"><div class="bar-in"><h1>標籤範本</h1></div></header><main>
    <div class="status info"><span class="grow">要打印標籤或修改範本，請在 YYC AI 的對話中說明。YYC AI 準備好標籤後，會給你一條打印連結。</span></div>
    ${error ? `<div class="status bad"><span class="grow">未能載入範本：${esc(error)}</span></div>` : ""}
    ${list.length ? `<div class="templates">${list.map((t) => `<section class="template">
      <div class="pics">${[1, 2, 3, 4].map((n) => `<img class="lbl" alt="${esc(t.name)} 範例 ${n}" data-t="${esc(t.id)}" data-v="${t.version}" data-n="${n}" hidden><div class="blank" data-blank="${esc(t.id)}-${n}"></div>`).join("")}</div>
      <div class="txt"><h2>${esc(t.name)}</h2><div class="note">${esc(t.updatedBy)} 於 ${when(t.updatedAt)} 修改</div></div>
      ${t.lock ? `<div class="lock">${esc(t.lock.holder)} 正在修改此範本（${when(t.lock.since)} 起）</div>` : ""}
    </section>`).join("")}</div>` : error ? "" : `<div class="empty">未有範本。請 YYC AI 製作一個，例如「為論壇嘉賓製作名牌範本」。</div>`}</main>`;
  for (const img of document.querySelectorAll("img[data-t]")) {
    const key = `${img.dataset.t}@${img.dataset.v}/${img.dataset.n}`;
    try {
      if (!pics.has(key)) pics.set(key, await picture(await bitsOf(`templates/${img.dataset.t}/files/examples/${img.dataset.n}.bin`)));
      img.src = pics.get(key);
      img.hidden = false;
      document.querySelector(`[data-blank="${img.dataset.t}-${img.dataset.n}"]`)?.remove();
    } catch { /* stays blank */ }
  }
}

// ---- a print request ----------------------------------------------------------------------------------------------
const s = { id: null, p: null, error: null, printer: null, run: null, urls: [], bits: [] };

async function loadPrint() {
  s.p = await (await api(`prints/${s.id}`)).json();
}

async function report(body) {
  try { s.p = await (await api(`prints/${s.id}/status`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json(); }
  catch (e) { s.error = `未能記錄進度：${e.message}`; }
}

function printPage() {
  const p = s.p;
  if (!p) {
    $("#app").innerHTML = `<main><div class="status bad"><span class="grow">${esc(s.error || "找不到此打印要求")}</span></div></main>`;
    return;
  }
  document.title = `${p.title || "打印標籤"} - 標籤`;
  const run = s.run, busy = run && !run.done && !run.error;
  const connected = s.printer?.connected;
  const left = p.labels - p.printed;
  let body;
  if (p.status === "cancelled") {
    body = `<div class="ended">已取消，沒有打印${p.printed ? `餘下的 ${left} 張` : ""}。${BACK_TO_CHAT}</div>`;
  } else if (p.status === "printed") {
    body = `<div class="ended ok">已打印全部 ${p.labels} 張。</div>`;
  } else {
    const status = [];
    if (s.error) status.push(`<div class="status bad"><span class="grow">${esc(s.error)}</span></div>`);
    if (!navigator.bluetooth) status.push(`<div class="status warn"><span class="grow">此瀏覽器不能連接打印機：請用電腦上的 Chrome 或 Edge 打開此頁。</span></div>`);
    const printWhat = run?.error ? `打印失敗：${esc(run.error)}` : busy ? `打印中：${p.printed} / ${p.labels} 張` :
      p.printed ? `已打印 ${p.printed} 張，餘下 ${left} 張` : `打印 ${p.labels} 張標籤`;
    const bar = busy ? `<div class="prog"><div class="prog-bar"><i id="prog-bar" style="width:${progressPct()}%"></i></div>
        <span id="prog-text">${esc(progressText())}</span></div>` : "";
    body = `${status.join("")}<div class="steps">
      <div class="step ${connected ? "done" : ""}"><span class="n">1</span><div class="what"><b>${connected ? `已連接 ${esc(s.printer.name)}` : "連接打印機"}</b>
        <span>${connected ? "打印機已準備好。" : "打開打印機，按「連接打印機」，在彈出的視窗中選擇 M3。"}</span></div>
        ${connected ? "" : `<button id="connect" class="primary" ${navigator.bluetooth ? "" : "disabled"}>連接打印機</button>`}</div>
      <div class="step ${connected ? "" : "off"}"><span class="n">2</span><div class="what"><b>${printWhat}</b>
        <span>${busy ? "請勿關閉此頁，直至打印完成。" : "按次序逐張打印，完成後此頁會顯示。"}</span></div>
        ${busy ? `<button id="stop">停止</button>` : `<button id="print" class="primary" ${connected ? "" : "disabled"}>${p.printed ? `由第 ${p.printed + 1} 張繼續` : "打印"}</button>`}
        ${bar}</div>
      <div class="cancel-row"><button id="cancel" ${busy ? "disabled" : ""}>取消，不打印</button></div>
    </div>`;
  }
  $("#app").innerHTML = `<header class="bar"><div class="bar-in"><h1>${esc(p.title || "打印標籤")}</h1>
      <span class="note">${p.labels} 張，${esc(p.createdBy)} 於 ${when(p.createdAt)} 送出</span></div></header>
    <main>${body}<div class="labels">${Array.from({ length: p.labels }, (_, i) => `<div class="label ${i < p.printed ? "done" : ""}">
      <div class="pic">${s.urls[i] ? `<img class="lbl" alt="第 ${i + 1} 張標籤" src="${s.urls[i]}">` : `<div class="blank"></div>`}</div>
      <div class="cap"><span class="num">${i + 1}</span></div></div>`).join("")}</div></main>`;
  const on = (q, f) => { const b = $(q); if (b) b.onclick = f; };
  on("#connect", async () => {
    try { s.printer = await Printer.connect(); s.printer.device.addEventListener("gattserverdisconnected", printPage); s.error = null; }
    catch (e) { if (e.name !== "NotFoundError") s.error = `未能連接打印機：${e.message}`; }
    printPage();
  });
  on("#print", () => print());
  on("#stop", () => { s.run.stop = true; });
  on("#cancel", async () => { await report({ status: "cancelled" }); printPage(); });
}

// Progress of the run: which label, and where that label is (s.run.prog from Printer.printLabel's onProgress). The bar
// covers the whole run; each label is a tenth preparing, half sending, the rest the printer printing and feeding.
function progressFrac() {
  const g = s.run?.prog;
  if (!g) return 0;
  return g.phase === "prepare" ? 0 : g.phase === "send" ? 0.1 + 0.5 * (g.pct / 100)
    : g.phase === "print" ? 0.6 + 0.4 * ((g.pct + g.feed) / 200) : 1;
}
function progressPct() {
  const r = s.run;
  if (!r?.label) return 0;
  const done = r.label - r.first, total = s.p.labels - r.first + 1;
  return Math.round(((done + progressFrac()) / total) * 100);
}
function progressText() {
  const r = s.run, g = r?.prog;
  if (!g) return "開始中…";
  const which = `第 ${r.label} / ${s.p.labels} 張`;
  if (g.phase === "prepare") return `${which}：準備打印機…`;
  if (g.phase === "send") return `${which}：傳送到打印機 ${g.pct}%（傳送完才開始出紙）`;
  if (g.phase === "print") return `${which}：打印中 ${g.pct}%，出紙 ${g.feed}%`;
  return `${which}：完成，準備下一張`;
}
function showProgress(prog) {
  s.run.prog = prog;
  const bar = $("#prog-bar"), text = $("#prog-text");
  if (bar) bar.style.width = `${progressPct()}%`;
  if (text) text.textContent = progressText();
}

// Each label is its own print job, in order, from the first not yet printed; a failure stops the run and it can go on
// from that label.
async function print() {
  s.error = null;
  s.run = { stop: false, done: false, error: null, first: s.p.printed + 1, label: s.p.printed + 1, prog: null };
  printPage();
  for (let n = s.p.printed + 1; n <= s.p.labels; n++) {
    if (s.p.status === "cancelled") break;
    if (s.run.stop) { s.run.error = "已停止"; break; }
    s.run.label = n; showProgress(null);
    try {
      s.bits[n - 1] ??= await bitsOf(`prints/${s.id}/labels/${n}.bin`);
      await s.printer.printLabel({ width: W, height: H, bits: s.bits[n - 1] }, { density: s.p.density, onProgress: showProgress });
    } catch (e) {
      s.run.error = `第 ${n} 張：${e.message}`;
      break;
    }
    await report({ printed: n });
    printPage();
  }
  if (!s.run.error) s.run.done = true;
  printPage();
}

async function main() {
  const m = location.pathname.match(/^\/p\/([0-9a-f]{16})$/);
  if (!m) {
    await templates();
    live((ev) => { if (ev.kind === "template" || ev.kind === "lock") templates(); });
    return;
  }
  s.id = m[1];
  $("#app").innerHTML = `<main><div class="status info">載入中…</div></main>`;
  try { await loadPrint(); } catch (e) { s.error = e.message; }
  printPage();
  if (!s.p) return;
  // Cancelled or printed elsewhere (YYC AI through the API, another tab): redraw; a run in progress stops after the
  // label the printer is on.
  live(async (ev) => {
    if (ev.kind !== "print" || ev.id !== s.id) return;
    try { await loadPrint(); } catch { return; }
    printPage();
  });
  for (let i = 0; i < s.p.labels; i++) {
    try {
      s.bits[i] = await bitsOf(`prints/${s.id}/labels/${i + 1}.bin`);
      s.urls[i] = await picture(s.bits[i]);
      const box = document.querySelectorAll(".label .pic")[i];
      if (box) box.innerHTML = `<img class="lbl" alt="第 ${i + 1} 張標籤" src="${s.urls[i]}">`;
    } catch { /* stays blank */ }
  }
}
main();
