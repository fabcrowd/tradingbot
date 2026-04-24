import type { UiLogEntry } from "./types";

/** Log lines mirrored to the UI that reflect warmup / WFO progress (not per-candle noise). */
export function isWarmupProgressLog(e: UiLogEntry): boolean {
  if (e.kind !== "server_log") return false;
  const d = e.detail ?? "";
  if (d.includes("scalp_wfo:")) return true;
  if (/ScalpRuntime:.*candle close/i.test(d)) return false;
  if (
    d.includes("ScalpRuntime:") &&
    /warmup|WFO|backfill|champion|bar threshold|Begin Warmup|prep WFO|startup WFO|Complete:|operator_begin|primed|walk-forward/i.test(d)
  ) {
    return true;
  }
  if ((e.title ?? "").includes("scalp_wfo")) return true;
  return false;
}

const _MAX_CHARS = 160;

export function warmupTickerTextsFromLogs(logs: UiLogEntry[], maxLines = 6): string[] {
  const picked = logs.filter(isWarmupProgressLog).slice(-maxLines);
  return [...picked].reverse().map((e) => {
    let s = (e.detail ?? "").replace(/\s+/g, " ").trim();
    if (s.length > _MAX_CHARS) s = `${s.slice(0, _MAX_CHARS - 1)}…`;
    return s;
  });
}
