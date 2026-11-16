// AI 觀察: the case tabs beyond the overview (question tree, agreement, sources, graph, answers side by side, forums)
// and the overview's stakeholder table, news-volume chart and recency list. Loaded before app.js; uses its helpers
// (esc, I, level, toneWord, engLabel, engShort, ANGLE, redraw) at call time. Every block here sits in a `.tx` root;
// its styles are static/tabs.css. app.js wires the shared controls (#coonly, .drow, [data-ssort], [data-gview],
// [data-node]/[data-pin], #unpin, #qsel, [data-forum], [data-goq], #reanalyse); the controls only these tabs have
// carry data-tx and are wired by the one listener at the end of this file.

// one icon set, as app.js's: 24 viewBox, 2px stroke
const TI = {
  chev: '<svg class="icon chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>',
  up: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6"/></svg>',
  down: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 5v14M6 13l6 6 6-6"/></svg>',
  sort: '<svg class="icon faint" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m8 9 4-4 4 4M8 15l4 4 4-4"/></svg>',
  ext: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/></svg>',
  search: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/></svg>',
  check: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>',
  more: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>',
};
let txD = null;   // the case last drawn by these tabs, for the data-tx controls

// ---------------------------------------------------------------- shared lookups

function lookups(d) {
  if (d._lk) return d._lk;
  const asks = Object.fromEntries(d.asks.map((a) => [a.id, a]));
  const qs = Object.fromEntries(d.questions.map((q) => [q.id, q]));
  const issues = Object.fromEntries(d.issues.map((i) => [i.id, i]));
  const gUrls = Object.fromEntries((d.group_urls || []).map((r) => [r.group_id, r.urls || []]));
  // a statement's question(s) and apps, through its claims
  const gQs = {};
  d.groups.forEach((g) => { gQs[g.id] = [...new Set((g.claims || []).map((c) => asks[c.ask_id]?.question_id).filter(Boolean))]; });
  const agree = (g) => { const n = g.engines.length, of = d.case.engines.length;
    return g.contradiction ? "矛盾" : n >= of ? "全部一致" : n >= Math.max(2, Math.ceil(of / 2)) ? "多數" : n >= 2 ? "部分" : "單一"; };
  return (d._lk = { asks, qs, issues, gUrls, gQs, agree });
}

// the apps as dots in the case's fixed order (filled: the app says it); app.js uses this inside its own .dots
const dots = (d, engs) => d.case.engines.map((e) => `<span class="dot ${engs.includes(e) ? "on" : ""}" title="${engLabel(e)}${engs.includes(e) ? "" : "：沒有"}"></span>`).join("");
// the same with its meaning in words: dots, then "3/5", read out as the apps' names
const appMarks = (d, engs) => `<span class="apps-mark" role="img" aria-label="${engs.length ? engs.map(engLabel).join("、") : "沒有應用程式"}">`
  + `<span class="dots" aria-hidden="true">${dots(d, engs)}</span><span class="n" aria-hidden="true">${engs.length}/${d.case.engines.length}</span></span>`;
const dotKey = (d) => `<p class="dotkey small"><span class="muted">圓點次序</span>${d.case.engines.map((e, i) => `<span><span class="dot on"></span>${i + 1} ${engShort(e)}</span>`).join("")}</p>`;
const toneFlag = (t) => `<span class="flag ${t < 0 ? "neg" : t > 0 ? "pos" : ""}">${toneWord(t)}</span>`;
const riskMark = (g) => `<span class="risk r-${level(g.risk || 0) === "高" ? "hi" : level(g.risk || 0) === "中" ? "mid" : "lo"}"><b>${g.risk ?? "–"}</b>${level(g.risk || 0)}風險</span>`;

// ---------------------------------------------------------------- overview extras

function stakeholderTable(d) {
  const { qs, gQs } = lookups(d);
  const rows = {};
  d.questions.forEach((q) => { const r = (rows[q.stakeholder || "其他"] = rows[q.stakeholder || "其他"] || { qs: new Set(), pos: 0, neg: 0, n: 0, high: 0, claims: 0, top: null }); r.qs.add(q.id); });
  d.asks.forEach((a) => { const q = qs[a.question_id]; if (!q || a.status !== "done") return; const r = rows[q.stakeholder || "其他"]; r.n++; if (a.tone > 0) r.pos++; if (a.tone < 0) r.neg++; });
  d.groups.forEach((g) => { const seen = new Set(); gQs[g.id].forEach((qid) => { const q = qs[qid]; if (!q) return; const k = q.stakeholder || "其他"; if (seen.has(k)) return; seen.add(k);
    const r = rows[k]; r.claims++; if ((g.risk || 0) >= 65) r.high++; if (!r.top || (g.risk || 0) > (r.top.risk || 0)) r.top = g; }); });
  const list = Object.entries(rows).filter(([, r]) => r.n).sort((a, b) => b[1].high - a[1].high || (b[1].top?.risk || 0) - (a[1].top?.risk || 0));
  if (!list.length) return "";
  return `<h2>不同持份者問 AI 會得到甚麼印象</h2><p class="muted small">按高風險說法多少排列；每行最後一句是該持份者會讀到風險最高的說法。</p>
    <ul class="tx stake">${list.map(([k, r]) => `<li><div class="who">${esc(k)}</div>
      <div class="nums small"><span>${r.qs.size} 條問題，${r.n} 個回答</span><span>回答語氣：正面 ${r.pos}，負面 ${r.neg}</span><span ${r.high ? 'class="hot"' : ""}>高風險說法 ${r.high}／${r.claims}</span></div>
      ${r.top ? `<div class="top ${level(r.top.risk || 0) === "高" ? "high" : ""}">${riskMark(r.top)} <mark>${esc(r.top.text)}</mark></div>` : ""}</li>`).join("")}</ul>`;
}

