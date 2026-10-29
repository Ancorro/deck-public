#!/usr/bin/env bash
# Run a relevant group or explicit pytest selection in isolation (docs/development.md).
set -e
cd "$(dirname "$0")/.."
group=python
if [ "$#" -eq 0 ]; then
  group=all
else
  case "$1" in
    frontend|bridge|server|core|lint|all) group=$1; shift ;;
  esac
fi
uv=$(command -v uv || echo "$(cd .. && pwd)/uv.exe")
if [ "$group" = lint ]; then  # ruff over the Python side (ruff.toml), and a syntax check of every page part
  "$uv" run --no-project --python 3.12 --with ruff ruff check deck deploy tests
  for f in deck/static/app/*.js deck/static/*.js; do node --check "$f"; done
  exit 0
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
    bridge) targets=(tests/test_chat_bridge.py tests/test_agent_errors.py tests/test_agy.py tests/test_claude*.py) ;;
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
  --with pytest-asyncio python -m pytest -q "${targets[@]}" "$@"
