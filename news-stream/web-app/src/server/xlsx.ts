// A small dependency-free .xlsx writer that runs on the Worker (the same layout as openpyxl's output):
// sheets of [name, header, rows] with optional meta rows (bold keys) above, the header bold white on green, links
// on cells that start with http, column widths from the header, the panes frozen under the header.
// The zip is deflated with CompressionStream("deflate-raw") (workerd and Node both have it).

export type Cell = string | number | null | undefined;
export type Sheet = [name: string, header: string[], rows: Cell[][]];

const enc = new TextEncoder();

// XML 1.0 forbids most control characters (openpyxl refuses them): they are left out
const xml = (s: string) => s.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]/g, "")
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function colName(i: number): string {  // 1 → A
  let s = "";
  for (let n = i; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s;
}

const STYLE = { plain: 0, bold: 1, head: 2 };

function sheetXml(header: string[], rows: Cell[][], meta: [string, Cell][] | null) {
  const out: string[] = [], links: [string, string][] = [];
  let r = 0;
  const row = (cells: Cell[], style: (ci: number) => number, link: boolean) => {
    r++;
    const cs: string[] = [];
    cells.forEach((v, i) => {
      const ref = colName(i + 1) + r, s = style(i), st = s ? ` s="${s}"` : "";
      if (v === null || v === undefined || v === "") { if (s) cs.push(`<c r="${ref}"${st}/>`); return; }  // openpyxl keeps no empty string
      if (typeof v === "number" && Number.isFinite(v)) cs.push(`<c r="${ref}"${st}><v>${v}</v></c>`);
      else {
        const t = String(v);
        cs.push(`<c r="${ref}"${st} t="inlineStr"><is><t xml:space="preserve">${xml(t)}</t></is></c>`);
        if (link && t.startsWith("http")) links.push([ref, t]);
      }
    });
    out.push(`<row r="${r}">${cs.join("")}</row>`);
  };
  if (meta) {
    for (const [k, v] of meta) row([k, v], i => (i === 0 ? STYLE.bold : STYLE.plain), false);
    r++;  // ws.append([]): an empty row
  }
  const r0 = r + 1;
  row(header, () => STYLE.head, false);
  for (const x of rows) row(x.map(v => (v == null ? "" : typeof v === "number" ? v : String(v))), () => STYLE.plain, true);
  const cols = header.map((h, i) => `<col min="${i + 1}" max="${i + 1}" width="${Math.min(60, Math.max(10, Array.from(String(h)).length + 4))}" customWidth="1"/>`).join("");
  const pane = `<pane ySplit="${r0}" topLeftCell="A${r0 + 1}" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A${r0 + 1}" sqref="A${r0 + 1}"/>`;
  const hl = links.length ? `<hyperlinks>${links.map(([ref], i) => `<hyperlink ref="${ref}" r:id="rId${i + 1}"/>`).join("")}</hyperlinks>` : "";
  const body = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetViews><sheetView workbookViewId="0">${pane}</sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/>${cols ? `<cols>${cols}</cols>` : ""}<sheetData>${out.join("")}</sheetData>${hl}</worksheet>`;
  const rels = links.length ? `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${links.map(([, url], i) => `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="${xml(url)}" TargetMode="External"/>`).join("")}</Relationships>` : null;
  return { body, rels };
}

const STYLES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="3"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font><font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font><font><b/><color rgb="00FFFFFF"/><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts><fills count="3"><fill><patternFill/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="001F7A4D"/><bgColor rgb="001F7A4D"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`;

/** web.xlsx: the workbook's bytes */
export async function xlsx(sheets: Sheet[], meta: [string, Cell][] | null = null): Promise<Uint8Array> {
  const files: [string, string][] = [];
  const names = sheets.map(([n]) => Array.from(n).slice(0, 31).join(""));
  sheets.forEach(([, header, rows], i) => {
    const { body, rels } = sheetXml(header, rows, meta);
    files.push([`xl/worksheets/sheet${i + 1}.xml`, body]);
    if (rels) files.push([`xl/worksheets/_rels/sheet${i + 1}.xml.rels`, rels]);
  });
  const head = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n`;
  files.unshift(
    ["[Content_Types].xml", head + `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${names.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("")}</Types>`],
    ["_rels/.rels", head + `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
    ["xl/workbook.xml", head + `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView activeTab="0"/></bookViews><sheets>${names.map((n, i) => `<sheet name="${xml(n)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join("")}</sheets></workbook>`],
    ["xl/_rels/workbook.xml.rels", head + `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${names.map((_, i) => `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join("")}<Relationship Id="rId${names.length + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`],
    ["xl/styles.xml", STYLES],
  );
  return zip(files.map(([n, s]) => [n, enc.encode(s)]));
}

// ---- zip (deflate) ----

const CRC = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();
function crc32(b: Uint8Array) {
  let c = 0xffffffff;
  for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

async function deflate(b: Uint8Array): Promise<Uint8Array> {
  const s = new Blob([b as BlobPart]).stream().pipeThrough(new CompressionStream("deflate-raw"));
  return new Uint8Array(await new Response(s).arrayBuffer());
}

async function zip(files: [string, Uint8Array][]): Promise<Uint8Array> {
  const parts: Uint8Array[] = [], central: Uint8Array[] = [];
  let off = 0;
  for (const [name, data] of files) {
    const nm = enc.encode(name), comp = await deflate(data), crc = crc32(data);
    const h = new DataView(new ArrayBuffer(30));
    h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x0800, true); h.setUint16(8, 8, true);
    h.setUint16(10, 0, true); h.setUint16(12, 0x21, true);  // 1980-01-01 00:00
    h.setUint32(14, crc, true); h.setUint32(18, comp.length, true); h.setUint32(22, data.length, true);
    h.setUint16(26, nm.length, true); h.setUint16(28, 0, true);
    const c = new DataView(new ArrayBuffer(46));
    c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true);
    c.setUint16(10, 8, true); c.setUint16(12, 0, true); c.setUint16(14, 0x21, true);
    c.setUint32(16, crc, true); c.setUint32(20, comp.length, true); c.setUint32(24, data.length, true);
    c.setUint16(28, nm.length, true); c.setUint32(42, off, true);
    parts.push(new Uint8Array(h.buffer), nm, comp);
    central.push(new Uint8Array(c.buffer), nm);
    off += 30 + nm.length + comp.length;
  }
  const cdSize = central.reduce((a, b) => a + b.length, 0);
  const e = new DataView(new ArrayBuffer(22));
  e.setUint32(0, 0x06054b50, true); e.setUint16(8, files.length, true); e.setUint16(10, files.length, true);
  e.setUint32(12, cdSize, true); e.setUint32(16, off, true);
  const all = [...parts, ...central, new Uint8Array(e.buffer)];
  const out = new Uint8Array(all.reduce((a, b) => a + b.length, 0));
  let p = 0;
  for (const b of all) { out.set(b, p); p += b.length; }
  return out;
}

/** web.attachment: the Content-Disposition of a download (an ASCII fallback name, the real name UTF-8 in filename*) */
export function attachment(name: string): string {
  const dot = name.lastIndexOf(".");
  const [stem, ext] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
  const plain = stem.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "") || "download";
  const q = encodeURIComponent(name).replace(/[!'()*]/g, c => "%" + c.charCodeAt(0).toString(16).toUpperCase());
  return `attachment; filename="${plain}${/^[.A-Za-z0-9]*$/.test(ext) ? ext : ""}"; filename*=UTF-8''${q}`;
}
