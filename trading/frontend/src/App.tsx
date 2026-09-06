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
  type Snapshot,
  type Tick,
  type TradingConfig,
} from "./api";

type LatestVotes = Record<string, AgentVote>;

function formatMoney(n: number) {
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

export default function App() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<MarketEvent[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [votes, setVotes] = useState<LatestVotes>({});
  const [wsState, setWsState] = useState<"connecting" | "live" | "dead" | "polling">(
    "connecting"
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [homeUrl, setHomeUrl] = useState("/");
  const [flash, setFlash] = useState(false);
  const [config, setConfig] = useState<TradingConfig | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    fetchConfig()
      .then((c) => {
        setConfig(c);
        setHomeUrl(c.resume_agent_home || "/");
      })
      .catch(() => setHomeUrl("/"));
    fetchSnapshot()
      .then((s) => {
        setSnap(s);
        setEvents(s.events || []);
        setDecisions(s.decisions || []);
      })
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
            const s = msg.payload as Snapshot;
            setSnap(s);
            setEvents(s.events || []);
            setDecisions(s.decisions || []);
          } else if (msg.type === "tick") {
            const p = msg.payload as {
              tick_count: number;
              state: Snapshot["state"];
              market: Tick[];
              events: MarketEvent[];
              votes: AgentVote[];
              decisions: Decision[];
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
            if (p.events?.length) setEvents((prev) => [...p.events, ...prev].slice(0, 40));
            if (p.decisions?.length) {
              setDecisions((prev) => [...p.decisions, ...prev].slice(0, 40));
            }
            if (p.votes?.length) {
              const next: LatestVotes = {};
              for (const v of p.votes) next[v.agent_id] = v;
              setVotes(next);
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
          setSnap(s);
          setEvents(s.events || []);
          setDecisions(s.decisions || []);
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
      setSnap(s);
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
