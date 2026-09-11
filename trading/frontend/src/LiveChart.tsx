/**
 * Professional candlestick chart (TradingView Lightweight Charts).
 * UnifiedDecision markers via createSeriesMarkers; expand / fullscreen.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
  type Time,
} from "lightweight-charts";
import type {
  AgentVote,
  MarketMeta,
  TradeMarker,
  TradingAssetMode,
  UnifiedDecision,
} from "./api";
import {
  clearAnnotations,
  createAnnotation,
  deleteAnnotation,
  fetchAnnotations,
  fetchCandles,
  fetchChartMarkers,
  fetchUnifiedDecisionById,
  fetchUnifiedDecisions,
  updateAnnotation,
  type ChartAnnotation,
  type OhlcCandle,
} from "./api";
import {
  candleBucketTs,
  eventsToMarkerItems,
  groupMarkersOnCandle,
  mergeMarkerItems,
  mergeUnifiedDecisions,
  normalizeSymbol,
  toSeriesMarkers,
  toUnixSeconds,
  unifiedToMarkerItems,
  type DecisionMarkerItem,
  type GroupedMarker,
} from "./chartMarkers";
import { copyTextToClipboard } from "./clipboard";
import { assetModeLabel, he } from "./i18n/he";

const DEFAULT_TFS = ["1m", "5m", "15m", "1h", "4h", "1d"];
const SIZE_KEY = "trading.chart.sizeMode";
const HEIGHT_KEY = "trading.chart.heightPx";
const IND_KEY = "trading.indicatorConfig";
const MIN_CHART_H = 280;
const MAX_CHART_H = 1200;

type SizeMode = "normal" | "expanded";

type IndKey =
  | "sma_20"
  | "sma_50"
  | "sma_200"
  | "ema_20"
  | "ema_50"
  | "bb"
  | "vwap"
  | "rsi"
  | "macd"
  | "atr"
  | "volume";

type IndStyle = { color: string; lineWidth: 1 | 2 | 3 | 4; period?: number };

type IndicatorConfig = {
  enabled: Partial<Record<IndKey, boolean>>;
  styles: Partial<Record<IndKey, IndStyle>>;
};

const DEFAULT_STYLES: Record<IndKey, IndStyle> = {
  sma_20: { color: "#f0b429", lineWidth: 2 },
  sma_50: { color: "#c084fc", lineWidth: 2 },
  sma_200: { color: "#fb7185", lineWidth: 2 },
  ema_20: { color: "#7aa2ff", lineWidth: 2 },
  ema_50: { color: "#3dd6c6", lineWidth: 2 },
  bb: { color: "rgba(122,162,255,0.55)", lineWidth: 1 },
  vwap: { color: "#ffffff", lineWidth: 2 },
  rsi: { color: "#e0a21f", lineWidth: 2 },
  macd: { color: "#7aa2ff", lineWidth: 2 },
  atr: { color: "#f97316", lineWidth: 2 },
  volume: { color: "#3dd6c6", lineWidth: 1 },
};

const DEFAULT_IND: IndicatorConfig = {
  enabled: {
    ema_20: true,
    ema_50: true,
    volume: true,
    sma_20: false,
    sma_50: false,
    sma_200: false,
    bb: false,
    vwap: false,
    rsi: false,
    macd: false,
    atr: false,
  },
  styles: { ...DEFAULT_STYLES },
};

const PRESETS: Record<string, Partial<Record<IndKey, boolean>>> = {
  basic: { ema_20: true, ema_50: true, volume: true },
  momentum: { ema_20: true, rsi: true, macd: true, volume: false },
  volatility: { bb: true, atr: true, volume: true },
  trend: { sma_50: true, sma_200: true, ema_20: true, vwap: true },
};

type Props = {
  symbols: string[];
  trades: TradeMarker[];
  votes: AgentVote[];
  decisions?: Array<{
    id: string;
    symbol: string;
    side: string;
    confidence: number;
    rationale?: string;
    executed: boolean;
    fill_price?: number | null;
    quantity?: number | null;
    ts: number;
    votes?: AgentVote[];
  }>;
  timeframe: string;
  timeframes: string[];
  onTimeframe: (tf: string) => void;
  timeframeBusy?: boolean;
  marketMeta?: MarketMeta | null;
  realData: boolean;
  candleUpdates?: Array<{
    symbol: string;
    timeframe: string;
    candle: OhlcCandle;
    provider?: string;
  }>;
  wsState?: string;
  onLayoutModeChange?: (mode: SizeMode) => void;
  assetModes?: Record<string, TradingAssetMode | string>;
  onOpenAssetSettings?: (symbol: string) => void;
};

function fmtPrice(n: number) {
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (n >= 1) return n.toFixed(4);
  return n.toFixed(6);
}

function toChartTime(ts: number): Time {
  return Math.floor(ts) as Time;
}

function loadSizeMode(): SizeMode {
  try {
    const v = localStorage.getItem(SIZE_KEY);
    if (v === "expanded") return "expanded";
  } catch {
    /* */
  }
  return "normal";
}

function loadHeight(): number {
  try {
    const n = Number(localStorage.getItem(HEIGHT_KEY));
    if (Number.isFinite(n) && n >= MIN_CHART_H && n <= MAX_CHART_H) return n;
  } catch {
    /* */
  }
  return 420;
}

function loadIndicatorConfig(): IndicatorConfig {
  try {
    const raw = localStorage.getItem(IND_KEY);
    if (!raw) return DEFAULT_IND;
    const parsed = JSON.parse(raw) as Partial<IndicatorConfig>;
    return {
      enabled: { ...DEFAULT_IND.enabled, ...(parsed.enabled || {}) },
      styles: { ...DEFAULT_STYLES, ...(parsed.styles || {}) },
    };
  } catch {
    return DEFAULT_IND;
  }
}

function decisionToUnifiedStub(
  d: NonNullable<Props["decisions"]>[number]
): UnifiedDecision {
  const status = d.executed ? "FILLED" : d.side === "HOLD" ? "HOLD" : "SIGNAL";
  const qty = d.quantity ?? null;
  const px = d.fill_price ?? null;
  return {
    decision_id: d.id,
    symbol: d.symbol,
    decision_time: d.ts,
    final_action: d.side,
    confidence: d.confidence,
    status,
    quantity: qty,
    fill_price: px,
    total_value: qty != null && px != null ? Number(qty) * Number(px) : null,
    summary: d.rationale || undefined,
    primary_reasons: d.rationale ? [d.rationale] : undefined,
    agent_votes: (d.votes || []).map((v) => ({
      agent_id: v.agent_id,
      agent_name: v.agent_name,
      side: v.side,
      confidence: v.confidence,
      rationale: v.rationale,
    })),
  };
}

function tradeToUnifiedStub(t: TradeMarker): UnifiedDecision {
  return {
    decision_id: t.id,
    symbol: t.symbol,
    decision_time: t.ts,
    final_action: t.side,
    confidence: t.confidence ?? 0,
    status: "FILLED",
    quantity: t.quantity ?? null,
    fill_price: t.price ?? null,
    total_value:
      t.quantity != null && t.price != null ? Number(t.quantity) * Number(t.price) : null,
    agent_votes: (t.agents || []).map((a) => ({
      agent_id: a.id,
      agent_name: a.name,
      side: a.side,
      confidence: a.confidence,
    })),
  };
}

function valueAtTime(
  series: Array<{ time: number; value: number }> | undefined,
  time: number
): number | null {
  if (!series?.length) return null;
  let best: number | null = null;
  for (const p of series) {
    if (p.time === time) return p.value;
    if (p.time <= time) best = p.value;
  }
  return best;
}

function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  if (children == null || children === "" || children === "—") return null;
  return (
    <div className="ud-field">
      <dt>{label}</dt>
      <dd dir="ltr" className="mono">
        {children}
      </dd>
    </div>
  );
}

