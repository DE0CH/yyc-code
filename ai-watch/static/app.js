// AI 觀察: the case list, a new case, and one case. Plain JS over the JSON API (aiwatch/web.py). Traditional Chinese
// (HK) throughout (YYC CLAUDE.md: staff read it most easily). No toasts or pop-ups: state shows in place with SVG icons.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// a read is answered from the nearest Cloudflare location's last copy when it has one (edge/worker.js), marked `edge`;
// `fresh` asks the origin itself
// `reads` is the current page's: leaving the page cancels its reads still under way
const EDGE = new WeakSet();
let reads = new AbortController();
const api = async (path, body, fresh) => {
  const r = await fetch(path, body === undefined ? { headers: fresh ? { "X-Fresh": "1" } : {}, signal: reads.signal }
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.status);
  if (r.headers.has("X-Edge-Copy")) EDGE.add(j);
  return j;
};
// a page's reads: a navigation the browser has no copy for takes the edge's copy first (drawn by `draw`), then
// everything asks the origin; the origin's answer is what the page keeps
async function load(paths, my, mode, draw) {
  if (!mode && draw) {
    const first = await Promise.all(paths.map((p) => api(p))).catch(() => null);
    if (my !== seq) return null;
    if (first && !first.some((j) => EDGE.has(j))) return first;
    if (first) draw(first);
  }
  const r = await Promise.all(paths.map((p) => api(p, undefined, true)));
  return my === seq ? r : null;
}

// one icon set: 24 viewBox, 2px stroke
const I = {
  spin: '<svg class="icon spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9"/></svg>',
  warn: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3 2 21h20L12 3z"/><path d="M12 10v5M12 18v.5"/></svg>',
  dash: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M7 12h10"/></svg>',
  wait: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="2 3" aria-hidden="true"><circle cx="12" cy="12" r="7"/></svg>',
  lock: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="1"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
  check: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>',
  split: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3v7M12 10 5 21M12 10l7 11"/></svg>',
};

const KIND = { event: "事件", person: "人物", company: "公司" };
const ANGLE = { positive: "正面", negative: "負面", neutral: "中立查證", base: "基本" };
const SUPPORT = { supported: ["來源支持", "pos"], partial: ["來源只部分支持", ""], unsupported: ["引用的網頁沒有這樣說", "neg"], unread: ["來源未能核對", ""] };
const STATUS = { news: "搜尋近期新聞", questions: "整理議題和問題", ready: "等你揀選問題", asking: "正在問 AI", analysing: "正在分析", done: "完成", failed: "失敗" };
const level = (r) => (r >= 65 ? "高" : r >= 40 ? "中" : "低");
const toneWord = (t) => (t > 0 ? "正面" : t < 0 ? "負面" : "中性");
let timer = null, ENG = {}, SIGNED = {}, tab = "overview", openCell = null;
// The last copy of each page's data, so a click shows the page at once and the fresh copy replaces it when it lands.
// `seq` numbers the navigations: a response for a page the user has already left is dropped.
const CACHE = { cases: null, note: "", case: {} }, drafts = {};
let seq = 0, tabSeq = 0, curD = null, acting = 0;

// The address holds the view, so a reload or a shared link opens the same thing: /case/<id>/<tab>?q=<question>
// &view=<graph view>&open=<answer>. A tab change is a history entry (Back returns to the previous tab); a change inside
// a tab replaces the address.
const TABS = ["overview", "tree", "risk", "agree", "sources", "graph", "answers", "forums", "more"];
function readUrl() {
  const m = location.pathname.match(/^\/case\/(\d+)(?:\/(\w+))?/);
  const p = new URLSearchParams(location.search);
  tab = m && TABS.includes(m[2]) ? m[2] : "overview";
  answersQ = +p.get("q") || null; openCell = +p.get("open") || null;
  graphView = p.get("view") === "trace" ? "trace" : "topic";
  return m;
}
function caseUrl(id) {
  const p = new URLSearchParams();
  if (tab === "answers" && answersQ) p.set("q", answersQ);
  if (tab === "graph" && graphView !== "topic") p.set("view", graphView);
  if (tab === "overview" && openCell) p.set("open", openCell);
  return `/case/${id}${tab === "overview" ? "" : "/" + tab}${p.toString() ? "?" + p : ""}`;
}
function syncUrl(id, push) {
  const u = caseUrl(id);
  if (u !== location.pathname + location.search) history[push ? "pushState" : "replaceState"](null, "", u);
}

// mode: undefined = a navigation (the cached copy or a placeholder at once, then the fresh one); "after" = after an
// action (the page stays, its button spinning, until the fresh copy); "poll" = the background refresh (keeps the page
// on an error). Outside "after", a fresh copy is held back while a button's action runs or the user types in the case
// page, and tried again in 8 s.
async function route(mode) {
  clearTimeout(timer);
  reads.abort(); reads = new AbortController();
  const m = readUrl(), my = ++seq;
  if (!mode) placeholder(m);
  try { if (m) await casePage(+m[1], my, mode); else await home(my, mode); }
  catch (e) {
    if (my !== seq) return;
    if (mode === "poll") { timer = setTimeout(() => route("poll"), 8000); return; }
    $("#app").innerHTML = `<p class="err">${I.warn} 載入失敗：${esc(e.message)}</p>`;
  }
}
window.addEventListener("popstate", () => route());
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-nav]");
  if (a) { e.preventDefault(); history.pushState(null, "", a.getAttribute("href")); route(); return; }
  const g = e.target.closest("[data-tab-go]");
  if (g && curD) setTab(g.dataset.tabGo, curD);
});

