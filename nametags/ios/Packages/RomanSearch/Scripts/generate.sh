#!/usr/bin/env bash
# Regenerates Sources/RomanSearch/Resources/romanisation.tsv from its pinned sources (see README "Data").
# Needs curl, unzip and a Swift toolchain (swiftc). Usage: Scripts/generate.sh
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
pkg="$(dirname "$here")"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

UNIHAN_URL=https://www.unicode.org/Public/18.0.0/ucd/Unihan.zip
CHUNLAW_SHA=689db59aafe72217dbad28be6c3b0269a751e06a
CCC_SHA=0768f9ad4f27a9de395c14ae83fd381ce7596517
# The LOC page is served with changing markup around it, so the pin is the SHA-256 of the table extracted from it.
LOC_URL=https://www.loc.gov/catdir/pinyin/romcover.html
LOC_TABLE_SHA256=8ba5dca133a535fb4b35aab6cc932410f4b20a863d2e4c5fa09a3580ba5e2d0a

mkdir -p "$work/src" "$work/unihan"
curl -sfL -o "$work/src/Unihan.zip" "$UNIHAN_URL"
unzip -q "$work/src/Unihan.zip" -d "$work/unihan"
curl -sfL -o "$work/src/chunlaw.tsv" \
  "https://raw.githubusercontent.com/chunlaw/cantonese-romanisation/$CHUNLAW_SHA/dictionary/source.tsv"
curl -sfL -o "$work/src/initials.csv" \
  "https://raw.githubusercontent.com/Ostrichbeta/CCCConverter/$CCC_SHA/src/cccconverter/dict/initials.csv"
curl -sfL -o "$work/src/finals.csv" \
  "https://raw.githubusercontent.com/Ostrichbeta/CCCConverter/$CCC_SHA/src/cccconverter/dict/finals.csv"
curl -sfL -A "Mozilla/5.0 (RomanSearch generator)" -o "$work/src/romcover.html" "$LOC_URL"

swiftc -O "$here/GenerateTable.swift" -o "$work/generate"
"$work/generate" "$work/unihan" "$work/src/chunlaw.tsv" "$work/src/initials.csv" "$work/src/finals.csv" \
  "$work/src/romcover.html" "$work/loc-wade-giles.tsv" \
  "Unihan 18.0.0; chunlaw/cantonese-romanisation@${CHUNLAW_SHA:0:12}; Ostrichbeta/CCCConverter@${CCC_SHA:0:12}; LOC Wade-Giles table sha256:${LOC_TABLE_SHA256:0:12}" \
  > "$work/romanisation.tsv"
got="$(sha256sum "$work/loc-wade-giles.tsv" | cut -d' ' -f1)"
if [ "$got" != "$LOC_TABLE_SHA256" ]; then
  echo "LOC Wade-Giles table changed: sha256 $got, pinned $LOC_TABLE_SHA256 (review it, then update the pin)" >&2
  exit 1
fi
mv "$work/romanisation.tsv" "$pkg/Sources/RomanSearch/Resources/romanisation.tsv"
wc -c "$pkg/Sources/RomanSearch/Resources/romanisation.tsv"
