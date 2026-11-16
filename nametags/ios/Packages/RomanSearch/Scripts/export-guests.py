#!/usr/bin/env python3
"""Exports the guest sheet for the real-data test (RealGuestsTests); the output must stay out of git.

Usage: Scripts/export-guests.py guests.xlsx /tmp/guests.json
       ROMANSEARCH_GUESTS_JSON=/tmp/guests.json swift test
Columns: I name, J surname, L institution, M title, N email, O mobile (header row skipped, empty names dropped).
"""
import json
import sys

import openpyxl

wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
guests = []
for row in wb.active.iter_rows(min_row=2, values_only=True):
    cell = lambda col: str(row[ord(col) - ord("A")] or "").strip()
    if not cell("I"):
        continue
    guests.append({"fields": [cell(c) for c in "ILMNO"], "surname": cell("J")})
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(guests, f, ensure_ascii=False)
print(f"{len(guests)} guests -> {sys.argv[2]}")
