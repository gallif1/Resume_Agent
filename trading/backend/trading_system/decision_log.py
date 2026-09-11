"""Structured decision logs: SIGNAL / DECISION / EXECUTION + plain-text export."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from .config import DECISION_LOG_HEARTBEAT_SECONDS, DECISION_LOG_LIMIT, FILL_COOLDOWN_SEC
from .models import Decision, Side


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _yes_no(v: bool | None) -> str:
    if v is True:
        return "YES"
    if v is False:
        return "NO"
    return "PENDING"


def build_market_snapshot(
    *,
    symbol: str,
    price: float,
    ts: float,
    volume: float,
    indicator: dict[str, Any],
    events: list[dict[str, Any]],
    quote_meta: dict[str, Any] | None,
    candles: list[Any] | None = None,
) -> dict[str, Any]:
    """Only fields that exist — no fabricated indicators.

    Optionally expands with indicators_used / interpreted_signals via
    indicators.evidence (safe for UnifiedDecision consumers).
    """
    meta = quote_meta or {}
    snap: dict[str, Any] = {
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
    try:
        from .indicators.evidence import build_decision_evidence

        evidence = build_decision_evidence(
            candles=candles,
            indicator=indicator,
            price=price,
            symbol=symbol,
        )
        snap["indicators_used"] = evidence.get("indicators_used")
        snap["interpreted_signals"] = evidence.get("interpreted_signals")
    except Exception:  # noqa: BLE001 — never break decision logging
        snap["indicators_used"] = None
        snap["interpreted_signals"] = None
    return snap


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
        source = {
            "API": "AI_API",
            "CACHE": "AI_CACHE",
            "SKIPPED": "AI_SKIPPED",
            "PRETRADE_API": "AI_PRETRADE_API",
            "PRETRADE_CACHE": "AI_PRETRADE_CACHE",
            "PRETRADE_SKIPPED": "AI_PRETRADE_SKIPPED",
            "PRETRADE_UNAVAILABLE": "AI_PRETRADE_UNAVAILABLE",
        }.get(raw, f"AI_{raw}" if not raw.startswith("AI_") else raw)
        agents.append(
            {
                "agent_id": "ai_analyst",
                "agent_name": "אנליסט שוק AI",
                "action": ai_meta.get("action") or "HOLD",
                "confidence": ai_meta.get("confidence") or 0.0,
                "reason": ai_meta.get("reason") or "עדיין אין ניתוח AI.",
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
            "reason": "ביצוע נייר הוחל",
            "fill_price": decision.fill_price,
            "quantity": decision.quantity,
            "cooldown_remaining_sec": None,
            "last_fill_ts": last_fill_ts,
        }
    reason = block_reason or (
        "HOLD — אין עסקה"
        if decision.side == Side.HOLD
        else "לא בוצע"
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
    pretrade: dict[str, Any] | None = None,
) -> str:
    if execution.get("status") == "FILLED":
        return "TRADE_EXECUTED"
    if pretrade and pretrade.get("source") in {
        "PRETRADE_API",
        "PRETRADE_CACHE",
        "PRETRADE_SKIPPED",
        "PRETRADE_UNAVAILABLE",
        "AI_PRETRADE_API",
        "AI_PRETRADE_CACHE",
        "AI_PRETRADE_SKIPPED",
        "AI_PRETRADE_UNAVAILABLE",
    }:
        # Still classify execution status primarily; marker stored on record.
        pass
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


def _agent_signature(agents: list[dict[str, Any]]) -> tuple:
    return tuple(
        (
            a.get("agent_id"),
            a.get("action"),
            round(float(a.get("confidence") or 0), 2),
            a.get("source"),
        )
        for a in agents
    )


def meaningful_log_change(prev: dict[str, Any] | None, record: dict[str, Any]) -> bool:
    if not prev:
        return True
    if prev.get("kind") != record.get("kind"):
        # Always keep fills / new blocks distinct from still-active noise.
        if record.get("kind") in {
            "TRADE_EXECUTED",
            "NEW_DECISION",
            "EXECUTION_BLOCKED",
        }:
            return True
        if prev.get("kind") == "SIGNAL_STILL_ACTIVE" and record.get("kind") == "SIGNAL_STILL_ACTIVE":
            pass
        else:
            return True
    prev_d = prev.get("decision") or {}
    cur_d = record.get("decision") or {}
    if prev_d.get("action") != cur_d.get("action"):
        return True
    prev_c = float(prev_d.get("final_confidence") or 0)
    cur_c = float(cur_d.get("final_confidence") or 0)
    if abs(prev_c - cur_c) >= 0.05:
        return True
    prev_agents = _agent_signature((prev.get("signal") or {}).get("agents") or [])
    cur_agents = _agent_signature((record.get("signal") or {}).get("agents") or [])
    if prev_agents != cur_agents:
        return True
    prev_ex = prev.get("execution") or {}
    cur_ex = record.get("execution") or {}
    if prev_ex.get("status") != cur_ex.get("status"):
        return True
    if prev_ex.get("status") == "FILLED" or cur_ex.get("status") == "FILLED":
        return True
    # New block reason (not just cooldown countdown)
    prev_reason = str(prev_ex.get("reason") or "")
    cur_reason = str(cur_ex.get("reason") or "")
    if prev_reason != cur_reason and "cooldown" not in cur_reason.lower():
        return True
    if record.get("pretrade") and record.get("pretrade") != prev.get("pretrade"):
        return True
    prev_m = prev.get("market") or {}
    cur_m = record.get("market") or {}
    if prev_m.get("freshness") != cur_m.get("freshness") or prev_m.get("session") != cur_m.get("session"):
        return True
    if (prev_m.get("detected_events") or []) != (cur_m.get("detected_events") or []):
        return True
    return False


def build_decision_log(
    *,
    decision: Decision,
    market: dict[str, Any],
    agents: list[dict[str, Any]],
    execution: dict[str, Any],
    kind: str,
    pretrade: dict[str, Any] | None = None,
    outcome: dict[str, Any] | None = None,
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
        "pretrade": pretrade,
        "outcome": outcome,
    }


class DecisionLogBuffer:
    """Rolling in-memory decision logs with heartbeat + change-based dedupe."""

    ALWAYS_RECORD = frozenset(
        {
            "TRADE_EXECUTED",
            "NEW_DECISION",
        }
    )

    def __init__(
        self,
        limit: int | None = None,
        heartbeat_sec: float | None = None,
    ) -> None:
        self.limit = int(limit or DECISION_LOG_LIMIT)
        self.heartbeat_sec = float(
            heartbeat_sec if heartbeat_sec is not None else DECISION_LOG_HEARTBEAT_SECONDS
        )
        self._logs: list[dict[str, Any]] = []
        self._last_by_symbol: dict[str, dict[str, Any]] = {}
        self._last_recorded_ts: dict[str, float] = {}

    @property
    def recent(self) -> list[dict[str, Any]]:
        return list(reversed(self._logs[-self.limit :]))

    def clear(self) -> int:
        """Drop all in-memory decision logs. Returns how many were removed."""
        n = len(self._logs)
        self._logs.clear()
        self._last_by_symbol.clear()
        self._last_recorded_ts.clear()
        return n

    def add(self, record: dict[str, Any], *, force: bool = False) -> dict[str, Any] | None:
        """Append or coalesce. Returns record if stored/updated, None if skipped as noise."""
        symbol = str(record.get("symbol") or "")
        kind = record.get("kind")
        prev = self._last_by_symbol.get(symbol)
        now = float(record.get("timestamp") or time.time())
        last_ts = self._last_recorded_ts.get(symbol, 0.0)
        changed = meaningful_log_change(prev, record)
        heartbeat_due = (now - last_ts) >= self.heartbeat_sec

        # Always keep fills and genuinely new decisions.
        must = force or kind in self.ALWAYS_RECORD or (
            kind == "EXECUTION_BLOCKED" and changed
        )
        if record.get("pretrade") and str((record.get("pretrade") or {}).get("source") or "").startswith(
            ("PRETRADE", "AI_PRETRADE")
        ):
            # Pretrade validation is always worth a full row once per attempt.
            if changed or not prev or (prev.get("pretrade") or {}).get("source") != (
                record.get("pretrade") or {}
            ).get("source"):
                must = True

        if kind == "SIGNAL_STILL_ACTIVE" and not must:
            if not heartbeat_due and not changed:
                # Quiet update of last-seen pointer without growing history.
                if prev:
                    # Refresh cooldown countdown on existing row only.
                    if "cooldown" in str((record.get("execution") or {}).get("reason") or "").lower():
                        for i in range(len(self._logs) - 1, -1, -1):
                            if self._logs[i].get("id") == prev.get("id"):
                                merged = dict(prev)
                                merged["execution"] = record.get("execution")
                                merged["market"] = record.get("market")
                                merged["timestamp"] = record.get("timestamp")
                                if record.get("outcome"):
                                    merged["outcome"] = record["outcome"]
                                self._logs[i] = merged
                                self._last_by_symbol[symbol] = merged
                                break
                return None
            # Heartbeat: update in place rather than append a near-duplicate.
            if prev and prev.get("kind") in {
                "SIGNAL_STILL_ACTIVE",
                "EXECUTION_BLOCKED",
                "NEW_DECISION",
            }:
                record = dict(record)
                record["kind"] = "SIGNAL_STILL_ACTIVE"
                record["id"] = prev.get("id") or record["id"]
                for i in range(len(self._logs) - 1, -1, -1):
                    if self._logs[i].get("id") == prev.get("id"):
                        self._logs[i] = record
                        break
                else:
                    self._logs.append(record)
                self._last_by_symbol[symbol] = record
                self._last_recorded_ts[symbol] = now
                return record

        # Coalesce noisy SIGNAL_STILL_ACTIVE / repeated EXECUTION_BLOCKED cooldown rows.
        if (
            kind in {"SIGNAL_STILL_ACTIVE", "EXECUTION_BLOCKED"}
            and prev
            and prev.get("kind") in {"SIGNAL_STILL_ACTIVE", "EXECUTION_BLOCKED", "NEW_DECISION"}
            and (prev.get("decision") or {}).get("action") == (record.get("decision") or {}).get("action")
            and (prev.get("execution") or {}).get("status") == "NOT_FILLED"
            and "cooldown" in str((record.get("execution") or {}).get("reason") or "").lower()
            and "cooldown" in str((prev.get("execution") or {}).get("reason") or "").lower()
            and not must
        ):
            if not heartbeat_due and not changed:
                record_u = dict(prev)
                record_u["execution"] = record.get("execution")
                record_u["market"] = record.get("market")
                record_u["timestamp"] = record.get("timestamp")
                if record.get("outcome"):
                    record_u["outcome"] = record["outcome"]
                for i in range(len(self._logs) - 1, -1, -1):
                    if self._logs[i].get("id") == prev.get("id"):
                        self._logs[i] = record_u
                        break
                self._last_by_symbol[symbol] = record_u
                return None
            record = dict(record)
            record["kind"] = "SIGNAL_STILL_ACTIVE"
            record["id"] = prev.get("id") or record["id"]
            for i in range(len(self._logs) - 1, -1, -1):
                if self._logs[i].get("id") == prev.get("id"):
                    self._logs[i] = record
                    break
            else:
                self._logs.append(record)
            self._last_by_symbol[symbol] = record
            self._last_recorded_ts[symbol] = now
            return record

        if not must and not changed and not heartbeat_due:
            return None

        self._logs.append(record)
        if len(self._logs) > self.limit:
            self._logs = self._logs[-self.limit :]
        self._last_by_symbol[symbol] = record
        self._last_recorded_ts[symbol] = now
        return record

    def patch_outcome(self, decision_id: str, outcome: dict[str, Any]) -> None:
        for i, row in enumerate(self._logs):
            if row.get("id") == decision_id:
                updated = dict(row)
                updated["outcome"] = outcome
                self._logs[i] = updated
                sym = str(updated.get("symbol") or "")
                if self._last_by_symbol.get(sym, {}).get("id") == decision_id:
                    self._last_by_symbol[sym] = updated
                break


def _format_outcome_block(outcome: dict[str, Any] | None) -> list[str]:
    lines = ["OUTCOME"]
    if not outcome:
        lines.append("PENDING (no actionable outcome tracked)")
        return lines
    lines.append(f"Entry price: {outcome.get('entry_price')}")
    horizons = outcome.get("horizons") or {}
    for key in ("5m", "15m", "60m"):
        h = horizons.get(key) or {}
        status = h.get("status") or "pending"
        if status == "resolved":
            correct = h.get("direction_correct")
            lines.append(f"{key} price: {h.get('price')}")
            lines.append(f"{key} return: {_pct(h.get('return_pct'))}")
            lines.append(f"{key} direction correct: {_yes_no(correct)}")
        elif status in {"unavailable", "no_history"}:
            lines.append(f"{key}: {status.upper()} (price not recoverable)")
        else:
            lines.append(f"{key}: PENDING")
    return lines


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
        pretrade = log.get("pretrade") or {}

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
                shown = ", ".join(f"{k}={v}" for k, v in list(inputs.items())[:8])
                lines.append(f"Inputs: {shown}")
            lines.append("")

        if pretrade:
            lines.extend(
                [
                    "AI PRETRADE",
                    f"Source: {pretrade.get('source')}",
                    f"Skip reason: {pretrade.get('skip_reason') or '—'}",
                    f"Action: {pretrade.get('action') or '—'}",
                    f"Confidence: {pretrade.get('confidence')}",
                    "",
                ]
            )

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
                f"winning_action = {dbg.get('winning_action')}",
                f"winning_score = {dbg.get('winning_score', dbg.get('raw_score'))}",
                f"total_weight = {dbg.get('total_weight', dbg.get('total_all_weights'))}",
                f"action_support = {dbg.get('action_support')}",
                f"agreement_factor = {dbg.get('agreement_factor')}",
                f"hold_ratio = {dbg.get('hold_ratio')}",
                f"opposition_ratio = {dbg.get('opposition_ratio')}",
                f"final_confidence = {dbg.get('final_confidence')}",
                f"formula = {dbg.get('formula')}",
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
        lines.extend(_format_outcome_block(log.get("outcome")))
        lines.append("")
        lines.append("=" * 32)
        lines.append("")
        chunks.append("\n".join(lines))
    header = (
        f"AI Trading System — decision logs\n"
        f"Exported: {_fmt_ts(time.time())}\n"
        f"Entries: {len(rows)}\n"
        f"Fill cooldown config: {FILL_COOLDOWN_SEC}s\n"
        f"Log heartbeat: {DECISION_LOG_HEARTBEAT_SECONDS}s\n\n"
    )
    return header + "\n".join(chunks)
