#!/bin/bash
# Stops the review page that scripts/start.sh started. SearXNG keeps running
# (it restarts with Docker by itself); `docker compose stop searxng` stops it.

set -euo pipefail

pids="$(lsof -ti tcp:8000 -sTCP:LISTEN 2>/dev/null || true)"
if [ -z "$pids" ]; then
  echo "the page is not running"
  exit 0
fi
# only our own page: a process whose command line is `company-reach review`
for pid in $pids; do
  if ps -o command= -p "$pid" | grep -q "company-reach review\|company_reach review"; then
    kill "$pid"
    echo "stopped the page (pid $pid)"
  else
    echo "port 8000 is used by something else (pid $pid); left alone" >&2
  fi
done
