import type { ChampionSummary, ScalpSnapshot } from "../lib/types";

type Props = {
  selectedPair: string;
  cdeProductId?: string;
  displaySymbol: string;
  isScalpTerminal: boolean;
  scalp?: ScalpSnapshot | null;
  /** Book/threat snapshot when scalp pair tabs are not configured. */
  threatLevel?: string;
  riskHalted?: boolean;
  riskHaltReason?: string;
  onSend: (payload: Record<string, unknown>) => void;
};

function resolveChampionForProduct(
  champions: Record<string, ChampionSummary> | null | undefined,
  productId: string | undefined,
): ChampionSummary | null {
  if (!champions || !productId) return null;
  const u = productId.trim().toUpperCase();
  for (const [k, v] of Object.entries(champions)) {
    if (String(k).trim().toUpperCase() === u) return v;
  }
  return null;
}

function fmtSource(src: string | undefined): string {
  if (!src) return "—";
  return src.replace(/_/g, " ").toUpperCase();
}

function lastNonEmptyLine(s: string | undefined): string | null {
  if (!s?.trim()) return null;
  const lines = s.trim().split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  return lines.length ? lines[lines.length - 1]! : null;
}

function formatWinRate(w: number | undefined): string {
  const x = w ?? 0;
  const pct = x > 0 && x <= 1 ? x * 100 : x;
  return `${pct.toFixed(1)}%`;
}