function newsChart(d) {
  const dated = d.news.filter((n) => n.published);
  if (!dated.length) return "";
  const end = new Date(); const weeks = [];
  for (let i = 8; i >= 0; i--) { const to = new Date(end - i * 7 * 864e5); weeks.push({ to, n: 0 }); }
  dated.forEach((n) => { const t = new Date(n.published); const w = weeks.find((w) => t <= w.to && t > new Date(w.to - 7 * 864e5)); if (w) w.n++; });
  const max = Math.max(1, ...weeks.map((w) => w.n)), W = 40, H = 96, top = 18;
  return `<h2>近 60 天新聞量</h2><div class="tx"><svg class="newschart" viewBox="0 0 ${weeks.length * W} ${top + H + 24}" role="img" aria-label="每週新聞數量：${weeks.map((w) => `${w.to.getMonth() + 1}月${w.to.getDate()}日前一週 ${w.n} 篇`).join("，")}">
    ${weeks.map((w, i) => { const h = Math.round(H * w.n / max); return `<g><rect x="${i * W + 6}" y="${top + H - h}" width="${W - 12}" height="${h}" class="bar"></rect>
      <text x="${i * W + W / 2}" y="${top + H - h - 5}" text-anchor="middle" class="v">${w.n || ""}</text>
      <text x="${i * W + W / 2}" y="${top + H + 17}" text-anchor="middle" class="l">${w.to.getMonth() + 1}/${w.to.getDate()}</text></g>`; }).join("")}
    <line x1="0" y1="${top + H}" x2="${weeks.length * W}" y2="${top + H}" class="axis"></line></svg></div><p class="muted small">每週截至當日的新聞數（Google 新聞，香港）。</p>`;
}

function recencyList(d) {
  const byE = {};
  d.asks.forEach((a) => { (byE[a.engine] = byE[a.engine] || []).push(a); });
  const latestIssue = d.issues.map((i) => i.latest).filter(Boolean).sort().pop();
  return `<ul class="tx recency">${Object.entries(byE).map(([e, as]) => { const done = as.filter((a) => a.status === "done");
    const ds = done.map((a) => a.latest_date).filter(Boolean).sort(); const last = ds[ds.length - 1];
    const age = last ? Math.round((Date.now() - new Date(last)) / 864e5) : null;
    const recent = done.filter((a) => a.latest_date && (Date.now() - new Date(a.latest_date)) / 864e5 <= 30).length;
    const atLatest = latestIssue ? done.filter((a) => a.latest_date && a.latest_date >= latestIssue).length : 0;
    return `<li><span>${engLabel(e)}</span><span class="r"><span class="${age === null || age > 30 ? "err" : ""}">${last ? `${last}（${age} 天前）` : `${I.warn} 沒有寫日期`}</span>
      <span class="muted small">提到近 30 天：${recent}／${done.length}${latestIssue ? `，寫到最新議題日期：${atLatest}` : ""}</span></span></li>`; }).join("")}</ul>`;
}

// ---------------------------------------------------------------- 問題樹

function renderTree(d) {
  txD = d;
  const { issues, gQs } = lookups(d);
  const asked = d.questions.filter((q) => d.asks.some((a) => a.question_id === q.id));
  if (!asked.length) return '<p class="muted tx-gap">未有回答。</p>';
  const stat = (q) => { const gs = d.groups.filter((g) => gQs[g.id].includes(q.id));
    const engs = [...new Set(d.asks.filter((a) => a.question_id === q.id && a.status === "done").map((a) => a.engine))];
    return { n: gs.length, high: gs.filter((g) => (g.risk || 0) >= 65).length, con: gs.filter((g) => g.contradiction).length, one: gs.filter((g) => g.engines.length === 1).length, engs }; };
  const node = (q) => { const s = stat(q);
    return `<li class="tnode"><button class="tq" data-goq="${q.id}"><span>${esc(q.text)}</span>${TI.chev}</button>
      <div class="tmeta small"><span class="who">${esc(q.stakeholder || "其他")}・${ANGLE[q.angle] || "追問"}</span>
        <span>說法 ${s.n}</span>${s.high ? `<span class="hot">高風險 ${s.high}</span>` : ""}${s.con ? `<span class="flag neg">${I.split}矛盾 ${s.con}</span>` : ""}${s.one ? `<span>只有一個應用程式提到 ${s.one}</span>` : ""}
        <span class="ans">回答 ${appMarks(d, s.engs)}</span></div></li>`; };
  const roots = asked.filter((q) => q.angle === "base" || (!q.issue_id && q.round === 1 && q.stakeholder !== "追問"));
  const branches = {};
  asked.filter((q) => !roots.includes(q)).forEach((q) => { const k = q.issue_id && issues[q.issue_id] ? q.issue_id : (q.stakeholder === "追問" ? "follow" : "none"); (branches[k] = branches[k] || []).push(q); });
  const title = (k) => (k === "follow" ? "追問" : k === "none" ? "其他角度" : issues[k].title);
  const n = Object.keys(branches).length;
  return `<div class="tx tx-tree"><p class="muted small tx-gap">${asked.length} 條已問的問題：${roots.length} 條起點問題，${n} 個議題分支。按一條問題，並排看各應用程式的回答。</p>
    ${dotKey(d)}
    <div class="tree"><section class="troot"><h3>起點</h3><ul>${roots.map(node).join("") || '<li class="muted">沒有</li>'}</ul></section>
      ${n ? `<ul class="tbranches">${Object.entries(branches).map(([k, qs]) => `<li class="tbranch"><div class="thead"><h3>${esc(title(k))}</h3>${issues[k]?.latest ? `<span class="muted small">${issues[k].latest}</span>` : ""}</div>
        ${issues[k] ? `<p class="tsum">${esc(issues[k].summary)}</p>` : ""}<ul>${qs.map(node).join("")}</ul></li>`).join("")}</ul>` : ""}</div></div>`;
}

