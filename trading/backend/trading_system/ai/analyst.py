"""AI Market Analyst — LLM vote only (never executes trades)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from ..config import (
    AI_CACHE_TTL_SECONDS,
    AI_ENABLED,
    AI_MAX_CALLS_PER_HOUR,
    AI_MIN_INTERVAL_SECONDS,
    AI_MODEL,
    AI_PRICE_TRIGGER_PERCENT,
    AI_PROVIDER,
    DATA_DIR,
    OPENAI_API_KEY,
)
from ..models import AgentVote, MarketEvent, Side, Tick

logger = logging.getLogger("trading.ai")


@dataclass
class AIResult:
    action: str
    confidence: float
    reason: str
    source: str  # API | CACHE | SKIPPED
    skip_reason: str | None = None

    def to_vote(self, symbol: str) -> AgentVote:
        side = Side.HOLD
        if self.action.upper() == "BUY":
            side = Side.BUY
        elif self.action.upper() == "SELL":
            side = Side.SELL
        src = {
            "API": "AI_API",
            "CACHE": "AI_CACHE",
            "SKIPPED": "AI_SKIPPED",
        }.get(self.source, self.source)
        return AgentVote(
            agent_id="ai_analyst",
            agent_name="AI Market Analyst",
            symbol=symbol,
            side=side,
            confidence=float(self.confidence),
            rationale=self.reason[:240],
            inputs={
                "source": src,
                "raw_source": self.source,
                "skip_reason": self.skip_reason,
            },
        )


class AITriggerLayer:
    """Decide whether an LLM call is warranted (cost control)."""

    def __init__(self) -> None:
        self._last_call_ts = 0.0
        self._last_prices: dict[str, float] = {}
        self._hour_bucket = ""
        self._calls_this_hour = 0

    @property
    def calls_this_hour(self) -> int:
        self._roll_hour()
        return self._calls_this_hour

    def _roll_hour(self) -> None:
        bucket = time.strftime("%Y%m%d%H", time.gmtime())
        if bucket != self._hour_bucket:
            self._hour_bucket = bucket
            self._calls_this_hour = 0

    def record_call(self) -> None:
        self._roll_hour()
        self._calls_this_hour += 1
        self._last_call_ts = time.time()

    def should_call(
        self,
        symbol: str,
        price: float,
        events: list[MarketEvent],
        heuristic_votes: list[AgentVote],
    ) -> tuple[bool, str]:
        if not AI_ENABLED:
            return False, "ai_disabled"
        if AI_PROVIDER != "openai" or not OPENAI_API_KEY:
            return False, "missing_api_key"
        self._roll_hour()
        if self._calls_this_hour >= AI_MAX_CALLS_PER_HOUR:
            return False, "hourly_limit"
        now = time.time()
        if now - self._last_call_ts < AI_MIN_INTERVAL_SECONDS:
            return False, "min_interval"

        meaningful = [
            e
            for e in events
            if e.symbol == symbol
            and e.kind in {"spike_up", "spike_down", "volume_surge", "mean_deviation"}
        ]
        if meaningful:
            return True, "event"

        prev = self._last_prices.get(symbol)
        if prev and prev > 0:
            move = abs(price - prev) / prev * 100
            if move >= AI_PRICE_TRIGGER_PERCENT:
                return True, "price_move"

        sides = {v.side for v in heuristic_votes if v.side != Side.HOLD}
        if Side.BUY in sides and Side.SELL in sides:
            return True, "agent_disagreement"

        # Periodic: allow one call if nothing else and interval elapsed 3x min.
        if now - self._last_call_ts >= AI_MIN_INTERVAL_SECONDS * 3:
            return True, "periodic"

        return False, "no_trigger"

    def note_price(self, symbol: str, price: float) -> None:
        self._last_prices[symbol] = price


class AIMarketAnalyst:
    agent_id = "ai_analyst"
    agent_name = "AI Market Analyst"

    def __init__(self) -> None:
        self.trigger = AITriggerLayer()
        self._cache: dict[str, tuple[float, AIResult]] = {}
        self._last_by_symbol: dict[str, dict[str, Any]] = {}
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._log_path = DATA_DIR / "ai_decisions.jsonl"

    def status(self) -> dict[str, Any]:
        return {
            "enabled": AI_ENABLED,
            "provider": AI_PROVIDER,
            "model": AI_MODEL,
            "api_key_configured": bool(OPENAI_API_KEY),
            "calls_this_hour": self.trigger.calls_this_hour,
            "max_calls_per_hour": AI_MAX_CALLS_PER_HOUR,
            "min_interval_sec": AI_MIN_INTERVAL_SECONDS,
            "last_by_symbol": self._last_by_symbol,
        }

    def maybe_vote(
        self,
        tick: Tick,
        snapshot: dict[str, Any],
        events: list[MarketEvent],
        heuristic_votes: list[AgentVote],
    ) -> AgentVote | None:
        """Return AI vote or None when skipped (heuristics still run)."""
        ok, why = self.trigger.should_call(tick.symbol, tick.price, events, heuristic_votes)
        cache_key = self._cache_key(snapshot)
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[0] < AI_CACHE_TTL_SECONDS:
            result = cached[1]
            result.source = "CACHE"
            self._last_by_symbol[tick.symbol] = {
                "action": result.action,
                "confidence": result.confidence,
                "reason": result.reason,
                "source": "CACHE",
                "ts": time.time(),
            }
            self.trigger.note_price(tick.symbol, tick.price)
            return result.to_vote(tick.symbol)

        if not ok:
            self._last_by_symbol[tick.symbol] = {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": f"Skipped: {why}",
                "source": "SKIPPED",
                "skip_reason": why,
                "ts": time.time(),
            }
            self.trigger.note_price(tick.symbol, tick.price)
            return None

        try:
            result = self._call_llm(snapshot)
            result.source = "API"
            self.trigger.record_call()
            self._cache[cache_key] = (time.time(), result)
            self._last_by_symbol[tick.symbol] = {
                "action": result.action,
                "confidence": result.confidence,
                "reason": result.reason,
                "source": "API",
                "ts": time.time(),
            }
            self.trigger.note_price(tick.symbol, tick.price)
            return result.to_vote(tick.symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI call failed: %s", exc)
            self._last_by_symbol[tick.symbol] = {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": f"AI error: {exc}"[:200],
                "source": "SKIPPED",
                "skip_reason": "api_error",
                "ts": time.time(),
            }
            self.trigger.note_price(tick.symbol, tick.price)
            return None

    def _cache_key(self, snapshot: dict[str, Any]) -> str:
        # Bucket numeric fields to reuse similar states.
        payload = {
            "symbol": snapshot.get("symbol"),
            "trend": snapshot.get("short_trend"),
            "vol": snapshot.get("volume_state"),
            "hv": snapshot.get("heuristic_votes"),
            "chg5": round(float(snapshot.get("change_5m_pct") or 0), 1),
            "rsi": round(float(snapshot.get("rsi_14") or 0) / 5) * 5,
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _call_llm(self, snapshot: dict[str, Any]) -> AIResult:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)
        system = (
            "You are a paper-trading market analyst. "
            "Return ONLY valid JSON with keys action, confidence, reason. "
            "action must be BUY, SELL, or HOLD. "
            "confidence is a number 0..1. "
            "reason is one short sentence. No markdown."
        )
        user = json.dumps(snapshot, separators=(",", ":"))
        resp = client.chat.completions.create(
            model=AI_MODEL,
            temperature=0.2,
            max_tokens=120,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        data = json.loads(text)
        action = str(data.get("action", "HOLD")).upper()
        if action not in {"BUY", "SELL", "HOLD"}:
            action = "HOLD"
        conf = float(data.get("confidence", 0.0))
        conf = max(0.0, min(0.99, conf))
        reason = str(data.get("reason") or "No reason provided")[:240]
        return AIResult(action=action, confidence=conf, reason=reason, source="API")

    def log_evaluation_row(self, row: dict[str, Any]) -> None:
        try:
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write AI log: %s", exc)
