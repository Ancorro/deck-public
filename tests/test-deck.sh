#!/usr/bin/env bash
# A test Deck next to a real one, for checking a patch live (docs/install.md#testing-next-to-a-real-install).
#
#   bash tests/test-deck.sh start [PORT]   start one from this checkout (port 8790 by default); prints its URL
#   bash tests/test-deck.sh stop [PORT]    stop it and its tmux server, by pid file
#   bash tests/test-deck.sh url [PORT]     its URL, if it is running (exit 1 if not)
#   bash tests/test-deck.sh log [PORT]     the end of its log
#
# It sets what the guide lists: TMUX and TMUX_PANE unset; HOME, USERPROFILE, DECK_DATA, LOCALAPPDATA and TMUX_TMPDIR in
# a short temp folder (tmux socket paths are limited to about 108 characters). Stop it with this script, never with
# `pkill -f DECK_PORT=...`: that pattern is in your own shell's command line too, so it kills the shell running it.
# PYTHON picks the interpreter (default python3, else python); it needs aiohttp and qrcode.
set -euo pipefail
cd "$(dirname "$0")/.."
cmd=${1:-}
port=${2:-8790}
case "$port" in *[!0-9]*|"") echo "test-deck: port must be a number" >&2; exit 2 ;; esac
root=/tmp; [ -d "$root" ] && [ -w "$root" ] || root=${TMPDIR:-.}
base=$root/deck-test-$port
pidf=$base/pid
py=${PYTHON:-$(command -v python3 || command -v python || echo python3)}

alive() { [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; }
answers() { command -v curl >/dev/null && curl -s -o /dev/null -m 2 "http://127.0.0.1:$port/"; }

case "$cmd" in
  start)
    if alive; then echo "test-deck: already running (pid $(cat "$pidf")): http://127.0.0.1:$port/"; exit 0; fi
    if answers; then echo "test-deck: port $port is taken by another Deck; pick a port at least 2 away" >&2; exit 1; fi
    rm -rf "$base"
    mkdir -p "$base/home/.config/deck" "$base/data" "$base/tm"
    echo test > "$base/home/.config/deck/app-version"  # skips the Windows app's first-run setup
    # DECK_INVENTORY: a lone node (the real host list would make it a follower of the real hosts, refusing writes)
    run=(env -u TMUX -u TMUX_PANE DECK_PORT="$port" HOME="$base/home" USERPROFILE="$base/home" DECK_DATA="$base/data"
         DECK_INVENTORY="$base/inventory.toml"
         LOCALAPPDATA="$base/tm" TMUX_TMPDIR="$base/tm" "$py" -m deck.server)
    if command -v setsid >/dev/null; then setsid "${run[@]}" >"$base/log" 2>&1 </dev/null &
    else "${run[@]}" >"$base/log" 2>&1 </dev/null & fi
    echo $! > "$pidf"
    for _ in $(seq 1 60); do
      answers && { echo "test-deck: http://127.0.0.1:$port/ (pid $(cat "$pidf"), folder $base)"; exit 0; }
      alive || break
      sleep 0.5
    done
    echo "test-deck: it did not start; the end of $base/log:" >&2
    tail -20 "$base/log" >&2
    exit 1 ;;
  stop)
    if alive; then
      pid=$(cat "$pidf")
      kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true  # its process group when setsid started it
      for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
      kill -9 "$pid" 2>/dev/null || true
    fi
    command -v tmux >/dev/null && TMUX_TMPDIR="$base/tm" tmux kill-server 2>/dev/null || true
    rm -f "$pidf"
    echo "test-deck: stopped (port $port)" ;;
  url)
    alive && { echo "http://127.0.0.1:$port/"; exit 0; }
    echo "test-deck: not running (port $port)" >&2; exit 1 ;;
  log)
    tail -40 "$base/log" ;;
  *)
    sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
