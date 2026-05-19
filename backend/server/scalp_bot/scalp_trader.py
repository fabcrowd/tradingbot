"""Position lifecycle manager for the scalp bot.

Opens positions on signals, tracks them, handles stop/tp fills via OCO pattern.
Capital-aware: reserves funds before entry, releases on close.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from .trade_history_store import append_trade_history_row, load_trade_history_tail
from .empirical_market_promotion import EmpiricalMarketPromotion
from .signal_engine import ScalpSignal, SignalEngine

if TYPE_CHECKING:
    from ..coinbase_order_manager import CoinbaseOrderManager
    from ..live_order_manager import LiveOrderManager
    from ..session_logger import SessionLogger
    from ..state import BotState
    from .indicators import IndicatorValues
    from .scalp_config import ScalpBotConfig, ScalpPairConfig

LOG = logging.getLogger(__name__)

# Paper/sim same-bar stop+TP: stop takes precedence (see ``_check_paper_exits_one``).
# Vec ``simulate_trades`` / ``simulate_trades_bidir`` / RSI sim: no ``open`` → stop-first; with
# opens, `_intrabar_stop_first(..., side=±1)` picks path (long vs short geometry). Live venue OCO
# ordering is separate — this names bar-path policy.
LIVE_BAR_PATH_EXIT_EVAL_ORDER = ("stop_before_tp_if_both_intrabar",)


def _clamp_protective_stop_for_resting_order(
    direction: str,
    stop_price: float,
    ref_price: float,
    *,
    epsilon_bps: float = 5.0,
) -> tuple[float, bool]:
    """Keep stop-limit trigger on the correct side of the reference for Coinbase CDE perps.

    Long protective is a **sell** with ``STOP_DIRECTION_STOP_DOWN``: the trigger should sit
    **below** the reference (mark). After breakeven + **ratchet-only** trailing, the stored
    stop can remain **above** a later mark if price pulls back — that looks like an impossible
    "stop above the market" but is a stale trail level; the venue may not behave like a
    classic stop. We clamp so the resting order is valid.

    Short protective is a **buy** with ``STOP_DIRECTION_STOP_UP``: trigger should be **above** ref.
    """
    if ref_price <= 0 or stop_price <= 0:
        return round(stop_price, 5), False
    eps = max(abs(ref_price) * epsilon_bps / 10_000.0, 1e-8)
    d = direction.lower()
    if d == "long":
        if stop_price >= ref_price - eps:
            return round(ref_price - eps, 5), True
    elif d == "short":
        if stop_price <= ref_price + eps:
            return round(ref_price + eps, 5), True
    return round(stop_price, 5), False


def _safe_float(x: object) -> float:
    if x is None or x == "":
        return 0.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class _PendingMarketExit:
    """Live market exit cl_ord_id → metadata so fill-poll can emit execution JSONL after close."""

    pair_key: str
    symbol: str
    direction: str
    qty: float
    reason: str
    ref_price: float
    contract_size: float = 1.0


@dataclass
class ScalpPosition:
    pair_key: str
    symbol: str
    direction: str          # "long" | "short"
    entry_price: float
    stop_price: float
    tp_price: float
    qty: float               # number of contracts (Coinbase CDE)
    entry_cl_ord_id: str
    strategy_mode: str = ""  # strategy active when entry opened (WFO / config mode)
    contract_size: float = 1.0  # underlying per contract (Coinbase CDE); 1.0 for spot
    stop_cl_ord_id: str = ""
    tp_cl_ord_id: str = ""
    status: str = "pending"     # pending | open | closed
    opened_at: float = field(default_factory=time.time)
    closed_at: float = 0.0
    pnl: float = 0.0
    close_reason: str = ""      # "stop", "tp", "manual", "error"
    mark_price: float = 0.0
    unrealized_pnl: float = 0.0
    liquidation_price: float = 0.0
    leverage: float = 1.0
    funding_rate: float | None = None
    # Signal / placement reference for slip vs VWAP fill (set in try_open).
    entry_signal_price: float = 0.0
    entry_order_type: str = ""  # limit | market | sim_instant | …
    # Accumulated venue fee (USD) across partial entry fills; logged at entry complete.
    entry_fill_fee_usd: float = 0.0
    # Pending entry only: accumulate venue fill legs for VWAP when entry fills in parts.
    entry_fill_cost: float = 0.0
    entry_fill_qty: float = 0.0
    # Break-even / trailing stop state
    breakeven_hit: bool = False    # True once stop has been moved to entry
    trail_active: bool = False     # True once trailing has started
    # Live limit entry: actual resting limit price (signal.entry_price ± offset).
    pending_limit_price: float = 0.0
    # Partial take-profit state (paper/sim only; live logs a warning)
    tp1_done: bool = False         # True once the first partial close has fired
    runner_qty: float = 0.0        # remaining qty after TP1 partial close
    runner_stop: float = 0.0       # breakeven stop for the runner leg
    # Extra ``_reserved_capital`` after live stop+TP rest (heuristic margin for protectives).
    margin_reserve_addon: float = 0.0
    # Intrabar mark extremes vs entry (for strategy report MFE / MAE).
    excursion_max_usd: float = 0.0
    excursion_min_usd: float = 0.0
    excursion_initialized: bool = False


class ScalpTrader:
    """Manages open scalp positions and interfaces with LiveOrderManager for fills."""

    def __init__(
        self,
        state: "BotState",
        cfg: "ScalpBotConfig",
        signal_engine: SignalEngine,
        live_mgr: "LiveOrderManager | CoinbaseOrderManager | None",
        session_logger: "SessionLogger | None" = None,
    ) -> None:
        self._state = state
        self._cfg = cfg
        self._signal_engine = signal_engine
        self._live_mgr = live_mgr
        self._session_log = session_logger
        # Multiple concurrent legs per pair_key are allowed; dict key is entry_cl_ord_id (unique).
        self._positions: dict[str, ScalpPosition] = {}
        # Market exit client ids (time stop / RSI / counter) → entry_cl_ord_id for fill routing.
        self._market_exit_entry_link: dict[str, str] = {}
        # Live: position is closed before the market fill arrives — keep meta for scalp_fill_execution.
        self._pending_market_exits: dict[str, _PendingMarketExit] = {}
        # Live counter-reversal: open opposite leg after exit fill (exit cl_ord_id → try_open args).
        self._pending_counter_reversals: dict[str, dict] = {}
        self._daily_pnl: float = 0.0
        self._daily_reset_day: int = 0
        self._reserved_capital: float = 0.0
        _th_max = max(1, int(getattr(cfg, "trade_history_max_entries", 500) or 500))
        self._trade_history: deque[dict] = deque(maxlen=_th_max)
        if bool(getattr(cfg, "persist_trade_history", True)):
            try:
                for row in load_trade_history_tail(_th_max):
                    self._trade_history.append(row)
            except Exception:
                LOG.exception("ScalpTrader: load persisted trade_history failed")
        # Running cumulative / trade count per strategy_mode (rebuilt from deque).
        self._strategy_trade_index: dict[str, int] = defaultdict(int)
        self._strategy_cumulative_pnl: dict[str, float] = defaultdict(float)
        self._sync_strategy_totals_from_history()
        self._sim_mode: bool = False
        # Per-pair backoff after protective reconcile / IF rejects (reduces cancel+replace storms).
        self._protective_reconcile_backoff_until: dict[str, float] = {}
        self._protective_if_streak: dict[str, int] = defaultdict(int)
        self._protective_circuit_until: dict[str, float] = {}
        # Set by ScalpRuntime: when True, block new entries (operator standby / go-live gate).
        self._entries_paused_fn: Callable[[], bool] | None = None
        # Set by ScalpRuntime: invoked once per UTC day when daily loss limit is breached.
        self._daily_loss_breach_fn: Callable[[], None] | None = None
        self._daily_loss_breach_notified: bool = False
        self._empirical = EmpiricalMarketPromotion(cfg)

    def note_entry_ttl_cancel_for_empirical(
        self,
        pair_key: str,
        symbol: str,
        direction: str,
        limit_px: float,
        mark_at_cancel: float,
    ) -> None:
        self._empirical.note_entry_ttl_cancel(
            pair_key,
            symbol,
            direction,
            limit_px,
            mark_at_cancel,
            session_log=self._session_log,
        )

    def _venue(self) -> str:
        return str(getattr(self._cfg, "venue", "coinbase_perps") or "coinbase_perps").strip().lower()

    def _coinbase_perps(self) -> bool:
        return self._venue() == "coinbase_perps"

    def _pnl_mult(self, pair_cfg: "ScalpPairConfig") -> float:
        """Dollar PnL per 1.0 price move: qty × contract_size (contracts × underlying)."""
        return float(pair_cfg.contract_size) if self._coinbase_perps() else 1.0

    def _sync_strategy_totals_from_history(self) -> None:
        """Rebuild per-strategy trade count and cumulative PnL from closed-leg deque (exit_ts order)."""
        self._strategy_trade_index.clear()
        self._strategy_cumulative_pnl.clear()
        ordered = sorted(self._trade_history, key=lambda r: float(r.get("exit_ts") or 0))
        for row in ordered:
            m = str(row.get("strategy_mode") or "unknown")
            self._strategy_trade_index[m] += 1
            self._strategy_cumulative_pnl[m] += float(row.get("pnl") or 0)

    @staticmethod
    def _pct_vs_notional(usd: float, notional: float) -> float:
        if notional <= 1e-12:
            return 0.0
        return float(usd) / float(notional) * 100.0

    def _note_excursion(self, pos: ScalpPosition, unrealized: float) -> None:
        u = float(unrealized)
        if not pos.excursion_initialized:
            pos.excursion_max_usd = u
            pos.excursion_min_usd = u
            pos.excursion_initialized = True
            return
        if u > pos.excursion_max_usd:
            pos.excursion_max_usd = u
        if u < pos.excursion_min_usd:
            pos.excursion_min_usd = u

    @staticmethod
    def _strategy_entry_signal(direction: str) -> str:
        return "Long" if str(direction).lower() == "long" else "Short"

    @staticmethod
    def _strategy_exit_signal(direction: str) -> str:
        return "Long Exit" if str(direction).lower() == "long" else "Short Exit"

    @staticmethod
    def _slip_bps_for_entry(direction: str, ref: float, fill: float) -> float | None:
        if ref <= 0:
            return None
        d = direction.lower()
        if d == "long":
            return (fill - ref) / ref * 10_000.0
        return (ref - fill) / ref * 10_000.0

    @staticmethod
    def _slip_bps_for_exit(direction: str, ref: float, fill: float) -> float | None:
        if ref <= 0:
            return None
        d = direction.lower()
        # Closing a long is a sell — worse if fill is below reference.
        if d == "long":
            return (ref - fill) / ref * 10_000.0
        return (fill - ref) / ref * 10_000.0

    def _emit_scalp_fill_execution(
        self,
        *,
        leg: str,
        pair_key: str,
        symbol: str,
        direction: str,
        order_type: str,
        placed_ts: float | None,
        fill_ts: float,
        fill_price: float,
        qty: float,
        fee_usd: float | None,
        reference_price: float | None,
        slip_bps: float | None,
        cl_ord_id: str,
        close_reason: str | None = None,
        strategy_mode: str = "",
        contract_size: float = 1.0,
        simulated: bool = False,
        exchange_trade_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> None:
        if self._session_log is None:
            return
        row: dict[str, object] = {
            "leg": leg,
            "pair_key": pair_key,
            "symbol": symbol,
            "direction": direction,
            "order_type": order_type,
            "placed_ts": placed_ts,
            "fill_ts": fill_ts,
            "fill_price": round(fill_price, 8),
            "qty": round(qty, 8),
            "fee_usd": None if fee_usd is None else round(float(fee_usd), 8),
            "reference_price": None if reference_price is None else round(float(reference_price), 8),
            "slip_bps": None if slip_bps is None else round(float(slip_bps), 6),
            "cl_ord_id": cl_ord_id[:80],
            "strategy_mode": strategy_mode or "",
            "contract_size": round(float(contract_size), 8),
            "simulated": simulated,
        }
        if close_reason is not None:
            row["close_reason"] = close_reason
        if exchange_trade_id:
            row["exchange_trade_id"] = str(exchange_trade_id)[:80]
        if exchange_order_id:
            row["exchange_order_id"] = str(exchange_order_id)[:80]
        self._session_log.log_scalp("scalp_fill_execution", **row)
        cb = getattr(self, "_slip_observation_cb", None)
        if (
            callable(cb)
            and leg == "entry"
            and slip_bps is not None
            and slip_bps == slip_bps  # finite (NaN != NaN)
        ):
            try:
                cb(float(slip_bps))
            except Exception:
                LOG.debug("slip_observation_cb failed", exc_info=True)

    def register_pending_market_exit(
        self,
        cl_ord_id: str,
        pos: ScalpPosition,
        reason: str,
        ref_price: float,
    ) -> None:
        oq = max(1, int(round(pos.qty))) if self._coinbase_perps() else round(pos.qty, 8)
        self._pending_market_exits[cl_ord_id] = _PendingMarketExit(
            pair_key=pos.pair_key,
            symbol=pos.symbol,
            direction=pos.direction,
            qty=float(oq),
            reason=str(reason)[:64],
            ref_price=float(ref_price),
            contract_size=float(pos.contract_size or 1.0),
        )

    def close_position_from_market_exit(
        self,
        pos: ScalpPosition,
        fill_price: float,
        reason: str,
    ) -> None:
        """Close an open leg after a live market exit fill is confirmed on the venue."""
        if pos.status != "open":
            return
        mult = pos.contract_size if self._coinbase_perps() else 1.0
        if pos.direction == "long":
            pnl = (fill_price - pos.entry_price) * pos.qty * mult
        else:
            pnl = (pos.entry_price - fill_price) * pos.qty * mult
        self._close_position(pos, pnl, reason, fill_price)
        if pnl < 0:
            self._signal_engine.record_loss(pos.pair_key)
        else:
            self._signal_engine.record_win(pos.pair_key)
        LOG.info(
            "ScalpTrader %s: closed via market fill (%s) @ %.5f | pnl=%.4f",
            pos.pair_key, reason, fill_price, pnl,
        )

    def on_market_exit_fill(
        self,
        cl_ord_id: str,
        fill_price: float,
        fill_qty: float,
        *,
        fee_usd: float | None = None,
        exchange_trade_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> _PendingMarketExit | None:
        meta = self._pending_market_exits.pop(cl_ord_id, None)
        if meta is None:
            return None
        slip = self._slip_bps_for_exit(meta.direction, meta.ref_price, fill_price)
        self._emit_scalp_fill_execution(
            leg="exit",
            pair_key=meta.pair_key,
            symbol=meta.symbol,
            direction=meta.direction,
            order_type="market",
            placed_ts=None,
            fill_ts=time.time(),
            fill_price=fill_price,
            qty=fill_qty,
            fee_usd=fee_usd,
            reference_price=meta.ref_price,
            slip_bps=slip,
            cl_ord_id=cl_ord_id,
            close_reason=meta.reason,
            strategy_mode="",
            contract_size=meta.contract_size,
            simulated=False,
            exchange_trade_id=exchange_trade_id,
            exchange_order_id=exchange_order_id,
        )
        return meta

    def _live_market_exit_inflight_for_entry(self, entry_cl_ord_id: str) -> bool:
        """True if a reduce-only market exit was already submitted and we are awaiting its fill."""
        return entry_cl_ord_id in self._market_exit_entry_link.values()

    async def _submit_live_market_exit(
        self,
        pos: ScalpPosition,
        *,
        close_id: str,
        reason: str,
        ref_price: float,
        reduce_only: bool = True,
    ) -> bool:
        """Submit market exit; return True if the venue accepted the order (close deferred until fill)."""
        if self._live_mgr is None or self._sim_mode:
            return False
        if self._live_market_exit_inflight_for_entry(pos.entry_cl_ord_id):
            LOG.warning(
                "ScalpTrader %s: duplicate market exit suppressed — already awaiting venue (%s, entry=%s…)",
                pos.pair_key,
                reason,
                pos.entry_cl_ord_id[:16],
            )
            return True
        self._link_market_exit_order(close_id, pos.entry_cl_ord_id)
        self.register_pending_market_exit(close_id, pos, reason, ref_price)
        close_side = "sell" if pos.direction == "long" else "buy"
        oq = max(1, int(round(pos.qty))) if self._coinbase_perps() else round(pos.qty, 8)
        try:
            flat_fn = getattr(self._live_mgr, "flatten_scalp_leg_market", None)
            if callable(flat_fn):
                result = await flat_fn(
                    symbol=pos.symbol,
                    side=close_side,
                    order_qty=float(oq),
                    cl_ord_id=close_id,
                    reduce_only=reduce_only,
                )
            else:
                params: dict = {
                    "symbol": pos.symbol,
                    "side": close_side,
                    "order_type": "market",
                    "order_qty": oq,
                    "cl_ord_id": close_id,
                }
                if reduce_only:
                    params["reduce_only"] = True
                result = await self._live_mgr.add_order(params=params)
        except Exception:
            LOG.exception("ScalpTrader: market exit submit failed for %s", pos.pair_key)
            self._pending_market_exits.pop(close_id, None)
            self._market_exit_entry_link.pop(close_id, None)
            self._state.record_exchange_error(
                "error",
                "Scalp market exit failed",
                f"{pos.pair_key}: {reason} — check venue position",
                "scalp_protective",
            )
            return False
        if not result:
            self._pending_market_exits.pop(close_id, None)
            self._market_exit_entry_link.pop(close_id, None)
            LOG.error(
                "ScalpTrader %s: market exit rejected (%s) — position stays open until reconcile",
                pos.pair_key,
                reason,
            )
            self._state.record_exchange_error(
                "error",
                "Scalp market exit rejected",
                f"{pos.pair_key}: {reason} — venue may still hold size",
                "scalp_protective",
            )
            return False
        LOG.warning(
            "ScalpTrader %s: market exit submitted (%s) cl=%s — awaiting fill before close",
            pos.pair_key,
            reason,
            close_id[:20],
        )
        return True

    def _log_entry_fill_execution(
        self,
        pos: ScalpPosition,
        fill_price: float,
        fill_qty: float,
        *,
        order_type_override: str | None = None,
        fee_usd: float | None = None,
        exchange_trade_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> None:
        ref = float(pos.pending_limit_price) if pos.pending_limit_price > 0 else float(pos.entry_signal_price or 0.0)
        if ref <= 0:
            ref = float(fill_price)
        slip = self._slip_bps_for_entry(pos.direction, ref, fill_price)
        ot = order_type_override or (pos.entry_order_type or "unknown")
        fee = fee_usd
        if fee is None and pos.entry_fill_fee_usd > 0:
            fee = pos.entry_fill_fee_usd
        self._emit_scalp_fill_execution(
            leg="entry",
            pair_key=pos.pair_key,
            symbol=pos.symbol,
            direction=pos.direction,
            order_type=str(ot),
            placed_ts=float(pos.opened_at),
            fill_ts=time.time(),
            fill_price=fill_price,
            qty=fill_qty,
            fee_usd=fee,
            reference_price=ref if ref > 0 else None,
            slip_bps=slip,
            cl_ord_id=pos.entry_cl_ord_id,
            strategy_mode=pos.strategy_mode or "",
            contract_size=float(pos.contract_size or 1.0),
            simulated=self._sim_mode or self._live_mgr is None,
            exchange_trade_id=exchange_trade_id,
            exchange_order_id=exchange_order_id,
        )

    def _log_exit_fill_execution_protective(
        self,
        pos: ScalpPosition,
        filled_cl_ord_id: str,
        fill_price: float,
        *,
        fee_usd: float | None = None,
        exchange_trade_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> None:
        is_stop = filled_cl_ord_id == pos.stop_cl_ord_id
        ref = float(pos.stop_price if is_stop else pos.tp_price)
        slip = self._slip_bps_for_exit(pos.direction, ref, fill_price)
        self._emit_scalp_fill_execution(
            leg="exit",
            pair_key=pos.pair_key,
            symbol=pos.symbol,
            direction=pos.direction,
            order_type="stop_loss_limit" if is_stop else "take_profit_limit",
            placed_ts=None,
            fill_ts=time.time(),
            fill_price=fill_price,
            qty=float(pos.qty),
            fee_usd=fee_usd,
            reference_price=ref,
            slip_bps=slip,
            cl_ord_id=filled_cl_ord_id,
            close_reason="stop" if is_stop else "tp",
            strategy_mode=pos.strategy_mode or "",
            contract_size=float(pos.contract_size or 1.0),
            simulated=False,
            exchange_trade_id=exchange_trade_id,
            exchange_order_id=exchange_order_id,
        )

    def close_open_leg_from_orphan_venue_fill(
        self,
        pos: ScalpPosition,
        fill_price: float,
        fill_qty: float,
        *,
        fill_side: str,
        fee_usd: float | None = None,
        exchange_trade_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> bool:
        """Close an open leg when the venue fill has no ``scalp_*`` client order id."""
        if pos.status != "open":
            return False
        side = str(fill_side or "").strip().lower()
        need = "sell" if pos.direction == "long" else "buy"
        if side != need:
            return False
        self._emit_scalp_fill_execution(
            leg="exit",
            pair_key=pos.pair_key,
            symbol=pos.symbol,
            direction=pos.direction,
            order_type="market",
            placed_ts=None,
            fill_ts=time.time(),
            fill_price=fill_price,
            qty=fill_qty,
            fee_usd=fee_usd,
            reference_price=float(pos.entry_price),
            slip_bps=self._slip_bps_for_exit(pos.direction, float(pos.entry_price), fill_price),
            cl_ord_id=f"orphan_{pos.entry_cl_ord_id}"[:80],
            close_reason="exchange_orphan_fill",
            strategy_mode=pos.strategy_mode or "",
            contract_size=float(pos.contract_size or 1.0),
            simulated=False,
            exchange_trade_id=exchange_trade_id,
            exchange_order_id=exchange_order_id,
        )
        self.close_position_from_market_exit(pos, fill_price, "exchange_orphan_fill")
        return True

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def sim_mode(self) -> bool:
        return self._sim_mode

    @sim_mode.setter
    def sim_mode(self, value: bool) -> None:
        self._sim_mode = value

    @property
    def open_position_count(self) -> int:
        return sum(1 for p in self._positions.values() if p.status in ("pending", "open"))

    @property
    def daily_pnl(self) -> float:
        self._maybe_reset_daily()
        return self._daily_pnl

    def forward_pnl_since(self, pair_key: str, since_ts: float) -> float:
        """Sum realized PnL for closed trades on ``pair_key`` with exit_ts >= since_ts."""
        total = 0.0
        for row in self._trade_history:
            if row.get("pair_key") != pair_key:
                continue
            ex = float(row.get("exit_ts") or 0.0)
            if ex >= since_ts:
                total += float(row.get("pnl") or 0.0)
        return total

    def forward_trades_since(self, pair_key: str, since_ts: float) -> int:
        """Count closed trades on ``pair_key`` with exit_ts >= since_ts."""
        count = 0
        for row in self._trade_history:
            if row.get("pair_key") != pair_key:
                continue
            ex = float(row.get("exit_ts") or 0.0)
            if ex >= since_ts:
                count += 1
        return count

    def positions_for_pair(self, pair_key: str) -> list[ScalpPosition]:
        return [p for p in self._positions.values() if p.pair_key == pair_key]

    def position_by_entry(self, entry_cl_ord_id: str) -> ScalpPosition | None:
        return self._positions.get(entry_cl_ord_id)

    def position_for_client_order(self, pair_key: str, cl_ord_id: str) -> ScalpPosition | None:
        if not cl_ord_id:
            return None
        for p in self._positions.values():
            if p.pair_key != pair_key:
                continue
            if cl_ord_id == p.entry_cl_ord_id:
                return p
            if cl_ord_id in (p.stop_cl_ord_id, p.tp_cl_ord_id):
                return p
        return None

    def _link_market_exit_order(self, exit_cl_ord_id: str, entry_cl_ord_id: str) -> None:
        self._market_exit_entry_link[exit_cl_ord_id] = entry_cl_ord_id

    def take_market_exit_entry_link(self, exit_cl_ord_id: str) -> str | None:
        return self._market_exit_entry_link.pop(exit_cl_ord_id, None)

    def pop_pending_counter_reversal(self, exit_cl_ord_id: str) -> dict | None:
        return self._pending_counter_reversals.pop(exit_cl_ord_id, None)

    def update_position_mark(self, pair_key: str, mark: float) -> None:
        """Mark-to-market for open/pending legs and empirical missed-move watches."""
        if mark <= 0:
            return
        for pos in self.positions_for_pair(pair_key):
            pos.mark_price = mark
            if pos.status == "open":
                mult = pos.contract_size if self._coinbase_perps() else 1.0
                if pos.direction == "long":
                    pos.unrealized_pnl = (mark - pos.entry_price) * pos.qty * mult
                else:
                    pos.unrealized_pnl = (pos.entry_price - mark) * pos.qty * mult
                self._note_excursion(pos, pos.unrealized_pnl)
                if self._coinbase_perps() and pos.liquidation_price > 0 and mark > 0:
                    thr = float(getattr(self._cfg, "liquidation_warn_pct", 5.0) or 5.0)
                    liq = pos.liquidation_price
                    dist_pct = abs(mark - liq) / max(mark, 1e-12) * 100.0
                    if dist_pct <= thr:
                        self._state.push_alert(
                            "warning",
                            f"Scalp near liquidation {pair_key}",
                            f"mark={mark:.4f} liq={liq:.4f} (~{dist_pct:.2f}% away)",
                            "scalp_perps",
                        )
        self._empirical.on_pair_mark(pair_key, mark, session_log=self._session_log)

    def has_position(self, pair_key: str) -> bool:
        return any(p.status in ("pending", "open") for p in self.positions_for_pair(pair_key))

    def has_open_position(self, pair_key: str) -> bool:
        return any(p.status == "open" for p in self.positions_for_pair(pair_key))

    def get_position(self, pair_key: str) -> ScalpPosition | None:
        """Arbitrary primary leg for pair (oldest open, else oldest pending) — prefer explicit entry id when possible."""
        active = [
            p for p in self.positions_for_pair(pair_key)
            if p.status in ("pending", "open")
        ]
        if not active:
            return None
        active.sort(key=lambda p: p.opened_at)
        opens = [p for p in active if p.status == "open"]
        return opens[0] if opens else active[0]

    def _entry_margin_usd(self, pos: ScalpPosition) -> float:
        lev = max(1.0, float(getattr(self._cfg, "max_leverage", 1.0)))
        if self._coinbase_perps():
            notional = float(pos.qty) * float(pos.contract_size) * float(pos.entry_price or 0.0)
        else:
            notional = float(pos.qty) * float(pos.entry_price or 0.0)
        return max(0.0, notional / lev)

    def _futures_margin_headroom_usd(self) -> float | None:
        mgr = self._live_mgr
        if mgr is None:
            return None
        fn = getattr(mgr, "futures_available_margin_usd", None)
        if callable(fn):
            return fn()
        bal_fn = getattr(mgr, "balance_snapshot", None)
        if not callable(bal_fn):
            return None
        fut = bal_fn().get("futures")
        if not isinstance(fut, dict):
            return None
        try:
            return float(fut.get("available_margin") or 0.0)
        except (TypeError, ValueError):
            return None

    def _margin_ok_for_second_protective(self, pos: ScalpPosition) -> bool:
        """Heuristic: enough free futures margin to rest a second protective (TP after stop)."""
        headroom = self._futures_margin_headroom_usd()
        if headroom is None:
            return True
        need = self._entry_margin_usd(pos)
        buf = float(getattr(self._cfg, "buying_power_buffer_usd", 0.0) or 0.0)
        return headroom >= max(need, buf)

    def _protective_if_backoff_sec(self) -> float:
        return max(
            30.0,
            float(getattr(self._cfg, "insufficient_funds_cooldown_sec", 300.0) or 300.0) / 5.0,
        )

    def _protective_circuit_open(self, pair_key: str) -> bool:
        return time.time() < float(self._protective_circuit_until.get(pair_key, 0.0))

    def _note_protective_order_failure(self, pair_key: str) -> None:
        """Trip a per-pair circuit after repeated insufficient-funds on protectives."""
        reason = str(getattr(self._state, "last_order_reject_reason", "") or "")
        if "INSUFFICIENT" not in reason.upper() or "FUND" not in reason.upper():
            return
        self._protective_if_streak[pair_key] = int(self._protective_if_streak.get(pair_key, 0)) + 1
        streak = self._protective_if_streak[pair_key]
        if streak < 3:
            return
        pause = max(
            600.0,
            float(getattr(self._cfg, "insufficient_funds_cooldown_sec", 300.0) or 300.0) * 6.0,
        )
        self._protective_circuit_until[pair_key] = time.time() + pause
        self._protective_reconcile_backoff_until[pair_key] = self._protective_circuit_until[pair_key]
        self._state.push_alert(
            "error",
            f"Protective circuit open: {pair_key}",
            f"{streak} consecutive insufficient-funds on stop/TP/exit — "
            f"pausing reconcile for {int(pause // 60)} min. "
            "Check CFM futures margin and cancel duplicate resting orders on Coinbase.",
            "scalp_protective",
            persistent=True,
        )
        LOG.error(
            "ScalpTrader %s: protective IF circuit OPEN (%d strikes, pause %.0fs) — %s",
            pair_key,
            streak,
            pause,
            reason[:200],
        )

    def _note_protective_order_success(self, pair_key: str) -> None:
        self._protective_if_streak[pair_key] = 0

    async def _sync_protectives_from_venue_cache(self, pos: ScalpPosition) -> tuple[bool, bool]:
        """Adopt resting stop/TP already on Coinbase; cancel duplicate stops."""
        mgr = self._live_mgr
        if mgr is None:
            return False, False
        fn = getattr(mgr, "resting_protectives_for_product", None)
        if not callable(fn):
            return False, False
        close_side = "buy" if pos.direction == "short" else "sell"
        stops, tps = fn(pos.symbol, close_side)
        stop_ok = bool(stops)
        tp_ok = bool(tps)
        if stops:
            best = max(
                stops,
                key=lambda o: (
                    _safe_float(o.get("trigger_price") or o.get("limit_price") or 0),
                    str(o.get("order_id") or ""),
                ),
            )
            cid = str(best.get("client_order_id") or "").strip()
            if not cid:
                cid = f"venue_stop_{str(best.get('order_id') or '')[:12]}"
            pos.stop_cl_ord_id = cid
            tr = _safe_float(best.get("trigger_price") or 0)
            if tr > 0:
                pos.stop_price = round(tr, 5)
            if len(stops) > 1:
                LOG.warning(
                    "ScalpTrader %s: %d duplicate resting stop(s) on venue — cancelling extras",
                    pos.pair_key,
                    len(stops) - 1,
                )
                cancel_oid = getattr(mgr, "cancel_exchange_order_id", None)
                if callable(cancel_oid):
                    for extra in stops:
                        if extra is best:
                            continue
                        oid = str(extra.get("order_id") or "")
                        if oid:
                            await cancel_oid(oid)
        if tps:
            best_tp = tps[-1]
            cid = str(best_tp.get("client_order_id") or "").strip()
            if not cid:
                cid = f"venue_tp_{str(best_tp.get('order_id') or '')[:12]}"
            pos.tp_cl_ord_id = cid
            lp = _safe_float(best_tp.get("limit_price") or 0)
            if lp > 0:
                pos.tp_price = round(lp, 5)
        return stop_ok, tp_ok

    def _release_reserved_for_position(self, pos: ScalpPosition) -> None:
        mult = pos.contract_size if self._coinbase_perps() else 1.0
        _lev_rel = max(1.0, float(getattr(self._cfg, "max_leverage", 1.0)))
        entry_part = float(pos.qty) * float(pos.entry_price) * mult / _lev_rel
        addon = float(getattr(pos, "margin_reserve_addon", 0.0) or 0.0)
        self._reserved_capital = max(0.0, self._reserved_capital - entry_part - addon)

    async def _cancel_coinbase_protectives(self, pos: ScalpPosition) -> None:
        """Best-effort cancel of resting stop/TP tied to this position (e.g. after exchange resync)."""
        if self._live_mgr is None or self._sim_mode:
            return
        for cl_id in (pos.stop_cl_ord_id, pos.tp_cl_ord_id):
            if not cl_id:
                continue
            try:
                await self._live_mgr.cancel_order(cl_id)
            except Exception:
                LOG.debug("ScalpTrader: cancel protective %s failed", cl_id[:16], exc_info=True)

    async def _place_protective_orders_coinbase(
        self,
        pos: ScalpPosition,
        *,
        place_tp: bool = True,
    ) -> tuple[bool, bool]:
        """Place stop + TP on Coinbase for an already-open ``ScalpPosition``."""
        pair_key = pos.pair_key
        if pos.status != "open":
            return False, False
        if self._live_mgr is None or self._sim_mode:
            return True, True

        oq = max(1, int(round(pos.qty))) if self._coinbase_perps() else round(pos.qty, 8)
        stop_side = "sell" if pos.direction == "long" else "buy"
        ref_px = float(pos.mark_price or 0.0) or float(pos.entry_price or 0.0)
        # epsilon_bps=30: 30 bps from fill price gives headroom for the 5-10s fill-lag window on
        # low-ATR pairs (e.g. XRP ATR≈0.001 = 7 bps; default 5 bps was narrower than one tick move).
        stp, stp_clamped = _clamp_protective_stop_for_resting_order(
            pos.direction, float(pos.stop_price), ref_px,
            epsilon_bps=30.0,
        )
        if stp_clamped:
            LOG.warning(
                "ScalpTrader %s: stop trigger %.5f was at/through ref %.5f — clamped to %.5f "
                "(ratchet stop vs current mark; see breakeven/trailing)",
                pair_key, pos.stop_price, ref_px, stp,
            )
            pos.stop_price = stp
        stop_ok = False
        try:
            stop_ok = bool(
                await self._live_mgr.add_order(params={
                    "symbol": pos.symbol,
                    "side": stop_side,
                    "order_type": "stop-loss-limit",
                    "trigger_price": round(pos.stop_price, 5),
                    "limit_price": round(
                        pos.stop_price * (0.9995 if pos.direction == "long" else 1.0005),
                        5,
                    ),
                    "order_qty": oq,
                    "cl_ord_id": pos.stop_cl_ord_id,
                })
            )
        except Exception:
            LOG.exception("ScalpTrader: failed to place stop for %s", pair_key)
        if not stop_ok:
            # Retry once with a wider safety margin (100 bps from fill price) to handle cases where
            # price moved significantly during fill lag (e.g. PREVIEW_STOP_PRICE_ABOVE_LAST_TRADE_PRICE).
            stp_retry, _ = _clamp_protective_stop_for_resting_order(
                pos.direction, float(pos.stop_price), ref_px,
                epsilon_bps=100.0,
            )
            if stp_retry != round(pos.stop_price, 5):
                LOG.warning(
                    "ScalpTrader %s: stop rejected — retrying with safety-clamped stop %.5f (was %.5f, ref=%.5f)",
                    pair_key, stp_retry, pos.stop_price, ref_px,
                )
                pos.stop_price = stp_retry
                try:
                    stop_ok = bool(
                        await self._live_mgr.add_order(params={
                            "symbol": pos.symbol,
                            "side": stop_side,
                            "order_type": "stop-loss-limit",
                            "trigger_price": round(pos.stop_price, 5),
                            "limit_price": round(
                                pos.stop_price * (0.9995 if pos.direction == "long" else 1.0005),
                                5,
                            ),
                            "order_qty": oq,
                            "cl_ord_id": pos.stop_cl_ord_id,
                        })
                    )
                except Exception:
                    LOG.exception("ScalpTrader: retry stop placement failed for %s", pair_key)
        if not stop_ok:
            LOG.error(
                "ScalpTrader %s: stop order NOT accepted by exchange (naked position risk) id=%s",
                pair_key, pos.stop_cl_ord_id,
            )
            self._state.record_exchange_error(
                "error",
                "Scalp stop not placed",
                f"{pair_key}: exchange rejected or empty ack for stop {pos.stop_cl_ord_id[:20]}…",
                "scalp_protective",
            )
            self._note_protective_order_failure(pair_key)
        else:
            self._note_protective_order_success(pair_key)

        if not place_tp:
            return stop_ok, True

        tp_side = "sell" if pos.direction == "long" else "buy"
        tp_ok = False
        if stop_ok and not self._margin_ok_for_second_protective(pos):
            LOG.warning(
                "ScalpTrader %s: deferring TP — futures available_margin below entry margin "
                "(stop resting; will retry on reconcile)",
                pair_key,
            )
            return stop_ok, False
        if stop_ok:
            await asyncio.sleep(0.75)
        try:
            tp_ok = bool(
                await self._live_mgr.add_order(params={
                    "symbol": pos.symbol,
                    "side": tp_side,
                    "order_type": "take-profit-limit",
                    "trigger_price": round(pos.tp_price, 5),
                    "limit_price": round(
                        pos.tp_price * (0.9998 if pos.direction == "long" else 1.0002),
                        5,
                    ),
                    "order_qty": oq,
                    "cl_ord_id": pos.tp_cl_ord_id,
                })
            )
        except Exception:
            LOG.exception("ScalpTrader: failed to place tp for %s", pair_key)
        if not tp_ok:
            LOG.error(
                "ScalpTrader %s: take-profit order NOT accepted by exchange id=%s",
                pair_key, pos.tp_cl_ord_id,
            )
            self._state.record_exchange_error(
                "warning",
                "Scalp TP not placed",
                f"{pair_key}: exchange rejected or empty ack for TP {pos.tp_cl_ord_id[:20]}…",
                "scalp_protective",
            )
            self._note_protective_order_failure(pair_key)
        elif tp_ok:
            self._note_protective_order_success(pair_key)
        return stop_ok, tp_ok

    async def _place_take_profit_coinbase(self, pos: ScalpPosition) -> bool:
        """Place take-profit only (live); used when initial TP failed but stop succeeded."""
        pair_key = pos.pair_key
        if pos.status != "open" or self._live_mgr is None or self._sim_mode:
            return False
        oq = max(1, int(round(pos.qty))) if self._coinbase_perps() else round(pos.qty, 8)
        tp_side = "sell" if pos.direction == "long" else "buy"
        tp_ok = False
        try:
            tp_ok = bool(
                await self._live_mgr.add_order(params={
                    "symbol": pos.symbol,
                    "side": tp_side,
                    "order_type": "take-profit-limit",
                    "trigger_price": round(pos.tp_price, 5),
                    "limit_price": round(
                        pos.tp_price * (0.9998 if pos.direction == "long" else 1.0002),
                        5,
                    ),
                    "order_qty": oq,
                    "cl_ord_id": pos.tp_cl_ord_id,
                })
            )
        except Exception:
            LOG.exception("ScalpTrader: failed to place TP retry for %s", pair_key)
        if not tp_ok:
            LOG.warning(
                "ScalpTrader %s: TP retry NOT accepted id=%s",
                pair_key, pos.tp_cl_ord_id,
            )
        return tp_ok

    async def _flatten_live_after_protective_failure(
        self,
        pos: ScalpPosition,
        reason: str,
        ref_price: float,
    ) -> None:
        """Submit reduce-only market exit after stop/TP failure; close only when fill confirms."""
        pair_key = pos.pair_key
        if self._live_mgr is None or self._sim_mode:
            return
        await self._cancel_coinbase_protectives(pos)
        close_id = f"scalp_prot_{uuid.uuid4().hex[:8]}"
        await self._submit_live_market_exit(
            pos,
            close_id=close_id,
            reason=reason,
            ref_price=ref_price,
            reduce_only=True,
        )

    async def ensure_coinbase_protectives_match_exchange(self, pair_key: str) -> None:
        """If resting stop/TP are missing on Coinbase, re-place only the missing leg(s).

        Covers the case where the bot has an open leg but venue TP/SL were never attached or
        were cancelled without a fill event (UI shows ``Add`` for TP/SL).
        """
        if not self._coinbase_perps() or self._live_mgr is None or self._sim_mode:
            return
        if self._protective_circuit_open(pair_key):
            return
        if time.time() < float(self._protective_reconcile_backoff_until.get(pair_key, 0.0)):
            return
        mgr = self._live_mgr
        is_open = getattr(mgr, "is_resting_protective_open", None)
        if not callable(is_open):
            return
        for pos in self.positions_for_pair(pair_key):
            if pos.status != "open":
                continue
            pid = str(pos.symbol or "").strip()
            if not pid:
                continue
            stop_ok = bool(await is_open(pos.stop_cl_ord_id, pid))
            tp_ok = bool(await is_open(pos.tp_cl_ord_id, pid))
            if not stop_ok or not tp_ok:
                v_stop, v_tp = await self._sync_protectives_from_venue_cache(pos)
                stop_ok = stop_ok or v_stop
                tp_ok = tp_ok or v_tp
                if v_stop or v_tp:
                    LOG.info(
                        "ScalpTrader %s: adopted venue resting protectives (stop=%s tp=%s)",
                        pair_key,
                        stop_ok,
                        tp_ok,
                    )
            if stop_ok and tp_ok:
                continue
            LOG.warning(
                "ScalpTrader %s: reconcile found missing resting protectives (stop_ok=%s tp_ok=%s) — re-placing",
                pair_key,
                stop_ok,
                tp_ok,
            )
            placed_stop = stop_ok
            placed_tp = tp_ok
            if stop_ok and not tp_ok:
                pos.tp_cl_ord_id = f"scalp_tp_{uuid.uuid4().hex[:8]}"
                placed_tp = await self._place_take_profit_coinbase(pos)
            elif tp_ok and not stop_ok:
                pos.stop_cl_ord_id = f"scalp_stop_{uuid.uuid4().hex[:8]}"
                placed_stop, _ = await self._place_protective_orders_coinbase(pos, place_tp=False)
            else:
                await self._cancel_coinbase_protectives(pos)
                await asyncio.sleep(0.75)
                pos.stop_cl_ord_id = f"scalp_stop_{uuid.uuid4().hex[:8]}"
                pos.tp_cl_ord_id = f"scalp_tp_{uuid.uuid4().hex[:8]}"
                placed_stop, placed_tp = await self._place_protective_orders_coinbase(pos)
            if not placed_stop:
                self._protective_reconcile_backoff_until[pair_key] = (
                    time.time() + self._protective_if_backoff_sec()
                )
                LOG.error(
                    "ScalpTrader %s: stop rejected again after reconcile re-place — "
                    "flattening (naked position risk, price likely through stop)",
                    pair_key,
                )
                ref_price = float(pos.mark_price or pos.entry_price)
                await self._flatten_live_after_protective_failure(
                    pos, "reconcile_stop_failed", ref_price
                )
                break  # pos closed; no further legs to process
            if not placed_tp:
                self._protective_reconcile_backoff_until[pair_key] = (
                    time.time() + self._protective_if_backoff_sec()
                )

    async def try_open(
        self,
        signal: ScalpSignal,
        pair_cfg: "ScalpPairConfig",
        available_capital: float,
        *,
        execution_risk_mult: float = 1.0,
    ) -> bool:
        """Attempt to open a position for the given signal. Returns True if placed."""
        self._maybe_reset_daily()

        paused = self._entries_paused_fn
        if callable(paused) and paused():
            LOG.info(
                "ScalpTrader %s: try_open suppressed — operator standby",
                signal.pair_key,
            )
            return False

        if self._state.scalp_entries_blocked():
            LOG.debug("ScalpTrader: entries blocked (halt or exchange cooldown) — skipping %s", signal.pair_key)
            return False

        if not self._cfg.enabled:
            LOG.debug("ScalpTrader: scalp disabled (OFF) — skipping %s", signal.pair_key)
            return False

        if signal.direction == "short" and not self._coinbase_perps():
            LOG.debug("ScalpTrader: short signals require venue=coinbase_perps — skipping %s", signal.pair_key)
            return False

        cap = self._cfg.concurrent_open_cap()
        if cap is not None and self.open_position_count >= cap:
            LOG.info("ScalpTrader: max concurrent positions reached (%d)", cap)
            return False

        use_exch = self._live_mgr is not None and not self._sim_mode
        if (
            use_exch
            and bool(getattr(self._cfg, "use_exchange_buying_power_cap", False))
            and self.open_position_count > 0
            and float(available_capital) <= 0.0
        ):
            LOG.info(
                "ScalpTrader %s: skip new entry — no exchange buying power while %d leg(s) open "
                "(exits/protectives continue)",
                signal.pair_key,
                self.open_position_count,
            )
            return False

        # Daily loss check
        daily_loss_limit = self._cfg.allocated_capital_usd * (self._cfg.daily_loss_limit_pct / 100.0)
        if self._daily_pnl < -daily_loss_limit:
            LOG.warning(
                "ScalpTrader: daily loss limit hit (%.2f / -%.2f) — halting for the day",
                self._daily_pnl, daily_loss_limit,
            )
            self._maybe_notify_daily_loss_breach()
            return False

        # Position sizing
        if signal.direction == "long":
            stop_distance = signal.entry_price - signal.stop_price
        else:
            stop_distance = signal.stop_price - signal.entry_price
        if stop_distance <= 0:
            return False
        dollar_risk = self._cfg.allocated_capital_usd * pair_cfg.risk_pct
        cap = float(getattr(self._cfg, "volatility_exec_risk_cap", 2.0))
        erm = max(1.0, min(float(execution_risk_mult), cap))
        if erm > 1.0:
            dollar_risk *= erm
            LOG.info(
                "ScalpTrader %s: execution risk mult ×%.3f (requested %.3f, cap %.3f)",
                signal.pair_key,
                erm,
                float(execution_risk_mult),
                cap,
            )

        # ── Correlation-aware risk scaling ────────────────────────────────────
        # When multiple correlated pairs (same correlation_group) are already open
        # in the same direction, scale down dollar_risk proportionally to avoid
        # concentrated exposure.  e.g. 1 open → half risk, 2 open → third risk.
        corr_group = getattr(pair_cfg, "correlation_group", "")
        if corr_group:
            correlated_open = sum(
                1 for pos in self._positions.values()
                if pos.status in ("pending", "open")
                and pos.direction == signal.direction
                and getattr(self._cfg.pairs.get(pos.pair_key), "correlation_group", "") == corr_group
            )
            if correlated_open > 0:
                dollar_risk = dollar_risk / (1 + correlated_open)
                LOG.info(
                    "ScalpTrader %s: correlation_group=%r correlated_open=%d "
                    "→ dollar_risk scaled to %.2f",
                    signal.pair_key, corr_group, correlated_open, dollar_risk,
                )

        mult = self._pnl_mult(pair_cfg)

        if self._coinbase_perps():
            # qty = integer contracts; $ risk ≈ stop_distance * contracts * contract_size
            contracts = int(dollar_risk // max(stop_distance * mult, 1e-12))
            contracts = max(1, contracts)
            qty = float(contracts)
            notional = qty * mult * signal.entry_price
        else:
            qty = dollar_risk / stop_distance
            notional = qty * signal.entry_price

        # Cap notional to available capital scaled by leverage.
        # On margin products (perps) only the margin is required: margin = notional / leverage.
        lev = max(1.0, float(getattr(self._cfg, "max_leverage", 1.0)))
        free_capital = available_capital - (self._reserved_capital / lev)
        max_nom = getattr(self._cfg, "max_notional_usd_per_pair", None)
        if max_nom is not None and notional > float(max_nom):
            if self._coinbase_perps():
                max_c = int(float(max_nom) // max(mult * signal.entry_price, 1e-12))
                qty = float(max(1, max_c))
                notional = qty * mult * signal.entry_price
            else:
                qty = float(max_nom) / signal.entry_price
                notional = qty * signal.entry_price

        margin_required = notional / lev
        if margin_required > free_capital:
            if free_capital <= 0:
                LOG.info("ScalpTrader: no free capital for %s", signal.pair_key)
                return False
            effective_capital = free_capital * lev  # max notional we can take
            if self._coinbase_perps():
                max_c = int(effective_capital // max(mult * signal.entry_price, 1e-12))
                qty = float(max(1, max_c))
                notional = qty * mult * signal.entry_price
            else:
                qty = effective_capital / signal.entry_price
                notional = qty * signal.entry_price
            margin_required = notional / lev
            LOG.info(
                "ScalpTrader %s: capped qty to capital (notional=%.2f margin=%.2f free=%.2f lev=%.1fx qty=%.6f)",
                signal.pair_key, notional, margin_required, free_capital, lev, qty,
            )

        # Place entry order
        entry_id = f"scalp_entry_{uuid.uuid4().hex[:8]}"
        stop_id = f"scalp_stop_{uuid.uuid4().hex[:8]}"
        tp_id = f"scalp_tp_{uuid.uuid4().hex[:8]}"

        use_exchange = self._live_mgr is not None and not self._sim_mode
        if use_exchange:
            order_type, used_promotion = self._empirical.resolve_order_type(signal.pair_key)
        else:
            ot = str(self._cfg.order_type or "limit").lower().strip()
            order_type = "limit" if ot == "hybrid" else ot
            used_promotion = False
        limit_px_live: float = 0.0
        if use_exchange:
            side = "buy" if signal.direction == "long" else "sell"
            oq = max(1, int(round(qty))) if self._coinbase_perps() else round(qty, 8)
            limit_px: float | None = None
            if order_type == "limit":
                base = float(signal.entry_price)
                off = float(getattr(self._cfg, "entry_limit_offset_bps", 0.0) or 0.0)
                if off > 0.0:
                    m = off / 10_000.0
                    if side == "buy":
                        base *= 1.0 + m
                    else:
                        base *= 1.0 - m
                limit_px = round(base, 5)
                limit_px_live = float(limit_px)

        position = ScalpPosition(
            pair_key=signal.pair_key,
            symbol=signal.symbol,
            direction=signal.direction,
            strategy_mode=str(getattr(signal, "mode", "") or ""),
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            tp_price=signal.tp_price,
            qty=float(max(1, int(round(qty))) if self._coinbase_perps() else qty),
            contract_size=float(pair_cfg.contract_size),
            entry_cl_ord_id=entry_id,
            stop_cl_ord_id=stop_id,
            tp_cl_ord_id=tp_id,
            status="pending",
            leverage=float(getattr(self._cfg, "max_leverage", 1.0)),
            pending_limit_price=float(limit_px_live) if use_exchange and order_type == "limit" else 0.0,
            entry_signal_price=float(signal.entry_price),
            entry_order_type=(
                str(order_type) if use_exchange else "sim_instant"
            ),
        )
        # Register **before** venue submit so a fast market fill cannot race ahead of ``_positions``.
        if use_exchange:
            self._positions[entry_id] = position
            try:
                result = await self._live_mgr.add_order(
                    params={
                        "symbol": signal.symbol,
                        "side": side,
                        "order_type": order_type,
                        "limit_price": limit_px,
                        "order_qty": oq,
                        "cl_ord_id": entry_id,
                    }
                )
            except Exception:
                self._positions.pop(entry_id, None)
                LOG.exception("ScalpTrader: failed to place entry order for %s", signal.pair_key)
                return False
            if not result:
                self._positions.pop(entry_id, None)
                LOG.warning("ScalpTrader %s: exchange rejected order — not tracking position", signal.pair_key)
                return False
            if order_type == "market" and used_promotion:
                LOG.warning(
                    "ScalpTrader %s: empirical promotion — market entry (burst)",
                    signal.pair_key,
                )
                self._empirical.after_promoted_market_entry(signal.pair_key)
                if self._session_log is not None:
                    self._session_log.log_scalp(
                        "entry_market_promoted",
                        pair_key=signal.pair_key,
                        symbol=signal.symbol,
                        direction=signal.direction,
                        qty=round(qty, 8),
                    )
        else:
            self._positions[entry_id] = position
        _lev_pend = max(1.0, float(getattr(self._cfg, "max_leverage", 1.0)))
        self._reserved_capital += notional / _lev_pend

        mode_label = "SIM" if self._sim_mode else ("PAPER" if self._live_mgr is None else "LIVE")
        LOG.info(
            "ScalpTrader %s: %s entry%s | qty=%.6f @ %.5f | stop=%.5f | tp=%.5f | "
            "notional=%.2f | id=%s",
            signal.pair_key,
            mode_label,
            "" if use_exchange else " (simulated)",
            qty, signal.entry_price,
            signal.stop_price, signal.tp_price, notional, entry_id,
        )
        if self._session_log is not None:
            self._session_log.log_scalp(
                "entry_placed",
                pair_key=signal.pair_key,
                symbol=signal.symbol,
                mode=mode_label,
                order_type=order_type if use_exchange else "sim_instant",
                empirical_promoted=bool(use_exchange and used_promotion),
                limit_price=round(limit_px_live, 8) if limit_px_live > 0 else None,
                qty=round(qty, 8),
                entry_price=round(signal.entry_price, 8),
                stop=round(signal.stop_price, 8),
                tp=round(signal.tp_price, 8),
                notional=round(notional, 4),
                cl_ord_id=entry_id,
                sim=self._sim_mode,
            )

        if not use_exchange:
            await self.on_entry_filled(entry_id, float(signal.entry_price), qty)

        return True

    async def on_entry_filled(
        self,
        entry_cl_ord_id: str,
        fill_price: float,
        fill_qty: float,
        *,
        exchange_trade_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> None:
        """Called when the entry order is confirmed filled. Place stop + tp."""
        pos = self._positions.get(entry_cl_ord_id)
        pair_key = pos.pair_key if pos is not None else entry_cl_ord_id
        if pos is None:
            LOG.warning(
                "ScalpTrader %s: on_entry_filled called but no position (protectives skipped)",
                pair_key,
            )
            return
        if pos.status != "pending":
            LOG.warning(
                "ScalpTrader %s: on_entry_filled skipped — status=%s (expected pending); "
                "Coinbase stop/TP will NOT be placed via this path",
                pair_key,
                pos.status,
            )
            return
        pos.status = "open"
        pos.entry_price = fill_price
        pos.qty = fill_qty
        pos.entry_fill_cost = 0.0
        pos.entry_fill_qty = 0.0
        mult = pos.contract_size if self._coinbase_perps() else 1.0
        self._note_excursion(pos, 0.0)

        # Paper/sim mode: stop/tp monitored via check_paper_exits() on each candle close
        if self._live_mgr is None or self._sim_mode:
            mode_label = "SIM" if self._sim_mode else "PAPER"
            LOG.info(
                "ScalpTrader %s: %s entry filled @ %.5f | stop=%.5f | tp=%.5f",
                pair_key, mode_label, fill_price, pos.stop_price, pos.tp_price,
            )
            self._log_entry_fill_execution(
                pos,
                fill_price,
                fill_qty,
                exchange_trade_id=exchange_trade_id,
                exchange_order_id=exchange_order_id,
            )
            return

        self._log_entry_fill_execution(
            pos,
            fill_price,
            fill_qty,
            exchange_trade_id=exchange_trade_id,
            exchange_order_id=exchange_order_id,
        )
        stop_ok, tp_ok = await self._place_protective_orders_coinbase(pos)
        if not stop_ok:
            LOG.error(
                "ScalpTrader %s: stop missing after fill — flattening leg (naked risk)",
                pair_key,
            )
            self._state.record_exchange_error(
                "error",
                "Scalp stop missing — auto flatten",
                f"{pair_key}: reduce-only market exit cl={pos.entry_cl_ord_id[:20]}…",
                "scalp_protective",
            )
            if self._session_log is not None:
                self._session_log.log_scalp(
                    "protective_failure_flatten",
                    pair_key=pair_key,
                    symbol=pos.symbol,
                    reason="protective_failed_stop",
                    entry_cl_ord_id=pos.entry_cl_ord_id,
                    ref_price=round(fill_price, 8),
                    qty=round(fill_qty, 8),
                )
            await self._flatten_live_after_protective_failure(
                pos, "protective_failed_stop", float(fill_price),
            )
            return
        tp_final = tp_ok
        if not tp_ok:
            pos.tp_cl_ord_id = f"scalp_tp_{uuid.uuid4().hex[:8]}"
            tp_final = await self._place_take_profit_coinbase(pos)
            if not tp_final:
                LOG.error(
                    "ScalpTrader %s: TP still missing after retry — flattening (NM-002)",
                    pair_key,
                )
                self._state.record_exchange_error(
                    "error",
                    "Scalp TP missing after retry — auto flatten",
                    f"{pair_key}: reduce-only market exit cl={pos.entry_cl_ord_id[:20]}…",
                    "scalp_protective",
                )
                await self._flatten_live_after_protective_failure(
                    pos, "protective_failed_tp", float(fill_price),
                )
                return
        prm = float(getattr(self._cfg, "protective_margin_reserve_mult", 0.0) or 0.0)
        if prm > 0.0 and stop_ok and tp_final:
            lev = max(1.0, float(getattr(self._cfg, "max_leverage", 1.0)))
            if self._coinbase_perps():
                notional = float(pos.qty) * float(pos.contract_size) * float(pos.entry_price)
            else:
                notional = float(pos.qty) * float(pos.entry_price)
            base_margin = notional / lev
            pos.margin_reserve_addon = base_margin * prm
            self._reserved_capital += pos.margin_reserve_addon
        LOG.info(
            "ScalpTrader %s: entry filled @ %.5f | stop=%s (%s) | tp=%s (%s)",
            pair_key,
            fill_price,
            pos.stop_cl_ord_id,
            "ok" if stop_ok else "FAILED",
            pos.tp_cl_ord_id,
            "ok" if tp_final else "FAILED",
        )

    async def on_exit_filled(
        self,
        pair_key: str,
        filled_cl_ord_id: str,
        fill_price: float,
        *,
        fee_usd: float | None = None,
        exchange_trade_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> None:
        """Called when stop or tp is filled. Cancels the sibling, closes position."""
        pos = self.position_for_client_order(pair_key, filled_cl_ord_id)
        if pos is None or pos.status != "open":
            return
        if filled_cl_ord_id not in (pos.stop_cl_ord_id, pos.tp_cl_ord_id):
            return

        mult = pos.contract_size if self._coinbase_perps() else 1.0
        if pos.direction == "long":
            pnl = (fill_price - pos.entry_price) * pos.qty * mult
        else:
            pnl = (pos.entry_price - fill_price) * pos.qty * mult
        is_stop = filled_cl_ord_id == pos.stop_cl_ord_id
        close_reason = "stop" if is_stop else "tp"

        self._log_exit_fill_execution_protective(
            pos,
            filled_cl_ord_id,
            fill_price,
            fee_usd=fee_usd,
            exchange_trade_id=exchange_trade_id,
            exchange_order_id=exchange_order_id,
        )

        # Cancel the sibling order
        sibling_id = pos.tp_cl_ord_id if is_stop else pos.stop_cl_ord_id
        if sibling_id and self._live_mgr is not None:
            try:
                await self._live_mgr.cancel_order(sibling_id)
            except Exception:
                LOG.debug("ScalpTrader: sibling cancel failed for %s", sibling_id, exc_info=True)

        self._close_position(pos, pnl, close_reason, fill_price)

        if is_stop:
            self._signal_engine.record_loss(pair_key)
        else:
            self._signal_engine.record_win(pair_key)

        LOG.info(
            "ScalpTrader %s: closed via %s @ %.5f | pnl=%.4f | daily_pnl=%.4f",
            pair_key, close_reason, fill_price, pnl, self._daily_pnl,
        )

    def check_time_stop(self, pair_key: str, pair_cfg: "ScalpPairConfig", current_price: float) -> None:
        """Close a position if it has been held longer than max_hold_bars.

        Called on every closed candle. Works for both paper and live modes.
        """
        max_hold_sec = pair_cfg.max_hold_bars * pair_cfg.interval * 60.0
        for pos in list(self.positions_for_pair(pair_key)):
            if pos.status != "open":
                continue
            age = time.time() - pos.opened_at
            if age < max_hold_sec:
                continue

            mult = pos.contract_size if self._coinbase_perps() else 1.0
            if pos.direction == "long":
                pnl = (current_price - pos.entry_price) * pos.qty * mult
            else:
                pnl = (pos.entry_price - current_price) * pos.qty * mult

            if self._live_mgr is not None and not self._sim_mode:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    LOG.error("ScalpTrader: check_time_stop called outside event loop for %s", pair_key)
                    return
                for oid in (pos.stop_cl_ord_id, pos.tp_cl_ord_id):
                    if oid:
                        async def _cancel_with_log(o: str = oid) -> None:
                            try:
                                await self._live_mgr.cancel_order(o)
                            except Exception as exc:  # noqa: BLE001
                                LOG.error("ScalpTrader: cancel_order failed for %s: %s", o[:16], exc)
                        loop.create_task(_cancel_with_log())

                async def _time_stop_exit(
                    p: ScalpPosition = pos,
                    px: float = current_price,
                    pk: str = pair_key,
                ) -> None:
                    close_id = f"scalp_tstop_{uuid.uuid4().hex[:8]}"
                    ok = await self._submit_live_market_exit(
                        p, close_id=close_id, reason="time_stop", ref_price=px, reduce_only=True,
                    )
                    if not ok:
                        LOG.error("ScalpTrader %s: TIME STOP market exit not accepted", pk)

                loop.create_task(_time_stop_exit())
                continue

            self._close_position(pos, pnl, "time_stop", current_price)
            if pnl < 0:
                self._signal_engine.record_loss(pair_key)
            else:
                self._signal_engine.record_win(pair_key)
            LOG.info(
                "ScalpTrader %s: TIME STOP after %.0fs (max=%.0fs) @ %.5f | pnl=%.4f",
                pair_key, age, max_hold_sec, current_price, pnl,
            )

        # Safety net: purge pending positions stuck past 2× entry_limit_ttl_sec.
        # This catches cases where the order manager TTL cancel fired but did not
        # clean up trader._positions, leaving has_position() stuck True indefinitely.
        pending_ttl = float(getattr(self._cfg, "entry_limit_ttl_sec", 0.0) or 0.0)
        if pending_ttl > 0:
            stale_threshold = pending_ttl * 2.0
            for pos in list(self.positions_for_pair(pair_key)):
                if pos.status != "pending":
                    continue
                age = time.time() - pos.opened_at
                if age < stale_threshold:
                    continue
                LOG.warning(
                    "ScalpTrader %s: STALE PENDING purged (age=%.0fs > 2×TTL=%.0fs) — "
                    "unblocking future entries",
                    pair_key, age, stale_threshold,
                )
                if self._live_mgr is not None and not self._sim_mode:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self._live_mgr.cancel_order(pos.entry_cl_ord_id))
                    except RuntimeError:
                        pass
                self._release_reserved_for_position(pos)
                try:
                    del self._positions[pos.entry_cl_ord_id]
                except KeyError:
                    pass

    def check_rsi_exit(self, pair_key: str, current_price: float) -> None:
        """Close position via RSI sell trigger (RSI crossed above sell threshold).

        Called from the runtime when the indicator detects rsi_sell_trigger=True.
        """
        for pos in list(self.positions_for_pair(pair_key)):
            if pos.status != "open":
                continue

            mult = pos.contract_size if self._coinbase_perps() else 1.0
            if pos.direction == "long":
                pnl = (current_price - pos.entry_price) * pos.qty * mult
            else:
                pnl = (pos.entry_price - current_price) * pos.qty * mult

            if self._live_mgr is not None and not self._sim_mode:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    LOG.error("ScalpTrader: check_rsi_exit called outside event loop for %s", pair_key)
                    return
                for oid in (pos.stop_cl_ord_id, pos.tp_cl_ord_id):
                    if oid:
                        async def _cancel_with_log_rsi(o: str = oid) -> None:
                            try:
                                await self._live_mgr.cancel_order(o)
                            except Exception as exc:  # noqa: BLE001
                                LOG.error("ScalpTrader: cancel_order failed for %s: %s", o[:16], exc)
                        loop.create_task(_cancel_with_log_rsi())

                async def _rsi_exit(
                    p: ScalpPosition = pos,
                    px: float = current_price,
                    pk: str = pair_key,
                ) -> None:
                    close_id = f"scalp_rsi_{uuid.uuid4().hex[:8]}"
                    ok = await self._submit_live_market_exit(
                        p, close_id=close_id, reason="rsi_exit", ref_price=px, reduce_only=True,
                    )
                    if not ok:
                        LOG.error("ScalpTrader %s: RSI exit market order not accepted", pk)

                loop.create_task(_rsi_exit())
                continue

            self._close_position(pos, pnl, "rsi_exit", current_price)
            if pnl < 0:
                self._signal_engine.record_loss(pair_key)
            else:
                self._signal_engine.record_win(pair_key)
            LOG.info(
                "ScalpTrader %s: RSI EXIT @ %.5f | pnl=%.4f | daily_pnl=%.4f",
                pair_key, current_price, pnl, self._daily_pnl,
            )

    @staticmethod
    def _reversal_score(counter_signal: "ScalpSignal", iv: "IndicatorValues", pos: "ScalpPosition") -> int:
        """Score how strongly the indicators support a full reversal (0–4).

        Each condition represents an independent dimension of conviction:
          +1  high signal confidence (all four DavidTech indicators aligned)
          +1  strong trend in new direction (ADX confirms momentum, not a pullback)
          +1  WAE momentum has clearly flipped (counter-momentum > 2× current-direction momentum)
          +1  price has crossed the T3 moving average (structural regime shift, not a wick)

        Score interpretation:
          0–1  skip — noise or marginal setup, not worth acting on
          2    exit only — indicator environment has degraded, take the scratch profit and wait
          3–4  full reversal — all systems aligned in the new direction
        """
        score = 0
        is_long = pos.direction == "long"

        # 1. Confidence: signal engine already aggregated confluence (t3+hlc+wae+adx all pointing counter)
        if counter_signal.confidence >= 0.85:
            score += 1

        # 2. ADX: strong trend in counter direction (not a mean-reversion noise spike)
        if iv.adx > 28:
            score += 1

        # 3. WAE momentum: counter-momentum significantly dominates original-direction momentum
        if is_long and iv.wae_up > 0 and iv.wae_down > 2.0 * iv.wae_up:
            score += 1
        elif not is_long and iv.wae_down > 0 and iv.wae_up > 2.0 * iv.wae_down:
            score += 1

        # 4. T3 cross: price has crossed the T3 MA, confirming a structural shift rather than a wick
        if is_long and iv.close < iv.t3:
            score += 1
        elif not is_long and iv.close > iv.t3:
            score += 1

        return score

    async def check_counter_signal(
        self,
        pair_key: str,
        pair_cfg: "ScalpPairConfig",
        counter_signal: "ScalpSignal",
        iv: "IndicatorValues",
        available_capital: float,
        *,
        execution_risk_mult: float = 1.0,
        position: ScalpPosition | None = None,
        allow_reversal: bool = True,
    ) -> None:
        """Evaluate a counter-direction signal while a position is open, and decide
        autonomously whether to exit early, reverse, or do nothing.

        Decision logic (no manual config required):

          Score 0–1: skip — not enough conviction, let the existing stop/TP work.
          Score 2:   exit only — indicators have degraded, close at the adjusted stop
                     level and wait for the next clean entry.
          Score 3–4: full reversal — all four dimensions aligned, close and immediately
                     open in the counter direction.

        Hard gate: ``breakeven_hit`` must be True. We never close a position at a loss
        to chase a new signal — the trailing/break-even system must have already
        guaranteed a scratch-or-better outcome.
        """
        pos = position if position is not None else self.get_position(pair_key)
        if pos is None or pos.status != "open":
            return

        if not pos.breakeven_hit:
            LOG.debug(
                "ScalpTrader %s: counter-signal (%s, conf=%.2f) — breakeven not hit, skipping",
                pair_key, counter_signal.direction, counter_signal.confidence,
            )
            return

        score = self._reversal_score(counter_signal, iv, pos)
        if score < 2:
            LOG.debug(
                "ScalpTrader %s: counter-signal score=%d/4 — insufficient conviction, skipping",
                pair_key, score,
            )
            return

        do_reversal = score >= 3 and self._coinbase_perps()  # shorts only available on perps
        mult = self._pnl_mult(pair_cfg)
        current_price = counter_signal.entry_price
        pnl = (
            (current_price - pos.entry_price) * pos.qty * mult if pos.direction == "long"
            else (pos.entry_price - current_price) * pos.qty * mult
        )

        LOG.info(
            "ScalpTrader %s: COUNTER-SIGNAL %s (%s→%s) score=%d/4 conf=%.2f "
            "adx=%.1f wae_up=%.4f wae_down=%.4f | pnl=%.4f @ %.5f",
            pair_key,
            "REVERSAL" if do_reversal else "EXIT",
            pos.direction, counter_signal.direction,
            score, counter_signal.confidence,
            iv.adx, iv.wae_up, iv.wae_down,
            pnl, current_price,
        )

        # Cancel resting stop + TP, then close with a market order
        if self._live_mgr is not None and not self._sim_mode:
            for oid in (pos.stop_cl_ord_id, pos.tp_cl_ord_id):
                if oid:
                    try:
                        await self._live_mgr.cancel_order(oid)
                    except Exception:
                        LOG.debug("ScalpTrader: counter cancel %s failed", oid[:20], exc_info=True)
            close_id = f"scalp_ctr_{uuid.uuid4().hex[:8]}"
            exit_reason = "counter_reversal" if do_reversal else "counter_exit"
            ok = await self._submit_live_market_exit(
                pos,
                close_id=close_id,
                reason=exit_reason,
                ref_price=current_price,
                reduce_only=True,
            )
            if not ok:
                return
            if do_reversal:
                self._pending_counter_reversals[close_id] = {
                    "signal": counter_signal,
                    "pair_cfg": pair_cfg,
                    "available_capital": available_capital,
                    "execution_risk_mult": execution_risk_mult,
                    "allow_reversal": allow_reversal,
                    "pair_key": pair_key,
                }
            return

        reason = "counter_reversal" if do_reversal else "counter_exit"
        self._close_position(pos, pnl, reason, current_price)
        if pnl < 0:
            self._signal_engine.record_loss(pair_key)
        else:
            self._signal_engine.record_win(pair_key)

        if do_reversal:
            rev_paused = self._entries_paused_fn
            if callable(rev_paused) and rev_paused():
                LOG.warning(
                    "ScalpTrader %s: reversal suppressed — operator standby (position closed only)",
                    pair_key,
                )
                return
            if not allow_reversal:
                LOG.debug(
                    "ScalpTrader %s: reversal suppressed — require_champion_to_trade gate (NM-012)",
                    pair_key,
                )
                return
            LOG.info(
                "ScalpTrader %s: opening reversal → %s @ %.5f",
                pair_key, counter_signal.direction, counter_signal.entry_price,
            )
            await self.try_open(
                counter_signal,
                pair_cfg,
                available_capital,
                execution_risk_mult=execution_risk_mult,
            )

    async def check_trail_and_breakeven(
        self,
        pos: ScalpPosition,
        pair_cfg: "ScalpPairConfig",
        current_price: float,
        atr: float,
    ) -> None:
        """Ratchet the stop-loss upward (for longs) or downward (for shorts) as price moves in
        our favour.

        Two-phase logic — both phases disabled by default (trigger = 0.0):

        Phase 1 — Break-even: once unrealised profit >= ``breakeven_atr_trigger × ATR``, move
        the stop to ``entry ± breakeven_buffer_bps`` so the worst-case outcome is a scratch trade
        that covers fees.

        Phase 2 — Trailing: once unrealised profit >= ``trail_atr_trigger × ATR``, trail the stop
        ``trail_atr_distance × ATR`` behind the current price.  The stop only ever ratchets in the
        profitable direction — it never moves against the position.

        For live Coinbase the old stop order is cancelled and a new one is placed at the updated
        level.  For paper/sim the stop_price is updated in memory and the next ``check_paper_exits``
        call will use the new level.
        """
        if atr <= 0:
            return
        pair_key = pos.pair_key
        if pos.status != "open":
            return

        is_long = pos.direction == "long"
        profit_per_contract = (
            (current_price - pos.entry_price) if is_long
            else (pos.entry_price - current_price)
        )
        profit_atr = profit_per_contract / atr  # how many ATRs in profit we are

        be_trigger = float(pair_cfg.breakeven_atr_trigger)
        trail_trigger = float(pair_cfg.trail_atr_trigger)
        trail_dist = float(pair_cfg.trail_atr_distance)
        buffer_bps = float(pair_cfg.breakeven_buffer_bps)

        # Guard: trailing must require more profit than break-even to avoid both
        # phases activating on the same candle from a misconfigured ratio.
        if trail_trigger > 0 and be_trigger > 0 and trail_trigger <= be_trigger:
            LOG.warning(
                "ScalpTrader %s: trail_atr_trigger (%.2f) <= breakeven_atr_trigger (%.2f) — "
                "disabling trailing to prevent phase collision",
                pair_key, trail_trigger, be_trigger,
            )
            trail_trigger = 0.0

        new_stop: float | None = None
        reason: str = ""

        # Phase 1 — break-even
        if be_trigger > 0 and not pos.breakeven_hit and profit_atr >= be_trigger:
            buffer = pos.entry_price * buffer_bps / 10_000.0
            candidate = (pos.entry_price + buffer) if is_long else (pos.entry_price - buffer)
            # Only move if it's strictly better than the current stop
            if (is_long and candidate > pos.stop_price) or (not is_long and candidate < pos.stop_price):
                new_stop = candidate
                reason = f"breakeven (profit={profit_atr:.2f}× ATR)"
                pos.breakeven_hit = True

        # Phase 2 — trailing
        if trail_trigger > 0 and profit_atr >= trail_trigger:
            trail_level = (current_price - trail_dist * atr) if is_long else (current_price + trail_dist * atr)
            # Only ratchet — never move stop against the position
            current_stop = new_stop if new_stop is not None else pos.stop_price
            if (is_long and trail_level > current_stop) or (not is_long and trail_level < current_stop):
                new_stop = trail_level
                pos.trail_active = True
                reason = f"trail (profit={profit_atr:.2f}× ATR, trail_dist={trail_dist}× ATR)"

        if new_stop is None:
            return

        old_stop = pos.stop_price
        pos.stop_price = round(new_stop, 5)
        stp, stp_clamped = _clamp_protective_stop_for_resting_order(
            pos.direction, float(pos.stop_price), float(current_price),
        )
        if stp_clamped:
            LOG.warning(
                "ScalpTrader %s: after %s, stop %.5f at/through ref %.5f — clamped to %.5f",
                pair_key, reason, pos.stop_price, current_price, stp,
            )
            pos.stop_price = stp
            reason = f"{reason}+clamp_below_mark"
        LOG.info(
            "ScalpTrader %s: stop adjusted via %s | %.5f → %.5f (entry=%.5f price=%.5f)",
            pair_key, reason, old_stop, pos.stop_price, pos.entry_price, current_price,
        )

        if self._live_mgr is None or self._sim_mode:
            # Paper/sim: update in memory, next check_paper_exits picks up the new level
            return

        # Live: place new stop first, then cancel old (avoids naked window on reject).
        old_stop_id = pos.stop_cl_ord_id
        old_stop_price = old_stop
        new_stop_id = f"scalp_stop_{uuid.uuid4().hex[:8]}"
        stop_side = "sell" if is_long else "buy"
        oq = max(1, int(round(pos.qty))) if self._coinbase_perps() else round(pos.qty, 8)
        try:
            ok = bool(await self._live_mgr.add_order(params={
                "symbol": pos.symbol,
                "side": stop_side,
                "order_type": "stop-loss-limit",
                "trigger_price": round(pos.stop_price, 5),
                "limit_price": round(
                    pos.stop_price * (0.9995 if is_long else 1.0005), 5
                ),
                "order_qty": oq,
                "cl_ord_id": new_stop_id,
            }))
        except Exception:
            LOG.exception("ScalpTrader %s: failed to place adjusted stop %s", pair_key, new_stop_id[:20])
            ok = False

        if ok:
            pos.stop_cl_ord_id = new_stop_id
            if old_stop_id:
                try:
                    await self._live_mgr.cancel_order(old_stop_id)
                except Exception:
                    LOG.warning(
                        "ScalpTrader %s: failed to cancel old stop %s after adjustment",
                        pair_key, old_stop_id[:20],
                    )
            LOG.info(
                "ScalpTrader %s: adjusted stop order placed @ %.5f id=%s",
                pair_key, pos.stop_price, new_stop_id[:20],
            )
        else:
            pos.stop_price = old_stop_price
            self._protective_reconcile_backoff_until[pair_key] = (
                time.time() + self._protective_if_backoff_sec()
            )
            LOG.error(
                "ScalpTrader %s: adjusted stop REJECTED — keeping prior stop @ %.5f (id=%s)",
                pair_key, old_stop_price, (old_stop_id or "")[:20],
            )
            self._state.push_alert(
                "error",
                f"Stop adjustment failed: {pair_key}",
                f"Exchange rejected adjusted stop; prior stop @ {old_stop_price:.5f} unchanged.",
                "scalp_protective",
            )

    def check_paper_exits(self, pair_key: str, candle: object) -> None:
        """Check whether a paper/sim position's stop or TP was hit.

        Uses candle.low / candle.high for stop / TP evaluation.
        Called both intra-bar (on every WS tick) and on bar close.
        """
        if self._live_mgr is not None and not self._sim_mode:
            return
        for pos in list(self.positions_for_pair(pair_key)):
            if pos.status != "open":
                continue
            self._check_paper_exits_one(pos, pair_key, candle)

    def _check_paper_exits_one(self, pos: ScalpPosition, pair_key: str, candle: object) -> None:
        candle_low: float = getattr(candle, "low", 0.0)
        candle_high: float = getattr(candle, "high", 0.0)

        mult = pos.contract_size if self._coinbase_perps() else 1.0
        if pos.direction == "long":
            hit_stop = candle_low <= pos.stop_price
            hit_tp = candle_high >= pos.tp_price
        else:
            hit_stop = candle_high >= pos.stop_price
            hit_tp = candle_low <= pos.tp_price

        if hit_stop and hit_tp:
            hit_tp = False

        if hit_stop:
            # If partial TP already fired, close only the runner qty
            close_qty = pos.runner_qty if pos.tp1_done and pos.runner_qty > 0 else pos.qty
            if pos.direction == "long":
                pnl = (pos.stop_price - pos.entry_price) * close_qty * mult
            else:
                pnl = (pos.entry_price - pos.stop_price) * close_qty * mult
            self._close_position(pos, pnl, "stop", pos.stop_price)
            self._signal_engine.record_loss(pair_key)
            LOG.info(
                "ScalpTrader %s: PAPER stop hit @ %.5f | pnl=%.4f | daily_pnl=%.4f",
                pair_key, pos.stop_price, pnl, self._daily_pnl,
            )
        elif hit_tp:
            pair_cfg = self._cfg.pairs.get(pair_key)
            partial_enabled = getattr(pair_cfg, "partial_tp_enabled", False) if pair_cfg else False

            if partial_enabled and not pos.tp1_done:
                # ── Partial TP1 fire ─────────────────────────────────────────
                if not self._sim_mode and self._live_mgr is not None:
                    LOG.warning(
                        "ScalpTrader %s: partial_tp_enabled=True but live orders not "
                        "supported — treating as full close",
                        pair_key,
                    )
                    # Fall through to full close below
                    partial_enabled = False

            if partial_enabled and not pos.tp1_done:
                tp_pct = float(getattr(pair_cfg, "partial_tp_pct", 0.5))
                tp1_qty = pos.qty * tp_pct
                runner_qty = pos.qty - tp1_qty
                if pos.direction == "long":
                    pnl1 = (pos.tp_price - pos.entry_price) * tp1_qty * mult
                else:
                    pnl1 = (pos.entry_price - pos.tp_price) * tp1_qty * mult
                # Lock partial gains
                pos.tp1_done = True
                pos.runner_qty = runner_qty
                pos.pnl += pnl1
                self._daily_pnl += pnl1
                self._maybe_notify_daily_loss_breach()
                # Move stop to breakeven (entry + small buffer)
                buf_bps = float(getattr(pair_cfg, "breakeven_buffer_bps", 5.0))
                if pos.direction == "long":
                    pos.runner_stop = pos.entry_price * (1.0 + buf_bps / 10_000.0)
                else:
                    pos.runner_stop = pos.entry_price * (1.0 - buf_bps / 10_000.0)
                pos.stop_price = pos.runner_stop
                LOG.info(
                    "ScalpTrader %s: PAPER partial TP1 @ %.5f | tp1_qty=%.6f pnl1=%.4f "
                    "runner_qty=%.6f new_stop=%.5f",
                    pair_key, pos.tp_price, tp1_qty, pnl1, runner_qty, pos.runner_stop,
                )
            else:
                # Full close (standard path or after partial already done)
                close_qty = pos.runner_qty if pos.tp1_done and pos.runner_qty > 0 else pos.qty
                if pos.direction == "long":
                    pnl = (pos.tp_price - pos.entry_price) * close_qty * mult
                else:
                    pnl = (pos.entry_price - pos.tp_price) * close_qty * mult
                self._close_position(pos, pnl, "tp", pos.tp_price)
                self._signal_engine.record_win(pair_key)
                LOG.info(
                    "ScalpTrader %s: PAPER TP hit @ %.5f | pnl=%.4f | daily_pnl=%.4f",
                    pair_key, pos.tp_price, pnl, self._daily_pnl,
                )

    def reconcile_close_ghost_leg(
        self,
        pos: ScalpPosition,
        mark_price: float,
        *,
        exit_price: float | None = None,
    ) -> None:
        """Clear bot state when the exchange has no position but we still track an open leg."""
        if pos.status != "open":
            return
        ref = float(exit_price) if exit_price is not None and float(exit_price) > 0 else mark_price
        if ref <= 0:
            ref = float(pos.entry_price)
        mult = pos.contract_size if self._coinbase_perps() else 1.0
        if pos.direction == "long":
            pnl = (ref - pos.entry_price) * pos.qty * mult
        else:
            pnl = (pos.entry_price - ref) * pos.qty * mult
        LOG.warning(
            "ScalpTrader %s: exchange flat — clearing ghost leg (entry=%.5f mark=%.5f qty=%.4f)",
            pos.pair_key,
            pos.entry_price,
            ref,
            pos.qty,
        )
        self._state.push_alert(
            "warning",
            f"Scalp reconcile: {pos.pair_key} flat on exchange",
            "Bot had open leg but venue size is zero — internal state cleared.",
            "scalp_reconcile",
        )
        self._close_position(pos, pnl, "exchange_reconcile_flat", ref)

    async def adopt_exchange_position(
        self,
        *,
        pair_key: str,
        symbol: str,
        direction: str,
        qty: float,
        entry_price: float,
        stop_price: float,
        tp_price: float,
        pair_cfg: "ScalpPairConfig",
        place_protectives: bool,
    ) -> ScalpPosition | None:
        """Track a venue perp leg the bot did not open (manual or missed fill)."""
        if self.has_position(pair_key):
            LOG.info(
                "ScalpTrader %s: adopt skipped — bot already tracks a leg for this pair",
                pair_key,
            )
            return None
        entry_id = f"scalp_adopt_{uuid.uuid4().hex[:8]}"
        stop_id = f"scalp_stop_{uuid.uuid4().hex[:8]}"
        tp_id = f"scalp_tp_{uuid.uuid4().hex[:8]}"
        oq = max(1, int(round(qty))) if self._coinbase_perps() else float(qty)
        pos = ScalpPosition(
            pair_key=pair_key,
            symbol=symbol,
            direction=direction,
            entry_price=float(entry_price),
            stop_price=float(stop_price),
            tp_price=float(tp_price),
            qty=float(oq),
            contract_size=float(pair_cfg.contract_size),
            entry_cl_ord_id=entry_id,
            stop_cl_ord_id=stop_id,
            tp_cl_ord_id=tp_id,
            status="open",
            entry_order_type="exchange_adopt",
            leverage=float(getattr(self._cfg, "max_leverage", 1.0)),
        )
        lev = max(1.0, float(getattr(self._cfg, "max_leverage", 1.0)))
        mult = pos.contract_size if self._coinbase_perps() else 1.0
        notional = float(pos.qty) * float(pos.entry_price) * mult
        self._reserved_capital += notional / lev
        self._positions[entry_id] = pos
        self._note_excursion(pos, 0.0)
        LOG.warning(
            "ScalpTrader %s: adopted exchange %s leg qty=%.4f entry=%.5f stop=%.5f tp=%.5f",
            pair_key,
            direction,
            pos.qty,
            pos.entry_price,
            pos.stop_price,
            pos.tp_price,
        )
        self._state.push_alert(
            "warning",
            f"Scalp adopted: {pair_key} {direction}",
            f"Venue size {pos.qty:.4f} @ {pos.entry_price:.5f} — protectives "
            f"{'will be placed' if place_protectives else 'deferred (scalp off/sim)'}",
            "scalp_reconcile",
        )
        if place_protectives and self._live_mgr is not None and not self._sim_mode:
            await self._place_protective_orders_coinbase(pos)
        return pos

    def _close_position(
        self,
        pos: ScalpPosition,
        pnl: float,
        reason: str,
        close_price: float,
    ) -> None:
        simulated = self._sim_mode or self._live_mgr is None
        if self._session_log is not None and simulated:
            slip = self._slip_bps_for_exit(pos.direction, close_price, close_price)
            self._emit_scalp_fill_execution(
                leg="exit",
                pair_key=pos.pair_key,
                symbol=pos.symbol,
                direction=pos.direction,
                order_type="sim_close",
                placed_ts=float(pos.opened_at),
                fill_ts=time.time(),
                fill_price=float(close_price),
                qty=float(pos.qty),
                fee_usd=None,
                reference_price=float(close_price),
                slip_bps=slip,
                cl_ord_id=f"paper_exit_{pos.entry_cl_ord_id}"[:80],
                close_reason=str(reason)[:64],
                strategy_mode=pos.strategy_mode or "",
                contract_size=float(pos.contract_size or 1.0),
                simulated=True,
            )
        mult_exc = pos.contract_size if self._coinbase_perps() else 1.0
        if pos.status == "open":
            cp = float(close_price)
            ep = float(pos.entry_price)
            q = float(pos.qty)
            if pos.direction == "long":
                u_exit = (cp - ep) * q * mult_exc
            else:
                u_exit = (ep - cp) * q * mult_exc
            self._note_excursion(pos, u_exit)

        pos.pnl = pnl
        pos.close_reason = reason
        pos.status = "closed"
        pos.closed_at = time.time()
        self._positions.pop(pos.entry_cl_ord_id, None)
        self._daily_pnl += pnl
        self._maybe_notify_daily_loss_breach()
        if self._coinbase_perps():
            notional = pos.qty * pos.contract_size * pos.entry_price
        else:
            notional = pos.qty * pos.entry_price
        _lev_close = max(1.0, float(getattr(self._cfg, "max_leverage", 1.0)))
        addon = float(getattr(pos, "margin_reserve_addon", 0.0) or 0.0)
        self._reserved_capital = max(0.0, self._reserved_capital - notional / _lev_close - addon)

        mode = pos.strategy_mode or "unknown"
        pnl_f = float(pnl)
        trade_idx = self._strategy_trade_index[mode] + 1
        self._strategy_cumulative_pnl[mode] += pnl_f
        cum_after = self._strategy_cumulative_pnl[mode]
        self._strategy_trade_index[mode] = trade_idx

        entry_notional = abs(float(pos.entry_price) * float(pos.qty) * mult_exc)
        exit_notional = abs(float(close_price) * float(pos.qty) * mult_exc)
        mfe_usd = float(pos.excursion_max_usd) if pos.excursion_initialized else 0.0
        mae_usd = float(pos.excursion_min_usd) if pos.excursion_initialized else 0.0
        net_pct = self._pct_vs_notional(pnl_f, entry_notional)
        mfe_pct = self._pct_vs_notional(mfe_usd, entry_notional)
        mae_pct = self._pct_vs_notional(mae_usd, entry_notional)
        alloc = float(getattr(self._cfg, "allocated_capital_usd", 0.0) or 0.0)
        cum_pct = (cum_after / alloc * 100.0) if alloc > 1e-12 else 0.0

        _hist = {
            "pair_key": pos.pair_key,
            "symbol": pos.symbol,
            "direction": pos.direction,
            "strategy_mode": mode,
            "entry_ts": pos.opened_at,
            "exit_ts": pos.closed_at,
            "entry_price": pos.entry_price,
            "exit_price": close_price,
            "qty": pos.qty,
            "pnl": round(pnl, 6),
            "reason": reason,
            "simulated": self._sim_mode or self._live_mgr is None,
            "entry_cl_ord_id": pos.entry_cl_ord_id,
            "strategy_trade_index": int(trade_idx),
            "cumulative_pnl_after": round(cum_after, 6),
            "entry_notional_usd": round(entry_notional, 6),
            "exit_notional_usd": round(exit_notional, 6),
            "mfe_usd": round(mfe_usd, 6),
            "mfe_pct": round(mfe_pct, 4),
            "mae_usd": round(mae_usd, 6),
            "mae_pct": round(mae_pct, 4),
            "net_pnl_pct": round(net_pct, 4),
            "cumulative_pnl_pct": round(cum_pct, 4),
        }
        self._trade_history.append(_hist)
        if bool(getattr(self._cfg, "persist_trade_history", True)):
            append_trade_history_row(_hist)
        self._state.push_alert(
            "success" if pnl > 0 else "warning",
            f"Scalp {reason.upper()}: {pos.pair_key}",
            f"{'Profit' if pnl > 0 else 'Loss'}: ${pnl:+.4f} | "
            f"entry={pos.entry_price:.5f} exit={close_price:.5f}",
            "scalp",
        )
        if self._session_log is not None:
            fill_ts = float(pos.closed_at)
            entry_sig = self._strategy_entry_signal(pos.direction)
            exit_sig = self._strategy_exit_signal(pos.direction)
            self._session_log.log_scalp(
                "strategy_report_trade",
                strategy_mode=mode,
                pair_key=pos.pair_key,
                symbol=pos.symbol,
                trade_number=int(trade_idx),
                direction=pos.direction,
                net_pnl_usd=round(pnl_f, 6),
                net_pnl_pct=round(net_pct, 4),
                mfe_usd=round(mfe_usd, 6),
                mfe_pct=round(mfe_pct, 4),
                mae_usd=round(mae_usd, 6),
                mae_pct=round(mae_pct, 4),
                cumulative_pnl_usd=round(cum_after, 6),
                cumulative_pnl_pct=round(cum_pct, 4),
                entry_notional_usd=round(entry_notional, 6),
                exit_notional_usd=round(exit_notional, 6),
                entry_row={
                    "type": "Entry",
                    "ts": round(float(pos.opened_at), 3),
                    "signal": entry_sig,
                    "price_usd": round(float(pos.entry_price), 8),
                    "qty": round(float(pos.qty), 8),
                    "notional_usd": round(entry_notional, 6),
                },
                exit_row={
                    "type": "Exit",
                    "ts": round(fill_ts, 3),
                    "signal": exit_sig,
                    "price_usd": round(float(close_price), 8),
                    "qty": round(float(pos.qty), 8),
                    "notional_usd": round(exit_notional, 6),
                },
                close_reason=str(reason)[:80],
                simulated=self._sim_mode or self._live_mgr is None,
                entry_cl_ord_id=str(pos.entry_cl_ord_id)[:80],
            )
            self._session_log.log_scalp(
                "position_closed",
                pair_key=pos.pair_key,
                symbol=pos.symbol,
                strategy_mode=mode,
                direction=pos.direction,
                reason=reason,
                entry_cl_ord_id=pos.entry_cl_ord_id,
                entry_price=round(pos.entry_price, 8),
                exit_price=round(close_price, 8),
                qty=round(pos.qty, 8),
                pnl=round(pnl, 6),
                daily_pnl=round(self._daily_pnl, 6),
                simulated=self._sim_mode or self._live_mgr is None,
            )

    def _maybe_notify_daily_loss_breach(self) -> None:
        if self._daily_loss_breach_notified:
            return
        limit = self._cfg.allocated_capital_usd * (self._cfg.daily_loss_limit_pct / 100.0)
        if limit <= 0:
            return
        if self._daily_pnl >= -limit:
            return
        self._daily_loss_breach_notified = True
        fn = self._daily_loss_breach_fn
        if fn is not None:
            try:
                fn()
            except Exception:
                LOG.exception("ScalpTrader: daily_loss_breach_fn failed")

    def reset_session(self) -> None:
        """Clear P&L, trade history, and close any open positions."""
        self._daily_pnl = 0.0
        self._daily_loss_breach_notified = False
        self._trade_history.clear()
        for pos in list(self._positions.values()):
            if pos.status == "open":
                pos.status = "closed"
                pos.close_reason = "session_reset"
                pos.closed_at = time.time()
        self._positions.clear()
        self._market_exit_entry_link.clear()
        self._pending_market_exits.clear()
        self._pending_counter_reversals.clear()
        self._reserved_capital = 0.0
        LOG.info("ScalpTrader: session reset — P&L, history, positions, and reserved capital cleared")

    def _maybe_reset_daily(self) -> None:
        if not bool(getattr(self._cfg, "daily_auto_reset", True)):
            return  # session-persistent mode: PnL and breach state never reset automatically
        day = int(time.time() // 86400)
        if day != self._daily_reset_day:
            self._daily_reset_day = day
            self._daily_pnl = 0.0
            self._daily_loss_breach_notified = False

    def _position_row(self, p: ScalpPosition) -> dict:
        return {
            "pair_key": p.pair_key,
            "entry_cl_ord_id": p.entry_cl_ord_id,
            "symbol": p.symbol,
            "direction": p.direction,
            "strategy_mode": p.strategy_mode or "unknown",
            "entry": p.entry_price,
            "stop": p.stop_price,
            "tp": p.tp_price,
            "entry_ts": int(p.opened_at),
            "qty": p.qty,
            "contract_size": p.contract_size,
            "status": p.status,
            "age_sec": round(time.time() - p.opened_at, 0),
            "unrealized_pnl": round(p.unrealized_pnl, 4),
            "mark_price": round(p.mark_price, 6) if p.mark_price else 0.0,
            "leverage": p.leverage,
            "liquidation_price": round(p.liquidation_price, 6) if p.liquidation_price else 0.0,
            "funding_rate": p.funding_rate,
            "breakeven_hit": p.breakeven_hit,
            "trail_active": p.trail_active,
        }

    def snapshot(self) -> dict:
        """Summary for dashboard display."""
        open_pos = {
            p.entry_cl_ord_id: self._position_row(p)
            for p in self._positions.values()
            if p.status == "open"
        }
        pending_pos = {
            p.entry_cl_ord_id: self._position_row(p)
            for p in self._positions.values()
            if p.status == "pending"
        }
        return {
            "open_positions": open_pos,
            "pending_entries": pending_pos,
            "open_count": len(open_pos),
            "pending_count": len(pending_pos),
            "daily_pnl": round(self._daily_pnl, 4),
            "reserved_capital": round(self._reserved_capital, 2),
            "trade_history": list(self._trade_history),
            "sim_mode": self._sim_mode,
            "empirical_market": self._empirical.dashboard_snapshot(),
        }
