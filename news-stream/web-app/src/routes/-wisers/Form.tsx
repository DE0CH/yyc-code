// /app/wisers/: WiseSearch's search form (WisersOne's Direct Search, read-only): 快速 (keywords, with WisersOne's own
// suggestions) or 邏輯 (contains all / any, excludes all / any), the search scope, the date, the media panel (types,
// categories, regions, specific media to include or exclude, author / column / section, language), hot picks when the
// account has them, the six label trees with suggestions, negative-news categories and the other options. 搜尋 first runs
// WisersOne's checks (stop words, illegal characters, the date limit: no search) in a dialog, then opens the results
// at their own URL. The form's fields go into its own history entry, so Back from the results shows them.
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Wait } from "../../lib/Wait";
import { configOf, getJSON, LANGS, NO_RETRY, PERIODS, SIZES, SORTS, TREES, WITHIN, qsOf, type Chosen, type Label, type Opt, type Options,
  type WParams } from "../../lib/wisers";
import { Frame, I, Keep, Seg, kept, useKept } from "./ui";

export const optionsQuery = { queryKey: ["wisers", "options"], queryFn: () => getJSON<Options>("options"), staleTime: 3_600_000,
  gcTime: 86_400_000, ...NO_RETRY };
export const configQuery = { queryKey: ["wisers", "config"], queryFn: () => getJSON<{ config: Record<string, unknown> }>("config"),
  staleTime: 3_600_000, gcTime: 86_400_000, ...NO_RETRY };
const LOOKUP = "（如 WisersOne 需要重新登入，可能要一分鐘）";

type Media = { id: string; name: string; on: "inc" | "exc" };
type Form = { mode: "quick" | "logic"; q: string; and: string; or: string; nand: string; nor: string; title: boolean; content: boolean;
  within: string; period: string; from: string; to: string; sort: string; mt: string[]; mc: string[]; mr: string[]; lang: string;
  author: string; column: string; section: string; media: Media[]; similar: boolean; neg: string[]; hot: string[];
  labs: Record<string, Chosen[]>; cluster: boolean; summary: boolean; size: string };
const list = (v?: string) => (v ? v.split(",").map(x => x.trim()).filter(Boolean) : []);
const labsOf = (s: WParams) => Object.fromEntries(TREES.map(t => {
  try { const v = JSON.parse(s[`lab_${t.key}` as keyof WParams] || "[]"); return [t.key, Array.isArray(v) ? v as Chosen[] : []]; }
  catch { return [t.key, []]; }
}));
export function formOf(s: WParams): Form {
  const r = s.range || "all";
  const names = (kept.get("media-names") || {}) as Record<string, string>;
  return { mode: s.and || s.or || s.nand || s.nor ? "logic" : "quick", q: s.q || "", and: s.and || "", or: s.or || "", nand: s.nand || "",
    nor: s.nor || "", title: r !== "content", content: r !== "title", within: s.within || "document", period: s.period || "7",
    from: s.from || "", to: s.to || "", sort: s.sort || "DESC:date", mt: list(s.mt), mc: list(s.mc), mr: list(s.mr), lang: s.lang || "all",
    author: s.author || "", column: s.column || "", section: s.section || "",
    media: [...list(s.inc).map(id => ({ id, name: names[id] || id, on: "inc" as const })), ...list(s.exc).map(id => ({ id, name: names[id] || id, on: "exc" as const }))],
    similar: s.similar !== "0", neg: list(s.neg), hot: list(s.hot), labs: labsOf(s), cluster: s.cluster !== "0", summary: s.summary !== "0",
    size: s.size || "50" };
}
export function paramsOf(f: Form): WParams {
  const p: WParams = {};
  if (f.mode === "logic") { for (const k of ["and", "or", "nand", "nor"] as const) if (f[k].trim()) p[k] = f[k].trim(); }
  else p.q = f.q.trim();
  if (f.title !== f.content) p.range = f.title ? "title" : "content";
  if (f.within !== "document") p.within = f.within;
  p.period = f.period;
  if (f.period === "custom") { if (f.from) p.from = f.from; if (f.to) p.to = f.to; }
  if (f.sort !== "DESC:date") p.sort = f.sort;
  for (const k of ["mt", "mc", "mr", "neg", "hot"] as const) if (f[k].length) p[k] = f[k].join(",");
  if (f.lang !== "all") p.lang = f.lang;
  for (const k of ["author", "column", "section"] as const) if (f[k].trim()) p[k] = f[k].trim();
  const inc = f.media.filter(m => m.on === "inc").map(m => m.id), exc = f.media.filter(m => m.on === "exc").map(m => m.id);
  if (inc.length) p.inc = inc.join(",");
  if (exc.length) p.exc = exc.join(",");
  if (!f.similar) p.similar = "0";
  if (!f.cluster) p.cluster = "0";
  if (!f.summary) p.summary = "0";
  if (f.size !== "50") p.size = f.size;
  for (const t of TREES) if (f.labs[t.key]?.length) p[`lab_${t.key}` as keyof WParams] = JSON.stringify(f.labs[t.key]);
  return p;
}
let lastForm: Form | null = null;  // what was last entered: Back to /app/wisers/ shows it as it was
export const rememberForm = (s: WParams) => { lastForm = formOf(s); };

