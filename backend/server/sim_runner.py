"""Simulation harness — systematic spread_bps sweep with quality scoring.

Four-phase pipeline per pair:
  1. Grid sweep: test spread_bps values from floor to ceiling using run_backtest(mode="simulated").
  2. Fee stress:  rerun top-N spreads at 1.5x fees to test robustness.
  3. Walk-forward validation: 3-fold temporal split (train/holdout) to detect overfitting.
  4. Quality scoring: 5-dimension score (sample size, expectancy, risk management, robustness, execution).

Plateau detection identifies the widest contiguous range of spread_bps where net_pnl >= 80% of peak,
signaling parameter stability rather than a fragile optimum.

Only spread_bps is swept because run_backtest(mode="simulated") re-prices existing fills around a
hypothetical mid-price — it cannot simulate *which* fills would have occurred under different order
sizes, cycle times, or other execution parameters.

Usage:
  python -m backend.server.sim_runner
  python -m backend.server.sim_runner --file data/trades_live.jsonl --pair TEL_USD
  python -m backend.server.sim_runner --out sim_report.json
"""

from __future__ import annotations

import argparse
import json

from collections import defaultdict
from pathlib import Path

from .backtest import _load_events, run_backtest
from .fee_schedule import maker_fee_bps

DEFAULT_DATA = Path(__file__).resolve().parent.parent.parent / "data" / "trades_live.jsonl"
MIN_SELLS_FOR_SIM = 20
SPREAD_FLOOR = 1
SPREAD_CEILING = 200
SPREAD_STEP = 1
FEE_STRESS_FACTOR = 1.5
TOP_N_FOR_STRESS = 5
WF_FOLDS = 3
PLATEAU_THRESHOLD_PCT = 0.80


def _split_folds(events: list[dict], n_folds: int) -> list[tuple[list[dict], list[dict]]]:
    """Split events into n temporal folds. Each fold uses prior folds as train and itself as test."""
    chunk = len(events) // n_folds
    if chunk < 5:
        return []
    folds: list[tuple[list[dict], list[dict]]] = []
    for i in range(1, n_folds):
        train = events[: chunk * i]
        test = events[chunk * i: chunk * (i + 1)]
        if len(test) >= 3:
            folds.append((train, test))
    if n_folds >= 2:
        train_last = events[: chunk * (n_folds - 1)]
        test_last = events[chunk * (n_folds - 1):]
        if len(test_last) >= 3:
            if not folds or folds[-1][1] is not test_last:
                folds.append((train_last, test_last))
    return folds


def _fee_stressed_events(events: list[dict], factor: float) -> list[dict]:
    """Return events with fees multiplied by factor."""
    stressed: list[dict] = []
    for e in events:
        rec = dict(e)
        rec["fee"] = float(rec.get("fee", 0)) * factor
        stressed.append(rec)
    return stressed


def _detect_plateau(sweep: list[dict], threshold_pct: float) -> dict | None:
    """Find the widest contiguous range of spread_bps where pnl >= threshold_pct of peak."""
    if not sweep:
        return None
    peak_pnl = max(r["realized_pnl"] for r in sweep)
    if peak_pnl <= 0:
        return None
    cutoff = peak_pnl * threshold_pct

    best_start = best_end = -1
    best_len = 0
    cur_start = -1

    for i, r in enumerate(sweep):
        if r["realized_pnl"] >= cutoff:
            if cur_start < 0:
                cur_start = i
            length = i - cur_start + 1
            if length > best_len:
                best_len = length
                best_start = cur_start
                best_end = i
        else:
            cur_start = -1

    if best_len < 2:
        return None

    return {
        "start_bps": sweep[best_start]["spread_bps"],
        "end_bps": sweep[best_end]["spread_bps"],
        "width_bps": sweep[best_end]["spread_bps"] - sweep[best_start]["spread_bps"],
        "peak_pnl": round(peak_pnl, 6),
        "cutoff_pnl": round(cutoff, 6),
        "count": best_len,
    }


def _quality_score(result: dict, _total_events: int, stressed_pnl: float | None) -> dict:
    """5-dimension quality score inspired by Backtest Expert methodology."""
    sells = result.get("total_sells", 0)
    pnl = result.get("realized_pnl", 0.0)
    win_rate = result.get("win_rate", 0.0)
    max_dd = result.get("max_drawdown", 0.0)
    sharpe = result.get("sharpe_ratio", 0.0)
    avg_pnl = result.get("avg_pnl_per_sell", 0.0)

    sample_score = min(1.0, sells / 50.0)

    expectancy_raw = avg_pnl * (win_rate / 100.0) if win_rate > 0 else 0.0
    expectancy_score = min(1.0, max(0.0, expectancy_raw * 100 + 0.5))

    risk_score = 0.0
    if max_dd > 0 and pnl != 0:
        calmar = pnl / max_dd
        risk_score = min(1.0, max(0.0, calmar / 3.0))
    elif pnl > 0:
        risk_score = 1.0

    robustness_score = 0.5
    if stressed_pnl is not None and pnl > 0:
        ratio = stressed_pnl / pnl
        robustness_score = min(1.0, max(0.0, ratio))

    execution_score = min(1.0, max(0.0, sharpe / 2.0)) if sharpe > 0 else 0.0

    weights = [0.15, 0.30, 0.25, 0.20, 0.10]
    scores = [sample_score, expectancy_score, risk_score, robustness_score, execution_score]
    composite = sum(w * s for w, s in zip(weights, scores))

    return {
        "composite": round(composite, 4),
        "sample_size": round(sample_score, 4),
        "expectancy": round(expectancy_score, 4),
        "risk_management": round(risk_score, 4),
        "robustness": round(robustness_score, 4),
        "execution_realism": round(execution_score, 4),
    }


