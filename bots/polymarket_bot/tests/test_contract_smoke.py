"""Smoke tests for the Polymarket bot service contract."""
from __future__ import annotations

from bots.polymarket_bot.src.config import load_config
from bots.polymarket_bot.src.state import BotState
from bots.polymarket_bot.src.positions import PositionManager, taker_fee
from bots.polymarket_bot.src.strategy_taker import TakerGate
from bots.polymarket_bot.src.features import Features
from bots.polymarket_bot.src.strategy_maker import MakerStrategy
from bots.polymarket_bot.src.risk import RiskEngine
from bots.polymarket_bot.src.models import RiskConfig
from bots.polymarket_bot.src.odds_client import decimal_to_probability, OddsClient, OddsEvent, BookmakerLine
from bots.polymarket_bot.src.strategy_sports import SportsArbStrategy, SportsMarket, _extract_teams_from_question
from bots.polymarket_bot.src.strategy_router import StrategyRouter, StrategyAllocation
from bots.polymarket_bot.src.binance_ws import BinanceDirectFeed, VolatilityScheduler
from bots.polymarket_bot.src.ws_feeds import ActiveMarket, BookState


def test_state_contract_defaults() -> None:
    cfg = load_config()
    state = BotState.from_config(cfg)
    status = state.status()
    assert "bot_id" in status
    assert "running" in status
    assert "paused" in status
    assert "open_positions" in status
    assert "pnl_total_usd" in status


def test_position_lifecycle() -> None:
    mgr = PositionManager()
    pos = mgr.open_position("mkt1", "tok1", "yes", 0.60, 10.0, is_maker=True)
    assert pos.status == "open"
    assert pos.fee_paid == 0.0
    assert mgr.get_exposure_usd() == 6.0
    assert mgr.count_for_market("mkt1") == 1

    pnl = mgr.resolve_position(pos.id, "yes")
    assert pnl > 0
    assert pos.status == "won"
    assert abs(pos.pnl - 4.0) < 0.01

    pos2 = mgr.open_position("mkt2", "tok2", "yes", 0.60, 10.0, is_maker=False)
    assert pos2.fee_paid > 0

    pnl2 = mgr.resolve_position(pos2.id, "no")
    assert pnl2 < 0
    assert pos2.status == "lost"


def test_taker_fee_formula() -> None:
    fee = taker_fee(100, 0.50)
    expected = 100 * 0.072 * 0.50 * 0.50
    assert abs(fee - expected) < 0.001


def test_taker_gate_no_rtds() -> None:
    gate = TakerGate(min_net_edge_bps=20.0)
    feat = Features("BTC", "m1", "t1", "t2", 0.50, 0.04, 80.0, 0.0)
    sig = gate.evaluate(feat)
    assert not sig.should_trade
    assert "no_signal_source" in sig.reason


def test_maker_inventory_skew() -> None:
    maker = MakerStrategy(quote_size=10.0, min_half_spread_bps=8.0, skew_scale=0.4)
    feat = Features("BTC", "m1", "t1", "t2", 0.50, 0.02, 40.0, 0.0)
    q_neutral = maker.quote(feat, inventory_ratio=0.0)
    q_long = maker.quote(feat, inventory_ratio=0.8)
    assert q_long.bid < q_neutral.bid
    assert q_long.ask < q_neutral.ask


def test_risk_engine() -> None:
    rc = RiskConfig(daily_loss_limit_usd=10.0, max_position_pct=0.5, max_portfolio_exposure_usd=100.0)
    engine = RiskEngine(rc)
    r = engine.check(pnl_total_usd=-11.0, open_positions=0, projected_exposure_usd=0.0)
    assert not r.allow
    assert "daily_loss" in r.reason

    r = engine.check(pnl_total_usd=0.0, open_positions=10, projected_exposure_usd=0.0, max_concurrent=10)
    assert not r.allow
    assert "concurrent" in r.reason

    r = engine.check(pnl_total_usd=5.0, open_positions=1, projected_exposure_usd=10.0)
    assert r.allow


