"""Trading agents that vote BUY / SELL / HOLD from market context.

Reasons are generated from the same numbers used by each rule (no invented prose).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from statistics import mean, pstdev

from ..models import AgentVote, MarketEvent, Side, Tick

# Rule thresholds (kept here so log reasons can cite the exact values).
MOMENTUM_WINDOW = 5
MOMENTUM_BUY_PCT = 0.25
MOMENTUM_SELL_PCT = -0.25
MEAN_REV_MIN_HISTORY = 8
MEAN_REV_BUY_PCT = -0.8
MEAN_REV_SELL_PCT = 0.8
VOL_MIN_HISTORY = 6
VOL_ELEVATED = 0.25


class BaseAgent(ABC):
    agent_id: str
    agent_name: str

    @abstractmethod
    def vote(
        self,
        tick: Tick,
        history: list[float],
        events: list[MarketEvent],
    ) -> AgentVote:
        raise NotImplementedError


class MomentumAgent(BaseAgent):
    agent_id = "momentum"
    agent_name = "Momentum Agent"

    def vote(self, tick: Tick, history: list[float], events: list[MarketEvent]) -> AgentVote:
        if len(history) < MOMENTUM_WINDOW:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.HOLD,
                0.3,
                f"HOLD because history has {len(history)} samples; need ≥{MOMENTUM_WINDOW}.",
                inputs={"history_len": len(history), "window": MOMENTUM_WINDOW},
            )
        recent = history[-MOMENTUM_WINDOW:]
        slope = (recent[-1] - recent[0]) / recent[0] * 100
        inputs = {
            "window": MOMENTUM_WINDOW,
            "price_change_pct": round(slope, 4),
            "buy_threshold_pct": MOMENTUM_BUY_PCT,
            "sell_threshold_pct": MOMENTUM_SELL_PCT,
            "first": recent[0],
            "last": recent[-1],
        }
        if slope > MOMENTUM_BUY_PCT:
            conf = min(0.95, 0.45 + abs(slope) / 2)
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.BUY,
                conf,
                (
                    f"BUY because price change over the last {MOMENTUM_WINDOW} samples was "
                    f"{slope:+.2f}%, above the BUY threshold of +{MOMENTUM_BUY_PCT:.2f}%."
                ),
                inputs=inputs,
            )
        if slope < MOMENTUM_SELL_PCT:
            conf = min(0.95, 0.45 + abs(slope) / 2)
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.SELL,
                conf,
                (
                    f"SELL because price change over the last {MOMENTUM_WINDOW} samples was "
                    f"{slope:+.2f}%, below the SELL threshold of {MOMENTUM_SELL_PCT:.2f}%."
                ),
                inputs=inputs,
            )
        return AgentVote(
            self.agent_id,
            self.agent_name,
            tick.symbol,
            Side.HOLD,
            0.4,
            (
                f"HOLD because price change over the last {MOMENTUM_WINDOW} samples was "
                f"{slope:+.2f}%, inside the neutral band "
                f"[{MOMENTUM_SELL_PCT:.2f}%, +{MOMENTUM_BUY_PCT:.2f}%]."
            ),
            inputs=inputs,
        )


class MeanReversionAgent(BaseAgent):
    agent_id = "mean_reversion"
    agent_name = "Mean Reversion Agent"

    def vote(self, tick: Tick, history: list[float], events: list[MarketEvent]) -> AgentVote:
        if len(history) < MEAN_REV_MIN_HISTORY:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.HOLD,
                0.3,
                f"HOLD because history has {len(history)} samples; need ≥{MEAN_REV_MIN_HISTORY}.",
                inputs={"history_len": len(history), "min_history": MEAN_REV_MIN_HISTORY},
            )
        avg = mean(history)
        deviation = (tick.price - avg) / avg * 100
        inputs = {
            "mean_price": round(avg, 6),
            "price": tick.price,
            "deviation_pct": round(deviation, 4),
            "buy_threshold_pct": MEAN_REV_BUY_PCT,
            "sell_threshold_pct": MEAN_REV_SELL_PCT,
            "history_len": len(history),
        }
        if deviation <= MEAN_REV_BUY_PCT:
            conf = min(0.9, 0.4 + abs(deviation) / 3)
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.BUY,
                conf,
                (
                    f"BUY because price is {deviation:+.2f}% below the mean "
                    f"({avg:.4f}), at/below the BUY threshold of {MEAN_REV_BUY_PCT:.2f}%."
                ),
                inputs=inputs,
            )
        if deviation >= MEAN_REV_SELL_PCT:
            conf = min(0.9, 0.4 + abs(deviation) / 3)
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.SELL,
                conf,
                (
                    f"SELL because price is {deviation:+.2f}% above the mean "
                    f"({avg:.4f}), at/above the SELL threshold of +{MEAN_REV_SELL_PCT:.2f}%."
                ),
                inputs=inputs,
            )
        return AgentVote(
            self.agent_id,
            self.agent_name,
            tick.symbol,
            Side.HOLD,
            0.35,
            (
                f"HOLD because price is only {deviation:+.2f}% from the mean "
                f"({avg:.4f}), inside the neutral band "
                f"[{MEAN_REV_BUY_PCT:.2f}%, +{MEAN_REV_SELL_PCT:.2f}%]."
            ),
            inputs=inputs,
        )


class VolatilityAgent(BaseAgent):
    agent_id = "volatility"
    agent_name = "Volatility Agent"

    def vote(self, tick: Tick, history: list[float], events: list[MarketEvent]) -> AgentVote:
        spike_events = [
            e for e in events if e.kind.startswith("spike") and e.symbol == tick.symbol
        ]
        spike = bool(spike_events)
        spike_kind = spike_events[0].kind if spike_events else None
        if len(history) < VOL_MIN_HISTORY:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.HOLD,
                0.25,
                f"HOLD because history has {len(history)} samples; need ≥{VOL_MIN_HISTORY}.",
                inputs={"history_len": len(history), "min_history": VOL_MIN_HISTORY},
            )
        returns = [
            (history[i] - history[i - 1]) / history[i - 1]
            for i in range(1, len(history))
            if history[i - 1]
        ]
        vol = pstdev(returns) * 100 if len(returns) > 1 else 0.0
        inputs = {
            "volatility_pct": round(vol, 4),
            "elevated_threshold_pct": VOL_ELEVATED,
            "tick_change_pct": round(tick.change_pct, 4),
            "spike": spike,
            "spike_kind": spike_kind,
        }
        if spike and tick.change_pct > 0:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.SELL,
                0.55,
                (
                    f"SELL because an upward spike ({spike_kind}) was detected with "
                    f"tick change {tick.change_pct:+.2f}% while realized vol={vol:.3f}%."
                ),
                inputs=inputs,
            )
        if spike and tick.change_pct < 0:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.BUY,
                0.55,
                (
                    f"BUY because a downward spike ({spike_kind}) was detected with "
                    f"tick change {tick.change_pct:+.2f}% while realized vol={vol:.3f}% "
                    f"(dip-buy bias)."
                ),
                inputs=inputs,
            )
        if vol > VOL_ELEVATED:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.HOLD,
                0.6,
                (
                    f"HOLD because realized volatility {vol:.3f}% exceeds the elevated "
                    f"threshold {VOL_ELEVATED:.2f}% — stand aside."
                ),
                inputs=inputs,
            )
        return AgentVote(
            self.agent_id,
            self.agent_name,
            tick.symbol,
            Side.HOLD,
            0.35,
            (
                f"HOLD because realized volatility {vol:.3f}% is below the elevated "
                f"threshold {VOL_ELEVATED:.2f}% and no spike event is active."
            ),
            inputs=inputs,
        )


def default_agents() -> list[BaseAgent]:
    return [MomentumAgent(), MeanReversionAgent(), VolatilityAgent()]


# Stable ids for UI coloring / logging
HEURISTIC_AGENT_IDS = ("momentum", "mean_reversion", "volatility")
AI_AGENT_ID = "ai_analyst"
