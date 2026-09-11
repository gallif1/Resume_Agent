/**
 * Professional candlestick chart (TradingView Lightweight Charts).
 * Incremental candle updates — does not recreate chart on every WS tick.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
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
  updateAnnotation,
  type ChartAnnotation,
  type OhlcCandle,
} from "./api";
import { groupMarkersOnCandle, type DecisionMarkerItem, type GroupedMarker } from "./chartMarkers";

const DEFAULT_TFS = ["1m", "5m", "15m", "1h", "4h", "1d"];

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
}: Props) {
  const [symbol, setSymbol] = useState(symbols[0] || "BTC-USD");
  const active = symbols.includes(symbol) ? symbol : symbols[0] || symbol;
  const tfs = timeframes.length ? timeframes : DEFAULT_TFS;
  const meta = marketMeta?.symbols?.[active];

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const overlayRefs = useRef<Record<string, ISeriesApi<"Line">>>({});
  const priceLinesRef = useRef<Map<string, IPriceLine>>(new Map());
  const followLiveRef = useRef(true);
  const loadingOlderRef = useRef(false);
  const candlesRef = useRef<OhlcCandle[]>([]);

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
  const [showDrawings, setShowDrawings] = useState(true);
  const [showIndicators, setShowIndicators] = useState(true);
  const [activeInd, setActiveInd] = useState<Partial<Record<IndKey, boolean>>>({
    sma_20: true,
    sma_50: false,
    ema_20: false,
  });
  const [drawMode, setDrawMode] = useState<"none" | "SUPPORT" | "RESISTANCE" | "TREND_LINE" | "TEXT_NOTE">("none");
  const [annotations, setAnnotations] = useState<ChartAnnotation[]>([]);
  const [selectedAnn, setSelectedAnn] = useState<string | null>(null);
  const [groupedOpen, setGroupedOpen] = useState<GroupedMarker | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const trendDraftRef = useRef<{ time: number; price: number } | null>(null);

  const lastCandle = candlesRef.current[candlesRef.current.length - 1];

  const markerItems: DecisionMarkerItem[] = useMemo(() => {
    const items: DecisionMarkerItem[] = [];
    if (showVotes) {
      for (const v of votes) {
        if (v.symbol !== active) continue;
        if (v.side !== "BUY" && v.side !== "SELL") continue;
        items.push({
          id: `vote-${v.agent_id}-${v.ts}`,
          kind: v.side === "BUY" ? "vote_buy" : "vote_sell",
          agentName: v.agent_name,
          action: v.side,
          confidence: v.confidence,
          reason: v.rationale,
          timestamp: v.ts,
          filled: false,
          price: undefined,
        });
      }
    }
    if (showFills) {
      for (const t of trades) {
        if (t.symbol !== active) continue;
        items.push({
          id: `fill-${t.id}`,
          kind: t.side === "BUY" ? "fill_buy" : "fill_sell",
          action: t.side,
          confidence: t.confidence,
          timestamp: t.ts,
          filled: true,
          finalDecision: t.side,
          price: t.price,
          agentName: (t.agents || []).map((a) => a.name || a.id).filter(Boolean).join(", "),
        });
      }
      for (const d of decisions) {
        if (d.symbol !== active) continue;
        if (d.executed) continue;
        if (d.side === "HOLD") continue;
        items.push({
          id: `rej-${d.id}`,
          kind: "rejected",
          action: d.side,
          confidence: d.confidence,
          reason: d.rationale,
          timestamp: d.ts,
          filled: false,
          finalDecision: d.side,
          skipReason: d.rationale,
        });
      }
    }
    return items;
  }, [votes, trades, decisions, active, showVotes, showFills]);

  const grouped = useMemo(
    () => groupMarkersOnCandle(markerItems, timeframe),
    [markerItems, timeframe]
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

  const loadCandles = useCallback(
    async (sym: string, tf: string, before?: number) => {
      if (before) {
        if (loadingOlderRef.current) return;
        loadingOlderRef.current = true;
        setLoadingOlder(true);
      } else {
        setLoading(true);
      }
      try {
        const res = await fetchCandles(sym, tf, 500, before);
        setProvider(res.provider || res.source || "");
        setUnavailable(!!res.unavailable);
        setHasMore(!!res.has_more);
        setIndicators(res.indicators || {});
        const rows = (res.candles || []) as OhlcCandle[];
        if (before) {
          const merged = [...rows, ...candlesRef.current];
          const byTs = new Map<number, OhlcCandle>();
          for (const c of merged) byTs.set(c.ts, c);
          const sorted = [...byTs.values()].sort((a, b) => a.ts - b.ts);
          applyCandleData(sorted);
        } else {
          applyCandleData(rows);
          if (followLiveRef.current && chartRef.current) {
            chartRef.current.timeScale().scrollToRealTime();
          }
        }
      } catch {
        if (!before) {
          setUnavailable(true);
          applyCandleData([]);
        }
      } finally {
        setLoading(false);
        setLoadingOlder(false);
        loadingOlderRef.current = false;
      }
    },
    [applyCandleData]
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
    chart.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.75, bottom: 0 },
    });
    chart.priceScale("right").applyOptions({
      scaleMargins: { top: 0.05, bottom: 0.25 },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

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
      // User left the live edge?
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
          label: drawMode === "SUPPORT" ? "Support" : "Resistance",
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
            label: "Trend",
            color: "#7aa2ff",
          }).then((r) => {
            if (r.annotation) setAnnotations((prev) => [...prev, r.annotation!]);
            trendDraftRef.current = null;
            setDrawMode("none");
          });
        }
      } else if (drawMode === "TEXT_NOTE") {
        const note = window.prompt("Note text");
        if (!note) return;
        void createAnnotation({
          symbol: active,
          annotation_type: "TEXT_NOTE",
          timeframe_scope: timeframe,
          price: Number(price),
          coordinates: { time, price: Number(price) },
          note,
          label: "Note",
          color: "#f0b429",
        }).then((r) => {
          if (r.annotation) setAnnotations((prev) => [...prev, r.annotation!]);
          setDrawMode("none");
        });
      } else {
        // Click grouped marker near this time
        const hit = grouped.find((g) => Math.abs(g.time - time) < 1);
        if (hit) setGroupedOpen(hit);
      }
    };
    chart.subscribeClick(onClick);

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      overlayRefs.current = {};
      priceLinesRef.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reload on symbol/timeframe
  useEffect(() => {
    followLiveRef.current = true;
    void loadCandles(active, timeframe);
    void loadAnnotations(active);
  }, [active, timeframe, loadCandles, loadAnnotations]);

  // Incremental candle updates
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
    // Clear old overlays
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
      chart.priceScale("rsi").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
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

  const onClearDrawings = async () => {
    if (!window.confirm("Delete all drawings for this symbol?")) return;
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
    const label = window.prompt("Label", a.label || "") ?? a.label;
    const note = window.prompt("Note", a.note || "") ?? a.note;
    const importance = window.prompt("Importance (low|medium|high)", a.importance || "medium");
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

  return (
    <section className="panel live-chart-panel pro-chart">
      <div className="chart-toolbar">
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
          <details className="chart-menu">
            <summary>Indicators</summary>
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
          <details className="chart-menu">
            <summary>Draw {drawMode !== "none" ? `(${drawMode})` : ""}</summary>
            <div className="chart-menu-body">
              <button type="button" onClick={() => setDrawMode("SUPPORT")}>
                Support
              </button>
              <button type="button" onClick={() => setDrawMode("RESISTANCE")}>
                Resistance
              </button>
              <button type="button" onClick={() => setDrawMode("TREND_LINE")}>
                Trend line
              </button>
              <button type="button" onClick={() => setDrawMode("TEXT_NOTE")}>
                Text note
              </button>
              <button type="button" onClick={() => setDrawMode("none")}>
                Cancel draw
              </button>
              <button type="button" onClick={onDeleteSelected} disabled={!selectedAnn}>
                Delete selected
              </button>
              <button type="button" onClick={onClearDrawings}>
                Clear drawings
              </button>
              {selectedAnn ? (
                <button type="button" onClick={editSelected}>
                  Edit selected
                </button>
              ) : null}
            </div>
          </details>
          <button type="button" className="btn-copy ghost" onClick={resetView}>
            Reset view
          </button>
          <button type="button" className="btn-copy ghost" onClick={goLive}>
            Go to live
          </button>
          <button type="button" className="btn-copy ghost" onClick={() => setPanelOpen((v) => !v)}>
            {panelOpen ? "Hide panel" : "Panel"}
          </button>
        </div>
        <div className="chart-status mono muted">
          <span className={`ws-badge ${wsState === "live" ? "live" : wsState === "polling" ? "polling" : "dead"}`}>
            {wsState}
          </span>
          <span className={`data-badge ${realData ? "real" : "sim"}`}>
            {realData ? "REAL" : "SIM"}
          </span>
          <span>
            {provider || meta?.provider || "—"} · {(meta?.freshness || "—").toUpperCase()}
          </span>
          {loading || loadingOlder ? <span className="loading-pill">Loading…</span> : null}
        </div>
      </div>

      <div className="chart-toggles">
        <label className="chk">
          <input type="checkbox" checked={showVotes} onChange={(e) => setShowVotes(e.target.checked)} />
          Agent votes
        </label>
        <label className="chk">
          <input type="checkbox" checked={showFills} onChange={(e) => setShowFills(e.target.checked)} />
          Paper fills
        </label>
        <label className="chk">
          <input
            type="checkbox"
            checked={showDrawings}
            onChange={(e) => setShowDrawings(e.target.checked)}
          />
          Drawings
        </label>
        <label className="chk">
          <input
            type="checkbox"
            checked={showIndicators}
            onChange={(e) => setShowIndicators(e.target.checked)}
          />
          Indicators
        </label>
      </div>

      <div className={`chart-body ${panelOpen ? "with-panel" : ""}`}>
        <div className="chart-main">
          {unavailable && !candlesRef.current.length ? (
            <div className="chart-unavailable">Data unavailable</div>
          ) : null}
          <div className="chart-canvas" ref={wrapRef} />
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
          {grouped.length > 0 ? (
            <div className="marker-legend muted">
              {grouped.slice(-8).map((g) => (
                <button
                  key={g.time}
                  type="button"
                  className="marker-chip"
                  onClick={() => setGroupedOpen(g)}
                >
                  {g.count > 1 ? `${g.count}×` : ""} {g.primary.replace("_", " ")}
                </button>
              ))}
            </div>
          ) : null}
        </div>

        {panelOpen ? (
          <aside className="chart-side-panel">
            <h3>OHLC</h3>
            {lastCandle ? (
              <pre className="detail-pre">
                {`O ${fmtPrice(lastCandle.open)}
H ${fmtPrice(lastCandle.high)}
L ${fmtPrice(lastCandle.low)}
C ${fmtPrice(lastCandle.close)}
Vol ${lastCandle.volume.toLocaleString()}`}
              </pre>
            ) : (
              <p className="muted">No candle</p>
            )}
            <h3>Drawings</h3>
            <ul className="ann-list">
              {annotations.map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    className={selectedAnn === a.id ? "active" : ""}
                    onClick={() => setSelectedAnn(a.id)}
                  >
                    {a.annotation_type} {a.price != null ? fmtPrice(a.price) : ""} {a.label || ""}
                  </button>
                </li>
              ))}
              {!annotations.length && <li className="muted">None</li>}
            </ul>
            <h3>Decisions</h3>
            {groupedOpen ? (
              <div className="grouped-detail">
                {groupedOpen.items.map((it) => (
                  <div key={it.id} className="agent-detail">
                    <div className="row">
                      <strong>{it.agentName || it.kind}</strong>
                      <span className={`tag ${it.action}`}>{it.action}</span>
                    </div>
                    <div className="mono muted">
                      {it.confidence != null ? `${(it.confidence * 100).toFixed(0)}%` : ""} ·{" "}
                      {it.filled ? "FILLED" : it.skipReason ? "SKIPPED" : "vote"}
                    </div>
                    <p>{it.reason || it.skipReason || "—"}</p>
                  </div>
                ))}
                <button type="button" className="btn-copy ghost" onClick={() => setGroupedOpen(null)}>
                  Close
                </button>
              </div>
            ) : (
              <p className="muted">Click a marker chip for details</p>
            )}
            <h3>Freshness</h3>
            <p className="mono muted">
              {(meta?.freshness || "—").toUpperCase()}
              {meta?.stale_reason ? ` · ${meta.stale_reason}` : ""}
            </p>
          </aside>
        ) : null}
      </div>
    </section>
  );
}
