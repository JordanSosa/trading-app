"""Candle schema, validation and causal resampling.

Candles are a pandas DataFrame indexed by the bar *open* time (UTC,
``DatetimeIndex``) with float columns ``open, high, low, close, volume``.
A bar with open time ``t`` and interval ``dt`` is only *closed* (final) once
wall-clock time reaches ``t + dt``; nothing in the engine may read a bar's
high/low/close before then.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


class CandleError(ValueError):
    """Raised when candle data fails validation."""


def make_frame(rows: list[tuple[int, float, float, float, float, float]]) -> pd.DataFrame:
    """Build a canonical candle frame from (open_ms, o, h, l, c, v) tuples."""
    if not rows:
        return empty_frame()
    arr = np.asarray(rows, dtype="float64")
    idx = pd.to_datetime(arr[:, 0].astype("int64"), unit="ms", utc=True)
    df = pd.DataFrame(arr[:, 1:6], index=idx, columns=COLUMNS)
    df.index.name = "open_time"
    return normalise(df)


def empty_frame() -> pd.DataFrame:
    df = pd.DataFrame(columns=COLUMNS, dtype="float64")
    df.index = pd.DatetimeIndex([], tz="UTC", name="open_time").as_unit("ns")
    return df


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Sort, de-duplicate (keep last) and coerce dtypes."""
    if df.empty:
        return empty_frame()
    out = df[COLUMNS].astype("float64")
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    out.index = out.index.as_unit("ns")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out.index.name = "open_time"
    return out


def validate(df: pd.DataFrame, *, interval_ms: int | None = None) -> list[str]:
    """Return a list of human-readable problems (empty if the data is clean).

    Hard errors (non-monotonic index, non-positive prices, inconsistent OHLC)
    raise ``CandleError``. Gaps are reported as warnings because exchanges do
    occasionally have outages; the engine simply processes what exists.
    """
    warnings: list[str] = []
    if df.empty:
        return warnings
    if not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise CandleError("candle index must be strictly increasing")
    vals = df[["open", "high", "low", "close"]].to_numpy()
    if not np.isfinite(vals).all():
        raise CandleError("candles contain NaN/inf prices")
    if (vals <= 0).any():
        raise CandleError("candles contain non-positive prices")
    o, h, l, c = vals.T
    tol = 1e-9 * np.maximum(np.abs(h), 1.0)
    if ((h + tol) < np.maximum(o, c)).any() or ((l - tol) > np.minimum(o, c)).any() or ((h + tol) < l).any():
        raise CandleError("candles contain inconsistent OHLC (high < max(open, close) or low > min(open, close))")
    if (df["volume"].to_numpy() < 0).any():
        raise CandleError("candles contain negative volume")
    if interval_ms is not None and len(df) > 1:
        steps = np.diff(index_ms(df.index))
        if (steps % interval_ms != 0).any():
            raise CandleError("candle open times are not aligned to the interval")
        gaps = int((steps > interval_ms).sum())
        if gaps:
            missing = int((steps[steps > interval_ms] // interval_ms - 1).sum())
            warnings.append(f"{gaps} gap(s) in candle data ({missing} missing bar(s))")
    return warnings


def split_closed(df: pd.DataFrame, now_ms: int, interval_ms: int) -> tuple[pd.DataFrame, pd.Series | None]:
    """Split into (closed bars, currently-forming bar or None) at wall-clock ``now_ms``."""
    if df.empty:
        return df, None
    open_ms = index_ms(df.index)
    closed_mask = open_ms + interval_ms <= now_ms
    closed = df[closed_mask]
    forming_mask = (~closed_mask) & (open_ms <= now_ms)
    forming = df[forming_mask]
    return closed, (forming.iloc[-1] if len(forming) else None)


def to_ms(ts: pd.Timestamp) -> int:
    """Epoch milliseconds of a Timestamp (``Timestamp.value`` is always ns)."""
    return int(ts.value // 1_000_000)


def index_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    """Epoch milliseconds of a DatetimeIndex, independent of its resolution."""
    return idx.as_unit("ms").asi8


def resample_causal(df: pd.DataFrame, rule: str, base_interval_ms: int) -> pd.DataFrame:
    """Higher-timeframe OHLCV aligned back onto the base index *without look-ahead*.

    Row ``t`` of the result holds the most recent higher-timeframe bar that was
    fully closed by the close of base bar ``t`` (i.e. at time ``t + base``).
    Partially formed higher-timeframe bars are never exposed. Rows before the
    first completed higher-timeframe bar are NaN.

    Example: with 1h base bars and ``rule='4h'``, the 4h bar [00:00, 04:00)
    becomes visible on the base bar opening at 03:00 (which closes at 04:00).
    """
    if df.empty:
        return df.copy()
    htf = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    htf = htf.dropna(subset=["close"])
    if htf.empty:
        return pd.DataFrame(np.nan, index=df.index, columns=COLUMNS)
    htf_ms = pd.Timedelta(rule).value // 1_000_000
    if htf_ms < base_interval_ms or htf_ms % base_interval_ms:
        raise ValueError(f"rule {rule!r} must be a whole multiple of the base interval")
    htf_close_ms = index_ms(htf.index) + htf_ms
    base_close_ms = index_ms(df.index) + base_interval_ms
    # For every base bar, the last HTF bar whose close <= that base bar's close.
    # A still-forming HTF bar closes after every base bar seen so far, so it
    # can never be selected: this is what makes the mapping causal.
    pos = np.searchsorted(htf_close_ms, base_close_ms, side="right") - 1
    out = np.full((len(df), len(COLUMNS)), np.nan)
    ok = pos >= 0
    out[ok] = htf[COLUMNS].to_numpy()[pos[ok]]
    return pd.DataFrame(out, index=df.index, columns=COLUMNS)
