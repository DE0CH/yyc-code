#!/usr/bin/env python3
"""Draws this template's labels: design.json + a guest list -> one bitmap per guest, ready to send to print.

    python3 render.py --design design.json --list guests.csv --fonts fonts/ --out out/
    python3 render.py --design design.json --samples --fonts fonts/ --out examples/

Each label is written as out/<nnn>-<_id>.bin (848 x 590 dots, 1 bit per dot, 1 = black, rows of 106 bytes, the
leftmost dot in the highest bit), a grey preview out/<nnn>-<_id>.png to look at, and out/all.bin (every label in
list order, ready to send in one request). out/report.json lists each label's template, rows and warnings.

design.json's format is in design.md. Needs Pillow and fontTools.
"""
import argparse, csv, io, json, os, re, sys
from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont

W, H = 848, 590                 # the NIIMBOT M3's printable area: 72 x 50 mm at 300 dots per inch
DPMM = 300 / 25.4               # dots per mm
PT = 25.4 / 72                  # mm per point
FALLBACK = "Noto Sans HK"
REF = 1000                      # px size widths are measured at, then scaled (sizes stay continuous)

SAMPLES = [
    {"_id": "sample-1", "name": "周潤發", "title": "先生", "institution": "香港電影金像獎協會", "table": "12"},
    {"_id": "sample-2", "name": "歐陽震華", "title": "教授", "institution": "香港科技大學工學院電子及計算機工程學系", "table": "3"},
    {"_id": "sample-3", "name": "Benedict Cumberbatch", "title": "Mr", "institution": "Royal Academy of Dramatic Art", "table": "7"},
    {"_id": "sample-4", "name": "Michelle Yeoh", "title": "Dame", "institution": "The Hong Kong University of Science and Technology", "table": "21"},
]

# ---- guests ------------------------------------------------------------------------------------------------------
FIELDS = ["name", "name_1", "name_2", "title", "institution", "institution_1", "institution_2", "table"]
SPLIT = ["name", "institution"]
COLUMNS = {
    "name": ["name", "姓名", "名字", "名稱"],
    "title": ["title", "職銜", "稱謂", "職位"],
    "institution": ["institution", "機構", "organisation", "organization", "公司", "單位"],
    "table": ["table", "枱號", "桌號", "席號"],
    "lang": ["language", "lang", "語言"],
}


