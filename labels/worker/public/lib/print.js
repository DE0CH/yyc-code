// Stage 3 → 4: the 1-bit bitmap → NIIMBOT M3 over Web Bluetooth (Chrome or Edge on a laptop; README.md "Printing").
// Shared by every design: designs never carry a copy of this file.
//
// Protocol per niimbluelib (github.com/MultiMote/niimbluelib): frames 55 55 cmd len data xor AA AA on the one
// notify/write characteristic of SERVICE. The M3 (model 6400, protocol 3.02, v5) has no task of its own in
// niimbluelib and takes its "D110M_V4" sequence; the image itself goes in confirmed 0xa7 blocks as NIIMBOT's own SDK
// sends it (blockPackets). Each label is its own print job, tested on the M3 (density 1, 848×590 dots, 2026-11-05).

export const SERVICE = "e7810a71-73ae-499d-8c15-faa9aef0c3f2";
const STALL_MS = 20000; // a label showing no progress this long counts as stuck

// Printer error codes (DB replies, A3 byte 6), names per niimbluelib PrinterErrorCode, in the staff's language.
export const PRINTER_ERRORS = {
  0x01: "打印機蓋未關好", 0x02: "沒有標籤紙", 0x03: "電量不足", 0x07: "打印頭過熱", 0x08: "出紙異常：請重新放置標籤紙及碳帶",
  0x09: "打印機忙碌中", 0x0d: "沒有碳帶", 0x0e: "碳帶型號不符", 0x0f: "碳帶已用完", 0x10: "標籤紙型號不符",
  0x11: "碳帶已用完或標籤紙未放妥", // what the M3 reported when its ribbon ran out (2026-10-29)
  0x35: "非專用碳帶",
};

export class PrinterError extends Error {
  constructor(message, code) { super(message); this.code = code; }
}

export function frame(cmd, data = []) {
  const d = Uint8Array.from(data);
  let chk = cmd ^ d.length;
  for (const b of d) chk ^= b;
  const out = new Uint8Array(d.length + 7);
  out.set([0x55, 0x55, cmd, d.length]);
  out.set(d, 4);
  out.set([chk, 0xaa, 0xaa], 4 + d.length);
  return out;
}

const u16 = (v) => [(v >> 8) & 0xff, v & 0xff];
export const BLOCK = 4096; // the M3's MaxImageCompressBytes (capability 0x23): the most raw bitmap bytes in one block

// An LZO1X stream holding src as one literal run: any LZO1X decoder accepts it (no compression).
export function lzoLiteral(src) {
  const t = src.length, head = [];
  if (t <= 238) head.push(17 + t);
  else { let tt = t - 18; head.push(0); while (tt > 255) { tt -= 255; head.push(0); } head.push(tt); }
  const out = new Uint8Array(head.length + t + 3);
  out.set(head); out.set(src, head.length); out.set([0x11, 0, 0], head.length + t);
  return out;
}

// 0xa7 CompressImage: 55 55 a7 len(u32) data xor aa aa.
export function frameA7(data) {
  const n = data.length, out = new Uint8Array(n + 10);
  out.set([0x55, 0x55, 0xa7, (n >>> 24) & 0xff, (n >>> 16) & 0xff, (n >>> 8) & 0xff, n & 0xff]);
  out.set(data, 7);
  let chk = 0; for (let i = 2; i < n + 7; i++) chk ^= out[i];
  out.set([chk, 0xaa, 0xaa], n + 7);
  return out;
}

// The label's rows as NIIMBOT's own SDK sends them to the M3 (cPrinterV6, image compression): raw bitmap rows, as
// many whole rows as fit in BLOCK bytes, each block LZO'd and sent as 0xa7 [block u16][lzo]. The printer answers each
// block with 0x88 [block u16][status: 1 decoded, 2 send it again]..., so a block lost or damaged over Bluetooth is
// sent again before it prints. Row packets (0x85 per row) had no such check and printed with rows missing.
export function blockPackets({ width, height, bits }) {
  const bpr = Math.ceil(width / 8), per = Math.floor(BLOCK / bpr), out = [];
  for (let i = 0, y = 0; y < height; i++, y += per) {
    const lzo = lzoLiteral(bits.subarray(y * bpr, Math.min(height, y + per) * bpr));
    const data = new Uint8Array(lzo.length + 2);
    data.set(u16(i)); data.set(lzo, 2);
    out.push(frameA7(data));
  }
  return out;
}

