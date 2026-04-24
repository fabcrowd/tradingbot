"""Sports odds arbitrage strategy.

Detects when sportsbook-implied probability diverges from Polymarket
contract prices. The edge comes from sportsbooks repricing faster than
Polymarket's CLOB on live game events (score changes, momentum shifts).

Signal flow:
  1. Match Polymarket sports markets to Odds API events by team names
  2. Compare sportsbook consensus probability to Polymarket YES/NO price
  3. If divergence exceeds fee + min_edge threshold, generate a signal
  4. Buy the underpriced side on Polymarket
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .odds_client import OddsClient, OddsEvent
from .sports_ws import GameState, SportsWsClient
from .ws_feeds import ActiveMarket, BookState

LOG = logging.getLogger("polymarket_bot.strategy_sports")

TAKER_FEE_RATE = 0.072


def _taker_fee_bps(price: float) -> float:
    p = min(0.999, max(0.001, price))
    return TAKER_FEE_RATE * p * (1.0 - p) * 10000.0


@dataclass
class SportsMarket:
    """A Polymarket sports market matched to an Odds API event."""
    pm_market: ActiveMarket
    book: BookState
    odds_event: OddsEvent | None = None
    game_state: GameState | None = None
    is_home_yes: bool = True
    last_signal_ts: float = 0.0


@dataclass
class SportsSignal:
    should_trade: bool
    side: str
    edge_bps: float
    reason: str
    pm_price: float = 0.0
    implied_prob: float = 0.0
    market_id: str = ""
    token_id: str = ""


class SportsArbStrategy:
    """Detects mispricing between sportsbooks and Polymarket on live games.

    Only generates signals when:
    - A game is live (in progress)
    - Sportsbook probability diverges from PM price by > min_edge + fee
    - The game is not in the final seconds (to avoid settlement race)
    """

    def __init__(
        self,
        min_edge_bps: float = 50.0,
        cooldown_sec: float = 30.0,
        max_period_skip: set[str] | None = None,
    ) -> None:
        self._min_edge_bps = min_edge_bps
        self._cooldown_sec = cooldown_sec
        self._max_period_skip = max_period_skip or {"F", "Final", "F/OT"}
        self._matched: dict[str, SportsMarket] = {}

    @property
    def matched_markets(self) -> dict[str, SportsMarket]:
        return self._matched

    def match_markets(
        self,
        pm_markets: dict[str, ActiveMarket],
        books: dict[str, BookState],
        odds_events: list[OddsEvent],
        sports_ws: SportsWsClient,
    ) -> int:
        """Attempt to match Polymarket markets to Odds API events and live games."""
        matched_count = 0
        for mid, am in pm_markets.items():
            q = am.question.upper()

            home, away, league = _extract_teams_from_question(q)
            if not home or not away:
                continue

            odds_evt = None
            for evt in odds_events:
                if _teams_match(evt.home_team, evt.away_team, home, away):
                    odds_evt = evt
                    break

            game = sports_ws.find_game(home, away, league)

            is_home_yes = _is_home_team_yes(q, home)

            yes_book = books.get(am.yes_token_id, BookState())

            sm = SportsMarket(
                pm_market=am,
                book=yes_book,
                odds_event=odds_evt,
                game_state=game,
                is_home_yes=is_home_yes,
            )

            if mid in self._matched:
                sm.last_signal_ts = self._matched[mid].last_signal_ts
            self._matched[mid] = sm

            if odds_evt or game:
                matched_count += 1

        return matched_count

    def evaluate(self, market_id: str) -> SportsSignal:
        """Evaluate a matched sports market for an arbitrage signal."""
        sm = self._matched.get(market_id)
        if sm is None:
            return SportsSignal(False, "none", 0.0, "not_matched")

        if sm.game_state and not sm.game_state.live:
            return SportsSignal(False, "none", 0.0, "game_not_live")

        if sm.game_state and sm.game_state.status in self._max_period_skip:
            return SportsSignal(False, "none", 0.0, "game_ended")

        if time.time() - sm.last_signal_ts < self._cooldown_sec:
            return SportsSignal(False, "none", 0.0, "cooldown")

        if sm.odds_event is None:
            return SportsSignal(False, "none", 0.0, "no_odds_data")

        # Get sportsbook-implied probability
        sharp_home, sharp_away = sm.odds_event.sharp_probability()
        consensus_home, consensus_away = sm.odds_event.consensus_probability()

        # Use sharp line as primary, consensus as fallback
        if sharp_home > 0:
            implied_home = sharp_home
            implied_away = sharp_away
        else:
            implied_home = consensus_home
            implied_away = consensus_away

        # Polymarket YES price
        pm_yes = sm.book.best_bid if sm.book.best_bid > 0 else 0.5
        pm_no = 1.0 - pm_yes

        # Map: if YES = home team wins, then implied YES prob = implied_home
        if sm.is_home_yes:
            implied_yes = implied_home
            implied_no = implied_away
        else:
            implied_yes = implied_away
            implied_no = implied_home

        # Normalize to sum to 1
        total = implied_yes + implied_no
        if total > 0:
            implied_yes /= total
            implied_no /= total

        # Compute edge: if sportsbooks say YES is worth 0.65 but PM has it at 0.55
        yes_edge_bps = (implied_yes - pm_yes) / max(pm_yes, 1e-9) * 10000.0
        no_edge_bps = (implied_no - pm_no) / max(pm_no, 1e-9) * 10000.0

        yes_fee = _taker_fee_bps(pm_yes)
        no_fee = _taker_fee_bps(pm_no)

        yes_net = yes_edge_bps - yes_fee
        no_net = no_edge_bps - no_fee

        if yes_net > self._min_edge_bps and yes_net > no_net:
            sm.last_signal_ts = time.time()
            return SportsSignal(
                should_trade=True,
                side="buy_yes",
                edge_bps=yes_net,
                reason=f"sportsbook_edge_yes (sharp={implied_yes:.3f} pm={pm_yes:.3f} fee={yes_fee:.0f}bps)",
                pm_price=pm_yes,
                implied_prob=implied_yes,
                market_id=market_id,
                token_id=sm.pm_market.yes_token_id,
            )
        elif no_net > self._min_edge_bps:
            sm.last_signal_ts = time.time()
            return SportsSignal(
                should_trade=True,
                side="buy_no",
                edge_bps=no_net,
                reason=f"sportsbook_edge_no (sharp={implied_no:.3f} pm={pm_no:.3f} fee={no_fee:.0f}bps)",
                pm_price=pm_no,
                implied_prob=implied_no,
                market_id=market_id,
                token_id=sm.pm_market.no_token_id,
            )

        best_net = max(yes_net, no_net)
        return SportsSignal(
            False, "none", best_net,
            f"edge_below_threshold (yes={yes_net:.0f} no={no_net:.0f} min={self._min_edge_bps:.0f})",
        )

    def evaluate_all(self) -> list[SportsSignal]:
        signals = []
        for mid in self._matched:
            sig = self.evaluate(mid)
            if sig.should_trade:
                signals.append(sig)
        return signals


def _extract_teams_from_question(q: str) -> tuple[str, str, str]:
    """Extract team abbreviations and league from a Polymarket question.

    Examples:
      "Will the Lakers beat the Celtics?" -> ("LAL", "BOS", "nba")
      "NBA: LAL vs BOS" -> ("LAL", "BOS", "nba")
    """
    league = ""
    for lg in ("NBA", "NFL", "MLB", "NHL", "EPL", "MLS", "UFC", "CBB", "CFB"):
        if lg in q:
            league = lg.lower()
            break

    q_clean = q.replace(":", "").replace(",", "")
    tokens = q_clean.split()

    if "VS" in tokens:
        idx = tokens.index("VS")
        if idx > 0 and idx < len(tokens) - 1:
            return tokens[idx - 1].strip(), tokens[idx + 1].strip(), league

    if "V" in tokens:
        idx = tokens.index("V")
        if idx > 0 and idx < len(tokens) - 1:
            return tokens[idx - 1].strip(), tokens[idx + 1].strip(), league

    # Look for "TEAM1 TEAM2" patterns separated by common delimiters
    for delim in [" - ", " AT ", " @ "]:
        if delim in q:
            parts = q.split(delim, 1)
            if len(parts) == 2:
                t1 = parts[0].strip().split()[-1] if parts[0].strip() else ""
                t2 = parts[1].strip().split()[0] if parts[1].strip() else ""
                return t1, t2, league

    return "", "", league


def _teams_match(odds_home: str, odds_away: str, pm_home: str, pm_away: str) -> bool:
    oh = odds_home.upper()
    oa = odds_away.upper()
    ph = pm_home.upper()
    pa = pm_away.upper()

    # Direct abbreviation match
    if (ph in oh or oh in ph) and (pa in oa or oa in pa):
        return True
    if (ph in oa or oa in ph) and (pa in oh or oh in pa):
        return True

    # Try 3-letter abbreviation matching
    from .odds_client import _normalize_team
    oh3 = _normalize_team(odds_home)
    oa3 = _normalize_team(odds_away)
    if (ph == oh3 and pa == oa3) or (ph == oa3 and pa == oh3):
        return True

    return False


def _is_home_team_yes(question: str, home_team: str) -> bool:
    """Determine if YES corresponds to the home team winning.

    For "Will X win?" or "X to beat Y?" patterns, YES = X wins.
    """
    q = question.upper()
    h = home_team.upper()

    # "Will TEAM win" -> YES = that team
    for pattern in ["WILL", "CAN"]:
        idx = q.find(pattern)
        if idx >= 0:
            after = q[idx + len(pattern):idx + len(pattern) + 20]
            return h in after

    # "TEAM1 vs TEAM2" -> YES typically = first team listed
    if " VS " in q:
        before_vs = q.split(" VS ")[0]
        return h in before_vs

    # Default: YES = home team
    return True
