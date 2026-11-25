#!/bin/zsh
# The check before HouseMind goes to a real house (QA 2, choice 14): every test on a clean copy
# of a commit – ruff, mypy, the fast suite, the slow suite (speed and a year in the time
# machine) and the frontend. Anything red stops here, so nothing red reaches a house.
#
# Usage: tools/check_head.sh [commit]   (default HEAD; the working tree is never used)
set -euo pipefail
REPO=${0:A:h:h}
GIT=${GIT:-/Library/Developer/CommandLineTools/usr/bin/git}
[[ -x $GIT ]] || GIT=git
REF=${1:-HEAD}
SHA=$($GIT -C $REPO rev-parse --short $REF)
WORK=$(mktemp -d)
trap 'rm -rf $WORK' EXIT
$GIT -C $REPO archive $REF | tar -x -C $WORK
cd $WORK
BIN=$REPO/.venv/bin
export PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1
# One step per line: with set -e only the last command of an &&-chain stops the script, so a
# ruff error used to skip the format check and mypy and still end "all green".
echo "== $SHA: ruff, mypy"
$BIN/ruff check .
$BIN/ruff format --check .
$BIN/mypy
# The fast suite in parallel (code QA, C5: 4 instead of 12 minutes); the slow one alone, as it
# measures speed.
echo "== $SHA: fast tests" && $BIN/python -m pytest -q -p no:cacheprovider -n auto
echo "== $SHA: slow tests" && $BIN/python -m pytest -q -p no:cacheprovider -m slow
echo "== $SHA: frontend"
cp -R $REPO/frontend/node_modules frontend/node_modules  # a copy: vitest writes its cache there
(cd frontend && npm run check && npx vitest run)
echo "== $SHA: all green"
