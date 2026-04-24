import type { ScalpSnapshot } from "./types";

/** Short tags for top bar — same sequence as Settings startup steps */
const STEP_SHORT: Record<string, string> = {
  feed: "FEED",
  backfill: "BARS",
  wfo: "WFO",
  champion: "CHAMP",
};

export type WarmupUiProgress = {
  /** 0–100; matches the active startup step when one is running */
  pct: number;
  /** e.g. WFO while walk-forward row is running */
  stepShort: string | null;
};

function clampPct(n: number): number {
  if (!Number.isFinite(n)) return 0;
  return Math.min(100, Math.max(0, n));
}

/**
 * Progress shown in the header and Settings "Progress" row.
 * Prefer `operator.warmup_steps` / `warmup.startup_steps` (same source as Startup Sequence),
 * not `warmup.progress_pct` alone (that is bar-count only and stays 100% while WFO runs).
 */
export function warmupProgressFromSnapshot(scalp: ScalpSnapshot | null | undefined): WarmupUiProgress {
  const warm = scalp?.warmup;
  const fallbackBars = clampPct(Number(warm?.progress_pct));

  if (!warm?.enabled) {
    return { pct: fallbackBars, stepShort: null };
  }

  const steps = scalp?.operator?.warmup_steps ?? warm?.startup_steps ?? [];
  if (steps.length === 0) {
    return { pct: fallbackBars, stepShort: null };
  }

  const running = steps.find((s) => s.status === "running");
  if (running) {
    return {
      pct: clampPct(Number(running.pct)),
      stepShort: STEP_SHORT[running.key] ?? String(running.key).toUpperCase(),
    };
  }

  const allDone = steps.every((s) => s.status === "done" || s.status === "failed");
  if (allDone) {
    if (steps.some((s) => s.status === "failed")) {
      return { pct: clampPct(Math.min(...steps.map((s) => Number(s.pct) || 0))), stepShort: null };
    }
    return { pct: 100, stepShort: null };
  }

  const sum = steps.reduce((a, s) => a + clampPct(Number(s.pct) || 0), 0);
  return { pct: Math.round((sum / steps.length) * 10) / 10, stepShort: null };
}
