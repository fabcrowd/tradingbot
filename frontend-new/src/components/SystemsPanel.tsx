import { useMemo, useState } from "react";
import type { ConfigSnapshot, ScalpSnapshot } from "../lib/types";
import { warmupProgressFromSnapshot } from "../lib/warmupUiProgress";

type SummaryPill = { key: string; text: string; tone: "on" | "info" | "risk" };

function modeSourceShort(src: string | undefined): string | null {
  if (!src || src === "config") return null;
  const map: Record<string, string> = {
    wfo_champion: "WFO CHAMP",
    operator_auto: "AUTO",
    operator: "MANUAL",
    bootstrap: "BOOTSTRAP",
    tuner: "TUNER",
    param_tuner_override: "TUNER",
    nemesis_tuner: "TUNER",
    forward_demotion: "DEMOTED",
  };
  return map[src] ?? src.replace(/_/g, " ").toUpperCase();
}

function fmtPill(n: number | undefined | null, d = 1): string {
  return Number(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
}

/** Compact scalp / WFO pills for SYSTEMS_CONTROLS header (replaces removed scalp tab rail). */
function buildScalpHeaderPills(scalp: ScalpSnapshot | null | undefined, selectedPair?: string): SummaryPill[] {
  if (!scalp?.pair_symbols || !selectedPair) return [];
  const sym = scalp.pair_symbols[selectedPair];
  if (!sym) return [];

  const pills: SummaryPill[] = [];
  const mode = scalp.active_modes?.[selectedPair];
  const srcRaw = scalp.mode_sources?.[selectedPair];
  const src = modeSourceShort(srcRaw) ?? (srcRaw ? srcRaw.replace(/_/g, " ").toUpperCase() : null);

  if (mode) {
    pills.push({
      key: "hdr-mode",
      text: `MODE · ${String(mode).replace(/_/g, " ")}`,
      tone: "on",
    });
  }
  if (src) {
    pills.push({ key: "hdr-src", text: `SRC · ${src}`, tone: "info" });
  }

  const ch = scalp.champions?.[sym];
  if (ch && typeof ch.mode === "string" && ch.mode) {
    pills.push({
      key: "hdr-champ",
      text: `CHAMP · ${String(ch.mode).replace(/_/g, " ")} · PF ${fmtPill(ch.profit_factor, 2)}`,
      tone: "on",
    });
  } else {
    pills.push({ key: "hdr-champ", text: "CHAMP · (no row)", tone: "info" });
  }

  const wfo = scalp.wfo;
  if (wfo?.enabled) {
    const next = wfo.seconds_until_next != null ? `${wfo.seconds_until_next}s` : "—";
    const tag = wfo.champion_active ? "READY" : `${fmtPill(wfo.data_progress_pct, 0)}%`;
    pills.push({
      key: "hdr-wfo",
      text: `WFO · ${tag} · next ${next}`,
      tone: wfo.champion_active ? "on" : "info",
    });
  }

  const lp = wfo?.last_wfo_pass;
  if (lp && wfo?.enabled) {
    const row = lp.pairs?.find((p) => p.pair_key === selectedPair);
    if (row) {
      pills.push({
        key: "hdr-wfo-pair",
        text: `LAST PASS · ${row.outcome}${row.skip_reason ? ` (${row.skip_reason})` : ""}`,
        tone: row.outcome === "champion_saved" ? "on" : "info",
      });
    }
  }

  if (scalp.regime_risk_on?.active) {
    pills.push({
      key: "hdr-reg",
      text: `REGIME · ${scalp.regime_risk_on.mode_label ?? "RISK ON"}`,
      tone: "risk",
    });
  }

  const tun = scalp.tuner?.[selectedPair];
  if (tun) {
    pills.push({
      key: "hdr-tun",
      text: `TUNER · ${String(tun.best_mode).replace(/_/g, " ")}${tun.frozen ? " · frozen" : ""}`,
      tone: tun.frozen ? "info" : "on",
    });
  }

  return pills;
}

/** Active strategy mode + selection source (e.g. WFO champion) for the selected pair. */
function resolveStrategyLabel(scalp: ScalpSnapshot, selectedPair?: string): string | null {
  const pk = selectedPair;
  if (!pk) return null;
  let m = scalp.active_modes?.[pk] ?? null;
  if (!m) {
    const sym = scalp.pair_symbols?.[pk];
    if (sym) {
      const ch = scalp.champions?.[sym];
      if (ch && typeof ch === "object" && "mode" in ch && ch.mode) m = String(ch.mode);
    }
  }
  if (!m) return null;
  const tag = modeSourceShort(scalp.mode_sources?.[pk]);
  return tag ? `${m} · ${tag}` : m;
}

type MainStatusPill = SummaryPill & { modifier?: "warming" };

/**
 * Single primary pill: scalp lifecycle, execution mode, and active strategy when known.
 */
function buildMainBotStatusPill(
  scalp: ScalpSnapshot | null | undefined,
  config: ConfigSnapshot | null,
  selectedPair?: string,
): MainStatusPill | null {
  if (!config && !scalp) return null;

  if (!scalp?.enabled) {
    if (!config) return null;
    const mode = String(config.mode ?? "").toLowerCase();
    const tone: "risk" | "info" = mode === "live" ? "risk" : "info";
    const label =
      mode === "live"
        ? "ENGINE · LIVE"
        : mode === "paper"
          ? "ENGINE · PAPER"
          : `ENGINE · ${String(config.mode ?? "—").toUpperCase()}`;
    return { key: "main-engine", text: label, tone };
  }

  if (scalp.runtime_attached === false) {
    return { key: "main-attach", text: "SCALP · ATTACHING", tone: "info" };
  }

  if (scalp.operator?.prep_busy === true) {
    return { key: "main-prep", text: "PREPARING", tone: "info", modifier: "warming" };
  }

  const startup = String(scalp.startup_phase ?? scalp.operator?.startup_phase ?? "standby").toLowerCase();
  const wp = String(scalp.warmup?.phase ?? "").toLowerCase();
  const strat = resolveStrategyLabel(scalp, selectedPair);
  const suffix = strat ? ` · ${strat}` : "";

  const isWarming = startup === "warming_up" || wp === "collecting" || wp === "optimizing";
  if (isWarming) {
    const ui = warmupProgressFromSnapshot(scalp);
    const stepPart = ui.stepShort ? `${ui.stepShort} ${Math.round(ui.pct)}%` : `${Math.round(ui.pct)}%`;
    return {
      key: "main-warm",
      text: `WARMING UP · ${stepPart}${suffix}`,
      tone: "info",
      modifier: "warming",
    };
  }

  if (startup === "primed") {
    return { key: "main-primed", text: `PRIMED · GO LIVE${suffix}`, tone: "on" };
  }

  if (startup === "live") {
    const sim = scalp.sim_mode;
    const base = sim ? "LIVE · SIM" : "LIVE · TRADING";
    return { key: "main-live", text: `${base}${suffix}`, tone: sim ? "info" : "risk" };
  }

  return { key: "main-standby", text: `STANDBY${suffix}`, tone: "info" };
}

interface Props {
  config: ConfigSnapshot | null;
  send: (payload: Record<string, unknown>) => void;
  selectedPair?: string;
  scalp?: ScalpSnapshot | null;
}

/**
 * Scalp / WFO status summary only. Coinbase CDE tuning lives in Settings.
 */
export function SystemsPanel({ config, send, selectedPair, scalp }: Props) {
  void send;
  const [panelOpen, setPanelOpen] = useState(true);

  const b = config;
  const mainStatusPill = useMemo(
    () => buildMainBotStatusPill(scalp ?? null, b, selectedPair),
    [b, scalp, selectedPair],
  );
  const scalpHeaderPills = useMemo(() => buildScalpHeaderPills(scalp ?? null, selectedPair), [scalp, selectedPair]);
  const hasCollapsedSummary = mainStatusPill != null || scalpHeaderPills.length > 0;

  return (
    <div className="panel systems">
      <div className="systems-hdr-block">
        <button
          type="button"
          className="systems-panel-hdr"
          onClick={() => setPanelOpen((o) => !o)}
          aria-expanded={panelOpen}
        >
          <span className="ph-title">SYSTEMS_CONTROLS</span>
          <span className="sys-chevron" aria-hidden>
            {panelOpen ? "▲" : "▼"}
          </span>
        </button>
        {scalpHeaderPills.length > 0 ? (
          <div className="systems-header-pills" role="status" aria-label="Scalp and WFO status">
            {scalpHeaderPills.map((p) => (
              <span key={p.key} className={`systems-pill systems-pill-${p.tone}`}>
                {p.text}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      {!panelOpen && !hasCollapsedSummary && (
        <div className="systems-summary-bar" role="status">
          <span className="systems-pill systems-pill-muted">Connecting…</span>
        </div>
      )}

      {!panelOpen && hasCollapsedSummary && (
        <div className="systems-summary-bar" role="status" aria-label="Bot status">
          {mainStatusPill ? (
            <span
              key={mainStatusPill.key}
              className={`systems-pill systems-pill-main systems-pill-${mainStatusPill.tone}${mainStatusPill.modifier === "warming" ? " systems-pill-main--warming" : ""}`}
            >
              {mainStatusPill.text}
            </span>
          ) : null}
          {scalpHeaderPills.map((p) => (
            <span key={`c-${p.key}`} className={`systems-pill systems-pill-${p.tone}`}>
              {p.text}
            </span>
          ))}
        </div>
      )}

      {panelOpen && !b && <div className="sys-empty">Connecting…</div>}

      {panelOpen && b && (
        <div
          className="sys-empty"
          style={{ fontSize: 11, lineHeight: 1.45, color: "var(--text-muted)", padding: "4px 0 12px" }}
        >
          Scalp, WFO, param tuner, and Coinbase execution are configured in{" "}
          <strong style={{ color: "var(--text-secondary)" }}>Settings</strong>. Use the pills above for live mode / champion
          / WFO status.
        </div>
      )}
    </div>
  );
}
