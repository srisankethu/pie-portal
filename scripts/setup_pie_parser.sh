#!/usr/bin/env bash
# Fetch the pie-parser Product Intelligence Engine at the commit this repo pins.
#
# pie-parser is a git submodule at ./pie-parser, so the pin lives in this
# repository's index rather than in a variable in this file. `git diff` shows a
# pin change as a one-line "Subproject commit ..." edit, and moving the pin is a
# reviewable commit instead of an edit to a shell string nobody diffs.
#
# This script exists only to turn git's submodule auth failure — which is
# terse and does not mention that the repository is private — into something
# actionable. If it ever stops earning that, delete it and document
# `git submodule update --init` instead.
#
# Already have pie-parser checked out elsewhere? Skip this and point the app at
# it: export PIE_PARSER_ROOT=/path/to/pie-parser
set -uo pipefail

cd "$(dirname "$0")/.."

if git submodule update --init --recursive pie-parser; then
  echo "pie-parser at $(git -C pie-parser rev-parse --short HEAD) (the pinned commit)"
  exit 0
fi

cat >&2 <<'EOF'

error: could not fetch the pie-parser submodule.

pie-parser is a PRIVATE repository, so this needs GitHub credentials. Pick one:

  1. GitHub CLI (HTTPS):  gh auth login          # GitHub.com -> HTTPS
  2. A Personal Access Token (repo scope) via git's credential helper.
  3. SSH — rewrite the HTTPS remote once, globally:
       git config --global url."git@github.com:".insteadOf "https://github.com/"
     then re-run this script.

Already have pie-parser cloned elsewhere? Skip this script entirely:
  export PIE_PARSER_ROOT=/path/to/pie-parser
EOF
exit 1
