import { Component, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { WsClient } from "./lib/wsClient";
import type {
  Alert,
  BookLevel,
  ConfigSnapshot,
  Fill,
  PairSnapshot,
  ScalpPosition,
  ScalpSnapshot,
  ScalpTrade,
  ScalpVenueOpenOrder,
  Snapshot,
  UiLogEntry,
} from "./lib/types";
import { warmupProgressFromSnapshot } from "./lib/warmupUiProgress";
import { warmupTickerTextsFromLogs } from "./lib/warmupLogTicker";
import { SystemsPanel } from "./components/SystemsPanel";
import { AnalyticsTab } from "./components/AnalyticsTab";
import { SettingsTab } from "./components/SettingsTab";
import { LogsTab } from "./components/LogsTab";
import { FlightDeck } from "./components/FlightDeck";
import { ScalpTerminalChart } from "./components/ScalpTerminalChart";
import { TerminalBrainPanel } from "./components/TerminalBrainPanel";
import "./styles/app.css";

const MAX_CLIENT_LOG = 15000;

/** When WS snapshots omit `scalp` (old server / parse drop), keep tab usable while connected. */
const SCALP_TAB_STUB: ScalpSnapshot = {
  runtime_attached: false,
  enabled: false,
  venue: "coinbase_perps",
  sim_mode: false,
  startup_phase: "standby",
  warmup: { phase: "disabled", enabled: false, startup_steps: [] },
  operator: {
    standby: true, prep_busy: false, require_manual_go_live: false,
    flow: null, flow_seq: 0, flow_event: null,
    startup_phase: "standby", can_begin_warmup: true, can_go_live: false, warmup_steps: [],
  },
  session_policy: {
    warmup_enabled: true,
    warmup_min_bars: 500,
    warmup_require_champion: true,
    warmup_max_hours: 0,
    wfo_enabled: true,
    wfo_interval_sec: 3600,
    wfo_train_hours: 6,
    wfo_holdout_hours: 2,
    wfo_step_hours: 2,
    wfo_top_k: 50,
    wfo_objective: "expectancy_sqrt_n",
    param_tuner_interval_sec: 120,
    wfo_max_roll_windows: 12,
    wfo_train_same_calendar_day_boost: 0,
    wfo_roll_span_hours: 0,
    wfo_min_trades: 20,
    wfo_min_holdout_trades: 0,
    backtest_funding_enabled: false,
    backtest_funding_bps_per_hour: 0,
    scalp_fee_assumption_revision: 0,
    fee_tier_30d_volume_usd: null,
    fee_tier_volume_source: "manual",
    fee_tier_poll_interval_sec: 900,
    fee_tier_add_bot_fill_notional: false,
    fee_tier_auto_apply_exchange_fee_rates: true,
    scalp_auto_invalidate_champion_on_fee_change: false,
    param_tuner_require_wfo_champion: true,
    param_tuner_allow_mode_override_champion: false,
    wfo_assume_taker_fee: false,
    wfo_forward_min_trades: 10,
    wfo_forward_demotion_threshold: -0.5,
    funding_warn_bps_per_hour: 5,
    empirical_market_promotion_enabled: false,
    empirical_market_ttl_cancel_arms_promotion: false,
  },
  trader: {
    open_positions: {},
    open_count: 0,
    daily_pnl: 0,
    reserved_capital: 0,
    trade_history: [],
    sim_mode: false,
    empirical_market: {
      enabled: false,
      promotion_remaining: {},
      active_watch_count: 0,
      pattern_buffer_len: 0,
    },
  },
  fee_tier: {
    volume_source: "manual",
    display_volume_usd: null,
    manual_baseline_usd: null,
    bot_fill_usd_session: 0,
    exchange: null,
    last_poll_ts: 0,
    poll_error: null,
    poll_interval_sec: 900,
    auto_apply_exchange_fee_rates: true,
    effective_maker_bps: 6.5,
    effective_taker_bps: 7.0,
  },
  indicators: {},
};

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error("[ErrorBoundary]", error, info.componentStack); }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, color: "#ffb4ab", fontFamily: "monospace", fontSize: 12 }}>
          <div style={{ marginBottom: 12, fontWeight: 700 }}>RENDER_ERROR</div>
          <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", color: "#e5e2e3" }}>
            {this.state.error.message}
          </pre>
          <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", color: "#919191", fontSize: 10, marginTop: 8 }}>
            {this.state.error.stack}
          </pre>
          <button
            style={{ marginTop: 16, padding: "6px 16px", background: "#353436", color: "#e5e2e3", border: "none", cursor: "pointer" }}
            onClick={() => this.setState({ error: null })}
          >RETRY</button>
        </div>
      );
    }
    return this.props.children;
  }
}

