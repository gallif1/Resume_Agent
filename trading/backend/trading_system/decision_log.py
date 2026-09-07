"""Structured decision logs: SIGNAL / DECISION / EXECUTION + plain-text export."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from .config import DECISION_LOG_LIMIT, FILL_COOLDOWN_SEC
from .models import Decision, Side


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def build_market_snapshot(
    *,
    symbol: str,
    price: float,
    ts: float,
    volume: float,
    indicator: dict[str, Any],
    events: list[dict[str, Any]],
    quote_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """Only fields that exist — no fabricated indicators."""
    meta = quote_meta or {}
    return {
        "symbol": symbol,
        "price": price,
        "timestamp": ts,
        "volume": volume,
        "change_1m_pct": indicator.get("change_1m_pct"),
        "change_5m_pct": indicator.get("change_5m_pct"),
        "change_15m_pct": indicator.get("change_15m_pct"),
        "change_1h_pct": indicator.get("change_1h_pct"),
        "sma_fast": indicator.get("sma_fast"),
        "sma_slow": indicator.get("sma_slow"),
        "ema_fast": indicator.get("ema_fast"),
        "rsi_14": indicator.get("rsi_14"),
        "volatility": indicator.get("volatility"),
        "volume_state": indicator.get("volume_state"),
        "trend": indicator.get("short_trend"),
        "detected_events": [e.get("kind") for e in events if e.get("symbol") == symbol],
        "provider": meta.get("provider"),
        "session": meta.get("session"),
        "freshness": meta.get("freshness"),
        "stale_reason": meta.get("stale_reason"),
        "asset_class": meta.get("asset_class"),
    }


def build_agent_entries(
    votes: list[dict[str, Any]],
    ai_meta: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    seen_ai = False
    for v in votes:
        entry = {
            "agent_id": v.get("agent_id"),
            "agent_name": v.get("agent_name"),
            "action": v.get("side"),
            "confidence": v.get("confidence"),
            "reason": v.get("rationale"),
            "inputs": v.get("inputs") or {},
            "ts": v.get("ts"),
        }
        if v.get("agent_id") == "ai_analyst":
            seen_ai = True
            src = (v.get("inputs") or {}).get("source") or "AI_API"
            entry["source"] = src
        agents.append(entry)

    if not seen_ai and ai_meta:
        raw = str(ai_meta.get("source") or "SKIPPED").upper()
        source = {"API": "AI_API", "CACHE": "AI_CACHE", "SKIPPED": "AI_SKIPPED"}.get(raw, f"AI_{raw}")
        agents.append(
            {
                "agent_id": "ai_analyst",
                "agent_name": "AI Market Analyst",
                "action": ai_meta.get("action") or "HOLD",
                "confidence": ai_meta.get("confidence") or 0.0,
                "reason": ai_meta.get("reason") or "No AI analysis yet.",
                "inputs": {"skip_reason": ai_meta.get("skip_reason")},
                "source": source,
                "ts": ai_meta.get("ts"),
            }
        )
    return agents


def build_execution(
    *,
    decision: Decision,
    block_reason: str | None,
    cooldown_remaining_sec: float | None,
    last_fill_ts: float | None,
) -> dict[str, Any]:
    if decision.executed and decision.side != Side.HOLD:
        return {
            "status": "FILLED",
            "reason": "Paper fill applied",
            "fill_price": decision.fill_price,
            "quantity": decision.quantity,
            "cooldown_remaining_sec": None,
            "last_fill_ts": last_fill_ts,
        }
    reason = block_reason or (
        "HOLD — no trade"
        if decision.side == Side.HOLD
        else "Not filled"
    )
    return {
        "status": "NOT_FILLED",
        "reason": reason,
        "fill_price": None,
        "quantity": None,
        "cooldown_remaining_sec": (
            round(cooldown_remaining_sec, 1) if cooldown_remaining_sec is not None else None
        ),
        "last_fill_ts": last_fill_ts,
    }


def classify_kind(
    *,
    decision: Decision,
    execution: dict[str, Any],
    prev: dict[str, Any] | None,
) -> str:
    if execution.get("status") == "FILLED":
        return "TRADE_EXECUTED"
    if execution.get("status") == "NOT_FILLED" and "cooldown" in str(execution.get("reason") or "").lower():
        if (
            prev
            and prev.get("symbol") == decision.symbol
            and (prev.get("decision") or {}).get("action") == decision.side.value
        ):
            return "SIGNAL_STILL_ACTIVE"
        return "EXECUTION_BLOCKED"
    if decision.side != Side.HOLD and execution.get("status") == "NOT_FILLED":
        return "EXECUTION_BLOCKED"
    if (
        prev
        and prev.get("symbol") == decision.symbol
        and (prev.get("decision") or {}).get("action") == decision.side.value
        and (prev.get("execution") or {}).get("status") == execution.get("status")
    ):
        return "SIGNAL_STILL_ACTIVE"
    return "NEW_DECISION"


def build_decision_log(
    *,
    decision: Decision,
    market: dict[str, Any],
    agents: list[dict[str, Any]],
    execution: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    engine = decision.engine or {}
    return {
        "id": decision.id,
        "timestamp": decision.ts,
        "symbol": decision.symbol,
        "kind": kind,
        "signal": {
            "action": decision.side.value,
            "agents": agents,
        },
        "market": market,
        "decision": {
            "action": decision.side.value,
            "final_confidence": decision.confidence,
            "rationale": decision.rationale,
            "explanation": engine.get("explanation"),
            "vote_counts": engine.get("vote_counts"),
            "weighted_contributions": engine.get("weighted_contributions"),
            "weights": engine.get("weights"),
            "action_score": engine.get("action_score"),
            "hold_score": engine.get("hold_score"),
            "hold_gate": engine.get("hold_gate"),
            "threshold": engine.get("threshold"),
            "winning_action": engine.get("winning_action"),
            "confidence_debug": engine.get("confidence_debug"),
        },
        "execution": execution,
    }


class DecisionLogBuffer:
    """Rolling in-memory decision logs with cooldown coalescing."""

    def __init__(self, limit: int | None = None) -> None:
        self.limit = int(limit or DECISION_LOG_LIMIT)
        self._logs: list[dict[str, Any]] = []
        self._last_by_symbol: dict[str, dict[str, Any]] = {}

    @property
    def recent(self) -> list[dict[str, Any]]:
        return list(reversed(self._logs[-self.limit :]))

    def add(self, record: dict[str, Any]) -> dict[str, Any]:
        symbol = str(record.get("symbol") or "")
        kind = record.get("kind")
        prev = self._last_by_symbol.get(symbol)

        # Coalesce noisy SIGNAL_STILL_ACTIVE / repeated EXECUTION_BLOCKED cooldown rows.
        if (
            kind in {"SIGNAL_STILL_ACTIVE", "EXECUTION_BLOCKED"}
            and prev
            and prev.get("kind") in {"SIGNAL_STILL_ACTIVE", "EXECUTION_BLOCKED", "NEW_DECISION"}
            and (prev.get("decision") or {}).get("action") == (record.get("decision") or {}).get("action")
            and (prev.get("execution") or {}).get("status") == "NOT_FILLED"
            and "cooldown" in str((record.get("execution") or {}).get("reason") or "").lower()
            and "cooldown" in str((prev.get("execution") or {}).get("reason") or "").lower()
        ):
            # Update the existing row in place (refresh cooldown / snapshot).
            record["kind"] = "SIGNAL_STILL_ACTIVE"
            record["id"] = prev.get("id") or record["id"]
            for i in range(len(self._logs) - 1, -1, -1):
                if self._logs[i].get("id") == prev.get("id"):
                    self._logs[i] = record
                    break
            else:
                self._logs.append(record)
            self._last_by_symbol[symbol] = record
            return record

        self._logs.append(record)
        if len(self._logs) > self.limit:
            self._logs = self._logs[-self.limit :]
        self._last_by_symbol[symbol] = record
        return record


def format_decision_logs_text(logs: list[dict[str, Any]], limit: int | None = None) -> str:
    """Plain-text export suitable for pasting into ChatGPT."""
    rows = logs[: limit or len(logs)]
    chunks: list[str] = []
    for i, log in enumerate(rows, start=1):
        market = log.get("market") or {}
        decision = log.get("decision") or {}
        execution = log.get("execution") or {}
        agents = (log.get("signal") or {}).get("agents") or []
        dbg = decision.get("confidence_debug") or {}
        weights = decision.get("weights") or {}
        counts = decision.get("vote_counts") or {}

        lines = [
            f"=== DECISION {i} ===",
            f"Kind: {log.get('kind')}",
            f"Timestamp: {_fmt_ts(log.get('timestamp'))}",
            f"Symbol: {log.get('symbol')}",
            "",
            "MARKET",
            f"Price: {market.get('price')}",
            f"1m: {_pct(market.get('change_1m_pct'))}",
            f"5m: {_pct(market.get('change_5m_pct'))}",
            f"15m: {_pct(market.get('change_15m_pct'))}",
            f"RSI: {market.get('rsi_14') if market.get('rsi_14') is not None else '—'}",
            f"SMA fast: {market.get('sma_fast') if market.get('sma_fast') is not None else '—'}",
            f"SMA slow: {market.get('sma_slow') if market.get('sma_slow') is not None else '—'}",
            f"EMA fast: {market.get('ema_fast') if market.get('ema_fast') is not None else '—'}",
            f"Trend: {market.get('trend') or '—'}",
            f"Volume state: {market.get('volume_state') or '—'}",
            f"Volatility: {market.get('volatility') if market.get('volatility') is not None else '—'}",
            f"Events: {', '.join(market.get('detected_events') or []) or '—'}",
            f"Provider: {market.get('provider') or '—'}",
            f"Session/freshness: {market.get('session') or '—'}/{market.get('freshness') or '—'}",
            "",
            "AGENTS",
        ]
        for a in agents:
            lines.append(f"{a.get('agent_name') or a.get('agent_id')}:")
            lines.append(f"Action: {a.get('action')}")
            conf = a.get("confidence")
            lines.append(f"Confidence: {float(conf) * 100:.0f}%" if conf is not None else "Confidence: —")
            lines.append(f"Reason: {a.get('reason') or '—'}")
            if a.get("agent_id") == "ai_analyst" or a.get("source"):
                lines.append(f"Source: {a.get('source') or '—'}")
                lines.append(f"Last analysis: {_fmt_ts(a.get('ts'))}")
            inputs = a.get("inputs") or {}
            if inputs:
                # Compact key values actually used by the rule.
                shown = ", ".join(f"{k}={v}" for k, v in list(inputs.items())[:8])
                lines.append(f"Inputs: {shown}")
            lines.append("")

        lines.extend(
            [
                "DECISION ENGINE",
                f"Votes: BUY {counts.get('BUY', 0)} / SELL {counts.get('SELL', 0)} / HOLD {counts.get('HOLD', 0)}",
                f"Weighted scores: BUY={weights.get('BUY', 0)} SELL={weights.get('SELL', 0)} HOLD={weights.get('HOLD', 0)}",
                f"Threshold (min_confidence): {decision.get('threshold')}",
                f"Action score: {decision.get('action_score')}",
                f"Hold gate (hold*0.85): {decision.get('hold_gate')}",
                f"Final action: {decision.get('action')}",
                f"Final confidence: {float(decision.get('final_confidence') or 0) * 100:.0f}%",
                f"Explanation: {decision.get('explanation') or decision.get('rationale') or '—'}",
                "",
                "CONFIDENCE DEBUG",
                f"raw_score = {dbg.get('raw_score')}",
                f"denominator = {dbg.get('denominator')} ({dbg.get('formula')})",
                f"confidence_before_cap = {dbg.get('confidence_before_cap')}",
                f"final_confidence = {dbg.get('final_confidence')} (cap {dbg.get('cap')})",
                f"note: {dbg.get('note') or '—'}",
                "",
                "EXECUTION",
                f"Status: {execution.get('status')}",
                f"Reason: {execution.get('reason')}",
            ]
        )
        if execution.get("cooldown_remaining_sec") is not None:
            lines.append(f"Cooldown remaining: {execution.get('cooldown_remaining_sec')}s")
        if execution.get("status") == "FILLED":
            lines.append(f"Fill price: {execution.get('fill_price')}")
            lines.append(f"Quantity: {execution.get('quantity')}")
        lines.append("")
        lines.append("=" * 32)
        lines.append("")
        chunks.append("\n".join(lines))
    header = (
        f"AI Trading System — decision logs\n"
        f"Exported: {_fmt_ts(time.time())}\n"
        f"Entries: {len(rows)}\n"
        f"Fill cooldown config: {FILL_COOLDOWN_SEC}s\n\n"
    )
    return header + "\n".join(chunks)
