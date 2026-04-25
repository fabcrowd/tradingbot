# Agent C — Settings UI: `wfo_action_log` wiring

## Data / render path

- `App.tsx`: `scalpForTab` = `snapshot?.scalp` → `<SettingsTab scalp={...} send={...} connected={...} snapshot={...} />`.
- `SettingsTab.tsx`: add WFO log in **`WfoTunerRuntimeSection`** (card “WFO & param tuner (runtime)”) — not `SystemHealthTile` (single-line status only).
- Bind to backend field under `scalp` or nested `scalp.wfo` (consistent with other WFO UI telemetry).

## UI patterns to copy

- **Tooltips:** `frontend-new/src/lib/scalpSettingsTooltips.ts` — extend `SCALP_WFO_TT`; `title={SCALP_WFO_TT.key}` on labels; optional `<p className="settings-explainer">`.
- **Read-only mono:** `.settings-readonly` in `WfoTunerRuntimeSection` (WFO objective, rolling bar span).
- **Scrollable log:** No `<textarea>` in repo yet; closest is **`RestartSection`** — `<details className="restart-debug">` + `.restart-debug-body` (`max-height`, `overflow-y: auto`, mono) in `settings-tab.css`. Alternative: `<textarea readOnly>` or `<pre>` in scroll div.

## Types

- **`frontend-new/src/lib/types.ts`:** add field on `WfoUi` if nested with WFO telemetry, or on `ScalpSnapshot` if top-level.
- **CSS:** extend `settings-tab.css` mirroring `.restart-debug-body` / `.settings-readonly` for height and scroll.