def _run_pair(
    pair_key: str,
    events: list[dict],
    fee_schedule: str,
    volume_30d: float,
) -> dict:
    """Full 4-phase simulation for a single pair."""
    sells = [e for e in events if e.get("side") == "sell"]
    if len(sells) < MIN_SELLS_FOR_SIM:
        return {
            "pair_key": pair_key,
            "skipped": True,
            "reason": f"Only {len(sells)} sells (need >= {MIN_SELLS_FOR_SIM})",
            "sell_count": len(sells),
        }

    base_maker_fee = maker_fee_bps(volume_30d, fee_schedule)

    sweep: list[dict] = []
    for bps in range(SPREAD_FLOOR, SPREAD_CEILING + 1, SPREAD_STEP):
        r = run_backtest(events, mode="simulated", spread_bps=bps)
        sweep.append({
            "spread_bps": bps,
            "realized_pnl": r["realized_pnl"],
            "win_rate": r["win_rate"],
            "total_sells": r["total_sells"],
            "total_wins": r["total_wins"],
            "max_drawdown": r["max_drawdown"],
            "sharpe_ratio": r["sharpe_ratio"],
            "avg_pnl_per_sell": r["avg_pnl_per_sell"],
            "total_win_dollars": r["total_win_dollars"],
        })

    sweep.sort(key=lambda x: x["realized_pnl"], reverse=True)
    top_n = sweep[:TOP_N_FOR_STRESS]

    stressed_events = _fee_stressed_events(events, FEE_STRESS_FACTOR)
    stress_results: list[dict] = []
    for entry in top_n:
        bps = entry["spread_bps"]
        sr = run_backtest(stressed_events, mode="simulated", spread_bps=bps)
        stress_results.append({
            "spread_bps": bps,
            "normal_pnl": entry["realized_pnl"],
            "stressed_pnl": sr["realized_pnl"],
            "pnl_retention": round(sr["realized_pnl"] / entry["realized_pnl"], 4)
            if entry["realized_pnl"] > 0 else 0.0,
        })

    sweep_by_bps = sorted(sweep, key=lambda x: x["spread_bps"])
    plateau = _detect_plateau(sweep_by_bps, PLATEAU_THRESHOLD_PCT)

    folds = _split_folds(events, WF_FOLDS)
    wf_results: list[dict] = []
    best_bps = top_n[0]["spread_bps"] if top_n else SPREAD_FLOOR
    for i, (train, test) in enumerate(folds):
        train_sweep: list[dict] = []
        for bps in range(SPREAD_FLOOR, SPREAD_CEILING + 1, SPREAD_STEP):
            r = run_backtest(train, mode="simulated", spread_bps=bps)
            train_sweep.append({"bps": bps, "pnl": r["realized_pnl"]})
        train_sweep.sort(key=lambda x: x["pnl"], reverse=True)
        fold_best_bps = train_sweep[0]["bps"] if train_sweep else best_bps

        test_at_optimal = run_backtest(test, mode="simulated", spread_bps=fold_best_bps)
        test_at_default = run_backtest(test, mode="simulated", spread_bps=best_bps)

        wf_results.append({
            "fold": i + 1,
            "train_best_bps": fold_best_bps,
            "test_pnl_at_train_best": test_at_optimal["realized_pnl"],
            "test_pnl_at_overall_best": test_at_default["realized_pnl"],
            "test_sells": test_at_optimal["total_sells"],
        })

    best_entry = top_n[0] if top_n else sweep_by_bps[0]
    stressed_pnl = stress_results[0]["stressed_pnl"] if stress_results else None
    full_result = run_backtest(events, mode="simulated", spread_bps=best_entry["spread_bps"])
    quality = _quality_score(full_result, len(events), stressed_pnl)

    return {
        "pair_key": pair_key,
        "skipped": False,
        "sell_count": len(sells),
        "buy_count": len(events) - len(sells),
        "base_maker_fee_bps": base_maker_fee,
        "fee_schedule": fee_schedule,
        "best_spread_bps": best_entry["spread_bps"],
        "best_pnl": best_entry["realized_pnl"],
        "best_win_rate": best_entry["win_rate"],
        "best_sharpe": best_entry["sharpe_ratio"],
        "plateau": plateau,
        "quality_score": quality,
        "top_5_sweep": top_n,
        "fee_stress": stress_results,
        "walk_forward": wf_results,
        "full_sweep": sweep_by_bps,
    }


