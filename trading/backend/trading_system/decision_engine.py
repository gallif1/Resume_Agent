"""Aggregate agent votes into actionable decisions and paper fills.

Calibrated confidence (exposed in engine.confidence_debug):

  action_support   = winning_score / total_vote_weight   # BUY+SELL+HOLD
  agreement_factor = n_agents_supporting_winner / n_agents
  opposition_ratio = opposing_BUY_or_SELL_score / total_vote_weight
  hold_ratio       = HOLD_score / total_vote_weight

  confidence = clamp(
      action_support
      * (0.75 + 0.25 * agreement_factor)
      * (1 - 0.55 * opposition_ratio)
      * (1 - 0.28 * hold_ratio),
      0.05, 0.95,
  )

HOLD disagreement and opposing BUY/SELL both reduce confidence. Near-99%
requires near-unanimous strong support with minimal HOLD/opposition.

Before aggregation, votes must share symbol / analysis timeframe /
market snapshot timestamp (when provided).
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from typing import Any

from .config import (
    ANALYSIS_TIMEFRAME,
    MIN_EXECUTION_CONFIDENCE,
    VOTE_SNAPSHOT_TOLERANCE_SEC,
)
from .models import AgentVote, Decision, Portfolio, Position, Side

logger = logging.getLogger("trading.decision")


def calibrated_confidence(
    side: Side,
    weights: dict[Side, float],
    votes: list[AgentVote],
) -> tuple[float, dict[str, Any]]:
    """Return (confidence, debug_dict) using the calibrated formula."""
    total_weight = sum(weights.values()) or 1.0
    winning_score = float(weights.get(side, 0.0))
    hold_score = float(weights.get(Side.HOLD, 0.0))
    buy_score = float(weights.get(Side.BUY, 0.0))
    sell_score = float(weights.get(Side.SELL, 0.0))

    if side == Side.BUY:
        opposing_score = sell_score
    elif side == Side.SELL:
        opposing_score = buy_score
    else:
        opposing_score = max(buy_score, sell_score)

    action_support = winning_score / total_weight
    n_agents = len(votes) or 1
    n_support = sum(1 for v in votes if v.side == side)
    n_disagree = n_agents - n_support
    agreement_factor = n_support / n_agents
    opposition_ratio = opposing_score / total_weight
    hold_ratio = hold_score / total_weight if side != Side.HOLD else 0.0

    agreement_adj = 0.75 + 0.25 * agreement_factor
    opposition_adj = 1.0 - 0.55 * opposition_ratio
    hold_adj = 1.0 - 0.28 * hold_ratio

    raw = action_support * agreement_adj * opposition_adj * hold_adj
    confidence = max(0.05, min(0.95, raw))

    formula = (
        "action_support * (0.75 + 0.25*agreement_factor) "
        "* (1 - 0.55*opposition_ratio) * (1 - 0.28*hold_ratio); "
        "clamp[0.05, 0.95]"
    )
    debug = {
        "winning_action": side.value,
        "winning_score": round(winning_score, 4),
        "total_weight": round(total_weight, 4),
        "action_support": round(action_support, 4),
        "agreement_factor": round(agreement_factor, 4),
        "n_supporting": n_support,
        "n_disagreeing": n_disagree,
        "n_agents": n_agents,
        "hold_ratio": round(hold_ratio, 4),
        "opposition_ratio": round(opposition_ratio, 4),
        "agreement_adj": round(agreement_adj, 4),
        "opposition_adj": round(opposition_adj, 4),
        "hold_adj": round(hold_adj, 4),
        "raw_before_clamp": round(raw, 6),
        "clamp_min": 0.05,
        "clamp_max": 0.95,
        "final_confidence": round(confidence, 4),
        "formula": formula,
        "raw_score": round(winning_score, 4),
        "action_total_buy_sell": round(buy_score + sell_score, 4),
        "total_all_weights": round(total_weight, 4),
        "denominator": round(total_weight, 4),
        "confidence_before_cap": round(raw, 6),
        "cap": 0.95,
        "note": (
            "HOLD והצבעות BUY/SELL מנוגדות מורידות ביטחון; "
            "0.95 שמור להסכמה חזקה כמעט פה-אחד."
        ),
    }
    return confidence, debug


def filter_compatible_votes(
    symbol: str,
    votes: list[AgentVote],
    *,
    timeframe: str | None = None,
    market_timestamp: float | None = None,
    tolerance_sec: float | None = None,
) -> tuple[list[AgentVote], list[dict[str, Any]]]:
    """Keep only votes matching symbol / TF / snapshot timestamp.

    Legacy votes with empty timeframe / missing timestamp are allowed
    (backward compatible with unit tests).
    """
    tol = VOTE_SNAPSHOT_TOLERANCE_SEC if tolerance_sec is None else tolerance_sec
    expected_tf = (timeframe or ANALYSIS_TIMEFRAME or "").strip()
    kept: list[AgentVote] = []
    skipped: list[dict[str, Any]] = []
    sym_u = symbol.upper()

    for vote in votes:
        reasons: list[str] = []
        if vote.symbol and vote.symbol.upper() != sym_u:
            reasons.append(
                f"symbol mismatch vote={vote.symbol} decision={sym_u}"
            )
        vote_tf = (vote.timeframe or "").strip()
        if expected_tf and vote_tf and vote_tf != expected_tf:
            reasons.append(
                f"timeframe mismatch vote={vote_tf} decision={expected_tf}"
            )
        if (
            market_timestamp is not None
            and vote.market_timestamp is not None
            and tol >= 0
        ):
            delta = abs(float(vote.market_timestamp) - float(market_timestamp))
            if delta > tol:
                reasons.append(
                    f"stale/mismatched snapshot delta={delta:.1f}s "
                    f"tol={tol:.1f}s"
                )
        if reasons:
            logger.info(
                "SKIPPED %s vote: %s",
                vote.agent_id,
                "; ".join(reasons),
            )
            skipped.append(
                {
                    "agent_id": vote.agent_id,
                    "symbol": vote.symbol,
                    "timeframe": vote.timeframe,
                    "market_timestamp": vote.market_timestamp,
                    "reasons": reasons,
                }
            )
            continue
        kept.append(vote)
    return kept, skipped


class DecisionEngine:
    def __init__(
        self,
        min_confidence: float = 0.45,
        ai_weight: float | None = None,
        min_execution_confidence: float | None = None,
    ):
        from .config import AI_AGENT_WEIGHT

        self.min_confidence = min_confidence
        self.ai_weight = AI_AGENT_WEIGHT if ai_weight is None else ai_weight
        self.min_execution_confidence = (
            MIN_EXECUTION_CONFIDENCE
            if min_execution_confidence is None
            else min_execution_confidence
        )
        self._decisions: list[Decision] = []

    def clear(self) -> int:
        n = len(self._decisions)
        self._decisions.clear()
        return n

    @property
    def recent(self) -> list[dict]:
        return [d.to_dict() for d in self._decisions[-50:]]

    def decide(
        self,
        symbol: str,
        votes: list[AgentVote],
        price: float,
        *,
        timeframe: str | None = None,
        market_timestamp: float | None = None,
    ) -> Decision | None:
        if not votes:
            return None

        tf = timeframe or ANALYSIS_TIMEFRAME
        compatible, skipped = filter_compatible_votes(
            symbol,
            votes,
            timeframe=tf,
            market_timestamp=market_timestamp,
        )
        if not compatible:
            logger.warning(
                "No compatible votes for %s tf=%s (skipped=%d)",
                symbol,
                tf,
                len(skipped),
            )
            return None

        weights: dict[Side, float] = {Side.BUY: 0.0, Side.SELL: 0.0, Side.HOLD: 0.0}
        contributions: list[dict[str, Any]] = []
        for vote in compatible:
            w = self.ai_weight if vote.agent_id == "ai_analyst" else 1.0
            weighted = vote.confidence * w
            weights[vote.side] += weighted
            contributions.append(
                {
                    "agent_id": vote.agent_id,
                    "agent_name": vote.agent_name,
                    "side": vote.side.value,
                    "confidence": round(vote.confidence, 4),
                    "weight_multiplier": w,
                    "weighted_contribution": round(weighted, 4),
                    "score": vote.score,
                    "timeframe": vote.timeframe,
                    "market_timestamp": vote.market_timestamp,
                }
            )

        actionable = sorted(
            ((side, score) for side, score in weights.items() if side != Side.HOLD),
            key=lambda x: x[1],
            reverse=True,
        )
        hold_score = weights[Side.HOLD]
        if actionable and actionable[0][1] >= self.min_confidence and actionable[0][1] >= hold_score * 0.85:
            side, score = actionable[0]
        else:
            side, score = Side.HOLD, hold_score

        confidence, conf_debug = calibrated_confidence(side, weights, compatible)

        counts = Counter(v.side.value for v in compatible)
        buy_n = counts.get("BUY", 0)
        sell_n = counts.get("SELL", 0)
        hold_n = counts.get("HOLD", 0)

        if side == Side.HOLD:
            why = (
                f"אף צד לא עבר את הסף "
                f"(min_confidence={self.min_confidence}, שערי HOLD=hold_score*0.85={hold_score * 0.85:.3f})."
            )
        elif score >= hold_score * 0.85 and score >= self.min_confidence:
            why = (
                f"ציון {side.value} {score:.3f} עבר את min_confidence {self.min_confidence} "
                f"וניצח את שערי HOLD {hold_score * 0.85:.3f} "
                f"(מרווח מול הסף {score - self.min_confidence:+.3f})."
            )
        else:
            why = f"{side.value} נבחר עם ציון {score:.3f}."

        explanation = (
            f"{buy_n} קנייה / {sell_n} מכירה / {hold_n} המתנה. "
            f"משקלים: BUY={weights[Side.BUY]:.3f}, SELL={weights[Side.SELL]:.3f}, "
            f"HOLD={weights[Side.HOLD]:.3f}. {why} "
            f"ביטחון מכויל {confidence:.0%} "
            f"(תמיכה={conf_debug['action_support']:.2f}, "
            f"הסכמה={conf_debug['agreement_factor']:.2f}, "
            f"קנס_HOLD={conf_debug['hold_ratio']:.2f}, "
            f"קנס_נגד={conf_debug['opposition_ratio']:.2f})."
        )
        rationale = (
            f"הצבעות {dict(counts)} → {side.value} "
            f"(ציון פעולה {score:.2f}, ביטחון {confidence:.0%})"
        )

        execution_gate: dict[str, Any] | None = None
        can_execute = side != Side.HOLD and score >= self.min_confidence
        if can_execute and confidence < self.min_execution_confidence:
            execution_gate = {
                "code": "min_execution_confidence",
                "reason": (
                    f"ביטחון מכויל {confidence:.0%} מתחת לסף ביצוע "
                    f"{self.min_execution_confidence:.0%} — BLOCKED"
                ),
                "calibrated_confidence": round(confidence, 4),
                "min_execution_confidence": self.min_execution_confidence,
                "analytical_side": side.value,
            }
            can_execute = False
            rationale = f"{rationale} · {execution_gate['reason']}"
            logger.info(
                "EXECUTION GATE %s %s conf=%.3f < %.3f",
                symbol,
                side.value,
                confidence,
                self.min_execution_confidence,
            )

        engine = {
            "vote_counts": {"BUY": buy_n, "SELL": sell_n, "HOLD": hold_n},
            "weighted_contributions": contributions,
            "weights": {
                "BUY": round(weights[Side.BUY], 4),
                "SELL": round(weights[Side.SELL], 4),
                "HOLD": round(weights[Side.HOLD], 4),
            },
            "action_score": round(float(score), 4),
            "hold_score": round(hold_score, 4),
            "hold_gate": round(hold_score * 0.85, 4),
            "threshold": self.min_confidence,
            "min_execution_confidence": self.min_execution_confidence,
            "ai_weight": self.ai_weight,
            "winning_action": side.value,
            "explanation": explanation,
            "confidence_debug": conf_debug,
            "skipped_votes": skipped,
            "analysis_timeframe": tf,
            "market_timestamp": market_timestamp,
            "execution_gate": execution_gate,
        }

        decision = Decision(
            id=uuid.uuid4().hex[:12],
            symbol=symbol,
            side=side,
            confidence=round(confidence, 3),
            votes=[v.to_dict() for v in compatible],
            rationale=rationale,
            engine=engine,
            timeframe=tf,
            market_timestamp=market_timestamp,
            analytical_side=side.value,
        )
        if can_execute:
            decision.executed = True
            decision.fill_price = price
            decision.quantity = 0.0
        self._decisions.append(decision)
        return decision

    def apply_fill(self, portfolio: Portfolio, decision: Decision) -> Portfolio:
        if not decision.executed or decision.side == Side.HOLD or decision.fill_price is None:
            return portfolio
        price = decision.fill_price
        if decision.side == Side.BUY:
            notional = portfolio.cash * 0.02
            if notional < 1 or price <= 0:
                decision.executed = False
                return portfolio
            qty = notional / price
            portfolio.cash -= qty * price
            pos = portfolio.positions.get(decision.symbol)
            if pos is None:
                portfolio.positions[decision.symbol] = Position(decision.symbol, qty, price)
            else:
                total_qty = pos.quantity + qty
                pos.avg_price = (pos.avg_price * pos.quantity + price * qty) / total_qty
                pos.quantity = total_qty
            decision.quantity = round(qty, 6)
        else:
            pos = portfolio.positions.get(decision.symbol)
            if pos is None or pos.quantity <= 0:
                decision.executed = False
                return portfolio
            frac = 0.75 if decision.confidence >= 0.7 else 0.5
            qty = pos.quantity * frac
            proceeds = qty * price
            portfolio.cash += proceeds
            pnl = (price - pos.avg_price) * qty
            portfolio.realized_pnl += pnl
            pos.quantity -= qty
            if pos.quantity < 1e-8:
                del portfolio.positions[decision.symbol]
            decision.quantity = round(qty, 6)
        return portfolio
