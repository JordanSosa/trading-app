"""Durable run state for forward (paper or live) runs.

Layout of the state directory::

    state.json     engine state + run metadata + universe + health (atomic writes)
    trades.csv     append-only fill ledger
    daily.csv      append-only ledger of completed trading days
    equity.csv     append-only equity curve (one row per bar)
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 2

TRADE_FIELDS = ["ts_ms", "time", "symbol", "side", "qty", "price", "notional", "fee", "reason", "qty_after"]
DAILY_FIELDS = ["day_end_ms", "date", "start_equity", "end_equity", "return", "target_hit", "loss_limit_hit"]
EQUITY_FIELDS = ["ts_ms", "time", "equity", "gross", "net", "risk_mult"]


def iso(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StateError(RuntimeError):
    pass


class StateStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

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

    # ------------------------------------------------------------ state document
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
        self.write_text("state.json", json.dumps(doc, indent=1, sort_keys=True, allow_nan=False) + "\n")

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
            os.chmod(tmp, 0o644)
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # -------------------------------------------------------------------- ledgers
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

    def append_daily(self, rows: list[tuple]) -> None:
        self._append_csv("daily.csv", DAILY_FIELDS, [
            {"day_end_ms": int(r[0]), "date": iso(int(r[0]) - 1)[:10], "start_equity": r[1], "end_equity": r[2],
             "return": r[2] / r[1] - 1.0 if r[1] else 0.0, "target_hit": int("T" in r[3]),
             "loss_limit_hit": int("L" in r[3])} for r in rows])

    def append_equity(self, rows: list[tuple]) -> None:
        self._append_csv("equity.csv", EQUITY_FIELDS, [
            {"ts_ms": int(r[0]), "time": iso(r[0]), "equity": r[1], "gross": r[2], "net": r[3], "risk_mult": r[4]}
            for r in rows])

    def append_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "events.jsonl").open("a") as fh:
            for e in events:
                fh.write(json.dumps({**e, "time": iso(e.get("ts_ms"))}, sort_keys=True, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # -------------------------------------------------------------------- readers
    def read_csv(self, name: str) -> list[dict[str, str]]:
        p = self.root / name
        if not p.exists() or p.stat().st_size == 0:
            return []
        with p.open(newline="") as fh:
            return list(csv.DictReader(fh))

    def read_events(self, limit: int | None = None) -> list[dict[str, Any]]:
        p = self.root / "events.jsonl"
        if not p.exists():
            return []
        lines = p.read_text().splitlines()
        if limit is not None:
            lines = lines[-limit:]
        return [json.loads(x) for x in lines if x.strip()]
