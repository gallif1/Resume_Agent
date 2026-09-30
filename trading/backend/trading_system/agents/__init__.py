"""Trading agents that vote BUY / SELL / HOLD from a shared FeatureSnapshot.

Technical indicators are REAL INPUTS to scoring — not post-hoc decoration.
All agents in one decision cycle must receive the same FeatureSnapshot
(symbol + timeframe + candle_timestamp).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..config import (
    ANALYSIS_TIMEFRAME,
    HEURISTIC_CONFIDENCE_CAP,
    MEANREV_BUY_THRESHOLD,
    MEANREV_SELL_THRESHOLD,
    MEANREV_SMA_DEV_SCALE_PCT,
    MEANREV_TREND_DAMPEN,
    MEANREV_VWAP_DEV_SCALE_PCT,
    MEANREV_W_BB,
    MEANREV_W_RSI,
    MEANREV_W_SMA20,
    MEANREV_W_VWAP,
    MOMENTUM_BUY_THRESHOLD,
    MOMENTUM_PRICE_SCALE_PCT,
    MOMENTUM_SELL_THRESHOLD,
    MOMENTUM_W_EMA,
    MOMENTUM_W_MACD,
    MOMENTUM_W_PRICE,
    MOMENTUM_W_RSI,
    MOMENTUM_W_VOLUME,
    VOL_ATR_EXTREME,
    VOL_ATR_HIGH,
    VOL_ATR_LOW,
    VOL_BB_WIDTH_EXTREME,
    VOL_BB_WIDTH_HIGH,
)
from ..indicators.features import FeatureSnapshot, build_feature_snapshot
from ..market_data.models import Candle
from ..models import AgentVote, MarketEvent, Side, Tick

# Backward-compatible aliases used by older tests / logs
MOMENTUM_WINDOW = 5
MOMENTUM_BUY_PCT = 0.25
MOMENTUM_SELL_PCT = -0.25
MEAN_REV_MIN_HISTORY = 8
MEAN_REV_BUY_PCT = -0.8
MEAN_REV_SELL_PCT = 0.8
VOL_MIN_HISTORY = 6
VOL_ELEVATED = 0.25


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _features_from_history(
    tick: Tick,
    history: list[float],
    timeframe: str = ANALYSIS_TIMEFRAME,
) -> FeatureSnapshot:
    """Synthesize a FeatureSnapshot from close history (tests / sim fallback)."""
    candles = [
        Candle(
            ts=float(i + 1),
            open=p,
            high=p * 1.001,
            low=p * 0.999,
            close=p,
            volume=1000.0 + (i % 5) * 100,
        )
        for i, p in enumerate(history)
    ]
    return build_feature_snapshot(
        symbol=tick.symbol,
        timeframe=timeframe,
        candles=candles,
        price=tick.price,
        momentum_window=MOMENTUM_WINDOW,
    )


def _agreement_boost(components: dict[str, float]) -> float:
    vals = [v for v in components.values() if v is not None]
    if not vals:
        return 0.0
    signs = [1 if v > 0.05 else (-1 if v < -0.05 else 0) for v in vals]
    nonzero = [s for s in signs if s != 0]
    if len(nonzero) < 2:
        return 0.0
    if all(s == nonzero[0] for s in nonzero):
        return 0.12
    if sum(1 for s in nonzero if s == nonzero[0]) >= len(nonzero) - 1:
        return 0.05
    return -0.08


def _score_to_side(score: float, buy_th: float, sell_th: float) -> Side:
    if score >= buy_th:
        return Side.BUY
    if score <= sell_th:
        return Side.SELL
    return Side.HOLD


def _confidence_from_score(
    score: float,
    components: dict[str, float],
    *,
    data_quality: dict[str, Any] | None = None,
    base: float = 0.42,
) -> float:
    abs_s = abs(score)
    conf = base + abs_s * 0.45 + _agreement_boost(components)
    dq = data_quality or {}
    if dq.get("stale"):
        conf *= 0.7
    missing = dq.get("missing_fields") or []
    if missing:
        conf *= max(0.55, 1.0 - 0.05 * len(missing))
    snap_c = dq.get("snapshot_confidence")
    if isinstance(snap_c, (int, float)) and snap_c < 0.7:
        conf *= 0.85 + 0.15 * float(snap_c)
    return round(min(HEURISTIC_CONFIDENCE_CAP, max(0.15, conf)), 4)


class BaseAgent(ABC):
    agent_id: str
    agent_name: str

    @abstractmethod
    def vote(
        self,
        tick: Tick,
        history: list[float],
        events: list[MarketEvent],
        features: FeatureSnapshot | None = None,
    ) -> AgentVote:
        raise NotImplementedError

    def _ctx(
        self,
        features: FeatureSnapshot,
        *,
        components: dict[str, float],
        used: dict[str, Any],
        informational: dict[str, Any] | None = None,
        score: float | None = None,
        regime: str | None = None,
    ) -> dict[str, Any]:
        return {
            "timeframe": features.timeframe,
            "market_timestamp": features.candle_timestamp,
            "score": score,
            "components": components,
            "used_for_decision": used,
            "informational": informational or {},
            "volatility_regime": regime,
        }


class MomentumAgent(BaseAgent):
    agent_id = "momentum"
    agent_name = "סוכן מומנטום"

    def vote(
        self,
        tick: Tick,
        history: list[float],
        events: list[MarketEvent],
        features: FeatureSnapshot | None = None,
    ) -> AgentVote:
        feat = features or _features_from_history(tick, history)
        if feat.candle_count < MOMENTUM_WINDOW and len(history) < MOMENTUM_WINDOW:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.HOLD,
                0.3,
                f"HOLD כי אין מספיק היסטוריה (נדרשות ≥{MOMENTUM_WINDOW}).",
                timeframe=feat.timeframe,
                market_timestamp=feat.candle_timestamp,
                score=0.0,
                components={},
                used_for_decision={"history_len": len(history)},
                inputs={"history_len": len(history), "window": MOMENTUM_WINDOW},
            )

        comps: dict[str, float] = {}
        used: dict[str, Any] = {}
        info: dict[str, Any] = {}
        reasons: list[str] = []

        # 1. Price momentum
        slope = feat.price_slope_pct
        if slope is None and len(history) >= MOMENTUM_WINDOW and history[-MOMENTUM_WINDOW]:
            slope = (history[-1] - history[-MOMENTUM_WINDOW]) / history[-MOMENTUM_WINDOW] * 100
        if slope is not None:
            price_c = _clamp(slope / MOMENTUM_PRICE_SCALE_PCT)
            comps["price_momentum"] = price_c
            used["price_slope_pct"] = round(slope, 4)
            if abs(price_c) >= 0.15:
                reasons.append(
                    f"מומנטום מחיר: {'שורי' if price_c > 0 else 'דובי'} ({slope:+.2f}%)"
                )

        # 2. EMA trend
        if feat.ema_20 is not None and feat.ema_50 is not None:
            if feat.ema_20 > feat.ema_50:
                ema_c = 1.0
            elif feat.ema_20 < feat.ema_50:
                ema_c = -1.0
            else:
                ema_c = 0.0
            # Optional EMA200 context (not required)
            if feat.ema_200 is not None and feat.current_price:
                if feat.current_price > feat.ema_200 and ema_c > 0:
                    ema_c = min(1.0, ema_c + 0.15)
                elif feat.current_price < feat.ema_200 and ema_c < 0:
                    ema_c = max(-1.0, ema_c - 0.15)
                elif feat.current_price > feat.ema_200 and ema_c < 0:
                    ema_c *= 0.7
                elif feat.current_price < feat.ema_200 and ema_c > 0:
                    ema_c *= 0.7
                used["ema_200"] = feat.ema_200
                used["price_vs_ema200"] = (
                    "above" if feat.current_price > feat.ema_200 else "below"
                )
            comps["ema_trend"] = _clamp(ema_c)
            used["ema_20"] = feat.ema_20
            used["ema_50"] = feat.ema_50
            if ema_c != 0:
                reasons.append(
                    "EMA20 > EMA50" if ema_c > 0 else "EMA20 < EMA50"
                )
        else:
            info["ema_trend"] = "unavailable"

        # 3. RSI confirmation (NOT automatic trade)
        if feat.rsi_14 is not None:
            rsi = feat.rsi_14
            if 50 <= rsi <= 70:
                rsi_c = 0.55 + (rsi - 50) / 40  # ~0.55..1.05 → clamp
            elif 30 <= rsi < 50:
                rsi_c = -0.55 - (50 - rsi) / 40
            elif rsi > 70:
                rsi_c = 0.85  # strong but extended
                info["rsi_extended"] = "overbought"
            else:  # < 30
                rsi_c = -0.85
                info["rsi_extended"] = "oversold"
            comps["rsi"] = _clamp(rsi_c)
            used["rsi_14"] = rsi
            reasons.append(f"RSI {rsi:.1f} תומך במומנטום")
        else:
            info["rsi"] = "unavailable"

        # 4. MACD
        if feat.macd is not None and feat.macd_signal is not None:
            diff = feat.macd - feat.macd_signal
            # Scale histogram if available
            macd_c = 1.0 if diff > 0 else (-1.0 if diff < 0 else 0.0)
            if feat.macd_hist is not None:
                # Soften by hist magnitude relative to price
                hist_scale = abs(feat.macd_hist) / max(feat.current_price * 0.0005, 1e-9)
                macd_c = _clamp(macd_c * min(1.0, 0.4 + hist_scale))
                used["macd_hist"] = feat.macd_hist
            comps["macd"] = macd_c
            used["macd"] = feat.macd
            used["macd_signal"] = feat.macd_signal
            if macd_c != 0:
                reasons.append(
                    "MACD מעל האות" if macd_c > 0 else "MACD מתחת לאות"
                )
        else:
            info["macd"] = "unavailable"

        # 5. Volume confirmation
        if feat.relative_volume is not None:
            rv = feat.relative_volume
            if rv >= 1.6:
                vol_c = 0.8
            elif rv >= 1.2:
                vol_c = 0.4
            elif rv >= 0.85:
                vol_c = 0.1
            else:
                vol_c = -0.35  # weak volume reduces conviction
            # Volume aligns with price direction
            if "price_momentum" in comps and comps["price_momentum"] < 0:
                vol_c = -vol_c if rv >= 1.2 else vol_c
            comps["volume"] = _clamp(vol_c)
            used["relative_volume"] = rv
            used["volume_state"] = feat.volume_state
            if rv >= 1.2:
                reasons.append(f"נפח {rv:.2f}x ממוצע — אישור")
            elif rv < 0.85:
                reasons.append(f"נפח חלש {rv:.2f}x — מוריד ביטחון")
        else:
            info["volume"] = "unavailable"

        # Weighted score — renormalize over available components
        weight_map = {
            "price_momentum": MOMENTUM_W_PRICE,
            "ema_trend": MOMENTUM_W_EMA,
            "macd": MOMENTUM_W_MACD,
            "rsi": MOMENTUM_W_RSI,
            "volume": MOMENTUM_W_VOLUME,
        }
        avail = {k: comps[k] for k in weight_map if k in comps}
        wsum = sum(weight_map[k] for k in avail) or 1.0
        score = sum(comps[k] * (weight_map[k] / wsum) for k in avail)
        score = _clamp(score)

        side = _score_to_side(score, MOMENTUM_BUY_THRESHOLD, MOMENTUM_SELL_THRESHOLD)
        conf = _confidence_from_score(score, comps, data_quality=feat.data_quality)
        if side == Side.HOLD:
            conf = min(conf, 0.55)

        if side == Side.BUY:
            head = f"BUY — ביטחון {conf:.0%}"
        elif side == Side.SELL:
            head = f"SELL — ביטחון {conf:.0%}"
        else:
            head = f"HOLD — ציון {score:+.2f} בתוך סף ניטרלי"

        rationale = head
        if reasons:
            rationale += " · " + " · ".join(reasons[:5])

        return AgentVote(
            self.agent_id,
            self.agent_name,
            tick.symbol,
            side,
            conf,
            rationale[:400],
            timeframe=feat.timeframe,
            market_timestamp=feat.candle_timestamp,
            score=round(score, 4),
            components={k: round(v, 4) for k, v in comps.items()},
            used_for_decision=used,
            informational=info,
            inputs={
                "score": round(score, 4),
                "weights": {k: weight_map[k] for k in avail},
                "thresholds": {
                    "buy": MOMENTUM_BUY_THRESHOLD,
                    "sell": MOMENTUM_SELL_THRESHOLD,
                },
                "USED_FOR_DECISION": used,
                "INFORMATIONAL_ONLY": info,
            },
        )


class MeanReversionAgent(BaseAgent):
    agent_id = "mean_reversion"
    agent_name = "סוכן חזרה לממוצע"

    def vote(
        self,
        tick: Tick,
        history: list[float],
        events: list[MarketEvent],
        features: FeatureSnapshot | None = None,
    ) -> AgentVote:
        feat = features or _features_from_history(tick, history)
        if feat.sma_20 is None and len(history) < MEAN_REV_MIN_HISTORY:
            return AgentVote(
                self.agent_id,
                self.agent_name,
                tick.symbol,
                Side.HOLD,
                0.3,
                f"HOLD כי אין SMA20 / היסטוריה מספקת (נדרשות ≥{MEAN_REV_MIN_HISTORY}).",
                timeframe=feat.timeframe,
                market_timestamp=feat.candle_timestamp,
                score=0.0,
                inputs={"history_len": len(history)},
            )

        comps: dict[str, float] = {}
        used: dict[str, Any] = {}
        info: dict[str, Any] = {}
        reasons: list[str] = []
        px = feat.current_price or tick.price

        # 1. Distance from SMA20 (positive = price above → SELL reversion evidence)
        if feat.sma_20 and feat.sma_20 > 0:
            dev = (px - feat.sma_20) / feat.sma_20 * 100.0
            # Mean-reversion: above SMA → negative score (favor SELL)
            sma_c = _clamp(-dev / MEANREV_SMA_DEV_SCALE_PCT)
            comps["sma20_deviation"] = sma_c
            used["sma_20"] = feat.sma_20
            used["sma20_dev_pct"] = round(dev, 4)
            if abs(dev) >= 0.4:
                reasons.append(f"מרחק מ-SMA20: {dev:+.2f}%")
            if feat.sma_50 is not None:
                info["sma_50"] = feat.sma_50
        else:
            info["sma_20"] = "unavailable"

        # 2. Bollinger position (0=lower, 1=upper). Low → BUY, high → SELL
        if feat.bb_position is not None:
            bp = feat.bb_position
            # Map: 0 → +1 (buy), 0.5 → 0, 1 → -1 (sell)
            bb_c = _clamp((0.5 - bp) * 2.0)
            # Soften near middle — require meaningful extension
            if 0.25 < bp < 0.75:
                bb_c *= 0.35
            comps["bollinger_position"] = bb_c
            used["bb_position"] = bp
            used["bb_upper"] = feat.bb_upper
            used["bb_lower"] = feat.bb_lower
            if bp <= 0.15:
                reasons.append(f"בולינגר נמוך ({bp:.2f}) — הארכה מטה")
            elif bp >= 0.85:
                reasons.append(f"בולינגר גבוה ({bp:.2f}) — הארכה מעלה")
        else:
            info["bollinger"] = "unavailable"

        # 3. RSI confirmation of extension
        if feat.rsi_14 is not None:
            rsi = feat.rsi_14
            if rsi <= 30:
                rsi_c = 0.9
            elif rsi <= 40:
                rsi_c = 0.45
            elif rsi >= 70:
                rsi_c = -0.9
            elif rsi >= 60:
                rsi_c = -0.45
            else:
                rsi_c = (50 - rsi) / 40.0  # mild
            comps["rsi"] = _clamp(rsi_c)
            used["rsi_14"] = rsi
            if rsi <= 40 or rsi >= 60:
                reasons.append(f"RSI {rsi:.1f} מאשר הארכה")
        else:
            info["rsi"] = "unavailable"

        # 4. VWAP distance
        if feat.vwap and feat.vwap > 0:
            vdev = (px - feat.vwap) / feat.vwap * 100.0
            vwap_c = _clamp(-vdev / MEANREV_VWAP_DEV_SCALE_PCT)
            comps["vwap_deviation"] = vwap_c
            used["vwap"] = feat.vwap
            used["vwap_dev_pct"] = round(vdev, 4)
            if abs(vdev) >= 0.35:
                reasons.append(f"מרחק מ-VWAP: {vdev:+.2f}%")
        else:
            info["vwap"] = "unavailable"

        weight_map = {
            "sma20_deviation": MEANREV_W_SMA20,
            "bollinger_position": MEANREV_W_BB,
            "rsi": MEANREV_W_RSI,
            "vwap_deviation": MEANREV_W_VWAP,
        }
        avail = {k: comps[k] for k in weight_map if k in comps}
        wsum = sum(weight_map[k] for k in avail) or 1.0
        score = sum(comps[k] * (weight_map[k] / wsum) for k in avail)

        # 5. Trend filter — dampen counter-trend mean-reversion
        trend_adj = 1.0
        bullish = False
        bearish = False
        if feat.ema_20 is not None and feat.ema_50 is not None:
            bullish = feat.ema_20 > feat.ema_50
            bearish = feat.ema_20 < feat.ema_50
            used["trend_ema20_vs_50"] = "bullish" if bullish else ("bearish" if bearish else "flat")
        elif feat.short_trend == "UP":
            bullish = True
        elif feat.short_trend == "DOWN":
            bearish = True

        if bullish and score < 0:
            # Strong uptrend — reduce SELL mean-reversion
            score *= MEANREV_TREND_DAMPEN
            trend_adj = MEANREV_TREND_DAMPEN
            reasons.append("מגמה שורית — מוחלש אות SELL לחזרה לממוצע")
        elif bearish and score > 0:
            score *= MEANREV_TREND_DAMPEN
            trend_adj = MEANREV_TREND_DAMPEN
            reasons.append("מגמה דובית — מוחלש אות BUY לחזרה לממוצע")

        score = _clamp(score)
        side = _score_to_side(score, MEANREV_BUY_THRESHOLD, MEANREV_SELL_THRESHOLD)
        conf = _confidence_from_score(score, comps, data_quality=feat.data_quality)
        if trend_adj < 1.0:
            conf *= 0.85
        if side == Side.HOLD:
            conf = min(conf, 0.55)

        if side == Side.BUY:
            head = f"BUY — ביטחון {conf:.0%}"
        elif side == Side.SELL:
            head = f"SELL — ביטחון {conf:.0%}"
        else:
            head = f"HOLD — סטייה קטנה מדי (ציון {score:+.2f})"

        rationale = head
        if reasons:
            rationale += " · " + " · ".join(reasons[:5])

        return AgentVote(
            self.agent_id,
            self.agent_name,
            tick.symbol,
            side,
            round(min(HEURISTIC_CONFIDENCE_CAP, conf), 4),
            rationale[:400],
            timeframe=feat.timeframe,
            market_timestamp=feat.candle_timestamp,
            score=round(score, 4),
            components={k: round(v, 4) for k, v in comps.items()},
            used_for_decision=used,
            informational=info,
            inputs={
                "score": round(score, 4),
                "weights": {k: weight_map[k] for k in avail},
                "trend_adjustment": trend_adj,
                "USED_FOR_DECISION": used,
                "INFORMATIONAL_ONLY": info,
            },
        )


class VolatilityAgent(BaseAgent):
    """Market-condition / risk agent — not automatically directional."""

    agent_id = "volatility"
    agent_name = "סוכן תנודתיות"

    def vote(
        self,
        tick: Tick,
        history: list[float],
        events: list[MarketEvent],
        features: FeatureSnapshot | None = None,
    ) -> AgentVote:
        feat = features or _features_from_history(tick, history)
        regime = self._regime(feat)
        used: dict[str, Any] = {
            "atr_14": feat.atr_14,
            "atr_pct": feat.atr_pct,
            "bb_width": feat.bb_width,
            "realized_volatility": feat.realized_volatility,
            "relative_volume": feat.relative_volume,
            "volume_spike": feat.volume_spike,
        }
        info: dict[str, Any] = {}

        spike_events = [
            e for e in events if e.kind.startswith("spike") and e.symbol == tick.symbol
        ]
        if spike_events:
            info["price_spike"] = {
                "kind": spike_events[0].kind,
                "note": "הקפיצה נרשמה כהקשר בלבד — לא מפעילה BUY/SELL אוטומטי",
            }

        # Directional evidence only when volatility is elevated AND trend aligns.
        comps: dict[str, float] = {}
        score = 0.0
        side = Side.HOLD
        reasons = [
            f"משטר תנודתיות: {regime}",
        ]
        if feat.atr_pct is not None:
            reasons.append(f"ATR: {feat.atr_pct:.2f}%")
        if feat.bb_width is not None:
            reasons.append(f"BB Width: {feat.bb_width:.2f}%")
        if feat.realized_volatility is not None:
            reasons.append(f"תנודתיות ממומשת: {feat.realized_volatility:.3f}")
        if feat.relative_volume is not None:
            reasons.append(f"נפח: {feat.relative_volume:.2f}x ממוצע")

        # Only allow weak directional tilt when regime is HIGH/EXTREME AND
        # short trend + EMA agree — never from spike alone.
        directional_ok = False
        if regime in {"HIGH", "EXTREME"}:
            trend_dir = 0
            if feat.ema_20 is not None and feat.ema_50 is not None:
                if feat.ema_20 > feat.ema_50:
                    trend_dir = 1
                elif feat.ema_20 < feat.ema_50:
                    trend_dir = -1
            elif feat.short_trend == "UP":
                trend_dir = 1
            elif feat.short_trend == "DOWN":
                trend_dir = -1
            slope = feat.price_slope_pct or 0.0
            if trend_dir != 0 and abs(slope) >= 0.35 and (
                (trend_dir > 0 and slope > 0) or (trend_dir < 0 and slope < 0)
            ):
                directional_ok = True
                score = 0.25 * trend_dir  # weak; usually still HOLD vs 0.35 threshold
                comps["directional_tilt"] = score
                used["directional_evidence"] = {
                    "trend_dir": trend_dir,
                    "price_slope_pct": slope,
                }
                reasons.append("יש הטיה כיוונית חלשה — לא מספיקה לבד לעסקה")

        conf = 0.55
        if regime == "LOW":
            conf = 0.45
        elif regime == "NORMAL":
            conf = 0.5
        elif regime == "HIGH":
            conf = 0.7
        elif regime == "EXTREME":
            conf = 0.78

        # Volatility agent defaults to HOLD — high confidence in the regime call.
        if not directional_ok or abs(score) < 0.35:
            side = Side.HOLD
            head = f"HOLD — משטר {regime}"
        else:
            side = Side.BUY if score > 0 else Side.SELL
            head = f"{side.value} — משטר {regime} עם הטיה כיוונית"
            conf = min(conf, 0.65)

        rationale = head + " · " + " · ".join(reasons[:6])

        return AgentVote(
            self.agent_id,
            self.agent_name,
            tick.symbol,
            side,
            round(min(HEURISTIC_CONFIDENCE_CAP, conf), 4),
            rationale[:400],
            timeframe=feat.timeframe,
            market_timestamp=feat.candle_timestamp,
            score=round(score, 4),
            components={k: round(v, 4) for k, v in comps.items()},
            used_for_decision=used,
            informational=info,
            volatility_regime=regime,
            inputs={
                "regime": regime,
                "score": round(score, 4),
                "USED_FOR_DECISION": used,
                "INFORMATIONAL_ONLY": info,
            },
        )

    @staticmethod
    def _regime(feat: FeatureSnapshot) -> str:
        atr_pct = feat.atr_pct
        bb_w = feat.bb_width
        rv = feat.realized_volatility
        score = 0
        if atr_pct is not None:
            if atr_pct >= VOL_ATR_EXTREME:
                score += 3
            elif atr_pct >= VOL_ATR_HIGH:
                score += 2
            elif atr_pct >= VOL_ATR_LOW:
                score += 1
        if bb_w is not None:
            if bb_w >= VOL_BB_WIDTH_EXTREME:
                score += 2
            elif bb_w >= VOL_BB_WIDTH_HIGH:
                score += 1
        if rv is not None and rv > VOL_ELEVATED * 2:
            score += 1
        if feat.volume_spike:
            score += 1
        if score >= 5:
            return "EXTREME"
        if score >= 3:
            return "HIGH"
        if score >= 1:
            return "NORMAL"
        return "LOW"


def default_agents() -> list[BaseAgent]:
    return [MomentumAgent(), MeanReversionAgent(), VolatilityAgent()]


# Stable ids for UI coloring / logging
HEURISTIC_AGENT_IDS = ("momentum", "mean_reversion", "volatility")
AI_AGENT_ID = "ai_analyst"
