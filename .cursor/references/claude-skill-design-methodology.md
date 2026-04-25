# Claude / Cursor skill design methodology (reference)

Saved for operators authoring or refining `SKILL.md` files. Source: community skill-design course (Khairallah / eng_khairallah1); adapted for this repo’s Cursor skills.

## What a skill is

A skill is a **markdown file** (`SKILL.md`, often in a folder with optional `references/`) that instructs the model how to perform **one** task: what to do, how, what good output looks like, what to avoid, and how to handle edge cases.

## Five components (all required for reliability)

1. **YAML trigger header** (`---` … `---`)
   - `name`, `description` (max ~1024 chars where enforced).
   - **Rule A — Pushy activation:** list **5–7+** explicit phrases users might say.
   - **Rule B — Negative boundaries:** “Do NOT use for …” so unrelated chats are not hijacked.
   - **Rule C — Third person:** “Generates …” not “I can help …”.

2. **Overview** — One paragraph for the model: purpose + when it runs.

3. **Workflow** — Numbered **imperative** steps; one action per step; testable (“ask for X if missing”, not “handle appropriately”).

4. **Output format** — Exact shape: headings, length, tone, tables, forbidden patterns.

5. **Examples** — At least **happy path** + **edge case** + ideally **negative** (request that must *not* activate this skill). Concrete mini input → output.

## Five failure modes (diagnose → fix)

| Mode | Symptom | Fix |
|------|---------|-----|
| Silent | Never fires | Enrich description with user’s exact words / synonyms |
| Hijacker | Fires wrongly | Narrow triggers; add negative boundaries |
| Drifter | Wrong output | Replace vague steps with testable instructions |
| Fragile | Breaks on odd input | Add “If [condition], then [action]” branches |
| Overachiever | Extra unsolicited content | “Output ONLY …”; forbid commentary |

## Testing protocol (before trusting a skill)

1. Happy path (complete input).
2. Minimal input (asks for missing pieces / degrades gracefully).
3. Edge cases (contradictions, typos, extreme length).
4. Negative test (similar task that must **not** use this skill).
5. Repeat test (same input 3× → consistent output).

## Deploy paths

- Personal: `~/.cursor/skills/` or Codex equivalent.
- Project: `.cursor/skills/` in the repository.

## Folder shape

```text
my-skill/
├── SKILL.md
└── references/   # optional
    └── ...
```