// shimmer placeholders: one line of text each, in the shapes of the page that is coming
const sk = (w, h = 14) => `<span class="sk" style="width:${w};height:${h}px"></span>`;
const skLines = (...ws) => ws.map((w) => sk(w)).join("");
function skelTab() {
  return `<div class="skel dashwrap" aria-busy="true"><span class="vh">載入中</span><div class="dash">
    <div class="dpanel s12">${sk("38%", 20)}${skLines("100%", "94%", "97%", "88%", "92%", "60%")}</div>
    <div class="dpanel s7">${sk("44%", 20)}${skLines("100%", "90%", "72%")}</div>
    <div class="dpanel s5">${sk("52%", 20)}${skLines("100%", "80%", "66%")}</div></div></div>`;
}
function skelSide(id) {
  const cs = CACHE.cases;
  return `<aside class="side"><a class="btn new" data-nav href="/" ${id ? "" : 'aria-current="page"'}>新個案</a>
    ${id ? `<h4>這個個案</h4><div class="skel">${skLines("80%", "64%", "72%", "58%", "70%")}</div>` : ""}
    <h4>所有個案</h4><div class="cases">${cs ? cs.map((x) => `<a data-nav href="/case/${x.id}" ${x.id === id ? 'aria-current="page"' : ""}><span>${esc(x.subject)}</span><small></small></a>`).join("")
      : `<div class="skel">${skLines("90%", "76%", "84%")}</div>`}</div></aside>`;
}
function placeholder(m) {
  const id = m ? +m[1] : null;
  if (id && CACHE.case[id]) return drawCase(CACHE.case[id]);
  if (!id && CACHE.cases) return drawHome(CACHE.cases, CACHE.note);
  const head = id ? `<div class="skel">${sk("55%", 34)}${sk("36%")}</div><div class="facts skel">${[1, 2, 3, 4, 5].map(() => `<div class="fact">${sk("48px", 22)}${sk("84px", 12)}</div>`).join("")}</div>`
    : `<div class="skel">${sk("40%", 34)}${sk("72%")}</div>`;
  $("#app").innerHTML = `<div class="frame">${skelSide(id)}<div class="content">${head}${skelTab()}</div></div>`;
  window.scrollTo(0, 0);
}

// a button that starts something on the server: it spins at once; on an error it comes back and says why beside itself
async function run(b, label, fn) {
  const was = b.innerHTML;
  acting++;
  b.disabled = true; b.innerHTML = `${I.spin} ${label}`;
  b.parentNode.querySelector(".aerr")?.remove();
  try { await fn(); }
  catch (e) { b.disabled = false; b.innerHTML = was; b.insertAdjacentHTML("afterend", `<span class="aerr small err" role="status">${I.warn} ${esc(e.message)}</span>`); }
  finally { acting--; }
}
const typing = () => document.activeElement?.matches("textarea, input[type=text], select");

function statusNote(s) {
  ENG = s.labels; SIGNED = s.signed_in || {};
  $("#spend").textContent = `本月分析費用 USD ${Number(s.month_usd).toFixed(2)}`;
  const bad = s.engines.filter((e) => e.status !== "ok" && ENG[e.engine] && SIGNED[e.engine] !== false || e.engine === "model" && e.status !== "ok");
  const say = { login_needed: "需要重新登入", blocked: "被網站攔截，稍後自動重試", key_limit: "本月分析額度已用完" };
  return bad.length ? `<p class="note">${bad.map((e) => `${esc(s.labels[e.engine] || "分析模型")}${say[e.status] || esc(e.status)}`).join("；")}</p>` : "";
}

// ---------------------------------------------------------------- home

// the same frame as a case: the sidebar (新個案, every case), then the form beside a dense table of the cases
async function home(my, mode) {
  const r = await load(["/api/cases", "/api/status"], my, mode, CACHE.cases ? null : ([cases, s]) => {
    CACHE.cases = cases; CACHE.note = statusNote(s); drawHome(cases, CACHE.note); });
  if (!r) return;
  const [cases, s] = r, note = statusNote(s);
  CACHE.cases = cases; CACHE.note = note;
  if (mode !== "after" && acting) { timer = setTimeout(() => route("poll"), 8000); return; }
  drawHome(cases, note);
  if (cases.some((x) => isLive(x.status))) timer = setTimeout(() => route("poll"), 8000);
}