const toggle = (xs: string[], v: string) => (xs.includes(v) ? xs.filter(x => x !== v) : xs.concat(v));

/** media checkboxes: a parent with items ticks / unticks all of them */
function OptTree({ name, entries, sel, set }: { name: string; entries?: Opt[]; sel: string[]; set: (v: string[]) => void }) {
  return <div className="cbs">{(entries || []).map(x => x.items?.length ? (
    <div className="grp" key={x.value || x.text}>
      <label className="cb par"><input type="checkbox" checked={x.items.every(i => sel.includes(i.value))}
        onChange={e => set(e.target.checked ? [...new Set([...sel, ...x.items!.map(i => i.value)])] : sel.filter(v => !x.items!.some(i => i.value === v)))} />{x.text}</label>
      <div className="kids">{x.items.map(i => (
        <label className="cb" key={i.value}><input type="checkbox" name={name} value={i.value} checked={sel.includes(i.value)} onChange={() => set(toggle(sel, i.value))} />{i.text}</label>
      ))}</div>
    </div>
  ) : (
    <label className="cb" key={x.value}><input type="checkbox" name={name} value={x.value} checked={sel.includes(x.value)} onChange={() => set(toggle(sel, x.value))} />{x.text}</label>
  ))}</div>;
}

/** one level of a label tree, loaded when shown */
function Level({ group, node, depth, chosen, pick }: { group: string; node: string; depth: number; chosen: Chosen[]; pick: (l: Label, on: boolean) => void }) {
  const q = useQuery({ queryKey: ["wisers", "labels", group, node], queryFn: () => getJSON<{ labels: Label[] }>(`labels?group=${group}&id=${encodeURIComponent(node)}`),
    staleTime: 86_400_000, gcTime: 86_400_000, ...NO_RETRY });
  const [open, setOpen] = useKept<Record<string, boolean>>(`lv:${group}:${node}`, {});
  if (q.isPending) return <Wait className="note" what="載入標籤" label={"載入標籤…" + LOOKUP} slowMs={100_000} />;
  if (q.isError) return <Wait className="note" what="載入標籤" failed onRetry={() => q.refetch()} />;
  const ids = new Set(chosen.map(c => c.id));
  return <ul className="lv">{q.data.labels.map(x => (
    <li key={x.id}>
      {x.kids ? <button type="button" className="tw" aria-expanded={!!open[x.id]} aria-label={open[x.id] ? "收起" : "展開"}
        onClick={() => setOpen({ ...open, [x.id]: !open[x.id] })}><I n={open[x.id] ? "down" : "next"} /></button> : <span className="tw" />}
      {depth > 0 && x.type ? <label className="cb"><input type="checkbox" checked={ids.has(x.id)} onChange={e => pick(x, e.target.checked)} />{x.name}</label>
        : <span className="nm">{x.name}</span>}
      {open[x.id] && <Level group={group} node={x.node} depth={depth + 1} chosen={chosen} pick={pick} />}
    </li>
  ))}</ul>;
}