// ---------------------------------------------------------------- 共同與分歧

function stmtRow(g, d) {
  const { gUrls, qs, gQs, agree } = lookups(d);
  const q = qs[gQs[g.id][0]];
  const nSrc = (gUrls[g.id] || []).length;
  return `<li class="srow ${level(g.risk || 0) === "高" ? "high" : ""}">
    <div class="stext"><mark>${esc(g.text)}</mark>
      ${g.contradiction ? `<div class="why small"><span class="flag neg">${I.split}矛盾</span> ${esc(g.contradiction)}</div>` : ""}
      ${q ? `<div class="from small">問題：${esc(q.text)}</div>` : ""}</div>
    <div class="sfacts small"><span class="f">${riskMark(g)}${toneFlag(g.tone)}${g.category ? `<span>${esc(g.category)}</span>` : ""}</span>
      <span class="f">${appMarks(d, g.engines)}<span>${agree(g)}</span><span class="${nSrc ? "" : "muted"}">來源 ${nSrc}</span></span></div></li>`;
}

let agreeF = "all";
const AGREE_FIRST = 6;   // under 全部, each kind shows its riskiest few; its own button shows the rest
function renderAgree(d) {
  txD = d;
  if (!d.groups.length) return '<p class="muted tx-gap">分析完成後在這裡列出。</p>';
  const of = d.case.engines.length;
  const common = d.groups.filter((g) => !g.contradiction && g.engines.length >= Math.max(2, Math.ceil(of / 2)));
  const contra = d.groups.filter((g) => g.contradiction);
  const single = d.groups.filter((g) => !g.contradiction && g.engines.length === 1);
  const some = d.groups.filter((g) => !common.includes(g) && !contra.includes(g) && !single.includes(g));
  const secs = [["common", "共同說法", "全部或多數應用程式都這樣說", common], ["contra", "互相矛盾", "應用程式之間說法不一，先核對", contra],
    ["some", "部分應用程式提出", "兩個或以上、但未過半", some], ["single", "只有一個應用程式提到", "最容易是錯誤或未經證實的說法", single]];
  const byRisk = (l) => [...l].sort((a, b) => (b.risk || 0) - (a.risk || 0));
  const high = (l) => l.filter((g) => (g.risk || 0) >= 65).length;
  const shown = secs.filter(([k]) => agreeF === "all" || agreeF === k);
  return `<div class="tx tx-agree">
    <div class="abar"><div class="segs" role="group" aria-label="只看">
      <button data-tx="agree" data-v="all" aria-pressed="${agreeF === "all"}">全部 <b>${d.groups.length}</b></button>
      ${secs.map(([k, t, , l]) => `<button data-tx="agree" data-v="${k}" aria-pressed="${agreeF === k}">${t.replace("部分應用程式提出", "部分").replace("只有一個應用程式提到", "單一").replace("共同說法", "共同")} <b>${l.length}</b></button>`).join("")}</div>
      <button class="btn quiet" id="reanalyse">重新分析</button></div>
    ${dotKey(d)}
    ${shown.map(([k, t, sub, l]) => `<section class="asec"><h2>${t}<span class="cnt">${l.length} 條${high(l) ? `，高風險 ${high(l)}` : ""}</span></h2><p class="muted small">${sub}；按風險排列${agreeF === "all" && l.length > AGREE_FIRST ? `，這裏列出最高的 ${AGREE_FIRST} 條` : ""}。</p>
      ${l.length ? `<ul class="srows">${byRisk(l).slice(0, agreeF === "all" ? AGREE_FIRST : l.length).map((g) => stmtRow(g, d)).join("")}</ul>` : '<p class="muted">沒有</p>'}
      ${agreeF === "all" && l.length > AGREE_FIRST ? `<p class="more"><button class="btn quiet" data-tx="agree" data-v="${k}">${TI.more}看全部 ${l.length} 條${t}</button></p>` : ""}</section>`).join("")}</div>`;
}

// ---------------------------------------------------------------- 來源