const isLive = (st) => ["news", "questions", "asking", "analysing"].includes(st);
// a redraw of a home page already on screen replaces only the lists, so the form keeps what is typed in it
function drawHome(cases, note) {
  const live = isLive;
  const engs = Object.entries(ENG).map(([k, v]) => { const off = SIGNED[k] === false;
    return `<label class="chip"><input type="checkbox" name="eng" value="${k}" ${off ? "" : "checked"}>${esc(v)}${off ? ` <span class="muted small">${I.lock} 未登入</span>` : ""}</label>`; }).join("");
  const sideCases = cases.map((x) => `<a data-nav href="/case/${x.id}"><span>${esc(x.subject)}</span><small>${live(x.status) ? I.spin : ""}</small></a>`).join("");
  const side = `<aside class="side"><a class="btn new" data-nav href="/" aria-current="page">新個案</a>
    <h4>所有個案</h4><div class="cases">${sideCases}</div></aside>`;
  const num = (k, v, cls = "") => `<span class="n ${cls}"><span class="k">${k}</span>${v ?? "–"}</span>`;
  const rows = cases.map((c) => { const s = c.summary || {}; const hi = s.risk_levels?.["高"] || 0;
    return `<li><a class="crow" data-nav href="/case/${c.id}">
      <span class="subj"><b>${esc(c.subject)}</b><small>${KIND[c.kind] || ""}，${c.engines.length} 個應用程式，${new Date(c.created_at).toLocaleDateString("zh-HK")}</small></span>
      <span class="st ${c.status === "failed" ? "err" : ""}">${live(c.status) ? I.spin : c.status === "done" ? I.check : c.status === "failed" ? I.warn : ""}${STATUS[c.status] || c.status}</span>
      ${num("回答", s.answers)}${num("說法", s.claims)}${num("高風險", s.answers ? (hi ? `<mark>${hi}</mark>` : 0) : null, "hi")}</a></li>`; }).join("");
  const list = `<div class="phead"><h2 id="cl">所有個案</h2><span class="muted small">${cases.length} 個</span></div>
        ${cases.length ? `<div class="chead" aria-hidden="true"><span>題目</span><span>狀態</span><span class="r">回答</span><span class="r">說法</span><span class="r">高風險</span></div>
        <ul class="clist">${rows}</ul>` : '<p class="muted">未有個案。在左邊輸入第一個題目。</p>'}`;
  if ($("#subject") && $("#caselist")) { $("#note").innerHTML = note; $(".side .cases").innerHTML = sideCases; $("#caselist").innerHTML = list; return; }
  $("#app").innerHTML = `<div class="frame">${side}<div class="content"><div id="note">${note}</div>
    <h1>問 AI 怎樣說</h1>
    <p class="lede">輸入一個事件、人物或公司。系統會找出近期新聞，從不同持份者的角度提問，再問香港人常用的 AI 應用程式，比較它們說甚麼、引用誰、哪裡互相矛盾。</p>
    <div class="homegrid">
      <section class="dpanel newcase" aria-labelledby="nc"><h2 id="nc">新個案</h2>
        <label class="row-label" for="subject">題目</label>
        <input type="text" class="subject" id="subject" placeholder="例如：大埔宏福苑大火">
        <fieldset><legend class="row-label">類別</legend><div class="seg">
          <label><input type="radio" name="kind" value="event" checked>事件</label>
          <label><input type="radio" name="kind" value="person">人物</label>
          <label><input type="radio" name="kind" value="company">公司</label></div></fieldset>
        <label class="row-label" for="context">想集中看甚麼（選填）</label>
        <textarea id="context" placeholder="例如：客戶是承建商，想知道 AI 怎樣描述業界責任"></textarea>
        <fieldset><legend class="row-label">問哪些應用程式</legend><div class="chips">${engs}</div></fieldset>
        <div class="actions"><button class="btn" id="go">開始</button><span id="msg" class="small err" role="status"></span></div>
      </section>
      <section class="dpanel" id="caselist" aria-labelledby="cl">${list}</section></div></div></div>`;
  $("#go").onclick = async () => {
    const subject = $("#subject").value.trim();
    if (!subject) { $("#msg").textContent = "請先輸入題目"; $("#subject").focus(); return; }
    $("#go").disabled = true; $("#go").innerHTML = `${I.spin} 開始`;
    try {
      const r = await api("/api/cases", { subject, kind: $("input[name=kind]:checked").value, context: $("#context").value,
        engines: $$("input[name=eng]:checked").map((x) => x.value) });
      history.pushState(null, "", `/case/${r.id}`); route();
    } catch (e) { $("#go").disabled = false; $("#go").textContent = "開始"; $("#msg").textContent = e.message; }
  };
}

// ---------------------------------------------------------------- one case

