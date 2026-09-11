"""Per-asset trading modes and portfolio-level controls (paper only).

Modes:
  DISABLED     — no analysis, no orders
  MONITOR_ONLY — analyze + log signals, never open/increase
  TRADE        — paper orders allowed within limits
  CLOSE_ONLY   — only reduce/close existing positions

Defaults are never TRADE (see ``ensure_default_asset_configs``).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .config import DATA_DIR, DEFAULT_SYMBOLS, STARTING_CASH
from .market_data.candle_store import get_market_db
from .time_utils import normalize_symbol


class TradingAssetMode(str, Enum):
    DISABLED = "DISABLED"
    MONITOR_ONLY = "MONITOR_ONLY"
    TRADE = "TRADE"
    CLOSE_ONLY = "CLOSE_ONLY"


@dataclass
class PortfolioControls:
    pause_new_entries: bool = False
    close_only_global: bool = False
    max_positions: int | None = None
    max_exposure: float | None = None  # absolute notional
    max_daily_loss: float | None = None
    max_trades_per_day: int | None = None
    daily_realized_pnl: float = 0.0
    daily_trade_count: int = 0
    day_key: str | None = None  # UTC YYYY-MM-DD
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "PortfolioControls":
        raw = raw or {}
        return cls(
            pause_new_entries=bool(raw.get("pause_new_entries", False)),
            close_only_global=bool(raw.get("close_only_global", False)),
            max_positions=(
                int(raw["max_positions"]) if raw.get("max_positions") is not None else None
            ),
            max_exposure=(
                float(raw["max_exposure"]) if raw.get("max_exposure") is not None else None
            ),
            max_daily_loss=(
                float(raw["max_daily_loss"]) if raw.get("max_daily_loss") is not None else None
            ),
            max_trades_per_day=(
                int(raw["max_trades_per_day"])
                if raw.get("max_trades_per_day") is not None
                else None
            ),
            daily_realized_pnl=float(raw.get("daily_realized_pnl") or 0.0),
            daily_trade_count=int(raw.get("daily_trade_count") or 0),
            day_key=raw.get("day_key"),
            updated_at=float(raw.get("updated_at") or time.time()),
        )


_controls_lock = threading.RLock()
_controls: PortfolioControls | None = None
_CONTROLS_PATH = DATA_DIR / "portfolio_controls.json"


def _controls_path() -> Path:
    return _CONTROLS_PATH


def load_portfolio_controls() -> PortfolioControls:
    global _controls
    with _controls_lock:
        if _controls is not None:
            return _controls
        path = _controls_path()
        # Prefer dedicated file; fall back to runtime_state.json key.
        raw: dict[str, Any] | None = None
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                raw = None
        if raw is None:
            state_path = DATA_DIR / "runtime_state.json"
            if state_path.is_file():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    if isinstance(state.get("portfolio_controls"), dict):
                        raw = state["portfolio_controls"]
                except Exception:  # noqa: BLE001
                    raw = None
        _controls = PortfolioControls.from_dict(raw)
        _roll_daily_counters(_controls)
        return _controls


def save_portfolio_controls(controls: PortfolioControls | None = None) -> PortfolioControls:
    global _controls
    with _controls_lock:
        if controls is not None:
            _controls = controls
        if _controls is None:
            _controls = PortfolioControls()
        _controls.updated_at = time.time()
        _roll_daily_counters(_controls)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = _controls_path()
        path.write_text(
            json.dumps(_controls.to_dict(), indent=2),
            encoding="utf-8",
        )
        # Also mirror into runtime_state.json when present (non-destructive).
        state_path = DATA_DIR / "runtime_state.json"
        try:
            state: dict[str, Any] = {}
            if state_path.is_file():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    state = {}
            state["portfolio_controls"] = _controls.to_dict()
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return _controls


def update_portfolio_controls(patch: dict[str, Any]) -> PortfolioControls:
    current = load_portfolio_controls()
    merged = current.to_dict()
    for key in (
        "pause_new_entries",
        "close_only_global",
        "max_positions",
        "max_exposure",
        "max_daily_loss",
        "max_trades_per_day",
        "daily_realized_pnl",
        "daily_trade_count",
        "day_key",
    ):
        if key in patch:
            merged[key] = patch[key]
    return save_portfolio_controls(PortfolioControls.from_dict(merged))


def _roll_daily_counters(controls: PortfolioControls) -> None:
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if controls.day_key != today:
        controls.day_key = today
        controls.daily_realized_pnl = 0.0
        controls.daily_trade_count = 0


def get_asset_mode(symbol: str) -> TradingAssetMode:
    symbol = normalize_symbol(symbol)
    db = get_market_db()
    row = db.get_trading_asset_config(symbol)
    if not row:
        # Safe default — never TRADE.
        return TradingAssetMode.MONITOR_ONLY
    try:
        return TradingAssetMode(str(row.get("mode") or "MONITOR_ONLY").upper())
    except ValueError:
        return TradingAssetMode.MONITOR_ONLY


def can_analyze(symbol: str) -> bool:
    return get_asset_mode(symbol) != TradingAssetMode.DISABLED


def _position_qty(portfolio: Any, symbol: str) -> float:
    if portfolio is None:
        return 0.0
    positions = getattr(portfolio, "positions", None)
    if isinstance(portfolio, dict):
        positions = portfolio.get("positions") or {}
    if not positions:
        return 0.0
    pos = positions.get(symbol)
    if pos is None:
        return 0.0
    if isinstance(pos, dict):
        return float(pos.get("quantity") or 0)
    return float(getattr(pos, "quantity", 0) or 0)


def _portfolio_equity(portfolio: Any, mark_prices: dict[str, float] | None = None) -> float:
    cash = float(getattr(portfolio, "cash", None) or (portfolio or {}).get("cash") or STARTING_CASH)
    positions = getattr(portfolio, "positions", None)
    if isinstance(portfolio, dict):
        positions = portfolio.get("positions") or {}
    equity = cash
    mark_prices = mark_prices or {}
    for sym, pos in (positions or {}).items():
        qty = float(pos.get("quantity") if isinstance(pos, dict) else getattr(pos, "quantity", 0) or 0)
        avg = float(pos.get("avg_price") if isinstance(pos, dict) else getattr(pos, "avg_price", 0) or 0)
        px = float(mark_prices.get(sym) or avg or 0)
        equity += qty * px
    return equity


def can_open_order(
    symbol: str,
    side: str,
    quantity: float | None,
    price: float | None,
    confidence: float | None = None,
    portfolio: Any = None,
    *,
    timeframe: str | None = None,
    trades_today: int | None = None,
) -> tuple[bool, str]:
    """Return (ok, hebrew_reason). Enforce before paper fill."""
    symbol = normalize_symbol(symbol)
    side_u = str(side or "").upper()
    if side_u not in {"BUY", "SELL"}:
        return False, "פעולה לא נתמכת לביצוע נייר."

    controls = load_portfolio_controls()
    _roll_daily_counters(controls)

    if controls.max_daily_loss is not None and controls.daily_realized_pnl <= -abs(
        float(controls.max_daily_loss)
    ):
        return False, "הגעת למגבלת הפסד יומי בתיק — כניסות חדשות חסומות."

    mode = get_asset_mode(symbol)
    if mode == TradingAssetMode.DISABLED:
        return False, f"{symbol} מושבת — לא ניתן לבצע הזמנות."

    if mode == TradingAssetMode.MONITOR_ONLY:
        return False, f"{symbol} במצב מעקב בלבד — ביצוע הזמנות חסום."

    db = get_market_db()
    cfg = db.get_trading_asset_config(symbol) or {}
    pos_qty = _position_qty(portfolio, symbol)

    # CLOSE_ONLY / global close-only: only allow reducing exposure.
    close_only = mode == TradingAssetMode.CLOSE_ONLY or controls.close_only_global
    if close_only:
        if side_u == "BUY":
            return False, f"{symbol} במצב סגירה בלבד — הגדלת פוזיציה חסומה."
        if pos_qty <= 0:
            return False, f"{symbol} במצב סגירה בלבד — אין פוזיציה לסגירה."
        # SELL that reduces is OK (continue other checks lightly).

    if controls.pause_new_entries and side_u == "BUY" and pos_qty <= 0:
        return False, "כניסות חדשות מושהות ברמת התיק."

    if mode == TradingAssetMode.TRADE or close_only:
        allowed_dirs = cfg.get("allowed_directions")
        if isinstance(allowed_dirs, list) and allowed_dirs:
            dirs = {str(x).upper() for x in allowed_dirs}
            if side_u not in dirs and not (close_only and side_u == "SELL"):
                return False, f"כיוון {side_u} אינו מורשה עבור {symbol}."

        allowed_tfs = cfg.get("allowed_timeframes")
        if timeframe and isinstance(allowed_tfs, list) and allowed_tfs:
            tfs = {str(x).lower() for x in allowed_tfs}
            if timeframe.lower() not in tfs:
                return False, f"מסגרת זמן {timeframe} אינה מורשית עבור {symbol}."

        min_conf = cfg.get("minimum_confidence")
        if min_conf is not None and confidence is not None:
            if float(confidence) < float(min_conf):
                return (
                    False,
                    f"ביטחון {float(confidence):.0%} נמוך מהמינימום "
                    f"{float(min_conf):.0%} עבור {symbol}.",
                )

        max_trades = cfg.get("max_trades_per_day")
        if max_trades is None:
            max_trades = controls.max_trades_per_day
        today_count = trades_today if trades_today is not None else controls.daily_trade_count
        if max_trades is not None and int(today_count) >= int(max_trades):
            return False, f"הגעת למגבלת עסקאות יומית עבור {symbol}."

        # Position / allocation caps for increasing trades.
        qty = float(quantity or 0)
        px = float(price or 0)
        notional = qty * px if qty and px else 0.0

        if side_u == "BUY" and not close_only:
            if controls.max_positions is not None and pos_qty <= 0:
                open_count = 0
                positions = getattr(portfolio, "positions", None)
                if isinstance(portfolio, dict):
                    positions = portfolio.get("positions") or {}
                for s, p in (positions or {}).items():
                    q = float(p.get("quantity") if isinstance(p, dict) else getattr(p, "quantity", 0) or 0)
                    if q > 0:
                        open_count += 1
                if open_count >= int(controls.max_positions):
                    return False, "הגעת למספר המקסימלי של פוזיציות פתוחות."

            max_pos = cfg.get("max_position_size")
            if max_pos is not None and qty > 0 and (pos_qty + qty) > float(max_pos):
                return False, f"גודל פוזיציה חורג מהמגבלה עבור {symbol}."

            max_alloc = cfg.get("max_allocation_amount")
            if max_alloc is not None and notional > 0 and notional > float(max_alloc):
                return False, f"הקצאה חורגת מהמגבלה עבור {symbol}."

            max_pct = cfg.get("max_portfolio_percentage")
            if max_pct is not None and notional > 0 and portfolio is not None:
                equity = _portfolio_equity(portfolio, {symbol: px} if px else None)
                if equity > 0 and (notional / equity) > float(max_pct):
                    return False, f"אחוז מהתיק חורג מהמגבלה עבור {symbol}."

            if controls.max_exposure is not None and notional > 0:
                # Approximate current exposure as sum of |qty|*mark.
                exposure = 0.0
                positions = getattr(portfolio, "positions", None)
                if isinstance(portfolio, dict):
                    positions = portfolio.get("positions") or {}
                for s, p in (positions or {}).items():
                    q = float(p.get("quantity") if isinstance(p, dict) else getattr(p, "quantity", 0) or 0)
                    avg = float(
                        p.get("avg_price") if isinstance(p, dict) else getattr(p, "avg_price", 0) or 0
                    )
                    mark = px if s == symbol and px else avg
                    exposure += abs(q * mark)
                if exposure + notional > float(controls.max_exposure):
                    return False, "חשיפת התיק חורגת מהמגבלה."

    return True, ""


def ensure_defaults_for_runtime(symbols: list[str] | tuple[str, ...] | None = None, portfolio: Any = None) -> list[dict[str, Any]]:
    """Ensure every watched symbol has a safe default asset config."""
    syms = list(symbols or DEFAULT_SYMBOLS)
    positions = getattr(portfolio, "positions", None) if portfolio is not None else None
    if isinstance(portfolio, dict):
        positions = portfolio.get("positions")
    return get_market_db().ensure_default_asset_configs(syms, positions or {})


def estimate_paper_quantity(portfolio: Any, side: str, price: float, confidence: float = 0.5) -> float:
    """Mirror DecisionEngine sizing so permission checks use realistic qty."""
    side_u = str(side).upper()
    px = float(price or 0)
    if px <= 0:
        return 0.0
    cash = float(getattr(portfolio, "cash", None) or (portfolio or {}).get("cash") or 0)
    if side_u == "BUY":
        notional = cash * 0.02
        if notional < 1:
            return 0.0
        return notional / px
    # SELL
    # Need symbol — caller should prefer passing quantity; this is a fallback.
    return 0.0