/** WisersOne's label type-ahead (label/labelautocomplete/<group>) */
function LabelSuggest({ group, chosen, add }: { group: string; chosen: Chosen[]; add: (c: Chosen) => void }) {
  const [s, setS] = useState("");
  const [d, setD] = useState("");
  useEffect(() => { const t = setTimeout(() => setD(s.trim()), 300); return () => clearTimeout(t); }, [s]);
  const q = useQuery({ queryKey: ["wisers", "label-suggest", group, d], enabled: d.length >= 1,
    queryFn: () => getJSON<{ labels: unknown }>(`labels/suggest?group=${group}&q=${encodeURIComponent(d)}`), staleTime: 3_600_000, ...NO_RETRY });
  const rows = (() => {
    const v = q.data?.labels as Record<string, unknown> | unknown[] | undefined;
    const arr = Array.isArray(v) ? v : v && typeof v === "object" ? (Object.values(v).find(Array.isArray) as unknown[] | undefined) || [] : [];
    return (arr as Record<string, unknown>[]).map(x => ({ id: String(x.labelId ?? x.id ?? ""), type: String(x.labelTypeId ?? x.type ?? ""),
      name: String(x.name_hk ?? x.labelName ?? x.name ?? "") })).filter(x => x.id && x.name && !chosen.some(c => c.id === x.id));
  })();
  return (
    <div className="sugg">
      <input value={s} onChange={e => setS(e.target.value)} placeholder="輸入名稱搜尋標籤" aria-label="搜尋標籤" />
      {d && rows.length > 0 && <ul className="slist">{rows.slice(0, 10).map(r => (
        <li key={r.id}><button type="button" onClick={() => { add(r); setS(""); }}>{r.name}</button></li>
      ))}</ul>}
    </div>
  );
}

function LabelTree({ t, chosen, set }: { t: (typeof TREES)[number]; chosen: Chosen[]; set: (v: Chosen[]) => void }) {
  const [shown, setShown] = useKept(`tree:${t.key}`, chosen.length > 0);
  return (
    <Keep k={"tree:" + t.key} className="adv tree" open={chosen.length > 0} onOpen={() => setShown(true)}
      summary={<>{t.title}{chosen.length > 0 && <span className="cnt">已選 {chosen.length}</span>}</>}>
      {chosen.length > 0 && <div className="chips">{chosen.map(c => (
        <button type="button" key={c.id} className="chip" onClick={() => set(chosen.filter(x => x.id !== c.id))}>{c.name}<I n="close" label="移除" /></button>
      ))}</div>}
      {shown && <>
        <LabelSuggest group={t.group} chosen={chosen} add={c => set(chosen.concat(c))} />
        <Level group={t.group} node={t.root} depth={0} chosen={chosen}
          pick={(l, on) => set(on ? chosen.filter(x => x.id !== l.id).concat({ id: l.id, type: l.type, name: l.name }) : chosen.filter(x => x.id !== l.id))} />
      </>}
    </Keep>
  );
}

