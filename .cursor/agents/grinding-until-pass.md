---
name: grinding-until-pass
description: >-
  Keep iterating on code changes until the tests pass, the build succeeds, or linting
  is clean. Runs in a tight loop of fix → run → check → repeat. Use when you want the
  agent to autonomously grind through test failures or build errors.
model: inherit
readonly: false
is_background: false
---

# Grind Until Pass

Loop until tests/build/lint pass.

## Default goal (this repo)

From `tradingbot-main`:

```bash
pytest backend/server/scalp_bot/ -q
```

Or a path the user specifies.

## Rules

- Max **10** iterations
- Minimal fixes; one error at a time
- No deleting tests or silencing linters without cause
- Stop if errors increase

Source: [spencerpauly/awesome-cursor-skills](https://github.com/spencerpauly/awesome-cursor-skills/tree/main/resources/grinding-until-pass)
