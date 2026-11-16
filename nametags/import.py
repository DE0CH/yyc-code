#!/usr/bin/env python3
"""Load an event's guest sheet into the name-tag app: nametags/import.py XLSX --event ID [--dry-run] [--site URL].

Reads the first sheet (header row 1, one guest per row, empty rows skipped) into the README's Guest type and PUTs the
whole list to /api/admin/guests?event=ID, which replaces that event's imported guests (sign-ups from the form and
staff entries stay; a guest already there keeps its QR code and state). The event must exist (staff page or
POST /api/events). Columns: A id, F status (Confirmation: Sent / Withdrawn / empty),
H salutation (the sheet's formula, its cached value; else the English part of G), I name, J surname, K firstname,
L institution, M title, N email, O mobile. The guest data never goes into git.
Credentials: the Access service token yyc-nametags-bridge in YYC_NAMETAGS_BRIDGE_ID / _SECRET (store yyc), e.g.
  session-secret --keys YYC_NAMETAGS_BRIDGE_ID,YYC_NAMETAGS_BRIDGE_SECRET --exec python3 nametags/import.py guests.xlsx \
    --event surgical-futures-forum
Prints only counts. Needs openpyxl."""
import argparse, json, os, re, sys, urllib.error, urllib.parse, urllib.request

import openpyxl

COLS = {"id": "A", "status": "F", "salutation": "H", "name": "I", "surname": "J", "firstname": "K",
        "institution": "L", "title": "M", "email": "N", "mobile": "O"}
STATUSES = {"sent": "Sent", "withdrawn": "Withdrawn"}


def idx(letter):
    return ord(letter) - ord("A")


def text(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s or None


def guests(path):
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    out, seen = [], set()
    for n, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        row = list(row) + [None] * (16 - len(row))
        if all(text(c) is None for c in row):
            continue
        g = {k: text(row[idx(c)]) for k, c in COLS.items()}
        if g["salutation"] is None or g["salutation"].startswith("="):
            g["salutation"] = (text(row[idx("G")]) or "").split(" ")[0] or None
        if g["status"] is not None:
            if g["status"].lower() not in STATUSES:
                sys.exit(f"row {n}: unknown status {g['status']!r}")
            g["status"] = STATUSES[g["status"].lower()]
        if not g["id"] or not g["name"]:
            sys.exit(f"row {n}: no id or no name")
        if g["id"] in seen:
            sys.exit(f"row {n}: duplicate id {g['id']}")
        seen.add(g["id"])
        g["table"] = None
        out.append(g)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--event", required=True, help="the event's id, e.g. surgical-futures-forum")
    ap.add_argument("--site", default="https://tags.yyc-internal.com")
    ap.add_argument("--dry-run", action="store_true", help="read the sheet and print counts; send nothing")
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9-]{3,40}", a.event):
        sys.exit("--event must be 3-40 of a-z 0-9 -")
    gs = guests(a.xlsx)
    counts = {s: sum(1 for g in gs if g["status"] == s) for s in ("Sent", "Withdrawn", None)}
    print(f"{len(gs)} guests read (Sent {counts['Sent']}, Withdrawn {counts['Withdrawn']}, no status {counts[None]})")
    if a.dry_run:
        return
    cid, secret = os.environ.get("YYC_NAMETAGS_BRIDGE_ID"), os.environ.get("YYC_NAMETAGS_BRIDGE_SECRET")
    if not cid or not secret:
        sys.exit("YYC_NAMETAGS_BRIDGE_ID / YYC_NAMETAGS_BRIDGE_SECRET are not set")
    url = a.site + "/api/admin/guests?" + urllib.parse.urlencode({"event": a.event})
    req = urllib.request.Request(url, method="PUT", data=json.dumps({"guests": gs}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "yyc-nametags-import",
                                          "CF-Access-Client-Id": cid, "CF-Access-Client-Secret": secret})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("sent:", r.status, r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"PUT /api/admin/guests?event={a.event}: {e.code} {e.read(300).decode(errors='replace')}")


if __name__ == "__main__":
    main()