def _infer_pair_meta(pair_key: str) -> tuple[str, float]:
    """Best-effort pair metadata for simulation (fee_schedule, volume_30d)."""
    try:
        import tomllib
        cfg_path = Path(__file__).resolve().parent.parent.parent / "config.toml"
        if cfg_path.exists():
            with cfg_path.open("rb") as f:
                cfg = tomllib.load(f)
            pair_cfg = cfg.get("pairs", {}).get(pair_key, {})
            return pair_cfg.get("fee_schedule", "spot_crypto"), 0.0
    except Exception:
        pass
    return "spot_crypto", 0.0


def run_simulation(
    data_path: Path | None = None,
    pair_filter: str | None = None,
    out_path: str | None = None,
) -> dict:
    """Entry point: run full simulation and return report dict."""
    path = data_path or DEFAULT_DATA
    if not path.exists():
        return {"error": f"Data file not found: {path}"}

    all_events = _load_events(path)
    if not all_events:
        return {"error": "No fill events found"}

    pairs_in_data: dict[str, list[dict]] = defaultdict(list)
    for e in all_events:
        pk = e.get("pair_key", e.get("pair", "unknown"))
        pairs_in_data[pk].append(e)

    if pair_filter:
        if pair_filter not in pairs_in_data:
            return {"error": f"Pair {pair_filter} not found in data"}
        pairs_in_data = {pair_filter: pairs_in_data[pair_filter]}

    report: dict = {"pairs": {}}
    for pk, events in sorted(pairs_in_data.items()):
        fee_schedule, vol = _infer_pair_meta(pk)
        print(f"\n{'='*60}")
        print(f"Simulating {pk} ({len(events)} events, fee_schedule={fee_schedule})")
        print(f"{'='*60}")
        result = _run_pair(pk, events, fee_schedule, vol)
        report["pairs"][pk] = result

        if result.get("skipped"):
            print(f"  SKIPPED: {result['reason']}")
            continue

        print(f"  Best spread_bps: {result['best_spread_bps']}")
        print(f"  Best PnL:        ${result['best_pnl']:.6f}")
        print(f"  Win rate:        {result['best_win_rate']:.1f}%")
        print(f"  Sharpe:          {result['best_sharpe']:.3f}")
        if result["plateau"]:
            p = result["plateau"]
            print(f"  Plateau:         {p['start_bps']}-{p['end_bps']} bps (width {p['width_bps']})")
        print(f"  Quality score:   {result['quality_score']['composite']:.4f}")
        print(f"    Sample:        {result['quality_score']['sample_size']:.4f}")
        print(f"    Expectancy:    {result['quality_score']['expectancy']:.4f}")
        print(f"    Risk Mgmt:     {result['quality_score']['risk_management']:.4f}")
        print(f"    Robustness:    {result['quality_score']['robustness']:.4f}")
        print(f"    Execution:     {result['quality_score']['execution_realism']:.4f}")

        if result["fee_stress"]:
            print("  Fee stress (1.5x):")
            for fs in result["fee_stress"]:
                print(f"    {fs['spread_bps']}bps: ${fs['normal_pnl']:.6f} -> ${fs['stressed_pnl']:.6f} (retain {fs['pnl_retention']:.0%})")

        if result["walk_forward"]:
            print("  Walk-forward:")
            for wf in result["walk_forward"]:
                print(f"    Fold {wf['fold']}: train_best={wf['train_best_bps']}bps, test_pnl=${wf['test_pnl_at_train_best']:.6f}")

    if out_path:
        clean = _strip_full_sweep(report)
        Path(out_path).write_text(json.dumps(clean, indent=2), encoding="utf-8")
        print(f"\nReport written to {out_path}")

    return report


def _strip_full_sweep(report: dict) -> dict:
    """Remove large full_sweep arrays for compact JSON output."""
    out = dict(report)
    out["pairs"] = {}
    for pk, data in report.get("pairs", {}).items():
        d = dict(data)
        d.pop("full_sweep", None)
        out["pairs"][pk] = d
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Spread simulation harness with quality scoring")
    parser.add_argument("--file", default=str(DEFAULT_DATA), help="Path to trades JSONL")
    parser.add_argument("--pair", default=None, help="Filter to a single pair")
    parser.add_argument("--out", default=None, help="Write JSON report to this path")
    args = parser.parse_args()

    run_simulation(
        data_path=Path(args.file),
        pair_filter=args.pair,
        out_path=args.out,
    )


if __name__ == "__main__":
    main()
