"""Aggregate agent votes into actionable decisions and paper fills."""

from __future__ import annotations

import uuid
from collections import Counter

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
        for vote in votes:
            w = self.ai_weight if vote.agent_id == "ai_analyst" else 1.0
            weights[vote.side] += vote.confidence * w
        # Prefer action over HOLD when action confidence is competitive.
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

        # Confidence for UI / threshold must NOT dilute the winning side by the
        # opposing side's weight (that previously blocked almost all SELLs at ~44%
        # whenever mean-reversion also voted BUY into a dip).
        action_total = weights[Side.BUY] + weights[Side.SELL]
        if side == Side.HOLD:
            total = sum(weights.values()) or 1.0
            confidence = min(0.99, hold_score / total)
        else:
            confidence = min(0.99, score / (action_total or score or 1.0))

        counts = Counter(v.side.value for v in votes)
        rationale = (
            f"Votes {dict(counts)} → {side.value} "
            f"(action score {score:.2f}, confidence {confidence:.0%})"
        )
        decision = Decision(
            id=uuid.uuid4().hex[:12],
            symbol=symbol,
            side=side,
            confidence=round(confidence, 3),
            votes=[v.to_dict() for v in votes],
            rationale=rationale,
        )
        # Execute on raw winning weight (same units as min_confidence), not diluted %.
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
        # Target ~2% of cash for buys; sell up to half of position.
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
            # Sell half by default; if confidence is strong (>=0.7), sell up to 75%.
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