/** the specific-media picker: WisersOne's media search, each source included (+) or excluded (ban) */
function MediaPicker({ media, set, lang }: { media: Media[]; set: (m: Media[]) => void; lang: string }) {
  const [s, setS] = useState("");
  const [d, setD] = useState("");
  const [page, setPage] = useState(0);
  useEffect(() => { const t = setTimeout(() => { setD(s.trim()); setPage(0); }, 300); return () => clearTimeout(t); }, [s]);
  const q = useQuery({ queryKey: ["wisers", "media", d, page, lang], enabled: d.length >= 1,
    queryFn: () => getJSON<{ media: unknown }>(`media?q=${encodeURIComponent(d)}&page=${page}&lang=${lang}`), staleTime: 86_400_000, ...NO_RETRY });
  const rows = (() => {
    const v = q.data?.media as Record<string, unknown> | undefined;
    const arr = (v && (Array.isArray(v) ? v : (Object.values(v).find(Array.isArray) as unknown[] | undefined))) || [];
    return (arr as Record<string, unknown>[]).map(x => ({ id: String(x.id ?? x.value ?? x.pubId ?? ""), name: String(x.name ?? x.text ?? x.zhName ?? x.enName ?? "") }))
      .filter(x => x.id && x.name);
  })();
  const add = (m: { id: string; name: string }, on: "inc" | "exc") => {
    const names = { ...((kept.get("media-names") || {}) as Record<string, string>), [m.id]: m.name };
    kept.set("media-names", names);
    set(media.filter(x => x.id !== m.id).concat({ ...m, on }));
  };
  return (
    <div className="picker">
      <h4>指定媒體</h4>
      {media.length > 0 && <div className="chips">{media.map(m => (
        <button type="button" key={m.id} className={"chip" + (m.on === "exc" ? " exc" : "")} onClick={() => set(media.filter(x => x.id !== m.id))}>
          <I n={m.on === "exc" ? "ban" : "plus"} label={m.on === "exc" ? "排除" : "包括"} />{m.name}<I n="close" label="移除" /></button>
      ))}</div>}
      <input value={s} onChange={e => setS(e.target.value)} placeholder="輸入媒體名稱" aria-label="搜尋媒體" />
      {q.isPending && d && <Wait className="note" what="搜尋媒體" />}
      {rows.length > 0 && <ul className="mlist">{rows.map(r => (
        <li key={r.id}>
          <span className="nm">{r.name}</span>
          <Link to="/app/wisers/media/$id" params={{ id: r.id }} className="lk" target="_blank">資料</Link>
          <button type="button" onClick={() => add(r, "inc")} aria-label={`包括 ${r.name}`}><I n="plus" /></button>
          <button type="button" onClick={() => add(r, "exc")} aria-label={`排除 ${r.name}`}><I n="ban" /></button>
        </li>
      ))}</ul>}
      {rows.length > 0 && <div className="pg">
        <button type="button" className="ib" disabled={page === 0} onClick={() => setPage(page - 1)}><I n="prev" label="上一頁" /></button>
        <button type="button" className="tb" onClick={() => setPage(page + 1)}>載入更多</button>
      </div>}
    </div>
  );
}

/** keywords with WisersOne's suggestions (search/keywords) */
function KeywordBox({ value, set }: { value: string; set: (v: string) => void }) {
  const [d, setD] = useState("");
  const [focus, setFocus] = useState(false);
  const [hi, setHi] = useState(-1);
  const last = value.split(/\s+/).pop() || "";
  useEffect(() => { const t = setTimeout(() => setD(last), 300); return () => clearTimeout(t); }, [last]);
  const q = useQuery({ queryKey: ["wisers", "suggest", d], enabled: focus && d.length >= 2,
    queryFn: () => getJSON<{ words: string[] }>(`suggest?q=${encodeURIComponent(d)}`), staleTime: 3_600_000, ...NO_RETRY });
  const words = (q.data?.words || []).filter(w => typeof w === "string" && w !== last).slice(0, 8);
  const take = (w: string) => { set(value.replace(/\S*$/, w) + " "); setHi(-1); };
  return (
    <div className="kw">
      <input name="q" value={value} onChange={e => { set(e.target.value); setHi(-1); }} onFocus={() => setFocus(true)}
        onBlur={() => setTimeout(() => setFocus(false), 150)} placeholder="例如：數碼港 AND 低空經濟" autoComplete="off" enterKeyHint="search"
        aria-label="關鍵字" onKeyDown={e => {
          if (!words.length) return;
          if (e.key === "ArrowDown") { e.preventDefault(); setHi((hi + 1) % words.length); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setHi((hi - 1 + words.length) % words.length); }
          else if (e.key === "Enter" && hi >= 0) { e.preventDefault(); take(words[hi]); }
        }} />
      {focus && words.length > 0 && <ul className="slist" role="listbox">{words.map((w, i) => (
        <li key={w} role="option" aria-selected={i === hi} className={i === hi ? "on" : ""}><button type="button" onMouseDown={() => take(w)}>{w}</button></li>
      ))}</ul>}
    </div>
  );
}

