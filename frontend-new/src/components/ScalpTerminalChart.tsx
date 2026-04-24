import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  LineType,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineData,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import type {
  IndicatorOverlayPoint,
  ScalpCandle,
  ScalpPosition,
  ScalpTrade,
  ScalpVenueOpenOrder,
} from "../lib/types";

/** TradingView dark–theme candle palette (crypto layout) */
const TV_BG = "#131722";
const TV_TEXT = "#B2B5BE";
const TV_GRID = "rgba(42, 46, 57, 0.55)";
const TV_GRID_SOFT = "rgba(42, 46, 57, 0.38)";
const TV_BORDER = "#363a45";
const TV_CANDLE_UP = "#089981";
const TV_CANDLE_DOWN = "#F23645";

const COL_LIMIT_BUY = TV_CANDLE_UP;
const COL_LIMIT_SELL = TV_CANDLE_DOWN;
const COL_ENTRY = "#ffca28";
/** Muted price-line colors (rgba) so candles / EMAs stay the visual focus */
const COL_ENTRY_LINE = "rgba(255, 202, 40, 0.42)";
const COL_SL_LINE = "rgba(255, 112, 67, 0.36)";
const COL_TP_LINE = "rgba(120, 195, 255, 0.4)";
const COL_LIMIT_BUY_LINE = "rgba(8, 153, 129, 0.38)";
const COL_LIMIT_SELL_LINE = "rgba(242, 54, 69, 0.38)";
const COL_EXIT_WIN = "#089981";
const COL_EXIT_LOSS = "#F23645";
/** TV-style study colors (solid lines, curved) */
const COL_EMA_FAST = "#2962FF";
const COL_EMA_SLOW = "#FF9800";
const COL_T3 = "#9C27B0";
const COL_VWAP = "#787B86";
/** Filled position average entry — Coinbase-style dotted guide + floating P/L pill */
const COL_POS_ENTRY_LINE = "rgba(46, 204, 113, 0.92)";

const IND_TOGGLE_STORAGE = "scalp_chart_indicator_lines";
const TRADE_LVL_STORAGE = "scalp_chart_trade_levels_visible";

function loadTradeLevelsVisible(): boolean {
  try {
    const v = localStorage.getItem(TRADE_LVL_STORAGE);
    if (v === "0" || v === "false") return false;
  } catch {
    /* ignore */
  }
  return true;
}

export type IndicatorLineToggles = {
  emaFast: boolean;
  emaSlow: boolean;
  t3: boolean;
  vwap: boolean;
};

/** Default off: MACD histogram pane carries primary momentum view (TV-style); enable lines from IND toggles. */
const DEFAULT_IND_TOGGLES: IndicatorLineToggles = {
  emaFast: false,
  emaSlow: false,
  t3: false,
  vwap: false,
};

function loadIndicatorToggles(): IndicatorLineToggles {
  const out = { ...DEFAULT_IND_TOGGLES };
  try {
    const raw = localStorage.getItem(IND_TOGGLE_STORAGE);
    if (raw) Object.assign(out, JSON.parse(raw) as Partial<IndicatorLineToggles>);
  } catch {
    /* ignore */
  }
  return out;
}

export type TerminalStrategyBanner = {
  mode: string;
  source?: string;
  regime?: string | null;
};

/** Candlestick series price-line width (library allows 1–4). */
type ChartPriceLineWidth = 1 | 2 | 3 | 4;

type PriceLineSpec = {
  id: string;
  price: number;
  color: string;
  lineStyle: LineStyle;
  title: string;
  lineWidth?: ChartPriceLineWidth;
  axisLabelVisible?: boolean;
};

type Props = {
  pairKey: string;
  closed: ScalpCandle[] | undefined;
  live: ScalpCandle | null | undefined;
  /** Per-closed-bar indicator values (same `t` as candles); drawn as lines under OHLC. */
  indicatorOverlay?: IndicatorOverlayPoint[];
  height?: number;
  priceDecimals?: number;
  cdeOpenOrders?: ScalpVenueOpenOrder[];
  openPositions?: ScalpPosition[];
  tradeHistory?: ScalpTrade[];
  strategyBanner?: TerminalStrategyBanner | null;
};

