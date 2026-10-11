"""Deterministic Risk Engine — final veto before paper broker fills.

Runs AFTER DecisionEngine and BEFORE paper execution.
Does NOT use an LLM. AI cannot override a rejected risk check.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from . import asset_config as asset_config_mod
from .config import (
    ANALYSIS_TIMEFRAME,
    FILL_COOLDOWN_SEC,
    RISK_ATR_STOP_MULT,
    RISK_DEFAULT_ALLOCATION_PCT,
    RISK_MIN_NOTIONAL,
    RISK_REWARD_RATIO,
)
from .models import Decision, Portfolio, Side

logger = logging.getLogger("trading.risk")


@dataclass
class RiskVerdict:
    approved: bool
    status: str  # APPROVED | BLOCKED | SKIPPED
    reason: str
    reason_code: str | None = None
    allocation_pct: float | None = None
    suggested_quantity: float | None = None
    atr: float | None = None
    atr_pct: float | None = None
    suggested_stop: float | None = None
    suggested_stop_distance: float | None = None
    suggested_take_profit: float | None = None
    risk_reward: float | None = None
    atr_risk_level: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RiskEngine:
    """Deterministic pre-execution risk checks + ATR-aware risk levels."""

    def __init__(
        self,
        *,
        allocation_pct: float | None = None,
        atr_stop_mult: float | None = None,
        reward_ratio: float | None = None,
        fill_cooldown_sec: float | None = None,
    ) -> None:
        self.allocation_pct = (
            RISK_DEFAULT_ALLOCATION_PCT if allocation_pct is None else allocation_pct
        )
        self.atr_stop_mult = RISK_ATR_STOP_MULT if atr_stop_mult is None else atr_stop_mult
        self.reward_ratio = RISK_REWARD_RATIO if reward_ratio is None else reward_ratio
        self.fill_cooldown_sec = (
            FILL_COOLDOWN_SEC if fill_cooldown_sec is None else fill_cooldown_sec
        )

    def evaluate(
        self,
        *,
        decision: Decision,
        portfolio: Portfolio,
        price: float,
        symbol: str,
        features: dict[str, Any] | None = None,
        last_fill_ts: float | None = None,
        analysis_timeframe: str | None = None,
    ) -> RiskVerdict:
        features = features or {}
        tf = analysis_timeframe or decision.timeframe or ANALYSIS_TIMEFRAME
        atr = _f(features.get("atr_14"))
        atr_pct = _f(features.get("atr_pct"))
        if atr_pct is None and atr is not None and price > 0:
            atr_pct = atr / price * 100.0

        levels = self._atr_levels(price, atr, decision.side)
        atr_risk = self._atr_risk_level(atr_pct)

        base_kw: dict[str, Any] = {
            "allocation_pct": self.allocation_pct,
            "atr": atr,
            "atr_pct": atr_pct,
            "atr_risk_level": atr_risk,
            **levels,
        }

        # Non-actionable: DecisionEngine may keep analytical BUY/SELL with
        # executed=False (confidence gate). Still report risk context.
        if decision.side == Side.HOLD:
            return RiskVerdict(
                approved=False,
                status="SKIPPED",
                reason="אין פעולה לביצוע (HOLD)",
                reason_code="hold",
                details={"timeframe": tf},
                **base_kw,
            )

        if not decision.executed:
            gate = (decision.engine or {}).get("execution_gate") or {}
            return RiskVerdict(
                approved=False,
                status="BLOCKED",
                reason=str(gate.get("reason") or "החלטה חסומה לפני מנוע הסיכון"),
                reason_code=str(gate.get("code") or "pre_risk_gate"),
                details={"timeframe": tf, "stage": "pre_risk"},
                **base_kw,
            )

        # Cooldown
        if last_fill_ts:
            elapsed = time.time() - float(last_fill_ts)
            if elapsed < self.fill_cooldown_sec:
                rem = self.fill_cooldown_sec - elapsed
                return RiskVerdict(
                    approved=False,
                    status="BLOCKED",
                    reason=f"המתנה פעילה; נותרו {rem:.0f}ש׳",
                    reason_code="cooldown",
                    details={"cooldown_remaining_sec": rem, "timeframe": tf},
                    **base_kw,
                )

        qty = self._estimate_qty(decision, portfolio, price)
        if decision.side == Side.BUY:
            notional = (qty or 0) * price
            if portfolio.cash < RISK_MIN_NOTIONAL or notional < RISK_MIN_NOTIONAL:
                return RiskVerdict(
                    approved=False,
                    status="BLOCKED",
                    reason="אין מספיק מזומן למינימום קנייה",
                    reason_code="insufficient_cash",
                    suggested_quantity=qty,
                    details={"cash": portfolio.cash, "timeframe": tf},
                    **base_kw,
                )
        elif decision.side == Side.SELL:
            pos = portfolio.positions.get(symbol)
            if pos is None or pos.quantity <= 0:
                return RiskVerdict(
                    approved=False,
                    status="BLOCKED",
                    reason="אין פוזיציה פתוחה למכירה",
                    reason_code="no_position",
                    details={"timeframe": tf},
                    **base_kw,
                )
            qty = pos.quantity * (0.75 if decision.confidence >= 0.7 else 0.5)

        ok, deny_he = asset_config_mod.can_open_order(
            symbol,
            decision.side.value,
            qty,
            price,
            decision.confidence,
            portfolio,
            timeframe=tf,
        )
        if not ok:
            code = "asset_mode"
            if "מעקב בלבד" in deny_he:
                code = "monitor_only"
            elif "חשיפת" in deny_he:
                code = "exposure_limit"
            elif "מזומן" in deny_he:
                code = "insufficient_cash"
            elif "פוזיציות" in deny_he:
                code = "max_positions"
            elif "הפסד יומי" in deny_he:
                code = "daily_loss"
            logger.info("RISK BLOCKED %s %s: %s", symbol, decision.side.value, deny_he)
            return RiskVerdict(
                approved=False,
                status="BLOCKED",
                reason=deny_he,
                reason_code=code,
                suggested_quantity=qty,
                details={"timeframe": tf},
                **base_kw,
            )

        logger.info(
            "RISK APPROVED %s %s alloc=%.1f%% atr_risk=%s",
            symbol,
            decision.side.value,
            self.allocation_pct * 100,
            atr_risk,
        )
        return RiskVerdict(
            approved=True,
            status="APPROVED",
            reason="אושר על ידי מנוע סיכון דטרמיניסטי",
            reason_code="approved",
            suggested_quantity=qty,
            details={"timeframe": tf},
            **base_kw,
        )

    def _estimate_qty(
        self, decision: Decision, portfolio: Portfolio, price: float
    ) -> float | None:
        if price <= 0:
            return None
        if decision.side == Side.BUY:
            notional = portfolio.cash * self.allocation_pct
            if notional < RISK_MIN_NOTIONAL:
                return 0.0
            return notional / price
        return None

    def _atr_levels(
        self, price: float, atr: float | None, side: Side
    ) -> dict[str, Any]:
        if atr is None or atr <= 0 or price <= 0 or side == Side.HOLD:
            return {
                "suggested_stop": None,
                "suggested_stop_distance": None,
                "suggested_take_profit": None,
                "risk_reward": None,
            }
        stop_dist = atr * self.atr_stop_mult
        tp_dist = stop_dist * self.reward_ratio
        if side == Side.BUY:
            stop = price - stop_dist
            tp = price + tp_dist
        else:
            stop = price + stop_dist
            tp = price - tp_dist
        return {
            "suggested_stop": round(stop, 6),
            "suggested_stop_distance": round(stop_dist, 6),
            "suggested_take_profit": round(tp, 6),
            "risk_reward": self.reward_ratio,
        }

    @staticmethod
    def _atr_risk_level(atr_pct: float | None) -> str | None:
        if atr_pct is None:
            return None
        if atr_pct >= 3.0:
            return "EXTREME"
        if atr_pct >= 1.5:
            return "HIGH"
        if atr_pct >= 0.35:
            return "NORMAL"
        return "LOW"


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
