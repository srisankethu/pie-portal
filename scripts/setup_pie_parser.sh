#!/usr/bin/env bash
# Fetch the pie-parser Product Intelligence Engine at the pinned commit.
#
# pie-portal integrates pie-parser by importing it in-process (see
# backend/app/pie_service.py). It is fetched into ./pie-parser at a pinned
# commit rather than committed into this repo, so the integration always builds
# against a known-good engine revision. Override the location with
# PIE_PARSER_ROOT if you already have a checkout elsewhere.
set -euo pipefail

PIN="0f17d49d82a712eaf5633c16afa860441043633e"
DEST="${1:-$(cd "$(dirname "$0")/.." && pwd)/pie-parser}"
REMOTE="https://github.com/srisankethu/pie-parser.git"

if [ -d "$DEST/.git" ]; then
  echo "pie-parser already present at $DEST"
  git -C "$DEST" fetch --depth 1 origin "$PIN" 2>/dev/null || true
  git -C "$DEST" checkout -q "$PIN" 2>/dev/null || \
    echo "note: could not check out pinned commit $PIN (using existing checkout)"
  exit 0
fi

echo "Cloning pie-parser into $DEST ..."
git clone "$REMOTE" "$DEST"
git -C "$DEST" checkout -q "$PIN"
echo "pie-parser checked out at $PIN"
