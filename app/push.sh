#!/usr/bin/env bash
# Developer shortcut: build the APK and install it straight onto the developer's phone over adb (no taps on the
# phone). One-time setup on the phone: Developer options → Wireless debugging → pair (see docs/install.md "Phone app").
#   ./push.sh            build, install, launch
#   ./push.sh --no-build install the last build
# DECK_PHONE: the phone's adb address, host or host:port (default: the contents of ~/.config/deck/phone).
set -euo pipefail
cd "$(dirname "$0")"
ADB="${ANDROID_HOME:-$HOME/Android/sdk}/platform-tools/adb"
PHONE="${DECK_PHONE:-$(cat "$HOME/.config/deck/phone" 2>/dev/null || true)}"
[ -n "$PHONE" ] || { echo "Set DECK_PHONE or write the phone's adb address to ~/.config/deck/phone"; exit 1; }
[[ "$PHONE" == *:* ]] || PHONE="$PHONE:5555"  # `adb tcpip 5555` keeps this port until the phone restarts

[ "${1:-}" = --no-build ] || ./build.sh
if ! "$ADB" connect "$PHONE" | grep -q "connected to"; then
  # After a phone restart port 5555 is gone; Wireless debugging listens on a random port instead. Find it (nmap).
  host="${PHONE%:*}"
  for port in $(nmap -Pn -p 30000-49999 --min-rate 5000 -T4 "$host" | awk -F/ '/open/{print $1}'); do
    if "$ADB" connect "$host:$port" | grep -q "connected to" && "$ADB" -s "$host:$port" tcpip 5555 >/dev/null; then
      sleep 3; "$ADB" connect "$PHONE" >/dev/null; break
    fi
  done
  "$ADB" -s "$PHONE" get-state >/dev/null 2>&1 || { echo "Can't reach $PHONE: is Wireless debugging on?"; exit 1; }
fi
"$ADB" -s "$PHONE" install -r ../deck/static/deck.apk
"$ADB" -s "$PHONE" shell monkey -p dev.deck.app -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
echo "Installed and opened on $PHONE"
