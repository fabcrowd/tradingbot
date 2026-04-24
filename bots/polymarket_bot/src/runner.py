"""Main service runner for the Polymarket bot.

Multi-strategy system:
  1. Sports Odds Arbitrage — sportsbook vs Polymarket mispricing on live games
  2. Maker Spread Capture — passive liquidity provision on wide-spread markets
  3. Crypto Taker — latency arb via direct Binance WS during volatile windows

Wires together: LiveFeedManager (WS data), PositionManager (lifecycle),
strategies, RiskEngine, execution, and the strategy router.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from .binance_ws import BinanceDirectFeed, VolatilityScheduler
from .config import load_config
from .execution import ExecutionGateway, LiveClobExecutor, PaperExecutor
from .features import compute_features
from .feeds import FeedAdapter
from .metrics import recompute_metrics
from .odds_client import OddsClient, LEAGUE_TO_SPORT
from .positions import PositionManager
from .risk import RiskEngine
from .session_log import SessionLogger
from .sports_ws import SportsWsClient
from .storage import JsonlStore
from .strategy_maker import MakerStrategy
from .strategy_router import StrategyAllocation, StrategyRouter
from .strategy_sports import SportsArbStrategy
from .strategy_taker import TakerGate
from .state import BotState
from .ws_feeds import LiveFeedManager

LOG = logging.getLogger("polymarket_bot")

BOT_DIR = Path(__file__).resolve().parent.parent


def _json(data: Any, status: int = 200) -> web.Response:
    return web.Response(
        status=status,
        text=json.dumps(data),
        content_type="application/json",
    )


class PolymarketBotService:
    def __init__(self) -> None:
        cfg = load_config()
        self._cfg = cfg
        self._state = BotState.from_config(cfg)
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._tick_task: asyncio.Task | None = None
        self._resolution_task: asyncio.Task | None = None
        self._sports_task: asyncio.Task | None = None
        self._odds_task: asyncio.Task | None = None

        # Data feeds
        self._live_feed = LiveFeedManager(cfg) if cfg.use_public_polymarket_feed else None
        self._fallback_feed = FeedAdapter()
        self._sports_ws = SportsWsClient()
        self._toa_keys = [k.strip() for k in cfg.the_odds_api_keys.split(",") if k.strip()]
        self._odds_client = OddsClient(
            api_key=cfg.odds_api_key,
            the_odds_api_keys=self._toa_keys,
            poll_interval_sec=cfg.sports_poll_sec,
        )
        self._binance_feed = BinanceDirectFeed()
        self._vol_scheduler = VolatilityScheduler(
            always_active=cfg.crypto_taker_always_active,
        )

        # Position and risk
        self._pos_mgr = PositionManager()
        self._risk = RiskEngine(self._state.risk)

        # Strategies
        self._maker = MakerStrategy(
            quote_size=cfg.quote_size_usd,
            max_position_shares=cfg.quote_size_usd * cfg.max_concurrent_positions,
        )
        self._taker_gate = TakerGate(
            min_net_edge_bps=cfg.taker_min_net_edge_bps,
            min_momentum_pct=cfg.taker_min_momentum_pct,
        )
        self._sports_arb = SportsArbStrategy(
            min_edge_bps=cfg.sports_min_edge_bps,
            cooldown_sec=cfg.sports_cooldown_sec,
        )
        self._router = StrategyRouter(
            allocation=StrategyAllocation(
                sports_pct=cfg.sports_alloc_pct,
                maker_pct=cfg.maker_alloc_pct,
                crypto_pct=cfg.crypto_alloc_pct,
            ),
            total_budget_usd=cfg.max_portfolio_exposure_usd,
        )

        # Execution
        self._live_exec = LiveClobExecutor(cfg) if cfg.mode == "live" else None
        self._exec: ExecutionGateway = (
            self._live_exec if self._live_exec is not None and self._live_exec.enabled else PaperExecutor()
        )

        # Persistence
        self._store = JsonlStore(BOT_DIR / "data" / f"trades_{cfg.mode}.jsonl")
        self._state.trades = self._store.tail(500)
        self._pos_mgr.load_from_records(self._state.trades)

        self._session_log = SessionLogger(BOT_DIR / "logs")
        self._last_risk_reason: str = ""

        recompute_metrics(self._state.metrics, self._state.trades, self._pos_mgr)
        self._setup_routes()

    def _setup_routes(self) -> None:
        self._app.router.add_get("/status", self._status)
        self._app.router.add_get("/metrics", self._metrics)
        self._app.router.add_get("/positions", self._positions)
        self._app.router.add_get("/trades", self._trades)
        self._app.router.add_get("/risk", self._risk_get)
        self._app.router.add_post("/risk", self._risk_post)
        self._app.router.add_post("/control/start", self._start)
        self._app.router.add_post("/control/stop", self._stop)
        self._app.router.add_post("/control/pause", self._pause)
        self._app.router.add_get("/strategies", self._strategies_status)
        self._app.router.add_get("/sports", self._sports_status)

    # ── API endpoints ───────────────────────────────────────────────

    async def _status(self, _: web.Request | None) -> web.Response:
        status = self._state.status()
        status["uses_public_polymarket_feed"] = self._cfg.use_public_polymarket_feed
        status["has_live_trading_credentials"] = self._cfg.has_live_trading_credentials
        status["live_ready"] = self._cfg.mode != "live" or self._cfg.has_live_trading_credentials
        status["execution_mode"] = (
            "live_dry_run"
            if self._cfg.mode == "live" and self._cfg.live_dry_run and self._live_exec and self._live_exec.enabled
            else ("live" if self._cfg.mode == "live" and self._live_exec and self._live_exec.enabled else "paper")
        )
        status["live_exec_enabled"] = bool(self._live_exec and self._live_exec.enabled)
        status["live_exec_error"] = self._live_exec.error if self._live_exec else ""
        status["strategies"] = self._router.status()
        status["binance_connected"] = self._binance_feed.connected
        status["sports_ws_connected"] = self._sports_ws.connected
        status["live_games"] = len(self._sports_ws.live_games)
        return _json(status)

    async def _metrics(self, _: web.Request) -> web.Response:
        return _json(self._state.metrics.to_dict())

    async def _positions(self, _: web.Request) -> web.Response:
        open_pos = [p.to_dict() for p in self._pos_mgr.open_positions]
        return _json({"positions": open_pos, "count": len(open_pos)})

    async def _trades(self, request: web.Request) -> web.Response:
        limit_raw = request.query.get("limit", "50")
        try:
            limit = max(1, min(500, int(limit_raw)))
        except ValueError:
            limit = 50
        return _json({"trades": self._state.trades[-limit:]})

    async def _risk_get(self, _: web.Request) -> web.Response:
        return _json(self._state.risk.to_dict())

    async def _risk_post(self, request: web.Request) -> web.Response:
        payload = await request.json()
        for key in ("daily_loss_limit_usd", "max_position_pct", "max_portfolio_exposure_usd"):
            if key in payload:
                setattr(self._state.risk, key, float(payload[key]))
        self._state.last_update_ts = time.time()
        return _json({"ok": True, "risk": self._state.risk.to_dict()})

    async def _start(self, _: web.Request) -> web.Response:
        if self._cfg.mode == "live" and not self._cfg.has_live_trading_credentials:
            return _json(
                {"ok": False, "error": "live_credentials_missing",
                 "message": "Set PMBOT_POLY_PRIVATE_KEY and PMBOT_POLY_FUNDER before starting live mode."},
                status=400,
            )
        self._state.running = True
        self._state.paused = False
        self._state.last_update_ts = time.time()
        self._session_log.log("start")
        return _json({"ok": True, "running": True, "paused": False})

    async def _stop(self, _: web.Request) -> web.Response:
        self._state.running = False
        self._state.paused = True
        self._state.last_update_ts = time.time()
        self._session_log.log("stop")
        return _json({"ok": True, "running": False, "paused": True})

    async def _pause(self, _: web.Request) -> web.Response:
        self._state.paused = True
        self._state.last_update_ts = time.time()
        self._session_log.log("pause")
        return _json({"ok": True, "running": self._state.running, "paused": True})

    async def _strategies_status(self, _: web.Request) -> web.Response:
        return _json(self._router.status())

    async def _sports_status(self, _: web.Request) -> web.Response:
        live = self._sports_ws.live_games
        matched = self._sports_arb.matched_markets
        return _json({
            "sports_ws_connected": self._sports_ws.connected,
            "live_games": [
                {"slug": g.slug, "league": g.league, "teams": f"{g.home_team} vs {g.away_team}",
                 "score": g.score, "period": g.period, "elapsed": g.elapsed}
                for g in live
            ],
            "matched_markets": len(matched),
            "odds_api": {
                "odds_api_io": {
                    "has_key": self._odds_client.has_io_key,
                    "requests": self._odds_client.io_requests,
                },
                "the_odds_api": {
                    "has_key": self._odds_client.has_toa_key,
                    "keys": self._odds_client.toa_key_count,
                    "active_key": self._odds_client.toa_active_key,
                    "requests": self._odds_client.toa_requests,
                    "remaining_quota": self._odds_client.toa_remaining_quota,
                },
                "total_requests": self._odds_client.requests_used,
            },
        })

    # ── Core tick loops ──────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """Main tick loop — runs maker + crypto taker strategies."""
        while True:
            await asyncio.sleep(max(0.05, self._cfg.cycle_ms / 1000.0))
            if not (self._state.running and not self._state.paused):
                continue
            try:
                await self._tick_crypto()
            except Exception as exc:
                LOG.error("Crypto tick error: %s", exc, exc_info=True)
            self._state.last_update_ts = time.time()

    async def _tick_crypto(self) -> None:
        """Maker quoting + crypto taker signal evaluation."""
        if self._live_feed:
            snap = await self._live_feed.next_snapshot()
            self._state.feed_staleness = self._live_feed.staleness_report()
        else:
            snap = await self._fallback_feed.next_snapshot()

        feat = compute_features(snap)

        inv_ratio = self._pos_mgr.inventory_ratio(
            feat.market_id, self._cfg.quote_size_usd * self._cfg.max_concurrent_positions,
        )
        at_max = len(self._pos_mgr.open_positions) >= self._cfg.max_concurrent_positions
        per_market = self._pos_mgr.count_for_market(feat.market_id)

        risk = self._risk.check(
            pnl_total_usd=self._state.metrics.pnl_total_usd,
            open_positions=len(self._pos_mgr.open_positions),
            projected_exposure_usd=self._pos_mgr.get_exposure_usd(),
            max_concurrent=self._cfg.max_concurrent_positions,
            per_market_count=per_market,
            per_market_limit=self._cfg.per_market_max_positions,
        )
        if not risk.allow and risk.reason != self._last_risk_reason:
            self._session_log.log_risk(reason=risk.reason, pnl=self._state.metrics.pnl_total_usd)
        self._last_risk_reason = risk.reason if not risk.allow else ""

        # ── Maker quoting (always active) ──
        self._router.mark_tick("maker")
        if risk.allow:
            self._router.activate("maker", "risk_ok")
            quote = self._maker.quote(feat, inventory_ratio=inv_ratio, at_max_long=at_max)
            fills = self._exec.execute_maker_cycle(quote)
            for f in fills:
                self._record_fill(f, feat, strategy="maker")
        else:
            self._router.deactivate("maker", risk.reason)

        # ── Crypto taker (schedule-aware) ──
        vol_active, vol_reason = self._vol_scheduler.is_active()
        if vol_active:
            self._router.activate("crypto", vol_reason)
        else:
            self._router.deactivate("crypto", vol_reason)

        self._router.mark_tick("crypto")
        if vol_active and risk.allow and not at_max:
            # Use direct Binance feed if connected, fall back to RTDS
            rtds = self._live_feed.rtds if self._live_feed else None
            taker = self._taker_gate.evaluate(feat, rtds=rtds)

            if self._binance_feed.connected:
                mom = self._binance_feed.momentum_pct("btcusdt", lookback_sec=30.0)
                if abs(mom) > abs(taker.momentum_pct):
                    taker = self._taker_gate.evaluate(feat, rtds=rtds, external_fair=None)

            self._state.last_taker_signal = {
                "should_trade": taker.should_trade,
                "side": taker.side,
                "edge_after_fee_bps": taker.edge_after_fee_bps,
                "reason": taker.reason,
                "implied_fair": taker.implied_fair,
                "momentum_pct": taker.momentum_pct,
                "ts": time.time(),
            }
            if taker.should_trade:
                self._router.record_signal("crypto")
                self._session_log.log_signal(
                    side=taker.side, edge=taker.edge_after_fee_bps,
                    momentum=taker.momentum_pct, implied_fair=taker.implied_fair,
                )
                token_id = feat.yes_token_id if taker.side == "buy_yes" else feat.no_token_id
                price = feat.best_ask if "buy" in taker.side else feat.best_bid
                taker_result = self._exec.execute_taker(
                    token_id=token_id, side=taker.side,
                    price=price, size=self._cfg.quote_size_usd,
                )
                if taker_result and taker_result.filled:
                    self._record_taker_fill(taker_result, feat, taker.side, strategy="crypto")

        recompute_metrics(self._state.metrics, self._state.trades, self._pos_mgr)

    async def _sports_tick_loop(self) -> None:
        """Sports arbitrage tick loop — runs on its own cadence."""
        while True:
            await asyncio.sleep(2.0)
            if not (self._state.running and not self._state.paused):
                continue
            try:
                await self._tick_sports()
            except Exception as exc:
                LOG.error("Sports tick error: %s", exc, exc_info=True)

    async def _tick_sports(self) -> None:
        """Match PM markets to sportsbook odds, evaluate arb signals."""
        if not self._live_feed:
            return

        self._router.mark_tick("sports")

        live_games = self._sports_ws.live_games
        has_live = len(live_games) > 0

        # Only poll odds APIs when there are live games (saves quota)
        if not has_live:
            # Light poll from odds-api.io only (cheap) — just basketball
            io_events = await self._odds_client.fetch_live_events("basketball")
            if not io_events:
                self._router.deactivate("sports", "no_live_games")
                return
            odds_events = io_events
        else:
            # Determine sport slugs from live games
            active_sport_slugs: set[str] = set()
            for g in live_games:
                league_lower = g.league.lower()
                for abbr, sport_slug in LEAGUE_TO_SPORT.items():
                    if abbr in league_lower:
                        active_sport_slugs.add(sport_slug)
                        break
                else:
                    active_sport_slugs.add("basketball")

            # Use full merge (both providers) when games are live
            odds_events = await self._odds_client.fetch_all_live_sports(
                list(active_sport_slugs)
            )

        if not odds_events:
            self._router.deactivate("sports", "no_odds_data")
            return

        self._router.activate("sports", f"{len(self._sports_ws.live_games)} live games")

        # Match markets
        self._sports_arb.match_markets(
            pm_markets=self._live_feed.markets,
            books=self._live_feed.books,
            odds_events=odds_events,
            sports_ws=self._sports_ws,
        )

        # Evaluate signals
        signals = self._sports_arb.evaluate_all()
        for sig in signals:
            risk = self._risk.check(
                pnl_total_usd=self._state.metrics.pnl_total_usd,
                open_positions=len(self._pos_mgr.open_positions),
                projected_exposure_usd=self._pos_mgr.get_exposure_usd(),
                max_concurrent=self._cfg.max_concurrent_positions,
                per_market_count=self._pos_mgr.count_for_market(sig.market_id),
                per_market_limit=self._cfg.per_market_max_positions,
            )
            if not risk.allow:
                continue

            self._router.record_signal("sports")
            self._session_log.log("sports_signal",
                side=sig.side, edge_bps=sig.edge_bps, reason=sig.reason,
                pm_price=sig.pm_price, implied_prob=sig.implied_prob,
                market_id=sig.market_id,
            )

            result = self._exec.execute_taker(
                token_id=sig.token_id, side=sig.side,
                price=sig.pm_price, size=self._cfg.quote_size_usd,
            )
            if result and result.filled:
                result.market_id = sig.market_id
                tr = self._exec.to_trade_record(result, f"sports:{sig.market_id[:16]}")
                side = "yes" if "yes" in sig.side else "no"
                pos = self._pos_mgr.open_position(
                    market_id=sig.market_id,
                    token_id=sig.token_id,
                    side=side,
                    price=sig.pm_price,
                    size=self._cfg.quote_size_usd,
                    is_maker=False,
                )
                tr["position_id"] = pos.id
                tr["market_id"] = pos.market_id
                tr["token_id"] = pos.token_id
                tr["strategy"] = "sports"
                self._state.trades.append(tr)
                self._store.append(tr)
                self._router.record_trade("sports")
                self._session_log.log_fill(
                    side=sig.side, price=sig.pm_price, size=self._cfg.quote_size_usd,
                    is_maker=False, strategy="sports", edge_bps=sig.edge_bps,
                )
                LOG.info(
                    "SPORTS FILL: %s %s @ %.4f edge=%.0fbps market=%s",
                    sig.side, pos.id, sig.pm_price, sig.edge_bps, sig.market_id[:16],
                )

        if signals:
            recompute_metrics(self._state.metrics, self._state.trades, self._pos_mgr)

    async def _odds_poll_loop(self) -> None:
        """Background loop to refresh odds from odds-api.io (cheap provider).
        the-odds-api.com data is only fetched via the sports tick loop when
        live games are detected, to conserve its 500 req/month quota.
        """
        while True:
            await asyncio.sleep(self._cfg.sports_poll_sec)
            if not self._cfg.odds_api_key:
                continue
            try:
                matched = self._sports_arb.matched_markets
                fetched: set[int] = set()
                for sm in matched.values():
                    evt = sm.odds_event
                    if evt and isinstance(evt.event_id, int) and evt.event_id not in fetched:
                        await self._odds_client.fetch_odds(evt.event_id)
                        fetched.add(evt.event_id)
            except Exception as exc:
                LOG.warning("Odds poll error: %s", exc)

    # ── Fill recording helpers ───────────────────────────────────────

    def _record_fill(self, f, feat, strategy: str = "maker") -> None:
        tr = self._exec.to_trade_record(f, feat.symbol)
        side = "yes" if "yes" in f.side.lower() else "no"
        pos = self._pos_mgr.open_position(
            market_id=f.market_id or feat.market_id,
            token_id=f.token_id or feat.yes_token_id,
            side=side,
            price=f.price,
            size=f.size,
            market_end_ts=feat.end_ts,
            is_maker=f.is_maker,
        )
        tr["position_id"] = pos.id
        tr["market_id"] = pos.market_id
        tr["token_id"] = pos.token_id
        tr["strategy"] = strategy
        self._state.trades.append(tr)
        self._store.append(tr)
        self._router.record_trade(strategy)
        self._session_log.log_fill(
            side=f.side, price=f.price, size=f.size,
            is_maker=f.is_maker, market_id=pos.market_id, strategy=strategy,
        )

    def _record_taker_fill(self, result, feat, taker_side: str, strategy: str = "crypto") -> None:
        tr = self._exec.to_trade_record(result, feat.symbol)
        side = "yes" if "yes" in taker_side else "no"
        pos = self._pos_mgr.open_position(
            market_id=feat.market_id,
            token_id=result.token_id or feat.yes_token_id,
            side=side,
            price=result.price,
            size=self._cfg.quote_size_usd,
            market_end_ts=feat.end_ts,
            is_maker=False,
        )
        tr["position_id"] = pos.id
        tr["market_id"] = pos.market_id
        tr["token_id"] = pos.token_id
        tr["strategy"] = strategy
        self._state.trades.append(tr)
        self._store.append(tr)
        self._router.record_trade(strategy)
        self._session_log.log_fill(
            side=taker_side, price=result.price, size=self._cfg.quote_size_usd,
            is_maker=False, strategy=strategy,
        )

    # ── Resolution polling ──────────────────────────────────────────

    async def _resolution_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            if not self._live_feed:
                continue
            try:
                await self._check_resolutions()
            except Exception as exc:
                LOG.warning("Resolution check error: %s", exc)

    async def _check_resolutions(self) -> None:
        seen_markets: set[str] = set()
        for pos in self._pos_mgr.open_positions:
            if pos.market_id in seen_markets:
                continue
            seen_markets.add(pos.market_id)
            assert self._live_feed is not None
            result = await self._live_feed.check_resolution(pos.market_id)
            if result is None:
                continue
            winning = result.get("winning_outcome", "unknown")
            records = self._pos_mgr.resolve_market(pos.market_id, winning)
            for tr in records:
                self._state.trades.append(tr)
                self._store.append(tr)
                self._session_log.log_resolution(
                    market_id=pos.market_id, winning=winning,
                    pnl=tr["pnl_delta"], status=tr["status"],
                )
            if records:
                recompute_metrics(self._state.metrics, self._state.trades, self._pos_mgr)
                LOG.info(
                    "Resolved %d positions for market %s -> %s",
                    len(records), pos.market_id[:16], winning,
                )

    # ── Lifecycle ───────────────────────────────────────────────────

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._cfg.host, self._cfg.port)
        await self._site.start()

        if self._live_feed:
            await self._live_feed.start()

        await self._sports_ws.start()
        await self._binance_feed.start()

        self._tick_task = asyncio.create_task(self._heartbeat_loop())
        self._resolution_task = asyncio.create_task(self._resolution_loop())
        self._sports_task = asyncio.create_task(self._sports_tick_loop())
        self._odds_task = asyncio.create_task(self._odds_poll_loop())

        LOG.info(
            "Polymarket bot started on %s:%s [sports_ws=on binance=on odds_io=%s the_odds_api=%s (%d keys)]",
            self._cfg.host, self._cfg.port,
            "on" if self._cfg.odds_api_key else "off",
            "on" if self._toa_keys else "off",
            len(self._toa_keys),
        )

    async def close(self) -> None:
        for task in (self._tick_task, self._resolution_task, self._sports_task, self._odds_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self._live_feed:
            with contextlib.suppress(Exception):
                await self._live_feed.close()
        with contextlib.suppress(Exception):
            await self._sports_ws.close()
        with contextlib.suppress(Exception):
            await self._binance_feed.close()
        with contextlib.suppress(Exception):
            await self._odds_client.close()
        self._session_log.close()
        if self._runner is not None:
            await self._runner.cleanup()


async def _run_forever() -> None:
    service = PolymarketBotService()
    await service.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await service.close()


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(name)-20s %(levelname)8s | %(message)s",
        datefmt="%H:%M:%S",
        level=logging.INFO,
    )
    asyncio.run(_run_forever())


if __name__ == "__main__":
    main()
