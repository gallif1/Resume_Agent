"""Aggregate agent votes into actionable decisions and paper fills.

Confidence formula (unchanged — exposed in engine.confidence_debug):
  For actionable sides: final = min(0.99, winning_score / (BUY_weight + SELL_weight))
  For HOLD:             final = min(0.99, HOLD_weight / sum(all_weights))
Note: when all actionable votes agree (no opposing BUY/SELL), action_total ≈ winning_score
so confidence caps at 0.99 — this is why 2 BUY + 1 HOLD often shows 99%.
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

from .models import AgentVote, Decision, Portfolio, Position, Side


class DecisionEngine:
    def __init__(self, min_confidence: float = 0.45, ai_weight: float | None = None):
        from .config import AI_AGENT_WEIGHT

        self.min_confidence = min_confidence
        self.ai_weight = AI_AGENT_WEIGHT if ai_weight is None else ai_weight
        self._decisions: list[Decision] = []

    @property
    def recent(self) -> list[dict]:
        return [d.to_dict() for d in self._decisions[-50:]]

    def decide(self, symbol: str, votes: list[AgentVote], price: float) -> Decision | None:
        if not votes:
            return None
        weights: dict[Side, float] = {Side.BUY: 0.0, Side.SELL: 0.0, Side.HOLD: 0.0}
        contributions: list[dict[str, Any]] = []
        for vote in votes:
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

        action_total = weights[Side.BUY] + weights[Side.SELL]
        total_all = sum(weights.values()) or 1.0
        if side == Side.HOLD:
            confidence_before_cap = hold_score / total_all
            formula = "hold_score / sum(BUY+SELL+HOLD weights)"
            denom = total_all
        else:
            confidence_before_cap = score / (action_total or score or 1.0)
            formula = "winning_action_score / (BUY_weight + SELL_weight)"
            denom = action_total or score or 1.0
        confidence = min(0.99, confidence_before_cap)

        counts = Counter(v.side.value for v in votes)
        buy_n = counts.get("BUY", 0)
        sell_n = counts.get("SELL", 0)
        hold_n = counts.get("HOLD", 0)

        if side == Side.HOLD:
            why = (
                f"No actionable side cleared the threshold "
                f"(min_confidence={self.min_confidence}, hold_gate=hold_score*0.85={hold_score * 0.85:.3f})."
            )
        elif score >= hold_score * 0.85 and score >= self.min_confidence:
            why = (
                f"{side.value} score {score:.3f} exceeded min_confidence {self.min_confidence} "
                f"and beat HOLD gate {hold_score * 0.85:.3f} "
                f"(margin vs threshold {score - self.min_confidence:+.3f})."
            )
        else:
            why = f"{side.value} selected with score {score:.3f}."

        explanation = (
            f"{buy_n} BUY / {sell_n} SELL / {hold_n} HOLD votes. "
            f"Weighted BUY={weights[Side.BUY]:.3f}, SELL={weights[Side.SELL]:.3f}, "
            f"HOLD={weights[Side.HOLD]:.3f}. {why}"
        )
        rationale = (
            f"Votes {dict(counts)} → {side.value} "
            f"(action score {score:.2f}, confidence {confidence:.0%})"
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
            "ai_weight": self.ai_weight,
            "winning_action": side.value,
            "explanation": explanation,
            "confidence_debug": {
                "raw_score": round(float(score), 4),
                "action_total_buy_sell": round(action_total, 4),
                "total_all_weights": round(total_all, 4),
                "denominator": round(float(denom), 4),
                "formula": formula,
                "confidence_before_cap": round(confidence_before_cap, 6),
                "cap": 0.99,
                "final_confidence": round(confidence, 4),
                "note": (
                    "When every actionable vote agrees (no opposing BUY/SELL), "
                    "denominator ≈ raw_score so confidence caps at 0.99."
                ),
            },
        }

        decision = Decision(
            id=uuid.uuid4().hex[:12],
            symbol=symbol,
            side=side,
            confidence=round(confidence, 3),
            votes=[v.to_dict() for v in votes],
            rationale=rationale,
            engine=engine,
        )
        if side != Side.HOLD and score >= self.min_confidence:
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
