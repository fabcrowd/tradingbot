"""Full health check: connect to WS, grab snapshot + config, report all subsystems."""
import asyncio, aiohttp, json, time

async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://localhost:8080/ws") as ws:
            snap = conf = None
            for _ in range(6):
                msg = await ws.receive(timeout=5)
                d = json.loads(msg.data)
                if d["type"] == "snapshot":
                    snap = d["data"]
                elif d["type"] == "config":
                    conf = d["data"]
                if snap and conf:
                    break

            if not snap:
                print("FAIL: no snapshot received")
                return

            print("=" * 60)
            print("BACKEND HEALTH CHECK", time.strftime("%H:%M:%S"))
            print("=" * 60)

            # WS connectivity
            print(f"\n[WS] connected=OK")

            # Mode
            print(f"[MODE] running={snap.get('running')} mode={snap.get('mode')}")

            # Scalp engine
            sc = snap.get("scalp")
            if not sc:
                print("[SCALP] MISSING — scalp key not in snapshot")
                return
            print(f"\n[SCALP ENGINE] enabled={sc['enabled']} sim_mode={sc['sim_mode']} venue={sc.get('venue')}")

            # Warmup
            wu = sc.get("warmup", {})
            print(f"[WARMUP] phase={wu.get('phase')} champion_found={wu.get('champion_found')} progress={wu.get('progress_pct')}%")

            # Active modes per pair
            modes = sc.get("active_modes", {})
            print(f"\n[ACTIVE MODES]")
            for pk, m in modes.items():
                print(f"  {pk}: {m}")

            # Champion
            ch = sc.get("champion")
            if ch:
                print(f"\n[CHAMPION] mode={ch['mode']} score={ch['score']:.1f} sharpe={ch['sharpe']:.1f} "
                      f"pf={ch['profit_factor']:.1f} wr={ch['win_rate']:.1%} trades={ch['trade_count']}")
            else:
                print(f"\n[CHAMPION] none loaded")

            # WFO
            wfo = sc.get("wfo")
            if wfo:
                print(f"[WFO] enabled={wfo.get('enabled')} champion_active={wfo.get('champion_active')} "
                      f"next_in={wfo.get('seconds_until_next')}s data_pct={wfo.get('data_progress_pct')}%")
            else:
                print("[WFO] not present in snapshot")

            # Tuner
            tu = sc.get("tuner")
            if tu:
                print(f"[TUNER] {json.dumps(tu, default=str)[:200]}")
            else:
                print("[TUNER] no tuner snapshot")

            # Trader
            tr = sc.get("trader", {})
            print(f"\n[TRADER] open_count={tr.get('open_count')} pending={tr.get('pending_count', 0)} "
                  f"daily_pnl={tr.get('daily_pnl')} sim_mode={tr.get('sim_mode')} "
                  f"reserved={tr.get('reserved_capital')}")
            positions = tr.get("open_positions", {})
            if positions:
                for pk, p in positions.items():
                    print(f"  OPEN: {p.get('pair_key')} dir={p.get('direction')} entry={p.get('entry')} "
                          f"stop={p.get('stop')} tp={p.get('tp')} pnl={p.get('unrealized_pnl')}")
            else:
                print("  No open positions (filled legs)")
            pending = tr.get("pending_entries", {})
            if pending:
                for pk, p in pending.items():
                    print(f"  PENDING: {p.get('pair_key')} dir={p.get('direction')} entry={p.get('entry')}")
            ex_pos = sc.get("exchange_positions", [])
            if ex_pos:
                print(f"  [EXCHANGE FCM] {len(ex_pos)} leg(s):")
                for row in ex_pos:
                    print(f"    {row.get('product_id')} {row.get('direction')} x{row.get('qty')} @ {row.get('entry_price')}")
            elif sc.get("venue") == "coinbase_perps":
                print("  [EXCHANGE FCM] flat (no configured symbols with size)")

            history = tr.get("trade_history", [])
            print(f"  Trade history: {len(history)} trades this session")

            # Indicators
            ind = sc.get("indicators", {})
            print(f"\n[INDICATORS]")
            for pk, iv in ind.items():
                print(f"  {pk}: candles={iv.get('candles')} ready={iv.get('ready')} "
                      f"rsi={iv.get('rsi')} atr={iv.get('atr')} ema_bull={iv.get('ema_bullish')} "
                      f"vol={iv.get('volume_confirmed')}")

            # Candles
            candles = sc.get("candles", {})
            print(f"\n[CANDLE FEED]")
            for pk, cd in candles.items():
                closed = cd.get("closed", [])
                live = cd.get("live")
                print(f"  {pk}: {len(closed)} closed bars, live={'YES' if live else 'NO'} "
                      f"interval={cd.get('interval')}m")

            # Balances
            bal = sc.get("balances", {})
            if bal:
                spot = bal.get("spot", [])
                fut = bal.get("futures", {})
                print(f"\n[BALANCES] spot_accounts={len(spot)} futures_buying_power={fut.get('buying_power')}")
            else:
                print(f"\n[BALANCES] not available")

            print(f"\n{'=' * 60}")
            print("ALL SYSTEMS CHECKED")
            print("=" * 60)

asyncio.run(main())
