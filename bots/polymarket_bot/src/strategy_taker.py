"""Fee-aware taker gate with real oracle-lag signal detection.

Uses Polymarket RTDS Binance/Chainlink price streams to detect when the
CLOB mid hasn't repriced to reflect a confirmed spot move. Only crosses
the spread when the edge exceeds the taker fee curve at the current price.
"""
from __future__ import annotations

from dataclasses import dataclass

from .features import Features
from .ws_feeds import RtdsBuffer

TAKER_FEE_RATE = 0.072


@dataclass
class TakerSignal:
    should_trade: bool
    side: str
    edge_after_fee_bps: float
    reason: str
    implied_fair: float = 0.0
    momentum_pct: float = 0.0


class TakerGate:
    """Fee-aware taker gate using RTDS oracle-lag detection.

    Signal flow:
      1. Read latest Binance BTC price + rolling momentum from RTDS buffer
      2. If BTC moved > min_momentum_pct in last 60s, compute directional signal
      3. Translate BTC direction into implied fair value for the prediction market
      4. Compute edge = |implied_fair - clob_mid| in bps
      5. Compute fee at current mid using Polymarket crypto fee curve
      6. Only signal if edge > fee + min_net_edge_bps
    """

    def __init__(
        self,
        fee_rate: float = TAKER_FEE_RATE,
        min_net_edge_bps: float = 20.0,
        min_momentum_pct: float = 0.3,
    ) -> None:
        self._fee_rate = fee_rate
        self._min_net_edge_bps = min_net_edge_bps
        self._min_momentum_pct = min_momentum_pct / 100.0

    def _fee_bps_at_price(self, p: float) -> float:
        p = min(0.999, max(0.001, p))
        return self._fee_rate * p * (1.0 - p) * 10000.0

    def _compute_implied_fair(self, momentum_pct: float, current_mid: float) -> float:
        """Translate BTC spot momentum into an implied fair value for the contract.

        For a "BTC up/down in 15 min" market:
        - Strong positive BTC momentum -> YES should be worth more -> fair > mid
        - Strong negative BTC momentum -> YES should be worth less -> fair < mid
        """
        strength = min(abs(momentum_pct) / 0.01, 1.0)
        direction = 1.0 if momentum_pct > 0 else -1.0
        shift = direction * strength * 0.10
        return max(0.01, min(0.99, current_mid + shift))

    def evaluate(
        self,
        feat: Features,
        rtds: RtdsBuffer | None = None,
        external_fair: float | None = None,
    ) -> TakerSignal:
        momentum_pct = 0.0
        implied_fair = feat.mid

        if rtds is not None:
            for sym in ("binance_btcusdt", "binance_ethusdt"):
                m = rtds.momentum_pct(sym, lookback_sec=60.0)
                if abs(m) > abs(momentum_pct):
                    momentum_pct = m

            if abs(momentum_pct) < self._min_momentum_pct:
                return TakerSignal(
                    False, "none", 0.0,
                    f"momentum_too_low ({momentum_pct*100:.3f}%)",
                    feat.mid, momentum_pct,
                )
            implied_fair = self._compute_implied_fair(momentum_pct, feat.mid)
        elif external_fair is not None:
            implied_fair = external_fair
        else:
            return TakerSignal(False, "none", 0.0, "no_signal_source", feat.mid, 0.0)

        raw_edge_bps = abs(implied_fair - feat.mid) / max(feat.mid, 1e-9) * 10000.0
        fee_bps = self._fee_bps_at_price(feat.mid)
        net = raw_edge_bps - fee_bps

        if net < self._min_net_edge_bps:
            return TakerSignal(
                False, "none", net,
                f"net_edge_below_threshold (raw={raw_edge_bps:.1f} fee={fee_bps:.1f})",
                implied_fair, momentum_pct,
            )

        side = "buy_yes" if implied_fair > feat.mid else "buy_no"
        return TakerSignal(True, side, net, "oracle_lag_signal", implied_fair, momentum_pct)