function fmt(v: number | undefined | null, d = 2): string {
  return Number(v ?? 0).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

function ts(epoch: number): string {
  if (!epoch) return "--:--:--";
  const d = new Date(epoch * 1000);
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function priceDec(sym: string): number {
  if (sym.startsWith("TEL")) return 6;
  if (sym.startsWith("XBT") || sym.startsWith("BTC")) return 1;
  if (sym.startsWith("ETH") || sym.startsWith("SOL")) return 2;
  return 4;
}

function WarmupLogTicker({ texts }: { texts: string[] }) {
  if (!texts.length) return null;
  const sep = "   •   ";
  const line = texts.join(sep);
  const full = `${line}${sep}`;
  return (
    <div className="sys-activity-ticker-wrap" title={line}>
      <div className="sys-activity-ticker-inner">
        <span className="sys-activity-ticker-chunk">{full}</span>
        <span className="sys-activity-ticker-chunk" aria-hidden>
          {full}
        </span>
      </div>
    </div>
  );
}

function SystemActivityBar({
  connected,
  snapshot,
  scalp,
  warmupTickerTexts,
}: {
  connected: boolean;
  snapshot: Snapshot | null;
  scalp: ScalpSnapshot | null | undefined;
  warmupTickerTexts: string[];
}) {
  if (!connected) {
    return (
      <div
        className="sys-activity-bar sys-activity-offline"
        role="status"
        title="WebSocket /ws failed. Open the browser console for [WS] logs. If you open the dashboard from another machine, set DASHBOARD_TOKEN on the server and rebuild the UI with VITE_DASHBOARD_TOKEN (or set VITE_WS_URL to ws://BOT_IP:8080/ws?token=…). npm run dev proxies /ws only when the Vite dev server is used."
      >
        <span className="sys-activity-label">SERVER · OFFLINE</span>
      </div>
    );
  }

  if (snapshot?.risk_halted) {
    return (
      <div className="sys-activity-bar sys-activity-halt" role="status" aria-live="polite">
        <span className="sys-activity-halt-dot" aria-hidden>
          ●
        </span>
        <span className="sys-activity-label sys-activity-label-pulse-risk">RISK HALTED</span>
      </div>
    );
  }

  const sc = scalp;
  if (sc?.enabled) {
    if (sc.runtime_attached === false) {
      return (
        <div className="sys-activity-bar sys-activity-attaching" role="status">
          <span className="sys-activity-label sys-activity-label-pulse-muted">SCALP · ATTACHING</span>
        </div>
      );
    }

    const startup = String(sc.startup_phase ?? sc.operator?.startup_phase ?? "standby").toLowerCase();
    const wp = String(sc.warmup?.phase ?? "").toLowerCase();
    const prepBusy = sc.operator?.prep_busy === true;

    if (prepBusy) {
      const tick = warmupTickerTexts.length > 0;
      return (
        <div
          className={`sys-activity-bar sys-activity-warming${tick ? " sys-activity-with-ticker sys-activity-wide" : ""}`}
          role="status"
        >
          <span className="sys-activity-warm-dot" aria-hidden>
            ●
          </span>
          <span className="sys-activity-label sys-activity-label-pulse-warm">PREPARING</span>
          {tick ? <WarmupLogTicker texts={warmupTickerTexts} /> : null}
        </div>
      );
    }

    const isWarming = startup === "warming_up" || wp === "collecting" || wp === "optimizing";
    if (isWarming) {
      const uiProg = warmupProgressFromSnapshot(sc);
      const detail = uiProg.stepShort ?? (wp === "optimizing" ? "WFO" : wp === "collecting" ? "BARS" : "");
      const label = detail ? `WARMING UP · ${detail}` : "WARMING UP";
      const pct = uiProg.pct;
      const showBar = sc.warmup?.enabled === true;
      const tick = warmupTickerTexts.length > 0;
      return (
        <div
          className={`sys-activity-bar sys-activity-warming sys-activity-wide${tick ? " sys-activity-with-ticker" : ""}`}
          role="status"
          aria-live="polite"
        >
          <span className="sys-activity-warm-dot" aria-hidden>
            ●
          </span>
          <span className="sys-activity-label sys-activity-label-pulse-warm">{label}</span>
          {showBar ? (
            <>
              <div className="sys-activity-track">
                <div className="sys-activity-fill" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
              </div>
              <span className="sys-activity-pct">{pct.toFixed(0)}%</span>
            </>
          ) : null}
          {tick ? <WarmupLogTicker texts={warmupTickerTexts} /> : null}
        </div>
      );
    }

    if (startup === "primed") {
      return (
        <div className="sys-activity-bar sys-activity-primed" role="status">
          <span className="sys-activity-label">PRIMED · GO LIVE</span>
        </div>
      );
    }

    if (startup === "live") {
      const sim = sc.sim_mode;
      return (
        <div
          className={`sys-activity-bar${sim ? " sys-activity-live-sim" : " sys-activity-armed"}`}
          role="status"
        >
          <span className={sim ? "sys-activity-dot-sim" : "sys-activity-dot-armed"} aria-hidden>
            ●
          </span>
          <span className="sys-activity-label">{sim ? "LIVE · SIM" : "LIVE · TRADING"}</span>
        </div>
      );
    }

    return (
      <div className="sys-activity-bar sys-activity-standby" role="status">
        <span className="sys-activity-label">STANDBY</span>
      </div>
    );
  }

  const mode = snapshot?.mode ? String(snapshot.mode).toUpperCase() : "—";
  return (
    <div className="sys-activity-bar sys-activity-live" role="status">
      <span className="sys-activity-dot" aria-hidden>
        ●
      </span>
      <span className="sys-activity-live-label">CONNECTED · {mode}</span>
    </div>
  );
}

const MAX_ALERTS = 8;
const ALERT_TTL_MS: Record<string, number> = { error: 15000, warning: 10000, info: 6000, success: 5000 };

function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [config, setConfig] = useState<ConfigSnapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const [activePair, setActivePair] = useState("");
  const [fillsOpen, setFillsOpen] = useState(true);
  const [pendingOrdersOpen, setPendingOrdersOpen] = useState(true);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [activeTab, setActiveTab] = useState<"terminal" | "analytics" | "settings" | "logs">("terminal");
  const [logMap, setLogMap] = useState<Map<string, UiLogEntry>>(() => new Map());
  const [logFocus, setLogFocus] = useState<{ logId?: string; exchangeErrorId?: string } | null>(null);
  const [lastSnapshotTs, setLastSnapshotTs] = useState<number | null>(null);
  const pairInitRef = useRef(false);
  const ws = useMemo(() => WsClient.shared, []);
  const [wallClockSec, setWallClockSec] = useState(() => Math.floor(Date.now() / 1000));
  const dismissAlert = useCallback((id: string) => setAlerts(prev => prev.filter(a => a.id !== id)), []);
  const clearLogFocus = useCallback(() => setLogFocus(null), []);

  const pushClientAlert = useCallback(
    (partial: Pick<Alert, "level" | "title" | "detail"> & { id: string; source?: string; ttlMs?: number }) => {
      const ts = Date.now() / 1000;
      const alert: Alert = {
        id: partial.id,
        level: partial.level,
        title: partial.title,
        detail: partial.detail,
        source: partial.source ?? "dashboard",
        ts,
      };
      setAlerts((prev) => [alert, ...prev.filter((a) => a.id !== partial.id)].slice(0, MAX_ALERTS));
      const ttl = partial.ttlMs ?? ALERT_TTL_MS[partial.level] ?? 10000;
      setTimeout(() => dismissAlert(partial.id), ttl);
    },
    [dismissAlert],
  );

  const wasConnectedRef = useRef(false);
  const riskHaltToastRef = useRef(false);
  const snapErrorToastRef = useRef(false);

  useEffect(() => {
    const was = wasConnectedRef.current;
    wasConnectedRef.current = connected;
    if (was && !connected) {
      queueMicrotask(() =>
        pushClientAlert({
          id: `disconnect-${Math.floor(Date.now() / 1000)}`,
          level: "warning",
          title: "Connection lost",
          detail: "WebSocket to the bot dropped — the UI may show stale data until reconnect.",
          source: "dashboard",
          ttlMs: 14000,
        }),
      );
    }
  }, [connected, pushClientAlert]);

  useEffect(() => {
    const h = snapshot?.risk_halted === true;
    if (h && !riskHaltToastRef.current) {
      const reason = snapshot?.risk_halt_reason;
      queueMicrotask(() =>
        pushClientAlert({
          id: "risk-halt-notify",
          level: "warning",
          title: "Risk halted",
          detail: reason || "Portfolio risk halt is active.",
          source: "dashboard",
          ttlMs: 18000,
        }),
      );
    }
    riskHaltToastRef.current = h;
  }, [snapshot?.risk_halted, snapshot?.risk_halt_reason, pushClientAlert]);

  useEffect(() => {
    const e = snapshot?.scalp?.snapshot_error === true;
    if (e && !snapErrorToastRef.current) {
      queueMicrotask(() =>
        pushClientAlert({
          id: "scalp-snapshot-error",
          level: "error",
          title: "Scalp snapshot degraded",
          detail: "Server fell back after snapshot() raised — check backend logs and session JSONL.",
          source: "dashboard",
          ttlMs: 22000,
        }),
      );
    }
    snapErrorToastRef.current = e;
  }, [snapshot?.scalp?.snapshot_error, pushClientAlert]);

  const mergeLogTail = useCallback((tail: UiLogEntry[] | undefined) => {
    if (!tail?.length) return;
    setLogMap((prev) => {
      const next = new Map(prev);
      for (const e of tail) next.set(e.id, e);
      while (next.size > MAX_CLIENT_LOG) {
        const sorted = [...next.values()].sort((a, b) => a.ts - b.ts);
        const drop = sorted.length - MAX_CLIENT_LOG;
        for (let i = 0; i < drop; i++) next.delete(sorted[i]!.id);
      }
      return next;
    });
  }, []);

  const openLogs = useCallback((opts?: { focusExchangeId?: string }) => {
    setActiveTab("logs");
    if (opts?.focusExchangeId) setLogFocus({ exchangeErrorId: opts.focusExchangeId });
    else setLogFocus(null);
  }, []);

  useEffect(() => {
    ws.setHandlers({
      onConnection: setConnected,
      onSnapshot: (next) => {
        mergeLogTail(next.ui_log_tail);
        setLastSnapshotTs(Date.now() / 1000);
        setSnapshot(prev => {
          const merged: Snapshot = { ...next };
          if (prev?.scalp && (next as Snapshot).scalp === undefined && !("scalp" in (next as object))) {
            merged.scalp = prev.scalp;
          }
          if (prev?.scalp?.candles && merged.scalp?.candles) {
            for (const pk of Object.keys(merged.scalp.candles)) {
              const incoming = merged.scalp.candles[pk];
              const existing = prev.scalp.candles[pk];
              const inc = incoming?.closed;
              const hasIncomingClosed = Array.isArray(inc) && inc.length > 0;
              const ex = existing?.closed;
              if (incoming && !hasIncomingClosed && Array.isArray(ex) && ex.length > 0) {
                incoming.closed = ex;
              }
              const incOv = incoming?.indicator_overlay;
              const hasIncomingOv = Array.isArray(incOv) && incOv.length > 0;
              const exOv = existing?.indicator_overlay;
              if (incoming && !hasIncomingOv && Array.isArray(exOv) && exOv.length > 0) {
                incoming.indicator_overlay = exOv;
              }
            }
          }
          return merged;
        });
        if (!pairInitRef.current) {
          const mm = Object.keys(next.pairs ?? {});
          const sk = next.scalp?.pair_symbols ? Object.keys(next.scalp.pair_symbols) : [];
          const tabKeys = sk.length > 0 ? sk : mm;
          let pick = (next.active_pair_key || "").trim();
          if (tabKeys.length > 0 && (!pick || !tabKeys.includes(pick))) {
            pick = tabKeys[0]!;
          }
          if (pick) {
            pairInitRef.current = true;
            setActivePair(pick);
          }
        }
      },
      onConfig: setConfig,
      onLogEvent: (entry) => {
        setLogMap((prev) => {
          const next = new Map(prev);
          next.set(entry.id, entry);
          while (next.size > MAX_CLIENT_LOG) {
            const sorted = [...next.values()].sort((a, b) => a.ts - b.ts);
            const drop = sorted.length - MAX_CLIENT_LOG;
            for (let i = 0; i < drop; i++) next.delete(sorted[i]!.id);
          }
          return next;
        });
      },
      onAlert: (alert) => {
        if (alert.persistent) return;
        setAlerts(prev => {
          const dupe = prev.find(a => a.title === alert.title && Date.now() / 1000 - a.ts < 5);
          if (dupe) return prev;
          const next = [alert, ...prev].slice(0, MAX_ALERTS);
          return next;
        });
        const ttl = ALERT_TTL_MS[alert.level] ?? 8000;
        setTimeout(() => dismissAlert(alert.id), ttl);
      },
    });
    ws.connect();
  }, [ws, dismissAlert, mergeLogTail]);

  useEffect(() => {
    const id = setInterval(() => setWallClockSec(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(id);
  }, []);

  /** Invalid ``activePair`` is ignored: ``selectedPair`` below clamps to ``terminalPairKeys``. */

  useEffect(() => {
    if (snapshot?.session_start_ts == null) return undefined;
    const id = window.setTimeout(() => {
      setWallClockSec(Math.floor(Date.now() / 1000));
    }, 0);
    return () => window.clearTimeout(id);
  }, [snapshot?.session_start_ts]);

  const send = (payload: Record<string, unknown>) => ws.send(payload);

  const logEntries = useMemo(
    () => [...logMap.values()].sort((a, b) => a.ts - b.ts),
    [logMap],
  );

  const warmupLogTickerLines = useMemo(
    () => warmupTickerTextsFromLogs(logEntries),
    [logEntries],
  );

  const scalpForSettings = useMemo(() => {
    if (snapshot?.scalp != null) return snapshot.scalp;
    if (connected) return SCALP_TAB_STUB;
    return null;
  }, [snapshot, connected]);

  const unackedExchangeErrors = useMemo(() => {
    const list = snapshot?.exchange_errors ?? [];
    return list.filter((e) => !e.acknowledged);
  }, [snapshot?.exchange_errors]);

  const latestUnackedExchange = useMemo(() => {
    if (!unackedExchangeErrors.length) return null;
    return unackedExchangeErrors[unackedExchangeErrors.length - 1];
  }, [unackedExchangeErrors]);

  const flashAnalyticsForExchange = unackedExchangeErrors.length > 0 && activeTab !== "analytics";
  const flashLogsForExchange = unackedExchangeErrors.length > 0 && activeTab !== "logs";

  const mmPairs = snapshot?.pairs ?? {};
  const scalpSyms = snapshot?.scalp?.pair_symbols ?? {};
  const mmKeys = Object.keys(mmPairs);
  const scalpKeys = Object.keys(scalpSyms);
  /** Scalp/Coinbase tabs when ``pair_symbols`` exists; else ``[bot.pairs]`` snapshot keys only. */
  const terminalPairKeys = scalpKeys.length > 0 ? scalpKeys : mmKeys;
  const rawPairPick = activePair || snapshot?.active_pair_key || terminalPairKeys[0] || "";
  const selectedPair =
    terminalPairKeys.length > 0 && !terminalPairKeys.includes(rawPairPick)
      ? terminalPairKeys[0]!
      : rawPairPick;
  const ps: PairSnapshot | undefined = mmPairs[selectedPair];
  /** When scalp pairs exist, terminal chart/book/orders follow CDE ``[scalp.pairs.*].symbol`` (not ``[pairs.*].symbol``). */
  const isScalpTerminal = scalpKeys.length > 0;
  const cdeProductId = isScalpTerminal && selectedPair ? scalpSyms[selectedPair] : undefined;
  const displaySymbol = cdeProductId ?? ps?.symbol ?? scalpSyms[selectedPair] ?? selectedPair ?? "";
  const tabLabel = (pk: string) =>
    isScalpTerminal && scalpSyms[pk] ? scalpSyms[pk] : mmPairs[pk]?.symbol ?? scalpSyms[pk] ?? pk;
  const tradingPairs =
    scalpKeys.length > 0
      ? scalpKeys
      : config?.pair_keys_for_trading?.length
        ? config.pair_keys_for_trading
        : terminalPairKeys;
  const fills: Fill[] = useMemo(() => {
    if (isScalpTerminal && selectedPair) {
      const th = (snapshot?.scalp?.trader?.trade_history ?? []) as ScalpTrade[];
      return [...th]
        .filter((t) => t.pair_key === selectedPair)
        .sort((a, b) => (b.exit_ts ?? 0) - (a.exit_ts ?? 0))
        .slice(0, 12)
        .map((t) => ({
          timestamp: t.exit_ts,
          pair_key: t.pair_key,
          side: t.direction === "short" ? "buy" : "sell",
          price: t.exit_price,
          qty: t.qty,
          fee: 0,
          pnl_delta: t.pnl ?? 0,
        }));
    }
    return (snapshot?.recent_fills ?? []).slice(-12).reverse();
  }, [isScalpTerminal, selectedPair, snapshot?.scalp?.trader?.trade_history, snapshot?.recent_fills]);
  const dec = priceDec(displaySymbol);

  const candlePack = selectedPair ? snapshot?.scalp?.candles?.[selectedPair] : undefined;
  const hasScalpChart =
    !!candlePack && ((candlePack.closed?.length ?? 0) > 0 || candlePack.live != null);

  const sessionPnl = (snapshot?.total_pnl ?? 0) - (snapshot?.session_start_pnl ?? 0);

  const mmBookBids = (ps?.bid_levels ?? []).slice(0, 5);
  const mmBookAsks = (ps?.ask_levels ?? []).slice(0, 5);
  const scalpOb = selectedPair ? snapshot?.scalp?.orderbooks?.[selectedPair] : undefined;
  const bidLevels: BookLevel[] = isScalpTerminal
    ? (scalpOb?.bids ?? []).slice(0, 12).map(([price, volume]) => ({ price, volume }))
    : mmBookBids.length > 0
      ? mmBookBids
      : (scalpOb?.bids ?? []).slice(0, 8).map(([price, volume]) => ({ price, volume }));
  const askLevels: BookLevel[] = isScalpTerminal
    ? (scalpOb?.asks ?? []).slice(0, 12).map(([price, volume]) => ({ price, volume }))
    : mmBookAsks.length > 0
      ? mmBookAsks
      : (scalpOb?.asks ?? []).slice(0, 8).map(([price, volume]) => ({ price, volume }));

  const coinbaseRestingForTab: ScalpVenueOpenOrder[] = (() => {
    const raw = snapshot?.scalp?.exchange_open_orders ?? [];
    if (!isScalpTerminal || !cdeProductId) return raw;
    const u = String(cdeProductId).toUpperCase();
    return raw.filter((o) => String(o.product_id).toUpperCase() === u);
  })();
  /** Distinct Coinbase ``product_id`` on OPEN/PENDING orders (full-key poll) — explains empty CDE_RESTING for one tab. */
  const venueOpenOrderProductCounts = useMemo(() => {
    const all = snapshot?.scalp?.exchange_open_orders_all ?? [];
    const m = new Map<string, number>();
    for (const o of all) {
      const p = String(o.product_id ?? "").trim();
      if (!p) continue;
      const k = p.toUpperCase();
      m.set(k, (m.get(k) ?? 0) + 1);
    }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [snapshot?.scalp?.exchange_open_orders_all]);
  const pendingOrdersHdr = isScalpTerminal ? `${coinbaseRestingForTab.length} open` : "0";

  const openPosMap = snapshot?.scalp?.trader?.open_positions ?? {};
  const positionsForChart: ScalpPosition[] = Object.values(openPosMap).filter(
    (p) => p.pair_key === selectedPair && (p.status === "open" || p.status === "pending"),
  );

  const terminalStrategyBanner =
    !isScalpTerminal || !selectedPair || !snapshot?.scalp
      ? null
      : {
          mode: snapshot.scalp.active_modes?.[selectedPair] ?? snapshot.scalp.auto_mode_fallback ?? "—",
          source: snapshot.scalp.mode_sources?.[selectedPair],
          regime:
            snapshot.scalp.regime_risk_on?.active && snapshot.scalp.regime_risk_on.mode_label
              ? snapshot.scalp.regime_risk_on.mode_label
              : null,
        };

  const tradesForChart = useMemo(() => {
    if (!isScalpTerminal || !selectedPair) return [];
    const th = (snapshot?.scalp?.trader?.trade_history ?? []) as ScalpTrade[];
    return th.filter((t) => t.pair_key === selectedPair);
  }, [isScalpTerminal, selectedPair, snapshot?.scalp?.trader?.trade_history]);

  const allBookVols = [...bidLevels.map((l) => l.volume), ...askLevels.map((l) => l.volume)];
  const maxBookVol = allBookVols.length > 0 ? Math.max(...allBookVols) : 0.001;
  const topBid = bidLevels[0]?.price;
  const topAsk = askLevels[0]?.price;
  const midFromBook =
    topBid != null && topAsk != null && topBid > 0 && topAsk > 0 ? (topBid + topAsk) / 2 : undefined;
  const spreadAbs =
    topBid != null && topAsk != null ? Math.max(0, topAsk - topBid) : (ps?.spread ?? 0);
  const spreadBps =
    midFromBook != null && midFromBook > 0
      ? ((spreadAbs / midFromBook) * 10000).toFixed(2)
      : ps?.mid_price
        ? ((ps.spread / ps.mid_price) * 10000).toFixed(2)
        : "0";

  const snapshotAgeSec =
    lastSnapshotTs != null ? Math.max(0, Math.floor(wallClockSec - lastSnapshotTs)) : null;
  const snapshotAgeTitle =
    snapshotAgeSec == null
      ? "No snapshot yet (waiting for WebSocket)."
      : `Seconds since last WebSocket snapshot (often longer while WFO or backfill runs).`;

  const alertIcon = (level: string) => {
    if (level === "error") return "✕";
    if (level === "warning") return "⚠";
    if (level === "success") return "✓";
    return "ℹ";
  };

  return (
    <div className="shell">
      {latestUnackedExchange && (
        <div
          className={`exchange-error-strip exchange-error-sev-${latestUnackedExchange.level === "error" ? "error" : "warn"}`}
          role="alert"
        >
          <div className="exchange-error-strip-main">
            <span className="exchange-error-strip-badge" aria-hidden>
              {unackedExchangeErrors.length}
            </span>
            <div className="exchange-error-strip-text">
              <div className="exchange-error-strip-title">{latestUnackedExchange.title}</div>
              {latestUnackedExchange.detail ? (
                <div className="exchange-error-strip-detail">{latestUnackedExchange.detail}</div>
              ) : null}
              <div className="exchange-error-strip-meta">
                {latestUnackedExchange.source || "exchange"} · {ts(latestUnackedExchange.ts)}
              </div>
            </div>
          </div>
          <div className="exchange-error-strip-actions">
            <button
              type="button"
              className="exchange-error-strip-btn"
              onClick={() => send({ action: "acknowledge_exchange_errors", error_ids: [latestUnackedExchange.id] })}
            >
              Acknowledge
            </button>
            {unackedExchangeErrors.length > 1 ? (
              <button
                type="button"
                className="exchange-error-strip-btn secondary"
                onClick={() => send({ action: "acknowledge_exchange_errors" })}
              >
                Acknowledge all ({unackedExchangeErrors.length})
              </button>
            ) : null}
            <button
              type="button"
              className="exchange-error-strip-btn secondary"
              onClick={() => openLogs({ focusExchangeId: latestUnackedExchange.id })}
            >
              View in Logs
            </button>
          </div>
        </div>
      )}
      {/* ── TOAST ALERTS ── */}
      {alerts.length > 0 && (
        <div className="toast-container">
          {alerts.map(a => (
            <div
              key={a.id}
              className={`toast toast-${a.level} toast-clickable`}
              role="button"
              tabIndex={0}
              onClick={() => {
                setActiveTab("logs");
                setLogFocus({ logId: a.id });
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setActiveTab("logs");
                  setLogFocus({ logId: a.id });
                }
              }}
              title={`${a.detail ? `${a.detail}\n\n` : ""}Click to open Logs`}
            >
              <span className="toast-icon">{alertIcon(a.level)}</span>
              <div className="toast-body">
                <div className="toast-title">{a.title}</div>
                {a.detail && <div className="toast-detail">{a.detail}</div>}
                {a.source && <div className="toast-source">{a.source}</div>}
              </div>
              <button
                type="button"
                className="toast-close"
                onClick={(e) => {
                  e.stopPropagation();
                  dismissAlert(a.id);
                }}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {/* ── TOP BAR ── */}
      <header className="topbar">
        <div className="topbar-left-cluster">
          <span className="brand">ARCEUS</span>
          <SystemActivityBar
            connected={connected}
            snapshot={snapshot}
            scalp={snapshot?.scalp}
            warmupTickerTexts={warmupLogTickerLines}
          />
        </div>
        <nav className="top-nav">
          <button className={`tn${activeTab === "terminal" ? " active" : ""}`} onClick={() => setActiveTab("terminal")}>TERMINAL</button>
          <button
            className={`tn${activeTab === "analytics" ? " active" : ""}${flashAnalyticsForExchange ? " nav-flash-unacked-exchange" : ""}`}
            onClick={() => setActiveTab("analytics")}
          >
            ANALYTICS
          </button>
          <button className={`tn${activeTab === "settings" ? " active" : ""}`} onClick={() => setActiveTab("settings")}>SETTINGS</button>
          <button
            className={`tn${activeTab === "logs" ? " active" : ""}${flashLogsForExchange ? " nav-flash-unacked-exchange" : ""}`}
            onClick={() => setActiveTab("logs")}
          >
            LOGS
          </button>
        </nav>
        <div className="status-row">
          <div className="mode-toggle">
            <button
              className={`mt-side ${snapshot?.mode === "paper" ? "active paper" : ""}`}
              onClick={() => { if (snapshot?.mode !== "paper") send({ action: "set_mode", mode: "paper" }); }}
            >PAPER</button>
            <button
              className={`mt-side ${snapshot?.mode === "live" ? "active live" : ""}`}
              onClick={() => { if (snapshot?.mode !== "live") send({ action: "set_mode", mode: "live" }); }}
            >LIVE</button>
          </div>
          {snapshot?.risk_halted && (
            <button className="status-chip halted" onClick={() => document.getElementById("risk-panel")?.scrollIntoView({ behavior: "smooth", block: "center" })}>
              RISK HALTED
            </button>
          )}
        </div>
      </header>

      <FlightDeck
        snapshot={snapshot}
        scalp={scalpForSettings}
        connected={connected}
        onOpenLogs={openLogs}
      />

      <div className="body">
        {/* ── SIDEBAR (Arceus icon nav) ── */}
        <aside className="sidebar">
          <button className={`si${activeTab === "terminal" ? " active" : ""}`} onClick={() => setActiveTab("terminal")}>
            <span className="material-symbols-outlined" style={activeTab === "terminal" ? { fontVariationSettings: "'FILL' 1" } : undefined}>terminal</span>
            <span>TERMINAL</span>
          </button>
          <button
            className={`si${activeTab === "analytics" ? " active" : ""}${flashAnalyticsForExchange ? " nav-flash-unacked-exchange" : ""}`}
            onClick={() => setActiveTab("analytics")}
          >
            <span className="material-symbols-outlined" style={activeTab === "analytics" ? { fontVariationSettings: "'FILL' 1" } : undefined}>pie_chart</span>
            <span>ANALYTICS</span>
          </button>
          <button className={`si${activeTab === "settings" ? " active" : ""}`} onClick={() => setActiveTab("settings")}>
            <span className="material-symbols-outlined" style={activeTab === "settings" ? { fontVariationSettings: "'FILL' 1" } : undefined}>settings</span>
            <span>SETTINGS</span>
          </button>
          <button
            className={`si${activeTab === "logs" ? " active" : ""}${flashLogsForExchange ? " nav-flash-unacked-exchange" : ""}`}
            onClick={() => setActiveTab("logs")}
          >
            <span className="material-symbols-outlined" style={activeTab === "logs" ? { fontVariationSettings: "'FILL' 1" } : undefined}>article</span>
            <span>LOGS</span>
          </button>
        </aside>

        {/* ── MAIN CONTENT ── */}
        <main className="main">
          {activeTab === "logs" ? (
            <ErrorBoundary>
              <LogsTab entries={logEntries} focus={logFocus} onFocusConsumed={clearLogFocus} />
            </ErrorBoundary>
          ) : activeTab === "analytics" ? (
            <ErrorBoundary>
              <AnalyticsTab snapshot={snapshot} send={send} />
            </ErrorBoundary>
          ) : activeTab === "settings" ? (
            <ErrorBoundary>
              <SettingsTab
                scalp={scalpForSettings}
                send={send}
                connected={connected}
                snapshot={snapshot}
                focusPairKey={selectedPair}
              />
            </ErrorBoundary>
          ) : (
          <ErrorBoundary>
          <>
          {/* KPIs */}
          <section className="kpi-section">
            <div className="kpi-row">
              <div className="kpi">
                <div className="kl">SESSION PNL</div>
                <div className={`kv ${sessionPnl >= 0 ? "accent" : "danger"}`}>{sessionPnl >= 0 ? "+" : ""}{fmt(sessionPnl, 2)} USD</div>
              </div>
              <div className="kpi">
                <div className="kl">LIFETIME PNL</div>
                <div className={`kv ${(snapshot?.total_pnl ?? 0) >= 0 ? "accent" : "danger"}`}>{(snapshot?.total_pnl ?? 0) >= 0 ? "+" : ""}{fmt(snapshot?.total_pnl, 2)} USD</div>
              </div>
              <div className="kpi"><div className="kl">DAILY TRADES</div><div className="kv">{fmt(snapshot?.total_trades, 0)} OPS</div></div>
              <div className="kpi"><div className="kl">WIN RATE</div><div className="kv">{fmt(snapshot?.win_rate, 1)}% OPTIMAL</div></div>
              <div className="kpi"><div className="kl">RISK EXPOSURE</div><div className={snapshot?.risk_halted ? "kv danger" : "kv"}>{snapshot?.risk_halted ? "HALTED" : "LOW/MED"}</div></div>
            </div>
            <button
              type="button"
              className="reset-pnl-btn"
              title="Resets session counters and curve only. Lifetime P&L is never cleared."
              onClick={() => {
                if (window.confirm("Reset session P&L view (counters / curve)? Lifetime total is unchanged.")) {
                  send({ action: "reset_pnl", scope: "session" });
                }
              }}
            >
              RESET SESSION
            </button>
          </section>

          {/* MAIN GRID: Chart + Right Column */}
          <section className="grid-main">
            {/* LEFT: Chart + Analytics + Fills */}
            <div className="left-col">
              {/* CHART */}
              <div className="panel chart-panel">
                <div className="panel-hdr chart-panel-hdr">
                  <div className="chart-title-stack">
                    <span
                      className="ph-title"
                      title={
                        isScalpTerminal
                          ? "CDE nano perpetual product_id from [scalp.pairs.*].symbol (date in the code is venue naming, not a quarterly future leg)"
                          : undefined
                      }
                    >
                      {displaySymbol || "---"}
                    </span>
                    {isScalpTerminal && selectedPair ? (
                      <span className="chart-feed-caption">
                        CDE perpetual — chart + L2 + orders use this product_id (long ids like{" "}
                        <code className="chart-pair-key">*-20DEC30-CDE</code> are perps here, not classic dated futures).
                        Config key <code className="chart-pair-key">{selectedPair}</code>
                        {candlePack?.interval != null ? ` · ${candlePack.interval}m candles` : ""}
                      </span>
                    ) : null}
                  </div>
                  <div className="pair-tabs">
                    {terminalPairKeys.map((k) => {
                      const trading = tradingPairs.includes(k);
                      return (
                        <button key={k} className={`ptab${k === selectedPair ? " active" : ""}${trading ? " trading" : ""}`}
                          onClick={() => { setActivePair(k); send({ action: "set_active_pair", pair_key: k }); }}>
                          {trading && <span className="ptab-dot" />}
                          {tabLabel(k)}
                        </button>
                      );
                    })}
                  </div>
                  <span
                    className="spread-tag"
                    title={isScalpTerminal ? "Mid from Coinbase CDE perp L2 for selected product_id" : undefined}
                  >
                    {isScalpTerminal ? "CDE PERP " : ""}MID {fmt(spreadAbs, dec)} ({spreadBps} bps)
                  </span>
                </div>
                <div className={hasScalpChart ? "chart-area chart-area-live" : "chart-area chart-area-placeholder"}>
                  {hasScalpChart && selectedPair ? (
                    <ScalpTerminalChart
                      pairKey={selectedPair}
                      closed={candlePack?.closed}
                      live={candlePack?.live ?? null}
                      indicatorOverlay={candlePack?.indicator_overlay}
                      height={300}
                      priceDecimals={dec}
                      cdeOpenOrders={isScalpTerminal ? coinbaseRestingForTab : []}
                      openPositions={positionsForChart}
                      tradeHistory={tradesForChart}
                      strategyBanner={terminalStrategyBanner}
                    />
                  ) : (
                    <p className="chart-placeholder-text">
                      {snapshot?.scalp?.enabled === false
                        ? "Scalp is off — enable in Settings. Charts and order book use live feed data when connected."
                        : !connected
                          ? "WebSocket offline — charts and book need a backend connection."
                          : "Waiting for candle / book data (warmup or feed). Use Settings for scalp / WFO; LOGS for alerts."}
                    </p>
                  )}
                </div>
                <div className="chart-footer">
                  <span>BID {fmt(topBid ?? ps?.best_bid, dec)}</span>
                  <span>ASK {fmt(topAsk ?? ps?.best_ask, dec)}</span>
                  <span>MID {fmt(midFromBook ?? ps?.mid_price, dec)}</span>
                  <span>THREAT {(ps?.threat_level ?? "calm").toUpperCase()}</span>
                </div>
              </div>

              {/* ORDERS & FILLS */}
              <div className="panel orders-fills-panel">
                {/* CDE resting first — Coinbase scalp venue */}
                {isScalpTerminal ? (
                  <>
                    <button
                      type="button"
                      className="of-section-hdr"
                      onClick={() => setPendingOrdersOpen((o) => !o)}
                    >
                      <span>CDE_RESTING ({pendingOrdersHdr})</span>
                      <span className="of-chevron">{pendingOrdersOpen ? "▲" : "▼"}</span>
                    </button>
                    {pendingOrdersOpen && (
                      <div className="of-body">
                        <div className="of-subhdr">
                          Coinbase CDE perp resting orders for this product — limits, take-profit-limit, stop-loss-limit
                          (same product_id as chart; venue reconcile snapshot).
                        </div>
                        <div className="orders-head orders-head-coinbase">
                          <span>SIDE</span>
                          <span>PRODUCT</span>
                          <span>TYPE</span>
                          <span>LIMIT / TRIG</span>
                          <span>STATUS</span>
                          <span>FILLED</span>
                        </div>
                        {coinbaseRestingForTab.map((o) => {
                          const lp = o.limit_price ?? 0;
                          const tr = o.trigger_price ?? 0;
                          const px = lp > 0 ? lp : tr;
                          const pxLabel =
                            lp > 0 && tr > 0
                              ? `${fmt(lp, dec)} / ${fmt(tr, dec)}`
                              : px > 0
                                ? fmt(px, dec)
                                : "—";
                          return (
                            <div key={o.order_id || o.client_order_id} className="order-row-full order-row-coinbase">
                              <span className={String(o.side).toLowerCase() === "buy" ? "c-buy" : "c-sell"}>
                                {String(o.side).toUpperCase()}
                              </span>
                              <span style={{ fontSize: 10 }}>{o.product_id}</span>
                              <span style={{ fontSize: 10 }}>{o.order_type}</span>
                              <span style={{ fontSize: 10 }} className="tabular-nums">
                                {pxLabel}
                              </span>
                              <span style={{ fontSize: 10 }}>{o.status}</span>
                              <span>{fmt(o.filled_base, 4)}</span>
                            </div>
                          );
                        })}
                        {coinbaseRestingForTab.length === 0 ? (
                          <div className="no-data">
                            No resting CDE orders for this product (reconcile snapshot)
                            {(snapshot?.scalp?.exchange_open_orders_all?.length ?? 0) > 0 ? (
                              <span style={{ display: "block", marginTop: 6, color: "var(--text-muted)" }}>
                                This tab only lists orders for{" "}
                                <span className="mono">{cdeProductId ?? "—"}</span>. The venue reports{" "}
                                {(snapshot?.scalp?.exchange_open_orders_all ?? []).length} open order(s) on this
                                API key — none use that product id.
                                {venueOpenOrderProductCounts.length > 0 ? (
                                  <>
                                    {" "}
                                    Open elsewhere:{" "}
                                    <span className="mono">
                                      {venueOpenOrderProductCounts.map(([pid, n]) => `${pid}×${n}`).join(" · ")}
                                    </span>
                                    .
                                  </>
                                ) : null}{" "}
                                If another row is your scalp contract, switch that pair tab (multi-pair) or align{" "}
                                <span className="mono">[scalp.pairs.*].symbol</span> with the exact Coinbase product id.
                                {(snapshot?.scalp?.exchange_open_orders_outside_pairs?.length ?? 0) > 0 ? (
                                  <>
                                    {" "}
                                    <span className="mono">
                                      {snapshot?.scalp?.exchange_open_orders_outside_pairs?.length ?? 0}
                                    </span>{" "}
                                    order(s) are outside every configured{" "}
                                    <span className="mono">[scalp.pairs.*].symbol</span> — cancel on Coinbase or add
                                    the pair.
                                  </>
                                ) : null}
                              </span>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                    )}
                  </>
                ) : null}

                {/* RECENT FILLS — collapsible */}
                <button type="button" className="of-section-hdr" onClick={() => setFillsOpen(o => !o)}>
                  <span>RECENT_FILLS ({fills.length})</span>
                  <div className="of-hdr-right">
                    <span className="live-dot">LIVE STREAM</span>
                    <span className="of-chevron">{fillsOpen ? "▲" : "▼"}</span>
                  </div>
                </button>
                {fillsOpen && (
                  <div className="of-body">
                    <div className="fills-head">
                      <span>TIMESTAMP</span><span>TYPE</span><span>PAIR</span><span>PRICE</span><span>SIZE</span><span>P&L</span>
                    </div>
                    {fills.map((f, i) => {
                      const pnl = f.pnl_delta;
                      const pnlStr = f.side === "buy"
                        ? "ENTRY"
                        : (pnl >= 0 ? `+${fmt(pnl, 4)}` : fmt(pnl, 4));
                      const pnlClass = f.side === "buy" ? "c-entry" : pnl >= 0 ? "c-buy" : "c-sell";
                      return (
                        <div key={i} className="fill-row">
                          <span className="fill-ts">{ts(f.timestamp)}</span>
                          <span className={f.side === "buy" ? "c-buy" : "c-sell"}>{f.side.toUpperCase()}</span>
                          <span>{f.pair_key}</span>
                          <span>{fmt(f.price, priceDec(f.pair_key))}</span>
                          <span>{fmt(f.qty, 4)}</span>
                          <span className={pnlClass}>{pnlStr}</span>
                        </div>
                      );
                    })}
                    {fills.length === 0 && (
                      <div className="no-data">
                        {isScalpTerminal
                          ? "No closed scalp fills for this tab yet (round-trips from trade_history; open entries appear on the chart / positions)."
                          : "No fills yet"}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* RIGHT COLUMN — market + shared tools */}
            <div className="right-col">
              <div className="panel" style={{ padding: "10px 12px", fontSize: 10, color: "var(--text-muted)", lineHeight: 1.45 }}>
                <span className="ph-title" style={{ display: "block", marginBottom: 6 }}>TERMINAL</span>
                Operator / scalp controls live in <strong style={{ color: "var(--text-secondary)" }}>Settings</strong> and the flight deck above. This column shows depth, per-tab <strong style={{ color: "var(--text-secondary)" }}>brain</strong> (strategy / WFO / activity), and risk summary.
              </div>

              <div className="panel ctrl-panel brain-panel-wrap">
                <TerminalBrainPanel
                  selectedPair={selectedPair}
                  cdeProductId={cdeProductId}
                  displaySymbol={displaySymbol}
                  isScalpTerminal={isScalpTerminal}
                  scalp={snapshot?.scalp}
                  threatLevel={ps?.threat_level ?? undefined}
                  riskHalted={snapshot?.risk_halted}
                  riskHaltReason={snapshot?.risk_halt_reason ?? undefined}
                  onSend={send}
                />
              </div>

              {/* ORDER BOOK DEPTH */}
              <div className="panel book-panel">
                <div className="ph-title">ORDER_BOOK <span className="material-symbols-outlined" style={{ fontSize: 12, opacity: 0.4, verticalAlign: "middle" }}>info</span></div>
                <div className="book-head">
                  <span>SIZE</span><span>PRICE</span><span>PRICE</span><span>SIZE</span>
                </div>
                <div className="book-body">
                  {Array.from({ length: Math.max(bidLevels.length, askLevels.length, 1) }).map((_, i) => {
                    const bid = bidLevels[i];
                    const ask = askLevels[i];
                    return (
                      <div key={i} className="book-row">
                        <span className="bk-vol">{bid ? fmt(bid.volume, 2) : ""}</span>
                        <span className="bk-bid-bar">
                          {bid && <span className="bar bid-bar" style={{ width: `${(bid.volume / maxBookVol) * 100}%` }} />}
                          <span className="bk-price c-buy">{bid ? fmt(bid.price, dec) : ""}</span>
                        </span>
                        <span className="bk-ask-bar">
                          <span className="bk-price c-sell">{ask ? fmt(ask.price, dec) : ""}</span>
                          {ask && <span className="bar ask-bar" style={{ width: `${(ask.volume / maxBookVol) * 100}%` }} />}
                        </span>
                        <span className="bk-vol">{ask ? fmt(ask.volume, 2) : ""}</span>
                      </div>
                    );
                  })}
                </div>
                <div className="book-spread">SPREAD: {fmt(spreadAbs, dec)} ({spreadBps} bps)</div>
              </div>

              <div id="risk-panel" className="panel risk-panel">
                <div className="ph-title">RISK (SESSION)</div>
                <div className="risk-row">
                  <span className="rl">PNL FLOOR</span>
                  <span className="rv">{config?.min_total_pnl_usd ?? "off"}</span>
                </div>
                <div className="risk-row">
                  <span className="rl">DAILY LOSS LIMIT</span>
                  <span className="rv">{config?.daily_loss_limit_usd ?? "off"}</span>
                </div>
                <div className="risk-row">
                  <span className="rl">MAX DRAWDOWN</span>
                  <span className="rv">{config?.max_drawdown_pct ?? "off"}%</span>
                </div>
                {snapshot?.risk_halted ? (
                  <div className="halt-banner">RISK HALTED: {snapshot.risk_halt_reason}</div>
                ) : (
                  <div className="risk-row">
                    <span className="rl">STATUS</span>
                    <span className="rv">OK</span>
                  </div>
                )}
              </div>

              {/* SYSTEMS PANEL */}
              <SystemsPanel config={config} send={send} selectedPair={selectedPair} scalp={snapshot?.scalp} />
            </div>
          </section>
          </>
          </ErrorBoundary>
          )}
        </main>
      </div>

      <footer className="bot-footer">
        <div className="footer-metrics">
          <span className="footer-snap-age" title={snapshotAgeTitle}>
            SNAPSHOT AGE: {snapshotAgeSec != null ? `${snapshotAgeSec}s` : "--"}
          </span>
          <span>UPTIME: {snapshot?.session_start_ts ? `${Math.floor((wallClockSec - snapshot.session_start_ts) / 3600)}H` : "--"}</span>
        </div>
        <div className="footer-right">
          <span className="footer-version">V4.0.0-ARCEUS</span>
        </div>
      </footer>
    </div>
  );
}

export default App;