function Guide() {
  return (
    <div className="guide">
      <p><b>AND</b>：兩個字詞都要出現，例如 數碼港 AND 低空經濟。空格與 AND 相同。</p>
      <p><b>OR</b>：任何一個字詞出現，例如 (施政報告 OR 財政預算)。</p>
      <p><b>NOT</b>：不可出現，例如 房屋 NOT 公屋。不可以只有 NOT。</p>
      <p><b>"…"</b>：完全相同的詞組，例如 "一語千金"。</p>
      <p>「同一段落」「同一句」：字詞要在同一段或同一句內出現。</p>
    </div>
  );
}

type Check = { ok: boolean; problems: { kind: string; words?: unknown; detail?: unknown }[] };

function SearchForm({ init }: { init: Form }) {
  const navigate = useNavigate();
  const [f, setF] = useState<Form>(init);
  useEffect(() => { setF(init); }, [init]);
  useEffect(() => { lastForm = f; }, [f]);
  const set = (patch: Partial<Form>) => setF(x => ({ ...x, ...patch }));
  const o = useQuery(optionsQuery);
  const cfg = useQuery(configQuery);
  const conf = configOf(cfg.data?.config);
  const on = (k: string) => String(conf[k] ?? "").toLowerCase() === "true";
  const M = o.data?.media;
  const [help, setHelp] = useState(false);
  const [check, setCheck] = useState<{ busy: boolean; res?: Check; err?: string } | null>(null);
  const dlg = useRef<HTMLDialogElement>(null);
  const [first] = useState(() => ({ media: init.mt.length + init.mc.length + init.mr.length + init.media.length > 0 || init.lang !== "all" ||
    !!(init.author || init.column || init.section), neg: init.neg.length > 0, other: !init.similar || !init.cluster || !init.summary || init.size !== "50" }));
  const hotpicks = useMemo(() => {
    const v = o.data?.hotpicks as Record<string, unknown> | undefined;
    const arr = (v && (Array.isArray(v) ? v : (Object.values(v).find(Array.isArray) as unknown[] | undefined))) || [];
    return (arr as Record<string, unknown>[]).map(x => ({ id: String(x.id ?? x.hotPickId ?? x.value ?? ""), name: String(x.name ?? x.text ?? x.title ?? "") }))
      .filter(x => x.id && x.name);
  }, [o.data]);
  const empty = f.mode === "quick" ? !f.q.trim() : !(f.and.trim() || f.or.trim());
  const go = async () => {
    const p = paramsOf(f);
    await navigate({ to: "/app/wisers", search: p as never, replace: true, resetScroll: false });
    navigate({ to: "/app/wisers/search", search: p as never });
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (empty) return;
    lastForm = f;
    setCheck({ busy: true });
    try {
      const res = await getJSON<Check>("check?" + qsOf(paramsOf(f)), 100);
      if (res.ok) { setCheck(null); return go(); }
      setCheck({ busy: false, res });
    } catch (err) {
      setCheck({ busy: false, err: (err as Error).message });
    }
  };
  useEffect(() => {
    const d = dlg.current;
    if (!d) return;
    if (check && !check.busy && !d.open) d.showModal();
    if (!check && d.open) d.close();
  }, [check]);
  const optNote = o.isPending ? <Wait className="note" what="載入媒體選項" label={"載入媒體選項…" + LOOKUP} slowMs={100_000} />
    : o.isError ? <Wait className="note" what="載入媒體選項" failed onRetry={() => o.refetch()} /> : null;
  return (
    <form id="wf" className="wform" onSubmit={submit}>
      <div className="modes">
        <Seg label="搜尋方式" value={f.mode} set={v => set({ mode: v })} options={[["quick", "快速搜尋"], ["logic", "邏輯搜尋"]]} />
        <button type="button" className="ib" aria-expanded={help} onClick={() => setHelp(!help)}><I n="help" label="搜尋語法" /></button>
      </div>
      {help && <Guide />}
      {f.mode === "quick" ? <KeywordBox value={f.q} set={v => set({ q: v })} /> : (
        <div className="logic">
          {(([["and", "包含全部字詞"], ["or", "包含任何字詞"], ["nand", "不包含全部字詞"], ["nor", "不包含任何字詞"]]) as const).map(([k, t]) => (
            <label className="fld" key={k}>{t}<input name={k} value={f[k]} onChange={e => set({ [k]: e.target.value } as Partial<Form>)} autoComplete="off" /></label>
          ))}
          <p className="hint">字詞之間以空格分開。不可以只有「不包含」。</p>
        </div>
      )}
      <div className="line">
        <span className="lbl">搜尋範圍</span>
        <label className="cb"><input type="checkbox" checked={f.title} onChange={e => set({ title: e.target.checked })} />標題</label>
        <label className="cb"><input type="checkbox" checked={f.content} onChange={e => set({ content: e.target.checked })} />內文</label>
        <Seg label="字詞距離" value={f.within} set={v => set({ within: v })} options={WITHIN} />
      </div>
      <div className="line">
        <span className="lbl">日期</span>
        <select name="period" value={f.period} onChange={e => set({ period: e.target.value })} aria-label="日期">
          {PERIODS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
        </select>
        {f.period === "custom" && <span className="range">
          <input type="date" name="from" value={f.from} max={f.to || undefined} onChange={e => set({ from: e.target.value })} aria-label="由" />
          <span>至</span>
          <input type="date" name="to" value={f.to} min={f.from || undefined} onChange={e => set({ to: e.target.value })} aria-label="至" />
        </span>}
        {cfg.data && <span className="hint">可搜尋最近 {String(conf.maxSearchYears ?? 5)} 年</span>}
      </div>
      <div className="line">
        <span className="lbl">排序</span>
        <Seg label="排序" value={f.sort} set={v => set({ sort: v })} options={SORTS.filter(([v]) => v !== "tracking" || f.neg.length > 0)} />
      </div>
      <Keep k="media" className="adv media" open={first.media} summary="媒體／作者">
        <h4>媒體種類</h4>{optNote}<OptTree name="mt" entries={M?.typeList} sel={f.mt} set={v => set({ mt: v })} />
        <h4>媒體類別</h4><OptTree name="mc" entries={M?.categoryList} sel={f.mc} set={v => set({ mc: v })} />
        <h4>地區</h4><OptTree name="mr" entries={M?.regionList} sel={f.mr} set={v => set({ mr: v })} />
        <h4>媒體語言</h4>
        <Seg label="媒體語言" value={f.lang} set={v => set({ lang: v })} options={LANGS} />
        <MediaPicker media={f.media} set={m => set({ media: m })} lang={f.lang} />
        <div className="row3">{(([["author", "作者"], ["column", "專欄"], ["section", "版面"]]) as const).map(([k, t]) => (
          <label className="fld" key={k}>{t}<input name={k} value={f[k]} onChange={e => set({ [k]: e.target.value } as Partial<Form>)} /></label>
        ))}</div>
      </Keep>
      {on("enableHotPick") && hotpicks.length > 0 && <Keep k="hot" className="adv hot" open={f.hot.length > 0}
        summary={<>熱選{f.hot.length > 0 && <span className="cnt">已選 {f.hot.length}</span>}</>}>
        <div className="cbs">{hotpicks.map(h => (
          <label className="cb" key={h.id}><input type="checkbox" checked={f.hot.includes(h.id)} onChange={() => set({ hot: toggle(f.hot, h.id) })} />{h.name}</label>
        ))}</div>
      </Keep>}
      {TREES.map(t => <LabelTree key={t.key} t={t} chosen={f.labs[t.key] || []} set={v => set({ labs: { ...f.labs, [t.key]: v } })} />)}
      <Keep k="neg" className="adv neg" open={first.neg} summary={<>負面新聞{f.neg.length > 0 && <span className="cnt">已選 {f.neg.length}</span>}</>}>
        <p className="hint">只顯示含所選類別負面字詞的文章。選了類別後可按「追蹤」排序。</p>
        {optNote}<OptTree name="neg" entries={o.data?.negative} sel={f.neg} set={v => set({ neg: v })} />
      </Keep>
      <Keep k="other" className="adv other" open={first.other} summary="其他選項">
        <label className="cb"><input type="checkbox" name="similar" checked={f.similar} onChange={e => set({ similar: e.target.checked })} />同時搜尋相似字詞</label>
        <label className="cb"><input type="checkbox" checked={f.cluster} onChange={e => set({ cluster: e.target.checked })} />合併相似文章</label>
        <label className="cb"><input type="checkbox" checked={f.summary} onChange={e => set({ summary: e.target.checked })} />顯示摘要</label>
        <div className="line"><span className="lbl">每頁</span><Seg label="每頁篇數" value={f.size} set={v => set({ size: v })} options={SIZES.map(s => [s, `${s} 篇`] as [string, string])} /></div>
      </Keep>
      <div className="acts">
        <button type="button" className="sec" onClick={() => { lastForm = null; setF(formOf({})); }}><I n="reset" />重設</button>
        <button type="submit" className="go" disabled={empty || !!check?.busy}><I n="search" />{check?.busy ? "檢查中…" : "搜尋"}</button>
      </div>
      <dialog ref={dlg} className="wdlg" onClose={() => setCheck(null)}>
        {check?.err ? <p>未能檢查這次搜尋：{check.err}</p> : <>
          <h3>搜尋前請先修改</h3>
          {(check?.res?.problems || []).map((p, i) => (
            <p key={i}>{p.kind === "stop" ? "以下字詞太常見，Wisers 會略去：" : p.kind === "illegal" ? "以下字元不可使用：" : "日期超出帳戶可搜尋的範圍："}
              {p.words ? <b>{([] as unknown[]).concat(p.words).join("、")}</b> : null}</p>
          ))}
        </>}
        <div className="acts">
          <button type="button" className="sec" onClick={() => setCheck(null)}>返回修改</button>
          {(check?.err || check?.res?.problems.every(p => p.kind === "stop")) &&
            <button type="button" className="go" onClick={() => { setCheck(null); go(); }}>仍然搜尋</button>}
        </div>
      </dialog>
    </form>
  );
}

export function WisersForm() {
  const s = useSearch({ strict: false }) as WParams;
  const has = !!(s.q || s.and || s.or);
  const init = useMemo(() => (has ? formOf(s) : lastForm || formOf({})), [qsOf(s)]);  // eslint-disable-line react-hooks/exhaustive-deps
  return <Frame>
    <p className="sub">在 YYC 的 WisersOne 帳戶上搜尋，每次搜尋都計算帳戶的額度（同一搜尋 24 小時內不再計）。只讀：不會儲存搜尋、加入文件夾或發送電郵。</p>
    <SearchForm init={init} />
  </Frame>;
}
