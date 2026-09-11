"""SQLite persistence for OHLCV candles and chart annotations.

UTC timestamps. Unique (symbol, timeframe, ts) prevents duplicate candles.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from .models import Candle

ANNOTATION_TYPES = frozenset(
    {
        "SUPPORT",
        "RESISTANCE",
        "TREND_LINE",
        "TEXT_NOTE",
        "TRADE_MARKER",
    }
)
IMPORTANCE_LEVELS = frozenset({"low", "medium", "high"})
LINE_STYLES = frozenset({"solid", "dashed", "dotted"})
TIMEFRAME_SCOPES = frozenset({"all", "1m", "5m", "15m", "1h", "4h", "1d"})


class MarketDB:
    def __init__(self, path: Path | None = None) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path or (DATA_DIR / "market.db")
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS candles (
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        ts REAL NOT NULL,
                        open REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        close REAL NOT NULL,
                        volume REAL NOT NULL DEFAULT 0,
                        PRIMARY KEY (symbol, timeframe, ts)
                    );
                    CREATE INDEX IF NOT EXISTS idx_candles_sym_tf_ts
                        ON candles(symbol, timeframe, ts);

                    CREATE TABLE IF NOT EXISTS chart_annotations (
                        id TEXT PRIMARY KEY,
                        user_id TEXT,
                        symbol TEXT NOT NULL,
                        timeframe_scope TEXT NOT NULL DEFAULT 'all',
                        annotation_type TEXT NOT NULL,
                        coordinates TEXT NOT NULL DEFAULT '{}',
                        price REAL,
                        label TEXT,
                        note TEXT,
                        color TEXT,
                        line_style TEXT DEFAULT 'solid',
                        importance TEXT DEFAULT 'medium',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1
                    );
                    CREATE INDEX IF NOT EXISTS idx_ann_symbol_active
                        ON chart_annotations(symbol, active);

                    CREATE TABLE IF NOT EXISTS paper_events (
                        id TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        event_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        ts REAL NOT NULL,
                        price REAL,
                        quantity REAL,
                        confidence REAL,
                        status TEXT,
                        payload TEXT NOT NULL DEFAULT '{}',
                        UNIQUE(event_type, event_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_paper_events_sym_ts
                        ON paper_events(symbol, ts);

                    CREATE TABLE IF NOT EXISTS unified_decisions (
                        decision_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        timeframe TEXT,
                        decision_time REAL NOT NULL,
                        candle_time REAL,
                        final_action TEXT,
                        confidence REAL,
                        status TEXT,
                        quantity REAL,
                        fill_price REAL,
                        total_value REAL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_unified_decisions_sym_time
                        ON unified_decisions(symbol, decision_time);

                    CREATE TABLE IF NOT EXISTS trading_asset_config (
                        id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL UNIQUE,
                        asset_type TEXT,
                        provider TEXT,
                        mode TEXT NOT NULL DEFAULT 'MONITOR_ONLY',
                        max_allocation_amount REAL,
                        max_portfolio_percentage REAL,
                        max_position_size REAL,
                        max_trades_per_day INTEGER,
                        minimum_confidence REAL,
                        cooldown_seconds REAL,
                        allowed_directions TEXT,
                        allowed_timeframes TEXT,
                        stop_loss_policy TEXT,
                        take_profit_policy TEXT,
                        enabled_at REAL,
                        updated_at REAL,
                        config_json TEXT DEFAULT '{}'
                    );
                    CREATE INDEX IF NOT EXISTS idx_asset_config_symbol
                        ON trading_asset_config(symbol);
                    """
                )
                self._migrate_paper_events_decision_id(conn)
                conn.commit()
            finally:
                conn.close()

    def _migrate_paper_events_decision_id(self, conn: sqlite3.Connection) -> None:
        """Safe additive migration: add decision_id column if missing."""
        cols = {
            str(r["name"])
            for r in conn.execute("PRAGMA table_info(paper_events)").fetchall()
        }
        if "decision_id" not in cols:
            conn.execute("ALTER TABLE paper_events ADD COLUMN decision_id TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_events_decision_id "
                "ON paper_events(decision_id)"
            )

    # --- candles ---------------------------------------------------------

    def upsert_candles(self, symbol: str, timeframe: str, candles: list[Candle]) -> int:
        if not candles:
            return 0
        symbol = symbol.upper()
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    """
                    INSERT INTO candles(symbol, timeframe, ts, open, high, low, close, volume)
                    VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(symbol, timeframe, ts) DO UPDATE SET
                        open=excluded.open,
                        high=excluded.high,
                        low=excluded.low,
                        close=excluded.close,
                        volume=excluded.volume
                    """,
                    [
                        (
                            symbol,
                            timeframe,
                            float(c.ts),
                            float(c.open),
                            float(c.high),
                            float(c.low),
                            float(c.close),
                            float(c.volume),
                        )
                        for c in candles
                    ],
                )
                conn.commit()
                return len(candles)
            finally:
                conn.close()

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 500,
        before: float | None = None,
        after: float | None = None,
    ) -> list[Candle]:
        symbol = symbol.upper()
        limit = max(1, min(int(limit), 5000))
        clauses = ["symbol = ?", "timeframe = ?"]
        params: list[Any] = [symbol, timeframe]
        if before is not None:
            clauses.append("ts < ?")
            params.append(float(before))
        if after is not None:
            clauses.append("ts >= ?")
            params.append(float(after))
        where = " AND ".join(clauses)
        sql = (
            f"SELECT ts, open, high, low, close, volume FROM candles "
            f"WHERE {where} ORDER BY ts DESC LIMIT ?"
        )
        params.append(limit)
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        candles = [
            Candle(
                ts=float(r["ts"]),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r["volume"]),
            )
            for r in rows
        ]
        candles.reverse()
        return candles

    def candle_count(self, symbol: str, timeframe: str) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM candles WHERE symbol=? AND timeframe=?",
                    (symbol.upper(), timeframe),
                ).fetchone()
                return int(row["n"] if row else 0)
            finally:
                conn.close()

    # --- annotations -----------------------------------------------------

    def list_annotations(self, symbol: str, *, active_only: bool = True) -> list[dict[str, Any]]:
        symbol = symbol.upper()
        sql = "SELECT * FROM chart_annotations WHERE symbol = ?"
        params: list[Any] = [symbol]
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY created_at ASC"
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        return [self._ann_row(r) for r in rows]

    def create_annotation(self, payload: dict[str, Any]) -> dict[str, Any]:
        cleaned = validate_annotation_payload(payload, partial=False)
        now = time.time()
        row = {
            "id": uuid.uuid4().hex[:16],
            "user_id": cleaned.get("user_id"),
            "symbol": cleaned["symbol"],
            "timeframe_scope": cleaned["timeframe_scope"],
            "annotation_type": cleaned["annotation_type"],
            "coordinates": json.dumps(cleaned["coordinates"]),
            "price": cleaned.get("price"),
            "label": cleaned.get("label"),
            "note": cleaned.get("note"),
            "color": cleaned.get("color"),
            "line_style": cleaned.get("line_style", "solid"),
            "importance": cleaned.get("importance", "medium"),
            "created_at": now,
            "updated_at": now,
            "active": 1,
        }
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO chart_annotations(
                        id, user_id, symbol, timeframe_scope, annotation_type,
                        coordinates, price, label, note, color, line_style,
                        importance, created_at, updated_at, active
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row["id"],
                        row["user_id"],
                        row["symbol"],
                        row["timeframe_scope"],
                        row["annotation_type"],
                        row["coordinates"],
                        row["price"],
                        row["label"],
                        row["note"],
                        row["color"],
                        row["line_style"],
                        row["importance"],
                        row["created_at"],
                        row["updated_at"],
                        row["active"],
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_annotation(row["id"]) or {}

    def get_annotation(self, ann_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM chart_annotations WHERE id = ?", (ann_id,)
                ).fetchone()
            finally:
                conn.close()
        return self._ann_row(row) if row else None

    def update_annotation(self, ann_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        existing = self.get_annotation(ann_id)
        if not existing:
            return None
        merged = {**existing, **payload, "id": ann_id, "symbol": existing["symbol"]}
        cleaned = validate_annotation_payload(merged, partial=True)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE chart_annotations SET
                        timeframe_scope=?, annotation_type=?, coordinates=?,
                        price=?, label=?, note=?, color=?, line_style=?,
                        importance=?, updated_at=?, active=?
                    WHERE id=?
                    """,
                    (
                        cleaned["timeframe_scope"],
                        cleaned["annotation_type"],
                        json.dumps(cleaned["coordinates"]),
                        cleaned.get("price"),
                        cleaned.get("label"),
                        cleaned.get("note"),
                        cleaned.get("color"),
                        cleaned.get("line_style", "solid"),
                        cleaned.get("importance", "medium"),
                        time.time(),
                        1 if cleaned.get("active", True) else 0,
                        ann_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_annotation(ann_id)

    def delete_annotation(self, ann_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM chart_annotations WHERE id = ?", (ann_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def clear_annotations(self, symbol: str) -> int:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM chart_annotations WHERE symbol = ?", (symbol.upper(),)
                )
                conn.commit()
                return int(cur.rowcount)
            finally:
                conn.close()

    # --- paper events (fills / decisions for chart markers) ---------------

    def upsert_paper_event(self, event: dict[str, Any]) -> dict[str, Any]:
        from ..time_utils import marker_key, normalize_symbol, to_unix_seconds

        event_type = str(event.get("event_type") or "").strip().lower()
        event_id = str(event.get("event_id") or "").strip()
        if not event_type or not event_id:
            raise ValueError("event_type and event_id required")
        symbol = normalize_symbol(str(event.get("symbol") or ""))
        if not symbol:
            raise ValueError("symbol required")
        side = str(event.get("side") or "HOLD").upper()
        ts = to_unix_seconds(event.get("ts") or time.time())
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        decision_id = event.get("decision_id")
        if decision_id is None:
            decision_id = payload.get("decision_id")
        if decision_id is not None:
            decision_id = str(decision_id).strip() or None
        # For fills/decisions the event_id is the decision id when not set explicitly.
        if decision_id is None and event_type in {"fill", "decision"}:
            decision_id = event_id
        row_id = marker_key(event_type, event_id)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO paper_events(
                        id, event_type, event_id, decision_id, symbol, side, ts,
                        price, quantity, confidence, status, payload
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(event_type, event_id) DO UPDATE SET
                        decision_id=excluded.decision_id,
                        symbol=excluded.symbol,
                        side=excluded.side,
                        ts=excluded.ts,
                        price=excluded.price,
                        quantity=excluded.quantity,
                        confidence=excluded.confidence,
                        status=excluded.status,
                        payload=excluded.payload
                    """,
                    (
                        row_id,
                        event_type,
                        event_id,
                        decision_id,
                        symbol,
                        side,
                        float(ts),
                        event.get("price"),
                        event.get("quantity"),
                        event.get("confidence"),
                        event.get("status"),
                        json.dumps(payload, default=str),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_paper_event(row_id) or {}

    def get_paper_event(self, row_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM paper_events WHERE id = ?", (row_id,)
                ).fetchone()
            finally:
                conn.close()
        return self._event_row(row) if row else None

    def list_paper_events(
        self,
        symbol: str,
        *,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        from ..time_utils import normalize_symbol

        symbol = normalize_symbol(symbol)
        clauses = ["symbol = ?"]
        params: list[Any] = [symbol]
        if from_ts is not None:
            clauses.append("ts >= ?")
            params.append(float(from_ts))
        if to_ts is not None:
            clauses.append("ts <= ?")
            params.append(float(to_ts))
        where = " AND ".join(clauses)
        sql = f"SELECT * FROM paper_events WHERE {where} ORDER BY ts ASC LIMIT ?"
        params.append(max(1, min(int(limit), 5000)))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        return [self._event_row(r) for r in rows]

    def clear_paper_events(self, symbol: str | None = None) -> int:
        with self._lock:
            conn = self._connect()
            try:
                if symbol:
                    from ..time_utils import normalize_symbol

                    cur = conn.execute(
                        "DELETE FROM paper_events WHERE symbol = ?",
                        (normalize_symbol(symbol),),
                    )
                else:
                    cur = conn.execute("DELETE FROM paper_events")
                conn.commit()
                return int(cur.rowcount)
            finally:
                conn.close()

    def _event_row(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        keys = row.keys()
        decision_id = row["decision_id"] if "decision_id" in keys else None
        if decision_id is None and isinstance(payload, dict):
            decision_id = payload.get("decision_id")
        return {
            "id": row["id"],
            "event_type": row["event_type"],
            "event_id": row["event_id"],
            "decision_id": decision_id,
            "symbol": row["symbol"],
            "side": row["side"],
            "ts": row["ts"],
            "price": row["price"],
            "quantity": row["quantity"],
            "confidence": row["confidence"],
            "status": row["status"],
            "payload": payload if isinstance(payload, dict) else {},
        }

    # --- unified decisions -----------------------------------------------

    def upsert_unified_decision(self, decision_dict: dict[str, Any]) -> dict[str, Any]:
        from ..time_utils import normalize_symbol

        if not isinstance(decision_dict, dict):
            raise ValueError("decision_dict must be an object")
        decision_id = str(
            decision_dict.get("decision_id") or decision_dict.get("id") or ""
        ).strip()
        if not decision_id:
            raise ValueError("decision_id required")
        symbol = normalize_symbol(str(decision_dict.get("symbol") or ""))
        if not symbol:
            raise ValueError("symbol required")
        decision_time = float(
            decision_dict.get("decision_time")
            or decision_dict.get("ts")
            or time.time()
        )
        payload = dict(decision_dict)
        payload["decision_id"] = decision_id
        payload["symbol"] = symbol
        qty = decision_dict.get("quantity")
        fill_price = decision_dict.get("fill_price")
        total_value = decision_dict.get("total_value")
        if total_value is None and qty is not None and fill_price is not None:
            try:
                total_value = float(qty) * float(fill_price)
            except (TypeError, ValueError):
                total_value = None
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO unified_decisions(
                        decision_id, symbol, timeframe, decision_time, candle_time,
                        final_action, confidence, status, quantity, fill_price,
                        total_value, payload
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(decision_id) DO UPDATE SET
                        symbol=excluded.symbol,
                        timeframe=excluded.timeframe,
                        decision_time=excluded.decision_time,
                        candle_time=excluded.candle_time,
                        final_action=excluded.final_action,
                        confidence=excluded.confidence,
                        status=excluded.status,
                        quantity=excluded.quantity,
                        fill_price=excluded.fill_price,
                        total_value=excluded.total_value,
                        payload=excluded.payload
                    """,
                    (
                        decision_id,
                        symbol,
                        decision_dict.get("timeframe"),
                        decision_time,
                        decision_dict.get("candle_time"),
                        decision_dict.get("final_action")
                        or decision_dict.get("side")
                        or decision_dict.get("action"),
                        decision_dict.get("confidence"),
                        decision_dict.get("status"),
                        qty,
                        fill_price,
                        total_value,
                        json.dumps(payload, default=str),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_unified_decision(decision_id) or {}

    def get_unified_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM unified_decisions WHERE decision_id = ?",
                    (str(decision_id),),
                ).fetchone()
            finally:
                conn.close()
        return self._unified_row(row) if row else None

    def list_unified_decisions(
        self,
        symbol: str,
        *,
        from_ts: float | None = None,
        to_ts: float | None = None,
        timeframe: str | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        from ..time_utils import normalize_symbol

        symbol = normalize_symbol(symbol)
        clauses = ["symbol = ?"]
        params: list[Any] = [symbol]
        if from_ts is not None:
            clauses.append("decision_time >= ?")
            params.append(float(from_ts))
        if to_ts is not None:
            clauses.append("decision_time <= ?")
            params.append(float(to_ts))
        if timeframe:
            clauses.append("(timeframe IS NULL OR timeframe = ?)")
            params.append(str(timeframe))
        where = " AND ".join(clauses)
        sql = (
            f"SELECT * FROM unified_decisions WHERE {where} "
            f"ORDER BY decision_time ASC LIMIT ?"
        )
        params.append(max(1, min(int(limit), 5000)))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        return [self._unified_row(r) for r in rows]

    def _unified_row(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        # Prefer full JSON payload; overlay indexed columns for consistency.
        out = dict(payload)
        out.update(
            {
                "decision_id": row["decision_id"],
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "decision_time": row["decision_time"],
                "candle_time": row["candle_time"],
                "final_action": row["final_action"],
                "confidence": row["confidence"],
                "status": row["status"],
                "quantity": row["quantity"],
                "fill_price": row["fill_price"],
                "total_value": row["total_value"],
            }
        )
        return out

    # --- trading asset config --------------------------------------------

    def get_trading_asset_config(self, symbol: str) -> dict[str, Any] | None:
        from ..time_utils import normalize_symbol

        symbol = normalize_symbol(symbol)
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM trading_asset_config WHERE symbol = ?", (symbol,)
                ).fetchone()
            finally:
                conn.close()
        return self._asset_config_row(row) if row else None

    def list_trading_asset_configs(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM trading_asset_config ORDER BY symbol ASC"
                ).fetchall()
            finally:
                conn.close()
        return [self._asset_config_row(r) for r in rows]

    def set_trading_asset_config(self, config: dict[str, Any]) -> dict[str, Any]:
        from ..time_utils import normalize_symbol

        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        symbol = normalize_symbol(str(config.get("symbol") or ""))
        if not symbol:
            raise ValueError("symbol required")
        mode = str(config.get("mode") or "MONITOR_ONLY").upper().strip()
        if mode not in {"DISABLED", "MONITOR_ONLY", "TRADE", "CLOSE_ONLY"}:
            raise ValueError("mode must be DISABLED|MONITOR_ONLY|TRADE|CLOSE_ONLY")
        now = time.time()
        existing = self.get_trading_asset_config(symbol) or {}
        row_id = existing.get("id") or uuid.uuid4().hex[:16]

        def _json_field(key: str, default: Any = None) -> str | None:
            val = config[key] if key in config else existing.get(key, default)
            if val is None:
                return None
            if isinstance(val, str):
                return val
            return json.dumps(val, default=str)

        enabled_at = config.get("enabled_at")
        if enabled_at is None:
            enabled_at = existing.get("enabled_at")
        if mode == "TRADE" and enabled_at is None:
            enabled_at = now
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO trading_asset_config(
                        id, symbol, asset_type, provider, mode,
                        max_allocation_amount, max_portfolio_percentage,
                        max_position_size, max_trades_per_day, minimum_confidence,
                        cooldown_seconds, allowed_directions, allowed_timeframes,
                        stop_loss_policy, take_profit_policy, enabled_at,
                        updated_at, config_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        asset_type=excluded.asset_type,
                        provider=excluded.provider,
                        mode=excluded.mode,
                        max_allocation_amount=excluded.max_allocation_amount,
                        max_portfolio_percentage=excluded.max_portfolio_percentage,
                        max_position_size=excluded.max_position_size,
                        max_trades_per_day=excluded.max_trades_per_day,
                        minimum_confidence=excluded.minimum_confidence,
                        cooldown_seconds=excluded.cooldown_seconds,
                        allowed_directions=excluded.allowed_directions,
                        allowed_timeframes=excluded.allowed_timeframes,
                        stop_loss_policy=excluded.stop_loss_policy,
                        take_profit_policy=excluded.take_profit_policy,
                        enabled_at=excluded.enabled_at,
                        updated_at=excluded.updated_at,
                        config_json=excluded.config_json
                    """,
                    (
                        row_id,
                        symbol,
                        config.get("asset_type") or existing.get("asset_type"),
                        config.get("provider") or existing.get("provider"),
                        mode,
                        config.get("max_allocation_amount", existing.get("max_allocation_amount")),
                        config.get(
                            "max_portfolio_percentage",
                            existing.get("max_portfolio_percentage"),
                        ),
                        config.get("max_position_size", existing.get("max_position_size")),
                        config.get("max_trades_per_day", existing.get("max_trades_per_day")),
                        config.get("minimum_confidence", existing.get("minimum_confidence")),
                        config.get("cooldown_seconds", existing.get("cooldown_seconds")),
                        _json_field("allowed_directions"),
                        _json_field("allowed_timeframes"),
                        config.get("stop_loss_policy", existing.get("stop_loss_policy")),
                        config.get("take_profit_policy", existing.get("take_profit_policy")),
                        enabled_at,
                        now,
                        _json_field("config_json", {}) or "{}",
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_trading_asset_config(symbol) or {}

    def ensure_default_asset_configs(
        self,
        symbols: list[str] | tuple[str, ...],
        positions: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Safe migration defaults: open position → CLOSE_ONLY, else MONITOR_ONLY.

        Never defaults a symbol to TRADE.
        """
        from ..time_utils import normalize_symbol

        positions = positions or {}
        open_syms = {
            normalize_symbol(sym)
            for sym, pos in positions.items()
            if pos is not None
            and (
                (isinstance(pos, dict) and float(pos.get("quantity") or 0) > 0)
                or (hasattr(pos, "quantity") and float(getattr(pos, "quantity") or 0) > 0)
            )
        }
        out: list[dict[str, Any]] = []
        for raw in symbols:
            symbol = normalize_symbol(str(raw))
            if not symbol:
                continue
            existing = self.get_trading_asset_config(symbol)
            if existing:
                out.append(existing)
                continue
            mode = "CLOSE_ONLY" if symbol in open_syms else "MONITOR_ONLY"
            asset_type = "crypto" if "-" in symbol else "stock"
            row = self.set_trading_asset_config(
                {
                    "symbol": symbol,
                    "asset_type": asset_type,
                    "mode": mode,
                    "allowed_directions": ["BUY", "SELL"],
                    "allowed_timeframes": ["1m", "5m", "15m", "1h", "4h", "1d"],
                }
            )
            out.append(row)
        return out

    def _asset_config_row(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}

        def _parse_json_field(raw: Any, default: Any) -> Any:
            if raw is None:
                return default
            if isinstance(raw, (list, dict)):
                return raw
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return default

        return {
            "id": row["id"],
            "symbol": row["symbol"],
            "asset_type": row["asset_type"],
            "provider": row["provider"],
            "mode": row["mode"],
            "max_allocation_amount": row["max_allocation_amount"],
            "max_portfolio_percentage": row["max_portfolio_percentage"],
            "max_position_size": row["max_position_size"],
            "max_trades_per_day": row["max_trades_per_day"],
            "minimum_confidence": row["minimum_confidence"],
            "cooldown_seconds": row["cooldown_seconds"],
            "allowed_directions": _parse_json_field(row["allowed_directions"], None),
            "allowed_timeframes": _parse_json_field(row["allowed_timeframes"], None),
            "stop_loss_policy": row["stop_loss_policy"],
            "take_profit_policy": row["take_profit_policy"],
            "enabled_at": row["enabled_at"],
            "updated_at": row["updated_at"],
            "config_json": _parse_json_field(row["config_json"], {}),
        }

    def _ann_row(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        coords_raw = row["coordinates"] or "{}"
        try:
            coordinates = json.loads(coords_raw)
        except json.JSONDecodeError:
            coordinates = {}
        if not isinstance(coordinates, dict):
            coordinates = {}
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "symbol": row["symbol"],
            "timeframe_scope": row["timeframe_scope"],
            "annotation_type": row["annotation_type"],
            "coordinates": coordinates,
            "price": row["price"],
            "label": row["label"],
            "note": row["note"],
            "color": row["color"],
            "line_style": row["line_style"],
            "importance": row["importance"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "active": bool(row["active"]),
        }


def validate_annotation_payload(payload: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    """Validate and sanitize annotation fields. Rejects executable content."""
    if not isinstance(payload, dict):
        raise ValueError("annotation must be an object")

    symbol = str(payload.get("symbol") or "").upper().strip()
    if not symbol or len(symbol) > 32:
        raise ValueError("invalid symbol")

    ann_type = str(payload.get("annotation_type") or payload.get("type") or "").upper()
    if ann_type not in ANNOTATION_TYPES:
        raise ValueError(f"annotation_type must be one of {sorted(ANNOTATION_TYPES)}")

    scope = str(payload.get("timeframe_scope") or "all").lower()
    if scope not in TIMEFRAME_SCOPES:
        raise ValueError(f"timeframe_scope must be one of {sorted(TIMEFRAME_SCOPES)}")

    coords = payload.get("coordinates") or {}
    if not isinstance(coords, dict):
        raise ValueError("coordinates must be an object")
    # Keep only JSON-safe primitives / lists of numbers.
    safe_coords: dict[str, Any] = {}
    for k, v in list(coords.items())[:20]:
        key = str(k)[:40]
        if isinstance(v, (int, float, str, bool)) or v is None:
            if isinstance(v, str):
                safe_coords[key] = v[:200]
            else:
                safe_coords[key] = v
        elif isinstance(v, list):
            safe_coords[key] = [
                float(x) if isinstance(x, (int, float)) else str(x)[:40] for x in v[:20]
            ]

    price = payload.get("price")
    if price is not None:
        price = float(price)
        if not (price == price) or abs(price) > 1e12:  # NaN check
            raise ValueError("invalid price")

    importance = str(payload.get("importance") or "medium").lower()
    if importance not in IMPORTANCE_LEVELS:
        raise ValueError("importance must be low|medium|high")

    line_style = str(payload.get("line_style") or "solid").lower()
    if line_style not in LINE_STYLES:
        raise ValueError("line_style must be solid|dashed|dotted")

    color = payload.get("color")
    if color is not None:
        color = str(color)[:32]
        if not color.startswith("#") and color.lower() not in {
            "red",
            "green",
            "blue",
            "yellow",
            "orange",
            "white",
            "gray",
            "grey",
        }:
            # Allow hex or simple named colors only.
            if not (color.startswith("#") and 4 <= len(color) <= 9):
                color = "#3dd6c6"

    label = payload.get("label")
    if label is not None:
        label = str(label)[:80]
        if "<" in label or ">" in label or "javascript:" in label.lower():
            raise ValueError("label contains disallowed content")

    note = payload.get("note")
    if note is not None:
        note = str(note)[:500]
        if "<script" in note.lower() or "javascript:" in note.lower():
            raise ValueError("note contains disallowed content")

    out = {
        "symbol": symbol,
        "annotation_type": ann_type,
        "timeframe_scope": scope,
        "coordinates": safe_coords,
        "price": price,
        "label": label,
        "note": note,
        "color": color or ("#3dd6c6" if ann_type == "SUPPORT" else "#e85d5d"),
        "line_style": line_style,
        "importance": importance,
        "user_id": (str(payload["user_id"])[:64] if payload.get("user_id") else None),
        "active": bool(payload.get("active", True)),
    }
    if not partial and ann_type in {"SUPPORT", "RESISTANCE"} and price is None:
        # Allow price from coordinates.price
        if "price" in safe_coords and isinstance(safe_coords["price"], (int, float)):
            out["price"] = float(safe_coords["price"])
        else:
            raise ValueError("SUPPORT/RESISTANCE require price")
    return out


_db: MarketDB | None = None
_db_lock = threading.Lock()


def get_market_db() -> MarketDB:
    global _db
    with _db_lock:
        if _db is None:
            _db = MarketDB()
        return _db
