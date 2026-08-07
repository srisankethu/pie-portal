#!/usr/bin/env bash
# Fetch the pie-parser Product Intelligence Engine at the pinned commit.
#
# pie-portal integrates pie-parser by importing it in-process (see
# backend/app/pie_service.py). It is fetched into ./pie-parser at a pinned
# commit rather than committed into this repo, so the integration always builds
# against a known-good engine revision. Override the location with
# PIE_PARSER_ROOT if you already have a checkout elsewhere.
#
# pie-parser is a PRIVATE repo, so cloning needs GitHub auth. This script tries
# HTTPS first and falls back to SSH automatically; if both fail it prints exactly
# how to authenticate rather than failing silently.
set -uo pipefail

# pie-parser master. Moved from 0f17d49 for the scoped-source fix: passing a
# customer identity into resolution REQUIRES it, because against the previous
# pin naming a customer made every scoped line resolve to nothing at all.
PIN="9ef48ffdb35858439cdb8176eaa4d9b19e049cc0"
DEST="${1:-$(cd "$(dirname "$0")/.." && pwd)/pie-parser}"
HTTPS_REMOTE="https://github.com/srisankethu/pie-parser.git"
SSH_REMOTE="git@github.com:srisankethu/pie-parser.git"

die() { echo "error: $*" >&2; exit 1; }

# Already checked out? Just move it to the pinned commit and exit.
if [ -d "$DEST/.git" ]; then
  echo "pie-parser already present at $DEST"
  git -C "$DEST" fetch --depth 1 origin "$PIN" 2>/dev/null || true
  if git -C "$DEST" checkout -q "$PIN" 2>/dev/null; then
    echo "pie-parser checked out at $PIN"
  else
    echo "note: could not check out pinned commit $PIN (using existing checkout)"
  fi
  exit 0
fi

# A leftover, non-git directory would make `git clone` refuse. Catch it clearly.
if [ -e "$DEST" ] && [ -n "$(ls -A "$DEST" 2>/dev/null)" ]; then
  die "$DEST already exists and is not a pie-parser checkout.
  Remove it and re-run:  rm -rf \"$DEST\" && $0"
fi

clone_from() {  # $1 = remote url
  echo "Cloning pie-parser from $1 ..."
  git clone "$1" "$DEST" 2>&1
}

if clone_from "$HTTPS_REMOTE"; then
  :
else
  echo ""
  echo "HTTPS clone failed (pie-parser is private — this is usually auth)."
  echo "Trying SSH ..."
  rm -rf "$DEST"
  if ! clone_from "$SSH_REMOTE"; then
    cat >&2 <<EOF

error: could not clone pie-parser over HTTPS or SSH.

pie-parser is a private repository, so you need GitHub credentials. Pick one:

  1. GitHub CLI (HTTPS):   gh auth login      # GitHub.com -> HTTPS
  2. A Personal Access Token via git's credential helper (repo scope).
  3. SSH: add your key to GitHub, then re-run this script.

Already have pie-parser cloned elsewhere? Skip this script and point the app at it:
  export PIE_PARSER_ROOT=/path/to/pie-parser
EOF
    exit 1
  fi
fi

if ! git -C "$DEST" checkout -q "$PIN"; then
  die "cloned pie-parser but could not check out pinned commit $PIN"
fi
echo "pie-parser checked out at $PIN"