def s(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def column(row, field):
    for k in COLUMNS.get(field, []):
        for c, v in row.items():
            if c.strip().lower() == k and s(v):
                return s(v)
    return ""


def is_cjk(c):
    o = ord(c)
    return 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x20000 <= o <= 0x3134F


def as_lang(v):
    l = v.strip().lower()
    if l.startswith(("zh", "中", "chinese")):
        return "zh"
    if l.startswith(("en", "英", "english")):
        return "en"
    return None


def guest_of(row, ov):
    o = ov or {}
    pick = lambda f: s(o[f]) if o.get(f) is not None else column(row, f)
    name = pick("name")
    lang = (as_lang(s(o["lang"])) if o.get("lang") is not None else None) or as_lang(column(row, "lang")) \
        or ("zh" if any(is_cjk(c) for c in name) else "en")
    rows_of = lambda k: {1: "one", 2: "two"}.get(o.get(k) if isinstance(o.get(k), int) else None, "auto")
    text = {k: s(o[k]) for k in ["name_1", "name_2", "institution_1", "institution_2"] if o.get(k) is not None}
    return {
        "id": s(row.get("_id")), "lang": lang, "name": name, "title": pick("title"),
        "institution": pick("institution"), "table": pick("table"),
        "rows": {"name": rows_of("nameRows"), "institution": rows_of("institutionRows")}, "text": text,
        "columns": {k.strip(): s(v) for k, v in row.items() if k != "_id"},
    }


def split(text):
    t = text.strip()
    words = t.split()
    if len(words) < 2:
        k = (len(t) + 1) // 2
        return t[:k], t[k:]
    ln = lambda w: len(" ".join(w))
    best = min(range(1, len(words)), key=lambda k: abs(ln(words[:k]) - ln(words[k:])))
    return " ".join(words[:best]), " ".join(words[best:])


def values_of(g, two):
    v = dict(g["columns"])
    for f in FIELDS:
        v[f] = ""
    name = f'{g["title"]} {g["name"]}' if g["lang"] == "en" and g["title"] else g["name"]
    if g["lang"] == "zh":
        v["title"] = g["title"]
    for f, text in [("name", name), ("institution", g["institution"])]:
        if two.get(f):
            a, b = split(text)
            v[f + "_1"] = g["text"].get(f + "_1", a)
            v[f + "_2"] = g["text"].get(f + "_2", b)
        else:
            v[f] = text
    v["table"] = "" if not g["table"] else (f'Table {g["table"]}' if g["lang"] == "en" else f'{g["table"]} 號枱')
    return v


# ---- fonts -------------------------------------------------------------------------------------------------------
class Fonts:
    """Every .ttf/.otf in a folder, by family and weight (variable fonts set to the weight asked for)."""

    def __init__(self, folder):
        self.files = []
        for n in sorted(os.listdir(folder)):
            if not n.lower().endswith((".ttf", ".otf")):
                continue
            p = os.path.join(folder, n)
            t = TTFont(p, lazy=True)
            names = t["name"]
            fam = names.getDebugName(16) or names.getDebugName(1)
            axes = {a.axisTag: (a.minValue, a.maxValue) for a in t["fvar"].axes} if "fvar" in t else {}
            w = axes.get("wght") or (t["OS/2"].usWeightClass,) * 2
            italic = bool(t["head"].macStyle & 2)
            hhea = t["hhea"]
            self.files.append({"path": p, "family": fam, "weight": w, "italic": italic, "var": "wght" in axes,
                               "cmap": set(t.getBestCmap().keys()), "upem": t["head"].unitsPerEm,
                               "asc": hhea.ascent, "desc": -hhea.descent})
        self.cache = {}

    def pick(self, family, weight):
        best = None
        for i, f in enumerate(self.files):
            if f["family"].lower() != family.lower():
                continue
            w = min(max(weight, f["weight"][0]), f["weight"][1])
            d = abs(w - weight) + (10000 if f["italic"] else 0)
            if best is None or d < best[0]:
                best = (d, i, w)
        return None if best is None else (best[1], best[2])

    def font(self, pick, px):
        key = (pick, round(px, 3))
        if key not in self.cache:
            f = self.files[pick[0]]
            ft = ImageFont.truetype(f["path"], px)
            if f["var"]:
                ft.set_variation_by_axes([pick[1]] + [a["default"] for a in ft.get_variation_axes()[1:]])
            self.cache[key] = ft
        return self.cache[key]


def weight_of(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        named = {"thin": 100, "extralight": 200, "extra-light": 200, "light": 300, "medium": 500, "semibold": 600,
                 "semi-bold": 600, "bold": 700, "extrabold": 800, "extra-bold": 800, "black": 900, "heavy": 900}
        l = v.lower()
        if l in named:
            return float(named[l])
        try:
            return float(l)
        except ValueError:
            return 400.0
    return 400.0


class Faces:
    """A text box's fonts: its own, then Noto Sans HK, then any font with the character."""

    def __init__(self, fonts, family, weight, letter, warnings, eid):
        self.fonts, self.letter, self.chain = fonts, letter, []
        p = fonts.pick(family, weight)
        if p:
            self.chain.append(p)
        else:
            warnings.append({"element": eid, "warning": f'font "{family}" is not loaded: {FALLBACK} used'})
        p = fonts.pick(FALLBACK, weight)
        if p and p not in self.chain:
            self.chain.append(p)
        for i, f in enumerate(fonts.files):
            if not any(c[0] == i for c in self.chain):
                self.chain.append((i, min(max(weight, f["weight"][0]), f["weight"][1])))
        if not self.chain:
            raise SystemExit("no fonts in the fonts folder")

    def runs(self, text):
        out = []
        for c in text:
            p = next((k for k, pk in enumerate(self.chain) if c.isspace() or ord(c) in self.fonts.files[pk[0]]["cmap"]), 0)
            if out and (out[-1][0] == p or c.isspace()):
                out[-1][1] += c
            else:
                out.append([p, c])
        return out

    def run_width(self, k, text, size):
        return self.fonts.font(self.chain[k], REF).getlength(text) * size / REF

    def width(self, text, size):
        rs = self.runs(text)
        n = sum(len(t) for _, t in rs)
        return sum(self.run_width(k, t, size) for k, t in rs) + self.letter * size * max(n - 1, 0)

    def metrics(self):
        f = self.fonts.files[self.chain[0][0]]
        return f["asc"] / f["upem"], f["desc"] / f["upem"]

    def draw(self, d, text, x, base, size, fill):
        pen = x
        for k, t in self.runs(text):
            ft = self.fonts.font(self.chain[k], size * DPMM)
            if self.letter:
                for c in t:
                    d.text((pen * DPMM, base * DPMM), c, font=ft, fill=fill, anchor="ls")
                    pen += self.run_width(k, c, size) + self.letter * size
            else:
                d.text((pen * DPMM, base * DPMM), t, font=ft, fill=fill, anchor="ls")
                pen += self.run_width(k, t, size)


# ---- layout ------------------------------------------------------------------------------------------------------
def num(e, k, d):
    v = e.get(k)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else d


def template_for(design, lang):
    t = design.get("templates") or {}
    for l in [lang, "en" if lang == "zh" else "zh"]:
        if isinstance(t.get(l), dict):
            return l, t[l]
    raise SystemExit("design.json templates has neither zh nor en")


def elements_for(tpl, ov):
    o = ov or {}
    patch, hide = o.get("elements") or {}, set(o.get("hide") or [])
    out = []
    for e in tpl.get("elements") or []:
        e = dict(e)
        for k, v in (patch.get(e.get("id", "")) or {}).items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        if e.get("id") in hide:
            e["hidden"] = True
        if not e.get("hidden"):
            out.append(e)
    return out


FIELD = re.compile(r"\{([^}]*)\}")


def fields_in(text):
    return [m.strip() for m in FIELD.findall(text)]


def fill(tpl, v, unknown):
    """"{name}" -> the value; a line made only of empty fields is dropped."""
    out = []
    for ln in tpl.split("\n"):
        fields = filled = 0

        def sub(m):
            nonlocal fields, filled
            k = m.group(1).strip()
            fields += 1
            if k in v:
                filled += bool(v[k])
                return v[k]
            unknown.add(k)
            return ""

        line = FIELD.sub(sub, ln)
        if fields and not filled and not FIELD.sub("", ln).strip():
            continue
        out.append(line)
    return "\n".join(out)


CLOSING = "，。、：；！？）」』〉》】,."


def tokens(p):
    out, cur = [], ""
    for c in p:
        if is_cjk(c) or "　" <= c <= "〿" or "＀" <= c <= "￯":
            if c in CLOSING and out and not cur:
                out[-1] += c
                continue
            if cur:
                out.append(cur)
                cur = ""
            out.append(c)
        elif c == " ":
            out.append(cur + " ")
            cur = ""
        else:
            cur += c
    if cur:
        out.append(cur)
    return out


def wrap(p, mx, w):
    lines, ln = [], ""
    for t in tokens(p):
        nxt = ln + t
        if ln and w(nxt.rstrip()) > mx:
            lines.append(ln.rstrip())
            ln = t.lstrip()
        else:
            ln = nxt
    lines.append(ln.rstrip())
    return lines


def balanced(p, mx, w):
    """Wrapped lines of even length: the narrowest width needing no more lines than the full width."""
    greedy = wrap(p, mx, w)
    if len(greedy) < 2:
        return greedy
    lo, hi = 0.0, mx
    for _ in range(14):
        mid = (lo + hi) / 2
        l = wrap(p, mid, w)
        if len(l) <= len(greedy) and all(w(x) <= mid + 0.01 for x in l):
            hi = mid
        else:
            lo = mid
    return wrap(p, hi, w)


def lay(e, content, faces):
    """The largest size from `size` down to minScale x size at which every line fits the box."""
    w, h = num(e, "w", 72.0), num(e, "h", 10.0)
    full = num(e, "size", 12.0) * PT
    mn = min(max(num(e, "minScale", 0.5), 0.05), 1.0)
    lh = num(e, "lineHeight", 1.2)
    max_lines = e.get("maxLines") if isinstance(e.get("maxLines"), int) else None
    shrink = e.get("fit", "shrink") != "none"
    scale = 1.0
    while True:
        size = full * scale
        m = lambda t: faces.width(t, size)
        lines = []
        for p in content.split("\n"):
            if e.get("wrap"):
                lines += balanced(p, w, m) if e.get("balance", True) else wrap(p, w, m)
            else:
                lines.append(p)
        fits = all(m(l) <= w + 0.05 for l in lines) and len(lines) * size * lh <= h + 0.05 \
            and (max_lines is None or len(lines) <= max_lines)
        if fits or not shrink or scale <= mn:
            return {"lines": lines, "size": size, "scale": scale, "fits": fits}
        scale = max(scale * 0.97, mn)


def colour(e, k="color"):
    v = e.get(k, "#000")
    return 255 if v in ("white", "#fff", "#ffffff") else None if v == "none" else 0


def draw_text(d, e, laid, faces):
    x, y, w, h = num(e, "x", 0.0), num(e, "y", 0.0), num(e, "w", 72.0), num(e, "h", 10.0)
    lh, size = num(e, "lineHeight", 1.2), laid["size"]
    asc, desc = faces.metrics()
    block = len(laid["lines"]) * size * lh
    va = e.get("valign", "middle")
    top = y if va == "top" else y + h - block if va == "bottom" else y + (h - block) / 2
    c = colour(e)
    for l in laid["lines"]:
        base = top + (size * lh - size * (asc + desc)) / 2 + size * asc
        lw = faces.width(l, size)
        al = e.get("align", "center")
        lx = x if al == "left" else x + w - lw if al == "right" else x + (w - lw) / 2
        if c is not None:
            faces.draw(d, l, lx, base, size, c)
        top += size * lh


def px(v):
    return v * DPMM


def draw_image(img, e, files_dir, warnings):
    src = e.get("src", "")
    path = os.path.join(files_dir, src)
    if not src or not os.path.isfile(path):
        warnings.append({"element": e.get("id", ""), "warning": f"image {src} not found"})
        return
    try:
        pic = Image.open(path).convert("RGBA")
    except Exception:
        warnings.append({"element": e.get("id", ""), "warning": f"image {src} is not a PNG or JPEG"})
        return
    x, y, w, h = px(num(e, "x", 0.0)), px(num(e, "y", 0.0)), px(num(e, "w", 10.0)), px(num(e, "h", 10.0))
    fit = e.get("fit", "contain")
    if fit == "fill":
        nw, nh = w, h
    else:
        k = (max if fit == "cover" else min)(w / pic.width, h / pic.height)
        nw, nh = pic.width * k, pic.height * k
    pic = pic.resize((max(1, round(nw)), max(1, round(nh))), Image.LANCZOS)
    al, va = e.get("align", "center"), e.get("valign", "middle")
    ox = x if al == "left" else x + w - nw if al == "right" else x + (w - nw) / 2
    oy = y if va == "top" else y + h - nh if va == "bottom" else y + (h - nh) / 2
    flat = Image.new("L", pic.size, 255)
    flat.paste(pic.convert("L"), mask=pic.getchannel("A"))
    if fit == "cover":  # keep only what falls inside the box
        l, t = max(0, round(x - ox)), max(0, round(y - oy))
        flat = flat.crop((l, t, l + round(w), t + round(h)))
        ox, oy = max(ox, x), max(oy, y)
    img.paste(flat, (round(ox), round(oy)))


def draw_shape(d, e):
    t, c = e.get("type"), colour(e)
    if c is None:
        return
    if t == "line":
        d.line([px(num(e, "x1", 0.0)), px(num(e, "y1", 0.0)), px(num(e, "x2", 10.0)), px(num(e, "y2", 0.0))],
               fill=c, width=max(1, round(px(num(e, "width", 0.3)))))
        return
    x, y, w, h = px(num(e, "x", 0.0)), px(num(e, "y", 0.0)), px(num(e, "w", 10.0)), px(num(e, "h", 10.0))
    sw = round(px(num(e, "stroke", 0.3)))
    f = c if e.get("fill") else None
    box = [x, y, x + w - 1, y + h - 1]
    if t == "rect":
        r = px(num(e, "radius", 0.0))
        d.rounded_rectangle(box, radius=r, fill=f, outline=c if sw > 0 else None, width=max(sw, 0)) if r else \
            d.rectangle(box, fill=f, outline=c if sw > 0 else None, width=max(sw, 0))
    elif t == "ellipse":
        d.ellipse(box, fill=f, outline=c if sw > 0 else None, width=max(sw, 0))


def render(design, row, fonts, files_dir):
    gid = s(row.get("_id"))
    ov = (design.get("overrides") or {}).get(gid)
    g = guest_of(row, ov)
    tname, tpl = template_for(design, g["lang"])
    els = elements_for(tpl, ov)
    warnings, unknown = [], set()

    # one row or two for the name and the institution: forced, or two when a one-row box overflows at its smallest
    two = {}
    for f in SPLIT:
        uses = lambda k: any(e.get("type") == "text" and k in fields_in(e.get("text", "")) for e in els)
        has_two = uses(f + "_1") or uses(f + "_2")
        r = g["rows"][f]
        if r != "auto":
            two[f] = r == "two"
        elif not uses(f) or not has_two:
            two[f] = has_two and not uses(f)
        else:
            v = values_of(g, {})
            two[f] = False
            for e in els:
                if e.get("type") == "text" and f in fields_in(e.get("text", "")):
                    content = fill(e.get("text", ""), v, set())
                    faces = Faces(fonts, e.get("font", FALLBACK), weight_of(e.get("weight")), num(e, "letterSpacing", 0.0), [], "")
                    if content.strip() and not lay(e, content, faces)["fits"]:
                        two[f] = True
    values = values_of(g, two)

    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    scales = {}
    for e in els:
        eid, t = e.get("id", ""), e.get("type", "")
        if t == "text":
            content = fill(e.get("text", ""), values, unknown)
            if not content.strip():
                continue
            faces = Faces(fonts, e.get("font", FALLBACK), weight_of(e.get("weight")), num(e, "letterSpacing", 0.0), warnings, eid)
            laid = lay(e, content, faces)
            if not laid["fits"]:
                warnings.append({"element": eid, "warning": f'text does not fit its box even at minScale {num(e, "minScale", 0.5)}'})
            if laid["scale"] < 0.999:
                scales[eid] = round(laid["scale"], 2)
            draw_text(d, e, laid, faces)
        elif t == "image":
            draw_image(img, e, files_dir, warnings)
            d = ImageDraw.Draw(img)
        elif t in ("line", "rect", "ellipse"):
            draw_shape(d, e)
        else:
            warnings.append({"element": eid, "warning": f'unknown element type "{t}"'})
    for k in sorted(unknown):
        warnings.append({"element": "", "warning": f"{{{k}}} is not a field or a column of this guest list"})
    info = {"guest": g["id"], "lang": g["lang"], "template": tname,
            "rows": {f: 2 if two[f] else 1 for f in SPLIT}, "warnings": warnings, "scale": scales}
    return img, info


def bits_of(img, threshold):
    """1 bit per dot, 1 = black, rows of 106 bytes, the leftmost dot in the highest bit."""
    return img.point(lambda v: 255 if v < threshold else 0).convert("1").tobytes()


def read_list(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        text = f.read()
    rows = list(csv.DictReader(io.StringIO(text), dialect="excel-tab" if "\t" in text.split("\n", 1)[0] else "excel"))
    for i, r in enumerate(rows):
        if not s(r.get("_id")):
            r["_id"] = f"g{i + 1}"
    return rows


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--design", required=True)
    src = a.add_mutually_exclusive_group(required=True)
    src.add_argument("--list", help="the guest list (CSV, or TSV from Excel)")
    src.add_argument("--samples", action="store_true", help="the four sample guests (for the template's examples)")
    a.add_argument("--only", help="comma-separated _ids to draw")
    a.add_argument("--fonts", required=True, help="a folder of .ttf/.otf files")
    a.add_argument("--files", help="the template's files folder (pictures); default: next to design.json")
    a.add_argument("--out", required=True)
    o = a.parse_args()
    design = json.load(open(o.design, encoding="utf-8"))
    rows = SAMPLES if o.samples else read_list(o.list)
    if o.only:
        keep = set(o.only.split(","))
        rows = [r for r in rows if s(r.get("_id")) in keep]
    fonts = Fonts(o.fonts)
    files_dir = o.files or os.path.dirname(os.path.abspath(o.design))
    os.makedirs(o.out, exist_ok=True)
    threshold = num(design, "threshold", 128.0)
    report, every = [], bytearray()
    for i, r in enumerate(rows):
        img, info = render(design, r, fonts, files_dir)
        stem = f"{i + 1:03d}-{re.sub(r'[^A-Za-z0-9._-]', '_', info['guest'] or 'guest')}"
        b = bits_of(img, threshold)
        assert len(b) == 62540
        open(os.path.join(o.out, stem + ".bin"), "wb").write(b)
        img.save(os.path.join(o.out, stem + ".png"))
        every += b
        report.append({"file": stem + ".bin", **info})
    open(os.path.join(o.out, "all.bin"), "wb").write(every)
    json.dump(report, open(os.path.join(o.out, "report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    warn = [f'{x["file"]}: {w["warning"]}' for x in report for w in x["warnings"]]
    print(f"{len(report)} labels in {o.out} (all.bin: {len(every)} bytes)" + ("".join("\n  " + w for w in warn)))


if __name__ == "__main__":
    main()
