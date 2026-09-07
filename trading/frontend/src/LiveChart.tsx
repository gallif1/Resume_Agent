import { useMemo, useState } from "react";
import type { AgentVote, MarketMeta, PricePoint, TradeMarker } from "./api";

const PAD = { top: 18, right: 16, bottom: 28, left: 56 };
const AGENT_COLORS: Record<string, string> = {
  momentum: "#3dd6c6",
  mean_reversion: "#f0b429",
  volatility: "#7aa2ff",
  ai_analyst: "#c084fc",
};

type Props = {
  symbols: string[];
  history: Record<string, PricePoint[]>;
  trades: TradeMarker[];
  votes: AgentVote[];
  timeframe: string;
  timeframes: string[];
  onTimeframe: (tf: string) => void;
  marketMeta?: MarketMeta | null;
  realData: boolean;
};

function formatPrice(n: number) {
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (n >= 100) return n.toFixed(2);
  return n.toFixed(3);
}

export default function LiveChart({
  symbols,
  history,
  trades,
  votes,
  timeframe,
  timeframes,
  onTimeframe,
  marketMeta,
  realData,
}: Props) {
  const [symbol, setSymbol] = useState(symbols[0] || "BTC-USD");
  const active = symbols.includes(symbol) ? symbol : symbols[0] || symbol;
  const points = history[active] || [];
  const symbolTrades = trades.filter((t) => t.symbol === active && t.price != null);
  const symbolVotes = votes.filter(
    (v) => v.symbol === active && (v.side === "BUY" || v.side === "SELL")
  );
  const meta = marketMeta?.symbols?.[active];

  const chart = useMemo(() => {
    const width = 920;
    const height = 320;
    const innerW = width - PAD.left - PAD.right;
    const innerH = height - PAD.top - PAD.bottom;
    if (points.length < 2) {
      return { width, height, path: "", min: 0, max: 0, xAt: () => 0, yAt: () => 0, ticks: [] as number[] };
    }
    const prices = points.map((p) => p.price);
    let min = Math.min(...prices);
    let max = Math.max(...prices);
    const pad = (max - min) * 0.08 || max * 0.002 || 1;
    min -= pad;
    max += pad;
    const t0 = points[0].ts;
    const t1 = points[points.length - 1].ts;
    const span = Math.max(t1 - t0, 1e-6);
    const xAt = (ts: number) => PAD.left + ((ts - t0) / span) * innerW;
    const yAt = (price: number) => PAD.top + ((max - price) / (max - min || 1)) * innerH;
    const path = points
      .map((p, i) => `${i === 0 ? "M" : "L"}${xAt(p.ts).toFixed(1)},${yAt(p.price).toFixed(1)}`)
      .join(" ");
    const ticks = [min, min + (max - min) / 2, max];
    return { width, height, path, min, max, xAt, yAt, ticks };
  }, [points]);

  const last = points[points.length - 1];

  return (
    <section className="panel live-chart-panel">
      <div className="chart-head">
        <div>
          <h2>Live Chart</h2>
          <div className="chart-source-row">
            <span className={`data-badge ${realData ? "real" : "sim"}`}>
              {realData ? "REAL MARKET DATA" : "SIMULATED DATA"}
            </span>
            {meta && (
              <span className="muted mono chart-meta-line">
                {meta.provider} · {meta.session.toUpperCase()} · {meta.freshness.toUpperCase()}
                {meta.last_update_ts
                  ? ` · upd ${new Date(meta.last_update_ts * 1000).toLocaleTimeString()}`
                  : ""}
              </span>
            )}
          </div>
        </div>
        <div className="chart-legend">
          <span className="legend-item">
            <i className="swatch buy-tri" /> Buy fill
          </span>
          <span className="legend-item">
            <i className="swatch sell-tri" /> Sell fill
          </span>
          <span className="legend-item">
            <i className="swatch vote-dot" /> Agent vote
          </span>
        </div>
      </div>

      <div className="symbol-tabs" role="tablist" aria-label="Chart symbol">
        {symbols.map((sym) => (
          <button
            key={sym}
            type="button"
            role="tab"
            aria-selected={sym === active}
            className={`symbol-tab ${sym === active ? "active" : ""}`}
            onClick={() => setSymbol(sym)}
          >
            {sym}
          </button>
        ))}
      </div>

      <div className="symbol-tabs tf-tabs" role="tablist" aria-label="Chart timeframe">
        {timeframes.map((tf) => (
          <button
            key={tf}
            type="button"
            role="tab"
            aria-selected={tf === timeframe}
            className={`symbol-tab ${tf === timeframe ? "active" : ""}`}
            onClick={() => onTimeframe(tf)}
          >
            {tf}
          </button>
        ))}
      </div>

      <div className="chart-stage">
        {points.length < 2 ? (
          <div className="chart-empty muted">
            {realData ? (
              <>
                No chart data yet for <strong>{active}</strong> ({timeframe}).
                {active === "AAPL" || active === "NVDA"
                  ? " Stocks load candles from Yahoo — wait one poll cycle, or check network."
                  : " Crypto loads from Coinbase — wait a moment after START."}
                {meta?.stale_reason ? ` (${meta.stale_reason})` : ""}
              </>
            ) : (
              "Waiting for simulated price history…"
            )}
          </div>
        ) : (
          <svg
            className="live-chart"
            viewBox={`0 0 ${chart.width} ${chart.height}`}
            role="img"
            aria-label={`${active} live price chart`}
          >
            <defs>
              <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="rgba(61, 214, 198, 0.28)" />
                <stop offset="100%" stopColor="rgba(61, 214, 198, 0)" />
              </linearGradient>
            </defs>

            {chart.ticks.map((price) => {
              const y = chart.yAt(price);
              return (
                <g key={price}>
                  <line
                    x1={PAD.left}
                    x2={chart.width - PAD.right}
                    y1={y}
                    y2={y}
                    className="chart-grid"
                  />
                  <text x={PAD.left - 8} y={y + 4} textAnchor="end" className="chart-axis">
                    {formatPrice(price)}
                  </text>
                </g>
              );
            })}

            <path
              d={`${chart.path} L${chart.xAt(points[points.length - 1].ts)},${
                chart.height - PAD.bottom
              } L${chart.xAt(points[0].ts)},${chart.height - PAD.bottom} Z`}
              fill="url(#priceFill)"
              opacity={0.9}
            />
            <path d={chart.path} className="chart-line" />

            {symbolVotes.map((v) => {
              const nearest =
                points.reduce((best, p) =>
                  Math.abs(p.ts - v.ts) < Math.abs(best.ts - v.ts) ? p : best
                ) || last;
              const x = chart.xAt(v.ts || nearest.ts);
              const y = chart.yAt(nearest.price);
              const color = AGENT_COLORS[v.agent_id] || "#93a4bf";
              return (
                <g key={`vote-${v.agent_id}-${v.ts}-${v.side}`} className="vote-marker">
                  <circle cx={x} cy={y} r={4.5} fill={color} stroke="#0b1220" strokeWidth={1.5} />
                  <title>
                    {v.agent_name}: {v.side} @ {formatPrice(nearest.price)} (
                    {(v.confidence * 100).toFixed(0)}%)
                  </title>
                </g>
              );
            })}

            {symbolTrades.map((t) => {
              const x = chart.xAt(t.ts);
              const y = chart.yAt(t.price as number);
              const buy = t.side === "BUY";
              const pointsAttr = buy
                ? `${x},${y - 9} ${x - 7},${y + 5} ${x + 7},${y + 5}`
                : `${x},${y + 9} ${x - 7},${y - 5} ${x + 7},${y - 5}`;
              const agents =
                t.agents?.map((a) => a.name || a.id).filter(Boolean).join(", ") || "agents";
              return (
                <g key={`trade-${t.id}`} className={`trade-marker ${t.side.toLowerCase()}`}>
                  <polygon
                    points={pointsAttr}
                    fill={buy ? "var(--buy)" : "var(--sell)"}
                    stroke="#0b1220"
                    strokeWidth={1.2}
                  />
                  <title>
                    {t.side} {t.quantity?.toFixed?.(4) ?? ""} @ {formatPrice(t.price as number)} ·{" "}
                    {agents}
                  </title>
                </g>
              );
            })}

            {last && (
              <g>
                <circle
                  cx={chart.xAt(last.ts)}
                  cy={chart.yAt(last.price)}
                  r={3.5}
                  className="chart-live-dot"
                />
                <text
                  x={chart.width - PAD.right}
                  y={PAD.top - 4}
                  textAnchor="end"
                  className="chart-last"
                >
                  {active} · {formatPrice(last.price)}
                </text>
              </g>
            )}
          </svg>
        )}
      </div>

      <div className="chart-foot muted">
        Real historical candles ({timeframe}) with agent votes and paper fills overlaid. Paper trading
        only — no real broker orders.
      </div>
    </section>
  );
}
