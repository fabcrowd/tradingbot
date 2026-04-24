import type { Alert, ConfigSnapshot, Snapshot, UiLogEntry } from "./types";

/** Build WS URL: optional full override, else same host as the page + `/ws` + optional token (LAN / DASHBOARD_TOKEN). */
export function resolveDashboardWebSocketUrl(): string {
  const explicit = import.meta.env.VITE_WS_URL?.trim();
  if (explicit) return explicit;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const token = import.meta.env.VITE_DASHBOARD_TOKEN?.trim();
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${protocol}//${window.location.host}/ws${qs}`;
}

type Handlers = {
  onConnection?: (connected: boolean) => void;
  onSnapshot?: (snapshot: Snapshot) => void;
  onConfig?: (config: ConfigSnapshot) => void;
  onAlert?: (alert: Alert) => void;
  onLogEvent?: (entry: UiLogEntry) => void;
};

export class WsClient {
  static shared = new WsClient();
  private ws: WebSocket | null = null;
  private handlers: Handlers = {};
  private reconnectTimer: number | null = null;
  private pendingQueue: string[] = [];
  private intentionalClose = false;

  setHandlers(handlers: Handlers) {
    this.handlers = handlers;
  }

  connect() {
    this.intentionalClose = false;

    if (this.ws) {
      const s = this.ws.readyState;
      if (s === WebSocket.OPEN || s === WebSocket.CONNECTING) return;
      this.ws.onopen = null;
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.onmessage = null;
      this.ws = null;
    }

    const url = resolveDashboardWebSocketUrl();
    console.log("[WS] connecting", url.replace(/token=[^&]+/g, "token=***"));
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      console.log("[WS] connected, readyState=", ws.readyState);
      this.handlers.onConnection?.(true);
      this._flushQueue();
    };

    ws.onclose = (ev) => {
      console.warn(
        "[WS] closed, code=",
        ev.code,
        "reason=",
        ev.reason,
        "intentional=",
        this.intentionalClose,
        "— if LAN access, set DASHBOARD_TOKEN on server and VITE_DASHBOARD_TOKEN + rebuild, or VITE_WS_URL=ws://HOST:8080/ws?token=…",
      );
      this.handlers.onConnection?.(false);
      if (!this.intentionalClose) {
        this._scheduleReconnect();
      }
    };

    ws.onerror = () => {
      this.handlers.onConnection?.(false);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as { type: string; data?: unknown; message?: string; title?: string; [k: string]: unknown };
        if (msg.type === "snapshot" && msg.data) {
          this.handlers.onSnapshot?.(msg.data as Snapshot);
        } else if (msg.type === "config" && msg.data) {
          this.handlers.onConfig?.(msg.data as ConfigSnapshot);
        } else if (msg.type === "log_event" && msg.data && typeof msg.data === "object") {
          this.handlers.onLogEvent?.(msg.data as UiLogEntry);
        } else if (msg.type === "alert" && msg.title) {
          const a = msg as unknown as Alert;
          const raw = msg as Record<string, unknown>;
          if (typeof raw.id === "string" && raw.id) {
            a.id = raw.id;
          } else if (!a.id) {
            a.id = `${a.ts}-${Math.random().toString(36).slice(2, 6)}`;
          }
          if (typeof raw.persistent === "boolean") {
            a.persistent = raw.persistent;
          }
          if (typeof raw.exchange_error_id === "string" && raw.exchange_error_id) {
            a.exchange_error_id = raw.exchange_error_id;
          }
          this.handlers.onAlert?.(a);
        } else if (msg.type === "error" && msg.message) {
          console.error("[WS] Server error:", msg.message);
          this.handlers.onAlert?.({
            id: `err-${Date.now()}`,
            level: "error",
            title: "Server Error",
            detail: msg.message as string,
            source: "ws_server",
            ts: Date.now() / 1000,
          });
        } else if (msg.type === "rebuild_frontend_result") {
          const ok = msg.ok === true;
          const detail = typeof msg.detail === "string" ? msg.detail : "";
          window.dispatchEvent(new CustomEvent("dashboard-rebuild-result", { detail: { ok, detail } }));
        }
      } catch (e) {
        console.warn(
          "[WS] JSON parse failed (often NaN/Inf from server) —",
          e,
          String(event.data).slice(0, 280),
        );
      }
    };
  }

  send(payload: Record<string, unknown>) {
    const raw = JSON.stringify(payload);
    const state = this.ws ? this.ws.readyState : -1;
    console.log("[WS] send()", payload.action, "readyState=", state);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(raw);
      console.log("[WS] sent OK");
    } else {
      this.pendingQueue.push(raw);
      console.warn("[WS] queued, readyState=", state, "queue=", this.pendingQueue.length);
      if (!this.ws || this.ws.readyState === WebSocket.CLOSED || this.ws.readyState === WebSocket.CLOSING) {
        if (this.ws?.readyState === WebSocket.CLOSING) {
          this.ws.onopen = null;
          this.ws.onclose = null;
          this.ws.onerror = null;
          this.ws.onmessage = null;
          this.ws = null;
        }
        this.connect();
      }
    }
  }

  close() {
    this.intentionalClose = true;
    if (this.reconnectTimer) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
    }
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 1500);
  }

  private _flushQueue() {
    while (this.pendingQueue.length > 0 && this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(this.pendingQueue.shift()!);
    }
  }
}
