---
name: parallel-exploring
description: >-
  Explore a large codebase in parallel by launching multiple explore subagents that
  each investigate a different area simultaneously. Use when onboarding onto a new
  project, understanding architecture, or investigating a cross-cutting concern.
model: inherit
readonly: true
is_background: true
---

# Parallel Explore

Use when you need to understand a large or unfamiliar codebase quickly — onboarding, how a feature works across layers, or mapping architecture.

## How it works

Launch **multiple** Task subagents with `subagent_type: "explore"` in **one message** so they run concurrently. Explore agents are read-only.

## Steps

1. Split into zones (e.g. `backend/server/scalp_bot/`, `pine/`, `frontend-new/`, `tools/`, config).
2. One focused Task per zone — paths, framework, entry points, risks.
3. Synthesize: stack, data flow (feed → bar_store → signal → execution), key files, tech debt.

## Notes

- Use **very thorough** when asked for full map.
- Single-symbol questions → parent uses Grep, not this subagent.

Source: [spencerpauly/awesome-cursor-skills](https://github.com/spencerpauly/awesome-cursor-skills/tree/main/resources/parallel-exploring)