// Commands before the rows of one label (cmd, data, wait for the reply). labelType: the loaded roll's own type
// (Printer.labelType); a transparent roll (5) sent as 1 (with gaps) made the M3 miss the gap and jam (2026-10-30).
export function jobStart({ width, height }, { density = 1, labelType = 1 } = {}) {
  return [
    [0x23, [labelType], true], [0x21, [density], true],
    [0x01, [...u16(1), 0, 0, 0, 0, 0, 0, 0], true], // pages, 4×00, colour, speed, flag
    [0xa3, [1], false], [0x03, [1], true],
    [0x13, [...u16(height), ...u16(width), ...u16(1), ...u16(0), 0, 0, 0, ...u16(0)], true], // rows, cols, copies, cut height, cut type, 00, send-all, part height
  ];
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const QUIET = new Set([0xdd, 0xde, 0xdf, 0xd9, 0xd3]); // heartbeats / check-line chatter

export class Printer {
  static async connect(bluetooth = navigator.bluetooth) {
    if (!bluetooth) throw new PrinterError("此瀏覽器不支援藍牙打印：請使用 Chrome 或 Edge");
    const device = await bluetooth.requestDevice({ filters: [{ namePrefix: "M3" }], optionalServices: [SERVICE] });
    const server = await device.gatt.connect();
    const service = await server.getPrimaryService(SERVICE);
    const chars = await service.getCharacteristics();
    const char = chars.find((c) => c.properties.notify) || chars[0];
    const p = new Printer(device, char);
    await char.startNotifications();
    char.addEventListener("characteristicvaluechanged", (e) => p.#onNotify(new Uint8Array(e.target.value.buffer)));
    return p;
  }

  #buf = new Uint8Array(0);
  #waiters = [];
  #queue = [];

  constructor(device, char) { this.device = device; this.char = char; this.name = device.name; }

  get connected() { return !!this.device.gatt?.connected; }

  #onNotify(data) {
    const b = new Uint8Array(this.#buf.length + data.length);
    b.set(this.#buf); b.set(data, this.#buf.length);
    this.#buf = b;
    for (;;) {
      let start = -1;
      for (let i = 0; i + 1 < this.#buf.length; i++) if (this.#buf[i] === 0x55 && this.#buf[i + 1] === 0x55) { start = i; break; }
      if (start < 0) { this.#buf = new Uint8Array(0); return; }
      this.#buf = this.#buf.subarray(start);
      if (this.#buf.length < 4 || this.#buf.length < this.#buf[3] + 7) return;
      const n = this.#buf[3] + 7;
      const pkt = { code: this.#buf[2], body: this.#buf.slice(4, 4 + this.#buf[3]) };
      this.#buf = this.#buf.slice(n);
      const w = this.#waiters.shift();
      if (w) w(pkt); else this.#queue.push(pkt);
    }
  }

  #next(timeout) {
    if (this.#queue.length) return Promise.resolve(this.#queue.shift());
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => { this.#waiters = this.#waiters.filter((w) => w !== done); reject(new PrinterError("打印機沒有回應")); }, timeout);
      const done = (p) => { clearTimeout(t); resolve(p); };
      this.#waiters.push(done);
    });
  }

  async send(pkt) { await this.char.writeValueWithoutResponse(pkt); }

  // The next reply with one of codes within timeout ms, or null; other replies are dropped.
  async #reply(codes, timeout) {
    for (const end = Date.now() + timeout; ;) {
      const left = end - Date.now();
      if (left <= 0) return null;
      try { const p = await this.#next(left); if (codes.includes(p.code)) return p; }
      catch { return null; }
    }
  }

  // One 0xa7 block in 240-byte writes, then the printer's 0x88 for it: status 1 = decoded, anything else = send it
  // again (3 tries); no answer in 5 s = ask with 0x89 [block u16].
  async #sendBlock(i, pkt) {
    for (let attempt = 1; attempt <= 3; attempt++) {
      for (let o = 0; o < pkt.length; o += 240) { await this.send(pkt.subarray(o, o + 240)); await sleep(8); }
      let r = await this.#reply([0x88, 0xdb], 5000);
      if (!r) { await this.send(frame(0x89, u16(i))); r = await this.#reply([0x88, 0xdb], 3000); }
      if (!r) throw new PrinterError("打印機沒有確認收到標籤資料");
      if (r.code === 0xdb) throw new PrinterError(PRINTER_ERRORS[r.body[0]] || `打印機錯誤 0x${(r.body[0] ?? 0).toString(16)}`, r.body[0]);
      if (((r.body[0] << 8) | r.body[1]) === i && r.body[2] === 1) return;
    }
    throw new PrinterError("標籤資料傳送失敗：請重新連接打印機再試");
  }

  async ask(cmd, data = [1], timeout = 3000) {
    this.#queue = [];
    await this.send(frame(cmd, data));
    for (;;) {
      const { code, body } = await this.#next(timeout);
      if (code === 0xdb) throw new PrinterError(PRINTER_ERRORS[body[0]] || `打印機錯誤 0x${(body[0] ?? 0).toString(16)}`, body[0]);
      if (code === 0x00) throw new PrinterError(`打印機不支援指令 0x${cmd.toString(16)}`);
      if (QUIET.has(code)) continue;
      return { code, body };
    }
  }

  // The loaded roll's label type from its tag (1A): 1 with gaps, 2 black, 3 continuous, 5 transparent, … ; 1 when
  // the tag can't be read.
  async labelType() {
    try {
      const { body: r } = await this.ask(0x1a);
      let i = 9 + r[8]; i += 1 + r[i];
      if (r.length >= i + 5 && r[i + 4]) return r[i + 4];
    } catch { /* no tag */ }
    return 1;
  }

  // One label: start, page, rows, end, wait until the printer reports it printed, finish. onProgress hears each
  // stage: { phase: "prepare" }, { phase: "send", pct } as the printer confirms each block, { phase: "print", pct, feed }
  // from the printer's own status (A3: print and feed percent), { phase: "finish" }.
  async printLabel(bmp, { density = 1, labelType, onProgress = () => {} } = {}) {
    if (bmp.width !== 848 || bmp.height !== 590) throw new PrinterError(`標籤圖像尺寸不符 (${bmp.width}×${bmp.height})`);
    onProgress({ phase: "prepare" });
    const lt = labelType ?? (this._labelType ??= await this.labelType());
    await this.ask(0xc1);
    for (const [c, d, wait] of jobStart(bmp, { density, labelType: lt })) {
      if (wait) await this.ask(c, d); else { await this.send(frame(c, d)); await sleep(50); }
    }
    const blocks = blockPackets(bmp);
    this.#queue = [];
    for (let i = 0; i < blocks.length; i++) {
      await this.#sendBlock(i, blocks[i]);
      onProgress({ phase: "send", pct: Math.round(((i + 1) / blocks.length) * 100) });
    }
    onProgress({ phase: "print", pct: 0, feed: 0 });
    await this.ask(0xe3, [1], 10000);
    // The M3 can stop mid-page (ribbon out, label stuck) and then reports the same progress forever: fail on its
    // error byte (A3 byte 6) or after STALL_MS without progress.
    let last = "", since = Date.now();
    for (;;) {
      await sleep(300);
      const { body: st } = await this.ask(0xa3, [1], 5000);
      if (((st[0] << 8) | st[1]) >= 1) break;
      onProgress({ phase: "print", pct: Math.min(100, st[2] ?? 0), feed: Math.min(100, st[3] ?? 0) });
      if (st.length > 6 && st[6]) throw new PrinterError(PRINTER_ERRORS[st[6]] || `打印機錯誤 0x${st[6].toString(16)}`, st[6]);
      const key = Array.from(st.subarray(0, 4)).join(",");
      if (key !== last) { last = key; since = Date.now(); }
      else if (Date.now() - since > STALL_MS) throw new PrinterError("打印機已停止出紙：請檢查碳帶及標籤紙");
    }
    onProgress({ phase: "finish" });
    await this.ask(0xf3);
  }

  disconnect() { this.device.gatt?.disconnect(); }
}
