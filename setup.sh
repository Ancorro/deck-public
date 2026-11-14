#!/usr/bin/env bash
# Everything the test suite needs, from PyPI only: uv (fetches Python packages) and Node.js.
set -e
python3 -m pip install -q uv nodejs-wheel-binaries
