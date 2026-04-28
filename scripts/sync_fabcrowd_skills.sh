#!/usr/bin/env bash
# Copy Cursor assets from a local clone of https://github.com/fabcrowd/skills into .cursor/
# Expects: skills/<id>/SKILL.md, optional agents/, rules/, references/
# Usage: FABSKILLS_REPO=/path/to/skills ./scripts/sync_fabcrowd_skills.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${FABSKILLS_REPO:-}"
if [[ -z "$REPO" || ! -d "$REPO" ]]; then
  echo "Set FABSKILLS_REPO to your clone of https://github.com/fabcrowd/skills" >&2
  exit 1
fi

sync_dir() {
  local name="$1"
  if [[ -d "$REPO/$name" ]]; then
    mkdir -p "$ROOT/.cursor/$name"
    cp -R "$REPO/$name/"* "$ROOT/.cursor/$name/"
    echo "Synced $name/"
  fi
}

sync_dir skills
sync_dir agents
sync_dir rules
sync_dir references
echo "Done. Open this repo in Cursor to load project skills."
