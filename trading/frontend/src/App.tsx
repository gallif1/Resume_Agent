import { useEffect, useRef, useState } from "react";
import {
  fetchConfig,
  fetchSnapshot,
  pauseSystem,
  startSystem,
  stopSystem,
  tradingWsUrls,
  type AgentVote,
  type Decision,
  type MarketEvent,
  type PricePoint,
  type Snapshot,
  type Tick,
  type TradeMarker,
  type TradingConfig,
} from "./api";
import LiveChart from "./LiveChart";

type LatestVotes = Record<string, AgentVote>;

const HISTORY_CAP = 360;
const VOTE_CAP = 120;
const TRADE_CAP = 80;

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

export default function App() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<MarketEvent[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [votes, setVotes] = useState<LatestVotes>({});
  const [chartVotes, setChartVotes] = useState<AgentVote[]>([]);
  const [history, setHistory] = useState<Record<string, PricePoint[]>>({});
  const [trades, setTrades] = useState<TradeMarker[]>([]);
  const [wsState, setWsState] = useState<"connecting" | "live" | "dead" | "polling">(
    "connecting"
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [homeUrl, setHomeUrl] = useState("/");
  const [flash, setFlash] = useState(false);
  const [config, setConfig] = useState<TradingConfig | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const applySnapshot = (s: Snapshot) => {
    setSnap(s);
    setEvents(s.events || []);
    setDecisions(s.decisions || []);
    setHistory(mergeHistory({}, s.price_history));
    setTrades((s.trades || []).slice(0, TRADE_CAP));
    setChartVotes(votesFromDecisions(s.decisions || []));
  };

  useEffect(() => {
    fetchConfig()
      .then((c) => {
        setConfig(c);
        setHomeUrl(c.resume_agent_home || "/");
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
              events: MarketEvent[];
              votes: AgentVote[];
              decisions: Decision[];
              trades?: TradeMarker[];
              portfolio: Snapshot["portfolio"];
            };
            setSnap((prev) =>
              prev
                ? {
                    ...prev,
                    tick_count: p.tick_count,
                    state: p.state,
                    market: p.market,
                    portfolio: p.portfolio,
                  }
                : prev
            );
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
            if (p.events?.length) setEvents((prev) => [...p.events, ...prev].slice(0, 40));
            if (p.decisions?.length) {
              setDecisions((prev) => [...p.decisions, ...prev].slice(0, 40));
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
              const next: LatestVotes = {};
              for (const v of p.votes) next[v.agent_id] = v;
              setVotes(next);
              const actionable = p.votes.filter((v) => v.side === "BUY" || v.side === "SELL");
              if (actionable.length) {
                setChartVotes((prev) => [...actionable, ...prev].slice(0, VOTE_CAP));
              }
              setFlash(true);
              window.setTimeout(() => setFlash(false), 350);
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
    setBusy(true);
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
      setBusy(false);
    }
  };

  const state = snap?.state ?? "stopped";
  const market = snap?.market ?? [];
  const agents = snap?.agents ?? [];
  const portfolio = snap?.portfolio;
  const symbols = snap?.symbols?.length
    ? snap.symbols
    : market.map((t) => t.symbol);
  const wsLabel =
    wsState === "live"
      ? "WS connected"
      : wsState === "polling"
        ? "WS dead · polling"
        : wsState === "connecting"
          ? "WS connecting"
          : "WS dead";

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-kicker">Live paper trading</span>
          <h1>AI Trading System</h1>
        </div>
        <a className="back-link" href={homeUrl}>
          ← Resume Agent
        </a>
      </header>

      <section className="hero-controls">
        <span className={`status-pill ${state}`}>
          <span className="status-dot" />
          {state.toUpperCase()}
        </span>
        <button
          type="button"
          className="btn btn-start"
          disabled={busy || state === "running"}
          onClick={() => run("start")}
        >
          START
        </button>
        <button
          type="button"
          className="btn btn-pause"
          disabled={busy || state !== "running"}
          onClick={() => run("pause")}
        >
          PAUSE
        </button>
        <button
          type="button"
          className="btn btn-stop"
          disabled={busy || state === "stopped"}
          onClick={() => run("stop")}
        >
          STOP
        </button>
        <span
          className={`ws-badge ${
            wsState === "live" ? "live" : wsState === "polling" ? "polling" : "dead"
          }`}
        >
          {wsLabel}
        </span>
        <span className="meta mono">
          ticks {snap?.tick_count ?? 0}
          {error ? ` · ${error}` : ""}
        </span>
      </section>

      <LiveChart
        symbols={symbols.length ? symbols : ["BTC-USD"]}
        history={history}
        trades={trades}
        votes={chartVotes}
      />

      <div className="grid">
        <section className="panel">
          <h2>Market Feed</h2>
          <table className="market-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Price</th>
                <th>Change</th>
                <th>Volume</th>
              </tr>
            </thead>
            <tbody>
              {market.map((t) => (
                <tr key={t.symbol}>
                  <td className="mono">{t.symbol}</td>
                  <td className="mono">{t.price.toLocaleString()}</td>
                  <td className={`mono ${t.change_pct >= 0 ? "up" : "down"}`}>
                    {t.change_pct >= 0 ? "+" : ""}
                    {t.change_pct.toFixed(3)}%
                  </td>
                  <td className="mono muted">{Math.round(t.volume).toLocaleString()}</td>
                </tr>
              ))}
              {!market.length && (
                <tr>
                  <td colSpan={4} className="muted">
                    Waiting for market data…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>

        <section className="panel">
          <h2>Portfolio</h2>
          <div className="portfolio">
            <div className="stat">
              <div className="label">Cash</div>
              <div className="value">{formatMoney(portfolio?.cash ?? 0)}</div>
            </div>
            <div className="stat">
              <div className="label">Realized P&amp;L</div>
              <div
                className={`value ${(portfolio?.realized_pnl ?? 0) >= 0 ? "up" : "down"}`}
              >
                {formatMoney(portfolio?.realized_pnl ?? 0)}
              </div>
            </div>
            <div className="stat">
              <div className="label">Positions</div>
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
              <div className="muted">No open positions yet.</div>
            )}
          </div>
        </section>

        <section className="panel" style={{ gridColumn: "1 / -1" }}>
          <h2>Agents</h2>
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
                      Waiting for first vote…
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        <section className="panel">
          <h2>Event Engine</h2>
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
            {!events.length && <div className="muted">No events yet — press START.</div>}
          </div>
        </section>

        <section className="panel">
          <h2>Decision Engine</h2>
          <div className="list">
            {decisions.map((d) => (
              <div className="item" key={d.id + String(d.ts)}>
                <div className="row">
                  <span>
                    <span className={`tag ${d.side}`}>{d.side}</span>{" "}
                    <strong className="mono">{d.symbol}</strong>
                  </span>
                  <span className="mono muted">
                    {(d.confidence * 100).toFixed(0)}%
                    {d.executed ? " · filled" : ""}
                  </span>
                </div>
                <div className="muted">{d.rationale}</div>
              </div>
            ))}
            {!decisions.length && (
              <div className="muted">Decisions will appear once agents vote.</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
