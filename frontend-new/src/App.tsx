import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { WsClient } from "./lib/wsClient";
import type { ActiveOrder, Alert, BookLevel, ConfigSnapshot, Fill, PairSnapshot, Snapshot } from "./lib/types";
import { SystemsPanel } from "./components/SystemsPanel";
import "./styles/app.css";

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

function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [config, setConfig] = useState<ConfigSnapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const [activePair, setActivePair] = useState("");
  const [fillsOpen, setFillsOpen] = useState(true);
  const [ordersOpen, setOrdersOpen] = useState(true);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const pairInitRef = useRef(false);
  const ws = useMemo(() => WsClient.shared, []);

  const MAX_ALERTS = 8;
  const ALERT_TTL_MS: Record<string, number> = { error: 15000, warning: 10000, info: 6000, success: 5000 };
  const dismissAlert = useCallback((id: string) => setAlerts(prev => prev.filter(a => a.id !== id)), []);

  useEffect(() => {
    ws.setHandlers({
      onConnection: setConnected,
      onSnapshot: (next) => {
        setSnapshot(next);
        if (!pairInitRef.current && next.active_pair_key) {
          pairInitRef.current = true;
          setActivePair(next.active_pair_key);
        }
      },
      onConfig: setConfig,
      onAlert: (alert) => {
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
  }, [ws, dismissAlert]);

  const send = (payload: Record<string, unknown>) => ws.send(payload);
  const pairs = snapshot?.pairs ?? {};
  const pairKeys = Object.keys(pairs);
  const selectedPair = activePair || snapshot?.active_pair_key || pairKeys[0] || "";
  const ps: PairSnapshot | undefined = pairs[selectedPair];
  const pc = config?.pairs?.[selectedPair];
  const tradingPairs = config?.pair_keys_for_trading ?? pairKeys;
  const fills: Fill[] = (snapshot?.recent_fills ?? []).slice(-12).reverse();
  const orders: ActiveOrder[] = snapshot?.active_orders ?? [];
  const dec = priceDec(ps?.symbol ?? "");

  const sessionPnl = (snapshot?.total_pnl ?? 0) - (snapshot?.session_start_pnl ?? 0);
  const sellFills = (snapshot?.recent_fills ?? []).filter(f => f.side === "sell");
  const recentWins = sellFills.filter(f => f.pnl_delta > 0).length;
  const recentTotal = sellFills.length;
  const profitFactor = (() => {
    const wins = sellFills.filter(f => f.pnl_delta > 0).reduce((s, f) => s + f.pnl_delta, 0);
    const losses = Math.abs(sellFills.filter(f => f.pnl_delta < 0).reduce((s, f) => s + f.pnl_delta, 0));
    return losses > 0 ? wins / losses : wins > 0 ? 99.9 : 0;
  })();
  const maxDD = snapshot?.peak_pnl
    ? Math.max(0, (snapshot.peak_pnl - snapshot.total_pnl) / Math.max(Math.abs(snapshot.peak_pnl), 0.01) * 100)
    : 0;

  const bidLevels: BookLevel[] = (ps?.bid_levels ?? []).slice(0, 5);
  const askLevels: BookLevel[] = (ps?.ask_levels ?? []).slice(0, 5);
  const allBookVols = [...bidLevels.map(l => l.volume), ...askLevels.map(l => l.volume)];
  const maxBookVol = allBookVols.length > 0 ? Math.max(...allBookVols) : 0.001;
  const spreadBps = ps?.mid_price
    ? (ps.spread / ps.mid_price * 10000).toFixed(2)
    : "0";

  const alertIcon = (level: string) => {
    if (level === "error") return "✕";
    if (level === "warning") return "⚠";
    if (level === "success") return "✓";
    return "ℹ";
  };

  return (
    <div className="shell">
      {/* ── TOAST ALERTS ── */}
      {alerts.length > 0 && (
        <div className="toast-container">
          {alerts.map(a => (
            <div key={a.id} className={`toast toast-${a.level}`}>
              <span className="toast-icon">{alertIcon(a.level)}</span>
              <div className="toast-body">
                <div className="toast-title">{a.title}</div>
                {a.detail && <div className="toast-detail">{a.detail}</div>}
                {a.source && <div className="toast-source">{a.source}</div>}
              </div>
              <button className="toast-close" onClick={() => dismissAlert(a.id)}>✕</button>
            </div>
          ))}
        </div>
      )}

      {/* ── TOP BAR ── */}
      <header className="topbar">
        <div className="brand">MITCH TRADINGBOT // BRUTALIST_OBSERVATORY</div>
        <nav className="top-nav">
          <button className="tn active">TERMINAL</button>
          <button className="tn">ANALYTICS</button>
          <button className="tn">EXECUTIONS</button>
        </nav>
        <div className="status-row">
          {!connected ? (
            <span className="status-chip offline">OFFLINE</span>
          ) : snapshot?.mode === "paper" ? (
            <span className="status-chip paper">PAPER TEST</span>
          ) : snapshot?.running ? (
            <span className="status-chip live">LIVE</span>
          ) : (
            <span className="status-chip paused">LIVE — PAUSED</span>
          )}
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

      <div className="body">
        {/* ── SIDEBAR ── */}
        <aside className="sidebar">
          <div className="agent-box">STRAT_ALPHA<br /><small>V2.0.4 ACTIVE</small></div>
          <button className="si active">DASHBOARD</button>
          <button className="si">PORTFOLIO</button>
          <button className="si">BOT_CONFIG</button>
          <button className="si">SENSORS</button>
          <button className="si">LOGS</button>
          <button className="deploy-btn">DEPLOY_NODE</button>
        </aside>

        {/* ── MAIN CONTENT ── */}
        <main className="main">
          {/* KPIs */}
          <section className="kpi-section">
            <div className="kpi-row">
              <div className="kpi">
                <div className="kl">SESSION PNL</div>
                <div className={`kv ${sessionPnl >= 0 ? "accent" : "danger"}`}>{sessionPnl >= 0 ? "+" : ""}{fmt(sessionPnl, 4)} USDT</div>
              </div>
              <div className="kpi">
                <div className="kl">LIFETIME PNL</div>
                <div className={`kv ${(snapshot?.total_pnl ?? 0) >= 0 ? "accent" : "danger"}`}>{(snapshot?.total_pnl ?? 0) >= 0 ? "+" : ""}{fmt(snapshot?.total_pnl, 4)} USDT</div>
              </div>
              <div className="kpi"><div className="kl">DAILY TRADES</div><div className="kv">{fmt(snapshot?.total_trades, 0)} OPS</div></div>
              <div className="kpi"><div className="kl">WIN RATE</div><div className="kv">{fmt(snapshot?.win_rate, 1)}% OPTIMAL</div></div>
              <div className="kpi"><div className="kl">RISK EXPOSURE</div><div className={snapshot?.risk_halted ? "kv danger" : "kv"}>{snapshot?.risk_halted ? "HALTED" : "LOW/MED"}</div></div>
            </div>
            <button type="button" className="reset-pnl-btn" onClick={(e) => {
              if (e.shiftKey) {
                if (confirm("Reset ALL stats (lifetime + session) to zero?")) send({ action: "reset_pnl", scope: "all" });
              } else {
                send({ action: "reset_pnl", scope: "session" });
              }
            }}>RESET P&L</button>
          </section>

          {/* MAIN GRID: Chart + Right Column */}
          <section className="grid-main">
            {/* LEFT: Chart + Analytics + Fills */}
            <div className="left-col">
              {/* CHART */}
              <div className="panel chart-panel">
                <div className="panel-hdr">
                  <span className="ph-title">LIVE_MARKET_FLUX</span>
                  <div className="pair-tabs">
                    {pairKeys.map(k => {
                      const trading = tradingPairs.includes(k);
                      return (
                        <button key={k} className={`ptab${k === selectedPair ? " active" : ""}${trading ? " trading" : ""}`}
                          onClick={() => { setActivePair(k); send({ action: "set_active_pair", pair_key: k }); }}>
                          {trading && <span className="ptab-dot" />}
                          {pairs[k]?.symbol ?? k}
                        </button>
                      );
                    })}
                  </div>
                  <span className="spread-tag">SPREAD: {fmt(ps?.spread, dec)} ({spreadBps} bps)</span>
                </div>
                <div className="chart-area">
                  <div className="chart-glow" />
                </div>
                <div className="chart-footer">
                  <span>BID {fmt(ps?.best_bid, dec)}</span>
                  <span>ASK {fmt(ps?.best_ask, dec)}</span>
                  <span>MID {fmt(ps?.mid_price, dec)}</span>
                  <span>THREAT {(ps?.threat_level ?? "calm").toUpperCase()}</span>
                </div>
              </div>

              {/* ALPHA ANALYTICS */}
              <div className="panel analytics-panel">
                <div className="panel-hdr">
                  <span className="ph-title">ALPHA_ANALYTICS</span>
                  <span className="strat-mode">STRAT_MODE: {config?.learner_enabled ? "ADAPTIVE" : "FIXED"}</span>
                </div>
                <div className="analytics-grid">
                  <div className="an-card">
                    <div className="an-label">EXPECTED VALUE (EV)</div>
                    <div className="an-val accent">{sellFills.length > 0 ? `+${(sessionPnl / Math.max(recentTotal, 1)).toFixed(4)}` : "---"}</div>
                    <div className="an-sub">PER EXECUTION</div>
                  </div>
                  <div className="an-card">
                    <div className="an-label">PROFIT FACTOR</div>
                    <div className="an-val">{profitFactor.toFixed(2)}</div>
                    <div className="an-sub">GROSS PROFIT / LOSS</div>
                  </div>
                  <div className="an-card">
                    <div className="an-label">WIN / TOTAL</div>
                    <div className="an-val">{recentWins} / {recentTotal}</div>
                    <div className="an-sub">SELL LEGS</div>
                  </div>
                  <div className="an-card">
                    <div className="an-label">MAX DRAWDOWN</div>
                    <div className="an-val danger">{maxDD.toFixed(2)}%</div>
                    <div className="an-sub">TRAILING PEAK</div>
                  </div>
                </div>
              </div>

              {/* ORDERS & FILLS */}
              <div className="panel orders-fills-panel">
                {/* ACTIVE ORDERS — collapsible */}
                <button type="button" className="of-section-hdr" onClick={() => setOrdersOpen(o => !o)}>
                  <span>ACTIVE_ORDERS ({orders.length})</span>
                  <span className="of-chevron">{ordersOpen ? "▲" : "▼"}</span>
                </button>
                {ordersOpen && (
                  <div className="of-body">
                    {(snapshot?.order_reject_count ?? 0) > 0 && snapshot?.last_order_reject_reason && (
                      <div className="reject-banner">
                        ORDER REJECTED ({snapshot.order_reject_count}x): {snapshot.last_order_reject_reason}
                      </div>
                    )}
                    <div className="orders-head">
                      <span>TYPE</span><span>PAIR</span><span>PRICE</span><span>SIZE</span><span>PROGRESS</span>
                    </div>
                    {orders.map(o => {
                      const pct = o.qty > 0 ? (o.filled_qty / o.qty * 100) : 0;
                      const d = priceDec(o.pair_key);
                      return (
                        <div key={o.cl_ord_id} className="order-row-full">
                          <span className={o.side === "buy" ? "c-buy" : "c-sell"}>{o.side.toUpperCase()}</span>
                          <span>{o.pair_key}</span>
                          <span>{fmt(o.price, d)}</span>
                          <span>{fmt(o.qty, 4)}</span>
                          <span className="order-progress">
                            <span className="prog-bar"><span className={`prog-fill ${o.side}`} style={{ width: `${pct}%` }} /></span>
                            <span className="prog-lbl">{fmt(o.filled_qty, 4)}/{fmt(o.qty, 4)}</span>
                          </span>
                        </div>
                      );
                    })}
                    {orders.length === 0 && !(snapshot?.order_reject_count) && (
                      <div className="no-data">No active orders</div>
                    )}
                  </div>
                )}

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
                    {fills.length === 0 && <div className="no-data">No fills yet</div>}
                  </div>
                )}
              </div>
            </div>

            {/* RIGHT COLUMN */}
            <div className="right-col">
              {/* OPERATOR CONTROLS */}
              <div className="panel ctrl-panel">
                <div className="ph-title">OPERATOR_CONTROLS</div>
                <div className="ctrl-btns">
                  <button type="button" className="big-btn start" onClick={() => send({ action: "start" })}>
                    <svg viewBox="0 0 24 24" width="20" height="20"><polygon points="8,5 19,12 8,19" fill="currentColor" /></svg>
                    START BOT
                  </button>
                  <button type="button" className="big-btn stop" onClick={() => send({ action: "stop" })}>
                    <svg viewBox="0 0 24 24" width="20" height="20"><rect x="6" y="6" width="12" height="12" fill="currentColor" /></svg>
                    STOP BOT
                  </button>
                </div>
                <button type="button" className="kill-btn" onClick={() => send({ action: "kill" })}>EMERGENCY_KILL_SWITCH</button>
                <div className="ctrl-row-btns">
                  <button type="button" className="ghost-btn" onClick={() => send({ action: "update_risk", resume_risk_halt: true })}>RESUME</button>
                  <button type="button" className="ghost-btn" onClick={() => send({ action: "toggle_pair", pair_key: selectedPair, enabled: !tradingPairs.includes(selectedPair) })}>
                    {tradingPairs.includes(selectedPair) ? "DISABLE PAIR" : "ENABLE PAIR"}
                  </button>
                  <button type="button" className="ghost-btn" onClick={() => send({ action: "soft_restart" })}>SOFT RESTART</button>
                  <button type="button" className="ghost-btn" style={{color:"#f87171"}} onClick={() => { if (confirm("Restart the bot process? Config will reload from disk.")) send({ action: "restart_process" }); }}>RESTART PROCESS</button>
                </div>
              </div>

              {/* ORDER BOOK DEPTH */}
              <div className="panel book-panel">
                <div className="ph-title">ORDER_BOOK_DEPTH</div>
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
                <div className="book-spread">SPREAD: {fmt(ps?.spread, dec)} ({spreadBps} bps)</div>
              </div>

              {/* RISK ENGINE CONFIG */}
              <div id="risk-panel" className="panel risk-panel">
                <div className="ph-title">RISK_ENGINE_CONFIG</div>
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
                <div className="risk-row">
                  <span className="rl">ORDER SIZE</span>
                  <span className="rv">{pc?.order_size ?? "-"}</span>
                </div>
                <div className="risk-row">
                  <span className="rl">SPREAD</span>
                  <span className="rv">{pc?.spread_bps ?? "-"} bps</span>
                </div>
                <div className="risk-row">
                  <span className="rl">FLOOR</span>
                  <span className="rv">{pc?.spread_floor_bps ?? "-"} bps</span>
                </div>
                {snapshot?.risk_halted && (
                  <div className="halt-banner">RISK HALTED: {snapshot.risk_halt_reason}</div>
                )}
              </div>

              {/* SYSTEMS PANEL */}
              <SystemsPanel config={config} send={send} selectedPair={selectedPair} />
            </div>
          </section>
        </main>
      </div>

      <footer className="bot-footer">
        MITCH_LABS // BRUTALIST_OBSERVATORY &copy; 2026
      </footer>
    </div>
  );
}

export default App;