async function casePage(id, my, mode) {
  const r = await load([`/api/cases/${id}`, "/api/status", "/api/cases"], my, mode, CACHE.case[id] ? null : ([d, s, cases]) => {
    CACHE.case[id] = { d, note: statusNote(s), cases }; CACHE.cases = cases; drawCase(CACHE.case[id]); });
  if (!r) return;
  const [d, s, cases] = r, note = statusNote(s);
  CACHE.case[id] = { d, note, cases }; CACHE.cases = cases; CACHE.note = note;
  if (mode !== "after" && (acting || typing())) { timer = setTimeout(() => route("poll"), 8000); return; }
  drawCase(CACHE.case[id]);
  const sideJobs = d.tasks.some((t) => ["forums", "suggest"].includes(t.kind) && ["queued", "running"].includes(t.status));
  if (isLive(d.case.status) || sideJobs) timer = setTimeout(() => route("poll"), 8000);
}

function drawCase({ d, note, cases }) {
  const c = d.case, s = c.summary || {};
  const asks = d.asks;
  const running = ["news", "questions", "asking", "analysing"].includes(c.status);
  const pendingAnalysis = d.tasks.filter((t) => ["extract", "sources", "group", "merge", "summary"].includes(t.kind) && ["queued", "running"].includes(t.status)).reduce((n, t) => n + +t.n, 0);
  let body;
  if (c.status === "news" || c.status === "questions") {
    body = `<p class="progress">${I.spin} ${c.status === "news" ? "正在搜尋近 60 天的新聞" : "正在整理近期議題，並從不同持份者的角度提問"}</p>`;
  } else if (c.status === "ready") {
    body = questionPicker(d);
  } else {
    const answered = asks.filter((a) => a.status !== "queued").length;
    body = `${running ? `<div class="progress">${I.spin}<span>${STATUS[c.status]}：回答 ${answered}/${asks.length}${pendingAnalysis ? `，待分析 ${pendingAnalysis} 項` : ""}</span>
        <span class="track"><i style="width:${Math.round(100 * answered / (asks.length || 1))}%"></i></span></div>` : ""}
      <div class="tabs mobile" role="tablist">${tabList(d).map(([k, v]) => `<button role="tab" data-tab="${k}" aria-selected="${tab === k}">${v}</button>`).join("")}</div>
      <div id="tab">${renderTab(d)}</div>`;
  }
  const hasTabs = !["news", "questions", "ready"].includes(c.status);
  const side = `<aside class="side"><a class="btn new" data-nav href="/">新個案</a>
    ${hasTabs ? `<h4>這個個案</h4><nav aria-label="個案內容">${tabList(d).map(([k, v]) => `<button data-tab="${k}" aria-selected="${tab === k}">${v}</button>`).join("")}</nav>` : ""}
    <h4>所有個案</h4><div class="cases">${cases.map((x) => `<a data-nav href="/case/${x.id}" ${x.id === c.id ? 'aria-current="page"' : ""}><span>${esc(x.subject)}</span><small>${["news", "questions", "asking", "analysing"].includes(x.status) ? I.spin : ""}</small></a>`).join("")}</div></aside>`;
  $("#app").innerHTML = `<div class="frame">${side}<div class="content">${note}<a class="back mobile-only" data-nav href="/">所有個案</a>
    <h1>${esc(c.subject)}</h1>
    <div class="meta"><span>${KIND[c.kind]}</span><span>${running ? I.spin + " " : ""}${STATUS[c.status] || c.status}</span>
      <span>${c.engines.length} 個應用程式</span><span>分析費用 USD ${Number(d.cost).toFixed(2)}</span></div>
    ${c.context ? `<p class="lede" style="margin-top:8px">${esc(c.context)}</p>` : ""}
    ${s.answers ? facts(s) : ""}
    ${body}</div></div>`;
  $$("[data-tab]").forEach((b) => (b.onclick = () => setTab(b.dataset.tab, d)));
  wire(d);
}

// a tab change marks the tab and shows a placeholder in the same frame, then draws the tab in the next one
function setTab(t, d) {
  tab = t; syncUrl(d.case.id, true);
  $$("[data-tab]").forEach((x) => x.setAttribute("aria-selected", x.dataset.tab === tab));
  if (!$("#tab")) return;
  $("#tab").innerHTML = skelTab(); window.scrollTo(0, 0);
  const my = ++tabSeq;
  requestAnimationFrame(() => setTimeout(() => { if (my === tabSeq && curD === d) redraw(d); }, 0));
}

function tabList(d) {
  return [["overview", "總覽"], ["tree", "問題樹"], ["risk", `需要處理${d.groups.length ? `（${d.groups.length}）` : ""}`], ["agree", "共同與分歧"],
    ["sources", `來源${d.sources.length ? `（${d.sources.length}）` : ""}`], ["graph", "圖譜"], ["answers", "回答"], ["forums", "論壇"], ["more", "追問"]];
}

function facts(s) {
  const f = [[s.answers, "個回答"], [`${s.mention_rate}%`, "回答有提及對象"], [`${s.cited_rate}%`, "回答有引用網址"],
    [`${s.official_share}%`, "引用屬官方網站"], [s.claims, "條說法"],
    ...(s.risk_levels ? [[s.risk_levels["高"] ? `<mark>${s.risk_levels["高"]}</mark>` : 0, "條高風險說法"]] : []),
    [s.contradict, "條說法互相矛盾"], [(s.net_tone_answers > 0 ? "+" : "") + s.net_tone_answers, "回答淨語氣（−100 至 +100）"]];
  return `<div class="facts">${f.map(([b, t]) => `<div class="fact"><b>${b}</b><span>${t}</span></div>`).join("")}</div>`;
}