def test_decimal_to_probability() -> None:
    p = decimal_to_probability(1.50)
    assert abs(p - 0.6667) < 0.01
    p = decimal_to_probability(3.00)
    assert abs(p - 0.3333) < 0.01
    p = decimal_to_probability(2.00)
    assert abs(p - 0.5000) < 0.01


def test_sports_team_extraction() -> None:
    home, away, league = _extract_teams_from_question("NBA: LAL VS BOS")
    assert home == "LAL"
    assert away == "BOS"
    assert league == "nba"

    home2, away2, league2 = _extract_teams_from_question("WILL THE LAKERS BEAT THE CELTICS?")
    assert league2 == ""


def test_sports_arb_no_odds() -> None:
    arb = SportsArbStrategy(min_edge_bps=50.0)
    am = ActiveMarket("m1", "Will LAL beat BOS?", "t1", "t2", "", 0.0, "sports")
    book = BookState(best_bid=0.55, best_ask=0.57)

    arb._matched["m1"] = SportsMarket(pm_market=am, book=book, odds_event=None, game_state=None)
    sig = arb.evaluate("m1")
    assert not sig.should_trade
    assert "no_odds_data" in sig.reason


def test_sports_arb_with_edge() -> None:
    arb = SportsArbStrategy(min_edge_bps=50.0)
    am = ActiveMarket("m1", "Will LAL beat BOS?", "t1", "t2", "", 0.0, "sports")
    book = BookState(best_bid=0.50, best_ask=0.52)

    evt = OddsEvent(
        event_id=12345, sport_slug="basketball",
        league_slug="usa-nba", league_name="NBA",
        home_team="Los Angeles Lakers", away_team="Boston Celtics",
        commence_time="", status="live",
        bookmakers=[BookmakerLine("Bet365", 1.50, 2.70, 0.667, 0.370)],
    )

    from bots.polymarket_bot.src.sports_ws import GameState
    game = GameState(game_id=1, league="NBA", slug="lal-bos", home_team="LAL",
                     away_team="BOS", status="InProgress", score="55-48",
                     period="3rd", elapsed="8:00", live=True, ended=False)

    arb._matched["m1"] = SportsMarket(
        pm_market=am, book=book, odds_event=evt, game_state=game, is_home_yes=True,
    )
    sig = arb.evaluate("m1")
    assert sig.should_trade or sig.edge_bps > 0


def test_strategy_router() -> None:
    alloc = StrategyAllocation(sports_pct=50, maker_pct=30, crypto_pct=20)
    router = StrategyRouter(allocation=alloc, total_budget_usd=200.0)
    assert router.budget_for("sports") == 100.0
    assert router.budget_for("maker") == 60.0
    assert router.budget_for("crypto") == 40.0

    router.activate("sports", "live_games")
    assert router.strategies["sports"].active
    router.record_signal("sports")
    assert router.strategies["sports"].signals_fired == 1
    router.record_trade("sports", pnl=5.0)
    assert router.strategies["sports"].trades_executed == 1
    assert router.strategies["sports"].pnl_usd == 5.0


def test_volatility_scheduler() -> None:
    sched = VolatilityScheduler(always_active=False)
    active, reason = sched.is_active()
    assert isinstance(active, bool)
    assert isinstance(reason, str)

    sched_always = VolatilityScheduler(always_active=True)
    active2, reason2 = sched_always.is_active()
    assert active2
    assert reason2 == "always_active"

    sched.force_active(duration_sec=10.0)
    active3, reason3 = sched.is_active()
    assert active3
    assert reason3 == "force_active"


def test_binance_feed_momentum() -> None:
    feed = BinanceDirectFeed(symbols=("btcusdt",), max_ticks=100)
    assert feed.momentum_pct("btcusdt") == 0.0
    assert feed.latest("btcusdt") is None


if __name__ == "__main__":
    test_state_contract_defaults()
    test_position_lifecycle()
    test_taker_fee_formula()
    test_taker_gate_no_rtds()
    test_maker_inventory_skew()
    test_risk_engine()
    test_decimal_to_probability()
    test_sports_team_extraction()
    test_sports_arb_no_odds()
    test_sports_arb_with_edge()
    test_strategy_router()
    test_volatility_scheduler()
    test_binance_feed_momentum()
    print("All smoke tests passed.")
