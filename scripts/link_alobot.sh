#!/usr/bin/env bash
# Link (or verify) the read-only reference checkout of AloBot.
#
#   scripts/link_alobot.sh [PATH]     create vendor/alobot -> PATH (default ../AloBot)
#   scripts/link_alobot.sh --check    verify the link exists and is at ALOBOT_COMMIT
#
# The link is gitignored: it is a convenience for reading AloBot's real
# source while working here. Nothing at runtime imports from it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LINK="$ROOT/vendor/alobot"
PIN_FILE="$ROOT/ALOBOT_COMMIT"

if [[ "${1:-}" == "--check" ]]; then
  [[ -L "$LINK" ]] || { echo "vendor/alobot is not linked - run: make link-alobot ALOBOT_PATH=/path/to/AloBot"; exit 1; }
  want="$(tr -d '[:space:]' < "$PIN_FILE")"
  have="$(git -C "$LINK" rev-parse HEAD)"
  if [[ "$want" == "$have" ]]; then
    echo "vendor/alobot is at pinned commit $have"
  else
    echo "WARNING: vendor/alobot is at $have but this project was verified against $want"
    echo "         Re-run the AloBot schema compatibility check before trusting AloBot-backed pages."
    exit 2
  fi
  exit 0
fi

target="${1:-$ROOT/../AloBot}"
target="$(cd "$target" && pwd)"
[[ -f "$target/CLAUDE.md" && -d "$target/app" ]] || { echo "$target does not look like the AloBot checkout"; exit 1; }
mkdir -p "$ROOT/vendor"
ln -sfn "$target" "$LINK"
echo "vendor/alobot -> $target"
"$0" --check || true
