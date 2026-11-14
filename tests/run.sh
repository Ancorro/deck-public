#!/usr/bin/env bash
# Run a relevant group or explicit pytest selection in isolation (docs/development.md).
set -e
cd "$(dirname "$0")/.."
group=python
if [ "$#" -eq 0 ]; then
  group=all
else
  case "$1" in
    frontend|bridge|server|core|lint|live|all) group=$1; shift ;;
  esac
fi
uv=$(command -v uv || echo "$(cd .. && pwd)/uv.exe")
if [ "$group" = lint ]; then  # ruff over the Python side (ruff.toml), and a syntax check of every page part
  "$uv" run --no-project --python 3.12 --with ruff ruff check deck deploy tests
  for f in deck/static/app/*.js deck/static/*.js; do node --check "$f"; done
  exit 0
fi
if [ "$group" = live ]; then  # the real agent CLIs (tests/live_agents.py): their logins stay in the real home
  win=$([ "$OS" = Windows_NT ] && echo "--with pywinpty" || true)
  unset TMUX TMUX_PANE
  # Windows: Deck's MSYS2 kit, from this checkout or else the installed server's
  if [ "$OS" = Windows_NT ] && [ ! -d msys ] && [ -z "$DECK_MSYS" ] && [ -d "$LOCALAPPDATA/Deck/server/msys" ]; then
    export DECK_MSYS="$LOCALAPPDATA/Deck/server/msys"
  fi
  live=$(mktemp -d)
  export DECK_DATA="$live/data" DECK_LIVE_TMUX="$live/tmux" PYTHONUTF8=1  # its own Deck data and tmux server
  mkdir -p "$DECK_LIVE_TMUX"
  status=0
  "$uv" run --no-project --python 3.12 $win --with pytest python -m pytest -q -p no:cacheprovider \
    -o asyncio_mode=strict -W ignore::pytest.PytestConfigWarning tests/live_agents.py "$@" || status=$?
  tmux_bin=$([ "$OS" = Windows_NT ] && echo "${DECK_MSYS:-msys}/usr/bin/tmux.exe" || echo tmux)
  TMUX_TMPDIR="$DECK_LIVE_TMUX" "$tmux_bin" kill-server 2>/dev/null || true
  # and every chat bridge it started (they run detached: a failed or interrupted test would leave its agent running)
  "$uv" run --no-project --python 3.12 $win python -c 'import sys; sys.path.insert(0, ".")
from deck import bridges
for n in bridges.names(): bridges.remove(n)' 2>/dev/null || true
  rm -rf "$live"
  exit $status
fi
if [ "$group" = frontend ] || [ "$group" = all ]; then
  node --test tests/*.test.cjs
  [ "$group" = frontend ] && exit 0
fi
# Explicit paths replace group defaults; flags such as -k still filter the group.
has_path=false
option_value=false
for arg in "$@"; do
  if [ "$option_value" = true ]; then option_value=false; continue; fi
  case "$arg" in
    -k|-m|-o|-c|--override-ini|--rootdir|--basetemp|--confcutdir|--deselect|--ignore|--ignore-glob|--maxfail|--tb|--capture|--color|--code-highlight|--assert|--junitxml|--junit-xml|--junitprefix|--log-level|--log-file|--log-cli-level|--asyncio-mode)
      option_value=true ;;
    -*) ;;
    *) target=${arg%%::*}; if [ -e "$target" ] || [[ "$target" = *.py ]]; then has_path=true; fi ;;
  esac
done
targets=()
if [ "$has_path" = false ]; then
  case "$group" in
    bridge) targets=(tests/test_chat_bridge.py tests/test_adapters.py tests/test_agent_errors.py tests/test_agy.py tests/test_claude*.py) ;;
    server) targets=(tests/test_server*.py) ;;
    core) targets=(tests/test_core.py tests/test_saves.py tests/test_session_move.py) ;;
    *) targets=(tests) ;;
  esac
fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
unset TMUX TMUX_PANE
export TMUX_TMPDIR="$tmp" LOCALAPPDATA="$tmp" DECK_DATA="$tmp/data" HOME="$tmp" PYTHONUTF8=1
win=$([ "$OS" = Windows_NT ] && echo "--with pywinpty" || true)
"$uv" run --no-project --python 3.12 --with aiohttp --with qrcode $win --with pytest --with pytest-aiohttp \
  --with pytest-asyncio --with pillow python -m pytest -q "${targets[@]}" "$@"
