#!/usr/bin/env bash
# The full test suite: browser behavior tests and every Python test (tests/run.sh all).
set -e
cd "$(dirname "$0")"
# Node.js from setup.sh's nodejs-wheel-binaries when none is installed.
command -v node >/dev/null || PATH="$(python3 -c 'import nodejs_wheel, os; print(os.path.dirname(nodejs_wheel.__file__))')/bin:$PATH"
bash tests/run.sh all "$@"
