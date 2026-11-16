#!/usr/bin/env bash
# Pins Xcode and boots a simulator matching Deyao's iPhone (iPhone 17 = model iPhone18,3, iOS 27.0), as DE0CH/jarvis4's
# .github/actions/ios-simulator does; fails instead of falling back when a pinned version is missing.
# Writes SIM_UDID and SIM_DEVICE to $GITHUB_ENV.
set -euo pipefail
XC="${XC:-27.0}" XC_BUILD="${XC_BUILD:-27A266a}" IOS="${IOS:-27.0}" MODEL="${MODEL:-iPhone18,3}" NAME="${NAME:-nametags-iphone}"
start=$(date +%s)
X="/Applications/Xcode_$XC.app"
[ -d "$X" ] || { echo "::error::$X is not on this runner (image ${ImageOS:-?} ${ImageVersion:-?}): $(ls -d /Applications/Xcode*.app | tr '\n' ' ')"; exit 1; }
sudo xcode-select -s "$X"
got=$(xcodebuild -version | awk '/Build version/ {print $3}')
[ "$got" = "$XC_BUILD" ] || { echo "::error::$X is build $got, pinned $XC_BUILD"; exit 1; }
runtime() { xcrun simctl list runtimes -j | python3 -c "
import json, sys
rs = [r for r in json.load(sys.stdin)['runtimes'] if r['platform'] == 'iOS' and r['isAvailable'] and r['version'] == sys.argv[1]]
print(rs[0]['identifier'] if rs else '')" "$IOS"; }
RT=$(runtime)
if [ -z "$RT" ]; then
  echo "no iOS $IOS runtime on the image: downloading it"
  xcodebuild -downloadPlatform iOS -buildVersion "$IOS"
  RT=$(runtime)
fi
[ -n "$RT" ] || { echo "::error::no iOS $IOS simulator runtime"; xcrun simctl list runtimes; exit 1; }
DT=$(xcrun simctl list devicetypes -j | python3 -c "
import json, plistlib, sys, os
for t in json.load(sys.stdin)['devicetypes']:
    p = os.path.join(t.get('bundlePath', ''), 'Contents/Resources/profile.plist')
    try:
        m = plistlib.load(open(p, 'rb')).get('modelIdentifier')
    except Exception:
        continue
    if m == sys.argv[1]:
        print(t['identifier'] + '\t' + t['name']); break" "$MODEL")
[ -n "$DT" ] || { echo "::error::no simulator device type for $MODEL"; exit 1; }
UDID=$(xcrun simctl create "$NAME" "${DT%%$'\t'*}" "$RT")
xcrun simctl boot "$UDID"
xcrun simctl bootstatus "$UDID" -b > /dev/null
xcrun simctl status_bar "$UDID" override --time 9:41 --dataNetwork wifi --wifiBars 3 --cellularBars 4 --batteryState charged --batteryLevel 100 || true
line="${DT#*$'\t'} ($MODEL), iOS $IOS ($RT), Xcode $XC ($got), runner image ${ImageOS:-?} ${ImageVersion:-?}, ready in $(( $(date +%s) - start )) s"
echo "$line"
[ -n "${GITHUB_STEP_SUMMARY:-}" ] && echo "simulator: $line" >> "$GITHUB_STEP_SUMMARY"
[ -n "${GITHUB_ENV:-}" ] && { echo "SIM_UDID=$UDID"; echo "SIM_DEVICE=$line"; } >> "$GITHUB_ENV"
echo "SIM_UDID=$UDID"
