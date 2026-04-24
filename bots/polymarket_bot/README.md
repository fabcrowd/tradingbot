# Polymarket Bot Service

Isolated service for Polymarket strategy execution under the shared multi-bot UI/UX contract.

## Purpose

- Keep Polymarket logic fully separated from Kraken and other bot stacks.
- Expose a normalized API contract for shared UI panels.
- Start in paper mode by default.

## Service Contract

The service exposes:

- `GET /status`
- `GET /metrics`
- `GET /positions`
- `GET /trades?limit=N`
- `GET /risk`
- `POST /risk`
- `POST /control/start`
- `POST /control/stop`
- `POST /control/pause`

## Run Locally

```bash
pip install -r bots/polymarket_bot/requirements.txt
python -m bots.polymarket_bot.src.runner
```

Default bind: `0.0.0.0:8091`

## Config

Copy:

```bash
cp bots/polymarket_bot/.env.example bots/polymarket_bot/.env
```

All env vars are prefixed with `PMBOT_`.

## Notes

- This scaffold intentionally focuses on service boundaries and observability contract.
- Public Polymarket feed mode works without API credentials.
- Live mode supports safe credential wiring via dry-run:
  - `PMBOT_MODE=live`
  - `PMBOT_LIVE_DRY_RUN=true`
- To enable live credential initialization, set:
  - `PMBOT_POLY_PRIVATE_KEY`
  - `PMBOT_POLY_FUNDER`
- Real live order placement is still gated; current live path is dry-run for safe rollout.
