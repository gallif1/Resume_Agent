/**
 * Professional candlestick chart (TradingView Lightweight Charts).
 * Persisted decision/fill markers via createSeriesMarkers; expand / fullscreen.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import type { AgentVote, MarketMeta, TradeMarker } from "./api";
import {
  clearAnnotations,
  createAnnotation,
  deleteAnnotation,
  fetchAnnotations,
  fetchCandles,
  fetchChartMarkers,
  updateAnnotation,
  type ChartAnnotation,
  type OhlcCandle,
} from "./api";
import {
  candleBucketTs,
  eventsToMarkerItems,
  groupMarkersOnCandle,
  mergeMarkerItems,
  normalizeSymbol,
  toSeriesMarkers,
  toUnixSeconds,
  type DecisionMarkerItem,
  type GroupedMarker,
} from "./chartMarkers";
import { he } from "./i18n/he";

const DEFAULT_TFS = ["1m", "5m", "15m", "1h", "4h", "1d"];
const SIZE_KEY = "trading.chart.sizeMode";
const HEIGHT_KEY = "trading.chart.heightPx";
const MIN_CHART_H = 280;
const MAX_CHART_H = 1200;

type SizeMode = "normal" | "expanded";

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
  compact?: boolean;
};

type IndKey = "sma_20" | "sma_50" | "ema_20" | "ema_50" | "bb" | "vwap" | "rsi" | "macd";

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

function liveTradeToItems(
  trades: TradeMarker[],
  votes: AgentVote[],
  decisions: Props["decisions"],
  symbol: string,
  timeframe: string
): DecisionMarkerItem[] {
  const sym = normalizeSymbol(symbol);
  const events: Array<Record<string, unknown>> = [];
  for (const t of trades) {
    if (normalizeSymbol(t.symbol) !== sym) continue;
    events.push({
      event_type: "fill",
      event_id: t.id,
      id: `fill:${t.id}`,
      key: `fill:${t.id}`,
      symbol: sym,
      side: t.side,
      ts: t.ts,
      price: t.price,
      quantity: t.quantity,
      confidence: t.confidence,
      status: "FILLED",
      payload: {
        fill_id: t.id,
        order_id: t.id,
        fill_price: t.price,
        agents: (t.agents || []).map((a) => ({
          agent_name: a.name || a.id,
          agent_id: a.id,
        })),
        final_decision: t.side,
      },
    });
  }
  for (const v of votes) {
    if (normalizeSymbol(v.symbol) !== sym) continue;
    if (v.side !== "BUY" && v.side !== "SELL" && v.side !== "HOLD") continue;
    events.push({
      event_type: "agent_vote",
      event_id: `${v.agent_id}-${v.ts}`,
      id: `agent_vote:${v.agent_id}-${v.ts}`,
      key: `agent_vote:${v.agent_id}-${v.ts}`,
      symbol: sym,
      side: v.side,
      ts: v.ts,
      confidence: v.confidence,
      status: "VOTE",
      payload: {
        agent_name: v.agent_name,
        agent_id: v.agent_id,
        reason: v.rationale,
        final_decision: v.side,
      },
    });
  }
  for (const d of decisions || []) {
    if (normalizeSymbol(d.symbol) !== sym) continue;
    if (d.executed) {
      events.push({
        event_type: "fill",
        event_id: d.id,
        id: `fill:${d.id}`,
        key: `fill:${d.id}`,
        symbol: sym,
        side: d.side,
        ts: d.ts,
        price: d.fill_price,
        quantity: d.quantity,
        confidence: d.confidence,
        status: "FILLED",
        payload: {
          fill_id: d.id,
          order_id: d.id,
          fill_price: d.fill_price,
          agents: (d.votes || []).map((a) => ({
            agent_name: a.agent_name,
            agent_id: a.agent_id,
            side: a.side,
            confidence: a.confidence,
          })),
          final_decision: d.side,
          rationale: d.rationale,
        },
      });
    } else if (d.side !== "HOLD") {
      events.push({
        event_type: "decision",
        event_id: d.id,
        id: `decision:${d.id}`,
        key: `decision:${d.id}`,
        symbol: sym,
        side: d.side,
        ts: d.ts,
        confidence: d.confidence,
        status: "SIGNAL",
        payload: {
          rationale: d.rationale,
          skip_reason: d.rationale,
          final_decision: d.side,
          agents: (d.votes || []).map((a) => ({
            agent_name: a.agent_name,
            agent_id: a.agent_id,
          })),
        },
      });
    }
  }
  return eventsToMarkerItems(events, timeframe);
}

export default function LiveChart({
  symbols,
  trades,
  votes,
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
  compact = false,
}: Props) {
  const [symbol, setSymbol] = useState(symbols[0] || "BTC-USD");
  const active = symbols.includes(symbol) ? symbol : symbols[0] || symbol;
  const tfs = timeframes.length ? timeframes : DEFAULT_TFS;
  const meta = marketMeta?.symbols?.[active];

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
  } | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [indicators, setIndicators] = useState<Record<string, Array<{ time: number; value: number }>>>({});
  const [showVotes, setShowVotes] = useState(true);
  const [showFills, setShowFills] = useState(true);
  const [showBuy, setShowBuy] = useState(true);
  const [showSell, setShowSell] = useState(true);
  const [showHold, setShowHold] = useState(false);
  const [showDrawings, setShowDrawings] = useState(true);
  const [showIndicators, setShowIndicators] = useState(true);
  const [activeInd, setActiveInd] = useState<Partial<Record<IndKey, boolean>>>({
    sma_20: true,
    sma_50: false,
    ema_20: false,
  });
  const [drawMode, setDrawMode] = useState<"none" | "SUPPORT" | "RESISTANCE" | "TREND_LINE" | "TEXT_NOTE">(
    "none"
  );
  const [annotations, setAnnotations] = useState<ChartAnnotation[]>([]);
  const [selectedAnn, setSelectedAnn] = useState<string | null>(null);
  const [groupedOpen, setGroupedOpen] = useState<GroupedMarker | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [persistedMarkers, setPersistedMarkers] = useState<DecisionMarkerItem[]>([]);
  const [unmappedFills, setUnmappedFills] = useState(0);
  const [sizeMode, setSizeMode] = useState<SizeMode>(() => loadSizeMode());
  const [chartHeight, setChartHeight] = useState(() => loadHeight());
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [isNarrow, setIsNarrow] = useState(false);
  const trendDraftRef = useRef<{ time: number; price: number } | null>(null);
  const logicalRangeRef = useRef<{ from: number; to: number } | null>(null);

  const lastCandle = candlesRef.current[candlesRef.current.length - 1];
  const expanded = sizeMode === "expanded";

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
    document.body.classList.toggle("chart-expanded-body", expanded);
    return () => document.body.classList.remove("chart-expanded-body");
  }, [expanded]);

  // Preserve zoom/scroll across expand/collapse, then resize to the live container.
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
      if (!fs && sizeBeforeFsRef.current) {
        // keep expanded workspace after leaving browser fullscreen unless user collapsed
      }
      requestAnimationFrame(() => resizeChartToContainer());
    };
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, [resizeChartToContainer]);

  const liveItems = useMemo(
    () => liveTradeToItems(trades, votes, decisions, active, timeframe),
    [trades, votes, decisions, active, timeframe]
  );

  const allItems = useMemo(
    () => mergeMarkerItems(persistedMarkers, liveItems),
    [persistedMarkers, liveItems]
  );

  const filteredItems = useMemo(() => {
    return allItems.filter((it) => {
      if (it.kind.startsWith("fill_") && !showFills) return false;
      if ((it.kind.startsWith("vote_") || it.kind === "rejected") && !showVotes) return false;
      if (it.kind === "vote_hold" && !showHold) return false;
      if (it.action === "BUY" && !showBuy) return false;
      if (it.action === "SELL" && !showSell) return false;
      if (it.action === "HOLD" && !showHold) return false;
      return true;
    });
  }, [allItems, showVotes, showFills, showBuy, showSell, showHold]);

  const grouped = useMemo(
    () => groupMarkersOnCandle(filteredItems, timeframe),
    [filteredItems, timeframe]
  );
  groupedRef.current = grouped;

  const decisionCount = useMemo(
    () =>
      allItems.filter(
        (i) => i.eventType === "agent_vote" || i.eventType === "decision" || i.kind.startsWith("vote_")
      ).length,
    [allItems]
  );
  const fillCount = useMemo(
    () => allItems.filter((i) => i.kind.startsWith("fill_") || i.filled).length,
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

  const loadMarkers = useCallback(
    async (sym: string, tf: string, rows: OhlcCandle[]) => {
      const candleTs = new Set(rows.map((c) => c.ts));
      try {
        const fromTs = rows.length ? rows[0].ts - 1 : undefined;
        const toTs = rows.length ? rows[rows.length - 1].ts + 86400 : undefined;
        const [inRange, allSym] = await Promise.all([
          fetchChartMarkers(sym, tf, fromTs, toTs),
          fetchChartMarkers(sym, tf),
        ]);
        const items = eventsToMarkerItems(
          (inRange.markers || []) as Array<Record<string, unknown>>,
          tf
        );
        setPersistedMarkers(items);

        const fills = (allSym.markers || []).filter((m) => m.event_type === "fill");
        let unmapped = 0;
        for (const f of fills) {
          const bucket = candleBucketTs(Number(f.ts), tf);
          if (candleTs.size && !candleTs.has(bucket)) {
            unmapped += 1;
            if (import.meta.env.DEV) {
              console.warn("[chart-markers] unmapped fill", {
                id: f.id,
                symbol: f.symbol,
                ts: f.ts,
                bucket,
                timeframe: tf,
                reason: "no candle at bucket open",
              });
            }
          }
        }
        setUnmappedFills(unmapped);
      } catch (err) {
        if (import.meta.env.DEV) console.warn("[chart-markers] load failed", err);
        setPersistedMarkers([]);
        setUnmappedFills(0);
      }
    },
    []
  );

  const loadCandles = useCallback(
    async (sym: string, tf: string, before?: number) => {
      if (before) {
        if (loadingOlderRef.current) return;
        loadingOlderRef.current = true;
        setLoadingOlder(true);
      } else {
        setLoading(true);
        setPersistedMarkers([]);
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
          setPersistedMarkers([]);
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
    // Volume lives on pane 1 — priceScale(id) defaults to pane 0 and throws without paneIndex.
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.15, bottom: 0 },
    });
    chart.priceScale("right").applyOptions({
      scaleMargins: { top: 0.05, bottom: 0.25 },
    });

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
      setHover({
        time: Number(raw.time),
        open: raw.open,
        high: raw.high,
        low: raw.low,
        close: raw.close,
        volume: volRaw?.value ?? 0,
        changePct,
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
      const price = candleSeries.coordinateToPrice(param.point.y);
      if (price == null) return;
      const time = Number(param.time);
      if (drawMode === "SUPPORT" || drawMode === "RESISTANCE") {
        void createAnnotation({
          symbol: active,
          annotation_type: drawMode,
          timeframe_scope: "all",
          price: Number(price),
          coordinates: { price: Number(price) },
          label: drawMode === "SUPPORT" ? he.support : he.resistance,
          importance: "medium",
          color: drawMode === "SUPPORT" ? "#3dd6c6" : "#e85d5d",
        }).then((r) => {
          if (r.annotation) setAnnotations((prev) => [...prev, r.annotation!]);
          setDrawMode("none");
        });
      } else if (drawMode === "TREND_LINE") {
        if (!trendDraftRef.current) {
          trendDraftRef.current = { time, price: Number(price) };
        } else {
          const a = trendDraftRef.current;
          void createAnnotation({
            symbol: active,
            annotation_type: "TREND_LINE",
            timeframe_scope: timeframe,
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
      } else if (drawMode === "TEXT_NOTE") {
        const note = window.prompt(he.notePrompt);
        if (!note) return;
        void createAnnotation({
          symbol: active,
          annotation_type: "TEXT_NOTE",
          timeframe_scope: timeframe,
          price: Number(price),
          coordinates: { time, price: Number(price) },
          note,
          label: he.textNote,
          color: "#f0b429",
        }).then((r) => {
          if (r.annotation) setAnnotations((prev) => [...prev, r.annotation!]);
          setDrawMode("none");
        });
      } else {
        const hit = groupedRef.current.find((g) => Math.abs(g.time - time) < 1);
        if (hit) {
          setGroupedOpen(hit);
          setDrawerOpen(true);
        }
      }
    };
    chart.subscribeClick(onClick);

    return () => {
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

  // Attach series markers whenever groups change (survives live candle updates)
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

  // Reload on symbol/timeframe — clear prior symbol markers first
  useEffect(() => {
    followLiveRef.current = true;
    setGroupedOpen(null);
    setPersistedMarkers([]);
    void loadCandles(active, timeframe);
    void loadAnnotations(active);
  }, [active, timeframe, loadCandles, loadAnnotations]);

  // Incremental candle updates — markers stay via separate effect
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
      if (vol) {
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
  }, [candleUpdates, active, timeframe]);

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

    const addLine = (key: string, color: string, dataKey: string) => {
      const data = indicators[dataKey] || [];
      if (!data.length) return;
      const s = chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      s.setData(data.map((d) => ({ time: toChartTime(d.time), value: d.value })));
      overlayRefs.current[key] = s;
    };
    if (activeInd.sma_20) addLine("sma_20", "#f0b429", "sma_20");
    if (activeInd.sma_50) addLine("sma_50", "#c084fc", "sma_50");
    if (activeInd.ema_20) addLine("ema_20", "#7aa2ff", "ema_20");
    if (activeInd.ema_50) addLine("ema_50", "#3dd6c6", "ema_50");
    if (activeInd.vwap) addLine("vwap", "#ffffff", "vwap");
    if (activeInd.bb) {
      addLine("bb_u", "rgba(122,162,255,0.5)", "bb_upper");
      addLine("bb_m", "rgba(122,162,255,0.35)", "bb_mid");
      addLine("bb_l", "rgba(122,162,255,0.5)", "bb_lower");
    }
    if (activeInd.rsi) {
      const s = chart.addSeries(
        LineSeries,
        { color: "#e0a21f", lineWidth: 2, priceScaleId: "rsi" },
        2
      );
      s.setData((indicators.rsi_14 || []).map((d) => ({ time: toChartTime(d.time), value: d.value })));
      overlayRefs.current.rsi = s;
      s.priceScale().applyOptions({ scaleMargins: { top: 0.15, bottom: 0.1 } });
    }
    if (activeInd.macd) {
      const s = chart.addSeries(
        LineSeries,
        { color: "#7aa2ff", lineWidth: 2, priceScaleId: "macd" },
        2
      );
      s.setData((indicators.macd || []).map((d) => ({ time: toChartTime(d.time), value: d.value })));
      overlayRefs.current.macd = s;
    }
  }, [indicators, activeInd, showIndicators]);

  // Drawings as price lines
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
  };

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

  const canvasStyle =
    expanded || isFullscreen
      ? undefined
      : {
          height: compact
            ? "min(62vh, 520px)"
            : isMobile
              ? "min(55vh, 420px)"
              : `${chartHeight}px`,
        };

  const showDrawer = drawerOpen && !!groupedOpen;

  const indicatorMenu = (
    <details className="chart-menu">
      <summary>{he.indicators}</summary>
      <div className="chart-menu-body">
        {(
          [
            ["sma_20", "SMA 20"],
            ["sma_50", "SMA 50"],
            ["ema_20", "EMA 20"],
            ["ema_50", "EMA 50"],
            ["bb", "Bollinger"],
            ["vwap", "VWAP"],
            ["rsi", "RSI 14"],
            ["macd", "MACD"],
          ] as const
        ).map(([k, label]) => (
          <label key={k} className="chk">
            <input
              type="checkbox"
              checked={!!activeInd[k]}
              onChange={(e) => setActiveInd((p) => ({ ...p, [k]: e.target.checked }))}
            />
            {label}
          </label>
        ))}
      </div>
    </details>
  );

  const drawMenu = (
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
  );

  const markersMenu = (
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
            <i className="lg-tri sell" /> {he.legendSell}
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
        </div>
      </div>
    </details>
  );

  return (
    <section
      ref={panelRef}
      className={`panel live-chart-panel pro-chart size-${sizeMode}${isFullscreen ? " is-fullscreen" : ""}${
        showDrawer ? " has-drawer" : ""
      }${isNarrow ? " is-narrow" : ""}${compact ? " live-chart-compact" : ""}`}
      data-testid="live-chart-panel"
    >
      <div className="chart-toolbar chart-toolbar-compact">
        <div className="symbol-tabs" role="tablist">
          {symbols.map((sym) => (
            <button
              key={sym}
              type="button"
              className={`symbol-tab ${sym === active ? "active" : ""}`}
              onClick={() => setSymbol(sym)}
            >
              {sym}
            </button>
          ))}
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
          {compact ? (
            <details className="chart-menu chart-menu-more">
              <summary>{he.moreActions}</summary>
              <div className="chart-menu-body chart-menu-more-body">
                {indicatorMenu}
                {drawMenu}
                {markersMenu}
              </div>
            </details>
          ) : (
            <>
              {indicatorMenu}
              {drawMenu}
              {markersMenu}
            </>
          )}
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
          <span className={`ws-badge ${wsState === "live" ? "live" : wsState === "polling" ? "polling" : "dead"}`}>
            {wsState}
          </span>
          <span className={`data-badge ${realData ? "real" : "sim"}`}>
            {realData ? he.realData : he.simData}
          </span>
          <span>
            {provider || meta?.provider || "—"}
          </span>
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
          {!compact && !isMobile && !expanded ? (
            <div
              className="chart-resize-handle"
              title="Drag to resize chart height"
              onMouseDown={onResizeDrag}
            />
          ) : null}
          {hover ? (
            <div className="chart-tooltip mono">
              <div>{new Date(hover.time * 1000).toLocaleString()}</div>
              <div>
                O {fmtPrice(hover.open)} H {fmtPrice(hover.high)} L {fmtPrice(hover.low)} C{" "}
                {fmtPrice(hover.close)}
              </div>
              <div>
                Vol {hover.volume.toLocaleString()} · {hover.changePct >= 0 ? "+" : ""}
                {hover.changePct.toFixed(2)}%
              </div>
            </div>
          ) : null}
        </div>

        {showDrawer && groupedOpen ? (
          <aside className="chart-drawer" aria-label={he.markerDetails}>
            <div className="chart-drawer-head">
              <h3>{he.markerDetails}</h3>
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
              {lastCandle ? (
                <pre className="detail-pre drawer-ohlc">
                  {`O ${fmtPrice(lastCandle.open)}  H ${fmtPrice(lastCandle.high)}
L ${fmtPrice(lastCandle.low)}  C ${fmtPrice(lastCandle.close)}`}
                </pre>
              ) : null}
              <p className="mono muted">
                {he.candleTime} {new Date(groupedOpen.time * 1000).toLocaleString()} ·{" "}
                {groupedOpen.count}
              </p>
              {groupedOpen.items.map((it) => (
                <div key={it.id} className="agent-detail marker-detail-card">
                  <div className="row">
                    <strong>{it.kind.replace(/_/g, " ")}</strong>
                    <span className={`tag ${it.action}`}>{it.action}</span>
                  </div>
                  <dl className="marker-dl mono">
                    <div>
                      <dt>{he.symbol}</dt>
                      <dd>{it.symbol}</dd>
                    </div>
                    <div>
                      <dt>{he.executionTime}</dt>
                      <dd>{new Date(toUnixSeconds(it.timestamp) * 1000).toLocaleString()}</dd>
                    </div>
                    <div>
                      <dt>{he.candleTime}</dt>
                      <dd>{new Date(it.candleTs * 1000).toLocaleString()}</dd>
                    </div>
                    <div>
                      <dt>{he.action}</dt>
                      <dd>{it.action}</dd>
                    </div>
                    <div>
                      <dt>{he.quantity}</dt>
                      <dd>{it.quantity != null ? Number(it.quantity).toFixed(4) : "—"}</dd>
                    </div>
                    <div>
                      <dt>{he.requestedPrice}</dt>
                      <dd>
                        {it.payload?.requested_price != null
                          ? fmtPrice(Number(it.payload.requested_price))
                          : it.price != null
                            ? fmtPrice(it.price)
                            : "—"}
                      </dd>
                    </div>
                    <div>
                      <dt>{he.fillPrice}</dt>
                      <dd>{it.price != null ? fmtPrice(it.price) : "—"}</dd>
                    </div>
                    <div>
                      <dt>{he.totalValue}</dt>
                      <dd>{it.totalValue != null ? fmtPrice(it.totalValue) : "—"}</dd>
                    </div>
                    <div>
                      <dt>{he.confidence}</dt>
                      <dd>
                        {it.confidence != null ? `${(it.confidence * 100).toFixed(0)}%` : "—"}
                      </dd>
                    </div>
                    <div>
                      <dt>{he.agentsVoted}</dt>
                      <dd>
                        {(it.agents || [])
                          .map((a) => String(a.agent_name || a.agent_id || ""))
                          .filter(Boolean)
                          .join(", ") ||
                          it.agentName ||
                          "—"}
                      </dd>
                    </div>
                    <div>
                      <dt>{he.orchestrator}</dt>
                      <dd>{it.finalDecision || "—"}</dd>
                    </div>
                    <div>
                      <dt>{he.reasons}</dt>
                      <dd>{it.reason || "—"}</dd>
                    </div>
                    <div>
                      <dt>{he.paperOrderId}</dt>
                      <dd>{it.orderId || "—"}</dd>
                    </div>
                    <div>
                      <dt>{he.fillId}</dt>
                      <dd>{it.fillId || "—"}</dd>
                    </div>
                    <div>
                      <dt>{he.status}</dt>
                      <dd>{it.status || (it.filled ? "FILLED" : "—")}</dd>
                    </div>
                    {it.skipReason ? (
                      <div>
                        <dt>{he.skipReason}</dt>
                        <dd>{it.skipReason}</dd>
                      </div>
                    ) : null}
                  </dl>
                </div>
              ))}
              {annotations.length ? (
                <>
                  <h3>{he.drawings}</h3>
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
