"""UnifiedDecision: correlate paper events under one decision_id.

Primary correlation for NEW records is always ``decision_id`` (same as
``Decision.id``). Approximate timestamp clustering must NOT be used for new
records.

Historical fallback (events without decision_id)
------------------------------------------------
``group_historical_events_without_decision_id`` rebuilds approximate groups
for *legacy* rows only by:

1. Normalizing the symbol (``normalize_symbol``).
2. Mapping each event timestamp onto a candle interval open
   (``candle_bucket_ts``) so fills/votes on the same bar share a key.
3. Preferring explicit order/fill relationships in the payload
   (``order_id`` / ``fill_id`` / ``decision_id`` in payload) when present.

This helper exists solely for backfilling chart history. New persists always
set the ``decision_id`` column and payload field.
"""

from __future__ import annotations

import time
from typing import Any

from .time_utils import candle_bucket_ts, normalize_symbol

MISSING_EXPLANATION_HE = "הנימוק המלא לא נשמר עבור החלטה היסטורית זו."


def _side_value(obj: Any) -> str:
    if obj is None:
        return "HOLD"
    if hasattr(obj, "value"):
        return str(obj.value)
    return str(obj).upper()


def _vote_dicts(votes: list[Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for v in votes or []:
        if hasattr(v, "to_dict"):
            out.append(v.to_dict())
        elif isinstance(v, dict):
            out.append(v)
    return out


def _compact_agent_votes(votes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for v in votes:
        compact.append(
            {
                "agent_id": v.get("agent_id"),
                "agent_name": v.get("agent_name"),
                "side": v.get("side") or v.get("action"),
                "confidence": v.get("confidence"),
                "rationale": v.get("rationale") or v.get("reason"),
            }
        )
    return compact


def _agreement(votes: list[dict[str, Any]], final_action: str) -> dict[str, int]:
    supporting = 0
    opposing = 0
    for v in votes:
        side = str(v.get("side") or v.get("action") or "HOLD").upper()
        if side == final_action:
            supporting += 1
        elif side in {"BUY", "SELL"} and final_action in {"BUY", "SELL"} and side != final_action:
            opposing += 1
        elif side == "HOLD" and final_action in {"BUY", "SELL"}:
            opposing += 1
    return {
        "supporting": supporting,
        "opposing": opposing,
        "total": len(votes),
    }


def _risk_level(
    *,
    confidence: float,
    agreement: dict[str, int],
    risk_factors: list[str],
    data_quality: dict[str, Any] | None,
) -> str:
    stale = bool((data_quality or {}).get("stale"))
    oppose_ratio = 0.0
    total = agreement.get("total") or 0
    if total:
        oppose_ratio = agreement.get("opposing", 0) / total
    if stale or len(risk_factors) >= 3 or oppose_ratio >= 0.5 or confidence < 0.4:
        return "HIGH"
    if len(risk_factors) >= 1 or oppose_ratio >= 0.25 or confidence < 0.55:
        return "MEDIUM"
    return "LOW"


def _indicators_used(market_snapshot: dict[str, Any] | None, evidence: dict[str, Any] | None) -> dict[str, Any]:
    snap = market_snapshot or {}
    ev = evidence or {}
    # Prefer Phase-3 compact evidence when already attached to the log snapshot.
    prebuilt = snap.get("indicators_used")
    if isinstance(prebuilt, dict) and prebuilt:
        out = dict(prebuilt)
        # Keep legacy keys for older UI consumers when present on snap/ev.
        for k in ("sma_fast", "sma_slow", "ema_fast", "volatility", "volume_state", "trend"):
            if k not in out or out[k] is None:
                if k in ev and ev[k] is not None:
                    out[k] = ev[k]
                elif k in snap and snap[k] is not None:
                    out[k] = snap[k]
        return out

    keys = (
        "price",
        "ema_20",
        "ema_50",
        "rsi_14",
        "macd",
        "macd_signal",
        "relative_volume",
        "atr_14",
        "vwap",
        "nearest_support",
        "nearest_resistance",
        "sma_fast",
        "sma_slow",
        "ema_fast",
        "volatility",
        "volume_state",
        "trend",
        "change_1m_pct",
        "change_5m_pct",
        "change_15m_pct",
        "change_1h_pct",
    )
    out: dict[str, Any] = {}
    for k in keys:
        if k in ev and ev[k] is not None:
            out[k] = ev[k]
        elif k in snap and snap[k] is not None:
            out[k] = snap[k]
        elif isinstance(ev.get("extras"), dict) and ev["extras"].get(k) is not None:
            out[k] = ev["extras"][k]
    return out


def _interpreted_signals(
    votes: list[dict[str, Any]],
    indicators: dict[str, Any],
    market_snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    snap = market_snapshot or {}
    # Prefer Phase-3 Hebrew interpretations from indicators.evidence.
    prebuilt = snap.get("interpreted_signals")
    signals: list[dict[str, Any]] = []
    if isinstance(prebuilt, list) and prebuilt:
        signals.extend(prebuilt)

    for v in votes:
        side = str(v.get("side") or v.get("action") or "HOLD").upper()
        if side not in {"BUY", "SELL", "HOLD"}:
            continue
        signals.append(
            {
                "source": v.get("agent_id") or "agent",
                "signal": side,
                "confidence": v.get("confidence"),
                "detail": v.get("rationale") or v.get("reason"),
            }
        )
    if not isinstance(prebuilt, list) or not prebuilt:
        rsi = indicators.get("rsi_14")
        if isinstance(rsi, (int, float)):
            if rsi >= 70:
                signals.append({"source": "rsi_14", "signal": "OVERBOUGHT", "value": rsi})
            elif rsi <= 30:
                signals.append({"source": "rsi_14", "signal": "OVERSOLD", "value": rsi})
        trend = indicators.get("trend") or indicators.get("short_trend") or snap.get("trend")
        if trend:
            signals.append({"source": "trend", "signal": str(trend)})
    return signals


def _hebrew_primary_reasons(
    *,
    final_action: str,
    confidence: float,
    agreement: dict[str, int],
    votes: list[dict[str, Any]],
    indicators: dict[str, Any],
    execution: dict[str, Any] | None,
    decision: Any,
    has_structured: bool,
) -> list[str]:
    if not has_structured:
        return [MISSING_EXPLANATION_HE]

    reasons: list[str] = []
    engine = getattr(decision, "engine", None) if decision is not None else None
    if isinstance(decision, dict):
        engine = decision.get("engine")
    engine = engine or {}

    conf_pct = f"{confidence:.0%}" if confidence <= 1 else f"{confidence:.0f}%"
    reasons.append(f"החלטה סופית: {final_action} בביטחון {conf_pct}.")

    if agreement.get("total"):
        reasons.append(
            f"הסכמת סוכנים: {agreement['supporting']}/{agreement['total']} תומכים "
            f"({agreement['opposing']} מתנגדים)."
        )

    if engine.get("explanation"):
        reasons.append(str(engine["explanation"])[:220])
    elif getattr(decision, "rationale", None) or (
        isinstance(decision, dict) and decision.get("rationale")
    ):
        rationale = (
            getattr(decision, "rationale", None)
            if not isinstance(decision, dict)
            else decision.get("rationale")
        )
        if rationale:
            reasons.append(str(rationale)[:220])

    rsi = indicators.get("rsi_14")
    if isinstance(rsi, (int, float)):
        reasons.append(f"RSI(14)={rsi:.1f}.")

    status = (execution or {}).get("status")
    if status == "FILLED":
        reasons.append("ביצוע נייר הושלם.")
    elif status in {"BLOCKED", "NOT_FILLED"} and (execution or {}).get("reason"):
        reasons.append(str((execution or {}).get("reason"))[:180])

    # Keep 2–4 deterministic strings.
    deduped: list[str] = []
    for r in reasons:
        r = (r or "").strip()
        if r and r not in deduped:
            deduped.append(r)
    if len(deduped) < 2:
        top = sorted(
            votes,
            key=lambda v: float(v.get("confidence") or 0),
            reverse=True,
        )[:2]
        for v in top:
            name = v.get("agent_name") or v.get("agent_id") or "סוכן"
            side = v.get("side") or v.get("action") or "HOLD"
            deduped.append(f"{name}: {side}.")
    return deduped[:4] or [MISSING_EXPLANATION_HE]


def _hebrew_risk_factors(
    *,
    agreement: dict[str, int],
    data_quality: dict[str, Any] | None,
    indicators: dict[str, Any],
    execution: dict[str, Any] | None,
    has_structured: bool,
) -> list[str]:
    if not has_structured:
        return [MISSING_EXPLANATION_HE]
    factors: list[str] = []
    dq = data_quality or {}
    if dq.get("stale"):
        factors.append("נתוני שוק מיושנים או חלקיים.")
    missing = dq.get("missing_fields") or []
    if missing:
        factors.append(f"שדות חסרים: {', '.join(str(m) for m in missing[:5])}.")
    if agreement.get("total") and agreement.get("opposing", 0) > 0:
        factors.append(
            f"יש התנגדות בין סוכנים ({agreement['opposing']} מתוך {agreement['total']})."
        )
    vol = indicators.get("volatility")
    if isinstance(vol, (int, float)) and vol >= 0.02:
        factors.append(f"תנודתיות גבוהה יחסית ({vol:.3f}).")
    if (execution or {}).get("status") in {"BLOCKED", "NOT_FILLED"} and (
        execution or {}
    ).get("reason"):
        factors.append(str((execution or {}).get("reason"))[:160])
    return factors[:6]


def _resolve_status(
    *,
    final_action: str,
    execution: dict[str, Any] | None,
    filled: bool | None,
    block_reason: str | None,
) -> str:
    ex_status = str((execution or {}).get("status") or "").upper()
    if filled or ex_status == "FILLED":
        return "FILLED"
    if block_reason or ex_status in {"BLOCKED", "REJECTED"}:
        return "BLOCKED"
    if final_action == "HOLD":
        return "HOLD"
    if ex_status == "NOT_FILLED" and final_action in {"BUY", "SELL"}:
        return "BLOCKED" if block_reason else "SIGNAL"
    if final_action in {"BUY", "SELL"}:
        return "SIGNAL"
    return "HOLD"


def build_unified_decision(
    *,
    decision: Any,
    votes: list[Any] | None = None,
    execution: dict[str, Any] | None = None,
    market_snapshot: dict[str, Any] | None = None,
    indicator_evidence: dict[str, Any] | None = None,
    portfolio_context: dict[str, Any] | None = None,
    raw_events: list[dict[str, Any]] | None = None,
    timeframe: str | None = None,
    candle_time: float | None = None,
    filled: bool | None = None,
    block_reason: str | None = None,
    data_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a UnifiedDecision dict from live decision cycle inputs."""
    if decision is None:
        raise ValueError("decision required")

    if hasattr(decision, "to_dict"):
        d = decision.to_dict()
        decision_id = str(getattr(decision, "id"))
        final_action = _side_value(getattr(decision, "side"))
        confidence = float(getattr(decision, "confidence") or 0)
        symbol = normalize_symbol(str(getattr(decision, "symbol")))
        decision_time = float(getattr(decision, "ts") or time.time())
        quantity = getattr(decision, "quantity", None)
        fill_price = getattr(decision, "fill_price", None)
        rationale = getattr(decision, "rationale", None)
        has_structured = True
    elif isinstance(decision, dict):
        d = decision
        decision_id = str(d.get("decision_id") or d.get("id") or "").strip()
        if not decision_id:
            raise ValueError("decision_id required")
        final_action = str(d.get("final_action") or d.get("side") or d.get("action") or "HOLD").upper()
        confidence = float(d.get("confidence") or 0)
        symbol = normalize_symbol(str(d.get("symbol") or ""))
        decision_time = float(d.get("decision_time") or d.get("ts") or time.time())
        quantity = d.get("quantity")
        fill_price = d.get("fill_price")
        rationale = d.get("rationale")
        # Historical rebuilds may lack votes/engine — do not fabricate.
        has_structured = bool(
            votes
            or d.get("votes")
            or d.get("engine")
            or d.get("primary_reasons")
            or market_snapshot
            or indicator_evidence
        )
    else:
        raise ValueError("decision must be Decision or dict")

    vote_list = _vote_dicts(votes)
    if not vote_list and isinstance(d, dict):
        vote_list = _vote_dicts(d.get("votes"))

    snap = market_snapshot or {}
    dq = data_quality or snap.get("data_quality") or {}
    indicators = _indicators_used(snap, indicator_evidence)
    agreement = _agreement(vote_list, final_action)
    status = _resolve_status(
        final_action=final_action,
        execution=execution,
        filled=filled,
        block_reason=block_reason,
    )
    primary_reasons = _hebrew_primary_reasons(
        final_action=final_action,
        confidence=confidence,
        agreement=agreement,
        votes=vote_list,
        indicators=indicators,
        execution=execution,
        decision=decision,
        has_structured=has_structured,
    )
    risk_factors = _hebrew_risk_factors(
        agreement=agreement,
        data_quality=dq,
        indicators=indicators,
        execution=execution,
        has_structured=has_structured,
    )
    risk_level = _risk_level(
        confidence=confidence,
        agreement=agreement,
        risk_factors=[r for r in risk_factors if r != MISSING_EXPLANATION_HE],
        data_quality=dq,
    )

    total_value = None
    if quantity is not None and fill_price is not None:
        try:
            total_value = float(quantity) * float(fill_price)
        except (TypeError, ValueError):
            total_value = None

    conf_pct = f"{confidence:.0%}" if confidence <= 1 else f"{float(confidence):.0f}%"
    if has_structured:
        summary = f"{symbol}: {final_action} ({status}) · ביטחון {conf_pct}"
        if rationale:
            summary = f"{summary} — {str(rationale)[:120]}"
    else:
        summary = MISSING_EXPLANATION_HE

    if candle_time is None and decision_time:
        try:
            candle_time = float(candle_bucket_ts(decision_time, timeframe or "5m"))
        except Exception:  # noqa: BLE001
            candle_time = decision_time

    return {
        "decision_id": decision_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "decision_time": decision_time,
        "candle_time": candle_time,
        "final_action": final_action,
        "confidence": confidence,
        "status": status,
        "quantity": quantity,
        "fill_price": fill_price,
        "total_value": total_value,
        "summary": summary,
        "primary_reasons": primary_reasons,
        "risk_factors": risk_factors,
        "agent_votes": _compact_agent_votes(vote_list),
        "indicators_used": indicators,
        "interpreted_signals": _interpreted_signals(vote_list, indicators, snap),
        "portfolio_context": portfolio_context or {},
        "data_quality": dq,
        "agreement": agreement,
        "risk_level": risk_level,
        "execution": execution or {},
        "block_reason": block_reason,
        "raw_events": list(raw_events or []),
        "engine": (d.get("engine") if isinstance(d, dict) else getattr(decision, "engine", None))
        or {},
    }


def group_historical_events_without_decision_id(
    events: list[dict[str, Any]],
    *,
    timeframe: str = "5m",
) -> list[list[dict[str, Any]]]:
    """Fallback grouping for legacy paper_events lacking decision_id.

    See module docstring. Do not use this for newly written records.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("decision_id") or (ev.get("payload") or {}).get("decision_id"):
            # Already correlated — skip fallback path for that event.
            continue
        symbol = normalize_symbol(str(ev.get("symbol") or ""))
        ts = float(ev.get("ts") or 0)
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        rel = (
            payload.get("order_id")
            or payload.get("fill_id")
            or payload.get("decision_id")
            or ev.get("event_id")
        )
        bucket = candle_bucket_ts(ts, timeframe) if ts else 0
        # Prefer order/fill relationship key when present; else symbol+candle.
        if rel and str(rel) not in {str(ev.get("event_id")), ""}:
            key = f"{symbol}|rel:{rel}"
        else:
            key = f"{symbol}|candle:{bucket}"
        buckets.setdefault(key, []).append(ev)
    return list(buckets.values())
