"""Turns raw ticks into higher-level market events."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Deque

from .models import MarketEvent, Tick

# Chart history kept longer than the short agent window.
CHART_HISTORY_LEN = 360


class EventEngine:
    def __init__(self, window: int = 30, chart_window: int = CHART_HISTORY_LEN):
        self._history: dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=window))
        self._chart_history: dict[str, Deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=chart_window)
        )
        self._events: Deque[MarketEvent] = deque(maxlen=200)

    @property
    def recent(self) -> list[dict]:
        return [e.to_dict() for e in list(self._events)[-40:]]

    def clear_session(self) -> None:
        """Clear detected events and short history buffers (paper session reset)."""
        self._events.clear()
        self._history.clear()
        self._chart_history.clear()

    def chart_history(self, symbol: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """Timed price points for live charts (all symbols, or one)."""
        if symbol:
            key = symbol.upper()
            return {key: list(self._chart_history.get(key, ()))}
        return {sym: list(points) for sym, points in self._chart_history.items()}

    def process(self, ticks: list[Tick]) -> list[MarketEvent]:
        emitted: list[MarketEvent] = []
        for tick in ticks:
            hist = self._history[tick.symbol]
            hist.append(tick.price)
            self._chart_history[tick.symbol].append(
                {
                    "ts": tick.ts,
                    "price": tick.price,
                    "volume": tick.volume,
                    "change_pct": tick.change_pct,
                }
            )
            if abs(tick.change_pct) >= 0.35:
                severity = "warn" if abs(tick.change_pct) < 0.8 else "critical"
                kind = "spike_up" if tick.change_pct > 0 else "spike_down"
                evt = MarketEvent.create(
                    kind=kind,
                    symbol=tick.symbol,
                    message=f"{tick.symbol} זז ב-{tick.change_pct:+.2f}% ל-{tick.price}",
                    severity=severity,
                    change_pct=tick.change_pct,
                    price=tick.price,
                )
                self._events.append(evt)
                emitted.append(evt)

            if len(hist) >= 10:
                mean = sum(hist) / len(hist)
                deviation = (tick.price - mean) / mean * 100
                if abs(deviation) >= 1.2:
                    evt = MarketEvent.create(
                        kind="mean_deviation",
                        symbol=tick.symbol,
                        message=f"{tick.symbol} במרחק {deviation:+.2f}% מהממוצע הקצר",
                        severity="info",
                        deviation_pct=round(deviation, 3),
                        mean=round(mean, 4),
                        price=tick.price,
                    )
                    self._events.append(evt)
                    emitted.append(evt)

            if tick.volume > 2500 and abs(tick.change_pct) >= 0.15:
                evt = MarketEvent.create(
                    kind="volume_surge",
                    symbol=tick.symbol,
                    message=f"זינוק נפח ב-{tick.symbol} ({tick.volume:.0f})",
                    severity="info",
                    volume=tick.volume,
                    change_pct=tick.change_pct,
                )
                self._events.append(evt)
                emitted.append(evt)

        return emitted

    def history_prices(self, symbol: str) -> list[float]:
        return list(self._history.get(symbol.upper(), ()))
