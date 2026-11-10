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


def fetch_verified(http: HttpClient, obj: ArchiveObject, cache_dir: Path) -> bytes:
    """Return the object's bytes, downloading and verifying it if not cached.

    A cached file is accepted when its MD5 (or recorded SHA-256) still matches.
    """
    dest = cache_dir / obj.key
    if dest.exists() and dest.stat().st_size == obj.size:
        blob = dest.read_bytes()
        if _matches(http, obj, blob, dest):
            return blob
        log.warning("cached archive %s failed re-verification; downloading again", obj.name)
    blob = http.get_bytes(f"{ARCHIVE}/{obj.key}")
    if blob is None:
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


def download_klines(symbol: str, interval: str, interval_ms: int, cache_dir: Path,
                    start_month: str = "2019-01", end_month: str = "9999-12") -> np.ndarray:
    """All monthly kline archives for one perp symbol, concatenated (may be empty)."""
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
    arr = np.concatenate(parts)
    arr = arr[np.argsort(arr[:, 0], kind="stable")]
    keep = np.ones(len(arr), dtype=bool)
    keep[:-1] = arr[1:, 0] != arr[:-1, 0]
    return arr[keep]


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