let coOnly = false, openDomain = null, srcSort = { k: "importance", dir: -1 }, srcKind = "all", srcAll = false;
const SRC_FIRST = 20;
const SRC_KIND = { official: "官方", media: "媒體", wiki: "百科", forum: "論壇", social: "社交平台", business: "商業／登記", other: "其他" };
function renderSources(d) {
  txD = d;
  const { gUrls } = lookups(d);
  const done = d.asks.filter((a) => a.status === "done");
  const mention = done.filter((a) => a.mentions).length, noSrc = done.filter((a) => !a.citations?.length).length;
  if (!d.sources.length) return '<p class="muted tx-gap">回答沒有引用網址，或者未有回答。</p>';
  // a page counts as cited in the text when a statement cites it; attached when it is only in the answer's source list
  const inText = {};
  d.groups.forEach((g) => (gUrls[g.id] || []).forEach((u) => { inText[u] = (inText[u] || new Set()).add(g.id); }));
  const byUrl = Object.fromEntries(d.sources.map((s) => [s.url, s]));
  const dom = {};
  d.sources.forEach((s) => { const r = (dom[s.domain] = dom[s.domain] || { domain: s.domain, kind: s.kind, urls: [], engines: new Set(), groups: new Set(), supported: new Set(), text: 0, attached: 0 });
    r.urls.push(s); s.engines.forEach((e) => r.engines.add(e)); r.attached += s.cited_by; (inText[s.url] || new Set()).forEach((g) => { r.groups.add(g); r.text++; }); });
  d.groups.forEach((g) => { if (g.support === "supported" || g.support === "partial") (gUrls[g.id] || []).forEach((u) => { const s = byUrl[u]; if (s && dom[s.domain]) dom[s.domain].supported.add(g.id); }); });
  const sk = { importance: (r) => r.importance, apps: (r) => r.engines.size, claims: (r) => r.groups.size, text: (r) => r.text, domain: (r) => r.domain };
  const all = Object.values(dom).map((r) => ({ ...r, importance: r.engines.size * (1 + r.supported.size) }));
  const maxImp = Math.max(1, ...all.map((r) => r.importance));
  const co = all.filter((r) => r.engines.size >= 2).length, official = all.filter((r) => r.kind === "official").length;
  const kinds = Object.keys(SRC_KIND).map((k) => [k, all.filter((r) => (r.kind || "other") === k).length]).filter(([, n]) => n);
  let rows = all.filter((r) => (!coOnly || r.engines.size >= 2) && (srcKind === "all" || (r.kind || "other") === srcKind))
    .sort((a, b) => { const x = sk[srcSort.k](a), y = sk[srcSort.k](b); return (x > y ? 1 : x < y ? -1 : b.attached - a.attached) * srcSort.dir; });
  const total = rows.length, cut = !srcAll && total > SRC_FIRST + 5;
  if (cut) rows = rows.slice(0, SRC_FIRST);
  const engs = d.case.engines, cols = 5 + engs.length;
  const th = (k, t, cl) => `<th class="${cl || ""}" ${srcSort.k === k ? `aria-sort="${srcSort.dir < 0 ? "descending" : "ascending"}"` : ""}><button data-ssort="${k}">${t}${srcSort.k === k ? (srcSort.dir < 0 ? TI.down : TI.up) : TI.sort}</button></th>`;
  const pageState = (x) => (x.status === "read" ? toneWord(x.tone) : x.status === "queued" ? "待讀取" : "未能讀取");
  return `<div class="tx tx-src">
    <dl class="sfig"><div><dt>被引用網域</dt><dd>${all.length}</dd></div><div><dt>兩個或以上應用程式共同引用</dt><dd>${co}</dd></div>
      <div><dt>官方網域</dt><dd class="${official ? "" : "err"}">${official}</dd></div><div><dt>回答沒有附來源</dt><dd>${noSrc}<small>／${done.length}</small></dd></div>
      <div><dt>回答有提及對象</dt><dd>${mention}<small>／${done.length}</small></dd></div></dl>
    <p class="muted small">重要度 = 引用的應用程式數目 ×（1 + 該網域網頁支持的說法數目）。按一行看該網域被引用的網頁。</p>
    <div class="filters"><label class="chipbtn chk"><input type="checkbox" id="coonly" ${coOnly ? "checked" : ""}>只看共同引用（${co}）</label>
      <span class="grp" role="group" aria-label="類型"><button class="chipbtn" data-tx="kind" data-v="all" aria-pressed="${srcKind === "all"}">全部類型</button>${kinds.map(([k, n]) => `<button class="chipbtn" data-tx="kind" data-v="${k}" aria-pressed="${srcKind === k}">${SRC_KIND[k]} ${n}</button>`).join("")}</span></div>
    ${total ? `<table class="dtable"><thead><tr>${th("domain", "網域", "l")}${th("importance", "重要度", "imp")}${th("apps", "應用程式", "n")}${th("claims", "說法", "n")}${th("text", "文中／附上", "n")}
      ${engs.map((e) => `<th class="eng" title="${engLabel(e)}">${engShort(e)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => { const open = openDomain === r.domain; return `<tr class="drow ${open ? "is-open" : ""}" data-dom="${esc(r.domain)}">
      <td class="l"><button class="dbtn" aria-expanded="${open}">${TI.chev}<span class="dn">${esc(r.domain)}</span></button>${r.kind && r.kind !== "other" ? `<span class="tag k-${r.kind}">${SRC_KIND[r.kind]}</span>` : ""}</td>
      <td class="imp" data-k="重要度"><span class="ibar" aria-hidden="true"><i style="width:${Math.max(2, Math.round(100 * r.importance / maxImp))}%"></i></span><b>${r.importance}</b></td>
      <td class="n" data-k="應用程式">${r.engines.size}</td><td class="n" data-k="說法">${r.groups.size}</td><td class="n" data-k="文中／附上">${r.text}／${r.attached}</td>
      ${engs.map((e) => `<td class="eng"><span class="dot ${r.engines.has(e) ? "on" : ""}" role="img" aria-label="${engLabel(e)}${r.engines.has(e) ? "有引用" : "沒有引用"}"></span></td>`).join("")}
      <td class="appsm">${engs.filter((e) => r.engines.has(e)).map(engShort).join("、")}</td></tr>
      ${open ? `<tr class="dopen"><td colspan="${cols}"><ul class="pages">${r.urls.map((x) => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title || x.url)}</a>
        <div class="pm small"><span>${x.engines.map(engShort).join("、")}</span><span>被引用 ${x.cited_by} 次</span><span class="${x.status === "read" ? "" : "muted"}">${pageState(x)}</span></div>${x.summary ? `<p class="sum">${esc(x.summary)}</p>` : ""}</li>`).join("")}</ul></td></tr>` : ""}`; }).join("")}</tbody></table>
    ${cut ? `<p class="more"><button class="btn quiet" data-tx="srcall">${TI.more}顯示其餘 ${total - SRC_FIRST} 個網域</button></p>` : srcAll && total > SRC_FIRST + 5 ? `<p class="more"><button class="btn quiet" data-tx="srcall">只顯示首 ${SRC_FIRST} 個</button></p>` : ""}`
    : '<p class="muted">沒有符合的網域。</p>'}</div>`;
}

// ---------------------------------------------------------------- 圖譜 (laid out once, drawn still: no motion)

