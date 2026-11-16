#!/usr/bin/env python3
"""The proof view's keys and its browser-kept notes (web-app/src/lib/proofKeys.ts; Deyao, 2026-10-21: the whole check
works without a mouse; notes saved locally and downloaded as a file). Runs the module in Node (npx tsx); no database.

    python3 tests/test_proof_keys.py

1. keyAction: every listed key maps to its action; nothing fires with Cmd/Ctrl/Alt (the browser's own shortcuts) or
   for Shift+letter; while typing only Enter (save), Cmd/Ctrl+Enter (save) and Esc (cancel) count, Shift+Enter is a
   new line; isTyping knows text fields from buttons and checkboxes.
2. The notes: upsert adds a record, an emptied one is dropped; pending counts records changed since the download;
   exportNotes names the file yyc-proof-notes-YYYY-MM-DD-HHMM.json in Hong Kong time, lists every record and marks
   them downloaded; loadNotes / saveNotes survive a storage that throws.
"""
import json, os, subprocess, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "web-app")

SCRIPT = r"""
import * as K from "./src/lib/proofKeys.ts";
const a = (key, mods = {}, typing = false) => { const r = K.keyAction({ key, ...mods }, typing); return r ? JSON.stringify(r) : null; };
const out = {};
for (const k of ["ArrowRight", "j", "ArrowLeft", "k", "]", "[", "x", "!", "o", "3", "0", "n", "v", "m", "l", " ", "f", "d", "c", "?", "Enter", "Escape", "q"])
  out[k] = a(k);
out.shiftSpace = a(" ", { shiftKey: true });
out.metaF = a("f", { metaKey: true }); out.ctrlD = a("d", { ctrlKey: true }); out.altJ = a("j", { altKey: true });
out.shiftX = a("X", { shiftKey: true });
out.typing = { j: a("j", {}, true), enter: a("Enter", {}, true), shiftEnter: a("Enter", { shiftKey: true }, true),
  metaEnter: a("Enter", { metaKey: true, shiftKey: true }, true), esc: a("Escape", {}, true), x: a("x", {}, true) };
const el = (tag, type) => ({ closest: (sel) => {
  if (sel.startsWith("textarea")) return tag === "textarea" ? {} : null;
  if (sel === "input") return tag === "input" ? { type } : null;
  return null; } });
out.isTyping = [K.isTyping(el("textarea")), K.isTyping(el("input", "text")), K.isTyping(el("input", "checkbox")),
  K.isTyping(el("button")), K.isTyping(null)];
const base = { id: 7, url: "https://x/7", title: "t", source: "s", sid: "sid", published: null, seen: null,
  scores: [{ stream: "CEHK", name: "CEHK", score: 3, stage1: 3, threshold: 50, passed: false }] };
let n = K.upsert({}, base, r => { r.verdict = "wrong"; }, "2026-10-21T04:00:00.000Z");
out.afterFlag = [Object.keys(n), n[7].verdict, n[7].created, K.pending(n)];
const ex = K.exportNotes(n, "2026-10-21T04:05:30.000Z");
out.export = [ex.name, JSON.parse(ex.body).records.map(r => [r.id, r.verdict, r.scores[0].score, "downloaded" in r]), K.pending(ex.notes)];
n = K.upsert(ex.notes, base, r => { r.note = "later"; }, "2026-10-21T04:06:00.000Z");
out.changedAfter = K.pending(n);
n = K.upsert(n, base, r => { r.verdict = null; r.note = " "; }, "2026-10-21T04:07:00.000Z");
out.emptied = Object.keys(n).length;
const bad = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("full"); }, removeItem() {} };
out.storage = [JSON.stringify(K.loadNotes(bad)), K.saveNotes(bad, {}), K.saveNotes(null, {})];
const mem = {}; const ok = { getItem: k => mem[k] ?? null, setItem: (k, v) => { mem[k] = v; }, removeItem: k => { delete mem[k]; } };
K.saveNotes(ok, ex.notes); out.roundTrip = Object.keys(K.loadNotes(ok));
out.keysListed = K.KEYS.map(k => k[0]);
console.log(JSON.stringify(out));
"""


def run():
    r = subprocess.run(["npx", "tsx", "--input-type=module", "-e", SCRIPT], cwd=APP, capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError(r.stderr[-3000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


class Keys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.o = run()

    def test_keys(self):
        o = self.o
        A = lambda **k: json.dumps(k, separators=(",", ":"))  # noqa: E731
        want = {"ArrowRight": A(a="next"), "j": A(a="next"), "ArrowLeft": A(a="prev"), "k": A(a="prev"),
                "]": A(a="nextSource"), "[": A(a="prevSource"), "x": A(a="flag"), "!": A(a="flag"), "o": A(a="ok"),
                "3": A(a="stream", n=3), "0": None, "n": A(a="note"), "v": A(a="view"), "m": A(a="more"), "l": A(a="links"),
                " ": A(a="pageText", dir=1), "f": A(a="fullscreen"), "d": A(a="download"), "c": A(a="clear"),
                "?": A(a="help"), "Enter": A(a="open"), "Escape": A(a="escape"), "q": None}
        for k, v in want.items():
            self.assertEqual(o[k], v, k)
        self.assertEqual(o["shiftSpace"], A(a="pageText", dir=-1))
        for k in ("metaF", "ctrlD", "altJ", "shiftX"):
            self.assertIsNone(o[k], k)
        t = o["typing"]
        self.assertEqual((t["j"], t["x"], t["shiftEnter"]), (None, None, None))
        self.assertEqual((t["enter"], t["metaEnter"], t["esc"]), (A(a="noteSave"), A(a="noteSave"), A(a="noteCancel")))
        self.assertEqual(o["isTyping"], [True, True, False, False, False])
        for k in ("X or !", "N", "D", "C", "F", "?", "M", "V", "O", "1–9"):
            self.assertIn(k, o["keysListed"])

    def test_notes(self):
        o = self.o
        self.assertEqual(o["afterFlag"], [["7"], "wrong", "2026-10-21T04:00:00.000Z", 1])
        self.assertEqual(o["export"], ["yyc-proof-notes-2026-10-21-1205.json", [[7, "wrong", 3, False]], 0])
        self.assertEqual(o["changedAfter"], 1)
        self.assertEqual(o["emptied"], 0)
        self.assertEqual(o["storage"], ["{}", False, False])
        self.assertEqual(o["roundTrip"], ["7"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