function questionPicker(d) {
  const by = {};
  d.questions.forEach((q) => { (by[q.stakeholder || "其他"] = by[q.stakeholder || "其他"] || []).push(q); });
  return `<h2>近期議題</h2>
    ${d.issues.length ? `<ul class="issues">${d.issues.map((i) => `<li><b>${esc(i.title)}</b><br><span class="small">${esc(i.summary)}</span> <span class="muted small">${i.latest || ""}</span></li>`).join("")}</ul>` : '<p class="muted">近 60 天找不到相關新聞，問題按一般已知的資料提出。</p>'}
    <h2>要問的問題</h2>
    <p class="muted small">全部已勾選。取消不想問的；問完後可在「追問」加入自己的問題。</p>
    ${Object.entries(by).map(([who, qs]) => `<div class="qgroup"><h3>${esc(who)}</h3>
      ${qs.map((q) => `<label class="qpick"><input type="checkbox" name="q" value="${q.id}" checked><span class="t">${esc(q.text)}<span class="angle">${ANGLE[q.angle] || ""}</span></span></label>`).join("")}</div>`).join("")}
    <div class="sticky-ask"><div class="actions" style="margin:0"><button class="btn" id="ask">問 AI</button>
      <button class="btn quiet" id="regen">另擬一組問題</button><span id="cnt" class="small muted"></span></div></div>`;
}

function wire(d) {
  const c = d.case;
  curD = d;
  const cnt = () => { const n = $$("input[name=q]:checked").length; if ($("#cnt")) $("#cnt").textContent = `${n} 條問題 × ${c.engines.length} 個應用程式，共 ${n * c.engines.length} 次提問`; };
  $$("input[name=q]").forEach((x) => (x.onchange = cnt)); cnt();
  const post = (b, label, path, body = {}) => run(b, label, async () => { await api(`/api/cases/${c.id}/${path}`, body); await route("after"); });
  if ($("#ask")) $("#ask").onclick = (e) => {
    const ids = $$("input[name=q]:checked").map((x) => +x.value);
    if (!ids.length) { $("#cnt").textContent = "請最少勾選一條問題"; return; }
    post(e.currentTarget, "問 AI", "ask", { question_ids: ids });
  };
  if ($("#regen")) $("#regen").onclick = (e) => post(e.currentTarget, "另擬一組問題", "questions");
  if ($("#followq")) { $("#followq").value = drafts[c.id] || ""; $("#followq").oninput = (e) => { drafts[c.id] = e.target.value; }; }
  if ($("#follow")) $("#follow").onclick = (e) => {
    const qs = $("#followq").value.split("\n").map((x) => x.trim()).filter(Boolean);
    if (!qs.length) { $("#followq").focus(); return; }
    run(e.currentTarget, "追問", async () => { await api(`/api/cases/${c.id}/followup`, { questions: qs }); delete drafts[c.id];
      tab = "overview"; syncUrl(c.id, true); await route("after"); window.scrollTo(0, 0); });
  };
  if ($("#retry")) $("#retry").onclick = (e) => post(e.currentTarget, "重問失敗的問題", "retry");
  if ($("#del")) $("#del").onclick = (e) => {
    const b = e.currentTarget;
    if (!b.dataset.sure) { b.dataset.sure = 1; b.textContent = "再按一次就會刪除"; return; }
    run(b, "刪除", async () => { await api(`/api/cases/${c.id}/delete`, {}); delete CACHE.case[c.id];
      if (CACHE.cases) CACHE.cases = CACHE.cases.filter((x) => x.id !== c.id); history.pushState(null, "", "/"); route(); });
  };
  $$(".cell[data-ask]").forEach((b) => (b.onclick = () => { openCell = openCell === +b.dataset.ask ? null : +b.dataset.ask; syncUrl(c.id); redraw(d); }));
  $$("[data-goq]").forEach((b) => (b.onclick = () => { answersQ = +b.dataset.goq; setTab("answers", d); }));
  if ($("#qsel")) $("#qsel").onchange = (e) => { answersQ = +e.target.value; syncUrl(c.id); redraw(d); };
  if ($("#coonly")) $("#coonly").onchange = (e) => { coOnly = e.target.checked; redraw(d); };
  $$(".drow").forEach((r) => (r.onclick = () => { openDomain = openDomain === r.dataset.dom ? null : r.dataset.dom; redraw(d); }));
  $$("[data-gview]").forEach((b) => (b.onclick = () => { graphView = b.dataset.gview; graphPin = null; syncUrl(c.id); redraw(d); }));
  $$("[data-node], [data-pin]").forEach((n) => { const go = () => { const id = n.dataset.node || n.dataset.pin; graphPin = graphPin === id ? null : id; redraw(d); };
    n.onclick = go; n.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } }; });
  if ($("#unpin")) $("#unpin").onclick = () => { graphPin = null; redraw(d); };
  $$("[data-forum]").forEach((b) => (b.onclick = () => { $$("[data-forum]").forEach((x) => x !== b && (x.disabled = true));
    post(b, b.textContent, "forums", { span: b.dataset.forum }).finally(() => $$("[data-forum]").forEach((x) => x.isConnected && !x.querySelector(".spin") && (x.disabled = false))); }));
  if ($("#suggest")) $("#suggest").onclick = (e) => post(e.currentTarget, "AI 建議下一輪問題", "suggest");
  $$("[data-sugg]").forEach((b) => (b.onclick = () => { const t = d.suggestions[+b.dataset.sugg].text; const ta = $("#followq");
    ta.value = (ta.value.trim() ? ta.value.trim() + "\n" : "") + t; drafts[c.id] = ta.value; b.disabled = true; b.innerHTML = I.check; }));
  if ($("#again")) $("#again").onclick = (e) => run(e.currentTarget, "用這題重新提問", async () => { const r = await api(`/api/cases/${c.id}/again`, {}); tab = "overview"; history.pushState(null, "", `/case/${r.id}`); route(); });
  $$("[data-rf]").forEach((b) => (b.onclick = () => { RF[b.dataset.rf] = b.dataset.v; redraw(d); }));
  $$("[data-sort]").forEach((b) => (b.onclick = () => { const k = b.dataset.sort; if (RF.sort === k) RF.dir = -RF.dir; else { RF.sort = k; RF.dir = -1; } redraw(d); }));
  $$("[data-ssort]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); const k = b.dataset.ssort; if (srcSort.k === k) srcSort.dir = -srcSort.dir; else { srcSort.k = k; srcSort.dir = -1; } redraw(d); }));
  if ($("#reanalyse")) $("#reanalyse").onclick = (e) => post(e.currentTarget, "重新分析", "reanalyse");
}

