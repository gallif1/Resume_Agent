"""Persistent forward-outcome tracking for paper decisions (no broker).

Stores actionable BUY/SELL decisions, resolves 5m/15m/60m horizons via a
periodic scheduler, and aggregates agent / Decision Engine accuracy stats.
Agent weights are NEVER auto-tuned from these stats.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR, OUTCOME_HORIZONS_SEC

logger = logging.getLogger("trading.outcomes")

GetPriceFn = Callable[[str, float], tuple[float | None, str]]
# Returns (price_or_None, status) where status is "ok" | "unavailable" | "no_history"


def _horizon_keys() -> list[tuple[str, float]]:
    """[(key, seconds), ...] e.g. ('5m', 300)."""
    return list(OUTCOME_HORIZONS_SEC)


def direction_correct(action: str, return_pct: float | None) -> bool | None:
    if return_pct is None:
        return None
    a = (action or "").upper()
    if a == "BUY":
        return return_pct > 0
    if a == "SELL":
        return return_pct < 0
    return None


class OutcomeStore:
    """JSONL-backed outcome records that survive restarts."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (DATA_DIR / "decision_outcomes.jsonl")
        self._lock = threading.RLock()
        self._by_id: dict[str, dict[str, Any]] = {}
        self._by_decision: dict[str, str] = {}
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    oid = str(row.get("id") or "")
                    if not oid:
                        continue
                    self._by_id[oid] = row
                    did = str(row.get("decision_id") or "")
                    if did:
                        self._by_decision[did] = oid
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed loading outcomes: %s", exc)

    def _rewrite(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for row in self._by_id.values():
                    f.write(json.dumps(row, default=str) + "\n")
            tmp.replace(self.path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed rewriting outcomes: %s", exc)

    def get(self, outcome_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._by_id.get(outcome_id)
            return dict(row) if row else None

    def get_by_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self._lock:
            oid = self._by_decision.get(decision_id)
            if not oid:
                return None
            row = self._by_id.get(oid)
            return dict(row) if row else None

    def clear(self) -> int:
        """Erase all outcomes from memory and the JSONL file."""
        with self._lock:
            n = len(self._by_id)
            self._by_id.clear()
            self._by_decision.clear()
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text("", encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed clearing outcomes file: %s", exc)
            return n

    def record_actionable(
        self,
        *,
        decision_id: str,
        symbol: str,
        action: str,
        entry_price: float,
        entry_ts: float | None = None,
        final_confidence: float | None = None,
        agent_votes: list[dict[str, Any]] | None = None,
        executed: bool = False,
        trade: dict[str, Any] | None = None,
        kind: str | None = None,
    ) -> dict[str, Any] | None:
        """Create outcome scaffold for BUY/SELL. Idempotent per decision_id."""
        action_u = (action or "").upper()
        if action_u not in {"BUY", "SELL"}:
            return None
        if entry_price <= 0:
            return None
        with self._lock:
            existing_id = self._by_decision.get(decision_id)
            if existing_id and existing_id in self._by_id:
                row = self._by_id[existing_id]
                if executed and trade:
                    row["executed"] = True
                    row["trade"] = trade
                    self._rewrite()
                return dict(row)

            now = entry_ts if entry_ts is not None else time.time()
            horizons: dict[str, Any] = {}
            for key, secs in _horizon_keys():
                horizons[key] = {
                    "due_at": now + secs,
                    "seconds": secs,
                    "price": None,
                    "return_pct": None,
                    "direction_correct": None,
                    "status": "pending",
                    "resolved_at": None,
                }
            row = {
                "id": uuid.uuid4().hex[:16],
                "decision_id": decision_id,
                "ts": now,
                "symbol": symbol,
                "action": action_u,
                "entry_price": float(entry_price),
                "final_confidence": final_confidence,
                "agent_votes": list(agent_votes or []),
                "executed": bool(executed),
                "trade": trade,
                "kind": kind,
                "horizons": horizons,
            }
            self._by_id[row["id"]] = row
            self._by_decision[decision_id] = row["id"]
            self._rewrite()
            return dict(row)

    def resolve_due(self, get_price: GetPriceFn, now: float | None = None) -> int:
        """Resolve any pending horizons whose due_at has passed. Returns count written."""
        now = now if now is not None else time.time()
        written = 0
        with self._lock:
            dirty = False
            for row in self._by_id.values():
                symbol = str(row.get("symbol") or "")
                entry = float(row.get("entry_price") or 0)
                action = str(row.get("action") or "")
                horizons = row.get("horizons") or {}
                for key, h in horizons.items():
                    if not isinstance(h, dict):
                        continue
                    if h.get("status") not in (None, "pending"):
                        continue
                    due = float(h.get("due_at") or 0)
                    if due > now:
                        continue
                    # Prefer price near the due timestamp (restart-safe).
                    price, status = get_price(symbol, due)
                    if price is None or price <= 0:
                        h["status"] = status if status in {"unavailable", "no_history"} else "unavailable"
                        h["resolved_at"] = now
                        dirty = True
                        written += 1
                        continue
                    ret = ((price - entry) / entry) * 100.0 if entry > 0 else None
                    h["price"] = round(float(price), 6)
                    h["return_pct"] = round(float(ret), 4) if ret is not None else None
                    h["direction_correct"] = direction_correct(action, ret)
                    h["status"] = "resolved"
                    h["resolved_at"] = now
                    # Per-agent correctness at this horizon
                    agent_results = h.setdefault("agent_results", {})
                    for v in row.get("agent_votes") or []:
                        aid = str(v.get("agent_id") or "")
                        if not aid:
                            continue
                        aside = str(v.get("side") or v.get("action") or "").upper()
                        if aside not in {"BUY", "SELL"}:
                            agent_results[aid] = {
                                "action": aside,
                                "direction_correct": None,
                                "return_pct": h["return_pct"],
                            }
                            continue
                        agent_results[aid] = {
                            "action": aside,
                            "direction_correct": direction_correct(aside, ret),
                            "return_pct": h["return_pct"],
                        }
                    dirty = True
                    written += 1
            if dirty:
                self._rewrite()
        return written

    def performance_stats(self, min_samples: int = 1) -> dict[str, Any]:
        """Aggregate accuracy / avg returns for Decision Engine and each agent."""
        with self._lock:
            rows = list(self._by_id.values())

        engine = _empty_actor_stats("decision_engine", "Decision Engine")
        agents: dict[str, dict[str, Any]] = {}

        for row in rows:
            action = str(row.get("action") or "").upper()
            if action not in {"BUY", "SELL"}:
                continue
            _accumulate_actor(engine, action, row.get("horizons") or {}, is_final=True)

            for v in row.get("agent_votes") or []:
                aid = str(v.get("agent_id") or "")
                aname = str(v.get("agent_name") or aid)
                aside = str(v.get("side") or v.get("action") or "").upper()
                if aside not in {"BUY", "SELL"}:
                    continue
                if aid not in agents:
                    agents[aid] = _empty_actor_stats(aid, aname)
                # Build synthetic horizons using agent-side correctness from stored agent_results
                # or recompute from market return + agent action.
                synthetic = {}
                for hk, h in (row.get("horizons") or {}).items():
                    if not isinstance(h, dict) or h.get("status") != "resolved":
                        continue
                    ret = h.get("return_pct")
                    ar = (h.get("agent_results") or {}).get(aid)
                    if ar and "direction_correct" in ar:
                        correct = ar.get("direction_correct")
                    else:
                        correct = direction_correct(aside, ret if isinstance(ret, (int, float)) else None)
                    synthetic[hk] = {
                        "status": "resolved",
                        "return_pct": ret,
                        "direction_correct": correct,
                    }
                _accumulate_actor(agents[aid], aside, synthetic, is_final=False)

        def finalize(s: dict[str, Any]) -> dict[str, Any]:
            out = dict(s)
            for hk, _ in _horizon_keys():
                n = int(out.get(f"n_{hk}") or 0)
                c = int(out.get(f"correct_{hk}") or 0)
                if n >= min_samples:
                    out[f"accuracy_{hk}"] = round(c / n, 4)
                    out[f"accuracy_{hk}_pct"] = round(100.0 * c / n, 1)
                else:
                    out[f"accuracy_{hk}"] = None
                    out[f"accuracy_{hk}_pct"] = None
                br = out.get(f"buy_returns_{hk}") or []
                sr = out.get(f"sell_returns_{hk}") or []
                out[f"avg_buy_return_{hk}"] = (
                    round(sum(br) / len(br), 4) if br else None
                )
                out[f"avg_sell_return_{hk}"] = (
                    round(sum(sr) / len(sr), 4) if sr else None
                )
                # Drop raw lists from API payload
                out.pop(f"buy_returns_{hk}", None)
                out.pop(f"sell_returns_{hk}", None)
            out["total_actionable"] = int(out.get("total_actionable") or 0)
            return out

        return {
            "decision_engine": finalize(engine),
            "agents": {aid: finalize(s) for aid, s in agents.items()},
            "horizons": [k for k, _ in _horizon_keys()],
            "outcome_count": len(rows),
            "resolved_enough": any(
                int(finalize(engine).get(f"n_{k}") or 0) >= min_samples for k, _ in _horizon_keys()
            ),
        }

    def attach_outcome_summary(self, decision_id: str) -> dict[str, Any] | None:
        row = self.get_by_decision(decision_id)
        if not row:
            return None
        return summarize_outcome(row)


def summarize_outcome(row: dict[str, Any]) -> dict[str, Any]:
    horizons_out: dict[str, Any] = {}
    for key, _ in _horizon_keys():
        h = (row.get("horizons") or {}).get(key) or {}
        horizons_out[key] = {
            "status": h.get("status") or "pending",
            "price": h.get("price"),
            "return_pct": h.get("return_pct"),
            "direction_correct": h.get("direction_correct"),
            "due_at": h.get("due_at"),
        }
    return {
        "outcome_id": row.get("id"),
        "entry_price": row.get("entry_price"),
        "action": row.get("action"),
        "executed": row.get("executed"),
        "trade": row.get("trade"),
        "horizons": horizons_out,
    }


def _empty_actor_stats(actor_id: str, name: str) -> dict[str, Any]:
    s: dict[str, Any] = {
        "id": actor_id,
        "name": name,
        "total_actionable": 0,
        "buy_predictions": 0,
        "sell_predictions": 0,
    }
    for key, _ in _horizon_keys():
        s[f"n_{key}"] = 0
        s[f"correct_{key}"] = 0
        s[f"buy_returns_{key}"] = []
        s[f"sell_returns_{key}"] = []
    return s


def _accumulate_actor(
    stats: dict[str, Any],
    action: str,
    horizons: dict[str, Any],
    *,
    is_final: bool,
) -> None:
    stats["total_actionable"] = int(stats.get("total_actionable") or 0) + 1
    if action == "BUY":
        stats["buy_predictions"] = int(stats.get("buy_predictions") or 0) + 1
    elif action == "SELL":
        stats["sell_predictions"] = int(stats.get("sell_predictions") or 0) + 1
    for key, h in horizons.items():
        if not isinstance(h, dict) or h.get("status") != "resolved":
            continue
        correct = h.get("direction_correct")
        ret = h.get("return_pct")
        if correct is None and ret is None:
            continue
        stats[f"n_{key}"] = int(stats.get(f"n_{key}") or 0) + 1
        if correct is True:
            stats[f"correct_{key}"] = int(stats.get(f"correct_{key}") or 0) + 1
        if isinstance(ret, (int, float)):
            if action == "BUY":
                stats.setdefault(f"buy_returns_{key}", []).append(float(ret))
            elif action == "SELL":
                stats.setdefault(f"sell_returns_{key}", []).append(float(ret))
        _ = is_final  # reserved for future trade-vs-signal split
