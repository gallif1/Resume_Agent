import { useCallback, useEffect, useRef, useState } from "react";
import {
  clearDecisionLogs,
  fetchConfig,
  fetchSnapshot,
  pauseSystem,
  resetPaperSystem,
  setChartTimeframe,
  startSystem,
  stopSystem,
  tradingWsUrls,
  type AgentVote,
  type AIStatus,
  type Decision,
  type DecisionLog,
  type MarketEvent,
  type MarketMeta,
  type PricePoint,
  type Snapshot,
  type Tick,
  type TradeMarker,
  type TradingConfig,
  type PerformanceStats,
} from "./api";
import { formatDecisionLogsText } from "./decisionLogFormat";
import { copyTextToClipboard } from "./clipboard";
import { he, stateLabel, sessionLabel } from "./i18n/he";
import LiveChart from "./LiveChart";

type LatestVotes = Record<string, AgentVote>;

const HISTORY_CAP = 360;
const VOTE_CAP = 120;
const TRADE_CAP = 80;
const DEFAULT_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

function formatMoney(n: number) {
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

function mergeHistory(
  prev: Record<string, PricePoint[]>,
  incoming: Record<string, PricePoint[]> | undefined
): Record<string, PricePoint[]> {
  if (!incoming) return prev;
  const next: Record<string, PricePoint[]> = { ...prev };
  for (const [sym, points] of Object.entries(incoming)) {
    if (!points?.length) continue;
    next[sym] = points.slice(-HISTORY_CAP);
  }
  return next;
}

function appendPoints(
  prev: Record<string, PricePoint[]>,
  points: Array<PricePoint & { symbol: string }>
): Record<string, PricePoint[]> {
  if (!points.length) return prev;
  const next: Record<string, PricePoint[]> = { ...prev };
  for (const p of points) {
    const series = next[p.symbol] ? [...next[p.symbol]] : [];
    const last = series[series.length - 1];
    if (last && last.ts === p.ts && last.price === p.price) continue;
    series.push({
      ts: p.ts,
      price: p.price,
      volume: p.volume,
      change_pct: p.change_pct,
    });
    next[p.symbol] = series.slice(-HISTORY_CAP);
  }
  return next;
}

function mergeTrades(prev: TradeMarker[], incoming: TradeMarker[] | undefined): TradeMarker[] {
  if (!incoming?.length) return prev;
  const seen = new Set(prev.map((t) => t.id));
  const added = incoming.filter((t) => t.id && !seen.has(t.id));
  if (!added.length) return prev;
  return [...added, ...prev].slice(0, TRADE_CAP);
}

function votesFromDecisions(decisions: Decision[]): AgentVote[] {
  const out: AgentVote[] = [];
  for (const d of decisions) {
    for (const v of d.votes || []) {
      if (v.side === "BUY" || v.side === "SELL") out.push(v);
    }
  }
  return out.slice(0, VOTE_CAP);
}

function sessionBadge(t: Tick): { label: string; cls: string } {
  const freshness = (t.freshness || "").toLowerCase();
  const session = (t.session || "").toLowerCase();
  let cls = "unavailable";
  if (freshness === "unavailable") cls = "unavailable";
  else if (freshness === "stale") cls = "stale";
  else if (session === "closed") cls = "closed";
  else if (session === "open" || freshness === "live") cls = "open";
  return { label: sessionLabel(cls, he.unavailable), cls };
}

function pickAiSymbol(
  ai: AIStatus | null,
  symbols: string[]
): { symbol: string; entry: NonNullable<AIStatus["last_by_symbol"]>[string] } | null {
  const by = ai?.last_by_symbol || {};
  const preferred = [...symbols, "BTC-USD"];
  for (const sym of preferred) {
    if (by[sym]) return { symbol: sym, entry: by[sym] };
  }
  const first = Object.entries(by)[0];
  if (first) return { symbol: first[0], entry: first[1] };
  return null;
}

function accLabel(v?: number | null): string {
  if (v == null || Number.isNaN(v)) return "N/A";
  return `${v.toFixed(0)}%`;
}

function outcomeLine(
  key: string,
  h?: {
    status?: string;
    price?: number | null;
    return_pct?: number | null;
    direction_correct?: boolean | null;
  }
): string {
  if (!h || h.status === "pending" || !h.status) return `${key}: PENDING`;
  if (h.status === "unavailable" || h.status === "no_history") {
    return `${key}: ${h.status.toUpperCase()}`;
  }
  const dir =
    h.direction_correct === true ? "correct" : h.direction_correct === false ? "incorrect" : "—";
  const ret =
    h.return_pct == null
      ? "—"
      : `${h.return_pct >= 0 ? "+" : ""}${h.return_pct.toFixed(2)}%`;
  return `${key}: price ${h.price ?? "—"} · return ${ret} · ${dir}`;
}

export default function App() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<MarketEvent[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [decisionLogs, setDecisionLogs] = useState<DecisionLog[]>([]);
  const [expandedLogId, setExpandedLogId] = useState<string | null>(null);
  const [copyMsg, setCopyMsg] = useState<string | null>(null);
  const [manualCopyText, setManualCopyText] = useState<string | null>(null);
  const [votes, setVotes] = useState<LatestVotes>({});
  const [chartVotes, setChartVotes] = useState<AgentVote[]>([]);
  const [, setHistory] = useState<Record<string, PricePoint[]>>({});
  const [trades, setTrades] = useState<TradeMarker[]>([]);
  const [chartTimeframe, setChartTimeframeState] = useState("5m");
  const [timeframes, setTimeframes] = useState<string[]>(DEFAULT_TIMEFRAMES);
  const [marketMeta, setMarketMeta] = useState<MarketMeta | null>(null);
  const [aiStatus, setAiStatus] = useState<AIStatus | null>(null);
  const [performance, setPerformance] = useState<PerformanceStats | null>(null);
  const [candleUpdates, setCandleUpdates] = useState<
    Array<{ symbol: string; timeframe: string; candle: import("./api").OhlcCandle; provider?: string }>
  >([]);
  const [dataMode, setDataMode] = useState<string>("simulated");
  const [wsState, setWsState] = useState<"connecting" | "live" | "dead" | "polling">(
    "connecting"
  );
  const [controlBusy, setControlBusy] = useState(false);
  const [chartBusy, setChartBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [homeUrl, setHomeUrl] = useState("/");
  const [flash, setFlash] = useState(false);
  const [config, setConfig] = useState<TradingConfig | null>(null);
  const [chartExpanded, setChartExpanded] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  const onChartLayout = useCallback((mode: "normal" | "expanded") => {
    setChartExpanded(mode === "expanded");
  }, []);

  // Body scroll lock is owned by LiveChart when expanded (fixed overlay).
  useEffect(() => {
    return () => document.body.classList.remove("chart-expanded-body");
  }, []);

  const applySnapshot = (s: Snapshot) => {
    setSnap(s);
    setEvents(s.events || []);
    setDecisions(s.decisions || []);
    setDecisionLogs(s.decision_logs || []);
    setHistory(mergeHistory({}, s.price_history));
    setTrades((s.trades || []).slice(0, TRADE_CAP));
    setChartVotes(votesFromDecisions(s.decisions || []));
    if (s.chart_timeframe) setChartTimeframeState(s.chart_timeframe);
    if (s.market_meta) {
      setMarketMeta(s.market_meta);
      if (s.market_meta.timeframes?.length) setTimeframes(s.market_meta.timeframes);
    }
    if (s.ai) setAiStatus(s.ai);
    if (s.performance) setPerformance(s.performance);
    if (s.data_mode) setDataMode(s.data_mode);
  };

  useEffect(() => {
    fetchConfig()
      .then((c) => {
        setConfig(c);
        setHomeUrl(c.resume_agent_home || "/");
        if (c.chart_timeframes?.length) setTimeframes(c.chart_timeframes);
        if (c.default_chart_timeframe) setChartTimeframeState(c.default_chart_timeframe);
        if (c.data_mode) setDataMode(c.data_mode);
        if (c.market_meta) setMarketMeta(c.market_meta);
        if (c.ai) setAiStatus(c.ai);
      })
      .catch(() => setHomeUrl("/"));
    fetchSnapshot()
      .then(applySnapshot)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    let closed = false;
    let retry: number | undefined;
    let attempt = 0;
    let urlIndex = 0;

    const connect = () => {
      if (closed) return;
      const urls = tradingWsUrls(config);
      if (!urls.length) return;
      if (urlIndex >= urls.length) {
        urlIndex = 0;
        attempt += 1;
        setWsState("dead");
        retry = window.setTimeout(connect, Math.min(8000, 500 * Math.max(1, attempt)));
        return;
      }

      setWsState("connecting");
      let opened = false;
      const ws = new WebSocket(urls[urlIndex]);
      wsRef.current = ws;

      ws.onopen = () => {
        opened = true;
        attempt = 0;
        urlIndex = 0;
        setWsState("live");
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string) as {
            type: string;
            payload: unknown;
          };
          if (msg.type === "hello" || msg.type === "state") {
            applySnapshot(msg.payload as Snapshot);
          } else if (msg.type === "tick") {
            const p = msg.payload as {
              tick_count: number;
              state: Snapshot["state"];
              market: Tick[];
              price_points?: Array<PricePoint & { symbol: string }>;
              price_history?: Record<string, PricePoint[]>;
              chart_timeframe?: string;
              events: MarketEvent[];
              votes: AgentVote[];
              decisions: Decision[];
              decision_logs?: DecisionLog[];
              trades?: TradeMarker[];
              portfolio: Snapshot["portfolio"];
              market_meta?: MarketMeta;
              ai?: AIStatus;
              performance?: PerformanceStats;
              data_mode?: string;
              candle_updates?: Array<{
                symbol: string;
                timeframe: string;
                candle: import("./api").OhlcCandle;
                provider?: string;
              }>;
            };
            setSnap((prev) =>
              prev
                ? {
                    ...prev,
                    tick_count: p.tick_count,
                    state: p.state,
                    market: p.market,
                    portfolio: p.portfolio,
                    chart_timeframe: p.chart_timeframe ?? prev.chart_timeframe,
                    market_meta: p.market_meta ?? prev.market_meta,
                    ai: p.ai ?? prev.ai,
                    data_mode: p.data_mode ?? prev.data_mode,
                  }
                : prev
            );
            if (p.price_history) {
              setHistory(mergeHistory({}, p.price_history));
            } else {
              const points =
                p.price_points?.length
                  ? p.price_points
                  : (p.market || []).map((t) => ({
                      symbol: t.symbol,
                      ts: t.ts,
                      price: t.price,
                      volume: t.volume,
                      change_pct: t.change_pct,
                    }));
              setHistory((prev) => appendPoints(prev, points));
            }
            if (p.chart_timeframe) setChartTimeframeState(p.chart_timeframe);
            if (p.market_meta) {
              setMarketMeta(p.market_meta);
              if (p.market_meta.timeframes?.length) setTimeframes(p.market_meta.timeframes);
            }
            if (p.ai) setAiStatus(p.ai);
            if (p.performance) setPerformance(p.performance);
            if (p.data_mode) setDataMode(p.data_mode);
            if (p.candle_updates?.length) {
              setCandleUpdates(p.candle_updates);
            }
            if (p.events?.length) setEvents((prev) => [...p.events, ...prev].slice(0, 40));
            if (p.decisions?.length) {
              setDecisions((prev) => [...p.decisions, ...prev].slice(0, 40));
            }
            if (p.decision_logs) {
              setDecisionLogs(p.decision_logs);
            }
            setTrades((prev) =>
              mergeTrades(
                prev,
                p.trades?.length
                  ? p.trades
                  : (p.decisions || [])
                      .filter((d) => d.executed && (d.side === "BUY" || d.side === "SELL"))
                      .map((d) => ({
                        id: d.id,
                        symbol: d.symbol,
                        side: d.side as "BUY" | "SELL",
                        price: d.fill_price,
                        quantity: d.quantity,
                        ts: d.ts,
                        confidence: d.confidence,
                        agents: (d.votes || [])
                          .filter((v) => v.side === "BUY" || v.side === "SELL")
                          .map((v) => ({
                            id: v.agent_id,
                            name: v.agent_name,
                            side: v.side,
                            confidence: v.confidence,
                          })),
                      }))
              )
            );
            if (p.votes?.length) {
              const byAgent: Record<string, AgentVote[]> = {};
              for (const v of p.votes) {
                (byAgent[v.agent_id] ||= []).push(v);
              }
              const next: LatestVotes = {};
              for (const [id, list] of Object.entries(byAgent)) {
                // Prefer last actionable vote in the tick so HOLD on NVDA does not
                // hide a BUY/SELL that just moved cash on SOL/AAPL.
                const actionableSym = list.filter((v) => v.side === "BUY" || v.side === "SELL");
                next[id] = actionableSym.length
                  ? actionableSym[actionableSym.length - 1]
                  : list[list.length - 1];
              }
              setVotes(next);
              const actionable = p.votes.filter((v) => v.side === "BUY" || v.side === "SELL");
              if (actionable.length) {
                setChartVotes((prev) => [...actionable, ...prev].slice(0, VOTE_CAP));
                setFlash(true);
                window.setTimeout(() => setFlash(false), 450);
              }
            }
          } else if (msg.type === "error") {
            const payload = msg.payload as { message?: string };
            setError(payload.message || "Runtime error");
          }
        } catch {
          /* ignore */
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (closed) return;
        if (!opened) {
          urlIndex += 1;
          retry = window.setTimeout(connect, 150);
          return;
        }
        setWsState("dead");
        attempt += 1;
        urlIndex = 0;
        retry = window.setTimeout(connect, Math.min(8000, 500 * attempt));
      };

      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      closed = true;
      if (retry) window.clearTimeout(retry);
      wsRef.current?.close();
    };
  }, [config]);

  useEffect(() => {
    if (wsState === "live") return;
    const id = window.setInterval(() => {
      fetchSnapshot()
        .then((s) => {
          applySnapshot(s);
          setWsState((prev) => (prev === "live" ? prev : "polling"));
        })
        .catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(id);
  }, [wsState]);

  const run = async (action: "start" | "pause" | "stop") => {
    if (action === "start" && snap?.state === "running") {
      setError(he.alreadyRunning);
      return;
    }
    if (action === "pause" && snap?.state !== "running") {
      setError(he.notRunning);
      return;
    }
    if (action === "stop" && snap?.state === "stopped") {
      setError(he.alreadyStopped);
      return;
    }
    setControlBusy(true);
    setError(null);
    try {
      const s =
        action === "start"
          ? await startSystem()
          : action === "pause"
            ? await pauseSystem()
            : await stopSystem();
      applySnapshot(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setControlBusy(false);
    }
  };

  const onClearLogs = async () => {
    if (!decisionLogs.length) {
      setCopyMsg(he.copyNoLogs);
      window.setTimeout(() => setCopyMsg(null), 2500);
      return;
    }
    if (!window.confirm(he.clearLogsConfirm)) {
      return;
    }
    setControlBusy(true);
    setError(null);
    try {
      const s = await clearDecisionLogs();
      applySnapshot(s);
      setExpandedLogId(null);
      setManualCopyText(null);
      setCopyMsg(he.copiedN(s.cleared_logs ?? 0));
      window.setTimeout(() => setCopyMsg(null), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setControlBusy(false);
    }
  };

  const onResetPaper = async () => {
    if (!window.confirm(he.resetConfirm)) {
      return;
    }
    setControlBusy(true);
    setError(null);
    try {
      const s = await resetPaperSystem();
      applySnapshot(s);
      setVotes({});
      setChartVotes([]);
      setTrades([]);
      setEvents([]);
      setDecisions([]);
      setDecisionLogs([]);
      setExpandedLogId(null);
      setManualCopyText(null);
      setPerformance(s.performance ?? null);
      setCopyMsg(`${he.resetPaper} — ${he.start}`);
      window.setTimeout(() => setCopyMsg(null), 3500);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setControlBusy(false);
    }
  };

  const onTimeframe = async (tf: string) => {
    if (tf === chartTimeframe) return;
    setChartBusy(true);
    setError(null);
    // Optimistic UI — backend warms candles in the background.
    setChartTimeframeState(tf);
    try {
      const s = await setChartTimeframe(tf);
      applySnapshot(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setChartBusy(false);
    }
  };

  const state = snap?.state ?? "stopped";
  const market = snap?.market ?? [];
  const agents = (snap?.agents ?? []).filter((a) => a.id !== "ai_analyst");
  const portfolio = snap?.portfolio;
  const symbols = snap?.symbols?.length
    ? snap.symbols
    : market.map((t) => t.symbol);
  const realData = dataMode !== "simulated";
  const aiPick = pickAiSymbol(aiStatus, symbols);
  const aiCalls = aiStatus?.calls_this_hour ?? 0;
  const aiMax = aiStatus?.max_calls_per_hour ?? 0;
  const filledDecisions = decisions.filter((d) => d.executed);
  const copyLogs = async (limit: number | "all") => {
    const n = limit === "all" ? decisionLogs.length : limit;
    if (!decisionLogs.length) {
      setCopyMsg(he.copyNoLogs);
      window.setTimeout(() => setCopyMsg(null), 2500);
      return;
    }
    const text = formatDecisionLogsText(decisionLogs, n);
    const count = Math.min(n, decisionLogs.length);
    try {
      const result = await copyTextToClipboard(text);
      if (result === "ok") {
        setManualCopyText(null);
        setCopyMsg(he.copiedN(count));
      } else {
        setManualCopyText(text);
        setCopyMsg(he.clipboardBlocked);
      }
    } catch {
      setManualCopyText(text);
      setCopyMsg(he.copyFailed);
    }
    window.setTimeout(() => setCopyMsg(null), 4000);
  };

  const execBadge = (log: DecisionLog) => {
    const st = log.execution?.status;
    if (st === "FILLED") return he.filled;
    if (log.kind === "SIGNAL_STILL_ACTIVE") return he.signalActive;
    if (String(log.execution?.reason || "").toLowerCase().includes("cooldown")) return he.cooldown;
    if (st === "NOT_FILLED" && log.decision?.action !== "HOLD") return he.notFilled;
    return log.kind.replace(/_/g, " ");
  };
  const wsLabel =
    wsState === "live"
      ? he.wsLive
      : wsState === "polling"
        ? he.wsPolling
        : wsState === "connecting"
          ? he.wsConnecting
          : he.wsDead;

  return (
    <div className={`app${chartExpanded ? " chart-layout-expanded" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-kicker">{he.brandKicker}</span>
          <h1>{he.brandTitle}</h1>
        </div>
        <a className="back-link" href={homeUrl}>
          {he.backLink}
        </a>
      </header>

      <section className="hero-controls">
        <span className={`status-pill ${state}`}>
          <span className="status-dot" />
          {stateLabel(state)}
        </span>
        <button
          type="button"
          className="btn btn-start"
          disabled={controlBusy || state === "running"}
          onClick={() => run("start")}
        >
          {he.start}
        </button>
        <button
          type="button"
          className="btn btn-pause"
          disabled={controlBusy || state !== "running"}
          onClick={() => run("pause")}
        >
          {he.pause}
        </button>
        <button
          type="button"
          className="btn btn-stop"
          disabled={controlBusy || state === "stopped"}
          onClick={() => run("stop")}
        >
          {he.stop}
        </button>
        <button
          type="button"
          className="btn btn-clear-logs"
          disabled={controlBusy || !decisionLogs.length}
          onClick={() => onClearLogs()}
          title={he.clearLogs}
        >
          {he.clearLogs}
        </button>
        <button
          type="button"
          className="btn btn-reset"
          disabled={controlBusy}
          onClick={() => onResetPaper()}
          title={he.resetPaper}
        >
          {he.resetPaper}
        </button>
        <span className={`data-badge ${realData ? "real" : "sim"}`}>
          {realData ? he.realData : he.simData}
        </span>
        <span
          className={`ws-badge ${
            wsState === "live" ? "live" : wsState === "polling" ? "polling" : "dead"
          }`}
        >
          {wsLabel}
        </span>
        <span className="meta mono">
          {he.ticks} {snap?.tick_count ?? 0}
          {error ? ` · ${error}` : ""}
        </span>
      </section>

      <LiveChart
        symbols={symbols.length ? symbols : ["BTC-USD"]}
        trades={trades}
        votes={chartVotes}
        decisions={decisions}
        timeframe={chartTimeframe}
        timeframes={timeframes}
        onTimeframe={onTimeframe}
        timeframeBusy={chartBusy}
        marketMeta={marketMeta}
        realData={realData}
        candleUpdates={candleUpdates}
        wsState={wsState}
        onLayoutModeChange={onChartLayout}
      />
      <div className="grid">
        <section className="panel">
          <h2>{he.marketFeed}</h2>
          <table className="market-table">
            <thead>
              <tr>
                <th>{he.symbol}</th>
                <th>{he.price}</th>
                <th>{he.change}</th>
                <th>{he.volume}</th>
                <th>{he.session}</th>
                <th>{he.provider}</th>
              </tr>
            </thead>
            <tbody>
              {market.map((t) => {
                const badge = sessionBadge(t);
                return (
                  <tr key={t.symbol}>
                    <td className="mono">{t.symbol}</td>
                    <td className="mono">{t.price.toLocaleString()}</td>
                    <td className={`mono ${t.change_pct >= 0 ? "up" : "down"}`}>
                      {t.change_pct >= 0 ? "+" : ""}
                      {t.change_pct.toFixed(3)}%
                    </td>
                    <td className="mono muted">{Math.round(t.volume).toLocaleString()}</td>
                    <td>
                      <span className={`session-badge ${badge.cls}`}>{badge.label}</span>
                      {t.freshness ? (
                        <div className="muted mono" style={{ fontSize: "0.7rem", marginTop: 2 }}>
                          {t.freshness.toUpperCase()}
                        </div>
                      ) : null}
                    </td>
                    <td className="mono muted">{t.provider || "—"}</td>
                  </tr>
                );
              })}
              {!market.length && (
                <tr>
                  <td colSpan={6} className="muted">
                    {he.waitingMarket}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>

        <section className="panel">
          <h2>{he.portfolio}</h2>
          <div className="portfolio">
            <div className="stat">
              <div className="label">{he.cash}</div>
              <div className="value">{formatMoney(portfolio?.cash ?? 0)}</div>
            </div>
            <div className="stat">
              <div className="label">{he.realizedPnl}</div>
              <div
                className={`value ${(portfolio?.realized_pnl ?? 0) >= 0 ? "up" : "down"}`}
              >
                {formatMoney(portfolio?.realized_pnl ?? 0)}
              </div>
            </div>
            <div className="stat">
              <div className="label">{he.positions}</div>
              <div className="value">{Object.keys(portfolio?.positions || {}).length}</div>
            </div>
          </div>
          <div className="list">
            {Object.values(portfolio?.positions || {}).map((p) => (
              <div className="item" key={p.symbol}>
                <div className="row">
                  <strong className="mono">{p.symbol}</strong>
                  <span className="mono">
                    {p.quantity.toFixed(4)} @ {p.avg_price.toLocaleString()}
                  </span>
                </div>
              </div>
            ))}
            {!Object.keys(portfolio?.positions || {}).length && (
              <div className="muted">{he.noPositions}</div>
            )}
          </div>
        </section>

        <section className="panel" style={{ gridColumn: "1 / -1" }}>
          <h2>{he.agents}</h2>
          <div className="agents">
            {agents.map((a) => {
              const vote = votes[a.id];
              return (
                <div key={a.id} className={`agent-card ${flash && vote ? "flash" : ""}`}>
                  <h3>{a.name}</h3>
                  {vote ? (
                    <>
                      <div className="row" style={{ display: "flex", gap: "0.5rem" }}>
                        <span className={`tag ${vote.side}`}>{vote.side}</span>
                        <span className="mono muted">
                          {(vote.confidence * 100).toFixed(0)}% · {vote.symbol}
                        </span>
                      </div>
                      <p className="muted" style={{ margin: "0.4rem 0 0" }}>
                        {vote.rationale}
                      </p>
                    </>
                  ) : (
                    <p className="muted" style={{ margin: 0 }}>
                      {he.waitingVote}
                    </p>
                  )}
                </div>
              );
            })}
            <div className="ai-card">
              <div className="ai-card-head">
                <h3>{he.aiAnalyst}</h3>
                <span className="ai-source">
                  {aiPick?.entry.source || (aiStatus?.enabled ? he.idle : he.off)}
                </span>
              </div>
              {aiPick ? (
                <>
                  <div className="row" style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                    <span className={`tag ${aiPick.entry.action || "HOLD"}`}>
                      {aiPick.entry.action || "HOLD"}
                    </span>
                    <span className="mono muted">
                      {((aiPick.entry.confidence ?? 0) * 100).toFixed(0)}% · {aiPick.symbol}
                    </span>
                  </div>
                  <p className="muted" style={{ margin: "0.45rem 0 0" }}>
                    {aiPick.entry.reason || he.noRationale}
                  </p>
                </>
              ) : (
                <p className="muted" style={{ margin: 0 }}>
                  {aiStatus?.api_key_configured === false
                    ? he.aiNoKey
                    : he.waitingAi}
                </p>
              )}
              <div className="ai-calls mono">
                {aiMax <= 0
                  ? `${he.aiCallsHour}: ${aiCalls} (${he.aiUnlimited})`
                  : `${he.aiCallsHour}: ${aiCalls} / ${aiMax}`}
              </div>
            </div>
          </div>
        </section>

        <section className="panel">
          <h2>{he.eventEngine}</h2>
          <div className="list">
            {events.map((e) => (
              <div className="item" key={e.id + String(e.ts)}>
                <div className="row">
                  <strong>{e.symbol}</strong>
                  <span className="muted mono">{e.kind}</span>
                </div>
                <div>{e.message}</div>
              </div>
            ))}
            {!events.length && <div className="muted">{he.noEvents}</div>}
          </div>
        </section>

        <section className="panel">
          <div className="panel-head-row">
            <h2>{he.decisionEngine}</h2>
            <div className="copy-logs-bar">
              <button type="button" className="btn-copy" onClick={() => copyLogs(25)}>
                {he.copyRecent}
              </button>
              <button type="button" className="btn-copy ghost" onClick={() => copyLogs(10)}>
                10
              </button>
              <button type="button" className="btn-copy ghost" onClick={() => copyLogs(25)}>
                25
              </button>
              <button type="button" className="btn-copy ghost" onClick={() => copyLogs(50)}>
                50
              </button>
              <button type="button" className="btn-copy ghost" onClick={() => copyLogs("all")}>
                {he.all}
              </button>
              <button
                type="button"
                className="btn-copy ghost danger"
                disabled={controlBusy || !decisionLogs.length}
                onClick={() => onClearLogs()}
              >
                {he.clearLogs}
              </button>
              {copyMsg ? <span className="copy-toast mono">{copyMsg}</span> : null}
            </div>
          </div>
          <div className="perf-block" aria-label={he.systemPerformance}>
            <h3 className="perf-title">{he.systemPerformance}</h3>
            <div className="perf-grid">
              {(
                [
                  [he.decisionEngine, performance?.decision_engine],
                  [he.momentum, performance?.agents?.momentum],
                  [he.meanReversion, performance?.agents?.mean_reversion],
                  [he.volatility, performance?.agents?.volatility],
                  [he.aiAnalyst, performance?.agents?.ai_analyst],
                ] as const
              ).map(([label, row]) => (
                <div className="perf-row" key={label}>
                  <strong>{label}</strong>
                  <span className="mono muted">
                    5m {accLabel(row?.accuracy_5m_pct)} · 15m {accLabel(row?.accuracy_15m_pct)} ·
                    60m {accLabel(row?.accuracy_60m_pct)}
                  </span>
                </div>
              ))}
            </div>
            <p className="muted perf-note">
              {he.directionalAccuracy}
            </p>
          </div>
          {manualCopyText ? (
            <div className="manual-copy-box">
              <div className="row" style={{ marginBottom: "0.35rem" }}>
                <span className="muted">
                  {he.clipboardBlocked}
                </span>
                <button
                  type="button"
                  className="btn-copy ghost"
                  onClick={() => setManualCopyText(null)}
                >
                  {he.closeDetails}
                </button>
              </div>
              <textarea
                className="manual-copy-textarea"
                readOnly
                value={manualCopyText}
                onFocus={(e) => e.currentTarget.select()}
                rows={12}
              />
            </div>
          ) : null}
          {filledDecisions.length > 0 && (
            <div className="fills-strip" aria-label="Recent paper fills">
              {filledDecisions.slice(0, 8).map((d) => (
                <div className="fill-chip" key={`fill-${d.id}`}>
                  <span className={`tag ${d.side}`}>{d.side}</span>
                  <strong className="mono">{d.symbol}</strong>
                  <span className="mono muted">
                    {d.quantity != null ? d.quantity.toFixed(4) : ""} @{" "}
                    {d.fill_price != null ? d.fill_price.toLocaleString() : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
          <div className="list decision-log-list">
            {decisionLogs.map((log) => {
              const open = expandedLogId === log.id;
              const action = log.decision?.action || log.signal?.action || "HOLD";
              const conf = log.decision?.final_confidence ?? 0;
              return (
                <div
                  className={`item decision-log-item ${log.execution?.status === "FILLED" ? "filled" : ""}`}
                  key={log.id}
                >
                  <div className="row">
                    <span>
                      <span className={`tag ${action}`}>{action}</span>{" "}
                      <strong className="mono">{log.symbol}</strong>
                      <span className="muted mono" style={{ marginLeft: 8 }}>
                        {log.kind.replace(/_/g, " ")}
                      </span>
                    </span>
                    <span className="mono muted">
                      {(conf * 100).toFixed(0)}% · {execBadge(log)}
                    </span>
                  </div>
                  <div className="decision-log-summary muted">
                    {log.execution?.reason || log.decision?.explanation || log.decision?.rationale}
                  </div>
                  <button
                    type="button"
                    className="why-btn"
                    aria-expanded={open}
                    onClick={() => setExpandedLogId(open ? null : log.id)}
                  >
                    {open ? he.hideDetails : he.why}
                  </button>
                  {open && (
                    <div className="decision-log-details">
                      <div className="detail-block">
                        <h4>{he.signal}</h4>
                        <p className="mono">
                          {log.signal?.action} · agents{" "}
                          {(log.signal?.agents || [])
                            .map(
                              (a) =>
                                `${a.agent_name?.replace(" Agent", "") || a.agent_id}:${a.action}`
                            )
                            .join(", ")}
                        </p>
                      </div>
                      <div className="detail-block">
                        <h4>{he.marketSnapshot}</h4>
                        <pre className="detail-pre">
                          {`Price: ${log.market?.price ?? "—"}
1m: ${log.market?.change_1m_pct ?? "—"}%
5m: ${log.market?.change_5m_pct ?? "—"}%
15m: ${log.market?.change_15m_pct ?? "—"}%
RSI: ${log.market?.rsi_14 ?? "—"}
SMA fast/slow: ${log.market?.sma_fast ?? "—"} / ${log.market?.sma_slow ?? "—"}
EMA: ${log.market?.ema_fast ?? "—"}
Trend: ${log.market?.trend ?? "—"}
Volume: ${log.market?.volume_state ?? "—"}
Volatility: ${log.market?.volatility ?? "—"}
Events: ${(log.market?.detected_events || []).join(", ") || "—"}
Provider: ${log.market?.provider ?? "—"} (${log.market?.freshness ?? "—"})`}
                        </pre>
                      </div>
                      <div className="detail-block">
                        <h4>{he.agents}</h4>
                        {(log.signal?.agents || []).map((a) => (
                          <div className="agent-detail" key={`${log.id}-${a.agent_id}-${a.ts}`}>
                            <div className="row">
                              <strong>{a.agent_name}</strong>
                              <span className={`tag ${a.action || "HOLD"}`}>{a.action}</span>
                            </div>
                            <div className="mono muted">
                              {((a.confidence ?? 0) * 100).toFixed(0)}%
                              {a.source ? ` · ${a.source}` : ""}
                            </div>
                            <p>{a.reason}</p>
                          </div>
                        ))}
                      </div>
                      <div className="detail-block">
                        <h4>{he.decision}</h4>
                        <pre className="detail-pre">
                          {`Action: ${log.decision?.action}
Final confidence: ${((log.decision?.final_confidence ?? 0) * 100).toFixed(0)}%
Votes: BUY ${log.decision?.vote_counts?.BUY ?? 0} / SELL ${log.decision?.vote_counts?.SELL ?? 0} / HOLD ${log.decision?.vote_counts?.HOLD ?? 0}
Weights: BUY=${log.decision?.weights?.BUY ?? 0} SELL=${log.decision?.weights?.SELL ?? 0} HOLD=${log.decision?.weights?.HOLD ?? 0}
Action score: ${log.decision?.action_score ?? "—"}
Threshold: ${log.decision?.threshold ?? "—"}
Hold gate: ${log.decision?.hold_gate ?? "—"}
Explanation: ${log.decision?.explanation || "—"}

Confidence debug:
  winning_action = ${log.decision?.confidence_debug?.winning_action ?? "—"}
  winning_score = ${log.decision?.confidence_debug?.winning_score ?? log.decision?.confidence_debug?.raw_score ?? "—"}
  total_weight = ${log.decision?.confidence_debug?.total_weight ?? log.decision?.confidence_debug?.total_all_weights ?? "—"}
  action_support = ${log.decision?.confidence_debug?.action_support ?? "—"}
  agreement_factor = ${log.decision?.confidence_debug?.agreement_factor ?? "—"}
  hold_ratio = ${log.decision?.confidence_debug?.hold_ratio ?? "—"}
  opposition_ratio = ${log.decision?.confidence_debug?.opposition_ratio ?? "—"}
  final = ${log.decision?.confidence_debug?.final_confidence ?? "—"}
  formula = ${log.decision?.confidence_debug?.formula ?? "—"}`}
                        </pre>
                      </div>
                      {log.pretrade ? (
                        <div className="detail-block">
                          <h4>{he.aiPretrade}</h4>
                          <pre className="detail-pre">
                            {`Source: ${log.pretrade.source ?? "—"}
Skip: ${log.pretrade.skip_reason ?? "—"}
Action: ${log.pretrade.action ?? "—"}
Confidence: ${log.pretrade.confidence ?? "—"}`}
                          </pre>
                        </div>
                      ) : null}
                      <div className="detail-block">
                        <h4>{he.outcome}</h4>
                        <pre className="detail-pre">
                          {`Entry: ${log.outcome?.entry_price ?? "—"}
${outcomeLine("5m", log.outcome?.horizons?.["5m"])}
${outcomeLine("15m", log.outcome?.horizons?.["15m"])}
${outcomeLine("60m", log.outcome?.horizons?.["60m"])}`}
                        </pre>
                      </div>
                      <div className="detail-block">
                        <h4>{he.execution}</h4>
                        <pre className="detail-pre">
                          {`Status: ${log.execution?.status}
Reason: ${log.execution?.reason}
Cooldown remaining: ${log.execution?.cooldown_remaining_sec ?? "—"}s
Fill: ${log.execution?.quantity ?? "—"} @ ${log.execution?.fill_price ?? "—"}`}
                        </pre>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
            {!decisionLogs.length && (
              <div className="muted">{he.logsAppear}</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