function redraw(d) { $("#tab").innerHTML = renderTab(d); wire(d); }

// 需要處理 as a table: filter by risk, tone, category and app; sort by any column
const RF = { risk: "all", tone: "all", cat: "all", app: "all", sort: "risk", dir: -1 };
function renderRiskTable(d) {
  if (!d.groups.length) return '<p class="muted" style="margin-top:16px">分析完成後在這裡列出。</p>';
  const { gUrls, agree } = lookups(d);
  const cats = [...new Set(d.groups.map((g) => g.category).filter(Boolean))];
  let rows = d.groups.filter((g) => (RF.risk === "all" || level(g.risk || 0) === RF.risk) && (RF.tone === "all" || toneWord(g.tone) === RF.tone)
    && (RF.cat === "all" || g.category === RF.cat) && (RF.app === "all" || g.engines.includes(RF.app)));
  const key = { risk: (g) => g.risk || 0, apps: (g) => g.engines.length, src: (g) => (gUrls[g.id] || []).length, cat: (g) => g.category || "", tone: (g) => g.tone };
  rows = rows.slice().sort((a, b) => { const x = key[RF.sort](a), y = key[RF.sort](b); return (x > y ? 1 : x < y ? -1 : 0) * RF.dir; });
  const chips = (k, opts) => opts.map(([v, t]) => `<button class="chipbtn" data-rf="${k}" data-v="${esc(v)}" aria-pressed="${RF[k] === v}">${esc(t)}</button>`).join("");
  const th = (k, t) => `<th ${RF.sort === k ? `aria-sort="${RF.dir < 0 ? "descending" : "ascending"}"` : ""}><button data-sort="${k}">${t}${RF.sort === k ? (RF.dir < 0 ? " ↓" : " ↑") : ""}</button></th>`;
  const SUP = SUPPORT;
  return `<div class="filters">
      <div class="grp"><span class="lbl">風險</span>${chips("risk", [["all", "全部"], ["高", "高"], ["中", "中"], ["低", "低"]])}</div>
      <div class="grp"><span class="lbl">語氣</span>${chips("tone", [["all", "全部"], ["負面", "負面"], ["中性", "中性"], ["正面", "正面"]])}</div>
      <div class="grp"><span class="lbl">類別</span>${chips("cat", [["all", "全部"], ...cats.map((x) => [x, x])])}</div>
      <div class="grp"><span class="lbl">應用程式</span>${chips("app", [["all", "全部"], ...d.case.engines.map((e) => [e, SHORT[e] || e])])}</div></div>
    <p class="muted small">${rows.length} 條說法。黃色標記是高風險說法（65 分或以上）。按欄名排序。</p>
    <table class="stable"><thead><tr>${th("risk", "風險")}<th><button disabled>說法</button></th>${th("cat", "類別")}${th("tone", "語氣")}${th("apps", "應用程式")}${th("src", "來源")}</tr></thead>
    <tbody>${rows.map((g) => { const sup = SUP[g.support] || ["", ""]; return `<tr class="${level(g.risk || 0) === "高" ? "high" : ""}">
      <td class="num" data-k="風險">${g.risk ?? "–"} <small class="muted">${level(g.risk || 0)}</small></td>
      <td class="text"><mark>${esc(g.text)}</mark>${g.contradiction ? `<div class="why small"><span class="flag neg">${I.split}</span> ${esc(g.contradiction)}</div>` : ""}
        <details><summary>各應用程式的原句</summary><ul class="small">${(g.claims || []).map((cl) => `<li>${engLabel(lookups(d).asks[cl.ask_id]?.engine)}：${esc(cl.text)}</li>`).join("")}</ul></details></td>
      <td data-k="類別">${esc(g.category || "")}</td>
      <td data-k="語氣"><span class="flag ${g.tone < 0 ? "neg" : g.tone > 0 ? "pos" : ""}">${toneWord(g.tone)}</span></td>
      <td data-k="應用程式"><span class="dots">${dots(d, g.engines)}</span><div class="small muted">${agree(g)}</div></td>
      <td data-k="來源">${(gUrls[g.id] || []).length}${sup[0] ? `<div class="small"><span class="flag ${sup[1]}">${sup[0]}</span></div>` : ""}</td></tr>`; }).join("")}</tbody></table>`;
}

