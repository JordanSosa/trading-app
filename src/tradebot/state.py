"""Durable run state for the forward (paper) run.

Layout of the state directory::

    state.json     engine state + run metadata + health (atomic writes)
    trades.csv     append-only fill ledger
    trips.csv      append-only round-trip ledger
    equity.csv     append-only hourly equity curve
    events.jsonl   append-only alerts / info events
    STATUS.md, equity.svg, dashboard.html, summary.json   (generated reports)

Everything is plain text so the full history can be audited in git.
"""

from __future__ import annotations

import csv
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

SCHEMA_VERSION = 1

TRADE_FIELDS = ["ts_ms", "time", "side", "qty", "price", "ref_price", "notional", "fee", "realized_pnl",
                "reason", "cash_after", "qty_after", "equity_after"]
TRIP_FIELDS = ["entry_ms", "exit_ms", "entry_time", "exit_time", "side", "entry_price", "exit_price", "pnl",
               "return", "exit_reason"]
EQUITY_FIELDS = ["ts_ms", "time", "price", "cash", "qty", "equity", "target"]


def iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


class StateError(RuntimeError):
    pass


class StateStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    # paths
    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def path(self, name: str) -> Path:
        return self.root / name

    def exists(self) -> bool:
        return self.state_path.exists()

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Exclusive advisory lock so two ticks can never interleave."""
        self.root.mkdir(parents=True, exist_ok=True)
        fh = open(self.root / ".tick.lock", "w")
        try:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StateError("another tick is already running (state directory is locked)") from exc
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    # state document
    def load(self) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        with self.state_path.open() as fh:
            doc = json.load(fh)
        ver = doc.get("schema_version")
        if ver != SCHEMA_VERSION:
            raise StateError(f"unsupported state schema version {ver} (expected {SCHEMA_VERSION})")
        return doc

    def save(self, doc: dict[str, Any]) -> None:
        doc = {**doc, "schema_version": SCHEMA_VERSION}
        self.write_text("state.json", json.dumps(doc, indent=2, sort_keys=True, allow_nan=False) + "\n")

    def write_text(self, name: str, text: str) -> None:
        """Atomic write (temp file + fsync + rename)."""
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / name
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o644)  # mkstemp creates 0600; reports must be readable
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ledgers
    def _append_csv(self, name: str, fields: list[str], rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.root / name
        new = not p.exists() or p.stat().st_size == 0
        with p.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            if new:
                w.writeheader()
            for r in rows:
                w.writerow(r)
            fh.flush()
            os.fsync(fh.fileno())

    def append_trades(self, trades: list[dict[str, Any]]) -> None:
        self._append_csv("trades.csv", TRADE_FIELDS, [{**t, "time": iso(t["ts_ms"])} for t in trades])

    def append_trips(self, trips: list[dict[str, Any]]) -> None:
        self._append_csv("trips.csv", TRIP_FIELDS, [
            {**t, "entry_time": iso(t["entry_ms"]), "exit_time": iso(t["exit_ms"])} for t in trips])

    def append_equity(self, rows: list[tuple]) -> None:
        self._append_csv("equity.csv", EQUITY_FIELDS, [
            {"ts_ms": r[0], "time": iso(r[0]), "price": r[1], "cash": r[2], "qty": r[3], "equity": r[4],
             "target": r[5]} for r in rows])

    def append_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "events.jsonl").open("a") as fh:
            for e in events:
                fh.write(json.dumps({**e, "time": iso(e.get("ts_ms"))}, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # readers
    def read_csv(self, name: str) -> pd.DataFrame:
        p = self.root / name
        if not p.exists() or p.stat().st_size == 0:
            return pd.DataFrame()
        return pd.read_csv(p)

    def read_events(self, limit: int | None = None) -> list[dict[str, Any]]:
        p = self.root / "events.jsonl"
        if not p.exists():
            return []
        lines = p.read_text().splitlines()
        if limit is not None:
            lines = lines[-limit:]
        return [json.loads(x) for x in lines if x.strip()]
