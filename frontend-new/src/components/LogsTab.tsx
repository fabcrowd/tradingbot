import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { UiLogEntry } from "../lib/types";

const LEVELS = ["all", "error", "warning", "info", "success"] as const;

function fmtTs(ts: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, {
    dateStyle: "short",
    timeStyle: "medium",
  });
}

function levelClass(level: string): string {
  if (level === "error") return "logs-level-error";
  if (level === "warning") return "logs-level-warn";
  if (level === "success") return "logs-level-success";
  return "logs-level-info";
}

export type LogFocus =
  | { logId?: string; exchangeErrorId?: string }
  | null;

type Props = {
  entries: UiLogEntry[];
  focus: LogFocus;
  onFocusConsumed: () => void;
};

export function LogsTab({ entries, focus, onFocusConsumed }: Props) {
  const focusId =
    focus?.logId
    ?? (focus?.exchangeErrorId
      ? entries.find((e) => e.exchange_error_id === focus.exchangeErrorId)?.id
      : undefined)
    ?? null;
  const [levelFilter, setLevelFilter] = useState<(typeof LEVELS)[number]>("all");
  const [sourceQ, setSourceQ] = useState("");
  const [textQ, setTextQ] = useState("");
  const [pinBottom, setPinBottom] = useState(true);
  const listRef = useRef<HTMLDivElement>(null);
  const rowRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  const filtered = useMemo(() => {
    const sq = sourceQ.trim().toLowerCase();
    const tq = textQ.trim().toLowerCase();
    return entries.filter((e) => {
      if (levelFilter !== "all" && e.level !== levelFilter) return false;
      if (sq && !e.source.toLowerCase().includes(sq)) return false;
      if (tq) {
        const blob = `${e.title} ${e.detail} ${e.kind}`.toLowerCase();
        if (!blob.includes(tq)) return false;
      }
      return true;
    });
  }, [entries, levelFilter, sourceQ, textQ]);

  useEffect(() => {
    if (!pinBottom || !listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [filtered, pinBottom]);

  useEffect(() => {
    if (!focus?.logId && !focus?.exchangeErrorId) return undefined;
    if (!focusId) return undefined;
    const el = rowRefs.current.get(focusId);
    if (!el) {
      const t = window.setTimeout(() => onFocusConsumed(), 8000);
      return () => window.clearTimeout(t);
    }
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    el.classList.add("logs-row-highlight");
    const t = window.setTimeout(() => {
      el.classList.remove("logs-row-highlight");
      onFocusConsumed();
    }, 2400);
    return () => window.clearTimeout(t);
  }, [focus, focusId, filtered, onFocusConsumed]);

  const setRowRef = useCallback((id: string, el: HTMLDivElement | null) => {
    if (el) rowRefs.current.set(id, el);
    else rowRefs.current.delete(id);
  }, []);

  return (
    <div className="logs-tab">
      <section className="panel logs-toolbar">
        <div className="logs-toolbar-row">
          <span className="ph-title">EVENT_LOG</span>
          <span className="logs-toolbar-meta">{filtered.length} / {entries.length} shown</span>
        </div>
        <div className="logs-filters">
          <label className="logs-filter">
            <span>Level</span>
            <select
              value={levelFilter}
              onChange={(e) => setLevelFilter(e.target.value as (typeof LEVELS)[number])}
              className="logs-select"
            >
              {LEVELS.map((l) => (
                <option key={l} value={l}>{l}</option>
              ))}
            </select>
          </label>
          <label className="logs-filter">
            <span>Source contains</span>
            <input
              type="text"
              value={sourceQ}
              onChange={(e) => setSourceQ(e.target.value)}
              placeholder="e.g. scalp, dashboard"
              className="logs-input"
            />
          </label>
          <label className="logs-filter logs-filter-grow">
            <span>Search</span>
            <input
              type="text"
              value={textQ}
              onChange={(e) => setTextQ(e.target.value)}
              placeholder="title, detail, kind"
              className="logs-input"
            />
          </label>
          <label className="logs-pin">
            <input
              type="checkbox"
              checked={pinBottom}
              onChange={(e) => setPinBottom(e.target.checked)}
            />
            Pin to bottom
          </label>
        </div>
      </section>

      <div className="panel logs-list-wrap">
        <div ref={listRef} className="logs-list" role="log" aria-live="polite">
          {filtered.length === 0 ? (
            <div className="logs-empty">No entries match filters.</div>
          ) : (
            filtered.map((e) => (
              <div
                key={e.id}
                ref={(el) => setRowRef(e.id, el)}
                className={`logs-row ${focusId === e.id ? "logs-row-focused" : ""}`}
                data-log-id={e.id}
              >
                <div className="logs-row-top">
                  <span className="logs-ts">{fmtTs(e.ts)}</span>
                  <span className={`logs-level ${levelClass(e.level)}`}>{e.level}</span>
                  <span className="logs-kind">{e.kind}</span>
                  <span className="logs-source">{e.source || "—"}</span>
                </div>
                <div className="logs-title">{e.title}</div>
                {e.detail ? <pre className="logs-detail">{e.detail}</pre> : null}
                {e.exchange_error_id ? (
                  <div className="logs-meta">exchange_error_id: {e.exchange_error_id}</div>
                ) : null}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
