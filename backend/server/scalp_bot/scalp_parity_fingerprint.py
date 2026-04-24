"""Single payload for sim / WFO / live parity observability (snapshot + logs)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .indicator_warmup import effective_min_bars_ready, ohlc_hist_maxlen_for_pair
from .scalp_config import effective_scalp_fee_bps_per_leg, wfo_fee_bps_per_leg

if TYPE_CHECKING:
    from .scalp_config import ScalpBotConfig, ScalpPairConfig


def build_scalp_parity_fingerprint(
    cfg: "ScalpBotConfig",
    *,
    champion_present: bool,
    per_pair: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fields that affect bar sim vs live path assumptions (not secrets)."""
    per_pair = per_pair or {}
    return {
        "backtest_fill_model": str(getattr(cfg, "backtest_fill_model", "close_slip") or ""),
        "wfo_assume_taker_fee": bool(getattr(cfg, "wfo_assume_taker_fee", False)),
        "fee_tier_volume_source": str(getattr(cfg, "fee_tier_volume_source", "exchange") or ""),
        "fee_bps_per_leg_config": float(getattr(cfg, "fee_bps_per_leg", 0.0) or 0.0),
        "fee_bps_taker_per_leg_config": float(getattr(cfg, "fee_bps_taker_per_leg", 0.0) or 0.0),
        "effective_fee_bps_per_leg_live_order_type": float(effective_scalp_fee_bps_per_leg(cfg)),
        "wfo_fee_bps_sim_per_leg": float(wfo_fee_bps_per_leg(cfg)),
        "champion_present": bool(champion_present),
        "scalp_fee_assumption_revision": int(getattr(cfg, "scalp_fee_assumption_revision", 0) or 0),
        "param_tuner_interval_sec": float(getattr(cfg, "param_tuner_interval_sec", 900.0) or 900.0),
        "param_tuner_min_bars_between_runs": int(
            getattr(cfg, "param_tuner_min_bars_between_runs", 0) or 0
        ),
        "param_tuner_cooldown_sec_after_apply": float(
            getattr(cfg, "param_tuner_cooldown_sec_after_apply", 0.0) or 0.0
        ),
        "volatility_armed_param_tuner_interval_mult": float(
            getattr(cfg, "volatility_armed_param_tuner_interval_mult", 1.0) or 1.0
        ),
        "wfo_holdout_tiebreakers": list(getattr(cfg, "wfo_holdout_tiebreakers", ()) or ()),
        "wfo_holdout_score_epsilon": float(getattr(cfg, "wfo_holdout_score_epsilon", 0.0) or 0.0),
        "per_pair": per_pair,
    }


def per_pair_parity_row(
    pair_key: str,
    pc: "ScalpPairConfig",
    *,
    resolved_mode: str,
) -> dict[str, Any]:
    return {
        "pair_key": pair_key,
        "symbol": pc.symbol,
        "interval": int(pc.interval),
        "resolved_mode": str(resolved_mode or "").strip(),
        "min_bars_ready_mode": int(effective_min_bars_ready(str(resolved_mode or "").strip(), pc)),
        "ohlc_hist_maxlen": int(ohlc_hist_maxlen_for_pair(pc)),
        "ohlc_hist_max_bars_cfg": int(getattr(pc, "ohlc_hist_max_bars", 0) or 0),
    }