export default function LiveChart({
  symbols,
  trades,
  votes: _votes,
  decisions = [],
  timeframe,
  timeframes,
  onTimeframe,
  timeframeBusy = false,
  marketMeta,
  realData,
  candleUpdates = [],
  wsState = "connecting",
  onLayoutModeChange,
  assetModes = {},
  onOpenAssetSettings,
}: Props) {
  void _votes; // votes are not primary markers (UnifiedDecision only)
  const [symbol, setSymbol] = useState(symbols[0] || "BTC-USD");
  const active = symbols.includes(symbol) ? symbol : symbols[0] || symbol;
  const tfs = timeframes.length ? timeframes : DEFAULT_TFS;
  const meta = marketMeta?.symbols?.[active];
  const activeMode = String(assetModes[active] || assetModes[normalizeSymbol(active)] || "MONITOR_ONLY");

  const panelRef = useRef<HTMLElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const overlayRefs = useRef<Record<string, ISeriesApi<"Line">>>({});
  const priceLinesRef = useRef<Map<string, IPriceLine>>(new Map());
  const followLiveRef = useRef(true);
  const loadingOlderRef = useRef(false);
  const candlesRef = useRef<OhlcCandle[]>([]);
  const groupedRef = useRef<GroupedMarker[]>([]);
  const sizeBeforeFsRef = useRef<SizeMode>("normal");
  const indicatorsRef = useRef<Record<string, Array<{ time: number; value: number }>>>({});

  const [loading, setLoading] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [provider, setProvider] = useState("");
  const [hover, setHover] = useState<{
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    changePct: number;
    ind: Array<{ key: string; value: number }>;
  } | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [indicators, setIndicators] = useState<Record<string, Array<{ time: number; value: number }>>>({});
  const [indConfig, setIndConfig] = useState<IndicatorConfig>(() => loadIndicatorConfig());
  const [showVotes, setShowVotes] = useState(true);
  const [showFills, setShowFills] = useState(true);
  const [showBuy, setShowBuy] = useState(true);
  const [showSell, setShowSell] = useState(true);
  const [showHold, setShowHold] = useState(false);
  const [showBlocked, setShowBlocked] = useState(true);
  const [showDrawings, setShowDrawings] = useState(true);
  const [showIndicators, setShowIndicators] = useState(true);
  const [drawMode, setDrawMode] = useState<"none" | "SUPPORT" | "RESISTANCE" | "TREND_LINE" | "TEXT_NOTE">(
    "none"
  );
  const [annotations, setAnnotations] = useState<ChartAnnotation[]>([]);
  const [selectedAnn, setSelectedAnn] = useState<string | null>(null);
  const [groupedOpen, setGroupedOpen] = useState<GroupedMarker | null>(null);
  const [pickList, setPickList] = useState<DecisionMarkerItem[] | null>(null);
  const [drawerDecision, setDrawerDecision] = useState<UnifiedDecision | null>(null);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [techOpen, setTechOpen] = useState(false);
  const [copyMsg, setCopyMsg] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [unifiedRows, setUnifiedRows] = useState<UnifiedDecision[]>([]);
  const [persistedFallback, setPersistedFallback] = useState<DecisionMarkerItem[]>([]);
  const [unmappedFills, setUnmappedFills] = useState(0);
  const [sizeMode, setSizeMode] = useState<SizeMode>(() => loadSizeMode());
  const [chartHeight, setChartHeight] = useState(() => loadHeight());
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [isNarrow, setIsNarrow] = useState(false);
  const trendDraftRef = useRef<{ time: number; price: number } | null>(null);
  const logicalRangeRef = useRef<{ from: number; to: number } | null>(null);
  const drawModeRef = useRef(drawMode);
  const activeRef = useRef(active);
  const timeframeRef = useRef(timeframe);
  const openDecisionCardRef = useRef<(item: DecisionMarkerItem) => Promise<void>>(async () => undefined);

  const expanded = sizeMode === "expanded";
  const enabled = indConfig.enabled;
  const styles = indConfig.styles;

  indicatorsRef.current = indicators;

  const resizeChartToContainer = useCallback(() => {
    const chart = chartRef.current;
    const el = wrapRef.current;
    if (!chart || !el) return;
    const rect = el.getBoundingClientRect();
    const w = Math.max(1, Math.floor(rect.width));
    const h = Math.max(1, Math.floor(rect.height));
    chart.resize(w, h);
  }, []);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 720px)");
    const narrow = window.matchMedia("(max-width: 900px)");
    const apply = () => {
      setIsMobile(mq.matches);
      setIsNarrow(narrow.matches);
    };
    apply();
    mq.addEventListener("change", apply);
    narrow.addEventListener("change", apply);
    return () => {
      mq.removeEventListener("change", apply);
      narrow.removeEventListener("change", apply);
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(SIZE_KEY, sizeMode);
    } catch {
      /* */
    }
    onLayoutModeChange?.(sizeMode);
  }, [sizeMode, onLayoutModeChange]);

  useEffect(() => {
    try {
      localStorage.setItem(HEIGHT_KEY, String(chartHeight));
    } catch {
      /* */
    }
  }, [chartHeight]);

  useEffect(() => {
    try {
      localStorage.setItem(IND_KEY, JSON.stringify(indConfig));
    } catch {
      /* */
    }
  }, [indConfig]);

  useEffect(() => {
    document.body.classList.toggle("chart-expanded-body", expanded);
    return () => document.body.classList.remove("chart-expanded-body");
  }, [expanded]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const range = chart.timeScale().getVisibleLogicalRange();
    if (range) logicalRangeRef.current = { from: range.from, to: range.to };
    const id = requestAnimationFrame(() => {
      resizeChartToContainer();
      const saved = logicalRangeRef.current;
      if (saved) {
        try {
          chart.timeScale().setVisibleLogicalRange(saved);
        } catch {
          /* */
        }
      }
    });
    return () => cancelAnimationFrame(id);
  }, [expanded, drawerOpen, isFullscreen, resizeChartToContainer]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      resizeChartToContainer();
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [resizeChartToContainer, expanded]);

  useEffect(() => {
    const onFs = () => {
      const fs = !!document.fullscreenElement;
      setIsFullscreen(fs);
      requestAnimationFrame(() => resizeChartToContainer());
    };
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, [resizeChartToContainer]);

  // Merge live decisions/trades into unified rows without duplicates by decision_id.
  useEffect(() => {
    const sym = normalizeSymbol(active);
    const live: UnifiedDecision[] = [];
    for (const d of decisions) {
      if (normalizeSymbol(d.symbol) !== sym) continue;
      live.push(decisionToUnifiedStub(d));
    }
    for (const t of trades) {
      if (normalizeSymbol(t.symbol) !== sym) continue;
      live.push(tradeToUnifiedStub(t));
    }
    if (!live.length) return;
    setUnifiedRows((prev) => mergeUnifiedDecisions(prev, live));
  }, [decisions, trades, active]);

  const unifiedItems = useMemo(
    () => unifiedToMarkerItems(unifiedRows, timeframe),
    [unifiedRows, timeframe]
  );

  const allItems = useMemo(
    () => mergeMarkerItems(unifiedItems, persistedFallback),
    [unifiedItems, persistedFallback]
  );

  const filteredItems = useMemo(() => {
    return allItems.filter((it) => {
      if ((it.kind === "fill_buy" || it.kind === "fill_sell") && !showFills) return false;
      if ((it.kind === "signal" || it.kind.startsWith("vote_")) && !showVotes) return false;
      if ((it.kind === "blocked" || it.kind === "rejected") && !showBlocked) return false;
      if ((it.kind === "hold" || it.kind === "vote_hold") && !showHold) return false;
      if (it.action === "BUY" && !showBuy) return false;
      if (it.action === "SELL" && !showSell) return false;
      if (it.action === "HOLD" && !showHold) return false;
      return true;
    });
  }, [allItems, showVotes, showFills, showBuy, showSell, showHold, showBlocked]);

  const grouped = useMemo(
    () => groupMarkersOnCandle(filteredItems, timeframe),
    [filteredItems, timeframe]
  );
  groupedRef.current = grouped;

  const decisionCount = useMemo(() => allItems.length, [allItems]);
  const fillCount = useMemo(
    () => allItems.filter((i) => i.kind === "fill_buy" || i.kind === "fill_sell").length,
    [allItems]
  );

  const applyCandleData = useCallback((rows: OhlcCandle[]) => {
    candlesRef.current = rows;
    const series = candleSeriesRef.current;
    const vol = volumeSeriesRef.current;
    if (!series) return;
    series.setData(
      rows.map((c) => ({
        time: toChartTime(c.ts),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    );
    if (vol) {
      vol.setData(
        rows.map((c) => ({
          time: toChartTime(c.ts),
          value: c.volume,
          color: c.close >= c.open ? "rgba(61,214,198,0.45)" : "rgba(232,93,93,0.45)",
        }))
      );
    }
  }, []);

  const loadMarkers = useCallback(async (sym: string, tf: string, rows: OhlcCandle[]) => {
    const candleTs = new Set(rows.map((c) => c.ts));
    try {
      const fromTs = rows.length ? rows[0].ts - 1 : undefined;
      const toTs = rows.length ? rows[rows.length - 1].ts + 86400 : undefined;
      const [unifiedRes, markersRes] = await Promise.all([
        fetchUnifiedDecisions(sym, { fromTs, toTs, timeframe: tf }).catch(() => null),
        fetchChartMarkers(sym, tf, fromTs, toTs).catch(() => null),
      ]);
      const fromApi = mergeUnifiedDecisions(
        unifiedRes?.unified || [],
        markersRes?.unified || []
      );
      setUnifiedRows((prev) => mergeUnifiedDecisions(prev, fromApi));

      // Fallback only when no unified rows: fills/decisions from paper events (not votes).
      if (!fromApi.length && markersRes?.markers?.length) {
        setPersistedFallback(
          eventsToMarkerItems(markersRes.markers as Array<Record<string, unknown>>, tf)
        );
      } else {
        setPersistedFallback([]);
      }

      const fills = (markersRes?.markers || []).filter((m) => m.event_type === "fill");
      let unmapped = 0;
      for (const f of fills) {
        const bucket = candleBucketTs(Number(f.ts), tf);
        if (candleTs.size && !candleTs.has(bucket)) {
          unmapped += 1;
        }
      }
      setUnmappedFills(unmapped);
    } catch (err) {
      if (import.meta.env.DEV) console.warn("[chart-markers] load failed", err);
      setUnmappedFills(0);
    }
  }, []);

  const loadCandles = useCallback(
    async (sym: string, tf: string, before?: number) => {
      if (before) {
        if (loadingOlderRef.current) return;
        loadingOlderRef.current = true;
        setLoadingOlder(true);
      } else {
        setLoading(true);
        setPersistedFallback([]);
        setUnmappedFills(0);
      }
      try {
        const res = await fetchCandles(sym, tf, 500, before);
        setProvider(res.provider || res.source || "");
        setUnavailable(!!res.unavailable);
        setHasMore(!!res.has_more);
        setIndicators(res.indicators || {});
        const rows = (res.candles || []) as OhlcCandle[];
        let sorted = rows;
        if (before) {
          const merged = [...rows, ...candlesRef.current];
          const byTs = new Map<number, OhlcCandle>();
          for (const c of merged) byTs.set(c.ts, c);
          sorted = [...byTs.values()].sort((a, b) => a.ts - b.ts);
          applyCandleData(sorted);
        } else {
          applyCandleData(rows);
          if (followLiveRef.current && chartRef.current) {
            chartRef.current.timeScale().scrollToRealTime();
          }
        }
        await loadMarkers(sym, tf, sorted);
      } catch {
        if (!before) {
          setUnavailable(true);
          applyCandleData([]);
          setPersistedFallback([]);
        }
      } finally {
        setLoading(false);
        setLoadingOlder(false);
        loadingOlderRef.current = false;
      }
    },
    [applyCandleData, loadMarkers]
  );

  const loadAnnotations = useCallback(async (sym: string) => {
    try {
      const res = await fetchAnnotations(sym);
      setAnnotations(res.annotations || []);
    } catch {
      setAnnotations([]);
    }
  }, []);

  const openDecisionCard = useCallback(async (item: DecisionMarkerItem) => {
    setPickList(null);
    setDrawerLoading(true);
    setDrawerOpen(true);
    setAgentsOpen(false);
    setTechOpen(false);
    try {
      if (item.unified && (item.unified.primary_reasons || item.unified.summary)) {
        setDrawerDecision(item.unified);
        return;
      }
      const id = item.decisionId || item.eventId;
      if (id) {
        const res = await fetchUnifiedDecisionById(id);
        if (res.ok && res.unified) {
          setDrawerDecision(res.unified);
          setUnifiedRows((prev) => mergeUnifiedDecisions(prev, [res.unified!]));
          return;
        }
      }
      setDrawerDecision(
        item.unified || {
          decision_id: id || item.id,
          symbol: item.symbol,
          decision_time: item.timestamp,
          final_action: item.action,
          confidence: item.confidence || 0,
          status: item.status || (item.filled ? "FILLED" : "SIGNAL"),
          quantity: item.quantity,
          fill_price: item.price,
          total_value: item.totalValue,
          summary: item.reason || undefined,
        }
      );
    } finally {
      setDrawerLoading(false);
    }
  }, []);

  drawModeRef.current = drawMode;
  activeRef.current = active;
  timeframeRef.current = timeframe;
  openDecisionCardRef.current = openDecisionCard;

  // Create chart once
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#0f1419" },
        textColor: "#9aa4b2",
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.04)" },
        horzLines: { color: "rgba(255,255,255,0.04)" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
      timeScale: {
        borderColor: "rgba(255,255,255,0.08)",
        timeVisible: true,
        secondsVisible: false,
      },
    });
    const candleSeries = chart.addSeries(
      CandlestickSeries,
      {
        upColor: "#3dd6c6",
        downColor: "#e85d5d",
        borderUpColor: "#3dd6c6",
        borderDownColor: "#e85d5d",
        wickUpColor: "#3dd6c6",
        wickDownColor: "#e85d5d",
      },
      0
    );
    const volumeSeries = chart.addSeries(
      HistogramSeries,
      {
        priceFormat: { type: "volume" },
        priceScaleId: "vol",
      },
      1
    );
    try {
      volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.75, bottom: 0 },
      });
    } catch {
      /* */
    }
    try {
      chart.priceScale("right").applyOptions({
        scaleMargins: { top: 0.05, bottom: 0.25 },
      });
    } catch {
      /* */
    }

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    markersPluginRef.current = createSeriesMarkers(candleSeries, []);

    chart.subscribeCrosshairMove((param: MouseEventParams) => {
      if (!param.time || !param.seriesData) {
        setHover(null);
        return;
      }
      const raw = param.seriesData.get(candleSeries) as
        | { open: number; high: number; low: number; close: number; time: Time }
        | undefined;
      if (!raw) {
        setHover(null);
        return;
      }
      const volRaw = param.seriesData.get(volumeSeries) as { value: number } | undefined;
      const changePct = raw.open ? ((raw.close - raw.open) / raw.open) * 100 : 0;
      const t = Number(raw.time);
      const indMap = indicatorsRef.current;
      const indVals: Array<{ key: string; value: number }> = [];
      const pushInd = (key: string, seriesKey: string) => {
        const v = valueAtTime(indMap[seriesKey], t);
        if (v != null) indVals.push({ key, value: v });
      };
      pushInd("EMA20", "ema_20");
      pushInd("EMA50", "ema_50");
      pushInd("SMA20", "sma_20");
      pushInd("SMA50", "sma_50");
      pushInd("SMA200", "sma_200");
      pushInd("VWAP", "vwap");
      pushInd("RSI", "rsi_14");
      pushInd("MACD", "macd");
      pushInd("ATR", "atr_14");
      pushInd("BB-U", "bb_upper");
      pushInd("BB-M", "bb_mid");
      pushInd("BB-L", "bb_lower");
      setHover({
        time: t,
        open: raw.open,
        high: raw.high,
        low: raw.low,
        close: raw.close,
        volume: volRaw?.value ?? 0,
        changePct,
        ind: indVals,
      });
    });

    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range) return;
      const bars = candlesRef.current.length;
      if (bars > 0 && range.to < bars - 2) {
        followLiveRef.current = false;
      } else if (bars > 0 && range.to >= bars - 1.5) {
        followLiveRef.current = true;
      }
      if (range.from < 5 && hasMore && !loadingOlderRef.current) {
        const oldest = candlesRef.current[0];
        if (oldest) void loadCandles(active, timeframe, oldest.ts);
      }
    });

    const onClick = (param: MouseEventParams) => {
      if (!param.point || param.time == null) return;
      const time = Number(param.time);
      const mode = drawModeRef.current;
      const sym = activeRef.current;
      const tf = timeframeRef.current;
      if (mode === "none") {
        const hit = groupedRef.current.find((g) => Math.abs(g.time - time) < 1);
        if (import.meta.env.DEV) {
          console.debug("[chart-click]", {
            time,
            groups: groupedRef.current.length,
            groupTimes: groupedRef.current.slice(0, 5).map((g) => g.time),
            hit: !!hit,
          });
        }
        if (hit) {
          setGroupedOpen(hit);
          const fills = hit.items.filter((i) => i.kind === "fill_buy" || i.kind === "fill_sell");
          if (fills.length > 1) {
            setPickList(fills);
            setDrawerDecision(null);
            setDrawerOpen(true);
          } else if (hit.items.length === 1) {
            void openDecisionCardRef.current(hit.items[0]);
          } else if (fills.length === 1) {
            void openDecisionCardRef.current(fills[0]);
          } else {
            setPickList(hit.items);
            setDrawerDecision(null);
            setDrawerOpen(true);
          }
        }
        return;
      }
      const price = candleSeries.coordinateToPrice(param.point.y);
      if (price == null) return;
      if (mode === "SUPPORT" || mode === "RESISTANCE") {
        void createAnnotation({
          symbol: sym,
          annotation_type: mode,
          timeframe_scope: "all",
          price: Number(price),
          coordinates: { price: Number(price) },
          label: mode === "SUPPORT" ? he.support : he.resistance,
          importance: "medium",
          color: mode === "SUPPORT" ? "#3dd6c6" : "#e85d5d",
        }).then((r) => {
          if (r.annotation) setAnnotations((prev) => [...prev, r.annotation!]);
          setDrawMode("none");
        });
      } else if (mode === "TREND_LINE") {
        if (!trendDraftRef.current) {
          trendDraftRef.current = { time, price: Number(price) };
        } else {
          const a = trendDraftRef.current;
          void createAnnotation({
            symbol: sym,
            annotation_type: "TREND_LINE",
            timeframe_scope: tf,
            coordinates: {
              t1: a.time,
              p1: a.price,
              t2: time,
              p2: Number(price),
            },
            label: he.trendLine,
            color: "#7aa2ff",
          }).then((r) => {
            if (r.annotation) setAnnotations((prev) => [...prev, r.annotation!]);
            trendDraftRef.current = null;
            setDrawMode("none");
          });
        }
      } else if (mode === "TEXT_NOTE") {
        const note = window.prompt(he.notePrompt);
        if (!note) return;
        void createAnnotation({
          symbol: sym,
          annotation_type: "TEXT_NOTE",
          timeframe_scope: tf,
          price: Number(price),
          coordinates: { time, price: Number(price) },
          note,
          label: he.textNote,
          color: "#f0b429",
        }).then((r) => {
          if (r.annotation) setAnnotations((prev) => [...prev, r.annotation!]);
          setDrawMode("none");
        });
      }
    };
    chart.subscribeClick(onClick);

    // Fallback: some environments don't deliver LWC click params reliably.
    const onDomClick = (ev: MouseEvent) => {
      if (drawModeRef.current !== "none") return;
      const rect = el.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      try {
        const t = chart.timeScale().coordinateToTime(x);
        if (t == null) return;
        const time = Number(t);
        const hit = groupedRef.current.find((g) => Math.abs(g.time - time) < 1);
        if (import.meta.env.DEV) {
          console.debug("[chart-dom-click]", {
            time,
            groups: groupedRef.current.length,
            hit: !!hit,
          });
        }
        if (!hit) return;
        setGroupedOpen(hit);
        const fills = hit.items.filter((i) => i.kind === "fill_buy" || i.kind === "fill_sell");
        if (fills.length > 1) {
          setPickList(fills);
          setDrawerDecision(null);
          setDrawerOpen(true);
        } else if (hit.items.length === 1) {
          void openDecisionCardRef.current(hit.items[0]);
        } else if (fills.length === 1) {
          void openDecisionCardRef.current(fills[0]);
        } else {
          setPickList(hit.items);
          setDrawerDecision(null);
          setDrawerOpen(true);
        }
      } catch {
        /* */
      }
    };
    el.addEventListener("click", onDomClick);

    return () => {
      el.removeEventListener("click", onDomClick);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      markersPluginRef.current = null;
      overlayRefs.current = {};
      priceLinesRef.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const plugin = markersPluginRef.current;
    if (!plugin) return;
    const seriesMarkers = toSeriesMarkers(grouped).map((m) => ({
      time: toChartTime(m.time),
      position: m.position,
      color: m.color,
      shape: m.shape,
      text: m.text,
      size: m.size,
      id: m.id,
    }));
    plugin.setMarkers(seriesMarkers);
  }, [grouped]);

  useEffect(() => {
    followLiveRef.current = true;
    setGroupedOpen(null);
    setPickList(null);
    setDrawerDecision(null);
    setDrawerOpen(false);
    setUnifiedRows([]);
    setPersistedFallback([]);
    void loadCandles(active, timeframe);
    void loadAnnotations(active);
  }, [active, timeframe, loadCandles, loadAnnotations]);

  useEffect(() => {
    const series = candleSeriesRef.current;
    const vol = volumeSeriesRef.current;
    if (!series) return;
    for (const u of candleUpdates) {
      if (u.symbol !== active || u.timeframe !== timeframe) continue;
      const c = u.candle;
      const data = {
        time: toChartTime(c.ts),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      };
      series.update(data);
      if (vol && enabled.volume !== false) {
        vol.update({
          time: toChartTime(c.ts),
          value: c.volume,
          color: c.close >= c.open ? "rgba(61,214,198,0.45)" : "rgba(232,93,93,0.45)",
        });
      }
      const rows = candlesRef.current;
      if (rows.length && rows[rows.length - 1].ts === c.ts) {
        rows[rows.length - 1] = c;
      } else if (!rows.length || c.ts > rows[rows.length - 1].ts) {
        rows.push(c);
      }
      if (followLiveRef.current && chartRef.current) {
        chartRef.current.timeScale().scrollToRealTime();
      }
    }
  }, [candleUpdates, active, timeframe, enabled.volume]);

  // Volume visibility
  useEffect(() => {
    const vol = volumeSeriesRef.current;
    if (!vol) return;
    vol.applyOptions({ visible: showIndicators && enabled.volume !== false });
  }, [showIndicators, enabled.volume]);

  // Indicator overlays
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    for (const s of Object.values(overlayRefs.current)) {
      try {
        chart.removeSeries(s);
      } catch {
        /* */
      }
    }
    overlayRefs.current = {};
    if (!showIndicators) return;

    const addLine = (
      key: string,
      color: string,
      dataKey: string,
      lineWidth: 1 | 2 | 3 | 4 = 2,
      priceScaleId?: string,
      pane?: number
    ) => {
      const data = indicators[dataKey] || [];
      if (!data.length) return;
      const s = chart.addSeries(
        LineSeries,
        {
          color,
          lineWidth,
          priceLineVisible: false,
          lastValueVisible: false,
          priceScaleId: priceScaleId || "right",
        },
        pane ?? 0
      );
      s.setData(data.map((d) => ({ time: toChartTime(d.time), value: d.value })));
      overlayRefs.current[key] = s;
    };

    const lw = (k: IndKey) => styles[k]?.lineWidth || DEFAULT_STYLES[k].lineWidth;
    const col = (k: IndKey) => styles[k]?.color || DEFAULT_STYLES[k].color;

    if (enabled.sma_20) addLine("sma_20", col("sma_20"), "sma_20", lw("sma_20"));
    if (enabled.sma_50) addLine("sma_50", col("sma_50"), "sma_50", lw("sma_50"));
    if (enabled.sma_200) {
      if ((indicators.sma_200 || []).length) {
        addLine("sma_200", col("sma_200"), "sma_200", lw("sma_200"));
      }
      // else skip gracefully — backend may only expose sma_50
    }
    if (enabled.ema_20) addLine("ema_20", col("ema_20"), "ema_20", lw("ema_20"));
    if (enabled.ema_50) addLine("ema_50", col("ema_50"), "ema_50", lw("ema_50"));
    if (enabled.vwap) addLine("vwap", col("vwap"), "vwap", lw("vwap"));
    if (enabled.bb) {
      addLine("bb_u", col("bb"), "bb_upper", lw("bb"));
      addLine("bb_m", "rgba(122,162,255,0.35)", "bb_mid", 1);
      addLine("bb_l", col("bb"), "bb_lower", lw("bb"));
    }
    if (enabled.rsi) {
      addLine("rsi", col("rsi"), "rsi_14", lw("rsi"), "rsi", 2);
      try {
        overlayRefs.current.rsi?.priceScale().applyOptions({
          scaleMargins: { top: 0.82, bottom: 0 },
        });
      } catch {
        /* */
      }
    }
    if (enabled.macd) {
      addLine("macd", col("macd"), "macd", lw("macd"), "macd", 2);
      try {
        overlayRefs.current.macd?.priceScale().applyOptions({
          scaleMargins: { top: 0.82, bottom: 0 },
        });
      } catch {
        /* */
      }
    }
    if (enabled.atr) {
      addLine("atr", col("atr"), "atr_14", lw("atr"), "atr", 2);
      try {
        overlayRefs.current.atr?.priceScale().applyOptions({
          scaleMargins: { top: 0.82, bottom: 0 },
        });
      } catch {
        /* */
      }
    }
  }, [indicators, enabled, styles, showIndicators]);

  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    for (const line of priceLinesRef.current.values()) {
      try {
        series.removePriceLine(line);
      } catch {
        /* */
      }
    }
    priceLinesRef.current.clear();
    if (!showDrawings) return;
    for (const a of annotations) {
      if (!a.active) continue;
      if (a.timeframe_scope !== "all" && a.timeframe_scope !== timeframe) continue;
      if (a.annotation_type !== "SUPPORT" && a.annotation_type !== "RESISTANCE") continue;
      if (a.price == null) continue;
      const pl = series.createPriceLine({
        price: a.price,
        color: a.color || (a.annotation_type === "SUPPORT" ? "#3dd6c6" : "#e85d5d"),
        lineWidth: a.importance === "high" ? 2 : 1,
        lineStyle: a.line_style === "dashed" ? 2 : a.line_style === "dotted" ? 1 : 0,
        axisLabelVisible: true,
        title: `${a.label || a.annotation_type} ${fmtPrice(a.price)}`,
      });
      priceLinesRef.current.set(a.id, pl);
    }
  }, [annotations, showDrawings, timeframe]);

  const resetView = () => {
    chartRef.current?.priceScale("right").applyOptions({ autoScale: true });
    chartRef.current?.timeScale().fitContent();
  };
  const goLive = () => {
    followLiveRef.current = true;
    chartRef.current?.timeScale().scrollToRealTime();
  };

  const setMode = (mode: SizeMode) => {
    const chart = chartRef.current;
    if (chart) {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range) logicalRangeRef.current = { from: range.from, to: range.to };
    }
    setSizeMode(mode);
  };

  const closeDrawer = () => {
    setDrawerOpen(false);
    setGroupedOpen(null);
    setPickList(null);
    setDrawerDecision(null);
  };

  const applyPreset = (name: keyof typeof PRESETS) => {
    const preset = PRESETS[name];
    const nextEnabled: Partial<Record<IndKey, boolean>> = {};
    for (const k of Object.keys(DEFAULT_IND.enabled) as IndKey[]) {
      nextEnabled[k] = false;
    }
    for (const [k, v] of Object.entries(preset)) {
      nextEnabled[k as IndKey] = !!v;
    }
    if (name === "trend" && !(indicators.sma_200 || []).length) {
      nextEnabled.sma_200 = false;
    }
    setIndConfig((p) => ({ ...p, enabled: nextEnabled }));
    setShowIndicators(true);
  };

  const toggleInd = (k: IndKey) => {
    setIndConfig((p) => ({
      ...p,
      enabled: { ...p.enabled, [k]: !p.enabled[k] },
    }));
  };

  const hideAllInd = () => {
    const next: Partial<Record<IndKey, boolean>> = {};
    for (const k of Object.keys(DEFAULT_IND.enabled) as IndKey[]) next[k] = false;
    setIndConfig((p) => ({ ...p, enabled: next }));
  };

  const resetInd = () => setIndConfig(DEFAULT_IND);

  const enterFullscreen = async () => {
    sizeBeforeFsRef.current = sizeMode;
    if (!expanded) setMode("expanded");
    const el = panelRef.current;
    if (el && el.requestFullscreen) {
      try {
        await el.requestFullscreen();
      } catch {
        /* */
      }
    }
  };

  const exitFullscreen = async () => {
    if (document.fullscreenElement) {
      try {
        await document.exitFullscreen();
      } catch {
        /* */
      }
    }
  };

  const onResizeDrag = (e: { preventDefault: () => void; clientY: number }) => {
    if (isMobile || expanded) return;
    e.preventDefault();
    const startY = e.clientY;
    const startH = chartHeight;
    const onMove = (ev: MouseEvent) => {
      const next = Math.min(MAX_CHART_H, Math.max(MIN_CHART_H, startH + (ev.clientY - startY)));
      setChartHeight(next);
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      requestAnimationFrame(() => resizeChartToContainer());
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const onClearDrawings = async () => {
    if (!window.confirm(he.clearDrawingsConfirm)) return;
    await clearAnnotations(active);
    setAnnotations([]);
  };

  const onDeleteSelected = async () => {
    if (!selectedAnn) return;
    await deleteAnnotation(selectedAnn);
    setAnnotations((prev) => prev.filter((a) => a.id !== selectedAnn));
    setSelectedAnn(null);
  };

  const editSelected = async () => {
    const a = annotations.find((x) => x.id === selectedAnn);
    if (!a) return;
    const label = window.prompt(he.labelPrompt, a.label || "") ?? a.label;
    const note = window.prompt(he.notePrompt, a.note || "") ?? a.note;
    const importance = window.prompt(he.importancePrompt, a.importance || "medium");
    const res = await updateAnnotation(a.id, {
      label: label || undefined,
      note: note || undefined,
      importance: (importance as "low" | "medium" | "high") || "medium",
      annotation_type: a.annotation_type,
      timeframe_scope: a.timeframe_scope,
      coordinates: a.coordinates,
      price: a.price,
      color: a.color,
      line_style: a.line_style,
      symbol: a.symbol,
    });
    if (res.annotation) {
      setAnnotations((prev) => prev.map((x) => (x.id === a.id ? res.annotation! : x)));
    }
  };

  const copyTechnical = async () => {
    if (!drawerDecision) return;
    const payload = {
      decision_id: drawerDecision.decision_id,
      execution: drawerDecision.execution || {},
      raw_events: drawerDecision.raw_events || [],
      engine: drawerDecision.engine || {},
      portfolio_context: drawerDecision.portfolio_context || {},
    };
    const result = await copyTextToClipboard(JSON.stringify(payload, null, 2));
    setCopyMsg(result === "ok" ? he.copied : he.clipboardBlocked);
    window.setTimeout(() => setCopyMsg(null), 2000);
  };

  const canvasStyle =
    expanded || isFullscreen
      ? undefined
      : { height: isMobile ? "min(55vh, 420px)" : `${chartHeight}px` };

  const showDrawer = drawerOpen && (!!drawerDecision || !!pickList);
  const sma200Missing = enabled.sma_200 && !(indicators.sma_200 || []).length;

  const agreementText = (u: UnifiedDecision) => {
    const a = u.agreement;
    if (!a || (a.total == null && a.supporting == null)) return null;
    return `${he.supporting} ${a.supporting ?? 0} · ${he.opposing} ${a.opposing ?? 0} · ${a.total ?? 0}`;
  };

  const freshnessText = (u: UnifiedDecision) => {
    const dq = u.data_quality || {};
    const f = dq.freshness ?? dq.stale;
    if (f == null || f === "") return null;
    if (typeof f === "boolean") return f ? he.staleData : "live";
    return String(f);
  };

  const interpretedList = (u: UnifiedDecision): string[] => {
    const raw = u.interpreted_signals;
    if (!raw) return [];
    if (Array.isArray(raw)) {
      return raw
        .map((item) => {
          if (item == null) return "";
          if (typeof item === "string") return item;
          if (typeof item === "object") {
            const o = item as Record<string, unknown>;
            if (typeof o.explanation === "string") return o.explanation;
            if (typeof o.detail === "string") {
              const src = o.source != null ? `${String(o.source)}: ` : "";
              return `${src}${o.detail}`;
            }
            if (typeof o.text === "string") return o.text;
            if (typeof o.message === "string") return o.message;
            if (typeof o.signal === "string") {
              const parts = [String(o.signal)];
              if (o.direction != null) parts.push(String(o.direction));
              if (o.strength != null) parts.push(String(o.strength));
              return parts.join(" · ");
            }
            try {
              return JSON.stringify(o);
            } catch {
              return "";
            }
          }
          return String(item);
        })
        .filter(Boolean);
    }
    return Object.entries(raw).map(([k, v]) => {
      if (v != null && typeof v === "object") {
        try {
          return `${k}: ${JSON.stringify(v)}`;
        } catch {
          return `${k}: ${String(v)}`;
        }
      }
      return `${k}: ${String(v)}`;
    });
  };

  const indicatorsUsedList = (u: UnifiedDecision): string[] => {
    const ind = u.indicators_used || {};
    return Object.entries(ind)
      .filter(([, v]) => v != null && v !== "")
      .map(([k, v]) => `${k}=${typeof v === "number" ? Number(v).toFixed(4) : String(v)}`);
  };

  return (
    <section
      ref={panelRef}
      className={`panel live-chart-panel pro-chart size-${sizeMode}${isFullscreen ? " is-fullscreen" : ""}${
        showDrawer ? " has-drawer" : ""
      }${isNarrow ? " is-narrow" : ""}`}
      data-testid="live-chart-panel"
    >
      <div className="chart-toolbar chart-toolbar-compact">
        <div className="symbol-tabs" role="tablist">
          {symbols.map((sym) => {
            const mode = String(assetModes[sym] || "MONITOR_ONLY");
            return (
              <button
                key={sym}
                type="button"
                className={`symbol-tab ${sym === active ? "active" : ""}`}
                onClick={() => setSymbol(sym)}
              >
                <span dir="ltr">{sym}</span>
                <span className={`asset-mode-chip mode-${mode.toLowerCase()}`}>
                  {assetModeLabel(mode)}
                </span>
              </button>
            );
          })}
        </div>
        <div className="tf-tabs">
          {tfs.map((tf) => (
            <button
              key={tf}
              type="button"
              className={`tf-tab ${tf === timeframe ? "active" : ""}`}
              disabled={timeframeBusy}
              onClick={() => onTimeframe(tf)}
            >
              {tf}
            </button>
          ))}
        </div>
        <div className="chart-actions">
          <button
            type="button"
            className="chart-ctrl-btn"
            onClick={() => onOpenAssetSettings?.(active)}
            title={he.assetSettings}
          >
            {he.assetSettings}
          </button>
          <details className="chart-menu chart-menu-wide">
            <summary>{he.indicators}</summary>
            <div className="chart-menu-body ind-menu">
              <div className="ind-presets">
                <span className="muted">{he.indicatorPresets}</span>
                <button type="button" onClick={() => applyPreset("basic")}>
                  {he.presetBasic}
                </button>
                <button type="button" onClick={() => applyPreset("momentum")}>
                  {he.presetMomentum}
                </button>
                <button type="button" onClick={() => applyPreset("volatility")}>
                  {he.presetVolatility}
                </button>
                <button type="button" onClick={() => applyPreset("trend")}>
                  {he.presetTrend}
                </button>
              </div>
              {(
                [
                  ["ema_20", "EMA 20"],
                  ["ema_50", "EMA 50"],
                  ["sma_20", "SMA 20"],
                  ["sma_50", "SMA 50"],
                  ["sma_200", "SMA 200"],
                  ["bb", "Bollinger"],
                  ["vwap", "VWAP"],
                  ["rsi", "RSI 14"],
                  ["macd", "MACD"],
                  ["atr", "ATR 14"],
                  ["volume", he.showVolume],
                ] as const
              ).map(([k, label]) => (
                <div key={k} className="ind-row">
                  <label className="chk">
                    <input
                      type="checkbox"
                      checked={!!enabled[k]}
                      onChange={() => toggleInd(k)}
                    />
                    <button
                      type="button"
                      className="ind-legend-btn"
                      style={{ color: styles[k]?.color || DEFAULT_STYLES[k].color }}
                      onClick={(e) => {
                        e.preventDefault();
                        toggleInd(k);
                      }}
                    >
                      {label}
                    </button>
                  </label>
                  {k !== "volume" && k !== "bb" ? (
                    <label className="ind-width muted">
                      {he.lineWidth}
                      <select
                        value={styles[k]?.lineWidth || 2}
                        onChange={(e) =>
                          setIndConfig((p) => ({
                            ...p,
                            styles: {
                              ...p.styles,
                              [k]: {
                                ...(p.styles[k] || DEFAULT_STYLES[k]),
                                lineWidth: Number(e.target.value) as 1 | 2 | 3 | 4,
                              },
                            },
                          }))
                        }
                      >
                        {[1, 2, 3, 4].map((n) => (
                          <option key={n} value={n}>
                            {n}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  {k !== "volume" ? (
                    <input
                      type="color"
                      aria-label={he.color}
                      value={
                        (styles[k]?.color || DEFAULT_STYLES[k].color).startsWith("#")
                          ? styles[k]?.color || DEFAULT_STYLES[k].color
                          : "#7aa2ff"
                      }
                      onChange={(e) =>
                        setIndConfig((p) => ({
                          ...p,
                          styles: {
                            ...p.styles,
                            [k]: {
                              ...(p.styles[k] || DEFAULT_STYLES[k]),
                              color: e.target.value,
                            },
                          },
                        }))
                      }
                    />
                  ) : null}
                </div>
              ))}
              {sma200Missing ? (
                <p className="muted sma200-note">{he.sma200Unavailable}</p>
              ) : null}
              <div className="ind-actions">
                <button type="button" onClick={resetInd}>
                  {he.resetIndicators}
                </button>
                <button type="button" onClick={hideAllInd}>
                  {he.hideAllIndicators}
                </button>
              </div>
            </div>
          </details>
          <details className="chart-menu">
            <summary>
              {he.draw} {drawMode !== "none" ? `(${drawMode})` : ""}
            </summary>
            <div className="chart-menu-body">
              <button type="button" onClick={() => setDrawMode("SUPPORT")}>
                {he.support}
              </button>
              <button type="button" onClick={() => setDrawMode("RESISTANCE")}>
                {he.resistance}
              </button>
              <button type="button" onClick={() => setDrawMode("TREND_LINE")}>
                {he.trendLine}
              </button>
              <button type="button" onClick={() => setDrawMode("TEXT_NOTE")}>
                {he.textNote}
              </button>
              <button type="button" onClick={() => setDrawMode("none")}>
                {he.cancelDraw}
              </button>
              <button type="button" onClick={onDeleteSelected} disabled={!selectedAnn}>
                {he.deleteSelected}
              </button>
              <button type="button" onClick={onClearDrawings}>
                {he.clearDrawings}
              </button>
              {selectedAnn ? (
                <button type="button" onClick={editSelected}>
                  {he.editSelected}
                </button>
              ) : null}
            </div>
          </details>
          <details className="chart-menu">
            <summary>{he.markers}</summary>
            <div className="chart-menu-body">
              <label className="chk">
                <input
                  type="checkbox"
                  checked={showVotes}
                  onChange={(e) => setShowVotes(e.target.checked)}
                />
                {he.agentDecisions}
              </label>
              <label className="chk">
                <input
                  type="checkbox"
                  checked={showFills}
                  onChange={(e) => setShowFills(e.target.checked)}
                />
                {he.executedTrades}
              </label>
              <label className="chk">
                <input type="checkbox" checked={showBuy} onChange={(e) => setShowBuy(e.target.checked)} />
                {he.buyMarkers}
              </label>
              <label className="chk">
                <input
                  type="checkbox"
                  checked={showSell}
                  onChange={(e) => setShowSell(e.target.checked)}
                />
                {he.sellMarkers}
              </label>
              <label className="chk">
                <input
                  type="checkbox"
                  checked={showHold}
                  onChange={(e) => setShowHold(e.target.checked)}
                />
                {he.holdDecisions}
              </label>
              <label className="chk">
                <input
                  type="checkbox"
                  checked={showBlocked}
                  onChange={(e) => setShowBlocked(e.target.checked)}
                />
                {he.blockedDecisions}
              </label>
              <label className="chk">
                <input
                  type="checkbox"
                  checked={showDrawings}
                  onChange={(e) => setShowDrawings(e.target.checked)}
                />
                {he.manualDrawings}
              </label>
              <label className="chk">
                <input
                  type="checkbox"
                  checked={showIndicators}
                  onChange={(e) => setShowIndicators(e.target.checked)}
                />
                {he.indicators}
              </label>
              <div className="compact-legend menu-legend" aria-label={he.markers}>
                <span>
                  <i className="lg-tri buy" /> {he.legendBuy}
                </span>
                <span>
                  <i className="lg-arrow buy" /> {he.legendFillBuy}
                </span>
                <span>
                  <i className="lg-arrow sell" /> {he.legendFillSell}
                </span>
                <span>
                  <i className="lg-dot" /> {he.legendYellow}
                </span>
                <span>
                  <i className="lg-hold" /> {he.legendHold}
                </span>
              </div>
            </div>
          </details>
          <button type="button" className="chart-ctrl-btn" onClick={goLive}>
            {he.goLive}
          </button>
          <button type="button" className="chart-ctrl-btn" onClick={resetView}>
            {he.resetView}
          </button>
          {expanded ? (
            <button
              type="button"
              className="chart-ctrl-btn chart-ctrl-primary"
              title={he.collapse}
              onClick={() => setMode("normal")}
            >
              {he.collapse}
            </button>
          ) : (
            <button
              type="button"
              className="chart-ctrl-btn chart-ctrl-primary"
              title={he.expand}
              onClick={() => setMode("expanded")}
            >
              {he.expand}
            </button>
          )}
          {isFullscreen ? (
            <button type="button" className="chart-ctrl-btn" onClick={exitFullscreen}>
              {he.exitFullscreen}
            </button>
          ) : (
            <button type="button" className="chart-ctrl-btn" onClick={enterFullscreen}>
              {he.fullscreen}
            </button>
          )}
        </div>
        <div className="chart-status mono muted">
          <span className={`asset-mode-chip mode-${activeMode.toLowerCase()}`}>
            {assetModeLabel(activeMode)}
          </span>
          <span className={`ws-badge ${wsState === "live" ? "live" : wsState === "polling" ? "polling" : "dead"}`}>
            {wsState}
          </span>
          <span className={`data-badge ${realData ? "real" : "sim"}`}>
            {realData ? he.realData : he.simData}
          </span>
          <span dir="ltr">{provider || meta?.provider || "—"}</span>
          <span className="marker-counters">
            {he.decisionsCount}: {decisionCount} · {he.executedCount}: {fillCount}
          </span>
          {loading || loadingOlder ? <span className="loading-pill">{he.loading}</span> : null}
        </div>
      </div>

      {unmappedFills > 0 ? (
        <div className="marker-warn" role="status">
          {he.unmappedTrades(unmappedFills)}
        </div>
      ) : null}

      <div
        className={`chart-body${showDrawer ? " with-drawer" : ""}${
          showDrawer && isNarrow ? " drawer-overlay" : ""
        }`}
      >
        <div className="chart-main">
          {unavailable && !candlesRef.current.length ? (
            <div className="chart-unavailable">{he.dataUnavailable}</div>
          ) : null}
          <div className="chart-canvas" ref={wrapRef} style={canvasStyle} />
          {!isMobile && !expanded ? (
            <div
              className="chart-resize-handle"
              title="Drag to resize chart height"
              onMouseDown={onResizeDrag}
            />
          ) : null}
          {hover ? (
            <div className="chart-tooltip mono" dir="ltr">
              <div>{new Date(hover.time * 1000).toLocaleString()}</div>
              <div>
                O {fmtPrice(hover.open)} H {fmtPrice(hover.high)} L {fmtPrice(hover.low)} C{" "}
                {fmtPrice(hover.close)}
              </div>
              <div>
                Vol {hover.volume.toLocaleString()} · {hover.changePct >= 0 ? "+" : ""}
                {hover.changePct.toFixed(2)}%
              </div>
              {hover.ind.length ? (
                <div className="tooltip-inds">
                  {hover.ind
                    .filter((x) => {
                      const map: Record<string, IndKey> = {
                        EMA20: "ema_20",
                        EMA50: "ema_50",
                        SMA20: "sma_20",
                        SMA50: "sma_50",
                        SMA200: "sma_200",
                        VWAP: "vwap",
                        RSI: "rsi",
                        MACD: "macd",
                        ATR: "atr",
                        "BB-U": "bb",
                        "BB-M": "bb",
                        "BB-L": "bb",
                      };
                      const k = map[x.key];
                      return k ? !!enabled[k] && showIndicators : false;
                    })
                    .map((x) => (
                      <span key={x.key}>
                        {x.key} {fmtPrice(x.value)}
                      </span>
                    ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        {showDrawer ? (
          <aside className="chart-drawer unified-drawer" dir="rtl" aria-label={he.markerDetails}>
            <div className="chart-drawer-head sticky-drawer-head">
              <h3>{pickList && !drawerDecision ? he.pickFill : he.markerDetails}</h3>
              <button
                type="button"
                className="chart-drawer-close"
                aria-label={he.closeDetails}
                onClick={closeDrawer}
              >
                ×
              </button>
            </div>
            <div className="chart-drawer-body">
              {drawerLoading ? <p className="muted">{he.loading}</p> : null}

              {pickList && !drawerDecision ? (
                <ul className="fill-pick-list">
                  {pickList.map((it) => (
                    <li key={it.id}>
                      <button type="button" onClick={() => void openDecisionCard(it)}>
                        <span className={`tag ${it.action}`}>{it.action}</span>
                        <span dir="ltr" className="mono">
                          {it.quantity != null ? Number(it.quantity).toFixed(4) : ""}{" "}
                          {it.price != null ? `@ ${fmtPrice(it.price)}` : ""}
                        </span>
                        <span className="muted">
                          {new Date(toUnixSeconds(it.timestamp) * 1000).toLocaleString()}
                        </span>
                        <span>{he.openDecisionCard}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}

              {drawerDecision ? (
                <div className="unified-card">
                  <section className="ud-outcome">
                    <div className="row ud-outcome-row">
                      <span className={`tag ${drawerDecision.final_action}`}>
                        {drawerDecision.final_action}
                      </span>
                      <span className="ud-status">{drawerDecision.status}</span>
                    </div>
                    {drawerDecision.summary ? (
                      <p className="ud-summary">{drawerDecision.summary}</p>
                    ) : (
                      <p className="muted">{he.noSummary}</p>
                    )}
                    <dl className="ud-dl">
                      <Field label={he.fillPrice}>
                        {drawerDecision.fill_price != null
                          ? fmtPrice(Number(drawerDecision.fill_price))
                          : null}
                      </Field>
                      <Field label={he.quantity}>
                        {drawerDecision.quantity != null
                          ? Number(drawerDecision.quantity).toFixed(4)
                          : null}
                      </Field>
                      <Field label={he.totalValue}>
                        {drawerDecision.total_value != null
                          ? fmtPrice(Number(drawerDecision.total_value))
                          : null}
                      </Field>
                      <Field label={he.executionTime}>
                        {new Date(
                          toUnixSeconds(Number(drawerDecision.decision_time)) * 1000
                        ).toLocaleString()}
                      </Field>
                      <Field label={he.symbol}>
                        <span dir="ltr">{drawerDecision.symbol}</span>
                      </Field>
                    </dl>
                  </section>

                  {drawerDecision.primary_reasons?.length ? (
                    <section>
                      <h4>{he.whySystemActed}</h4>
                      <ul className="ud-list">
                        {drawerDecision.primary_reasons.map((r, i) => (
                          <li key={i}>{r}</li>
                        ))}
                      </ul>
                    </section>
                  ) : null}

                  {drawerDecision.risk_factors?.length ? (
                    <section>
                      <h4>{he.whyMayBeWrong}</h4>
                      <ul className="ud-list risk">
                        {drawerDecision.risk_factors.map((r, i) => (
                          <li key={i}>{r}</li>
                        ))}
                      </ul>
                    </section>
                  ) : null}

                  <section>
                    <h4>{he.decisionStrength}</h4>
                    <dl className="ud-dl">
                      <Field label={he.confidence}>
                        {drawerDecision.confidence != null
                          ? `${(Number(drawerDecision.confidence) <= 1
                              ? Number(drawerDecision.confidence) * 100
                              : Number(drawerDecision.confidence)
                            ).toFixed(0)}%`
                          : null}
                      </Field>
                      <Field label={he.agreement}>{agreementText(drawerDecision)}</Field>
                      <Field label={he.riskLevel}>{drawerDecision.risk_level || null}</Field>
                      <Field label={he.freshness}>{freshnessText(drawerDecision)}</Field>
                      <Field label={he.status}>{drawerDecision.status || null}</Field>
                    </dl>
                  </section>

                  {interpretedList(drawerDecision).length ||
                  indicatorsUsedList(drawerDecision).length ? (
                    <section>
                      <h4>{he.marketEvidence}</h4>
                      <ul className="ud-list">
                        {interpretedList(drawerDecision).map((r, i) => (
                          <li key={`i-${i}`}>{r}</li>
                        ))}
                        {indicatorsUsedList(drawerDecision).map((r, i) => (
                          <li key={`ind-${i}`} dir="ltr" className="mono">
                            {r}
                          </li>
                        ))}
                      </ul>
                    </section>
                  ) : null}

                  {(drawerDecision.agent_votes || []).length ? (
                    <details
                      className="ud-collapse"
                      open={agentsOpen}
                      onToggle={(e) => setAgentsOpen((e.target as HTMLDetailsElement).open)}
                    >
                      <summary>{he.agentOpinions}</summary>
                      <ul className="agent-vote-rows">
                        {(drawerDecision.agent_votes || []).map((v, i) => {
                          const side = String(v.side || "HOLD").toUpperCase();
                          const final = String(drawerDecision.final_action || "").toUpperCase();
                          let cls = "hold";
                          if (side === "HOLD") cls = "hold";
                          else if (side === final) cls = "support";
                          else cls = "oppose";
                          return (
                            <li key={`${v.agent_id || i}-${i}`} className={`agent-vote-row ${cls}`}>
                              <strong>{v.agent_name || v.agent_id || "—"}</strong>
                              <span className={`tag ${side}`}>{side}</span>
                              <span dir="ltr" className="mono muted">
                                {v.confidence != null
                                  ? `${(Number(v.confidence) <= 1
                                      ? Number(v.confidence) * 100
                                      : Number(v.confidence)
                                    ).toFixed(0)}%`
                                  : ""}
                              </span>
                              {v.rationale ? <p className="muted">{v.rationale}</p> : null}
                            </li>
                          );
                        })}
                      </ul>
                    </details>
                  ) : null}

                  <details
                    className="ud-collapse"
                    open={techOpen}
                    onToggle={(e) => setTechOpen((e.target as HTMLDetailsElement).open)}
                  >
                    <summary>
                      {he.technicalDetails}
                      <button
                        type="button"
                        className="btn-copy tiny"
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          void copyTechnical();
                        }}
                      >
                        {he.copyTechnical}
                      </button>
                      {copyMsg ? <span className="copy-toast mono">{copyMsg}</span> : null}
                    </summary>
                    <dl className="ud-dl">
                      <Field label={he.decisionId}>
                        <span dir="ltr">{drawerDecision.decision_id}</span>
                      </Field>
                      <Field label={he.paperOrderId}>
                        {drawerDecision.execution?.order_id != null
                          ? String(drawerDecision.execution.order_id)
                          : null}
                      </Field>
                      <Field label={he.fillId}>
                        {drawerDecision.execution?.fill_id != null
                          ? String(drawerDecision.execution.fill_id)
                          : null}
                      </Field>
                    </dl>
                    {(drawerDecision.raw_events || []).length ? (
                      <pre className="detail-pre" dir="ltr">
                        {JSON.stringify(drawerDecision.raw_events, null, 2)}
                      </pre>
                    ) : null}
                  </details>

                  {groupedOpen ? (
                    <p className="mono muted">
                      {he.candleTime}{" "}
                      <span dir="ltr">
                        {new Date(groupedOpen.time * 1000).toLocaleString()}
                      </span>
                    </p>
                  ) : null}
                </div>
              ) : null}

              {annotations.length ? (
                <>
                  <h4>{he.drawings}</h4>
                  <ul className="ann-list">
                    {annotations.map((a) => (
                      <li key={a.id}>
                        <button
                          type="button"
                          className={selectedAnn === a.id ? "active" : ""}
                          onClick={() => setSelectedAnn(a.id)}
                        >
                          {a.annotation_type} {a.price != null ? fmtPrice(a.price) : ""}{" "}
                          {a.label || ""}
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
            </div>
          </aside>
        ) : null}
      </div>
    </section>
  );
}
