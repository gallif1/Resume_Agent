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
                    """
                )
                conn.commit()
            finally:
                conn.close()

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
