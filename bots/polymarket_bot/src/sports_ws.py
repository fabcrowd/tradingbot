"""Polymarket Sports WebSocket client.

Streams real-time game state (scores, periods, possession) from
wss://sports-api.polymarket.com/ws. No auth required. Server sends
all active events on connect — no subscription message needed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientSession, ClientTimeout, WSMsgType

LOG = logging.getLogger("polymarket_bot.sports_ws")

SPORTS_WS_URL = "wss://sports-api.polymarket.com/ws"


@dataclass
class GameState:
    game_id: int
    league: str
    slug: str
    home_team: str
    away_team: str
    status: str
    score: str
    period: str
    elapsed: str
    live: bool
    ended: bool
    turn: str = ""
    finished_ts: str = ""
    last_update: float = 0.0

    @property
    def home_score(self) -> int:
        parts = self.score.split("-")
        try:
            return int(parts[0])
        except (IndexError, ValueError):
            return 0

    @property
    def away_score(self) -> int:
        parts = self.score.split("-")
        try:
            return int(parts[1].split("|")[0])
        except (IndexError, ValueError):
            return 0

    @property
    def score_diff(self) -> int:
        return self.home_score - self.away_score

    def match_key(self) -> str:
        return f"{self.league}:{self.home_team}:{self.away_team}".lower()


class SportsWsClient:
    """Maintains a live connection to Polymarket Sports WS.

    Provides a dict of active games keyed by slug, updated in real time.
    """

    def __init__(self) -> None:
        self._games: dict[str, GameState] = {}
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._connected = False
        self._last_msg_ts: float = 0.0

    @property
    def games(self) -> dict[str, GameState]:
        return self._games

    @property
    def live_games(self) -> list[GameState]:
        return [g for g in self._games.values() if g.live and not g.ended]

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def staleness_sec(self) -> float:
        if self._last_msg_ts <= 0:
            return -1.0
        return time.time() - self._last_msg_ts

    async def start(self) -> None:
        self._task = asyncio.create_task(self._ws_loop())
        LOG.info("SportsWsClient started")

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=ClientTimeout(total=None))
        return self._session

    async def _ws_loop(self) -> None:
        while True:
            try:
                await self._connect()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                LOG.warning("Sports WS lost: %s — reconnecting in 5s", exc)
                self._connected = False
                await asyncio.sleep(5)

    async def _connect(self) -> None:
        sess = await self._get_session()
        async with sess.ws_connect(SPORTS_WS_URL, heartbeat=5) as ws:
            self._connected = True
            LOG.info("Sports WS connected")
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    if msg.data == "ping":
                        await ws.send_str("pong")
                        continue
                    self._handle(msg.data)
                elif msg.type == WSMsgType.PING:
                    await ws.pong()
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSED):
                    break
        self._connected = False

    def _handle(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self._process(item)
        elif isinstance(data, dict):
            self._process(data)

    def _process(self, d: dict[str, Any]) -> None:
        slug = d.get("slug", "")
        if not slug:
            return

        self._last_msg_ts = time.time()
        game = self._games.get(slug)
        if game is None:
            game = GameState(
                game_id=int(d.get("gameId", 0)),
                league=str(d.get("leagueAbbreviation", "")),
                slug=slug,
                home_team=str(d.get("homeTeam", "")),
                away_team=str(d.get("awayTeam", "")),
                status=str(d.get("status", "")),
                score=str(d.get("score", "0-0")),
                period=str(d.get("period", "")),
                elapsed=str(d.get("elapsed", "")),
                live=bool(d.get("live", False)),
                ended=bool(d.get("ended", False)),
                turn=str(d.get("turn", "")),
                finished_ts=str(d.get("finished_timestamp", "")),
                last_update=time.time(),
            )
            self._games[slug] = game
            if game.live:
                LOG.info("New live game: %s %s vs %s (%s)", game.league, game.home_team, game.away_team, game.score)
        else:
            old_score = game.score
            game.status = str(d.get("status", game.status))
            game.score = str(d.get("score", game.score))
            game.period = str(d.get("period", game.period))
            game.elapsed = str(d.get("elapsed", game.elapsed))
            game.live = bool(d.get("live", game.live))
            game.ended = bool(d.get("ended", game.ended))
            game.turn = str(d.get("turn", game.turn))
            game.last_update = time.time()

            if game.score != old_score and game.live:
                LOG.info(
                    "Score change: %s %s vs %s  %s -> %s (%s %s)",
                    game.league, game.home_team, game.away_team,
                    old_score, game.score, game.period, game.elapsed,
                )

    def find_game(self, home: str, away: str, league: str = "") -> GameState | None:
        home_u = home.upper()
        away_u = away.upper()
        league_u = league.upper()
        for g in self._games.values():
            if g.home_team.upper() == home_u and g.away_team.upper() == away_u:
                if not league_u or g.league.upper() == league_u:
                    return g
            if g.away_team.upper() == home_u and g.home_team.upper() == away_u:
                if not league_u or g.league.upper() == league_u:
                    return g
        return None
