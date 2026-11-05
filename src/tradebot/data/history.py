"""Bulk historical candles from Binance's public archive (data.binance.vision).

Monthly zip archives are downloaded once, SHA-256 verified against the
published ``.CHECKSUM`` files, and cached under ``data/raw``. The most recent
days not yet archived are filled from the REST mirror. Result: a single
canonical CSV ``data/BTCUSDT-<tf>.csv``.
"""

from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import TIMEFRAME_MS
from .candles import COLUMNS, index_ms, make_frame, normalise, validate
from .providers import BinanceVision, HttpClient

log = logging.getLogger(__name__)

ARCHIVE = "https://data.binance.vision/data/spot"


def _parse_archive_csv(raw: bytes, interval_ms: int | None = None) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            df = pd.read_csv(fh, header=None, usecols=range(6))
    # Some archives carry a header row; drop anything non-numeric.
    df = df[pd.to_numeric(df[0], errors="coerce").notna()].astype("float64")
    ts = df[0].to_numpy()
    # Binance switched spot archives to microsecond timestamps on 2025-01-01.
    ts_ms = np.where(ts > 1e14, ts // 1000, ts).astype("int64")
    if interval_ms:
        # A handful of bars after Binance's Feb-2018 outage carry off-grid open
        # times (e.g. 09:28:14.789). Snap to the grid; on collision the later
        # row wins in normalise(), which matches the exchange's own repair.
        ts_ms = ts_ms - (ts_ms % interval_ms)
    rows = list(zip(ts_ms, df[1], df[2], df[3], df[4], df[5]))
    return make_frame(rows)


def _download_verified(http: HttpClient, url: str, dest: Path) -> bool:
    """Download ``url`` to ``dest`` after SHA-256 verification. False on 404."""
    if dest.exists():
        return True
    blob = http.get_bytes(url)
    if blob is None:
        return False
    checksum = http.get_bytes(url + ".CHECKSUM")
    if checksum is not None:
        expected = checksum.decode().split()[0].strip().lower()
        actual = hashlib.sha256(blob).hexdigest()
        if expected != actual:
            raise RuntimeError(f"checksum mismatch for {url}: {actual} != {expected}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(blob)
    tmp.replace(dest)
    return True


def _month_iter(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def download_history(
    data_dir: str | Path = "data",
    timeframe: str = "1h",
    start: str = "2017-08-01",
    http: HttpClient | None = None,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Download (or refresh) full BTCUSDT history and return closed candles."""
    http = http or HttpClient(timeout_s=30, retries=4)
    data_dir = Path(data_dir)
    raw_dir = data_dir / "raw" / timeframe
    now = now or datetime.now(timezone.utc)
    start_d = date.fromisoformat(start)
    this_month = now.date().replace(day=1)
    frames: list[pd.DataFrame] = []

    # 1) Complete months from the monthly archive.
    for y, m in _month_iter(start_d, this_month - timedelta(days=1)):
        fname = f"BTCUSDT-{timeframe}-{y:04d}-{m:02d}.zip"
        url = f"{ARCHIVE}/monthly/klines/BTCUSDT/{timeframe}/{fname}"
        dest = raw_dir / "monthly" / fname
        if not _download_verified(http, url, dest):
            log.info("no monthly archive for %04d-%02d (not published yet?)", y, m)
            continue
        frames.append(_parse_archive_csv(dest.read_bytes(), TIMEFRAME_MS[timeframe]))

    # 2) Recent bars (current month and any unpublished month) from REST.
    df = normalise(pd.concat(frames)) if frames else None
    step = TIMEFRAME_MS[timeframe]
    last_ms = int(df.index[-1].value // 1_000_000) if df is not None and len(df) else None
    now_ms = int(now.timestamp() * 1000)
    if last_ms is None:
        need = int((now_ms - int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * 1000)) // step) + 2
    else:
        need = int((now_ms - last_ms) // step) + 2
    if need > 0:
        recent = BinanceVision(http).fetch(timeframe, need)
        frames.append(recent)
    df = normalise(pd.concat(frames))
    # Drop the still-forming bar.
    open_ms = index_ms(df.index)
    df = df[open_ms + step <= now_ms]
    for w in validate(df, interval_ms=step):
        log.warning("history: %s", w)
    out = data_dir / f"BTCUSDT-{timeframe}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index_label="open_time")
    return df


def load_history(path: str | Path) -> pd.DataFrame:
    """Load a canonical candle CSV written by :func:`download_history`."""
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    return normalise(df[COLUMNS])
