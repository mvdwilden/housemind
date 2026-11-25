#!/bin/sh
# Run slow tests in a container with about the processor and memory of a Raspberry Pi 4 /
# HA Green (B-40, E-157): a quarter of one processor core and 1 GB of memory.
#   tools/pi_like.sh tests/test_speed.py
# The same command runs on GitHub (see .github/workflows/full-test.yml).
set -eu
cd "$(dirname "$0")/.."
CPUS="${PI_CPUS:-0.25}"
MEMORY="${PI_MEMORY:-1g}"
exec docker run --rm --cpus="$CPUS" --memory="$MEMORY" \
  -v "$PWD":/src:ro -w /src \
  -e UV_PROJECT_ENVIRONMENT=/venv -e PYTHONDONTWRITEBYTECODE=1 -e UV_LINK_MODE=copy \
  ghcr.io/astral-sh/uv:0.8-python3.13-bookworm-slim \
  sh -c "uv sync --locked --quiet && uv run --no-sync pytest -m slow -s -p no:cacheprovider $*"