let graphView = "topic", graphPin = null;
const GW = 720;   // the drawing's width in px at 1:1, so 13px labels stay 13px on a desktop
const textW = (s) => [...s].reduce((w, ch) => w + (ch.charCodeAt(0) > 0x2e80 ? 13 : 7.4), 0);
const trunc = (s, n) => ([...s].length > n ? [...s].slice(0, n - 1).join("") + "…" : s);

// Fruchterman–Reingold, cooled over a fixed number of rounds, run once. No walls while it runs (walls piled the loose
// nodes along the edges); the result is then scaled to fit, and nodes that still touch are nudged apart.
function layout(nodes, links, w, h) {
  const n = nodes.length, k = 1;
  nodes.forEach((nd, i) => { if (nd.fx != null) { nd.x = nd.fx; nd.y = nd.fy; return; } const a = i * 2.399963, r = 0.4 * Math.sqrt(i + 1); nd.x = Math.cos(a) * r; nd.y = Math.sin(a) * r; });
  const idx = Object.fromEntries(nodes.map((nd, i) => [nd.id, i]));
  const L = links.map((l) => [idx[l.s], idx[l.t]]).filter(([i, j]) => i != null && j != null);
  let temp = Math.sqrt(n) * 0.3;
  for (let it = 0; it < 500; it++) {
    const disp = nodes.map(() => [0, 0]);
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      const dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y, dd = Math.max(0.01, Math.hypot(dx, dy)), f = (k * k) / dd;
      disp[i][0] += dx / dd * f; disp[i][1] += dy / dd * f; disp[j][0] -= dx / dd * f; disp[j][1] -= dy / dd * f;
    }
    L.forEach(([i, j]) => { const dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y, dd = Math.max(0.01, Math.hypot(dx, dy)), f = (dd * dd) / k;
      disp[i][0] -= dx / dd * f; disp[i][1] -= dy / dd * f; disp[j][0] += dx / dd * f; disp[j][1] += dy / dd * f; });
    nodes.forEach((nd, i) => { if (nd.fx != null) return;
      disp[i][0] -= nd.x * 0.35; disp[i][1] -= nd.y * 0.35;   // gravity toward the middle, so leaves don't drift off
      const dd = Math.max(0.01, Math.hypot(disp[i][0], disp[i][1])); nd.x += disp[i][0] / dd * Math.min(dd, temp); nd.y += disp[i][1] / dd * Math.min(dd, temp); });
    temp = Math.max(0.005, temp * 0.985);
  }
  // fit into the box, leaving room for labels on the sides
  const mx = 110, my = 34;
  const xs = nodes.map((nd) => nd.x), ys = nodes.map((nd) => nd.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  nodes.forEach((nd) => { nd.x = mx + (nd.x - x0) / Math.max(1e-6, x1 - x0) * (w - 2 * mx); nd.y = my + (nd.y - y0) / Math.max(1e-6, y1 - y0) * (h - 2 * my); });
  for (let it = 0; it < 60; it++) for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    const a = nodes[i], b = nodes[j], dx = b.x - a.x, dy = b.y - a.y, dd = Math.max(0.01, Math.hypot(dx, dy)), need = a.r + b.r + 10;
    if (dd < need) { const p = (need - dd) / 2, ux = dx / dd, uy = dy / dd; a.x -= ux * p; a.y -= uy * p; b.x += ux * p; b.y += uy * p; }
  }
  nodes.forEach((nd) => { nd.x = Math.max(nd.r + 4, Math.min(w - nd.r - 4, nd.x)); nd.y = Math.max(nd.r + 4, Math.min(h - nd.r - 4, nd.y)); });
  return nodes;
}

// labels in order of importance, each at the first free spot (right, left, below, above); one that fits nowhere is
// left off (its name is in the node's tooltip, its accessible name and the list beside the drawing)
function placeLabels(nodes, w, h, sides) {
  const boxes = nodes.map((nd) => [nd.x - nd.r - 2, nd.y - nd.r - 2, nd.x + nd.r + 2, nd.y + nd.r + 2]);
  const hit = (b) => b[0] < 2 || b[2] > w - 2 || b[1] < 2 || b[3] > h - 2 || boxes.some((o) => b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]);
  const place = (nd) => {
    const tw = textW(nd.text), r = nd.r;
    const tries = { r: [nd.x + r + 5, nd.y + 4.5, "start", [nd.x + r + 3, nd.y - 9, nd.x + r + 7 + tw, nd.y + 8]],
      l: [nd.x - r - 5, nd.y + 4.5, "end", [nd.x - r - 7 - tw, nd.y - 9, nd.x - r - 3, nd.y + 8]],
      b: [nd.x, nd.y + r + 15, "middle", [nd.x - tw / 2 - 2, nd.y + r + 2, nd.x + tw / 2 + 2, nd.y + r + 19]],
      t: [nd.x, nd.y - r - 6, "middle", [nd.x - tw / 2 - 2, nd.y - r - 21, nd.x + tw / 2 + 2, nd.y - r - 2]] };
    for (const s of nd.sides || sides) { const [x, y, anchor, b] = tries[s]; if (!hit(b)) { nd.lab = { x, y, anchor }; boxes.push(b); return; } }
    if (nd.short && nd.short !== nd.text) { nd.text = nd.short; nd.short = null; place(nd); }
  };
  [...nodes].filter((nd) => nd.text).sort((a, b) => b.prio - a.prio).forEach(place);
}

