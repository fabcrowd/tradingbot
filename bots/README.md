# Multi-Bot Service Standard

All bots in this repository must follow the same isolation and UI/UX integration contract.

## Required layout

```
bots/<bot_name>/
  src/
  tests/
  Dockerfile
  .env.example
  README.md
  service_manifest.yaml
  data/
  logs/
```

## Service contract

Each bot must expose:

- `GET /status`
- `GET /metrics`
- `GET /positions`
- `GET /trades?limit=N`
- `GET /risk`
- `POST /risk`
- `POST /control/start`
- `POST /control/stop`
- `POST /control/pause`

## Manifest contract

`service_manifest.yaml` must include:

- `bot_id`
- `display_name`
- `version`
- `runtime`
- `entrypoint`
- `healthcheck`
- `capabilities`
- `config_schema`
- `ui_panels`
- `metrics_contract`
- `risk_controls`
- `service`

Note: manifests are stored as JSON-compatible YAML so they can be parsed with
standard library JSON tooling.

