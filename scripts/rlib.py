"""Shared helpers for the research scripts (periods, cost model, evaluation)."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from compounder.backtest import ms, quick_returns, run_backtest, to_daily  # noqa: E402
from compounder.data.panel import Panel  # noqa: E402
from compounder.engine import EngineParams  # noqa: E402
from compounder.metrics import daily_stats  # noqa: E402
from compounder.strategy import StrategyParams, compute_targets  # noqa: E402

PERIODS = {
    "train": ("2020-04-01", "2024-01-01"),
    "valid": ("2024-01-01", "2025-01-01"),
    "dev": ("2020-04-01", "2025-01-01"),
    "holdout": ("2025-01-01", "2026-11-01"),
}
COST = 0.0008          # fee 5 bps + slippage 3 bps per side
BAND = 0.25


def load_panel(path: str | Path = ROOT / "data" / "research" / "hourly.npz") -> Panel:
    return Panel.load(path)


def clip_panel(panel: Panel, end: str) -> Panel:
    """Drop every bar at or after ``end`` so later data cannot be touched by accident.

    Symbols that have no bar before ``end`` are dropped too (they could not trade).
    """
    hi = int(np.searchsorted(panel.times, ms(end), side="left"))
    cut = panel.slice(0, hi)
    alive = np.isfinite(cut.close).any(axis=0)
    return cut.select([s for s, a in zip(cut.symbols, alive) if a])


def quick_eval(panel: Panel, p: StrategyParams, period: str = "train", cost: float = COST, band: float = BAND,
               targets=None) -> dict[str, Any]:
    """After-cost daily statistics of the linear approximation over a period."""
    tg = targets if targets is not None else compute_targets(panel, p)
    ret, close_ms, turn = quick_returns(panel, tg.weights, cost=cost, band=band)
    lo, hi = (ms(x) for x in PERIODS[period])
    sel = (panel.times >= lo) & (panel.times < hi)
    d, _ = to_daily(ret[sel], close_ms[sel])
    st = daily_stats(d)
    gross = np.abs(tg.weights[sel]).sum(axis=1)
    st["avg_gross"] = float(gross.mean())
    st["turnover_per_day"] = turn * 24
    return st


def brief(st: dict[str, Any]) -> str:
    return (f"sharpe {st['sharpe']:5.2f}  cagr {st['cagr'] * 100:7.1f}%  vol/d {st['daily_vol'] * 100:5.2f}%  "
            f"mdd {st['max_drawdown'] * 100:5.1f}%  gross {st.get('avg_gross', float('nan')):4.2f}  "
            f"turn/d {st.get('turnover_per_day', float('nan')):5.2f}")


def engine_eval(panel: Panel, p: StrategyParams, ep: EngineParams, period: str = "dev", equity: float = 10_000.0,
                targets=None, record: bool = False):
    tg = targets if targets is not None else compute_targets(panel, p)
    lo, hi = PERIODS[period]
    return run_backtest(panel, tg.weights, ep, equity, lo, hi, stop_frac=tg.stop_frac, record=record)


def ebrief(m: dict[str, Any]) -> str:
    return (f"compound/d {m['compound_daily'] * 100:6.3f}%  cagr {m['cagr'] * 100:7.1f}%  sharpe {m['sharpe']:5.2f}  "
            f"vol/d {m['daily_vol'] * 100:5.2f}%  mdd {m['engine_max_drawdown'] * 100:5.1f}%  "
            f">=2% {m['pct_days_at_target'] * 100:4.1f}%  worst {m['worst_day'] * 100:6.1f}%  "
            f"gross {m.get('avg_gross', float('nan')):4.2f}  trades {m['trades']:6d}  stops {m['stops']:4d}"
            f"{'  KILLED' if m['killed'] else ''}{'  LIQUIDATED' if m['liquidated'] else ''}")


def jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return jsonable(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return jsonable(obj.item())
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    return obj


def dump(path: str | Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(obj), indent=2, sort_keys=True) + "\n")