function graphData(d) {
  const { gUrls, issues } = lookups(d);
  const domOf = (u) => (u.split("/")[2] || "").replace(/^www\./, "");
  if (graphView === "topic") {
    const ents = d.entities.slice(0, 28);
    const nodes = [{ id: "subj", label: d.case.subject, type: "subj", r: 15, fx: 0, fy: 0, prio: 1000 }]
      .concat(d.issues.map((i) => ({ id: "i" + i.id, label: i.title, type: "issue", r: 9, prio: 500 })))
      .concat(ents.map((e) => ({ id: "e" + e.id, label: e.name, type: e.neg > e.pos ? "neg" : e.pos > e.neg ? "pos" : "neu", r: 4 + Math.min(9, Math.sqrt(e.n) * 1.6), ent: e, prio: e.n })));
    const links = d.issues.map((i) => ({ s: "subj", t: "i" + i.id }));
    ents.forEach((e) => { (e.issues || []).forEach((i) => issues[i] && links.push({ s: "i" + i, t: "e" + e.id })); if (!(e.issues || []).length) links.push({ s: "subj", t: "e" + e.id }); });
    const h = 600;
    layout(nodes, links, GW, h);
    nodes.forEach((nd) => { const big = nd.type === "issue" || nd.type === "subj"; nd.text = trunc(nd.label, big ? 13 : 9); nd.short = trunc(nd.label, big ? 7 : 5); });
    placeLabels(nodes, GW, h, ["r", "l", "b", "t"]);
    return { nodes, links, h };
  }
  // trace, in three columns: apps → statements (the 30 riskiest) → the domains they cite (top 20). Each column is
  // ordered by the average height of what it links to, so the links cross as little as they can.
  const gs = d.groups.slice(0, 30);
  const doms = {};
  gs.forEach((g) => (gUrls[g.id] || []).forEach((u) => { const dm = domOf(u); (doms[dm] = doms[dm] || new Set()).add(g.id); }));
  const domList = Object.entries(doms).sort((a, b) => b[1].size - a[1].size).slice(0, 20);
  const engs = d.case.engines, h = Math.max(480, gs.length * 20 + 56), top = 40, span = h - 2 * top;
  const ys = (n, i) => (n <= 1 ? h / 2 : top + i * span / (n - 1));
  const appY = Object.fromEntries(engs.map((e, i) => [e, top + 30 + i * (span - 60) / Math.max(1, engs.length - 1)]));
  const gOrd = gs.map((g) => ({ g, k: g.engines.reduce((s, e) => s + appY[e], 0) / Math.max(1, g.engines.length) })).sort((a, b) => a.k - b.k || (b.g.risk || 0) - (a.g.risk || 0));
  const gY = Object.fromEntries(gOrd.map((o, i) => [o.g.id, ys(gOrd.length, i)]));
  const dOrd = domList.map(([dm, set]) => ({ dm, set, k: [...set].reduce((s, id) => s + gY[id], 0) / set.size })).sort((a, b) => a.k - b.k);
  const nodes = engs.map((e) => ({ id: "a" + e, label: engShort(e), text: engShort(e), type: "app", r: 11, x: 120, y: appY[e], lab: { x: 103, y: appY[e] + 4.5, anchor: "end" } }))
    .concat(gOrd.map(({ g }) => ({ id: "g" + g.id, label: "", type: g.tone < 0 ? "neg" : g.tone > 0 ? "pos" : "neu", r: 3.5 + (g.risk || 0) / 18, grp: g, x: 330, y: gY[g.id] })))
    .concat(dOrd.map(({ dm, set }, i) => ({ id: "d" + dm, label: dm, text: trunc(dm, 26), type: "dom", r: 5 + Math.min(5, set.size), x: 500, y: ys(dOrd.length, i) })));
  nodes.filter((nd) => nd.type === "dom").forEach((nd) => { nd.lab = { x: nd.x + nd.r + 8, y: nd.y + 4.5, anchor: "start" }; });
  const keep = new Set(domList.map(([x]) => x));
  const links = [];
  gs.forEach((g) => { g.engines.forEach((e) => links.push({ s: "a" + e, t: "g" + g.id })); [...new Set((gUrls[g.id] || []).map(domOf))].forEach((dm) => { if (keep.has(dm)) links.push({ s: "g" + g.id, t: "d" + dm }); }); });
  return { nodes, links, h, curved: true };
}

