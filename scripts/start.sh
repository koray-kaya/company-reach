#!/bin/bash
# One click: start what the tool needs and open its page (issue #44).
#
#     scripts/start.sh
#
# Starts SearXNG (Docker) and the review page (uv) unless they already run,
# waits until the page answers, and opens it in the browser. Safe to run
# again: whatever runs is left alone. The page keeps running in the
# background; scripts/stop.sh stops it (do that after pulling new code).
# The macOS app built by scripts/make-desktop-app.sh runs this file.

set -euo pipefail

cd "$(dirname "$0")/.."
# An app started from the Finder gets a short PATH: add the usual homes of
# uv (Homebrew, the uv installer) and of Docker Desktop.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"

PAGE="http://127.0.0.1:8000/"
LOGS="data/logs"
mkdir -p "$LOGS"

fail() {
  echo "$1" >&2
  exit 1
}

answers() { curl -fs -o /dev/null "$PAGE"; }

command -v uv >/dev/null || fail "uv is not installed: https://docs.astral.sh/uv/"
command -v docker >/dev/null || fail "Docker is not installed."

# Docker Desktop may still be starting after the machine did.
if ! docker info >/dev/null 2>&1; then
  open -a Docker 2>/dev/null || fail "Docker is not running and could not be started."
  for _ in $(seq 90); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
  docker info >/dev/null 2>&1 || fail "Docker did not start within 90 seconds."
fi
docker compose up -d searxng >>"$LOGS/start.log" 2>&1 ||
  fail "SearXNG did not start; see $LOGS/start.log."

if ! answers; then
  # keep one old log, so the file does not grow for ever
  if [ -f "$LOGS/review.log" ] && [ "$(wc -c <"$LOGS/review.log")" -gt 5000000 ]; then
    mv "$LOGS/review.log" "$LOGS/review.log.1"
  fi
  nohup uv run company-reach review >>"$LOGS/review.log" 2>&1 </dev/null &
  for _ in $(seq 60); do
    answers && break
    sleep 1
  done
  answers || fail "The page did not start; see $LOGS/review.log."
fi

open "$PAGE"