function overlayToHistData(pts: IndicatorOverlayPoint[] | undefined): HistogramData<Time>[] {
  const tmp: HistogramData<Time>[] = [];
  if (!pts?.length) return tmp;
  for (const p of pts) {
    if (!Number.isFinite(p.t)) continue;
    const v = p.macd_hist;
    if (v == null || !Number.isFinite(v)) continue;
    const color = v >= 0 ? TV_CANDLE_UP : TV_CANDLE_DOWN;
    tmp.push({ time: p.t as Time, value: v, color });
  }
  const byT = new Map<number, HistogramData<Time>>();
  for (const row of tmp) {
    byT.set(Number(row.time), row);
  }
  return [...byT.entries()].sort((a, b) => a[0] - b[0]).map(([, row]) => row);
}

function overlayToLineData(
  pts: IndicatorOverlayPoint[] | undefined,
  pick: (p: IndicatorOverlayPoint) => number,
): LineData<Time>[] {
  const tmp: LineData<Time>[] = [];
  if (!pts?.length) return tmp;
  for (const p of pts) {
    if (!Number.isFinite(p.t)) continue;
    const v = pick(p);
    if (!Number.isFinite(v) || !(v > 0)) continue;
    tmp.push({ time: p.t as Time, value: v });
  }
  const byT = new Map<number, LineData<Time>>();
  for (const row of tmp) {
    byT.set(Number(row.time), row);
  }
  return [...byT.entries()].sort((a, b) => a[0] - b[0]).map(([, row]) => row);
}

function mergeClosedAndLive(closed: ScalpCandle[] | undefined, live: ScalpCandle | null | undefined): ScalpCandle[] {
  const out = [...(closed ?? [])];
  if (!live || !Number.isFinite(live.t)) return out;
  const idx = out.findIndex((c) => c.t === live.t);
  if (idx >= 0) out[idx] = live;
  else out.push(live);
  return out;
}

function snapBarTime(merged: ScalpCandle[], ts: number): number | null {
  if (!Number.isFinite(ts) || ts <= 0 || merged.length === 0) return null;
  let best: number | null = null;
  for (const c of merged) {
    if (!c || !Number.isFinite(c.t)) continue;
    if (c.t <= ts && (best === null || c.t > best)) best = c.t;
  }
  if (best !== null) return best;
  // Entry is older than the left edge of the loaded candles — still show a marker on the first bar.
  const first = merged.find((c) => c && Number.isFinite(c.t));
  return first != null ? Number(first.t) : null;
}

/** Prefer venue snapshot; else approximate from mark vs entry (matches scalp_trader sign). */
function effectiveUnrealizedUsd(p: ScalpPosition): number | null {
  const u = p.unrealized_pnl;
  if (u != null && Number.isFinite(u)) return u;
  const mark = p.mark_price;
  const entry = p.entry;
  const q = p.qty ?? 0;
  if (mark == null || !Number.isFinite(mark) || mark <= 0 || !(entry > 0) || !(q > 0)) return null;
  const mult = p.contract_size ?? 1;
  const long = (p.direction || "long").toLowerCase() !== "short";
  return long ? (mark - entry) * q * mult : (entry - mark) * q * mult;
}

function buildOpenFilledEntryLines(positions: ScalpPosition[] | undefined): PriceLineSpec[] {
  const out: PriceLineSpec[] = [];
  for (const p of positions ?? []) {
    if ((p.status || "").toLowerCase() !== "open") continue;
    if (!(p.entry > 0)) continue;
    const base = (p.entry_cl_ord_id || "pos").slice(0, 24);
    out.push({
      id: `${base}-open-entry`,
      price: p.entry,
      color: COL_POS_ENTRY_LINE,
      lineStyle: LineStyle.Dotted,
      title: "AVG",
      lineWidth: 1,
      axisLabelVisible: false,
    });
  }
  return out;
}