const engLabel = (e) => esc(ENG[e] || e);
// the grid's column heads and the phone layout's labels: short enough not to wrap
const SHORT = { google_aio: "AI 概覽", google_aimode: "AI 模式", copilot: "Copilot", perplexity: "Perplexity", dola: "豆包" };
const engShort = (e) => esc(SHORT[e] || ENG[e] || e);

function cellMark(a) {
  if (a.status === "queued") return `<span title="等候中">${I.wait}</span>`;
  if (a.status === "none") return `<span title="沒有答案">${I.dash}</span>`;
  if (a.status === "failed") return `<span class="flag neg" title="未能取得答案">${I.warn}</span>`;
  if (a.status === "login_needed") return `<span title="需要登入">${I.lock}</span>`;
  const t = a.tone > 0 ? "pos" : a.tone < 0 ? "neg" : "neu";
  return `<span class="tone ${t}" title="${toneWord(a.tone)}"></span>`;
}

function grid(d) {
  const engines = d.case.engines;
  const byQ = {};
  d.asks.forEach((a) => { (byQ[a.question_id] = byQ[a.question_id] || {})[a.engine] = a; });
  const rows = d.questions.filter((q) => byQ[q.id]).map((q) => {
    const cells = engines.map((e) => { const a = byQ[q.id][e];
      return `<td class="c" data-app="${engShort(e)}">${a ? `<button class="cell" data-ask="${a.id}" aria-expanded="${openCell === a.id}" aria-label="${engLabel(e)}：${toneWord(a.tone)}，看回答">${cellMark(a)}</button>` : ""}</td>`; }).join("");
    const open = engines.map((e) => byQ[q.id][e]).find((a) => a && a.id === openCell);
    return `<tr class="qrow"><td class="q">${esc(q.text)}<span class="who">${esc(q.stakeholder)}，${ANGLE[q.angle] || "追問"}</span></td>${cells}</tr>
      ${open ? `<tr class="answer-row"><td colspan="${engines.length + 1}"><h3>${engLabel(open.engine)} 的回答</h3>
        ${open.answer ? `<div class="answer">${esc(open.answer)}</div>` : `<p class="muted">${esc(open.error || "沒有答案")}</p>`}
        ${open.citations?.length ? `<ol class="cites">${open.citations.map((x) => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc((x.title || x.url).slice(0, 120))}</a></li>`).join("")}</ol>` : ""}</td></tr>` : ""}`;
  }).join("");
  return `<table class="grid"><thead><tr><th class="q">問題</th>${engines.map((e) => `<th title="${engLabel(e)}">${engShort(e)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>
    <p class="legend"><span><i class="tone neg"></i>負面</span><span><i class="tone neu"></i>中性</span><span><i class="tone pos"></i>正面</span>
      <span>${I.dash}沒有答案</span><span>${I.wait}等候中</span><span class="flag neg">${I.warn}未能取得</span><span>按一格看回答和引用</span></p>`;
}

// one statement in the overview's top-risk panel: score, the statement (highlighted when high), one line of facts;
// the apps' own sentences are one click away in 需要處理
function clip(g, d) {
  const lv = level(g.risk || 0);
  const sup = SUPPORT[g.support] || ["", ""];
  return `<li class="clip ${lv === "高" ? "high" : ""}">
    <div class="score">${g.risk ?? "–"}<small>${lv}</small></div>
    <div><div class="text"><mark>${esc(g.text)}</mark></div>
      ${g.contradiction ? `<div class="why"><span class="flag neg">${I.split}</span> ${esc(g.contradiction)}</div>` : ""}
      <div class="meta"><span class="flag ${g.tone < 0 ? "neg" : g.tone > 0 ? "pos" : ""}">${toneWord(g.tone)}</span>
        <span class="apps" title="${g.engines.map(engLabel).join("、")}"><span class="dots" aria-hidden="true">${dots(d, g.engines)}</span>${g.engines.length}/${d.case.engines.length} 個應用程式</span>
        ${sup[0] ? `<span class="flag ${sup[1]}">${sup[0]}</span>` : ""}</div></div></li>`;
}

function renderTab(d) {
  if (tab === "overview") {
    // a dashboard of panels on a 12-column grid: the questions × apps grid across the top; then two stacked columns
    // (riskiest statements and stakeholders; recency and issues) so neither leaves a hole; the news count and headlines last
    const go = (t, label) => `<button class="btn quiet sm" data-tab-go="${t}">${label}</button>`;
    const stake = stakeholderTable(d), chart = newsChart(d);
    const newsLi = (n) => `<li><time>${n.published || ""}</time><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a> <span class="muted">${esc(n.outlet)}</span></li>`;
    const N = 10;
    return `<div class="dashwrap"><div class="dash">
      <section class="dpanel s12"><div class="phead"><h2>每個應用程式怎樣答</h2>${d.asks.length ? go("answers", "並排看回答") : ""}</div>${d.asks.length ? grid(d) : '<p class="muted">這個個案還沒有回答。</p>'}</section>
      <div class="dcol s7">
        <section class="dpanel"><div class="phead"><h2>最需要處理</h2>${d.groups.length ? go("risk", `看全部 ${d.groups.length} 條說法`) : ""}</div>
          ${d.groups.length ? `<ul class="clips">${d.groups.slice(0, 5).map((g) => clip(g, d)).join("")}</ul>` : '<p class="muted">分析完成後在這裡列出。</p>'}</section>
        ${stake ? `<section class="dpanel stakep">${stake}</section>` : ""}</div>
      <div class="dcol s5">
        <section class="dpanel"><h2>AI 知道多新的發展</h2><p class="muted small">各應用程式回答中寫出的最新日期。超過 30 天或沒有寫日期以紅色標示。</p>${recencyList(d)}</section>
        <section class="dpanel"><h2>近期議題</h2>${d.issues.length ? `<ul class="issuelist">${d.issues.map((i) => `<li><div class="ih"><b>${esc(i.title)}</b><time>${i.latest || ""}</time></div><p>${esc(i.summary)}</p></li>`).join("")}</ul>` : '<p class="muted">近 60 天沒有相關新聞。</p>'}</section></div>
      <section class="dpanel s12"><div class="phead"><h2>近 60 天新聞</h2><span class="muted small">${d.news.length} 篇</span></div>
        ${d.news.length ? `<div class="newsgrid">${chart ? `<div class="chartbox">${chart}</div>` : ""}<div style="${chart ? "" : "grid-column: 1 / -1"}"><ul class="news">${d.news.slice(0, N).map(newsLi).join("")}</ul>
        ${d.news.length > N ? `<details class="morenews"><summary>其餘 ${d.news.length - N} 篇</summary><ul class="news">${d.news.slice(N, 60).map(newsLi).join("")}</ul></details>` : ""}</div></div>` : '<p class="muted">沒有</p>'}</section>
    </div></div>`;
  }
  if (tab === "risk") return renderRiskTable(d);
  if (tab === "tree") return renderTree(d);
  if (tab === "agree") return renderAgree(d);
  if (tab === "sources") return renderSources(d);
  if (tab === "graph") return renderGraph(d);
  if (tab === "answers") return renderAnswers(d);
  if (tab === "forums") return renderForums(d);
  if (tab === "more") {
    const failed = d.asks.filter((a) => ["failed", "login_needed"].includes(a.status)).length;
    const sugPending = d.tasks.some((t) => t.kind === "suggest" && ["queued", "running"].includes(t.status));
    return `<h2>追問</h2><p class="muted small">每行一條問題。會用同一組應用程式提問，分析後加入這個個案。</p>
      <textarea id="followq" rows="4" placeholder="例如：政府何時公開報告全文？"></textarea>
      <div class="actions"><button class="btn" id="follow">追問</button><button class="btn quiet" id="suggest" ${sugPending ? "disabled" : ""}>${sugPending ? I.spin + " " : ""}AI 建議下一輪問題</button>
        ${failed ? `<button class="btn quiet" id="retry">重問失敗的問題（${failed}）</button>` : ""}</div>
      ${d.suggestions.length ? `<ul class="sugg">${d.suggestions.map((x, i) => `<li><button class="linkish" data-sugg="${i}">加入</button> <span class="t">${esc(x.text)}</span><div class="muted small">${esc(x.why)}</div></li>`).join("")}</ul>` : ""}
      <h2>用同一題目重新開始</h2><p class="muted small">開一個新個案：重新搜尋新聞、重新擬問題，原有個案保留。</p><button class="btn quiet" id="again">用這題重新提問</button>
      <h2>重新分析</h2><p class="muted small">回答和來源不變，重新比較各應用程式的說法。</p><button class="btn quiet" id="reanalyse">重新分析</button>
      <h2>刪除個案</h2><p class="muted small">刪除後問題、回答和分析都不能復原。</p><button class="btn danger" id="del">刪除個案</button>`;
  }
  return "";
}

route();