function renderGraph(d) {
  txD = d;
  if (!d.groups.length) return '<p class="muted tx-gap">分析完成後顯示。</p>';
  const key = graphView;
  d._graph = d._graph || {};
  if (!d._graph[key]) d._graph[key] = graphData(d);
  const { nodes, links, h, curved } = d._graph[key];
  const pos = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const nb = new Set(); if (graphPin) { nb.add(graphPin); links.forEach((l) => { if (l.s === graphPin) nb.add(l.t); if (l.t === graphPin) nb.add(l.s); }); }
  const dim = (id) => (graphPin && !nb.has(id) ? " dim" : "");
  const edge = (a, b) => (curved ? `M${a.x.toFixed(1)} ${a.y.toFixed(1)}C${((a.x + b.x) / 2).toFixed(1)} ${a.y.toFixed(1)} ${((a.x + b.x) / 2).toFixed(1)} ${b.y.toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}`
    : `M${a.x.toFixed(1)} ${a.y.toFixed(1)}L${b.x.toFixed(1)} ${b.y.toFixed(1)}`);
  const name = (n) => n.label || n.grp?.text || "";
  const svg = `<svg class="graph" viewBox="0 0 ${GW} ${h}" width="${GW}" height="${h}" role="group" aria-label="${key === "topic" ? "議題與實體關係圖" : "應用程式、說法與網域關係圖"}">
    ${curved ? `<g class="colhead" aria-hidden="true"><text x="120" y="18" text-anchor="end">應用程式</text><text x="330" y="18" text-anchor="middle">說法</text><text x="494" y="18">網域</text></g>` : ""}
    <g aria-hidden="true">${links.map((l) => { const a = pos[l.s], b = pos[l.t]; return a && b ? `<path d="${edge(a, b)}" class="edge${graphPin ? (nb.has(l.s) && nb.has(l.t) && (l.s === graphPin || l.t === graphPin) ? " on" : " dim") : ""}"></path>` : ""; }).join("")}</g>
    ${nodes.map((n) => `<g class="node ${n.type}${dim(n.id)}${graphPin === n.id ? " pinned" : ""}" data-node="${esc(n.id)}" tabindex="0" role="button" aria-pressed="${graphPin === n.id}" aria-label="${esc(name(n))}">
      ${n.type === "issue" ? `<rect x="${(n.x - n.r).toFixed(1)}" y="${(n.y - n.r).toFixed(1)}" width="${(2 * n.r).toFixed(1)}" height="${(2 * n.r).toFixed(1)}" rx="2"></rect>`
        : n.type === "dom" ? `<rect x="${(n.x - n.r * 0.75).toFixed(1)}" y="${(n.y - n.r * 0.75).toFixed(1)}" width="${(1.5 * n.r).toFixed(1)}" height="${(1.5 * n.r).toFixed(1)}" transform="rotate(45 ${n.x.toFixed(1)} ${n.y.toFixed(1)})"></rect>`
        : `<circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="${n.r.toFixed(1)}"></circle>`}
      <circle class="hit" cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="${Math.max(n.r, 11).toFixed(1)}"></circle>
      ${n.lab ? `<text x="${n.lab.x.toFixed(1)}" y="${n.lab.y.toFixed(1)}" text-anchor="${n.lab.anchor}">${esc(n.text)}</text>` : ""}<title>${esc(name(n))}</title></g>`).join("")}</svg>`;
  const sw = (cls, shape) => `<svg class="sw ${cls}" viewBox="0 0 16 16" aria-hidden="true">${shape === "sq" ? '<rect x="3" y="3" width="10" height="10" rx="1.5"></rect>' : shape === "di" ? '<rect x="4" y="4" width="8" height="8" transform="rotate(45 8 8)"></rect>' : `<circle cx="8" cy="8" r="${shape || 5}"></circle>`}</svg>`;
  const legend = key === "topic"
    ? `<span>${sw("subj", 6)}個案對象</span><span>${sw("issue", "sq")}近期議題</span><span>${sw("neg")}實體：負面說法較多</span><span>${sw("pos")}正面較多</span><span>${sw("neu")}中性</span><span class="muted">點越大，被提及越多</span>`
    : `<span>${sw("app", 6)}應用程式</span><span>${sw("neg")}負面說法</span><span>${sw("pos")}正面說法</span><span>${sw("neu")}中性說法</span><span>${sw("dom", "di")}引用的網域</span><span class="muted">說法的點越大，風險越高</span>`;
  let side;
  const pinned = graphPin && pos[graphPin];
  if (pinned) {
    let gs = [];
    if (pinned.ent) gs = d.groups.filter((g) => (pinned.ent.groups || []).includes(g.id));
    else if (pinned.grp) gs = [pinned.grp];
    else if (pinned.type === "issue") gs = d.groups.filter((g) => "i" + g.issue_id === graphPin);
    else if (pinned.type === "subj") gs = d.groups;
    else if (pinned.type === "app") gs = d.groups.filter((g) => g.engines.includes(graphPin.slice(1)));
    else if (pinned.type === "dom") gs = d.groups.filter((g) => (lookups(d).gUrls[g.id] || []).some((u) => u.includes(graphPin.slice(1))));
    side = `<div class="ghead"><h3>${esc(pinned.label || "說法")}</h3><button class="btn quiet" id="unpin">取消選取</button></div>
      <p class="muted small">${gs.length} 條相關說法${gs.length > 20 ? "，列出風險最高的 20 條" : ""}。</p>
      <ul class="srows">${[...gs].sort((a, b) => (b.risk || 0) - (a.risk || 0)).slice(0, 20).map((g) => stmtRow(g, d)).join("") || '<li class="muted">沒有相關說法</li>'}</ul>`;
  } else if (key === "topic") {
    side = `<p class="muted small">按圖上一個點或下面一個名字，列出相關說法。</p><h3>最常被提及</h3>
      <ul class="ents">${d.entities.slice(0, 15).map((e) => `<li><button class="ent" data-pin="e${e.id}"><span class="nmw"><span class="nm">${esc(e.name)}</span> <span class="muted small">${esc(e.kind)}</span></span>
        <span class="r small">${e.n} 次${e.neg ? `<span class="flag neg">負 ${e.neg}</span>` : ""}${e.pos ? `<span class="flag pos">正 ${e.pos}</span>` : ""}</span></button></li>`).join("") || '<li class="muted">重新分析後顯示</li>'}</ul>`;
  } else {
    side = `<p class="muted small">左邊是應用程式，中間是風險最高的 30 條說法，右邊是它們引用的網域。按一個點看它連到甚麼，並列出相關說法。</p>`;
  }
  return `<div class="tx tx-graph"><div class="segs" role="group" aria-label="圖譜"><button data-gview="topic" aria-pressed="${key === "topic"}">議題與實體</button><button data-gview="trace" aria-pressed="${key === "trace"}">應用程式、說法與網域</button></div>
    <div class="gwrap"><div class="gmain"><div class="gscroll">${svg}</div><p class="scrollhint muted small">圖比畫面闊，可左右捲動。</p><p class="glegend small">${legend}</p></div><aside class="gside">${side}</aside></div></div>`;
}

// ---------------------------------------------------------------- 回答 (side by side)

