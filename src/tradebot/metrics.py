"""Performance statistics for equity curves, trades and 30-day campaigns."""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np
import pandas as pd

EQUITY_COLUMNS = ["price", "cash", "qty", "equity", "target"]


def equity_frame(records: Iterable[tuple]) -> pd.DataFrame:
    """Engine equity tuples -> frame indexed by bar close time (UTC)."""
    rows = list(records)
    if not rows:
        df = pd.DataFrame(columns=EQUITY_COLUMNS + ["exposure"], dtype="float64")
        df.index = pd.DatetimeIndex([], tz="UTC", name="time")
        return df
    arr = np.asarray(rows, dtype="float64")
    idx = pd.to_datetime(arr[:, 0].astype("int64"), unit="ms", utc=True)
    df = pd.DataFrame(arr[:, 1:], index=idx, columns=EQUITY_COLUMNS)
    df.index.name = "time"
    df["exposure"] = np.where(df["equity"] > 0, df["qty"] * df["price"] / df["equity"], 0.0)
    return df


def max_drawdown(equity: pd.Series | np.ndarray) -> float:
    e = np.asarray(equity, dtype="float64")
    if e.size == 0:
        return 0.0
    peak = np.maximum.accumulate(e)
    return float(np.max(1.0 - e / peak))


def performance(eq: pd.DataFrame, trades: list[dict] | pd.DataFrame, trips: list[dict] | pd.DataFrame,
                initial_equity: float, periods_per_year: float = 24 * 365) -> dict[str, Any]:
    trades_df = trades if isinstance(trades, pd.DataFrame) else pd.DataFrame(trades)
    trips_df = trips if isinstance(trips, pd.DataFrame) else pd.DataFrame(trips)
    out: dict[str, Any] = {"bars": int(len(eq))}
    if eq.empty:
        return out
    e = eq["equity"].to_numpy()
    final = float(e[-1])
    rets = np.diff(np.concatenate([[initial_equity], e])) / np.concatenate([[initial_equity], e[:-1]])
    n = len(rets)
    years = n / periods_per_year
    mu, sd = float(rets.mean()), float(rets.std(ddof=1)) if n > 1 else 0.0
    downside = rets[rets < 0]
    dsd = float(np.sqrt((downside ** 2).sum() / n)) if n else 0.0
    total = final / initial_equity - 1.0
    cagr = (final / initial_equity) ** (1.0 / years) - 1.0 if years > 0 and final > 0 else float("nan")
    mdd = max_drawdown(np.concatenate([[initial_equity], e]))
    out.update({
        "start": str(eq.index[0]),
        "end": str(eq.index[-1]),
        "initial_equity": initial_equity,
        "final_equity": final,
        "total_return": total,
        "cagr": cagr,
        "ann_vol": sd * math.sqrt(periods_per_year),
        "sharpe": (mu / sd * math.sqrt(periods_per_year)) if sd > 0 else 0.0,
        "sortino": (mu / dsd * math.sqrt(periods_per_year)) if dsd > 0 else 0.0,
        "max_drawdown": mdd,
        "calmar": (cagr / mdd) if mdd > 0 and not math.isnan(cagr) else float("nan"),
        "avg_exposure": float(eq["exposure"].mean()),
        "time_in_market": float((eq["exposure"] > 0.01).mean()),
        "n_trades": int(len(trades_df)),
        "fees_paid": float(trades_df["fee"].sum()) if len(trades_df) else 0.0,
        "turnover": float(trades_df["notional"].sum() / initial_equity) if len(trades_df) else 0.0,
    })
    if len(trips_df):
        wins = trips_df[trips_df["pnl"] > 0]
        losses = trips_df[trips_df["pnl"] <= 0]
        gross_win = float(wins["pnl"].sum())
        gross_loss = float(-losses["pnl"].sum())
        out.update({
            "round_trips": int(len(trips_df)),
            "win_rate": float(len(wins) / len(trips_df)),
            "avg_trip_return": float(trips_df["return"].mean()),
            "avg_win": float(wins["return"].mean()) if len(wins) else 0.0,
            "avg_loss": float(losses["return"].mean()) if len(losses) else 0.0,
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
            "avg_hold_hours": float(((trips_df["exit_ms"] - trips_df["entry_ms"]) / 3_600_000).mean()),
        })
    else:
        out.update({"round_trips": 0, "win_rate": float("nan"), "avg_trip_return": float("nan"),
                    "avg_win": float("nan"), "avg_loss": float("nan"), "profit_factor": float("nan"),
                    "avg_hold_hours": float("nan")})
    return out


def campaign_summary(campaigns: pd.DataFrame) -> dict[str, Any]:
    """Distribution of fixed-length campaign outcomes (e.g. 30-day runs)."""
    if campaigns.empty:
        return {"n": 0}
    r = campaigns["return"].to_numpy()
    q = np.quantile(r, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {
        "n": int(len(r)),
        "pct_positive": float((r > 0).mean()),
        "pct_flat": float((np.abs(r) < 1e-9).mean()),
        "pct_loss_gt_5": float((r < -0.05).mean()),
        "pct_loss_gt_10": float((r < -0.10).mean()),
        "mean": float(r.mean()),
        "median": float(q[2]),
        "std": float(r.std(ddof=1)) if len(r) > 1 else 0.0,
        "p05": float(q[0]),
        "p25": float(q[1]),
        "p75": float(q[3]),
        "p95": float(q[4]),
        "min": float(r.min()),
        "max": float(r.max()),
        "mean_max_dd": float(campaigns["max_drawdown"].mean()),
        "worst_max_dd": float(campaigns["max_drawdown"].max()),
        "mean_trades": float(campaigns["n_trades"].mean()),
        "mean_exposure": float(campaigns["time_in_market"].mean()),
    }


def fmt_pct(x: float | None, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "n/a"
    return f"{x * 100:.{digits}f}%"