function buildPriceLines(
  cde: ScalpVenueOpenOrder[] | undefined,
  positions: ScalpPosition[] | undefined,
): PriceLineSpec[] {
  const out: PriceLineSpec[] = [];
  const idSeen = new Set<string>();

  const push = (
    id: string,
    price: number,
    color: string,
    lineStyle: LineStyle,
    title: string,
    opts?: { lineWidth?: ChartPriceLineWidth; axisLabelVisible?: boolean },
  ) => {
    if (!(price > 0) || !Number.isFinite(price)) return;
    if (idSeen.has(id)) return;
    idSeen.add(id);
    out.push({
      id,
      price,
      color,
      lineStyle,
      title,
      ...opts,
    });
  };

  for (const p of positions ?? []) {
    const base = p.entry_cl_ord_id || "pos";
    const st = (p.status || "").toLowerCase();
    // Filled `open` legs: entry line + P/L pill are handled separately (always on, Coinbase-style).
    if (p.entry > 0 && st !== "open") {
      push(
        `${base}-entry`,
        p.entry,
        COL_ENTRY_LINE,
        st === "pending" ? LineStyle.Dashed : LineStyle.Solid,
        st === "pending" ? "ENTRY (pending)" : "ENTRY",
        { lineWidth: 1, axisLabelVisible: true },
      );
    }
    if (p.stop > 0) {
      push(`${base}-sl`, p.stop, COL_SL_LINE, LineStyle.Dotted, "SL", {
        lineWidth: 1,
        axisLabelVisible: true,
      });
    }
    if (p.tp > 0) {
      push(`${base}-tp`, p.tp, COL_TP_LINE, LineStyle.Dotted, "TP", {
        lineWidth: 1,
        axisLabelVisible: true,
      });
    }
  }

  for (const o of cde ?? []) {
    const lp = o.limit_price ?? 0;
    const tr = o.trigger_price ?? 0;
    const px = lp > 0 ? lp : tr;
    if (!(px > 0)) continue;
    const side = String(o.side || "").toLowerCase();
    const ot = String(o.order_type || "").toUpperCase();
    const oid = o.order_id || o.client_order_id || "ord";
    const buy = side === "buy";
    const col =
      ot.includes("STOP") && !ot.includes("TAKE")
        ? COL_SL_LINE
        : ot.includes("TAKE") || ot.includes("PROFIT") || ot.includes("TP")
          ? COL_TP_LINE
          : buy
            ? COL_LIMIT_BUY_LINE
            : COL_LIMIT_SELL_LINE;
    const shortOt = ot.replace(/_/g, " ").slice(0, 14) || "ORD";
    const title = `${buy ? "BUY" : "SELL"} · ${shortOt}`;
    push(`cde-${oid}-${px}`, px, col, LineStyle.Dotted, title, {
      lineWidth: 1,
      axisLabelVisible: true,
    });
  }

  return out;
}

function buildMarkers(
  merged: ScalpCandle[],
  positions: ScalpPosition[] | undefined,
  trades: ScalpTrade[] | undefined,
): SeriesMarker<Time>[] {
  const markers: SeriesMarker<Time>[] = [];

  // Open / pending position entry markers.
  //  - pending (order placed, awaiting fill): hollow circle, label "PLACED"
  //  - open (filled, still live): directional BUY / SELL arrow matching trade-history style
  //    so the fill point is visible immediately, not only after exit.
  for (const p of positions ?? []) {
    const ts = p.entry_ts;
    if (ts == null || ts <= 0 || !(p.entry > 0)) continue;
    const t = snapBarTime(merged, ts);
    if (t == null) continue;
    const mode = (p.strategy_mode || "").trim();
    const short = mode.length > 16 ? `${mode.slice(0, 14)}…` : mode;
    const isLong = (p.direction || "long") === "long";

    if (p.status === "pending") {
      markers.push({
        time: t as Time,
        position: "atPriceMiddle",
        shape: "circle",
        color: COL_ENTRY,
        price: p.entry,
        text: short ? `PLACED ${short}` : "PLACED",
        id: `placed-${p.entry_cl_ord_id || t}`,
        size: 1.2,
      });
    } else {
      // status === "open" — filled, directional arrow at fill price/time.
      markers.push({
        time: t as Time,
        position: isLong ? "belowBar" : "aboveBar",
        shape: isLong ? "arrowUp" : "arrowDown",
        color: isLong ? COL_LIMIT_BUY : COL_LIMIT_SELL,
        text: short ? `${isLong ? "BUY" : "SELL"} ${short}` : isLong ? "BUY" : "SELL",
        id: `filled-${p.entry_cl_ord_id || t}`,
        size: 1.2,
      });
    }
  }

  // Trade history: entry + exit markers for completed trades
  for (const tr of trades ?? []) {
    const isLong = (tr.direction || "long") === "long";
    // Entry marker
    if (tr.entry_ts > 0 && tr.entry_price > 0) {
      const et = snapBarTime(merged, tr.entry_ts);
      if (et != null) {
        markers.push({
          time: et as Time,
          position: isLong ? "belowBar" : "aboveBar",
          shape: isLong ? "arrowUp" : "arrowDown",
          color: isLong ? COL_LIMIT_BUY : COL_LIMIT_SELL,
          text: isLong ? "BUY" : "SELL",
          id: `th-entry-${tr.entry_ts}-${tr.entry_price}`,
          size: 1,
        });
      }
    }
    // Exit marker
    if (tr.exit_ts > 0 && tr.exit_price > 0) {
      const xt = snapBarTime(merged, tr.exit_ts);
      if (xt != null) {
        const win = (tr.pnl ?? 0) >= 0;
        const reason = (tr.reason || "").toUpperCase().slice(0, 6);
        markers.push({
          time: xt as Time,
          position: isLong ? "aboveBar" : "belowBar",
          shape: isLong ? "arrowDown" : "arrowUp",
          color: win ? COL_EXIT_WIN : COL_EXIT_LOSS,
          text: reason || (win ? "WIN" : "LOSS"),
          id: `th-exit-${tr.exit_ts}-${tr.exit_price}`,
          size: 1,
        });
      }
    }
  }

  // Sort by time (lightweight-charts requires ascending order)
  markers.sort((a, b) => Number(a.time) - Number(b.time));
  return markers;
}

