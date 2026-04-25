# Cursor project assets (committed)

This folder holds **repo-local** Cursor configuration that is safe to commit and clone:

| Path | Purpose |
|------|---------|
| `skills/` | Agent Skills (`SKILL.md` per skill folder). Cursor loads project skills when this workspace is open. |
| `rules/` | Project rules (`.mdc`). |
| `agents/` | Custom agent definitions (markdown front matter). |
| `references/` | Long-form reference docs for agents/skills. |

## Another machine

1. `git clone` this repository (or `git pull`).
2. Open the repo folder in **Cursor**. Skills and rules apply to this workspace automatically.

Skills you install only under your **user** Cursor directory (global) are not in this repo—copy those into `.cursor/skills/<skill-name>/SKILL.md` here if you want them versioned with the project.
