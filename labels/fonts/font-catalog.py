#!/usr/bin/env python3
"""The labels' font catalogue (labels/README.md "Fonts"): the faces font-catalog.txt lists, from google/fonts at
a pinned commit, into labels/worker/public/fonts/: each file brotli-compressed as <sha16>-<file>.br (the Worker sends it
with Content-Encoding: br to the staff page, and its renderer decompresses it itself; three Chinese fonts are over
Cloudflare's 25 MiB per file uncompressed), a preview PNG of each family, the licence, and catalog.json (each family
with its files and weights), which the renderer, the staff page and the MCP tool labels_fonts read. Needs python3 with
fonttools and Pillow, curl and brotli. Run from anywhere; WORK (default /tmp/label-fonts) keeps the downloads."""
import hashlib, json, os, re, subprocess, sys, urllib.parse
from pathlib import Path

COMMIT = "2fe1e8c6b137fb13041a7cfca6fdcd8a910ea4dc"  # google/fonts, 2026-11-03
SAMPLE = {"zh": "周潤發 先生", "en": "Benedict Cumberbatch"}

here = Path(__file__).resolve().parent
out = here.parent / "worker" / "public" / "fonts"
work = Path(os.environ.get("WORK", "/tmp/label-fonts"))
work.mkdir(parents=True, exist_ok=True)

from fontTools.ttLib import TTFont  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def get(url, dest):
    if not dest.exists():
        subprocess.run(["curl", "-sSfL", "--retry", "3", "-o", str(dest) + ".part", url], check=True)
        os.replace(str(dest) + ".part", dest)
    return dest


def weights_of(path):
    f = TTFont(path, lazy=True)
    if "fvar" in f:
        a = next((a for a in f["fvar"].axes if a.axisTag == "wght"), None)
        if a:
            return [int(a.minValue), int(a.maxValue)]
    w = f["OS/2"].usWeightClass
    return [w, w]


def family_of(path):
    names = TTFont(path, lazy=True)["name"]
    return str(names.getDebugName(16) or names.getDebugName(1))


def preview(path, text, dest):
    font = ImageFont.truetype(str(path), 44)
    box = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=font)
    im = Image.new("L", (box[2] - box[0] + 16, 64), 255)
    ImageDraw.Draw(im).text((8 - box[0], (64 - (box[3] - box[1])) // 2 - box[1]), text, font=font, fill=0)
    im.save(dest, optimize=True)


def main():
    out.mkdir(parents=True, exist_ok=True)
    (out / "previews").mkdir(exist_ok=True)
    fonts = []
    for line in (here / "font-catalog.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        category, folder, label, names = [x.strip() for x in line.split("|")]
        lang = "en" if category.startswith("英文") else "zh"
        files = []
        for name in [n.strip() for n in names.split(", ")]:  # a name may hold a comma: Inter[opsz,wght].ttf
            src = get(f"https://raw.githubusercontent.com/google/fonts/{COMMIT}/ofl/{folder}/{urllib.parse.quote(name)}", work / name)
            sha = hashlib.sha256(src.read_bytes()).hexdigest()
            served = f"{sha[:16]}-{re.sub(r'[^A-Za-z0-9.-]', '', name)}"
            if not (out / f"{served}.br").exists():
                subprocess.run(["brotli", "-q", "9", "-f", "-o", str(out / f"{served}.br"), str(src)], check=True)
            files.append({"file": served, "bytes": src.stat().st_size, "family": family_of(src), "weights": weights_of(src)})
        if not files:
            sys.exit(f"{folder}: no files listed")
        family = files[0]["family"]
        regular = next((f for f in files if "Regular" in f["file"] or "[" in f["file"]), files[0])
        preview(src_of(regular, work), SAMPLE[lang], out / "previews" / f"{folder}.png")
        lo, hi = min(f["weights"][0] for f in files), max(f["weights"][1] for f in files)
        variable = any(f["weights"][0] != f["weights"][1] for f in files)
        weights = f"{lo}-{hi} (any)" if variable else ", ".join(str(w) for w in sorted({f["weights"][0] for f in files}))
        fonts.append({"id": folder, "family": family, "label": label, "category": category, "lang": lang, "weights": weights,
                      "files": [{"file": f["file"], "bytes": f["bytes"]} for f in files], "preview": f"previews/{folder}.png", "license": "OFL-1.1"})
        print(f"{folder}: {family} ({len(files)} file(s))", flush=True)
    get(f"https://raw.githubusercontent.com/google/fonts/{COMMIT}/ofl/inter/OFL.txt", out / "OFL.txt")
    (out / "catalog.json").write_text(json.dumps({"source": f"google/fonts@{COMMIT}", "fonts": fonts}, ensure_ascii=False, indent=1))


def src_of(entry, work):
    # the downloaded file a served name came from (served = <sha16>-<name with brackets and commas dropped>)
    want = entry["file"].split("-", 1)[1]
    return next(work / n for n in os.listdir(work) if re.sub(r"[^A-Za-z0-9.-]", "", n) == want)


if __name__ == "__main__":
    main()
