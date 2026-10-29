#!/usr/bin/env bash
# Build the Deck phone app (debug APK) and publish it at <this Deck>/static/deck.apk for the phone to install.
# Needs JDK 21 + Android SDK (ANDROID_HOME, default ~/Android/sdk) + NDK, and Rust with cargo-ndk and the
# aarch64-linux-android target (the iroh tunnel, ../tunnel). See docs/install.md → Dependencies.
# DECK_APP_VERSION (e.g. 0.2.0, set by the release workflow): a public build instead: no host list, that version, and
# release-signed with DECK_KEYSTORE (password in DECK_KEYSTORE_PASS, alias DECK_KEY_ALIAS, default deck) when given,
# else debug-signed. Written to ../dist/deck-<version>.apk, not published at /static/deck.apk.
set -euo pipefail
cd "$(dirname "$0")"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/sdk}"
export JAVA_HOME="${JAVA_HOME:-$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")}"

# Developer convenience: start the app with this user's Deck hosts (from the machine notes' inventory, if any).
# Not committed. A public build starts empty.
VERSION="${DECK_APP_VERSION:-}"
if [ -n "$VERSION" ]; then echo "[]" > www/hosts.json; else PYTHONPATH=.. python3 -c '
import json
from deck import settings
print(json.dumps([{"name": h["name"], "url": h["url"]} for h in settings.hosts()["hosts"]]))' > www/hosts.json; fi

[ -d node_modules ] || npm install --no-audit --no-fund -s
[ -d android ] || npx cap add android
npx cap sync android
# Our native code: MainActivity (plugins on host pages too), the Dictation and Tunnel plugins (app/native/).
cp native/*.java android/app/src/main/java/dev/deck/app/
# The iroh tunnel (Rust, ../tunnel) as libdeck_tunnel.so. 64-bit ARM phones only; DECK_ABIS="arm64-v8a x86_64" adds
# the emulator's (needs `rustup target add x86_64-linux-android`).
export ANDROID_NDK_HOME="${ANDROID_NDK_HOME:-$(ls -d "$ANDROID_HOME"/ndk/* | sort -V | tail -1)}"
ABIS=(); for a in ${DECK_ABIS:-arm64-v8a}; do ABIS+=(-t "$a"); done
rm -rf android/app/src/main/jniLibs  # an earlier emulator build's x86_64 library must not ship in a phone build
(cd ../tunnel && "${CARGO:-$HOME/.cargo/bin/cargo}" ndk -q "${ABIS[@]}" --platform 24 -o ../app/android/app/src/main/jniLibs build --release --lib)
# Portrait only: the app never rotates. android/ is generated, so set it on every build.
M=android/app/src/main/AndroidManifest.xml
grep -q 'screenOrientation' "$M" || sed -i 's|android:name=".MainActivity"|android:name=".MainActivity"\n            android:screenOrientation="portrait"|' "$M"
# Dictation (app/native/DictationPlugin.java): microphone, and visibility of the phone's speech service (Android 11+).
grep -q 'RECORD_AUDIO' "$M" || sed -i 's|<uses-permission android:name="android.permission.INTERNET" />|<uses-permission android:name="android.permission.INTERNET" />\n    <uses-permission android:name="android.permission.RECORD_AUDIO" />\n    <queries><intent><action android:name="android.speech.RecognitionService" /></intent></queries>|' "$M"
# Camera: the host list scans a host's pairing QR itself (no browser, so no Tailscale needed to pair).
grep -q 'android.permission.CAMERA' "$M" || sed -i 's|<uses-permission android:name="android.permission.INTERNET" />|<uses-permission android:name="android.permission.INTERNET" />\n    <uses-permission android:name="android.permission.CAMERA" />|' "$M"
# Deck links (https://<host>.ts.net/...) may open in the app: pairing hand-off from the browser (intent:// with our
# package), or every scanned Deck link if the user turns on App info → Open by default → supported links.
grep -q 'android:host="\*.ts.net"' "$M" || sed -i '0,/<\/intent-filter>/s||</intent-filter>\n            <intent-filter>\n                <action android:name="android.intent.action.VIEW" />\n                <category android:name="android.intent.category.DEFAULT" />\n                <category android:name="android.intent.category.BROWSABLE" />\n                <data android:scheme="https" android:host="*.ts.net" />\n            </intent-filter>|' "$M"
# deck://pair?key=…: a host's pairing QR when it runs deck-iroh. Custom scheme, so it always opens the app.
grep -q 'android:scheme="deck"' "$M" || sed -i '0,/<\/intent-filter>/s||</intent-filter>\n            <intent-filter>\n                <action android:name="android.intent.action.VIEW" />\n                <category android:name="android.intent.category.DEFAULT" />\n                <category android:name="android.intent.category.BROWSABLE" />\n                <data android:scheme="deck" />\n            </intent-filter>|' "$M"
# Tunnel pages are plain http to 127.0.0.1 (the tunnel itself is encrypted): allow cleartext there and nowhere else.
mkdir -p android/app/src/main/res/xml
cat > android/app/src/main/res/xml/network_security_config.xml <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <domain-config cleartextTrafficPermitted="true"><domain includeSubdomains="false">127.0.0.1</domain></domain-config>
</network-security-config>
XML
grep -q 'networkSecurityConfig' "$M" || sed -i 's|<application|<application android:networkSecurityConfig="@xml/network_security_config"|' "$M"
if [ -z "$VERSION" ]; then
  (cd android && ./gradlew -q assembleDebug)
  cp android/app/build/outputs/apk/debug/app-debug.apk ../deck/static/deck.apk
  echo "APK: $(du -h ../deck/static/deck.apk | cut -f1) at /static/deck.apk"
  exit
fi
# Public build. versionCode from the version (0.2.0 → 200), so a newer release installs over an older one.
IFS=. read -r MA MI PA <<< "${VERSION#v}"
CODE=$((MA * 10000 + MI * 100 + ${PA:-0})); [ "$CODE" -ge 1 ] || CODE=1   # Android needs 1 or more
sed -i -E "s/versionCode [0-9]+/versionCode $CODE/; s/versionName \"[^\"]*\"/versionName \"${VERSION#v}\"/" android/app/build.gradle
OUT="../dist/deck-${VERSION#v}.apk"; mkdir -p ../dist
if [ -n "${DECK_KEYSTORE:-}" ]; then
  (cd android && ./gradlew -q assembleRelease)
  BT="$(ls -d "$ANDROID_HOME"/build-tools/* | sort -V | tail -1)"
  "$BT/zipalign" -f -p 4 android/app/build/outputs/apk/release/app-release-unsigned.apk "$OUT.aligned"
  "$BT/apksigner" sign --ks "$DECK_KEYSTORE" --ks-pass env:DECK_KEYSTORE_PASS --ks-key-alias "${DECK_KEY_ALIAS:-deck}" \
    --out "$OUT" "$OUT.aligned"
  rm -f "$OUT.aligned" "$OUT.idsig"
else
  (cd android && ./gradlew -q assembleDebug)
  cp android/app/build/outputs/apk/debug/app-debug.apk "$OUT"
  echo "No DECK_KEYSTORE: debug-signed"
fi
echo "APK: $(du -h "$OUT" | cut -f1) at dist/${OUT##*/}"
