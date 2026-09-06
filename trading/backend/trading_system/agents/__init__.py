"""Trading agents that vote BUY / SELL / HOLD from market context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from statistics import mean, pstdev

from ..models import AgentVote, MarketEvent, Side, Tick


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
        if len(history) < 5:
            return AgentVote(
                self.agent_id, self.agent_name, tick.symbol, Side.HOLD, 0.3, "Warming up"
            )
        recent = history[-5:]
        slope = (recent[-1] - recent[0]) / recent[0] * 100
        if slope > 0.25:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.BUY,
                min(0.95, 0.45 + abs(slope) / 2),
                f"Upward momentum {slope:+.2f}% over last window",
            )
        if slope < -0.25:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.SELL,
                min(0.95, 0.45 + abs(slope) / 2),
                f"Downward momentum {slope:+.2f}% over last window",
            )
        return AgentVote(
            self.agent_id, self.agent_name, tick.symbol, Side.HOLD, 0.4, "No clear trend"
        )


class MeanReversionAgent(BaseAgent):
    agent_id = "mean_reversion"
    agent_name = "Mean Reversion Agent"

    def vote(self, tick: Tick, history: list[float], events: list[MarketEvent]) -> AgentVote:
        if len(history) < 8:
            return AgentVote(
                self.agent_id, self.agent_name, tick.symbol, Side.HOLD, 0.3, "Warming up"
            )
        avg = mean(history)
        deviation = (tick.price - avg) / avg * 100
        if deviation <= -0.8:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.BUY,
                min(0.9, 0.4 + abs(deviation) / 3),
                f"Price {deviation:+.2f}% below mean — fade the move",
            )
        if deviation >= 0.8:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.SELL,
                min(0.9, 0.4 + abs(deviation) / 3),
                f"Price {deviation:+.2f}% above mean — fade the move",
            )
        return AgentVote(
            self.agent_id,
            self.agent_name,
            tick.symbol,
            Side.HOLD,
            0.35,
            "Near equilibrium",
        )


class VolatilityAgent(BaseAgent):
    agent_id = "volatility"
    agent_name = "Volatility Agent"

    def vote(self, tick: Tick, history: list[float], events: list[MarketEvent]) -> AgentVote:
        spike = any(e.kind.startswith("spike") and e.symbol == tick.symbol for e in events)
        if len(history) < 6:
            return AgentVote(
                self.agent_id, self.agent_name, tick.symbol, Side.HOLD, 0.25, "Warming up"
            )
        returns = [
            (history[i] - history[i - 1]) / history[i - 1]
            for i in range(1, len(history))
        ]
        vol = pstdev(returns) * 100 if len(returns) > 1 else 0.0
        if spike and tick.change_pct > 0:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.SELL,
                0.55,
                f"Spike-up with vol={vol:.3f}% — take profit bias",
            )
        if spike and tick.change_pct < 0:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.BUY,
                0.55,
                f"Spike-down with vol={vol:.3f}% — dip-buy bias",
            )
        if vol > 0.25:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.HOLD,
                0.6,
                f"Elevated volatility ({vol:.3f}%) — stand aside",
            )
        return AgentVote(
            self.agent_id,
            self.agent_name,
            tick.symbol,
            Side.HOLD,
            0.35,
            f"Calm tape (vol={vol:.3f}%)",
        )


def default_agents() -> list[BaseAgent]:
    return [MomentumAgent(), MeanReversionAgent(), VolatilityAgent()]
