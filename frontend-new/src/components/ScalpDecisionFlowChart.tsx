import type { ScalpSnapshot } from "../lib/types";

type Props = {
  scalp: ScalpSnapshot | null;
  /** Config pair key; falls back to first scalp pair when empty. */
  pairKey: string;
};

type NodeState = "off" | "infra" | "primary" | "overlay";

type NodeDef = {
  id: string;
  title: string;
  sub: string;
};

const NODES: NodeDef[] = [
  { id: "config", title: "CONFIG", sub: "[scalp.pairs.*].strategy_mode — manual pin vs auto" },
  { id: "wfo", title: "WFO", sub: "Continuous full-grid eval on bar store → writes champion JSON" },
  { id: "champion", title: "CHAMPION ROW", sub: "WFO eval-window winner for this symbol → resolves auto" },
  { id: "bootstrap", title: "NO-CHAMPION BOOTSTRAP", sub: "2h return-% ranking until a champion exists" },
  { id: "nemesis", title: "NEMESIS GATE", sub: "Tuner vs bootstrap expectancy / PF dual-lens (no champion)" },
  { id: "tuner", title: "PARAM TUNER", sub: "Perturbs tunables for the current execution mode" },
  { id: "override", title: "TUNER MODE OVERRIDE", sub: "Allowed: tuner may replace WFO champion mode" },
  { id: "demotion", title: "FORWARD DEMOTION", sub: "Live forward window failed → revert + demotion flag" },
  { id: "regime", title: "REGIME RISK-ON", sub: "Vol / volume stress → faster WFO / shorter bootstrap" },
  { id: "execution", title: "SIGNAL ENGINE", sub: "Indicators + entries on active mode & params" },
];

function resolvePairKey(scalp: ScalpSnapshot | null, pairKey: string): string {
  const keys = scalp?.pair_symbols ? Object.keys(scalp.pair_symbols) : [];
  if (pairKey && scalp?.pair_symbols?.[pairKey]) return pairKey;
  return keys[0] ?? "";
}

function championForPair(scalp: ScalpSnapshot, pk: string) {
  const sym = scalp.pair_symbols?.[pk];
  if (!sym || !scalp.champions) return undefined;
  const u = sym.toUpperCase();
  for (const [k, v] of Object.entries(scalp.champions)) {
    if (k.toUpperCase() === u) return v;
  }
  return undefined;
}

function computeNodeStates(scalp: ScalpSnapshot | null, pairKey: string): Record<string, NodeState> {
  const base: Record<string, NodeState> = Object.fromEntries(NODES.map((n) => [n.id, "off" as NodeState])) as Record<
    string,
    NodeState
  >;

  const pk = resolvePairKey(scalp, pairKey);
  if (!scalp?.enabled || !pk) return base;

  const src = scalp.mode_sources?.[pk] ?? "config";
  const ch = championForPair(scalp, pk);
  const wfoOn = Boolean(scalp.session_policy?.wfo_enabled && scalp.wfo?.enabled);
  const tun = scalp.tuner?.[pk];

  base.execution = "infra";

  if (wfoOn) base.wfo = "infra";
  if (ch) base.champion = "infra";

  switch (src) {
    case "config":
      base.config = "primary";
      break;
    case "wfo_champion":
      base.champion = "primary";
      base.wfo = "infra";
      break;
    case "bootstrap":
      base.bootstrap = "primary";
      break;
    case "tuner":
      base.tuner = "primary";
      break;
    case "nemesis_tuner":
      base.nemesis = "primary";
      base.tuner = "infra";
      break;
    case "param_tuner_override":
      base.override = "primary";
      base.tuner = "infra";
      break;
    case "forward_demotion":
      base.demotion = "primary";
      break;
    default:
      base.config = "primary";
  }

  if (tun && base.tuner !== "primary" && base.nemesis !== "primary" && base.override !== "primary") {
    base.tuner = "infra";
  }

  if (scalp.regime_risk_on?.active) base.regime = "overlay";

  return base;
}

function nodeClass(state: NodeState): string {
  if (state === "primary") return "settings-flow-node settings-flow-node--primary";
  if (state === "infra") return "settings-flow-node settings-flow-node--infra";
  if (state === "overlay") return "settings-flow-node settings-flow-node--overlay";
  return "settings-flow-node settings-flow-node--off";
}

export function ScalpDecisionFlowChart({ scalp, pairKey }: Props) {
  const pk = resolvePairKey(scalp, pairKey);
  const states = computeNodeStates(scalp, pairKey);
  const mode = pk ? scalp?.active_modes?.[pk] : undefined;
  const src = pk ? scalp?.mode_sources?.[pk] : undefined;
  const sym = pk ? scalp?.pair_symbols?.[pk] : undefined;

  if (!scalp?.enabled) {
    return <p className="settings-warn settings-flow-unavailable">Scalp disabled — pipeline idle.</p>;
  }

  if (!pk) {
    return <p className="settings-warn settings-flow-unavailable">No scalp pairs in snapshot.</p>;
  }

  return (
    <div className="settings-flow-wrap">
      <div className="settings-flow-legend">
        <span>
          <span className="settings-flow-legend-dot settings-flow-legend-dot--primary" /> Primary authority (mode source)
        </span>
        <span>
          <span className="settings-flow-legend-dot settings-flow-legend-dot--infra" /> Active subsystem
        </span>
        <span>
          <span className="settings-flow-legend-dot settings-flow-legend-dot--overlay" /> Risk overlay
        </span>
      </div>

      <div className="settings-flow-context">
        <span>
          Pair <code className="settings-code">{pk}</code>
        </span>
        {sym ? (
          <span>
            · Symbol <code className="settings-code">{sym}</code>
          </span>
        ) : null}
        {mode ? (
          <span>
            · Mode <code className="settings-code">{mode}</code>
          </span>
        ) : null}
        {src ? (
          <span>
            · Source <code className="settings-code">{src}</code>
          </span>
        ) : null}
      </div>

      <div className="settings-flow" aria-label="Scalp mode decision pipeline">
        {NODES.map((n, i) => (
          <div key={n.id} className="settings-flow-step">
            <div className={nodeClass(states[n.id] ?? "off")} title={n.sub}>
              <div className="settings-flow-node-title">{n.title}</div>
              <div className="settings-flow-node-sub">{n.sub}</div>
            </div>
            {i < NODES.length - 1 ? <div className="settings-flow-arrow" aria-hidden /> : null}
          </div>
        ))}
      </div>

      <p className="settings-flow-footnote">
        Order matches runtime resolution: config / WFO / champion or bootstrap paths, tuner &amp; Nemesis when no champion
        (per policy), optional override and forward demotion, regime overlay, then live signals. Highlighting follows{" "}
        <code className="settings-code">mode_sources</code> for the same pair as the Terminal chart when both are open.
      </p>
    </div>
  );
}
