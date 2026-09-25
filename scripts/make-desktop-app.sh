#!/bin/bash
# Builds a macOS app that runs scripts/start.sh on a double-click (issue #44).
#
#     scripts/make-desktop-app.sh                  # ~/Desktop/Company Reach.app
#     scripts/make-desktop-app.sh /path/To.app
#
# The app holds this checkout's path, so it is built on the machine that
# uses it and never committed. Build it again if the checkout moves.

set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
app="${1:-$HOME/Desktop/Company Reach.app}"
source="$(mktemp -t company-reach).applescript"
trap 'rm -f "$source"' EXIT

cat >"$source" <<APPLESCRIPT
try
  do shell script quoted form of "$here/scripts/start.sh"
on error message
  display dialog message with title "Company Reach" buttons {"OK"} default button 1 with icon caution
end try
APPLESCRIPT

rm -rf "$app"
osacompile -o "$app" "$source"
echo "built $app"
