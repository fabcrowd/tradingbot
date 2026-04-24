/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Full WebSocket URL (e.g. ws://127.0.0.1:8080/ws) when dev proxy to /ws fails */
  readonly VITE_WS_URL?: string;
  /** Must match server `DASHBOARD_TOKEN` when connecting from non-loopback hosts */
  readonly VITE_DASHBOARD_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
