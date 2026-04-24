import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ScalpSessionPolicy, ScalpSnapshot, Snapshot, WarmupStepData } from "../lib/types";
import { SCALP_WFO_TT } from "../lib/scalpSettingsTooltips";
import { warmupProgressFromSnapshot } from "../lib/warmupUiProgress";
import { ScalpDecisionFlowChart } from "./ScalpDecisionFlowChart";
import "../styles/settings-tab.css";

type Props = {
  scalp: ScalpSnapshot | null;
  send: (payload: Record<string, unknown>) => void;
  connected: boolean;
  snapshot: Snapshot | null;
  /** Same pair key as Terminal chart tab — flow highlights follow this pair. */
  focusPairKey?: string;
};

type CheckResult = {
  check: string;
  ok: boolean;
  message: string;
  ts: number;
};

type RestartPhase = "idle" | "confirm" | "restarting" | "checking" | "done";

function fmtBars(n: number | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString();
}

function fmtTs(ts: number): string {
  return new Date(ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function BenefitRisk({ benefit, risk }: { benefit: string; risk: string }) {
  return (
    <div className="settings-benefit-risk">
      <span className="benefit">
        <strong>Benefit</strong> — {benefit}
      </span>
      <span className="risk">
        <strong>Risk</strong> — {risk}
      </span>
    </div>
  );
}

// ── Restart section ───────────────────────────────────────────────────────────

function RestartSection({
  send,
  connected,
  snapshot,
  scalpEnabled,
}: {
  send: (payload: Record<string, unknown>) => void;
  connected: boolean;
  snapshot: Snapshot | null;
  scalpEnabled: boolean;
}) {
  const [phase, setPhase] = useState<RestartPhase>("idle");
  const [checks, setChecks] = useState<CheckResult[]>([]);
  const [tooltip, setTooltip] = useState<string | null>(null);
  const [debugLog, setDebugLog] = useState<{ ts: number; msg: string }[]>([]);
  const [stepLabel, setStepLabel] = useState("");

  // Track whether we were "restarting" when connected transitions
  const phaseRef = useRef<RestartPhase>("idle");
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const restartDeadlineRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const snapshotReceivedRef = useRef(false);
  const checksRunRef = useRef(false);

  useLayoutEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  const log = useCallback((msg: string) => {
    console.debug("[RestartSection]", msg);
    setDebugLog(prev => [{ ts: Date.now(), msg }, ...prev].slice(0, 50));
  }, []);

  const flashTooltip = useCallback((msg: string) => {
    setTooltip(msg);
    setTimeout(() => setTooltip(null), 4000);
  }, []);

  const addCheck = useCallback((result: CheckResult) => {
    setChecks(prev => [...prev, result]);
    if (!result.ok) {
      flashTooltip(`${result.check}: ${result.message}`);
      log(`FAIL [${result.check}]: ${result.message}`);
    } else {
      log(`OK   [${result.check}]: ${result.message}`);
    }
  }, [flashTooltip, log]);

  const finishChecks = useCallback((results: CheckResult[]) => {
    setPhase("done");
    phaseRef.current = "done";
    const failed = results.filter(r => !r.ok);
    if (failed.length === 0) {
      log("All health checks passed.");
    } else {
      log(`${failed.length} check(s) failed.`);
    }
  }, [log]);

  // Run health checks once WS reconnects and snapshot arrives
  const runChecks = useCallback(async (snap: Snapshot) => {
    if (checksRunRef.current) return;
    checksRunRef.current = true;

    const results: CheckResult[] = [];

    // 1. HTTP /health
    setStepLabel("Checking HTTP /health…");
    log("Running /health check…");
    try {
      const ctrl = new AbortController();
      const timeout = setTimeout(() => ctrl.abort(), 5000);
      const res = await fetch("/health", { signal: ctrl.signal });
      clearTimeout(timeout);
      const data = await res.json() as { ok?: boolean; service?: string };
      const ok = data.ok === true;
      const r: CheckResult = { check: "HTTP /health", ok, message: ok ? `service=${data.service ?? "ok"}` : `unexpected: ${JSON.stringify(data)}`, ts: Date.now() };
      results.push(r);
      addCheck(r);
    } catch (e) {
      const r: CheckResult = { check: "HTTP /health", ok: false, message: e instanceof Error ? e.message : String(e), ts: Date.now() };
      results.push(r);
      addCheck(r);
    }

    // 2. WS connected
    setStepLabel("Verifying WebSocket…");
    {
      const r: CheckResult = { check: "WebSocket", ok: true, message: "connected", ts: Date.now() };
      results.push(r);
      addCheck(r);
    }

    // 3. Snapshot received
    setStepLabel("Validating snapshot…");
    {
      const pairCount = Object.keys(snap.pairs ?? {}).length;
      const ok = pairCount > 0;
      const r: CheckResult = {
        check: "Snapshot / pairs",
        ok,
        message: ok ? `${pairCount} pair(s) loaded` : "no pairs in snapshot — check config",
        ts: Date.now(),
      };
      results.push(r);
      addCheck(r);
    }

    // 4. Scalp runtime (if enabled)
    if (scalpEnabled) {
      setStepLabel("Checking scalp runtime…");
      const attached = snap.scalp?.runtime_attached !== false;
      const warmupOk = snap.scalp != null;
      const ok = attached && warmupOk;
      const r: CheckResult = {
        check: "Scalp runtime",
        ok,
        message: ok
          ? `attached, warmup=${snap.scalp?.warmup?.phase ?? "—"}`
          : !warmupOk
            ? "scalp missing from snapshot"
            : "runtime not yet attached (may still be initializing)",
        ts: Date.now(),
      };
      results.push(r);
      addCheck(r);
    }

    // 5. Engine mode
    setStepLabel("Checking engine mode…");
    {
      const mode = snap.mode;
      const ok = mode === "paper" || mode === "live";
      const r: CheckResult = {
        check: "Engine mode",
        ok,
        message: ok ? `mode=${mode}` : `unexpected mode: ${mode ?? "(none)"}`,
        ts: Date.now(),
      };
      results.push(r);
      addCheck(r);
    }

    setStepLabel("");
    finishChecks(results);
  }, [scalpEnabled, addCheck, finishChecks, log]);

  // Watch: WS reconnects while restarting → move to "checking"
  useEffect(() => {
    if (phaseRef.current !== "restarting" || !connected) return undefined;
    const id = window.setTimeout(() => {
      log("WS reconnected — moving to health checks");
      setPhase("checking");
      setStepLabel("WebSocket reconnected…");
      if (restartDeadlineRef.current) {
        clearTimeout(restartDeadlineRef.current);
        restartDeadlineRef.current = null;
      }
    }, 0);
    return () => window.clearTimeout(id);
  }, [connected, log]);

  // Watch: snapshot arrives while checking
  useEffect(() => {
    if (phase !== "checking" || snapshot == null || snapshotReceivedRef.current) return undefined;
    const id = window.setTimeout(() => {
      snapshotReceivedRef.current = true;
      log("Snapshot received — starting checks");
      runChecks(snapshot);
    }, 0);
    return () => window.clearTimeout(id);
  }, [phase, snapshot, runChecks, log]);

  function handleRestartClick() {
    if (phase === "idle") {
      setPhase("confirm");
      phaseRef.current = "confirm";
      // Auto-cancel confirm after 4s
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
      confirmTimerRef.current = setTimeout(() => {
        if (phaseRef.current === "confirm") {
          setPhase("idle");
          phaseRef.current = "idle";
        }
      }, 4000);
    } else if (phase === "confirm") {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
      // Reset check state
      setChecks([]);
      setDebugLog([]);
      snapshotReceivedRef.current = false;
      checksRunRef.current = false;
      setStepLabel("Sending restart command…");
      log("Restart confirmed — sending restart_process");
      setPhase("restarting");
      phaseRef.current = "restarting";
      // Send restart — backend will execv, WS will drop
      send({ action: "restart_process" });
      // Safety deadline: if WS doesn't reconnect within 20s, show timeout error
      restartDeadlineRef.current = setTimeout(() => {
        if (phaseRef.current === "restarting" || phaseRef.current === "checking") {
          log("Restart deadline exceeded (20s)");
          const r: CheckResult = { check: "Reconnect timeout", ok: false, message: "Backend did not reconnect within 20s", ts: Date.now() };
          setChecks(prev => [...prev, r]);
          flashTooltip("Restart timeout — backend may have crashed");
          setPhase("done");
          phaseRef.current = "done";
        }
      }, 20000);
    }
  }

  function handleReset() {
    setPhase("idle");
    phaseRef.current = "idle";
    setChecks([]);
    setStepLabel("");
    snapshotReceivedRef.current = false;
    checksRunRef.current = false;
    if (restartDeadlineRef.current) clearTimeout(restartDeadlineRef.current);
  }

  const allOk = checks.length > 0 && checks.every(r => r.ok);
  const failCount = checks.filter(r => !r.ok).length;

  return (
    <section className="settings-card restart-card">
      <h2>Process restart</h2>
      <p className="settings-prose">
        Restarts the <strong>Python backend only</strong> (config reloads from disk). The
        Vite dev UI is unchanged; the WebSocket drops briefly and should reconnect in ~1–2s,
        then this panel runs health checks. If you use built static assets served from the
        backend, refresh the browser after restart. Errors flash as tooltips and are logged
        below.
      </p>

      <div className="restart-btn-area">
        {phase === "idle" && (
          <button
            type="button"
            className="settings-btn restart-btn"
            disabled={!connected}
            onClick={handleRestartClick}
            title={connected ? "Restart backend process" : "Not connected"}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14, verticalAlign: "middle", marginRight: 6 }}>
              restart_alt
            </span>
            RESTART BOT
          </button>
        )}

        {phase === "confirm" && (
          <div className="restart-confirm-row">
            <span className="restart-confirm-label">Click again to confirm restart</span>
            <button
              type="button"
              className="settings-btn restart-btn confirm-active"
              onClick={handleRestartClick}
            >
              CONFIRM RESTART
            </button>
            <button
              type="button"
              className="settings-btn"
              onClick={() => { setPhase("idle"); if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current); }}
            >
              Cancel
            </button>
          </div>
        )}

        {(phase === "restarting" || phase === "checking") && (
          <div className="restart-progress">
            <span className="restart-spinner" />
            <span className="restart-step-label">
              {phase === "restarting" ? "Waiting for backend to restart…" : stepLabel || "Running health checks…"}
            </span>
          </div>
        )}

        {phase === "done" && (
          <div className="restart-done-row">
            <span className={`restart-done-badge ${allOk ? "ok" : "fail"}`}>
              {allOk ? "✓ All checks passed" : `✕ ${failCount} check(s) failed`}
            </span>
            <button type="button" className="settings-btn" onClick={handleReset}>
              Reset
            </button>
          </div>
        )}

        {/* Flashing tooltip */}
        {tooltip && (
          <div className="restart-tooltip" role="alert">
            <span className="material-symbols-outlined" style={{ fontSize: 13, verticalAlign: "middle", marginRight: 5 }}>error</span>
            {tooltip}
          </div>
        )}
      </div>

      {/* Check results */}
      {checks.length > 0 && (
        <div className="restart-checks">
          {checks.map((c, i) => (
            <div key={i} className={`restart-check-row ${c.ok ? "check-ok" : "check-fail"}`}>
              <span className="check-icon">{c.ok ? "✓" : "✕"}</span>
              <span className="check-name">{c.check}</span>
              <span className="check-msg">{c.message}</span>
              <span className="check-ts">{fmtTs(c.ts)}</span>
            </div>
          ))}
          {(phase === "restarting" || phase === "checking") && stepLabel && (
            <div className="restart-check-row check-pending">
              <span className="check-icon">…</span>
              <span className="check-name">{stepLabel}</span>
              <span className="check-msg" />
              <span className="check-ts" />
            </div>
          )}
        </div>
      )}

      {/* Debug log (collapsed by default) */}
      {debugLog.length > 0 && (
        <details className="restart-debug">
          <summary className="restart-debug-summary">Debug log ({debugLog.length})</summary>
          <div className="restart-debug-body">
            {debugLog.map((e, i) => (
              <div key={i} className="restart-debug-line">
                <span className="restart-debug-ts">{fmtTs(e.ts)}</span>
                <span>{e.msg}</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

type DashboardRebuildPhase = "idle" | "confirm" | "building";

function DashboardRebuildSection({
  send,
  connected,
}: {
  send: (payload: Record<string, unknown>) => void;
  connected: boolean;
}) {
  const [phase, setPhase] = useState<DashboardRebuildPhase>("idle");
  const [logTail, setLogTail] = useState<string | null>(null);
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const phaseRef = useRef<DashboardRebuildPhase>("idle");

  useLayoutEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    const onResult = (ev: Event) => {
      const ce = ev as CustomEvent<{ ok: boolean; detail: string }>;
      const { ok, detail } = ce.detail ?? { ok: false, detail: "" };
      if (phaseRef.current !== "building") return;
      setPhase("idle");
      setLogTail(ok ? null : detail.slice(-4000));
      if (!ok && detail) {
        console.warn("[dashboard rebuild]", detail);
      }
    };
    window.addEventListener("dashboard-rebuild-result", onResult);
    return () => window.removeEventListener("dashboard-rebuild-result", onResult);
  }, []);

  function handleClick() {
    if (phase === "idle") {
      setPhase("confirm");
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
      confirmTimerRef.current = setTimeout(() => {
        if (phaseRef.current === "confirm") setPhase("idle");
      }, 8000);
    } else if (phase === "confirm") {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
      setPhase("building");
      send({ action: "rebuild_frontend_dist" });
    }
  }

  function handleCancel() {
    if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    setPhase("idle");
  }

  return (
    <section className="settings-card restart-card">
      <h2>Dashboard static bundle (dist)</h2>
      <p className="settings-prose">
        Runs <code>npm run build</code> in <code>frontend-new</code> on the machine where the Python backend runs.
        Does <strong>not</strong> restart the bot. After a success, do a <strong>hard refresh</strong>{" "}
        (Ctrl+Shift+R) so the browser loads the new <code>dist</code> assets. Requires <strong>Node.js / npm</strong> on
        PATH for that process.
      </p>
      <div className="restart-btn-area">
        {phase === "idle" && (
          <button
            type="button"
            className="settings-btn"
            disabled={!connected}
            onClick={handleClick}
            title={connected ? "Rebuild frontend-new/dist" : "Not connected"}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14, verticalAlign: "middle", marginRight: 6 }}>
              deployed_code
            </span>
            REBUILD DASHBOARD (DIST)
          </button>
        )}
        {phase === "confirm" && (
          <div className="restart-confirm-row">
            <span className="restart-confirm-label">Confirm: run npm build (30–90s typical)</span>
            <button type="button" className="settings-btn restart-btn confirm-active" onClick={handleClick}>
              CONFIRM BUILD
            </button>
            <button type="button" className="settings-btn" onClick={handleCancel}>
              Cancel
            </button>
          </div>
        )}
        {phase === "building" && (
          <div className="restart-progress">
            <span className="restart-spinner" />
            <span className="restart-step-label">npm run build… (see server terminal for full log)</span>
          </div>
        )}
      </div>
      {logTail ? (
        <details className="restart-debug" style={{ marginTop: 10 }}>
          <summary className="restart-debug-summary">Last build output (error)</summary>
          <pre className="restart-debug-body" style={{ whiteSpace: "pre-wrap", fontSize: 10, margin: 0 }}>
            {logTail}
          </pre>
        </details>
      ) : null}
    </section>
  );
}

// ── Startup Phase Display ────────────────────────────────────────────────────

const PHASE_LABELS: Record<string, string> = {
  standby:    "STANDBY — press Begin Warmup to prime the engine",
  warming_up: "WARMING UP — walk-forward optimization in progress",
  primed:     "PRIMED — click Go Live to begin trading",
  live:       "LIVE — entries enabled",
};

function StartupPhaseDisplay({
  phase,
  steps,
  canBeginWarmup,
  canGoLive,
  scalpEnabled,
  attached,
  prepBusy,
  send,
}: {
  phase: string;
  steps: WarmupStepData[];
  canBeginWarmup: boolean;
  canGoLive: boolean;
  scalpEnabled: boolean;
  attached: boolean;
  prepBusy: boolean;
  send: (p: Record<string, unknown>) => void;
}) {
  const pillClass =
    phase === "live"       ? "standby-off" :
    phase === "primed"     ? "pill-primed"  :
    phase === "warming_up" ? "pill-warming" :
                             "standby-on";

  const statusIcon = (s: WarmupStepData) => {
    if (s.status === "done")    return <span className="ws-icon done">✓</span>;
    if (s.status === "failed")  return <span className="ws-icon failed">✕</span>;
    if (s.status === "running") return <span className="ws-icon running">◎</span>;
    return <span className="ws-icon pending">○</span>;
  };

  return (
    <>
      <div className={`settings-pill ${pillClass}`}>
        {prepBusy ? "PREP RUNNING…" : (PHASE_LABELS[phase] ?? phase.toUpperCase())}
      </div>

      {/* Warmup steps progress (shown during warming_up or after) */}
      {steps.length > 0 && (
        <div className="startup-steps">
          {steps.map((s) => (
            <div key={s.key} className={`startup-step ss-${s.status}`}>
              {statusIcon(s)}
              <span className="ss-label">{s.label}</span>
              <div className="ss-track">
                <div className="ss-fill" style={{ width: `${Math.min(100, s.pct)}%` }} />
              </div>
              <span className="ss-pct">{Math.round(s.pct)}%</span>
              {s.detail && <span className="ss-detail">{s.detail}</span>}
              {s.status === "failed" && s.error && (
                <span className="ss-error">{s.error.slice(0, 120)}</span>
              )}
              {s.retry_count > 0 && (
                <span className="ss-retry">retry #{s.retry_count}</span>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="settings-btn-row">
        <button
          type="button"
          className="settings-btn primary"
          disabled={!scalpEnabled || !attached || !canBeginWarmup || prepBusy}
          onClick={() => send({ action: "scalp_begin_warmup" })}
        >
          Begin Warmup
        </button>
        <button
          type="button"
          className="settings-btn danger-outline"
          disabled={!scalpEnabled || !attached || phase === "standby" || phase === "warming_up"}
          onClick={() => send({ action: "scalp_operator_standby" })}
        >
          Enter Standby
        </button>
        <button
          type="button"
          className={`settings-btn success${canGoLive ? " go-live-ready" : ""}`}
          disabled={!scalpEnabled || !attached || !canGoLive || prepBusy}
          onClick={() => send({ action: "scalp_operator_go_live" })}
          title={!canGoLive ? "Complete warmup first" : "Arm the engine for live trading"}
        >
          Go Live
        </button>
      </div>
    </>
  );
}

// ── System Health Tile ────────────────────────────────────────────────────────

type HealthStatus = "ok" | "warn" | "fail" | "off";

function HealthRow({ label, status, detail }: { label: string; status: HealthStatus; detail: string }) {
  const dot =
    status === "ok"   ? "●" :
    status === "warn" ? "◉" :
    status === "fail" ? "✕" : "○";
  return (
    <div className={`health-row health-${status}`}>
      <span className="health-dot">{dot}</span>
      <span className="health-label">{label}</span>
      <span className="health-detail">{detail}</span>
    </div>
  );
}

function wfoPolicySignature(pol: ScalpSessionPolicy | undefined): string {
  if (!pol) return "";
  return [
    pol.wfo_interval_sec ?? "",
    pol.param_tuner_interval_sec ?? "",
    pol.wfo_max_roll_windows ?? "",
    pol.wfo_top_k ?? "",
    pol.wfo_train_same_calendar_day_boost ?? "",
    pol.wfo_train_hours,
    pol.wfo_holdout_hours,
    pol.wfo_step_hours,
    pol.wfo_min_trades ?? "",
    pol.wfo_min_holdout_trades ?? "",
    pol.backtest_funding_enabled === true ? "1" : "0",
    pol.backtest_funding_bps_per_hour ?? "",
    pol.scalp_fee_assumption_revision ?? "",
    pol.fee_tier_30d_volume_usd ?? "",
    pol.fee_tier_volume_source ?? "",
    pol.fee_tier_poll_interval_sec ?? "",
    pol.fee_tier_add_bot_fill_notional === true ? "1" : "0",
    pol.fee_tier_auto_apply_exchange_fee_rates === false ? "0" : "1",
    pol.scalp_auto_invalidate_champion_on_fee_change === true ? "1" : "0",
    pol.param_tuner_require_wfo_champion === true ? "1" : "0",
    pol.param_tuner_allow_mode_override_champion === true ? "1" : "0",
    pol.wfo_assume_taker_fee === true ? "1" : "0",
    pol.wfo_forward_min_trades ?? "",
    pol.wfo_forward_demotion_threshold ?? "",
    pol.funding_warn_bps_per_hour ?? "",
    pol.empirical_market_promotion_enabled === true ? "1" : "0",
    pol.empirical_market_ttl_cancel_arms_promotion === true ? "1" : "0",
  ].join("|");
}

function WfoTunerRuntimeSection({
  pol,
  scalp,
  send,
  connected,
  scalpEnabled,
  attached,
}: {
  pol: ScalpSessionPolicy | undefined;
  scalp: ScalpSnapshot | null;
  send: (payload: Record<string, unknown>) => void;
  connected: boolean;
  scalpEnabled: boolean;
  attached: boolean;
}) {
  const sig = wfoPolicySignature(pol);
  const venue = scalp?.venue ?? "";
  const ft = scalp?.fee_tier;
  const [wfo_interval_sec, setWfoIntervalSec] = useState("");
  const [param_tuner_interval_sec, setTunerIntervalSec] = useState("");
  const [wfo_max_roll_windows, setMaxRollWindows] = useState("");
  const [wfo_top_k, setTopK] = useState("");
  const [wfo_train_same_calendar_day_boost, setDayBoost] = useState("");
  const [wfo_train_hours, setTrainH] = useState("");
  const [wfo_holdout_hours, setHoldoutH] = useState("");
  const [wfo_step_hours, setStepH] = useState("");
  const [wfo_min_trades, setWfoMinTrades] = useState("");
  const [wfo_min_holdout_trades, setWfoMinHoldoutTrades] = useState("");
  const [backtest_funding_enabled, setBacktestFundingEnabled] = useState(false);
  const [backtest_funding_bps_per_hour, setBacktestFundingBps] = useState("");
  const [scalp_fee_assumption_revision, setFeeRevision] = useState("");
  const [fee_tier_volume_source, setFeeTierSource] = useState<"exchange" | "manual">("manual");
  const [fee_tier_poll_interval_sec, setFeeTierPollSec] = useState("");
  const [fee_tier_30d_volume_usd, setFeeTierVol] = useState("");
  const [fee_tier_add_bot_fill_notional, setFeeTierBotAdd] = useState(false);
  const [fee_tier_auto_apply_exchange_fee_rates, setFeeTierAutoApplyRates] = useState(true);
  const [scalp_auto_invalidate_champion_on_fee_change, setAutoInvFee] = useState(false);
  const [param_tuner_require_wfo_champion, setTunerRequireChamp] = useState(true);
  const [param_tuner_allow_mode_override_champion, setTunerOverride] = useState(false);
  const [wfo_assume_taker_fee, setWfoAssumeTaker] = useState(false);
  const [wfo_forward_min_trades, setWfoForwardMinTrades] = useState("");
  const [wfo_forward_demotion_threshold, setWfoForwardDemotion] = useState("");
  const [funding_warn_bps_per_hour, setFundingWarnBps] = useState("");
  const [empirical_market_promotion_enabled, setEmpiricalPromo] = useState(false);
  const [empirical_market_ttl_cancel_arms_promotion, setEmpiricalTtlCancelArm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feeRefreshBusy, setFeeRefreshBusy] = useState(false);
  const [localMsg, setLocalMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!pol) return;
    const id = requestAnimationFrame(() => {
      setWfoIntervalSec(String(pol.wfo_interval_sec ?? 900));
      setTunerIntervalSec(String(pol.param_tuner_interval_sec ?? 120));
      setMaxRollWindows(String(pol.wfo_max_roll_windows ?? 12));
      setTopK(String(pol.wfo_top_k ?? 50));
      setDayBoost(String(pol.wfo_train_same_calendar_day_boost ?? 0));
      setTrainH(String(pol.wfo_train_hours));
      setHoldoutH(String(pol.wfo_holdout_hours));
      setStepH(String(pol.wfo_step_hours));
      setWfoMinTrades(String(pol.wfo_min_trades ?? 20));
      setWfoMinHoldoutTrades(String(pol.wfo_min_holdout_trades ?? 0));
      setBacktestFundingEnabled(pol.backtest_funding_enabled === true);
      setBacktestFundingBps(String(pol.backtest_funding_bps_per_hour ?? 0));
      setFeeRevision(String(pol.scalp_fee_assumption_revision ?? 0));
      const src = (pol.fee_tier_volume_source ?? "manual").toLowerCase();
      setFeeTierSource(src === "exchange" ? "exchange" : "manual");
      setFeeTierPollSec(String(pol.fee_tier_poll_interval_sec ?? 900));
      setFeeTierVol(
        pol.fee_tier_30d_volume_usd != null && Number.isFinite(pol.fee_tier_30d_volume_usd)
          ? String(pol.fee_tier_30d_volume_usd)
          : "",
      );
      setFeeTierBotAdd(pol.fee_tier_add_bot_fill_notional === true);
      setFeeTierAutoApplyRates(pol.fee_tier_auto_apply_exchange_fee_rates !== false);
      setAutoInvFee(pol.scalp_auto_invalidate_champion_on_fee_change === true);
      setTunerRequireChamp(pol.param_tuner_require_wfo_champion !== false);
      setTunerOverride(pol.param_tuner_allow_mode_override_champion === true);
      setWfoAssumeTaker(pol.wfo_assume_taker_fee === true);
      setWfoForwardMinTrades(String(pol.wfo_forward_min_trades ?? 10));
      setWfoForwardDemotion(String(pol.wfo_forward_demotion_threshold ?? -0.5));
      setFundingWarnBps(String(pol.funding_warn_bps_per_hour ?? 5));
      setEmpiricalPromo(pol.empirical_market_promotion_enabled === true);
      setEmpiricalTtlCancelArm(pol.empirical_market_ttl_cancel_arms_promotion === true);
    });
    return () => cancelAnimationFrame(id);
  }, [sig, pol]);

  const canEdit = connected && scalpEnabled && attached && pol != null;

  const parseNum = (s: string, label: string): number | null => {
    const t = s.trim();
    if (t === "") {
      setLocalMsg(`${label} is empty`);
      return null;
    }
    const n = Number(t);
    if (!Number.isFinite(n)) {
      setLocalMsg(`${label} is not a valid number`);
      return null;
    }
    return n;
  };

  const apply = () => {
    setLocalMsg(null);
    if (!pol) return;
    const wi = parseNum(wfo_interval_sec, "WFO interval");
    if (wi == null) return;
    const pt = parseNum(param_tuner_interval_sec, "Tuner interval");
    if (pt == null) return;
    const mw = parseNum(wfo_max_roll_windows, "Max roll windows");
    if (mw == null) return;
    const tk = parseNum(wfo_top_k, "Top-K");
    if (tk == null) return;
    const db = parseNum(wfo_train_same_calendar_day_boost, "Same-day boost");
    if (db == null) return;
    const tr = parseNum(wfo_train_hours, "Train hours");
    if (tr == null) return;
    const ho = parseNum(wfo_holdout_hours, "Holdout hours");
    if (ho == null) return;
    const st = parseNum(wfo_step_hours, "Step hours");
    if (st == null) return;
    const minTr = parseNum(wfo_min_trades, "Min train trades");
    if (minTr == null) return;
    const minHo = parseNum(wfo_min_holdout_trades, "Min holdout trades (0 = same as train)");
    if (minHo == null) return;
    const fundBps = parseNum(backtest_funding_bps_per_hour, "Funding bps/hour");
    if (fundBps == null) return;
    const feeRev = parseNum(scalp_fee_assumption_revision, "Fee assumption revision");
    if (feeRev == null) return;
    const ftPoll = parseNum(fee_tier_poll_interval_sec, "Fee tier poll interval");
    if (ftPoll == null) return;
    if (ftPoll < 60 || ftPoll > 86_400) {
      setLocalMsg("Fee tier poll interval must be between 60 and 86400 seconds");
      return;
    }
    const fwdMin = parseNum(wfo_forward_min_trades, "Forward min trades");
    if (fwdMin == null) return;
    const fwdTh = parseNum(wfo_forward_demotion_threshold, "Forward demotion threshold");
    if (fwdTh == null) return;
    const fundWarn = parseNum(funding_warn_bps_per_hour, "Funding warn bps/hour");
    if (fundWarn == null) return;

    setBusy(true);
    send({
      action: "update_scalp_session_policy",
      patch: {
        wfo_interval_sec: wi,
        param_tuner_interval_sec: pt,
        wfo_max_roll_windows: Math.round(mw),
        wfo_top_k: Math.round(tk),
        wfo_train_same_calendar_day_boost: db,
        wfo_train_hours: tr,
        wfo_holdout_hours: ho,
        wfo_step_hours: st,
        wfo_min_trades: Math.round(minTr),
        wfo_min_holdout_trades: Math.round(minHo),
        backtest_funding_enabled: backtest_funding_enabled,
        backtest_funding_bps_per_hour: fundBps,
        scalp_fee_assumption_revision: Math.round(feeRev),
        fee_tier_volume_source: fee_tier_volume_source,
        fee_tier_poll_interval_sec: ftPoll,
        fee_tier_30d_volume_usd:
          fee_tier_30d_volume_usd.trim() === "" ? null : Number(fee_tier_30d_volume_usd.trim()),
        fee_tier_add_bot_fill_notional: fee_tier_add_bot_fill_notional,
        fee_tier_auto_apply_exchange_fee_rates: fee_tier_auto_apply_exchange_fee_rates,
        scalp_auto_invalidate_champion_on_fee_change: scalp_auto_invalidate_champion_on_fee_change,
        param_tuner_require_wfo_champion: param_tuner_require_wfo_champion,
        param_tuner_allow_mode_override_champion: param_tuner_allow_mode_override_champion,
        wfo_assume_taker_fee: wfo_assume_taker_fee,
        wfo_forward_min_trades: Math.round(fwdMin),
        wfo_forward_demotion_threshold: fwdTh,
        funding_warn_bps_per_hour: fundWarn,
        empirical_market_promotion_enabled: empirical_market_promotion_enabled,
        empirical_market_ttl_cancel_arms_promotion: empirical_market_ttl_cancel_arms_promotion,
      },
    });
    window.setTimeout(() => setBusy(false), 400);
  };

  const refreshFeeTier = () => {
    setFeeRefreshBusy(true);
    send({ action: "scalp_refresh_fee_tier" });
    window.setTimeout(() => setFeeRefreshBusy(false), 2500);
  };

  const fmtUsd = (n: number | null | undefined) =>
    n != null && Number.isFinite(n) ? `$${Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—";

  const fmtPollWall = (unixSec: number) => {
    if (!unixSec || unixSec <= 0) return "—";
    return new Date(unixSec * 1000).toLocaleString(undefined, {
      dateStyle: "short",
      timeStyle: "medium",
    });
  };

  if (!pol) {
    return (
      <section className="settings-card">
        <h2>WFO &amp; param tuner (runtime)</h2>
        <p className="settings-warn">Connect to the server with scalp enabled to edit these fields.</p>
      </section>
    );
  }

  return (
    <section className="settings-card">
      <h2>WFO &amp; param tuner (runtime)</h2>
      <p className="settings-prose">
        These values drive walk-forward optimization and the fine param tuner. Changes apply{" "}
        <strong>in memory only</strong> — restart reloads <code className="settings-code">config.toml</code>.
        Larger windows / higher Top-K / more folds use more CPU per pass.{" "}
        <span title="Hover labels and inputs for RECOMMENDED ranges, benefits, and risks.">
          Hover controls for full guidance.
        </span>
      </p>

      <div className="settings-form-field">
        <label htmlFor="wfo_interval_sec" title={SCALP_WFO_TT.wfo_interval}>
          WFO interval (seconds)
        </label>
        <p className="settings-explainer">
          Minimum time between scheduled WFO passes (server enforces at least 60s). Lower = fresher
          champions after each candle batch; uses more CPU.
        </p>
        <input
          id="wfo_interval_sec"
          type="number"
          min={60}
          step={1}
          value={wfo_interval_sec}
          onChange={(e) => setWfoIntervalSec(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.wfo_interval}
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="param_tuner_interval_sec" title={SCALP_WFO_TT.param_tuner_interval}>
          Param tuner interval (seconds)
        </label>
        <p className="settings-explainer">
          How often the tuner runs a local perturbation pass on the active mode (and across modes only when
          champion override is enabled). Floor 30s.
        </p>
        <input
          id="param_tuner_interval_sec"
          type="number"
          min={30}
          step={1}
          value={param_tuner_interval_sec}
          onChange={(e) => setTunerIntervalSec(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.param_tuner_interval}
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="wfo_max_roll_windows" title={SCALP_WFO_TT.wfo_max_roll_windows}>
          Max rolling windows
        </label>
        <p className="settings-explainer">
          Number of overlapping train→holdout folds kept in the sliding band behind the latest bar.
          More folds = stabler scoring across regimes; longer bar backfill span (see derived hours below).
        </p>
        <input
          id="wfo_max_roll_windows"
          type="number"
          min={1}
          max={200}
          step={1}
          value={wfo_max_roll_windows}
          onChange={(e) => setMaxRollWindows(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.wfo_max_roll_windows}
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="wfo_top_k" title={SCALP_WFO_TT.wfo_top_k}>
          Holdout Top-K
        </label>
        <p className="settings-explainer">
          After scoring the full grid on each training slice, only the top K candidates are simulated on
          holdout. Higher K = more CPU but less chance a good mode is dropped before OOS validation.
        </p>
        <input
          id="wfo_top_k"
          type="number"
          min={1}
          max={300}
          step={1}
          value={wfo_top_k}
          onChange={(e) => setTopK(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.wfo_top_k}
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="wfo_train_same_calendar_day_boost" title={SCALP_WFO_TT.wfo_train_same_calendar_day_boost}>
          Train same-day boost
        </label>
        <p className="settings-explainer">
          Extra weight (0 = off) on training trades whose entry falls on the same UTC calendar day as the
          end of the train window—nudges the grid toward very recent tape when ranking train scores.
        </p>
        <input
          id="wfo_train_same_calendar_day_boost"
          type="number"
          min={0}
          max={3}
          step={0.05}
          value={wfo_train_same_calendar_day_boost}
          onChange={(e) => setDayBoost(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.wfo_train_same_calendar_day_boost}
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="wfo_train_hours" title={SCALP_WFO_TT.wfo_train_hours}>
          Train window (hours)
        </label>
        <p className="settings-explainer">In-sample length for each fold (wall-clock hours of bars, not bar count).</p>
        <input
          id="wfo_train_hours"
          type="number"
          min={0.5}
          step={0.5}
          value={wfo_train_hours}
          onChange={(e) => setTrainH(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.wfo_train_hours}
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="wfo_holdout_hours" title={SCALP_WFO_TT.wfo_holdout_hours}>
          Holdout window (hours)
        </label>
        <p className="settings-explainer">
          Out-of-sample segment after each train slice used to score and gate champions.
        </p>
        <input
          id="wfo_holdout_hours"
          type="number"
          min={0.5}
          step={0.5}
          value={wfo_holdout_hours}
          onChange={(e) => setHoldoutH(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.wfo_holdout_hours}
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="wfo_step_hours" title={SCALP_WFO_TT.wfo_step_hours}>
          Roll step (hours)
        </label>
        <p className="settings-explainer">
          How far each fold slides back in time. Set very large (≥ train+holdout) for a single fold only.
        </p>
        <input
          id="wfo_step_hours"
          type="number"
          min={0.25}
          step={0.25}
          value={wfo_step_hours}
          onChange={(e) => setStepH(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.wfo_step_hours}
        />
      </div>

      <div className="settings-form-field">
        <span className="settings-k" title={SCALP_WFO_TT.wfo_objective}>
          WFO objective (read-only)
        </span>
        <div className="settings-readonly" title={SCALP_WFO_TT.wfo_objective}>
          {pol.wfo_objective ?? "—"}
          {pol.wfo_pnl_first_promotion ? (
            <span className="settings-muted" title={SCALP_WFO_TT.wfo_pnl_first_promotion}>
              {" "}
              (PnL-first promotion)
            </span>
          ) : null}
        </div>
        <p className="settings-explainer" style={{ marginTop: 6 }}>
          Champion ranking metric. Change in <code className="settings-code">config.toml</code>{" "}
          (<code className="settings-code">wfo_objective</code>) and restart to switch.
        </p>
      </div>

      <div className="settings-form-field">
        <span className="settings-k" title={SCALP_WFO_TT.wfo_roll_span}>
          Rolling bar span (read-only)
        </span>
        <div className="settings-readonly" title={SCALP_WFO_TT.wfo_roll_span}>
          {pol.wfo_roll_span_hours != null ? `${pol.wfo_roll_span_hours.toFixed(1)}h` : "—"}
        </div>
        <p className="settings-explainer" style={{ marginTop: 6 }}>
          Approximate hours of tape loaded for rolling WFO: train + holdout + (windows−1)×step (+ margin).
        </p>
      </div>

      <div className="settings-form-field">
        <span className="settings-k">Train IS gates (read-only)</span>
        <div className="settings-readonly">
          PF≥{pol.wfo_min_profit_factor ?? 0.8} · WR≥{((pol.wfo_min_win_rate ?? 0.2) * 100).toFixed(0)}% · max
          DD≤{pol.wfo_max_train_drawdown_pct ?? 30}%
        </div>
        <p className="settings-explainer" style={{ marginTop: 6 }}>
          In-sample hard gates on each train slice. Set{" "}
          <code className="settings-code">wfo_min_profit_factor</code>,{" "}
          <code className="settings-code">wfo_min_win_rate</code>,{" "}
          <code className="settings-code">wfo_max_train_drawdown_pct</code> in{" "}
          <code className="settings-code">config.toml</code> and restart (not a runtime patch).
        </p>
      </div>

      <div className="settings-form-field">
        <span className="settings-k" title={SCALP_WFO_TT.wfo_action_log}>
          WFO pass log (read-only)
        </span>
        <textarea
          className="settings-wfo-log"
          readOnly
          rows={8}
          value={scalp?.wfo?.wfo_action_log?.trim() ? scalp.wfo.wfo_action_log : "(no WFO activity yet)"}
          title={SCALP_WFO_TT.wfo_action_log}
        />
        {scalp?.wfo?.last_wfo_pass ? (
          <p className="settings-explainer" style={{ marginTop: 6 }}>
            Last pass: {scalp.wfo.last_wfo_pass.champion_count}/{scalp.wfo.last_wfo_pass.n_pairs} champions
            {Object.keys(scalp.wfo.last_wfo_pass.by_skip_reason ?? {}).length > 0
              ? ` · skips ${JSON.stringify(scalp.wfo.last_wfo_pass.by_skip_reason)}`
              : ""}
          </p>
        ) : null}
      </div>

      <h3 className="settings-subh">Train / holdout trade gates</h3>

      <div className="settings-form-field">
        <label htmlFor="wfo_min_trades" title={SCALP_WFO_TT.wfo_min_trades}>
          Min trades (train slice)
        </label>
        <p className="settings-explainer">
          Each rolling <strong>train</strong> window must contain at least this many closed trades for a grid
          candidate to score. Higher = less noise, but sparse strategies may never qualify.
        </p>
        <BenefitRisk
          benefit="Fewer spurious grid winners on thin sample sizes."
          risk="Can reject all modes in quiet tape; WFO finds no champion until you lower this or widen train hours."
        />
        <input
          id="wfo_min_trades"
          type="number"
          min={1}
          max={500}
          step={1}
          value={wfo_min_trades}
          onChange={(e) => setWfoMinTrades(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.wfo_min_trades}
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="wfo_min_holdout_trades" title={SCALP_WFO_TT.wfo_min_holdout_trades}>
          Min trades (holdout slice)
        </label>
        <p className="settings-explainer">
          <strong>0</strong> = use the same floor as train. Set <strong>lower</strong> than train so short holdout
          windows are not discarded just because the clock window is small.
        </p>
        <BenefitRisk
          benefit="More Top-K candidates survive OOS validation when holdout hours are tight (less 'frequency pressure')."
          risk="Holdout evidence is thinner; easier for a lucky window to pass gates—pair with stricter PF / PnL gates if needed."
        />
        <input
          id="wfo_min_holdout_trades"
          type="number"
          min={0}
          max={500}
          step={1}
          value={wfo_min_holdout_trades}
          onChange={(e) => setWfoMinHoldoutTrades(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.wfo_min_holdout_trades}
        />
      </div>

      <h3 className="settings-subh">Backtest funding (WFO + tuner)</h3>

      <div className="settings-form-field">
        <div className="settings-checkbox-row">
          <input
            id="backtest_funding_enabled"
            type="checkbox"
            checked={backtest_funding_enabled}
            onChange={(e) => setBacktestFundingEnabled(e.target.checked)}
            disabled={!canEdit}
            title={SCALP_WFO_TT.backtest_funding_enabled}
          />
          <label htmlFor="backtest_funding_enabled" title={SCALP_WFO_TT.backtest_funding_enabled}>
            Enable constant funding in bar simulator (perps)
          </label>
        </div>
        <p className="settings-explainer">
          Applies a flat <strong>signed bps/hour</strong> on notional for each bar a position is open. Positive
          rate = longs pay / shorts receive (typical positive-funding convention).
        </p>
        <BenefitRisk
          benefit="Backtests align closer with perp reality when carry is material."
          risk="Wrong rate or sign biases champions; start small and compare to venue funding polls."
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="backtest_funding_bps_per_hour" title={SCALP_WFO_TT.backtest_funding_bps}>
          Funding (bps per hour)
        </label>
        <input
          id="backtest_funding_bps_per_hour"
          type="number"
          min={-200}
          max={200}
          step={0.1}
          value={backtest_funding_bps_per_hour}
          onChange={(e) => setBacktestFundingBps(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.backtest_funding_bps}
        />
      </div>

      <h3 className="settings-subh">Fee assumptions &amp; tier tracking</h3>

      <div className="settings-form-field">
        <label htmlFor="scalp_fee_assumption_revision" title={SCALP_WFO_TT.fee_assumption_revision}>
          Fee assumption revision (integer)
        </label>
        <p className="settings-explainer">
          Bump this when you change maker/taker bps, USD/leg, or <code className="settings-code">order_type</code> in
          config so the on-disk snapshot and logs show a deliberate tier change. After each WFO pass the server
          refreshes <code className="settings-code">data/scalp_fee_assumption_state.json</code>.
        </p>
        <BenefitRisk
          benefit="Clear audit trail for when backtest fees stopped matching live tier."
          risk="Forgetting to bump after a real tier change can leave stale champions until the next WFO."
        />
        <input
          id="scalp_fee_assumption_revision"
          type="number"
          min={0}
          step={1}
          value={scalp_fee_assumption_revision}
          onChange={(e) => setFeeRevision(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.fee_assumption_revision}
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="fee_tier_volume_source" title={SCALP_WFO_TT.fee_tier_volume_source}>
          30d fee-tier volume source
        </label>
        <p className="settings-explainer">
          <strong>exchange</strong> polls Coinbase Advanced (perps only). <strong>manual</strong> uses the USD
          baseline below; optional session bot-notional add-on.
        </p>
        <select
          id="fee_tier_volume_source"
          value={fee_tier_volume_source}
          onChange={(e) => setFeeTierSource(e.target.value === "exchange" ? "exchange" : "manual")}
          disabled={!canEdit}
          title={SCALP_WFO_TT.fee_tier_volume_source}
        >
          <option value="exchange">exchange (poll Coinbase)</option>
          <option value="manual">manual (baseline USD)</option>
        </select>
        {venue !== "coinbase_perps" && fee_tier_volume_source === "exchange" && (
          <p className="settings-warn" style={{ marginTop: 8 }}>
            Volume polling only runs on <code className="settings-code">coinbase_perps</code>. With the current
            venue, use <strong>manual</strong> or switch venue in config and restart.
          </p>
        )}
      </div>

      <div className="settings-form-field">
        <div className="settings-checkbox-row">
          <input
            id="fee_tier_auto_apply_exchange_fee_rates"
            type="checkbox"
            checked={fee_tier_auto_apply_exchange_fee_rates}
            onChange={(e) => setFeeTierAutoApplyRates(e.target.checked)}
            disabled={!canEdit}
            title={SCALP_WFO_TT.fee_tier_auto_apply_rates}
          />
          <label
            htmlFor="fee_tier_auto_apply_exchange_fee_rates"
            title={SCALP_WFO_TT.fee_tier_auto_apply_rates}
          >
            Auto-apply exchange maker/taker bps from fee poll (WFO / bar sim / tuner)
          </label>
        </div>
        <p className="settings-explainer">
          When on with <strong>exchange</strong> volume source, each successful Coinbase{" "}
          <code className="settings-code">transaction_summary</code> poll can update in-memory{" "}
          <code className="settings-code">fee_bps_per_leg</code> /{" "}
          <code className="settings-code">fee_bps_taker_per_leg</code>.{" "}
          <code className="settings-code">config.toml</code> is not rewritten — restart reloads file values.
        </p>
      </div>

      <div className="settings-form-field">
        <label htmlFor="fee_tier_poll_interval_sec" title={SCALP_WFO_TT.fee_tier_poll_interval}>
          Fee-tier poll interval (seconds)
        </label>
        <p className="settings-explainer">Automatic REST poll cadence when source is exchange (60–86400).</p>
        <input
          id="fee_tier_poll_interval_sec"
          type="number"
          min={60}
          max={86400}
          step={1}
          value={fee_tier_poll_interval_sec}
          onChange={(e) => setFeeTierPollSec(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.fee_tier_poll_interval}
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="fee_tier_30d_volume_usd" title={SCALP_WFO_TT.fee_tier_30d_volume_usd}>
          Manual 30d volume baseline (USD, optional)
        </label>
        <p className="settings-explainer">
          When source is <strong>manual</strong>, set this to the trailing volume you see in Coinbase Advanced
          today (then bump fee revision when you edit it). When source is <strong>exchange</strong>, leave empty;
          display uses the poll.
        </p>
        <input
          id="fee_tier_30d_volume_usd"
          type="text"
          inputMode="decimal"
          placeholder="empty = not set"
          value={fee_tier_30d_volume_usd}
          onChange={(e) => setFeeTierVol(e.target.value)}
          disabled={!canEdit}
          className="settings-text-input"
          title={SCALP_WFO_TT.fee_tier_30d_volume_usd}
        />
      </div>

      <div className="settings-form-field">
        <div className="settings-checkbox-row">
          <input
            id="fee_tier_add_bot_fill_notional"
            type="checkbox"
            checked={fee_tier_add_bot_fill_notional}
            onChange={(e) => setFeeTierBotAdd(e.target.checked)}
            disabled={!canEdit}
            title={SCALP_WFO_TT.fee_tier_add_bot_fill}
          />
          <label htmlFor="fee_tier_add_bot_fill_notional" title={SCALP_WFO_TT.fee_tier_add_bot_fill}>
            Add this bot&apos;s session fill notional to manual baseline (display only)
          </label>
        </div>
      </div>

      <div className="settings-form-field" title={SCALP_WFO_TT.fee_tier_live_snapshot}>
        <span className="settings-k">Live fee-tier snapshot</span>
        <div className="settings-readonly">
          Display {fmtUsd(ft?.display_volume_usd ?? null)} · source {ft?.volume_source ?? "—"} · baseline{" "}
          {fmtUsd(ft?.manual_baseline_usd ?? null)} · bot session +
          {ft?.bot_fill_usd_session != null && Number.isFinite(ft.bot_fill_usd_session)
            ? ` $${ft.bot_fill_usd_session.toFixed(2)}`
            : " —"}{" "}
          · eff maker {ft?.effective_maker_bps != null ? `${ft.effective_maker_bps}` : "—"} bps · eff taker{" "}
          {ft?.effective_taker_bps != null ? `${ft.effective_taker_bps}` : "—"} bps · flat{" "}
          {ft?.fee_usd_per_contract_per_leg != null && Number.isFinite(ft.fee_usd_per_contract_per_leg)
            ? `$${ft.fee_usd_per_contract_per_leg.toFixed(2)}/contract/leg (config)`
            : "— (config)"}{" "}
          · auto-apply{" "}
          {ft?.auto_apply_exchange_fee_rates === false ? "off" : "on"} · last
          poll {fmtPollWall(ft?.last_poll_ts ?? 0)}
          {ft?.exchange && typeof (ft.exchange as { total_volume?: unknown }).total_volume !== "undefined"
            ? ` · exchange total_volume ${fmtUsd(Number((ft.exchange as { total_volume?: number }).total_volume))}`
            : ""}
        </div>
        {ft?.poll_error ? (
          <p className="settings-warn" style={{ marginTop: 6 }}>
            Poll error: {ft.poll_error}
          </p>
        ) : null}
        <div className="settings-inline-actions" style={{ marginTop: 10 }}>
          <button
            type="button"
            className="settings-btn"
            disabled={!canEdit || venue !== "coinbase_perps" || feeRefreshBusy}
            onClick={refreshFeeTier}
            title={SCALP_WFO_TT.fee_tier_refresh}
          >
            {feeRefreshBusy ? "Refreshing…" : "Refresh fee tier from exchange"}
          </button>
        </div>
      </div>

      <div className="settings-form-field">
        <div className="settings-checkbox-row">
          <input
            id="scalp_auto_invalidate_champion_on_fee_change"
            type="checkbox"
            checked={scalp_auto_invalidate_champion_on_fee_change}
            onChange={(e) => setAutoInvFee(e.target.checked)}
            disabled={!canEdit}
            title={SCALP_WFO_TT.fee_auto_invalidate}
          />
          <label
            htmlFor="scalp_auto_invalidate_champion_on_fee_change"
            title={SCALP_WFO_TT.fee_auto_invalidate}
          >
            Auto-remove champions on startup when fee snapshot ≠ config
          </label>
        </div>
        <p className="settings-explainer">
          On boot, compares persisted fee snapshot to current config; if different and this is on, champion rows
          are cleared so the next WFO re-selects under the new fee model.
        </p>
        <BenefitRisk
          benefit="Prevents trading off a champion optimized at an old fee tier after you change fees."
          risk="Surprise cold start: champions vanish until WFO runs again; can interrupt a session you expected to resume."
        />
      </div>

      <h3 className="settings-subh">Param tuner behavior</h3>

      <div className="settings-form-field">
        <div className="settings-checkbox-row">
          <input
            id="param_tuner_require_wfo_champion"
            type="checkbox"
            checked={param_tuner_require_wfo_champion}
            onChange={(e) => setTunerRequireChamp(e.target.checked)}
            disabled={!canEdit}
            title={SCALP_WFO_TT.param_tuner_require_champion}
          />
          <label htmlFor="param_tuner_require_wfo_champion" title={SCALP_WFO_TT.param_tuner_require_champion}>
            Require WFO champion before running param tuner
          </label>
        </div>
        <p className="settings-explainer">
          When on, the tuner is silent until a champion exists for the symbol; with a champion it tunes only the
          <strong> active</strong> strategy mode (unless override below is on).
        </p>
        <BenefitRisk
          benefit="No fine-tuning on random modes before WFO has anchored a coarse winner; avoids fighting WFO."
          risk="Disables the old Nemesis tuner-vs-bootstrap path before champion—bootstrap-only until WFO succeeds."
        />
      </div>

      <div className="settings-form-field">
        <div className="settings-checkbox-row">
          <input
            id="param_tuner_allow_mode_override_champion"
            type="checkbox"
            checked={param_tuner_allow_mode_override_champion}
            onChange={(e) => setTunerOverride(e.target.checked)}
            disabled={!canEdit}
            title={SCALP_WFO_TT.param_tuner_override}
          />
          <label htmlFor="param_tuner_allow_mode_override_champion" title={SCALP_WFO_TT.param_tuner_override}>
            Allow tuner to override WFO champion mode
          </label>
        </div>
        <p className="settings-explainer">
          Rare escape hatch: tuner may switch <code className="settings-code">active_mode</code> when it prefers a
          different mode on the lookback window. Champion JSON params may no longer match the live mode.
        </p>
        <BenefitRisk
          benefit="Can react quickly if the champion mode is clearly broken post-regime shift."
          risk="Splits authority between WFO and tuner; harder to reason about which layer owns mode selection."
        />
      </div>

      <h3 className="settings-subh">Forward validation, fees sim, funding, empirical hybrid</h3>

      <div className="settings-form-field">
        <div className="settings-checkbox-row">
          <input
            id="wfo_assume_taker_fee"
            type="checkbox"
            checked={wfo_assume_taker_fee}
            onChange={(e) => setWfoAssumeTaker(e.target.checked)}
            disabled={!canEdit}
            title={SCALP_WFO_TT.wfo_assume_taker_fee}
          />
          <label htmlFor="wfo_assume_taker_fee" title={SCALP_WFO_TT.wfo_assume_taker_fee}>
            WFO / tuner sim: assume taker fee per leg
          </label>
        </div>
        <p className="settings-explainer">
          Stress-test champions when live uses empirical market bursts; does not change live{" "}
          <code className="settings-code">order_type</code>.
        </p>
      </div>

      <div className="settings-form-field">
        <label htmlFor="wfo_forward_min_trades" title={SCALP_WFO_TT.wfo_forward_min_trades}>
          Forward demotion: min live trades
        </label>
        <input
          id="wfo_forward_min_trades"
          type="number"
          min={1}
          step={1}
          value={wfo_forward_min_trades}
          onChange={(e) => setWfoForwardMinTrades(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.wfo_forward_min_trades}
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="wfo_forward_demotion_threshold" title={SCALP_WFO_TT.wfo_forward_demotion_threshold}>
          Forward demotion threshold (ratio)
        </label>
        <input
          id="wfo_forward_demotion_threshold"
          type="number"
          step={0.05}
          value={wfo_forward_demotion_threshold}
          onChange={(e) => setWfoForwardDemotion(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.wfo_forward_demotion_threshold}
        />
      </div>

      <div className="settings-form-field">
        <label htmlFor="funding_warn_bps_per_hour" title={SCALP_WFO_TT.funding_warn_bps_per_hour}>
          Funding alert threshold (bps/hour, best-effort)
        </label>
        <input
          id="funding_warn_bps_per_hour"
          type="number"
          min={0}
          step={0.5}
          value={funding_warn_bps_per_hour}
          onChange={(e) => setFundingWarnBps(e.target.value)}
          disabled={!canEdit}
          title={SCALP_WFO_TT.funding_warn_bps_per_hour}
        />
      </div>

      <div className="settings-form-field">
        <div className="settings-checkbox-row">
          <input
            id="empirical_market_promotion_enabled"
            type="checkbox"
            checked={empirical_market_promotion_enabled}
            onChange={(e) => {
              const on = e.target.checked;
              setEmpiricalPromo(on);
              if (!on) setEmpiricalTtlCancelArm(false);
            }}
            disabled={!canEdit}
            title={SCALP_WFO_TT.empirical_market_promotion}
          />
          <label htmlFor="empirical_market_promotion_enabled" title={SCALP_WFO_TT.empirical_market_promotion}>
            Empirical limit→market promotion (TTL missed-move pattern)
          </label>
        </div>
      </div>

      <div className="settings-form-field">
        <div className="settings-checkbox-row">
          <input
            id="empirical_market_ttl_cancel_arms_promotion"
            type="checkbox"
            checked={empirical_market_ttl_cancel_arms_promotion}
            onChange={(e) => setEmpiricalTtlCancelArm(e.target.checked)}
            disabled={!canEdit || !empirical_market_promotion_enabled}
            title={SCALP_WFO_TT.empirical_market_ttl_cancel_arms_promotion}
          />
          <label
            htmlFor="empirical_market_ttl_cancel_arms_promotion"
            title={SCALP_WFO_TT.empirical_market_ttl_cancel_arms_promotion}
          >
            TTL cancel immediately arms market slots (bypass pattern cooldown)
          </label>
        </div>
      </div>

      {localMsg && <p className="settings-warn">{localMsg}</p>}

      <div className="settings-inline-actions">
        <button
          type="button"
          className="settings-btn primary"
          disabled={!canEdit || busy}
          onClick={apply}
          title={SCALP_WFO_TT.apply_runtime}
        >
          Apply runtime settings
        </button>
      </div>
    </section>
  );
}

function SystemHealthTile({ scalp }: { scalp: ScalpSnapshot | null; snapshot: Snapshot | null }) {
  if (!scalp) return <p className="settings-warn">Scalp engine offline.</p>;

  const op = scalp.operator;
  const warm = scalp.warmup;
  const phase = scalp.startup_phase ?? op?.startup_phase ?? "standby";
  const steps = op?.warmup_steps ?? warm?.startup_steps ?? [];

  const stepStatus = (key: string): HealthStatus => {
    const s = steps.find((x) => x.key === key);
    if (!s) return "off";
    if (s.status === "done") return "ok";
    if (s.status === "running") return "warn";
    if (s.status === "failed") return "fail";
    return "off";
  };
  const stepDetail = (key: string, fallback: string): string => {
    const s = steps.find((x) => x.key === key);
    return s?.detail || fallback;
  };

  // Bar store health
  const barsCollected = warm?.bars_collected ?? {};
  const barsRequired = warm?.bars_required ?? 100;
  const minBars = Object.values(barsCollected).length > 0
    ? Math.min(...Object.values(barsCollected))
    : 0;
  const barsOk = minBars >= barsRequired;
  const barsDetail = Object.entries(barsCollected)
    .map(([k, v]) => `${k}:${v}`)
    .join(" · ") || "No data";

  // Indicators
  const indicators = scalp.indicators ?? {};
  const indReady = Object.values(indicators).filter((i) => i.ready).length;
  const indTotal = Object.keys(indicators).length;
  const indStatus: HealthStatus = indTotal === 0 ? "off" : indReady === indTotal ? "ok" : "warn";

  // WFO
  const wfo = scalp.wfo;
  const wfoChampion = warm?.champion_found === true || wfo?.champion_active === true;
  const wfoStepSt = stepStatus("wfo");
  const wfoStatus: HealthStatus = wfoStepSt === "ok" && wfoChampion ? "ok" : wfoStepSt === "ok" ? "warn" : wfoStepSt;

  // Phase
  const phaseStatus: HealthStatus = phase === "live" ? "ok" : phase === "primed" ? "warn" : "off";

  // Balances
  const bal = scalp.balances;
  const balStatus: HealthStatus = bal?.futures ? "ok" : "warn";
  const balDetail = bal?.futures
    ? `Perp margin: $${(bal.futures.available_margin ?? 0).toFixed(2)} avail`
    : "Perp margin summary not loaded";

  return (
    <div className="health-tile">
      <HealthRow
        label="Candle Feed"
        status={stepStatus("feed")}
        detail={stepDetail("feed", "Not started")}
      />
      <HealthRow
        label="Bar Store"
        status={barsOk ? "ok" : minBars > 0 ? "warn" : "off"}
        detail={barsOk ? `OK — ${barsDetail}` : `${minBars}/${barsRequired} bars min — ${barsDetail}`}
      />
      <HealthRow
        label="Indicators"
        status={indStatus}
        detail={indTotal > 0 ? `${indReady}/${indTotal} pairs ready` : "Waiting for feed"}
      />
      <HealthRow
        label="WFO / Champion"
        status={wfoStatus}
        detail={
          wfoChampion
            ? stepDetail("wfo", "Champion active")
            : steps.find((x) => x.key === "wfo")?.status === "running"
              ? "Grid search running…"
              : "Waiting for warmup"
        }
      />
      <HealthRow
        label="Order Manager"
        status={scalp.runtime_attached ? balStatus : "fail"}
        detail={scalp.runtime_attached ? balDetail : "Runtime not attached"}
      />
      <HealthRow
        label="Startup Phase"
        status={phaseStatus}
        detail={PHASE_LABELS[phase] ?? phase}
      />
      <HealthRow
        label="Scalp Runtime"
        status={scalp.runtime_attached ? "ok" : "fail"}
        detail={scalp.runtime_attached ? `Attached · ${scalp.venue ?? "—"}` : "Not attached"}
      />
    </div>
  );
}

// ── Main SettingsTab ──────────────────────────────────────────────────────────

export function SettingsTab({ scalp, send, connected, snapshot, focusPairKey = "" }: Props) {
  const pol = scalp?.session_policy;
  const op = scalp?.operator;
  const warm = scalp?.warmup;
  const attached = scalp?.runtime_attached !== false;

  const prepBusy = op?.prep_busy === true;
  const startupPhase = scalp?.startup_phase ?? op?.startup_phase ?? "standby";
  const warmupUiPct = warmupProgressFromSnapshot(scalp ?? null).pct;

  const firstPk = scalp?.pair_symbols ? Object.keys(scalp.pair_symbols)[0] : undefined;
  const intervalMin =
    firstPk && scalp?.candles?.[firstPk]?.interval != null
      ? Number(scalp.candles[firstPk].interval)
      : Number(pol?.default_candle_interval_minutes ?? 5);
  const barsReq = pol?.warmup_min_bars ?? warm?.bars_required;
  const estHours =
    barsReq != null ? ((barsReq * intervalMin) / 60).toFixed(1) : "—";

  return (
    <div className="settings-tab">
      <header className="settings-tab-hdr">
        <h1>SETTINGS</h1>
        <p className="settings-tab-sub">
          Warm-up, walk-forward configuration, and operator go-live control for the scalp engine.
        </p>
      </header>

      <section className="settings-card settings-card-flow">
        <h2>Mode decision pipeline</h2>
        <p className="settings-prose">
          How the bot picks the execution strategy for each pair: config pin, walk-forward champion, bootstrap while
          no champion exists, param tuner (and Nemesis dual-lens), optional tuner override, forward demotion, regime
          overlay, then live signals. The highlighted pill matches the current{" "}
          <code className="settings-code">mode_sources</code> value for the focused pair (same key as the active
          Terminal tab when you switch charts).
        </p>
        <ScalpDecisionFlowChart scalp={scalp} pairKey={focusPairKey} />
      </section>

      <section className="settings-card">
        <h2>Mandatory warm-up and configuration</h2>
        <p className="settings-prose">
          The server enforces these gates before new entries are allowed (in addition to SIM/LIVE and risk
          limits). Values below are live from <code className="settings-code">config.toml</code>{" "}
          <code className="settings-code">[scalp]</code>.
        </p>
        <ul className="settings-list">
          <li>
            <strong>Bar threshold</strong> — each pair must reach{" "}
            <code className="settings-code">{fmtBars(pol?.warmup_min_bars ?? warm?.bars_required)}</code>{" "}
            closed bars (warmup) before trading can graduate. At your feed interval of{" "}
            <code className="settings-code">{intervalMin}m</code>, that is roughly{" "}
            <strong>{estHours}h</strong> of history per pair (estimate).
          </li>
          <li>
            <strong>Walk-forward (WFO)</strong> — on each session start (and when you run{" "}
            <em>Begin prep</em>), a grid search replays recent bars. Training / holdout / step widths are{" "}
            <code className="settings-code">{pol?.wfo_train_hours ?? "—"}h</code> /{" "}
            <code className="settings-code">{pol?.wfo_holdout_hours ?? "—"}h</code> /{" "}
            <code className="settings-code">{pol?.wfo_step_hours ?? "—"}h</code> when WFO is enabled. Use{" "}
            <strong>WFO &amp; param tuner (runtime)</strong> below to adjust intervals, folds, and Top-K
            without restarting (in-memory until you edit <code className="settings-code">config.toml</code>).
          </li>
          <li>
            <strong>Champion required</strong> —{" "}
            {pol?.warmup_require_champion !== false
              ? "yes: at least one WFO champion must be found (or warm-up times out)."
              : "no: bar count alone can graduate warm-up."}
          </li>
          <li>
            <strong>Warm-up time cap</strong> —{" "}
            {pol != null && pol.warmup_max_hours > 0
              ? `after ${pol.warmup_max_hours}h the server forces READY even if bars/champion lag (see logs).`
              : "disabled (0): only bars + champion logic applies."}
          </li>
        </ul>
        <div className="settings-status-row">
          <span className="settings-k">Warm-up phase</span>
          <span className="settings-v">{warm?.phase ?? "—"}</span>
          <span className="settings-k">Progress</span>
          <span className="settings-v" title="Same combined progress as the top bar: active startup step when running, else average of step bars.">
            {scalp ? `${warmupUiPct}%` : "—"}
          </span>
          <span className="settings-k">Champion (this run)</span>
          <span className="settings-v">{warm?.champion_found === true ? "yes" : warm?.champion_found === false ? "no" : "—"}</span>
        </div>
      </section>

      <details className="settings-accordion">
        <summary className="settings-accordion-summary">
          <span className="settings-accordion-title">WFO &amp; param tuner (runtime)</span>
          <span className="settings-accordion-hint">Intervals, folds, fees, empirical — expand to edit</span>
        </summary>
        <div className="settings-accordion-body">
          <WfoTunerRuntimeSection
            pol={pol}
            scalp={scalp}
            send={send}
            connected={connected}
            scalpEnabled={scalp?.enabled === true}
            attached={attached}
          />
        </div>
      </details>

      {/* ── Startup Sequence ── */}
      <section className="settings-card settings-card-actions">
        <h2>Startup Sequence</h2>
        <p className="settings-prose">
          Press <strong>Begin Warmup</strong> to start bar backfill and walk-forward optimization.
          Once all steps complete, the <strong>Go Live</strong> button will activate.
          No trades are placed until you click Go Live.
        </p>

        <StartupPhaseDisplay
          phase={startupPhase}
          steps={op?.warmup_steps ?? warm?.startup_steps ?? []}
          canBeginWarmup={op?.can_begin_warmup ?? startupPhase === "standby"}
          canGoLive={op?.can_go_live ?? startupPhase === "primed"}
          scalpEnabled={scalp?.enabled === true}
          attached={attached}
          prepBusy={prepBusy}
          send={send}
        />

        <div className="settings-card" style={{ marginTop: 12 }}>
          <h3 style={{ margin: "0 0 8px", fontSize: "1rem" }}>Portfolio risk</h3>
          <p className="settings-prose" style={{ marginBottom: 8 }}>
            <strong>Halt entries</strong> blocks new scalp entries only (stops/TP and open legs continue).
            <strong> Emergency stop</strong> also enters standby and cancels resting scalp orders (does not flatten).
            <strong> Emergency flatten</strong> halts entries and submits reduce-only market exits for every open leg (destructive).
            <strong> Manual cancel orders</strong> and <strong> manual close positions</strong> log the action and continue normal operation (no halt, no standby).
          </p>
          <div className="settings-status-row" style={{ marginBottom: 10 }}>
            <span className="settings-k">Entries blocked</span>
            <span className="settings-v">
              {scalp?.portfolio_risk?.scalp_entries_blocked === true ? "yes" : "no"}
            </span>
            <span className="settings-k">Scalp halt</span>
            <span className="settings-v" title={scalp?.portfolio_risk?.scalp_risk_halt_reason || ""}>
              {scalp?.portfolio_risk?.scalp_risk_halted === true ? "on" : "off"}
            </span>
          </div>
          {scalp?.portfolio_risk?.scalp_risk_halted && scalp.portfolio_risk.scalp_risk_halt_reason && (
            <p className="settings-warn" style={{ marginTop: 0 }}>
              Reason: {scalp.portfolio_risk.scalp_risk_halt_reason}
            </p>
          )}
          <div className="settings-btn-row">
            <button
              type="button"
              className="settings-btn danger-outline"
              disabled={!scalp?.enabled || !attached}
              onClick={() => send({ action: "scalp_risk_halt", reason: "operator_ui" })}
            >
              Halt entries
            </button>
            <button
              type="button"
              className="settings-btn primary"
              disabled={!scalp?.enabled || !attached || scalp?.portfolio_risk?.scalp_risk_halted !== true}
              onClick={() => send({ action: "scalp_risk_resume" })}
            >
              Resume entries
            </button>
            <button
              type="button"
              className="settings-btn danger"
              disabled={!scalp?.enabled || !attached}
              onClick={() => {
                if (!window.confirm("Emergency stop: standby + cancel resting orders. Positions stay open. Continue?")) return;
                send({ action: "scalp_emergency_stop", reason: "operator_ui" });
              }}
            >
              Emergency stop
            </button>
            <button
              type="button"
              className="settings-btn danger"
              disabled={!scalp?.enabled || !attached}
              onClick={() => {
                if (
                  !window.confirm(
                    "Emergency FLATTEN: halt + cancel protectives + reduce-only MARKET close on every open leg. Continue?",
                  )
                ) {
                  return;
                }
                const v = window.prompt("Type CONFIRM_FLATTEN to proceed:", "");
                if (v !== "CONFIRM_FLATTEN") return;
                send({
                  action: "scalp_emergency_flatten",
                  confirm: "CONFIRM_FLATTEN",
                  reason: "operator_ui",
                });
              }}
            >
              Emergency flatten
            </button>
            <button
              type="button"
              className="settings-btn secondary"
              disabled={!scalp?.enabled || !attached}
              onClick={() => {
                if (
                  !window.confirm(
                    "Cancel all resting scalp orders on the venue? Open positions are not closed. Entries are not halted.",
                  )
                ) {
                  return;
                }
                send({ action: "scalp_operator_manual_cancel_orders", reason: "operator_ui" });
              }}
            >
              Manual cancel orders
            </button>
            <button
              type="button"
              className="settings-btn secondary"
              disabled={!scalp?.enabled || !attached}
              onClick={() => {
                if (
                  !window.confirm(
                    "Submit reduce-only MARKET exits for every open leg? Protectives are cancelled first. Entries stay enabled (not an emergency halt).",
                  )
                ) {
                  return;
                }
                send({ action: "scalp_operator_manual_close_positions", reason: "operator_ui" });
              }}
            >
              Manual close positions
            </button>
          </div>
        </div>

        {(scalp?.config_warnings?.length ?? 0) > 0 && (
          <div className="settings-card" style={{ marginTop: 12 }}>
            <h3 style={{ margin: "0 0 8px", fontSize: "1rem" }}>Configuration notices</h3>
            <ul className="settings-warn" style={{ margin: 0, paddingLeft: 18 }}>
              {(scalp?.config_warnings ?? []).map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}

        {(!attached || !scalp?.enabled) && (
          <p className="settings-warn">
            Scalp is offline or disabled in config — actions unavailable until the scalp engine is enabled.
          </p>
        )}
      </section>

      <details className="settings-accordion">
        <summary className="settings-accordion-summary">
          <span className="settings-accordion-title">Diagnostics &amp; process restart</span>
          <span className="settings-accordion-hint">Health tile, rebuild dist, backend restart</span>
        </summary>
        <div className="settings-accordion-body">
          <section className="settings-card">
            <h2>System Health</h2>
            <SystemHealthTile scalp={scalp} snapshot={snapshot} />
          </section>

          <DashboardRebuildSection send={send} connected={connected} />

          <RestartSection
            send={send}
            connected={connected}
            snapshot={snapshot}
            scalpEnabled={scalp?.enabled === true}
          />
        </div>
      </details>
    </div>
  );
}