let answersQ = null;
function renderAnswers(d) {
  txD = d;
  const asked = d.questions.filter((q) => d.asks.some((a) => a.question_id === q.id));
  if (!asked.length) return '<p class="muted tx-gap">未有回答。</p>';
  const q = asked.find((x) => x.id === answersQ) || asked[0];
  const engs = d.case.engines;
  const as = engs.map((e) => d.asks.find((a) => a.question_id === q.id && a.engine === e));
  const state = (a) => (!a ? "沒有問" : a.status === "done" ? "" : a.status === "none" ? "沒有答案" : a.status === "queued" ? "等候中" : "未能取得");
  return `<div class="tx tx-ans"><label class="qlabel" for="qsel">問題（${asked.length} 條）</label>
    <select id="qsel" class="qsel">${asked.map((x) => `<option value="${x.id}" ${x.id === q.id ? "selected" : ""}>${esc(x.text)}</option>`).join("")}</select>
    <p class="muted small">${esc(q.stakeholder || "其他")}・${ANGLE[q.angle] || "追問"}</p>
    <div class="acmp" role="group" aria-label="跳到應用程式">${as.map((a, i) => `<button data-tx="col" data-v="${i}"><span class="an">${engShort(engs[i])}</span>
      <span class="small">${a && a.status === "done" ? `${toneFlag(a.tone)}<span>${[...(a.answer || "")].length} 字</span><span>來源 ${a.citations?.length || 0}</span>` : `<span class="muted">${state(a)}</span>`}</span></button>`).join("")}</div>
    <div class="cols" style="--n:${engs.length}">${as.map((a, i) => `<section class="col" id="acol-${i}"><h3>${engLabel(engs[i])}${a && a.status === "done" ? ` ${toneFlag(a.tone)}` : ""}</h3>
      ${!a ? '<p class="muted">沒有問這個應用程式</p>' : a.answer ? `<div class="answer">${esc(a.answer)}</div>${a.citations?.length ? `<h4>引用的網頁（${a.citations.length}）</h4><ol class="cites one">${a.citations.map((x) => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(trunc(x.title || x.url, 60))}</a></li>`).join("")}</ol>` : '<p class="muted small">沒有附來源</p>'}`
        : `<p class="muted">${a.status === "none" ? "沒有答案" : a.status === "queued" ? "等候中" : `${I.warn} ${esc(a.error || "未能取得")}`}</p>`}</section>`).join("")}</div></div>`;
}

// ---------------------------------------------------------------- 論壇

function renderForums(d) {
  txD = d;
  const spanName = { week: "近一週", month: "近一個月", year: "近一年", any: "不限時間" };
  const pending = d.tasks.some((t) => t.kind === "forums" && ["queued", "running"].includes(t.status));
  const byE = {};
  d.asks.filter((a) => a.status === "done").forEach((a) => { const r = (byE[a.engine] = byE[a.engine] || { n: 0, f: 0, sites: new Set() }); r.n++;
    const fs = (a.citations || []).filter((c) => /lihkg\.com|discuss\.com\.hk|hkgolden\.com|threads\.(net|com)|facebook\.com|instagram\.com|youtube\.com|reddit\.com|baby-kingdom\.com|x\.com|twitter\.com/.test(c.url));
    if (fs.length) { r.f++; fs.forEach((c) => r.sites.add((c.url.split("/")[2] || "").replace(/^www\./, ""))); } });
  const spans = Object.keys(spanName).filter((s) => d.forums.some((f) => f.span === s)).concat([...new Set(d.forums.map((f) => f.span))].filter((s) => !spanName[s]));
  const count = (sp) => d.forums.filter((f) => f.span === sp).length;
  return `<div class="tx tx-forum"><h2>AI 有沒有引用論壇和社交平台</h2>
    <ul class="recency">${Object.entries(byE).map(([e, r]) => `<li><span>${engLabel(e)}</span><span class="r"><span>${r.f}／${r.n} 個回答有引用</span>
      <span class="sites">${[...r.sites].map((s) => `<span class="tag">${esc(s)}</span>`).join("") || '<span class="muted small">沒有</span>'}</span></span></li>`).join("") || '<li class="muted">未有回答</li>'}</ul>
    <h2>LIHKG 和 Threads 上的討論</h2><p class="muted small">在 Google 搜尋這兩個平台上關於「${esc(d.case.subject)}」的帖子。揀一個時段搜尋；已搜尋的時段有剔號，可再搜尋更新。</p>
    <div class="segs" role="group" aria-label="搜尋時段">${Object.entries(spanName).map(([k, v]) => `<button data-forum="${k}" ${pending ? "disabled" : ""}>${count(k) ? TI.check : TI.search}${v}${count(k) ? ` <b>${count(k)}</b>` : ""}</button>`).join("")}</div>
    ${pending ? `<p class="progress">${I.spin} 正在搜尋</p>` : ""}
    ${spans.map((sp) => `<section class="fspan"><h3>${spanName[sp] || esc(sp)}<span class="cnt">${count(sp)} 個帖子</span></h3><ul class="posts">${d.forums.filter((f) => f.span === sp).map((f) => `<li>
      <div class="pt"><span class="tag">${esc(f.site)}</span><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.title)}</a>${f.posted ? `<span class="muted small">${esc(f.posted)}</span>` : ""}</div>
      ${f.snippet ? `<p class="sum">${esc(f.snippet)}</p>` : ""}</li>`).join("") || '<li class="muted">沒有找到</li>'}</ul></section>`).join("") || (pending ? "" : '<p class="muted">未有搜尋。揀一個時段開始。</p>')}</div>`;
}

// ---------------------------------------------------------------- the controls only these tabs have

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-tx]");
  if (!b || !txD) return;
  const v = b.dataset.v;
  if (b.dataset.tx === "agree") { agreeF = v; redraw(txD); const bar = document.querySelector(".tx-agree .abar"); if (bar && bar.getBoundingClientRect().top < 0) bar.scrollIntoView(); }
  else if (b.dataset.tx === "kind") { srcKind = v; redraw(txD); }
  else if (b.dataset.tx === "srcall") { srcAll = !srcAll; redraw(txD); }
  else if (b.dataset.tx === "col") { const c = document.getElementById("acol-" + v); if (c) c.scrollIntoView({ block: "nearest", inline: "start" }); }
});
