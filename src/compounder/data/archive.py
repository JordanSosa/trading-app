"""Bulk history from Binance's public archive (data.binance.vision).

The archive is an S3 bucket. Listing a prefix returns every object with its
size and MD5 (the S3 ETag), so one listing request per symbol tells us exactly
which monthly files exist and lets every download be integrity-checked without
a second request. Files whose ETag is not a plain MD5 fall back to the
published SHA-256 ``.CHECKSUM`` file. Unverifiable data is refused.

Only USD-M perpetual futures klines and funding rates are used: they are the
instruments the strategy trades, and the archive keeps delisted symbols, which
is what makes a survivorship-bias-free universe possible.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from xml.etree import ElementTree

import numpy as np

from .http import HttpClient, HttpError

log = logging.getLogger(__name__)

S3_ENDPOINT = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
ARCHIVE = "https://data.binance.vision"
FUTURES_KLINES = "data/futures/um/monthly/klines"
FUTURES_KLINES_DAILY = "data/futures/um/daily/klines"
SPOT_KLINES_DAILY = "data/spot/daily/klines"
# Perpetuals quoted per 1,000 or 1,000,000 units of a spot asset.
_SPOT_MULTIPLIERS = (("1000000", 1e6), ("1M", 1e6), ("1000", 1e3))
DAY_MS = 86_400_000
MAX_PATCH_DAYS = 120
FUTURES_FUNDING = "data/futures/um/monthly/fundingRate"
MAX_MEMBER_BYTES = 256 * 1024 * 1024
_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
_MD5 = re.compile(r"^[0-9a-f]{32}$")

# Kline columns kept from the archive CSV (index in the raw row).
KLINE_FIELDS = ("open_ms", "open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_quote")
_KLINE_COLS = (0, 1, 2, 3, 4, 5, 7, 8, 10)


class ArchiveError(RuntimeError):
    """Raised when archive data is missing, corrupt or unverifiable."""


@dataclass(frozen=True)
class ArchiveObject:
    key: str
    size: int
    etag: str

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]


def _list_page(http: HttpClient, prefix: str, marker: str | None, delimiter: str | None) -> ElementTree.Element:
    params = {"prefix": prefix}
    if delimiter:
        params["delimiter"] = delimiter
    if marker:
        params["marker"] = marker
    raw = http.get_bytes(S3_ENDPOINT, params=params)
    if raw is None:
        raise ArchiveError(f"archive listing not found for {prefix}")
    return ElementTree.fromstring(raw)


def list_prefixes(http: HttpClient, prefix: str) -> list[str]:
    """Immediate sub-directories of ``prefix`` (e.g. every symbol)."""
    out: list[str] = []
    marker: str | None = None
    while True:
        root = _list_page(http, prefix, marker, "/")
        out.extend(p.findtext("s3:Prefix", "", _NS) for p in root.findall("s3:CommonPrefixes", _NS))
        if root.findtext("s3:IsTruncated", "false", _NS) != "true":
            break
        marker = root.findtext("s3:NextMarker", None, _NS) or (out[-1] if out else None)
        if not marker:
            break
    return [p[len(prefix):].strip("/") for p in out if p]


def list_objects(http: HttpClient, prefix: str) -> list[ArchiveObject]:
    """Every object under ``prefix`` with size and ETag."""
    out: list[ArchiveObject] = []
    marker: str | None = None
    while True:
        root = _list_page(http, prefix, marker, None)
        page = [ArchiveObject(c.findtext("s3:Key", "", _NS), int(c.findtext("s3:Size", "0", _NS)),
                              c.findtext("s3:ETag", "", _NS).strip('"').lower())
                for c in root.findall("s3:Contents", _NS)]
        out.extend(page)
        if root.findtext("s3:IsTruncated", "false", _NS) != "true" or not page:
            break
        marker = page[-1].key
    return out


def fetch_verified(http: HttpClient, obj: ArchiveObject, cache_dir: Path, missing_ok: bool = False) -> bytes | None:
    """Return the object's bytes, downloading and verifying it if not cached.

    A cached file is accepted when its MD5 (or recorded SHA-256) still matches.
    An object requested by name rather than from a listing has ``size < 0`` and
    no ETag; it is verified against its published SHA-256. With ``missing_ok``
    a file that does not exist returns None instead of raising.
    """
    dest = cache_dir / obj.key
    if dest.exists() and (obj.size < 0 or dest.stat().st_size == obj.size):
        blob = dest.read_bytes()
        if _matches(http, obj, blob, dest):
            return blob
        log.warning("cached archive %s failed re-verification; downloading again", obj.name)
    blob = http.get_bytes(f"{ARCHIVE}/{obj.key}")
    if blob is None:
        if missing_ok:
            return None
        raise ArchiveError(f"archive object vanished: {obj.key}")
    if not _matches(http, obj, blob, dest, allow_remote=True):
        raise ArchiveError(f"integrity check failed for {obj.key}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(blob)
    tmp.replace(dest)
    return blob


def _matches(http: HttpClient, obj: ArchiveObject, blob: bytes, dest: Path, allow_remote: bool = False) -> bool:
    if _MD5.match(obj.etag):
        return hashlib.md5(blob, usedforsecurity=False).hexdigest() == obj.etag
    sidecar = dest.with_name(dest.name + ".sha256")
    actual = hashlib.sha256(blob).hexdigest()
    if sidecar.exists():
        return sidecar.read_text().strip() == actual
    if not allow_remote:
        return False
    published = http.get_bytes(f"{ARCHIVE}/{obj.key}.CHECKSUM")
    parts = published.decode(errors="replace").split() if published else []
    if not parts or len(parts[0]) != 64:
        raise ArchiveError(f"no valid published checksum for {obj.key}; refusing unverified data")
    if parts[0].lower() != actual:
        return False
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(actual + "\n")
    return True


def _single_member(raw: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        infos = zf.infolist()
        if len(infos) != 1 or infos[0].file_size > MAX_MEMBER_BYTES:
            raise ArchiveError("unexpected archive layout (expected one CSV of sane size)")
        return zf.read(infos[0])


def parse_klines(raw_zip: bytes, interval_ms: int) -> np.ndarray:
    """Archive zip -> float64 array with columns :data:`KLINE_FIELDS`, sorted by open time."""
    text = _single_member(raw_zip).decode("ascii", errors="replace")
    lines = text.splitlines()
    if lines and not lines[0][:1].isdigit():
        lines = lines[1:]  # newer files carry a header row
    if not lines:
        return np.empty((0, len(KLINE_FIELDS)))
    arr = np.loadtxt(lines, delimiter=",", usecols=_KLINE_COLS, dtype="float64", ndmin=2)
    ts = arr[:, 0]
    ts = np.where(ts > 1e14, ts // 1000, ts)  # tolerate microsecond timestamps
    arr[:, 0] = ts - (ts % interval_ms)       # snap the rare off-grid row
    arr = arr[np.argsort(arr[:, 0], kind="stable")]
    keep = np.ones(len(arr), dtype=bool)
    keep[:-1] = arr[1:, 0] != arr[:-1, 0]     # on duplicates the later row wins
    return arr[keep]


def parse_funding(raw_zip: bytes) -> np.ndarray:
    """Funding archive zip -> array of (settlement_ms, rate), sorted by time."""
    text = _single_member(raw_zip).decode("ascii", errors="replace")
    rows = []
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 2 or not parts[0][:1].isdigit():
            continue
        t = int(float(parts[0]))
        rows.append((t - t % 60_000, float(parts[-1])))  # the archive carries a few ms of jitter
    if not rows:
        return np.empty((0, 2))
    arr = np.asarray(rows, dtype="float64")
    return arr[np.argsort(arr[:, 0], kind="stable")]


_local = threading.local()


def _thread_http(timeout_s: float = 30.0, retries: int = 5) -> HttpClient:
    http = getattr(_local, "http", None)
    if http is None:
        http = _local.http = HttpClient(timeout_s=timeout_s, retries=retries)
    return http


def parallel_map(fn: Callable, items: Iterable, workers: int = 8, progress: str | None = None) -> list:
    items = list(items)
    out: list = [None] * len(items)
    done = 0
    lock = threading.Lock()

    def run(i: int) -> None:
        nonlocal done
        out[i] = fn(items[i])
        with lock:
            done += 1
            if progress and (done % 250 == 0 or done == len(items)):
                log.info("%s: %d/%d", progress, done, len(items))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run, range(len(items))))
    return out


def perp_symbols(http: HttpClient | None = None) -> list[str]:
    """Every USDT-margined perpetual ever listed (delisted ones included)."""
    http = http or _thread_http()
    names = list_prefixes(http, FUTURES_KLINES + "/")
    return sorted(s for s in names if s.endswith("USDT") and "_" not in s)


def month_of(name: str) -> str:
    """'BTCUSDT-1h-2024-01.zip' -> '2024-01'."""
    m = re.search(r"(\d{4}-\d{2})\.zip$", name)
    if not m:
        raise ArchiveError(f"unexpected archive file name: {name}")
    return m.group(1)


def _dedupe(arr: np.ndarray) -> np.ndarray:
    """Sort by open time; on duplicates the later row wins."""
    arr = arr[np.argsort(arr[:, 0], kind="stable")]
    keep = np.ones(len(arr), dtype=bool)
    keep[:-1] = arr[1:, 0] != arr[:-1, 0]
    return arr[keep]


def missing_days(arr: np.ndarray, interval_ms: int) -> list[str]:
    """UTC dates ('YYYY-MM-DD') with at least one bar missing between the first and last row."""
    if len(arr) < 2:
        return []
    t = arr[:, 0].astype("int64")
    expected = np.arange(t[0], t[-1] + interval_ms, interval_ms, dtype="int64")
    miss = np.setdiff1d(expected, t, assume_unique=False)
    return [str(np.datetime64(int(d) * DAY_MS, "ms"))[:10] for d in np.unique(miss // DAY_MS)]


def patch_gaps(symbol: str, interval: str, interval_ms: int, cache_dir: Path, arr: np.ndarray) -> np.ndarray:
    """Fill holes in the monthly archives from the per-day archives.

    Binance's monthly files have holes that its daily files do not (three days
    in February 2022 for most altcoins, for example). A day that is missing
    from both stays missing: the market really was closed.
    """
    days = missing_days(arr, interval_ms)
    if not days:
        return arr
    if len(days) > MAX_PATCH_DAYS:
        log.warning("%s: %d days with missing bars; patching the %d most recent", symbol, len(days), MAX_PATCH_DAYS)
        days = days[-MAX_PATCH_DAYS:]
    http = _thread_http()
    parts = [arr]
    for day in days:
        obj = ArchiveObject(f"{FUTURES_KLINES_DAILY}/{symbol}/{interval}/{symbol}-{interval}-{day}.zip", -1, "")
        try:
            blob = fetch_verified(http, obj, cache_dir, missing_ok=True)
            if blob is not None:
                parts.append(parse_klines(blob, interval_ms))
        except (HttpError, ArchiveError, zipfile.BadZipFile, ValueError) as exc:
            log.warning("%s: could not patch %s: %s", symbol, day, exc)
    if len(parts) == 1:
        return arr
    out = _dedupe(np.concatenate(parts))
    # Never extend the series: a patch only fills the inside of what the monthly files span.
    return out[(out[:, 0] >= arr[0, 0]) & (out[:, 0] <= arr[-1, 0])]


def spot_counterpart(symbol: str) -> tuple[str, float]:
    """The spot pair behind a perpetual and the price multiple between them.

    '1000PEPEUSDT' -> ('PEPEUSDT', 1000.0); 'BTCUSDT' -> ('BTCUSDT', 1.0).
    """
    base = symbol[:-4]
    for prefix, mult in _SPOT_MULTIPLIERS:
        if base.startswith(prefix) and len(base) > len(prefix):
            return base[len(prefix):] + "USDT", mult
    return symbol, 1.0


def patch_from_spot(symbol: str, interval: str, interval_ms: int, cache_dir: Path, arr: np.ndarray,
                    days: set[str]) -> tuple[np.ndarray, int]:
    """Fill a market-wide hole in the futures archive with the exchange's own spot candles.

    Only for ``days`` on which the futures archive has no data for any symbol
    (a publishing gap, not a trading halt: the spot archive has the day). The
    spot bars are rescaled so the first one opens where the perpetual last
    closed, which absorbs the basis and any 1000x quoting; a ratio more than
    2% away from the expected multiple is refused. Volumes are set to the
    perpetual's own average over the previous 24 bars. Returns the patched
    series and the number of bars filled.
    """
    todo = [d for d in missing_days(arr, interval_ms) if d in days]
    if not todo:
        return arr, 0
    spot, mult = spot_counterpart(symbol)
    http = _thread_http()
    filled = 0
    for day in todo:
        obj = ArchiveObject(f"{SPOT_KLINES_DAILY}/{spot}/{interval}/{spot}-{interval}-{day}.zip", -1, "")
        try:
            blob = fetch_verified(http, obj, cache_dir, missing_ok=True)
        except (HttpError, ArchiveError) as exc:
            log.warning("%s: could not fetch spot candles for %s: %s", symbol, day, exc)
            continue
        if blob is None:
            continue
        try:
            rows = parse_klines(blob, interval_ms)
        except (ArchiveError, zipfile.BadZipFile, ValueError) as exc:
            log.warning("%s: unreadable spot candles for %s: %s", symbol, day, exc)
            continue
        rows = rows[~np.isin(rows[:, 0], arr[:, 0]) & (rows[:, 0] > arr[0, 0]) & (rows[:, 0] < arr[-1, 0])]
        if not len(rows):
            continue
        prev = arr[arr[:, 0] < rows[0, 0]]
        if not len(prev) or rows[0, 0] - prev[-1, 0] != interval_ms or rows[0, 1] <= 0:
            continue                                     # nothing adjacent to splice onto
        scale = prev[-1, 4] / rows[0, 1]
        if abs(scale / mult - 1.0) > 0.02:
            log.warning("%s: spot/perpetual ratio %.6g is not the expected %.0f; %s left unfilled",
                        symbol, scale, mult, day)
            continue
        rows = rows.copy()
        rows[:, 1:5] *= scale
        rows[:, 6] = prev[-24:, 6].mean()
        rows[:, 8] = prev[-24:, 8].mean()
        rows[:, 5] = rows[:, 6] / rows[:, 4]
        rows[:, 7] = 0.0
        arr = _dedupe(np.concatenate([arr, rows]))
        filled += len(rows)
    return arr, filled


def download_klines(symbol: str, interval: str, interval_ms: int, cache_dir: Path,
                    start_month: str = "2019-01", end_month: str = "9999-12", patch: bool = False) -> np.ndarray:
    """All monthly kline archives for one perp symbol, concatenated (may be empty).

    With ``patch`` the holes inside the series are filled from the daily archives.
    """
    http = _thread_http()
    objs = [o for o in list_objects(http, f"{FUTURES_KLINES}/{symbol}/{interval}/") if o.key.endswith(".zip")]
    parts = []
    for o in sorted(objs, key=lambda x: x.key):
        if not start_month <= month_of(o.name) <= end_month:
            continue
        try:
            parts.append(parse_klines(fetch_verified(http, o, cache_dir), interval_ms))
        except (HttpError, ArchiveError, zipfile.BadZipFile, ValueError) as exc:
            raise ArchiveError(f"{o.key}: {exc}") from exc
    if not parts:
        return np.empty((0, len(KLINE_FIELDS)))
    arr = _dedupe(np.concatenate(parts))
    return patch_gaps(symbol, interval, interval_ms, cache_dir, arr) if patch else arr


def download_funding(symbol: str, cache_dir: Path, start_month: str = "2019-01",
                     end_month: str = "9999-12") -> np.ndarray:
    http = _thread_http()
    objs = [o for o in list_objects(http, f"{FUTURES_FUNDING}/{symbol}/") if o.key.endswith(".zip")]
    parts = []
    for o in sorted(objs, key=lambda x: x.key):
        if not start_month <= month_of(o.name) <= end_month:
            continue
        try:
            parts.append(parse_funding(fetch_verified(http, o, cache_dir)))
        except (HttpError, ArchiveError, zipfile.BadZipFile, ValueError) as exc:
            raise ArchiveError(f"{o.key}: {exc}") from exc
    if not parts:
        return np.empty((0, 2))
    arr = np.concatenate(parts)
    return arr[np.argsort(arr[:, 0], kind="stable")]
