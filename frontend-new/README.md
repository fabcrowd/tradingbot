# Stitch-Integrated Frontend

This frontend is the Google Stitch integration target for the trading bot dashboard.
It does not emulate Stitch. Instead, it consumes real Stitch exports and maps assets
into the app through an ingestion workflow.

## Workflow

1. Export a design ZIP from Google Stitch.
2. Ingest export into this frontend:

```bash
cd frontend-new
npm run stitch:ingest -- "/absolute/path/to/stitch-export.zip"
```

3. Run local UI:

```bash
npm install
npm run dev
```

4. Validate production build:

```bash
npm run build
```

5. Cutover to backend-served legacy frontend folder:

```bash
cd frontend-new
$env:BUILD_TO_LEGACY=1; npm run build; Remove-Item Env:BUILD_TO_LEGACY
```

## Rollback

Before cutover, keep a backup of `frontend/index.html` as `frontend/index.legacy.html`.
If needed, restore it manually and rebuild.

## Contract Notes

- WebSocket endpoint: `/ws`
- Existing backend action contract is preserved from the legacy UI:
  - `set_active_pair`, `toggle_pair`, `smart_defaults`
  - `update_config`, `apply_trading_controls`
  - `set_mode`, `set_adaptive_tuning`
  - `start`, `stop`, `kill`, `soft_restart`
  - `update_risk` (with optional `resume_risk_halt`)