export function TerminalBrainPanel({
  selectedPair,
  cdeProductId,
  displaySymbol,
  isScalpTerminal,
  scalp,
  threatLevel,
  riskHalted,
  riskHaltReason,
  onSend,
}: Props) {
  const srcRaw = isScalpTerminal ? scalp?.mode_sources?.[selectedPair] : undefined;
  const championRow = isScalpTerminal ? resolveChampionForProduct(scalp?.champions ?? null, cdeProductId) : null;
  const wfo = scalp?.wfo;
  const wfoEnabled = Boolean(scalp?.session_policy?.wfo_enabled ?? wfo?.enabled);
  const wfoPair = wfo?.pairs?.[selectedPair];
  const warmup = scalp?.warmup;
  const op = scalp?.operator;
  const tuner = scalp?.tuner?.[selectedPair];
  const regime = scalp?.regime_risk_on;
  const rawRegimeReasons = selectedPair ? regime?.pair_reasons?.[selectedPair] : undefined;
  const pairRegimeReasons = Array.isArray(rawRegimeReasons) ? rawRegimeReasons : undefined;

  const openForPair = isScalpTerminal
    ? Object.values(scalp?.trader?.open_positions ?? {}).filter((p) => p.pair_key === selectedPair).length
    : 0;

  const activity: string[] = [];

  if (isScalpTerminal && scalp) {
    const phase = scalp.startup_phase ?? op?.startup_phase;
    if (phase) activity.push(`Phase: ${phase}${scalp.sim_mode ? " · SIM" : " · LIVE"}`);

    const fe = op?.flow_event;
    if (fe?.kind) activity.push(`${fe.kind}${fe.detail ? ` — ${fe.detail}` : ""}`);

    const running = op?.warmup_steps?.find((s) => s.status === "running");
    if (running?.label) activity.push(`Warmup: ${running.label}${running.detail ? ` (${running.detail})` : ""}`);

    if (pairRegimeReasons?.length) activity.push(`Regime: ${pairRegimeReasons.join(" · ")}`);
    else if (regime?.active && regime.mode_label) activity.push(`Regime: ${regime.mode_label}`);

    const adj = tuner?.adjustments?.length ? tuner.adjustments[tuner.adjustments.length - 1] : null;
    if (adj) activity.push(`Tuner: ${adj}`);

    const wfoTail = lastNonEmptyLine(wfo?.wfo_action_log);
    if (wfoTail) activity.push(`WFO: ${wfoTail.slice(0, 120)}${wfoTail.length > 120 ? "…" : ""}`);

    if (openForPair > 0) activity.push(`Open legs (this pair): ${openForPair}`);

    const emp = scalp.trader?.empirical_market;
    if (emp?.enabled && (emp.active_watch_count ?? 0) > 0) {
      activity.push(`Empirical promotion: watching ${emp.active_watch_count} limit(s)`);
    }
  } else {
    if (threatLevel) activity.push(`Threat: ${threatLevel}`);
    if (riskHalted) activity.push(`Risk halt${riskHaltReason ? `: ${riskHaltReason}` : ""}`);
  }

  const wfoChampionLit = Boolean(championRow);
  let wfoLine: string;
  if (!isScalpTerminal) {
    wfoLine = "Walk-forward applies to scalp pairs only.";
  } else if (!wfoEnabled) {
    wfoLine = "WFO disabled in session policy.";
  } else if (wfoChampionLit) {
    wfoLine = `${championRow!.mode} · eval WR ${formatWinRate(championRow!.win_rate)} · ${championRow!.trade_count ?? 0} trades`;
  } else {
    const bc = wfoPair?.bar_count ?? 0;
    const need = wfo?.required_span_hours != null ? `≥${wfo.required_span_hours}h data` : "building data";
    const next =
      wfo?.seconds_until_next != null && wfo.seconds_until_next >= 0
        ? `next pass ~${Math.ceil(wfo.seconds_until_next / 60)}m`
        : "";
    wfoLine = `No champion for this product yet · ${bc} bars · ${need}${next ? ` · ${next}` : ""}`;
  }

  return (
    <div className="terminal-brain-panel">
      <div className="ph-title">BRAIN · {displaySymbol || selectedPair}</div>
      <div className="brain-pair-key mono">{selectedPair}</div>

      {!isScalpTerminal ? (
        <div className="brain-row">
          <span className="brain-k">SCALP</span>
          <span className="brain-v">
            Add <code className="mono">[scalp.pairs]</code> in <code className="mono">config.toml</code> for CDE strategy, WFO,
            and resting orders on this terminal.
          </span>
        </div>
      ) : (
        <>
          <div className="brain-row">
            <span className="brain-k">STRATEGY</span>
            <span className="brain-v brain-strategy">
              {scalp?.active_modes?.[selectedPair] ?? scalp?.auto_mode_fallback ?? "—"}
            </span>
          </div>
          <div className="brain-row">
            <span className="brain-k">SOURCE</span>
            <span className={`brain-pill brain-pill--${srcRaw === "wfo_champion" ? "champ" : "muted"}`}>
              {fmtSource(srcRaw)}
            </span>
          </div>

          <div
            className="brain-wfo-row"
            role="status"
            aria-label={
              wfoChampionLit
                ? `WFO champion active: ${championRow!.mode}`
                : wfoEnabled
                  ? "WFO running; no champion for this product yet"
                  : "WFO disabled"
            }
          >
            <span
              className={`brain-led${wfoChampionLit ? " brain-led--ok" : wfoEnabled ? " brain-led--warn" : " brain-led--off"}`}
              title={wfoChampionLit ? "WFO champion loaded for this chart product" : "No champion row for this product"}
              aria-hidden
            />
            <div className="brain-wfo-copy">
              <div className="brain-wfo-title">
                {wfoChampionLit ? (
                  <>
                    <span className="brain-wfo-champion-label">CHAMPION</span>
                    <span className="brain-wfo-champion-mode">{championRow!.mode}</span>
                  </>
                ) : wfoEnabled ? (
                  <span className="brain-wfo-pending">WFO active · no champion for this symbol</span>
                ) : (
                  <span className="brain-wfo-off">WFO off</span>
                )}
              </div>
              <div className="brain-wfo-sub">{wfoLine}</div>
            </div>
          </div>

          {warmup?.enabled && warmup.phase && warmup.phase !== "idle" ? (
            <div className="brain-row brain-row-tight">
              <span className="brain-k">WARMUP</span>
              <span className="brain-v">
                {warmup.phase}
                {warmup.progress_pct != null ? ` · ${warmup.progress_pct.toFixed(0)}%` : ""}
              </span>
            </div>
          ) : null}
        </>
      )}

      <div className="brain-activity-hdr">NOW</div>
      <ul className="brain-activity-list">
        {activity.length === 0 ? (
          <li className="brain-activity-empty">Waiting for snapshot activity…</li>
        ) : (
          activity.slice(0, 6).map((line, i) => (
            <li key={i}>{line}</li>
          ))
        )}
      </ul>

      <details className="brain-session-details">
        <summary>Session actions</summary>
        <div className="ctrl-row-btns brain-session-btns">
          <button type="button" className="ghost-btn" onClick={() => onSend({ action: "update_risk", resume_risk_halt: true })}>
            RESUME RISK
          </button>
          <button
            type="button"
            className="ghost-btn"
            title="Reload [bot]/[pairs] from config.toml into the dashboard (no process exit)"
            onClick={() => onSend({ action: "soft_restart" })}
          >
            SOFT RELOAD CONFIG
          </button>
          <button
            type="button"
            className="ghost-btn brain-btn-danger"
            title="Kill and respawn the backend; UI reconnects via WS"
            onClick={() => {
              if (confirm("Restart the Python backend? Config reloads from disk; the dashboard will reconnect.")) {
                onSend({ action: "restart_process" });
              }
            }}
          >
            RESTART BACKEND
          </button>
        </div>
      </details>
    </div>
  );
}
