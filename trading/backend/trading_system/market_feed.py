"""Simulated market data feed (no external broker required)."""

from __future__ import annotations

import math
import random
import time
from typing import Iterable

from .models import Tick


_BASE_PRICES: dict[str, float] = {
    "BTC-USD": 64000.0,
    "ETH-USD": 3200.0,
    "SOL-USD": 145.0,
    "AAPL": 190.0,
    "NVDA": 880.0,
    "MSFT": 420.0,
    "TSLA": 250.0,
}


class MarketFeed:
    """Generates realistic-ish random-walk ticks for configured symbols."""

    def __init__(self, symbols: Iterable[str]):
        self.symbols = [s.upper() for s in symbols]
        self._prices: dict[str, float] = {
            s: float(_BASE_PRICES.get(s, 100.0 + random.random() * 50))
            for s in self.symbols
        }
        self._phase = random.random() * math.pi
        self._last_ticks: dict[str, Tick] = {}

    def snapshot(self) -> list[dict]:
        return [t.to_dict() for t in self._last_ticks.values()] or [
            Tick(symbol=s, price=p, change_pct=0.0, volume=0.0).to_dict()
            for s, p in self._prices.items()
        ]

    def next_ticks(self) -> list[Tick]:
        now = time.time()
        self._phase += 0.07
        ticks: list[Tick] = []
        for symbol in self.symbols:
            prev = self._prices[symbol]
            # Mild drift + noise + occasional jump.
            drift = math.sin(self._phase + hash(symbol) % 7) * 0.0004
            noise = random.gauss(0, 0.0018)
            jump = random.choice([0.0, 0.0, 0.0, 0.0, random.uniform(-0.012, 0.012)])
            change = drift + noise + jump
            price = max(0.01, prev * (1.0 + change))
            self._prices[symbol] = price
            volume = abs(random.gauss(1200, 400)) * (1.0 + abs(change) * 40)
            tick = Tick(
                symbol=symbol,
                price=round(price, 4 if price < 1000 else 2),
                change_pct=round(change * 100, 4),
                volume=round(volume, 2),
                ts=now,
            )
            self._last_ticks[symbol] = tick
            ticks.append(tick)
        return ticks

    def price(self, symbol: str) -> float | None:
        return self._prices.get(symbol.upper())
