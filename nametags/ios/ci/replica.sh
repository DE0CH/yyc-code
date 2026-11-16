#!/usr/bin/env bash
# The phone replica: installs the Release simulator build, starts the stub server, and runs every Maestro flow with
# the screen recorded. Usage: replica.sh <path to NameTags.app> <output dir>   (needs SIM_UDID)
# Output: <out>/<flow>.mp4, <out>/screens/*.png, <out>/prints/*.png (the tags the app sent), <out>/*.log.
set -uo pipefail
APP="$1"; OUT="$2"
HERE="$(cd "$(dirname "$0")" && pwd)"
FLOWS="$HERE/../maestro"
mkdir -p "$OUT/screens" "$OUT/prints" "$OUT/maestro"
: "${SIM_UDID:?}"

"$HERE/stub/target/release/nametags-stub" 8787 "$OUT/prints" 2> "$OUT/stub.log" &
STUB=$!
trap 'kill $STUB 2>/dev/null' EXIT
for _ in $(seq 50); do curl -s -o /dev/null http://127.0.0.1:8787/ && break; sleep 0.2; done

xcrun simctl install "$SIM_UDID" "$APP"
export MAESTRO_DRIVER_STARTUP_TIMEOUT=180000

status=0; summary=""
for flow in "$FLOWS"/*.yaml; do
  name=$(basename "$flow" .yaml)
  xcrun simctl io "$SIM_UDID" recordVideo --codec h264 --force "$OUT/$name.mp4" 2> /dev/null &
  REC=$!
  sleep 1
  ( cd "$OUT/screens" && maestro --device "$SIM_UDID" test --test-output-dir "$OUT/maestro/$name" "$flow" ) > "$OUT/$name.log" 2>&1
  rc=$?
  kill -INT $REC; wait $REC 2>/dev/null
  if [ $rc -eq 0 ]; then summary+="passed  $name"$'\n'; else summary+="FAILED  $name"$'\n'; status=1
    echo "::error::Maestro flow $name failed"; tail -40 "$OUT/$name.log"
    xcrun simctl io "$SIM_UDID" screenshot "$OUT/screens/$name-FAILED.png" || true
  fi
done
# Screenshots Maestro wrote into its own output folders too
find "$OUT/maestro" -name "*.png" -exec cp {} "$OUT/screens/" \; 2>/dev/null || true
xcrun simctl spawn "$SIM_UDID" log show --last 30m --style compact \
  --predicate 'subsystem == "dev.de0ch.yycnametags"' > "$OUT/app.log" 2>/dev/null || true

echo "$summary"
echo "tags sent to the stub: $(ls "$OUT/prints" | wc -l)"; ls "$OUT/prints"
{ echo '### Maestro flows'; echo '```'; echo "$summary"; echo '```'; } >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
exit $status
