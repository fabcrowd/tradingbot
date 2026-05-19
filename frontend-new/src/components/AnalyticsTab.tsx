import { Fragment, useMemo, useState } from "react";
import type {
  ExchangeErrorEvent,
  ScalpTrade,
  Snapshot,
  StrategyLookbackModeRow,
  WfoLastPass,
  WfoModeScoreboardRow,
} from "../lib/types";

const STRATEGY_ORDER = [
  "daviddtech_scalp",
  "ema_momentum",
  "ema_scalp",
  "macd_scalp",
  "rsi_reversion",
  "supertrend",
  "squeeze_momentum",
  "qqe_mod",
  "utbot_alert",
  "hull_suite",
  "sar_chop",
] as const;

function fmt(v: number, d = 2): string {
  return Number(v ?? 0).toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

function modeLabel(m: string): string {
  return m.replace(/_/g, " ");
}

function fmtExTime(epoch: number): string {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString(undefined, {
    dateStyle: "short",
    timeStyle: "medium",
  });
}

/** Map per-mode eval score into 0–100 for a horizontal bar within one pair's scoreboard. */
function wfoMeanScoreBarPct(score: number, vmin: number, vmax: number): number {
  if (!Number.isFinite(score)) return 0;
  if (!Number.isFinite(vmin) || !Number.isFinite(vmax) || vmax <= vmin) {
    return score >= 0 ? 100 : 0;
  }
  return Math.max(0, Math.min(100, ((score - vmin) / (vmax - vmin)) * 100));
}

function WfoLastPassScoreboardSection({ lastPass }: { lastPass: WfoLastPass | null | undefined }) {
  const pairsWithBoard = useMemo(() => {
    if (!lastPass?.pairs?.length) return [];
    return lastPass.pairs.filter((p) => (p.wfo_mode_scoreboard?.length ?? 0) > 0);
  }, [lastPass]);

  if (!lastPass) {
    return (
      <section className="panel" style={{ marginTop: 12 }}>
        <div className="panel-hdr">
          <span className="ph-title">WFO_MODE_SCOREBOARD</span>
          <span className="strat-mode">last continuous WFO pass · per strategy mode</span>
        </div>
        <div className="no-data" style={{ padding: 20 }}>
          WFO snapshot not loaded (enable scalp / wait for first pass).
        </div>
      </section>
    );
  }

  const objective = lastPass.objective ?? "objective";

  if (pairsWithBoard.length === 0) {
    return (
      <section className="panel" style={{ marginTop: 12 }}>
        <div className="panel-hdr">
          <span className="ph-title">WFO_MODE_SCOREBOARD</span>
          <span className="strat-mode">
            last pass {fmtExTime(lastPass.ts)} · objective <span className="mono">{objective}</span>
          </span>
        </div>
        <div className="no-data" style={{ padding: 20 }}>
          No per-mode scoreboard for this pass (insufficient bar span or no grid rows met min trades). After the next
          WFO run completes, eval-window scores per strategy appear here.
        </div>
      </section>
    );
  }

  return (
    <section className="panel" style={{ marginTop: 12 }}>
      <div className="panel-hdr">
        <span className="ph-title">WFO_MODE_SCOREBOARD</span>
        <span className="strat-mode">
          last pass {fmtExTime(lastPass.ts)} · bars = eval score <span className="mono">{objective}</span> (best row
          per mode, continuous window)
        </span>
      </div>
      <div className="analytics-table-wrap" style={{ padding: "0 12px 12px" }}>
        {pairsWithBoard.map((p) => {
          const rows = (p.wfo_mode_scoreboard ?? []) as WfoModeScoreboardRow[];
          const scores = rows.map((r) => r.mean_holdout_score ?? 0);
          const vmin = Math.min(...scores);
          const vmax = Math.max(...scores);
          return (
            <div key={p.pair_key} style={{ marginBottom: 18 }}>
              <div className="analytics-subhdr mono" style={{ marginBottom: 8 }}>
                {p.pair_key}
                <span style={{ color: "var(--text-muted)", fontWeight: 400, marginLeft: 8, fontSize: 10 }}>
                  outcome {p.outcome}
                  {p.skip_reason ? ` · skip ${p.skip_reason}` : ""}
                  {p.gate_reason ? ` · gate ${p.gate_reason}` : ""}
                </span>
              </div>
              <table className="analytics-table">
                <thead>
                  <tr>
                    <th style={{ width: 22 }} />
                    <th>MODE</th>
                    <th>POOL</th>
                    <th>EVAL</th>
                    <th>MEAN SCORE</th>
                    <th style={{ minWidth: 120 }}>VS PASS</th>
                    <th>MEAN $ PnL</th>
                    <th style={{ fontSize: 10 }}>DD%</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const sc = row.mean_holdout_score ?? 0;
                    const pct = wfoMeanScoreBarPct(sc, vmin, vmax);
                    const champ = row.is_wfo_champion_mode;
                    const q = row.qualified_champion_pool;
                    const barBg = champ ? "var(--accent, #22c55e)" : q ? "#60a5fa" : "#6b7280";
                    return (
                      <tr
                        key={`${p.pair_key}-${row.mode}-${row.pi ?? ""}`}
                        style={
                          champ
                            ? { background: "rgba(var(--accent-rgb, 34, 197, 94), 0.09)" }
                            : undefined
                        }
                      >
                        <td style={{ color: champ ? "var(--accent)" : "transparent", fontWeight: 800 }}>{champ ? "★" : ""}</td>
                        <td className="mono" style={champ ? { color: "var(--accent)", fontWeight: 600 } : undefined}>
                          {modeLabel(row.mode)}
                        </td>
                        <td style={{ fontSize: 10, color: q ? "var(--accent)" : "var(--text-muted)" }}>
                          {q ? "qualified" : "out"}
                        </td>
                        <td className="mono" style={{ fontSize: 11 }}>
                          {row.holdout_windows ?? "—"}
                        </td>
                        <td className="mono" style={{ fontSize: 11 }}>
                          {fmt(sc, 4)}
                        </td>
                        <td>
                          <div
                            title={`min ${fmt(vmin, 4)} · max ${fmt(vmax, 4)} in this pair`}
                            style={{
                              height: 8,
                              background: "var(--surface-2, #1f2937)",
                              borderRadius: 4,
                              overflow: "hidden",
                            }}
                          >
                            <div
                              style={{
                                width: `${pct}%`,
                                height: "100%",
                                borderRadius: 4,
                                background: barBg,
                                transition: "width 0.25s ease-out",
                              }}
                            />
                          </div>
                        </td>
                        <td
                          className={(row.mean_holdout_total_pnl ?? 0) >= 0 ? "c-buy" : "c-sell"}
                          style={{ fontSize: 11 }}
                        >
                          {fmt(row.mean_holdout_total_pnl ?? 0, 4)}
                        </td>
                        <td className="mono" style={{ fontSize: 10, color: "var(--text-muted)" }}>
                          {row.mean_max_drawdown_pct != null ? fmt(row.mean_max_drawdown_pct, 1) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        })}
        <p className="analytics-footnote" style={{ marginTop: 4 }}>
          ★ = WFO champion mode for that pair (after tie-breakers). <span className="mono">POOL qualified</span> means
          the row met min trades and ranking gates for that pass. One row per mode shows the strongest grid variant on
          the continuous eval window.
        </p>
      </div>
    </section>
  );
}

function MaxConcurrentPositionsControl({
  capRaw,
  send,
}: {
  capRaw: number;
  send: (payload: Record<string, unknown>) => void;
}) {
  const [draft, setDraft] = useState(String(capRaw));
  const apply = () => {
    const n = parseInt(draft.trim(), 10);
    if (!Number.isFinite(n) || n < 0 || n > 64) return;
    send({ action: "set_scalp_max_concurrent_positions", max_concurrent_positions: n });
  };
  return (
    <div
      className="analytics-max-concurrent"
      style={{
        padding: "10px 12px 12px",
        borderTop: "1px solid var(--border-subtle, rgba(255,255,255,0.08))",
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 10,
        fontSize: 11,
      }}
    >
      <span style={{ color: "var(--text-muted)", letterSpacing: "0.06em", fontWeight: 600 }}>
        MAX CONCURRENT POSITIONS
      </span>
      <input
        type="number"
        min={0}
        max={64}
        step={1}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        className="analytics-num-input"
        title="0 = unlimited (capital, per-pair notional, and margin still apply). Does not edit config.toml."
        style={{
          width: 56,
          padding: "4px 8px",
          borderRadius: 4,
          border: "1px solid var(--border-subtle, #374151)",
          background: "var(--surface-2, #111827)",
          color: "var(--text-primary, #e5e7eb)",
          fontFamily: "inherit",
          fontSize: 12,
        }}
      />
      <button
        type="button"
        onClick={apply}
        style={{
          padding: "4px 12px",
          borderRadius: 4,
          border: "none",
          cursor: "pointer",
          background: "var(--accent, #22c55e)",
          color: "#052e16",
          fontWeight: 700,
          fontSize: 10,
          letterSpacing: "0.08em",
        }}
      >
        APPLY
      </button>
      <span style={{ color: "var(--text-muted)", flex: "1 1 200px", lineHeight: 1.35 }}>
        Runtime only — restarts revert to <span className="mono">config.toml</span>. Range 0–64; 0 removes the
        pair-count ceiling so sizing and balances bound risk.
      </span>
    </div>
  );
}

function localDayStartSec(ymd: string): number | null {
  if (!ymd || !/^\d{4}-\d{2}-\d{2}$/.test(ymd)) return null;
  const [y, m, d] = ymd.split("-").map((x) => parseInt(x, 10));
  if (!Number.isFinite(y) || !Number.isFinite(m) || !Number.isFinite(d)) return null;
  return new Date(y, m - 1, d).getTime() / 1000;
}

function tradeStrategyLabel(t: ScalpTrade): string {
  return t.strategy_mode && t.strategy_mode !== "unknown" ? t.strategy_mode : "legacy_unknown";
}

/** USD notional for strategy-report Size column (matches JSONL ``entry_row`` / ``exit_row``). */
function legNotionalUsd(t: ScalpTrade, leg: "entry" | "exit"): number {
  const qty = t.qty ?? 0;
  if (leg === "entry") {
    if (t.entry_notional_usd != null) return Math.abs(t.entry_notional_usd);
    return Math.abs((t.entry_price ?? 0) * qty);
  }
  if (t.exit_notional_usd != null) return Math.abs(t.exit_notional_usd);
  const ep = t.entry_price ?? 0;
  if (t.entry_notional_usd != null && ep > 0) {
    return Math.abs(t.entry_notional_usd * ((t.exit_price ?? 0) / ep));
  }
  return Math.abs((t.exit_price ?? 0) * qty);
}

function StrategyReportSection({ trades }: { trades: ScalpTrade[] }) {
  const modes = useMemo(() => {
    const s = new Set<string>();
    for (const t of trades) {
      s.add(tradeStrategyLabel(t));
    }
    return ["__all__", ...Array.from(s).sort()];
  }, [trades]);

  const [modeSel, setModeSel] = useState("__all__");
  const [dFrom, setDFrom] = useState("");
  const [dTo, setDTo] = useState("");

  const filtered = useMemo(() => {
    let rows = trades;
    if (modeSel !== "__all__") {
      rows = rows.filter((t) => tradeStrategyLabel(t) === modeSel);
    }
    const ts0 = localDayStartSec(dFrom);
    const ts1 = localDayStartSec(dTo);
    if (ts0 != null) rows = rows.filter((t) => (t.exit_ts ?? 0) >= ts0);
    if (ts1 != null) rows = rows.filter((t) => (t.exit_ts ?? 0) < ts1 + 86400);
    return [...rows].sort((a, b) => (b.exit_ts ?? 0) - (a.exit_ts ?? 0));
  }, [trades, modeSel, dFrom, dTo]);

  const exportCsv = () => {
    const headers = [
      "strategy_mode",
      "trade_number",
      "direction",
      "pair_key",
      "entry_ts",
      "exit_ts",
      "entry_signal",
      "exit_signal",
      "entry_price",
      "exit_price",
      "qty",
      "entry_notional_usd",
      "exit_notional_usd",
      "net_pnl_usd",
      "net_pnl_pct",
      "mfe_usd",
      "mfe_pct",
      "mae_usd",
      "mae_pct",
      "cumulative_pnl_usd",
      "cumulative_pnl_pct",
      "reason",
    ];
    const lines = [headers.join(",")];
    for (const t of filtered) {
      const dir = (t.direction ?? "").toLowerCase();
      const entrySig = dir === "long" ? "Long" : "Short";
      const exitSig = dir === "long" ? "Long Exit" : "Short Exit";
      const entryN = legNotionalUsd(t, "entry");
      const exitN = legNotionalUsd(t, "exit");
      const row = [
        tradeStrategyLabel(t),
        String(t.strategy_trade_index ?? ""),
        t.direction ?? "",
        t.pair_key,
        String(t.entry_ts ?? ""),
        String(t.exit_ts ?? ""),
        entrySig,
        exitSig,
        String(t.entry_price ?? ""),
        String(t.exit_price ?? ""),
        String(t.qty ?? ""),
        String(entryN),
        String(exitN),
        String(t.pnl ?? ""),
        String(t.net_pnl_pct ?? ""),
        String(t.mfe_usd ?? ""),
        String(t.mfe_pct ?? ""),
        String(t.mae_usd ?? ""),
        String(t.mae_pct ?? ""),
        String(t.cumulative_pnl_after ?? ""),
        String(t.cumulative_pnl_pct ?? ""),
        `"${String(t.reason ?? "").replace(/"/g, '""')}"`,
      ];
      lines.push(row.join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `strategy_report_${modeSel === "__all__" ? "all" : modeSel}_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <section className="panel" style={{ marginTop: 12 }}>
      <div className="panel-hdr" style={{ alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <span className="ph-title">STRATEGY_REPORT</span>
        <span className="strat-mode" style={{ flex: "1 1 180px" }}>
          closed legs · JSONL <span className="mono">strategy_report_trade</span> per close
        </span>
        <label style={{ fontSize: 10, color: "var(--text-muted)", display: "flex", alignItems: "center", gap: 6 }}>
          Strategy
          <select
            value={modeSel}
            onChange={(e) => setModeSel(e.target.value)}
            style={{
              padding: "4px 8px",
              borderRadius: 4,
              border: "1px solid var(--border-subtle, #374151)",
              background: "var(--surface-2, #111827)",
              color: "var(--text-primary)",
              fontSize: 11,
            }}
          >
            {modes.map((m) => (
              <option key={m} value={m}>
                {m === "__all__" ? "All strategies" : modeLabel(m)}
              </option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 10, color: "var(--text-muted)", display: "flex", alignItems: "center", gap: 6 }}>
          From
          <input type="date" value={dFrom} onChange={(e) => setDFrom(e.target.value)} style={{ fontSize: 11 }} />
        </label>
        <label style={{ fontSize: 10, color: "var(--text-muted)", display: "flex", alignItems: "center", gap: 6 }}>
          To
          <input type="date" value={dTo} onChange={(e) => setDTo(e.target.value)} style={{ fontSize: 11 }} />
        </label>
        <button
          type="button"
          onClick={exportCsv}
          disabled={filtered.length === 0}
          style={{
            padding: "6px 12px",
            borderRadius: 4,
            border: "1px solid var(--border-subtle)",
            background: "var(--surface-2)",
            color: "var(--text-primary)",
            fontSize: 10,
            fontWeight: 700,
            cursor: filtered.length === 0 ? "not-allowed" : "pointer",
          }}
        >
          EXPORT CSV
        </button>
      </div>
      <div className="analytics-table-wrap" style={{ padding: "0 12px 12px", overflowX: "auto" }}>
        {filtered.length === 0 ? (
          <div className="no-data" style={{ padding: 16 }}>
            No trades match filters (closed legs only; new fields appear after next bot version closes).
          </div>
        ) : (
          <table className="analytics-table strategy-report-table" style={{ fontSize: 11, minWidth: 920 }}>
            <thead>
              <tr>
                <th>Trade #</th>
                <th>Type</th>
                <th>Date &amp; time</th>
                <th>Signal</th>
                <th>Price</th>
                <th>Size</th>
                <th>Net P&amp;L</th>
                <th>MFE</th>
                <th>MAE</th>
                <th>Cumulative</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((t) => {
                const dir = (t.direction ?? "").toLowerCase();
                const long = dir === "long";
                const dirColor = long ? "#60a5fa" : "#f87171";
                const entrySig = long ? "Long" : "Short";
                const exitSig = long ? "Long Exit" : "Short Exit";
                const tn = t.strategy_trade_index ?? "—";
                const entryN = legNotionalUsd(t, "entry");
                const exitN = legNotionalUsd(t, "exit");
                const netPct = t.net_pnl_pct;
                const cumPct = t.cumulative_pnl_pct;
                const mfeU = t.mfe_usd;
                const maeU = t.mae_usd;
                const mfeP = t.mfe_pct;
                const maeP = t.mae_pct;
                const pk = `${t.entry_cl_ord_id ?? t.pair_key}-${t.exit_ts}`;
                return (
                  <Fragment key={pk}>
                    <tr style={{ borderTop: "1px solid var(--border-subtle, #374151)" }}>
                      <td rowSpan={2} style={{ verticalAlign: "middle", fontWeight: 700, color: dirColor }}>
                        {tn} <span style={{ display: "block", fontSize: 10 }}>{long ? "Long" : "Short"}</span>
                        <span
                          style={{
                            display: "block",
                            fontSize: 9,
                            color: "var(--text-muted)",
                            fontWeight: 500,
                            marginTop: 2,
                          }}
                          className="mono"
                        >
                          {t.pair_key}
                          {t.symbol ? ` · ${t.symbol}` : ""}
                        </span>
                      </td>
                      <td className="mono">Exit</td>
                      <td className="mono" style={{ whiteSpace: "nowrap" }}>
                        {fmtExTime(t.exit_ts)}
                      </td>
                      <td>{exitSig}</td>
                      <td className="mono">{fmt(t.exit_price ?? 0, 4)} USD</td>
                      <td className="mono" style={{ lineHeight: 1.35 }}>
                        {fmt(t.qty ?? 0, 4)}
                        <span style={{ display: "block", fontSize: 9, color: "var(--text-muted)" }}>
                          {fmt(exitN, 2)} USD
                        </span>
                      </td>
                      <td
                        rowSpan={2}
                        style={{
                          verticalAlign: "middle",
                          fontWeight: 700,
                          color: (t.pnl ?? 0) >= 0 ? "#4ade80" : "#f87171",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {(t.pnl ?? 0) >= 0 ? "+" : ""}
                        {fmt(t.pnl ?? 0, 3)} USD
                        <span style={{ display: "block", fontSize: 10, color: "var(--text-muted)" }}>
                          {netPct != null ? `${(t.pnl ?? 0) >= 0 ? "+" : ""}${fmt(netPct, 2)}%` : "—"}
                        </span>
                      </td>
                      <td rowSpan={2} className="mono" style={{ verticalAlign: "middle", fontSize: 10 }}>
                        {mfeU != null ? `${fmt(mfeU, 3)} USD` : "—"}
                        <span style={{ display: "block", color: "var(--text-muted)" }}>
                          {mfeP != null ? `${fmt(mfeP, 2)}%` : ""}
                        </span>
                      </td>
                      <td rowSpan={2} className="mono" style={{ verticalAlign: "middle", fontSize: 10 }}>
                        {maeU != null ? `${fmt(maeU, 3)} USD` : "—"}
                        <span style={{ display: "block", color: "var(--text-muted)" }}>
                          {maeP != null ? `${fmt(maeP, 2)}%` : ""}
                        </span>
                      </td>
                      <td rowSpan={2} className="mono" style={{ verticalAlign: "middle", fontSize: 10 }}>
                        {t.cumulative_pnl_after != null ? `${fmt(t.cumulative_pnl_after, 3)} USD` : "—"}
                        <span style={{ display: "block", color: "var(--text-muted)" }}>
                          {cumPct != null ? `${fmt(cumPct, 2)}%` : ""}
                        </span>
                      </td>
                    </tr>
                    <tr>
                      <td className="mono">Entry</td>
                      <td className="mono" style={{ whiteSpace: "nowrap" }}>
                        {fmtExTime(t.entry_ts)}
                      </td>
                      <td>{entrySig}</td>
                      <td className="mono">{fmt(t.entry_price ?? 0, 4)} USD</td>
                      <td className="mono" style={{ lineHeight: 1.35 }}>
                        {fmt(t.qty ?? 0, 4)}
                        <span style={{ display: "block", fontSize: 9, color: "var(--text-muted)" }}>
                          {fmt(entryN, 2)} USD
                        </span>
                      </td>
                    </tr>
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
        <p className="analytics-footnote" style={{ marginTop: 8 }}>
          MFE / MAE track unrealized extremes while the leg is open (mark updates). Cumulative is per strategy mode,
          ordered by exit time. Matches session JSONL analytics rows{" "}
          <span className="mono">subtype=strategy_report_trade</span>.
        </p>
      </div>
    </section>
  );
}

export function AnalyticsTab({
  snapshot,
  send,
}: {
  snapshot: Snapshot | null;
  send: (payload: Record<string, unknown>) => void;
}) {
  const scalp = snapshot?.scalp ?? null;
  const trades: ScalpTrade[] = useMemo(
    () => scalp?.trader?.trade_history ?? [],
    [scalp?.trader?.trade_history],
  );
  const slb = scalp?.strategy_lookback;

  const scalpByPair = useMemo(() => {
    const m = new Map<string, { n: number; pnl: number; wins: number }>();
    for (const t of trades) {
      const pk = t.pair_key;
      const row = m.get(pk) ?? { n: 0, pnl: 0, wins: 0 };
      row.n += 1;
      row.pnl += t.pnl ?? 0;
      if ((t.pnl ?? 0) > 0) row.wins += 1;
      m.set(pk, row);
    }
    return m;
  }, [trades]);

  const scalpByStrategy = useMemo(() => {
    const m = new Map<string, { n: number; pnl: number; wins: number }>();
    for (const t of trades) {
      const mode = t.strategy_mode && t.strategy_mode !== "unknown" ? t.strategy_mode : "legacy_unknown";
      const row = m.get(mode) ?? { n: 0, pnl: 0, wins: 0 };
      row.n += 1;
      row.pnl += t.pnl ?? 0;
      if ((t.pnl ?? 0) > 0) row.wins += 1;
      m.set(mode, row);
    }
    return m;
  }, [trades]);

  const scalpTotalClosed = useMemo(() => trades.reduce((s, t) => s + (t.pnl ?? 0), 0), [trades]);

  const exchangeLog = useMemo(() => {
    const raw = (snapshot?.exchange_errors ?? []) as ExchangeErrorEvent[];
    return [...raw].sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0));
  }, [snapshot?.exchange_errors]);

  const exchangeUnacked = useMemo(
    () => exchangeLog.filter((e) => !e.acknowledged).length,
    [exchangeLog],
  );

  const openScalp = scalp?.trader?.open_count ?? 0;
  const bal = scalp?.balances;
  const fut = bal?.futures;
  const committedUsd =
    fut != null ? (fut.initial_margin ?? 0) + (fut.open_orders_hold_usd ?? 0) : null;
  const capRaw = scalp?.max_concurrent_positions ?? 0;
  const capLabel = capRaw <= 0 ? "∞" : String(capRaw);

  return (
    <div className="analytics-dash">
      <section className="panel" style={{ marginBottom: 12 }}>
        <div className="panel-hdr">
          <span className="ph-title">SCALP_OVERVIEW</span>
          <span style={{ fontSize: 10, color: "var(--text-muted)", letterSpacing: "0.08em" }}>
            Coinbase CDE perps · venue command failures are logged in EXCHANGE_ERRORS below
          </span>
        </div>
        <div className="analytics-grid" style={{ padding: 12 }}>
          <div className="an-card">
            <div className="an-label">OPEN POSITIONS</div>
            <div className="an-val" style={{ color: openScalp ? "var(--accent)" : "var(--text-muted)" }}>
              {scalp ? String(openScalp) : "—"}
            </div>
            <div className="an-sub">SCALP · EXCHANGE + SIM TRACKED</div>
          </div>
          <div className="an-card">
            <div className="an-label">SCALP TODAY</div>
            <div className={`an-val ${(scalp?.trader?.daily_pnl ?? 0) >= 0 ? "accent" : "danger"}`}>
              {(scalp?.trader?.daily_pnl ?? 0) >= 0 ? "+" : ""}
              {fmt(scalp?.trader?.daily_pnl ?? 0, 4)} USD
            </div>
            <div className="an-sub">DAILY ROLLING (UTC DAY)</div>
          </div>
          <div className="an-card">
            <div className="an-label">SCALP CLOSED (RING)</div>
            <div className={`an-val ${scalpTotalClosed >= 0 ? "accent" : "danger"}`}>
              {scalpTotalClosed >= 0 ? "+" : ""}
              {fmt(scalpTotalClosed, 4)} USD
            </div>
            <div className="an-sub">{trades.length} CLOSED · IN-MEMORY</div>
          </div>
          <div className="an-card">
            <div className="an-label">SIM MODE</div>
            <div className="an-val" style={{ fontSize: 14, letterSpacing: "0.06em" }}>
              {scalp ? (scalp.sim_mode ? "ON" : "OFF") : "—"}
            </div>
            <div className="an-sub">TOGGLE IN SETTINGS (SCALP MODE)</div>
          </div>
          <div className="an-card">
            <div className="an-label">CONCURRENT CAP</div>
            <div className="an-val" style={{ fontSize: 14, letterSpacing: "0.06em" }} title="Runtime max open legs across all scalp pairs">
              {scalp ? `${openScalp} / ${capLabel}` : "—"}
            </div>
            <div className="an-sub">OPEN / MAX · 0 = UNLIMITED</div>
          </div>
        </div>
        {scalp && <MaxConcurrentPositionsControl key={capRaw} capRaw={capRaw} send={send} />}
      </section>

      <StrategyReportSection trades={trades} />

      <details className="settings-accordion analytics-details">
        <summary className="settings-accordion-summary">
          <span className="settings-accordion-title">More: capital, strategies, tables, backtest, exchange errors</span>
        </summary>
        <div className="settings-accordion-body analytics-details-body">
      {scalp && scalp.venue === "coinbase_perps" && (
        <section className="panel" style={{ marginBottom: 12 }}>
          <div className="panel-hdr">
            <span className="ph-title">COINBASE_CAPITAL</span>
            <span style={{ fontSize: 10, color: "var(--text-muted)", letterSpacing: "0.08em" }}>
              CDE / Advanced Trade · ~30s poll
            </span>
          </div>
          <div className="analytics-grid" style={{ padding: 12 }}>
            <div className="an-card">
              <div className="an-label">TOTAL FUTURES EQUITY (USD)</div>
              <div className="an-val accent">
                {fut != null &&
                fut.total_usd_balance != null &&
                Number.isFinite(fut.total_usd_balance) ? (
                  `$${fmt(fut.total_usd_balance, 2)}`
                ) : fut != null ? (
                  `$${fmt(
                    (fut.available_margin ?? 0) +
                      (fut.initial_margin ?? 0) +
                      (fut.open_orders_hold_usd ?? 0) +
                      (fut.unrealized_pnl ?? 0),
                    2,
                  )} *`
                ) : (
                  "…"
                )}
              </div>
              <div className="an-sub">
                {fut != null &&
                fut.total_usd_balance != null &&
                Number.isFinite(fut.total_usd_balance)
                  ? "COINBASE balance_summary.total_usd_balance"
                  : fut != null
                    ? "EST: AVAIL + MARGIN + ORDER HOLD + UNREAL PNL (if total_usd missing)"
                    : "WAITING FOR BALANCE POLL"}
              </div>
            </div>
            <div className="an-card">
              <div className="an-label">COMMITTED (POSITIONS + ORDERS)</div>
              <div
                className="an-val"
                style={{
                  color:
                    committedUsd != null && committedUsd > 0 ? "#fbbf24" : "var(--text-muted)",
                }}
              >
                {committedUsd != null ? `$${fmt(committedUsd, 2)}` : "—"}
              </div>
              <div className="an-sub">
                {fut != null
                  ? `MARGIN IN POSITIONS $${fmt(fut.initial_margin ?? 0, 2)} · OPEN ORDERS HOLD $${fmt(fut.open_orders_hold_usd ?? 0, 2)}`
                  : "—"}
              </div>
            </div>
            <div className="an-card">
              <div className="an-label">AVAILABLE MARGIN</div>
              <div className="an-val accent">{fut != null ? `$${fmt(fut.available_margin ?? 0, 2)}` : "—"}</div>
              <div className="an-sub">
                {fut != null
                  ? `FUTURES BUYING POWER (ORDER PREVIEW) $${fmt(fut.buying_power ?? 0, 2)}`
                  : "—"}
              </div>
            </div>
            <div className="an-card">
              <div className="an-label">SPOT USD / USDC (AVAILABLE)</div>
              <div className="an-val" style={{ fontSize: 16 }}>
                ${fmt(bal?.spot_usd_available ?? 0, 2)}
              </div>
              <div className="an-sub">USDC + USD ON SPOT WALLETS · NOT FUTURES EQUITY</div>
            </div>
          </div>
        </section>
      )}

      {scalp && (
        <section className="panel" style={{ marginBottom: 12 }}>
          <div className="panel-hdr">
            <span className="ph-title">SCALP_ACTIVE_STRATEGIES</span>
            <span style={{ fontSize: 10, color: "var(--text-muted)", letterSpacing: "0.08em" }}>
              per pair · live runtime state
            </span>
          </div>
          <div className="analytics-table-wrap">
            <table className="analytics-table">
              <thead>
                <tr>
                  <th>PAIR</th>
                  <th>SYMBOL</th>
                  <th>STRATEGY</th>
                  <th>SELECTED BY</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(scalp.active_modes ?? {})
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([pk, mode]) => {
                    const symbol = scalp.pair_symbols?.[pk] ?? "";
                    const source = scalp.mode_sources?.[pk] ?? "unknown";
                    const sourceLabel: Record<string, string> = {
                      config: "config.toml default",
                      wfo_champion: "WFO champion",
                      bootstrap: "no-champion bootstrap (2h return %)",
                      tuner: "param tuner",
                    };
                    return (
                      <tr key={pk}>
                        <td className="mono">{pk}</td>
                        <td className="mono" style={{ fontSize: 10 }}>{symbol}</td>
                        <td className="mono">{modeLabel(mode)}</td>
                        <td style={{ fontSize: 11, color: source === "wfo_champion" ? "var(--accent)" : "var(--text-muted)" }}>
                          {sourceLabel[source] ?? source}
                        </td>
                      </tr>
                    );
                  })}
                {Object.keys(scalp.active_modes ?? {}).length === 0 && (
                  <tr>
                    <td colSpan={4} className="no-data">No scalp pairs configured</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="analytics-two-col">
        <section className="panel">
          <div className="panel-hdr">
            <span className="ph-title">SCALP_PER_PAIR</span>
            <span className="strat-mode">closed trades</span>
          </div>
          <div className="analytics-table-wrap">
            <table className="analytics-table">
              <thead>
                <tr>
                  <th>PAIR</th>
                  <th>TRADES</th>
                  <th>WIN%</th>
                  <th>P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {!scalp && (
                  <tr>
                    <td colSpan={4} className="no-data">
                      Scalp offline
                    </td>
                  </tr>
                )}
                {scalp &&
                  Array.from(scalpByPair.entries())
                    .sort((a, b) => a[0].localeCompare(b[0]))
                    .map(([pk, row]) => (
                      <tr key={pk}>
                        <td className="mono">{pk}</td>
                        <td>{row.n}</td>
                        <td>{row.n ? fmt((row.wins / row.n) * 100, 1) : "—"}%</td>
                        <td className={row.pnl >= 0 ? "c-buy" : "c-sell"}>{fmt(row.pnl, 4)}</td>
                      </tr>
                    ))}
                {scalp && scalpByPair.size === 0 && (
                  <tr>
                    <td colSpan={4} className="no-data">
                      No closed scalp trades yet
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <div className="panel-hdr">
            <span className="ph-title">SCALP_RESERVED</span>
            <span className="strat-mode">capital earmarked for open entries</span>
          </div>
          <div className="analytics-table-wrap">
            <table className="analytics-table">
              <thead>
                <tr>
                  <th>METRIC</th>
                  <th>VALUE</th>
                </tr>
              </thead>
              <tbody>
                {!scalp && (
                  <tr>
                    <td colSpan={2} className="no-data">Scalp offline</td>
                  </tr>
                )}
                {scalp && (
                  <>
                    <tr>
                      <td>Reserved capital (USD)</td>
                      <td className="mono">{fmt(scalp.trader?.reserved_capital ?? 0, 2)}</td>
                    </tr>
                    <tr>
                      <td>Open count</td>
                      <td className="mono">{scalp.trader?.open_count ?? 0}</td>
                    </tr>
                  </>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="panel" style={{ marginTop: 12 }}>
        <div className="panel-hdr">
          <span className="ph-title">SCALP_LIVE_BY_STRATEGY</span>
          <span className="strat-mode">from closed positions · new trades tagged</span>
        </div>
        <div className="analytics-table-wrap">
          <table className="analytics-table">
            <thead>
              <tr>
                <th>STRATEGY</th>
                <th>TRADES</th>
                <th>WIN%</th>
                <th>P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {!scalp && (
                <tr>
                  <td colSpan={4} className="no-data">
                    Scalp offline
                  </td>
                </tr>
              )}
              {scalp &&
                Array.from(scalpByStrategy.entries())
                  .sort((a, b) => a[0].localeCompare(b[0]))
                  .map(([mode, row]) => (
                    <tr key={mode}>
                      <td className="mono">{modeLabel(mode)}</td>
                      <td>{row.n}</td>
                      <td>{row.n ? fmt((row.wins / row.n) * 100, 1) : "—"}%</td>
                      <td className={row.pnl >= 0 ? "c-buy" : "c-sell"}>{fmt(row.pnl, 4)}</td>
                    </tr>
                  ))}
              {scalp && scalpByStrategy.size === 0 && (
                <tr>
                  <td colSpan={4} className="no-data">
                    No closed trades
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          {scalp && trades.some((t) => !t.strategy_mode || t.strategy_mode === "unknown") && (
            <p className="analytics-footnote">
              Rows labeled <span className="mono">legacy_unknown</span> are closes from before strategy tagging was
              added; new entries record the active mode.
            </p>
          )}
        </div>
      </section>

      <section className="panel" style={{ marginTop: 12 }}>
        <div className="panel-hdr">
          <span className="ph-title">BACKTEST_BY_STRATEGY</span>
          <span className="strat-mode">
            vector backtest · entries opened in last {slb?.lookback_hours ?? "—"}h · all pairs
          </span>
        </div>
        {!slb?.pairs || Object.keys(slb.pairs).length === 0 ? (
          <div className="no-data" style={{ padding: 20 }}>
            No strategy_lookback data (warm bar store / enable scalp).
          </div>
        ) : (
          <div className="analytics-table-wrap">
            {Object.entries(slb.pairs)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([pairKey, modes]) => {
                const activeMode = scalp?.active_modes?.[pairKey] ?? "";
                return (
                  <div key={pairKey} style={{ marginBottom: 16 }}>
                    <div className="analytics-subhdr mono">{pairKey}</div>
                    <table className="analytics-table">
                      <thead>
                        <tr>
                          <th style={{ width: 16 }}></th>
                          <th>MODE</th>
                          <th>BT TR</th>
                          <th>BT WR%</th>
                          <th>BT P&amp;L</th>
                        </tr>
                      </thead>
                      <tbody>
                        {STRATEGY_ORDER.map((mode) => {
                          const isActive = mode === activeMode;
                          const row = modes[mode] as StrategyLookbackModeRow | undefined;
                          if (!row) {
                            return (
                              <tr key={mode} style={isActive ? { background: "rgba(var(--accent-rgb, 0,255,136), 0.07)" } : undefined}>
                                <td>{isActive ? "▸" : ""}</td>
                                <td className="mono">{modeLabel(mode)}</td>
                                <td colSpan={3} className="no-data">
                                  —
                                </td>
                              </tr>
                            );
                          }
                          const wr = (row.weighted_win_rate ?? row.win_rate) * 100;
                          const pnl = row.weighted_pnl ?? row.pnl;
                          return (
                            <tr key={mode} style={isActive ? { background: "rgba(var(--accent-rgb, 0,255,136), 0.07)" } : undefined}>
                              <td style={{ color: "var(--accent)", fontWeight: 700 }}>{isActive ? "▸" : ""}</td>
                              <td className="mono" style={isActive ? { color: "var(--accent)" } : undefined}>{modeLabel(mode)}</td>
                              <td>{row.trades}</td>
                              <td>{fmt(wr, 1)}</td>
                              <td className={pnl >= 0 ? "c-buy" : "c-sell"}>{fmt(pnl, 4)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                );
              })}
          </div>
        )}
      </section>

      <WfoLastPassScoreboardSection lastPass={scalp?.wfo?.last_wfo_pass ?? null} />

      <section className="panel" style={{ marginTop: 12 }}>
        <div className="panel-hdr" style={{ alignItems: "center", flexWrap: "wrap", gap: 8 }}>
          <span className="ph-title">EXCHANGE_ERRORS</span>
          <span style={{ fontSize: 10, color: "var(--text-muted)", letterSpacing: "0.08em", flex: "1 1 200px" }}>
            Exchange command failures · acknowledge on the banner or here
          </span>
          {exchangeUnacked > 0 && (
            <button
              type="button"
              onClick={() => send({ action: "acknowledge_exchange_errors" })}
              style={{
                padding: "6px 14px",
                borderRadius: 4,
                border: "none",
                cursor: "pointer",
                background: "rgba(245, 158, 11, 0.25)",
                color: "#fcd34d",
                fontWeight: 700,
                fontSize: 10,
                letterSpacing: "0.08em",
              }}
            >
              ACKNOWLEDGE ALL ({exchangeUnacked})
            </button>
          )}
        </div>
        <div className="analytics-table-wrap" style={{ padding: "0 12px 12px" }}>
          <table className="analytics-table">
            <thead>
              <tr>
                <th>TIME</th>
                <th>SEV</th>
                <th>SOURCE</th>
                <th>TITLE</th>
                <th>DETAIL</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {exchangeLog.length === 0 && (
                <tr>
                  <td colSpan={6} className="no-data">
                    No exchange errors recorded this session
                  </td>
                </tr>
              )}
              {exchangeLog.map((row) => (
                <tr
                  key={row.id}
                  style={
                    row.acknowledged
                      ? { opacity: 0.55 }
                      : row.level === "error"
                        ? { background: "rgba(239, 68, 68, 0.08)" }
                        : { background: "rgba(245, 158, 11, 0.07)" }
                  }
                >
                  <td className="mono" style={{ fontSize: 10, whiteSpace: "nowrap" }}>
                    {fmtExTime(row.ts)}
                  </td>
                  <td className="mono" style={{ fontSize: 10, fontWeight: 700 }}>
                    {row.level === "error" ? "ERR" : "WRN"}
                  </td>
                  <td className="mono" style={{ fontSize: 10 }}>{row.source || "—"}</td>
                  <td style={{ fontWeight: 600, maxWidth: 220 }}>{row.title}</td>
                  <td style={{ fontSize: 11, wordBreak: "break-word", maxWidth: 360 }}>{row.detail || "—"}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {!row.acknowledged ? (
                      <button
                        type="button"
                        onClick={() => send({ action: "acknowledge_exchange_errors", error_ids: [row.id] })}
                        style={{
                          padding: "4px 10px",
                          borderRadius: 4,
                          border: "1px solid var(--border-subtle, #374151)",
                          cursor: "pointer",
                          background: "var(--surface-2, #111827)",
                          color: "var(--text-primary, #e5e7eb)",
                          fontSize: 10,
                          fontWeight: 600,
                        }}
                      >
                        ACK
                      </button>
                    ) : (
                      <span style={{ fontSize: 10, color: "var(--text-muted)" }}>cleared</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
        </div>
      </details>
    </div>
  );
}
