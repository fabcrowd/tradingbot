# Cursor project assets

## Canonical catalog (GitHub)

**Source of truth for shared skills, agents, and rules:**  
[https://github.com/fabcrowd/skills](https://github.com/fabcrowd/skills)

That repository should use this layout (mirrors what Cursor expects under `.cursor/`):

| Path in `fabcrowd/skills` | Copies to |
|---------------------------|-----------|
| `skills/<skill-id>/SKILL.md` | `.cursor/skills/<skill-id>/SKILL.md` |
| `agents/*.md` | `.cursor/agents/` |
| `rules/*.mdc` | `.cursor/rules/` |
| `references/*` | `.cursor/references/` |

## New machine (recommended)

1. Clone the trading bot repo (this project).
2. Clone [fabcrowd/skills](https://github.com/fabcrowd/skills) anywhere on disk.
3. From this repo root, sync into `.cursor/`:
   - **Windows (PowerShell):** set `FABSKILLS_REPO` to your skills clone path, then run `.\scripts\sync_fabcrowd_skills.ps1`
   - **macOS / Linux:** `FABSKILLS_REPO=/path/to/skills ./scripts/sync_fabcrowd_skills.sh`
4. Open this folder in **Cursor** so project skills and rules load.

## Without sync

If skills are already committed under `.cursor/` in this repo, a plain `git clone` of the bot is enough until you adopt the shared catalog.

## Global-only skills

Skills installed only in your **user** Cursor directory are not on GitHub unless they live in [fabcrowd/skills](https://github.com/fabcrowd/skills) (or are copied here after sync).