type OverlayLineSeries = {
  emaFast: ISeriesApi<"Line">;
  emaSlow: ISeriesApi<"Line">;
  t3: ISeriesApi<"Line">;
  vwap: ISeriesApi<"Line">;
};

type OpenPnlPill = {
  key: string;
  topPx: number;
  usdText: string;
  qtyText: string;
  profit: boolean;
  loss: boolean;
};

export function ScalpTerminalChart({
  pairKey,
  closed,
  live,
  indicatorOverlay,
  height = 300,
  priceDecimals: _priceDecimals = 4,
  cdeOpenOrders = [],
  openPositions = [],
  tradeHistory = [],
  strategyBanner,
}: Props) {
  const [lineToggles, setLineToggles] = useState<IndicatorLineToggles>(loadIndicatorToggles);
  const [tradeLevelsOn, setTradeLevelsOn] = useState<boolean>(loadTradeLevelsVisible);

  useEffect(() => {
    try {
      localStorage.setItem(IND_TOGGLE_STORAGE, JSON.stringify(lineToggles));
    } catch {
      /* ignore */
    }
  }, [lineToggles]);

  useEffect(() => {
    try {
      localStorage.setItem(TRADE_LVL_STORAGE, tradeLevelsOn ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [tradeLevelsOn]);

  const flipInd = useCallback((key: keyof IndicatorLineToggles) => {
    setLineToggles((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const overlaySeriesRef = useRef<OverlayLineSeries | null>(null);
  const macdHistRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const priceLineRefs = useRef<IPriceLine[]>([]);
  // track what's already been fed to the chart so we can skip setData on live-only updates
  const closedRefKey = useRef<string>("");
  const overlayRefKey = useRef<string>("");

  const tradeLevelPriceLines = useMemo(() => {
    if (!tradeLevelsOn) return [];
    return buildPriceLines(cdeOpenOrders, openPositions);
  }, [cdeOpenOrders, openPositions, tradeLevelsOn]);

  const openFilledEntryLines = useMemo(() => buildOpenFilledEntryLines(openPositions), [openPositions]);

  const allPriceLines = useMemo(
    () => [...tradeLevelPriceLines, ...openFilledEntryLines],
    [tradeLevelPriceLines, openFilledEntryLines],
  );

  const [openPnlPills, setOpenPnlPills] = useState<OpenPnlPill[]>([]);

  const relayoutOpenPnlPills = useCallback(() => {
    const series = seriesRef.current;
    if (!series) {
      setOpenPnlPills([]);
      return;
    }
    const pills: OpenPnlPill[] = [];
    for (const p of openPositions ?? []) {
      if ((p.status || "").toLowerCase() !== "open" || !(p.entry > 0)) continue;
      const y = series.priceToCoordinate(p.entry);
      if (y == null || !Number.isFinite(Number(y))) continue;
      const u = effectiveUnrealizedUsd(p);
      let usdText: string;
      if (u == null || !Number.isFinite(u)) {
        usdText = "—";
      } else {
        const sign = u >= 0 ? "+" : "−";
        usdText = `${sign}$${Math.abs(u).toFixed(2)}`;
      }
      const profit = u != null && u >= 0;
      const loss = u != null && u < 0;
      pills.push({
        key: (p.entry_cl_ord_id || `${p.symbol}-${p.entry}`).slice(0, 48),
        topPx: Number(y),
        usdText,
        qtyText: String(Math.max(1, Math.round(Number(p.qty) || 1))),
        profit,
        loss,
      });
    }
    setOpenPnlPills(pills);
  }, [openPositions]);

  const relayoutOpenPnlPillsRef = useRef(relayoutOpenPnlPills);
  relayoutOpenPnlPillsRef.current = relayoutOpenPnlPills;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: TV_BG },
        textColor: TV_TEXT,
        fontSize: 11,
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'Trebuchet MS', Roboto, Ubuntu, 'Segoe UI', sans-serif",
      },
      grid: {
        vertLines: { color: TV_GRID, style: LineStyle.Solid },
        horzLines: { color: TV_GRID_SOFT, style: LineStyle.Solid },
      },
      crosshair: {
        mode: CrosshairMode.MagnetOHLC,
        vertLine: {
          color: "rgba(54, 58, 69, 0.75)",
          width: 1,
          style: LineStyle.Solid,
          labelBackgroundColor: "#1e2329",
        },
        horzLine: {
          color: "rgba(54, 58, 69, 0.75)",
          width: 1,
          style: LineStyle.Solid,
          labelBackgroundColor: "#1e2329",
        },
      },
      width: el.clientWidth,
      height,
      rightPriceScale: {
        borderColor: TV_BORDER,
        scaleMargins: { top: 0.08, bottom: 0.12 },
      },
      timeScale: {
        borderColor: TV_BORDER,
        timeVisible: true,
        secondsVisible: false,
        barSpacing: 5,
        rightOffset: 4,
      },
    });

    const lineOpts = {
      lineWidth: 2 as const,
      lineType: LineType.Curved,
      lineStyle: LineStyle.Solid,
      lastValueVisible: false,
      priceLineVisible: false,
      /** TV shows OHLC crosshair on candles; markers on every MA would clutter */
      crosshairMarkerVisible: false,
    } as const;
    const PANE_MAIN = 0;
    const emaFast = chart.addSeries(LineSeries, { color: COL_EMA_FAST, ...lineOpts }, PANE_MAIN);
    const emaSlow = chart.addSeries(LineSeries, { color: COL_EMA_SLOW, ...lineOpts }, PANE_MAIN);
    const t3Ser = chart.addSeries(LineSeries, { color: COL_T3, ...lineOpts }, PANE_MAIN);
    const vwapSer = chart.addSeries(LineSeries, { color: COL_VWAP, ...lineOpts }, PANE_MAIN);
    overlaySeriesRef.current = { emaFast, emaSlow, t3: t3Ser, vwap: vwapSer };

    const series = chart.addSeries(
      CandlestickSeries,
      {
        upColor: TV_CANDLE_UP,
        downColor: TV_CANDLE_DOWN,
        borderVisible: true,
        borderUpColor: TV_CANDLE_UP,
        borderDownColor: TV_CANDLE_DOWN,
        wickVisible: true,
        wickUpColor: TV_CANDLE_UP,
        wickDownColor: TV_CANDLE_DOWN,
      },
      PANE_MAIN,
    );

    const mainPane = chart.panes()[0]!;
    mainPane.setStretchFactor(3.2);
    const histPane = chart.addPane();
    histPane.setStretchFactor(1);
    const macdHist = chart.addSeries(
      HistogramSeries,
      {
        color: TV_CANDLE_UP,
        base: 0,
        priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
        lastValueVisible: false,
        priceLineVisible: false,
      },
      histPane.paneIndex(),
    );
    macdHistRef.current = macdHist;

    const markersPlugin = createSeriesMarkers(series, []);
    chartRef.current = chart;
    seriesRef.current = series;
    markersRef.current = markersPlugin;
    closedRefKey.current = "";
    overlayRefKey.current = "";

    requestAnimationFrame(() => relayoutOpenPnlPillsRef.current());

    const onVisibleRange = () => {
      requestAnimationFrame(() => relayoutOpenPnlPillsRef.current());
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRange);

    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({ width: containerRef.current.clientWidth, height });
      requestAnimationFrame(() => relayoutOpenPnlPillsRef.current());
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      try {
        markersPlugin.detach();
      } catch {
        /* ignore */
      }
      try {
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleRange);
      } catch {
        /* ignore */
      }
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      overlaySeriesRef.current = null;
      macdHistRef.current = null;
      markersRef.current = null;
      priceLineRefs.current = [];
      closedRefKey.current = "";
      overlayRefKey.current = "";
    };
  }, [pairKey, height]);

  // Closed candles effect — only runs setData when the closed array actually changes
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series) return;

    // Build a cheap identity key: count + last candle timestamp
    const lastT = closed && closed.length > 0 ? closed[closed.length - 1]!.t : 0;
    const key = `${closed?.length ?? 0}-${lastT}`;
    if (key === closedRefKey.current) return;
    closedRefKey.current = key;

    // lightweight-charts requires strictly ascending, unique timestamps. Dedupe
    // (last-wins for revised bars) and sort before setData — the backend occasionally
    // emits reordered rows when a backfill chunk lands after a fresher live bar.
    const byT = new Map<number, CandlestickData>();
    for (const c of closed ?? []) {
      if (!c || !Number.isFinite(c.t)) continue;
      byT.set(c.t, { time: c.t as Time, open: c.o, high: c.h, low: c.l, close: c.c });
    }
    const rows: CandlestickData[] = [...byT.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([, row]) => row);
    series.setData(rows);
    if (rows.length >= 2) chart?.timeScale().fitContent();
    requestAnimationFrame(() => relayoutOpenPnlPillsRef.current());
  }, [closed]);

  // Live candle effect — uses series.update() so the canvas is NOT fully redrawn
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !live || !Number.isFinite(live.t)) return;
    try {
      series.update({ time: live.t as Time, open: live.o, high: live.h, low: live.l, close: live.c });
    } catch {
      /* chart may not be ready yet */
    }
    requestAnimationFrame(() => relayoutOpenPnlPillsRef.current());
  }, [live]);

  // Historical indicator lines (aligned to closed-bar timestamps)
  useEffect(() => {
    const lines = overlaySeriesRef.current;
    if (!lines) return;
    const pts = indicatorOverlay;
    const lastT = pts && pts.length > 0 ? pts[pts.length - 1]!.t : 0;
    const key = `${pts?.length ?? 0}-${lastT}`;
    if (key === overlayRefKey.current) return;
    overlayRefKey.current = key;

    lines.emaFast.setData(overlayToLineData(pts, (p) => p.ema_fast));
    lines.emaSlow.setData(overlayToLineData(pts, (p) => p.ema_slow));
    lines.t3.setData(overlayToLineData(pts, (p) => p.t3));
    lines.vwap.setData(overlayToLineData(pts, (p) => p.vwap));

    const mh = macdHistRef.current;
    if (mh) {
      try {
        mh.setData(overlayToHistData(pts));
      } catch {
        /* chart teardown race */
      }
    }
  }, [indicatorOverlay]);

  // Show / hide overlay line series (TradingView-style toggles)
  useEffect(() => {
    const lines = overlaySeriesRef.current;
    if (!lines) return;
    lines.emaFast.applyOptions({ visible: lineToggles.emaFast });
    lines.emaSlow.applyOptions({ visible: lineToggles.emaSlow });
    lines.t3.applyOptions({ visible: lineToggles.t3 });
    lines.vwap.applyOptions({ visible: lineToggles.vwap });
  }, [lineToggles]);

  // Price lines effect
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    for (const pl of priceLineRefs.current) {
      try { series.removePriceLine(pl); } catch { /* ignore */ }
    }
    priceLineRefs.current = [];
    for (const ln of allPriceLines) {
      const handle = series.createPriceLine({
        price: ln.price,
        color: ln.color,
        lineWidth: ln.lineWidth ?? 1,
        lineStyle: ln.lineStyle,
        axisLabelVisible: ln.axisLabelVisible ?? true,
        title: ln.title,
        lineVisible: true,
      });
      priceLineRefs.current.push(handle);
    }
  }, [allPriceLines]);

  useLayoutEffect(() => {
    relayoutOpenPnlPills();
  }, [relayoutOpenPnlPills, allPriceLines, height, tradeLevelsOn]);

  // Markers effect
  useEffect(() => {
    const markersApi = markersRef.current;
    const series = seriesRef.current;
    if (!markersApi || !series) return;
    const merged = mergeClosedAndLive(closed, live);
    markersApi.setMarkers(buildMarkers(merged, openPositions, tradeHistory));
  }, [closed, live, openPositions, tradeHistory]);

  return (
    <div className="scalp-terminal-chart-wrap" style={{ position: "relative", width: "100%", height }}>
      {strategyBanner ? (
        <div className="chart-strategy-tv" aria-label="Active strategy">
          <div className="chart-strategy-tv-title">
            <span className="chart-strategy-tv-label">STRATEGY</span>
            <span className="chart-strategy-tv-mode">{strategyBanner.mode || "—"}</span>
          </div>
          {strategyBanner.source ? (
            <div className="chart-strategy-tv-meta">
              SRC <span className="chart-strategy-tv-pill">{strategyBanner.source}</span>
            </div>
          ) : null}
          {strategyBanner.regime ? (
            <div className="chart-strategy-tv-regime">{strategyBanner.regime}</div>
          ) : null}
        </div>
      ) : null}
      <div className="chart-ind-toggle-bar" aria-label="Indicator overlays">
        <span className="chart-ind-toggle-label">IND</span>
        <button
          type="button"
          className={`chart-ind-toggle${lineToggles.emaFast ? " on" : ""}`}
          style={{ borderColor: lineToggles.emaFast ? COL_EMA_FAST : undefined, color: lineToggles.emaFast ? COL_EMA_FAST : undefined }}
          onClick={() => flipInd("emaFast")}
          title="EMA fast (line + session reference)"
        >
          EMA F
        </button>
        <button
          type="button"
          className={`chart-ind-toggle${lineToggles.emaSlow ? " on" : ""}`}
          style={{ borderColor: lineToggles.emaSlow ? COL_EMA_SLOW : undefined, color: lineToggles.emaSlow ? COL_EMA_SLOW : undefined }}
          onClick={() => flipInd("emaSlow")}
          title="EMA slow"
        >
          EMA S
        </button>
        <button
          type="button"
          className={`chart-ind-toggle${lineToggles.t3 ? " on" : ""}`}
          style={{ borderColor: lineToggles.t3 ? COL_T3 : undefined, color: lineToggles.t3 ? COL_T3 : undefined }}
          onClick={() => flipInd("t3")}
          title="T3"
        >
          T3
        </button>
        <button
          type="button"
          className={`chart-ind-toggle${lineToggles.vwap ? " on" : ""}`}
          style={{ borderColor: lineToggles.vwap ? COL_VWAP : undefined, color: lineToggles.vwap ? COL_VWAP : undefined }}
          onClick={() => flipInd("vwap")}
          title="Session VWAP"
        >
          VWAP
        </button>
        <span className="chart-ind-toggle-sep" aria-hidden />
        <button
          type="button"
          className={`chart-ind-toggle${tradeLevelsOn ? " on" : ""}`}
          style={{
            borderColor: tradeLevelsOn ? COL_ENTRY : undefined,
            color: tradeLevelsOn ? COL_ENTRY : undefined,
          }}
          onClick={() => setTradeLevelsOn((v) => !v)}
          title="Entry / SL / TP and CDE resting order price lines (off = hide level lines; MACD histogram stays)"
        >
          LVLS
        </button>
      </div>
      <div className="chart-viewport" style={{ position: "relative", width: "100%", height }}>
        <div ref={containerRef} className="scalp-terminal-chart" style={{ width: "100%", height: "100%" }} />
        <div className="chart-open-pnl-layer" aria-hidden={openPnlPills.length === 0}>
          {openPnlPills.map((pill) => (
            <div
              key={pill.key}
              className={`chart-open-pnl-pill${pill.profit ? " chart-open-pnl-pill--profit" : ""}${pill.loss ? " chart-open-pnl-pill--loss" : ""}${!pill.profit && !pill.loss ? " chart-open-pnl-pill--neutral" : ""}`}
              style={{ top: pill.topPx }}
            >
              <span className="chart-open-pnl-qty">{pill.qtyText}</span>
              <span className="chart-open-pnl-usd">{pill.usdText}</span>
              <span className="chart-open-pnl-sfx">USD</span>
            </div>
          ))}
        </div>
        <div className="chart-macd-hint" aria-hidden>
          MACD <span className="chart-macd-hint-sub">histogram</span>
        </div>
      </div>
    </div>
  );
}
