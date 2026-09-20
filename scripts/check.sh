#!/usr/bin/env bash
# Everything that must be true before this project is deployed anywhere.
# One command, no arguments, non-zero on the first failure - the same gate a
# CI job would run, kept runnable by a person on a laptop.
#
#   bash scripts/check.sh
#
# It needs the test Postgres described in CLAUDE.md > Testing, and a linked
# vendor/alobot, because the suite builds AloBot's schema from AloBot's own
# migrations. It never touches a deployment.
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
fail() { printf '\033[31m!! %s\033[0m\n' "$1" >&2; exit 1; }

step "the AloBot reference is linked and at the pinned commit"
bash scripts/link_alobot.sh --check || fail "vendor/alobot is missing or has moved; see make link-alobot"

step "every source file compiles"
"$PYTHON" -m compileall -q app scripts >/dev/null || fail "a source file does not compile"

# Everything below talks to the THROWAWAY test database, never to a
# deployment: .env points DATABASE_URL at the compose hostname, which is not
# something this script should ever reach.
export DATABASE_URL="${TEST_DATABASE_URL:-postgresql+asyncpg://dashboard:dashboard@localhost:55432/dashboard_test}"

step "the migrations go up, down and up again"
# A migration that cannot be undone cannot be rolled back in an incident.
"$PYTHON" -m alembic upgrade head >/dev/null
"$PYTHON" -m alembic downgrade base >/dev/null
"$PYTHON" -m alembic upgrade head >/dev/null

step "there is exactly one migration head"
heads=$("$PYTHON" -m alembic heads 2>/dev/null | grep -c "head" || true)
[ "$heads" = "1" ] || fail "expected one migration head, found $heads - a branch would apply in an undefined order"

step "the test suite, including the money invariants and the browser walk"
"$PYTHON" -m pytest -q || fail "tests failed"

step "the production image builds, runs as a non-root user and knows its version"
if command -v docker >/dev/null 2>&1; then
  version="$(git rev-parse --short HEAD 2>/dev/null || echo dev)"
  docker build --quiet --build-arg "APP_VERSION=$version" -t alobot-dashboard:check . >/dev/null || fail "the image does not build"
  who="$(docker run --rm --entrypoint sh alobot-dashboard:check -c 'id -un')"
  [ "$who" = "dashboard" ] || fail "the image runs as $who, not the unprivileged dashboard user"
  stamped="$(docker run --rm --entrypoint sh alobot-dashboard:check -c 'printf %s "$APP_VERSION"')"
  [ "$stamped" = "$version" ] || fail "the image reports version '$stamped', expected '$version'"
  if docker run --rm --entrypoint sh alobot-dashboard:check -c 'python -c "import pytest"' >/dev/null 2>&1; then
    fail "the production image carries the test tooling"
  fi
else
  printf 'docker is not on PATH - skipping the image checks\n'
fi

printf '\n\033[32mall checks passed\033[0m\n'
