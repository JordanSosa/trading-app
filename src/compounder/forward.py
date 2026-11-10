"""Forward trading: one idempotent ``tick`` advances the account to *now*.

A tick can be run every few minutes by cron or GitHub Actions, or in a daemon
loop. It refreshes the universe when due, fetches the latest candles, replays
every bar that closed since the previous tick through the engine (so missed
ticks are caught up exactly), then executes the pending orders at the open of
the forming bar. Running two ticks back to back is a no-op for the second.

In paper mode the engine simulates fills, stops and funding itself. In live
mode a broker mirrors the exchange account into the engine and sends the
planned orders to the exchange instead (see :mod:`compounder.broker`).
"""

from __future__ import annotations

import logging
import math
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import __version__
from .config import Config
from .data.http import HttpClient
from .data.live import DataError, Instrument, MarketSource, make_source
from .data.panel import DAY_MS, Panel, build_panel, refresh_instant
from .engine import Engine, EngineState
from .state import StateError, StateStore, iso
from .strategy import compute_targets

log = logging.getLogger(__name__)

MAX_SOFT_FAILURES = 3        # consecutive data failures tolerated before a tick exits non-zero
CLOSE_GRACE_MS = 3_000       # a bar is final this long after its close time
UNIVERSE_HISTORY = 8         # refreshes kept in the state document
STALE_POSITION_BARS = 24     # paper mode: close a position whose market has had no bar for this long


@dataclass
class TickResult:
    ok: bool
    status: str
    message: str
    exit_code: int = 0
    bars_processed: int = 0
    trades: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    equity: float | None = None
    provider: str | None = None


@dataclass
class Snapshot:
    provider: str
    panel: Panel                      # closed bars only
    forming_ms: int | None            # open time of the forming bar
    forming_open: np.ndarray | None   # its open price per panel symbol (NaN if unknown)
    last: np.ndarray                  # latest traded price per panel symbol
    instruments: dict[str, Instrument]
    warnings: list[str]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clean(obj: Any) -> Any:
    """Make a structure strict-JSON safe (NaN/inf -> None, numpy scalars -> Python)."""
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [_clean(v) for v in obj.tolist()]
    return obj


# ------------------------------------------------------------------ universe
def rank_universe(source: MarketSource, instruments: dict[str, Instrument], refresh_ms: int, cfg: Config,
                  prefilter: int | None = None) -> dict[str, int]:
    """Point-in-time liquidity ranks as of ``refresh_ms`` (1 = most liquid).

    Mirrors :func:`compounder.data.panel.liquidity_rank`: mean daily USDT volume
    over the ``volume_window_days`` complete days before the refresh instant;
    a symbol needs ``min_history_days`` of history.
    """
    u = cfg.universe
    excluded = {b.upper() for b in u.exclude}
    names = [s for s, inst in instruments.items() if inst.base.upper() not in excluded]
    # Ranking every listed contract needs one request each. The symbols that can
    # reach the top N are overwhelmingly today's most active, so rank those.
    keep = prefilter if prefilter is not None else max(3 * u.size, u.size + 40)
    if len(names) > keep and hasattr(source, "day_volumes"):
        try:
            vol24 = source.day_volumes()
            names = sorted(names, key=lambda s: -vol24.get(s, 0.0))[:keep]
        except DataError as exc:
            log.warning("24h volume pre-filter unavailable (%s); ranking all %d symbols", exc, len(names))
    # Enough daily bars to cover the whole window even when the refresh instant is
    # several days in the past (first tick of a run in the middle of a week).
    need = max(u.volume_window_days, u.min_history_days) + u.refresh_days + 3
    scores: dict[str, float] = {}
    for sym in names:
        try:
            d = source.candles(instruments[sym], need, DAY_MS)
        except DataError as exc:
            log.warning("universe: no daily candles for %s: %s", sym, exc)
            continue
        if not len(d):
            continue
        before = d[d[:, 0] < refresh_ms]
        hist = before[before[:, 0] >= refresh_ms - u.min_history_days * DAY_MS]
        win = before[before[:, 0] >= refresh_ms - u.volume_window_days * DAY_MS]
        alive = len(before) and before[-1, 0] >= refresh_ms - DAY_MS
        listed = len(before) and before[0, 0] <= refresh_ms - u.min_history_days * DAY_MS
        if not listed or len(hist) < int(u.min_history_days * 0.9) or not alive:
            continue
        vol = float(win[:, 6].sum()) / u.volume_window_days
        if vol > 0:
            scores[sym] = vol
    ordered = sorted(scores, key=lambda s: -scores[s])
    return {s: i + 1 for i, s in enumerate(ordered)}


def universe_due(doc: dict[str, Any] | None, now_ms: int, cfg: Config) -> int | None:
    """The refresh instant to rank for, or None when the stored universe is current."""
    target = refresh_instant(now_ms, cfg.universe.refresh_days)
    hist = (doc or {}).get("universe", {}).get("history") or []
    if hist and hist[-1]["from_ms"] >= target:
        return None
    return target


def rank_rows(times: np.ndarray, symbols: tuple[str, ...], history: list[dict[str, Any]]) -> np.ndarray:
    """Per-bar rank array from the stored refresh history (the entry in force at each bar's open)."""
    rank = np.zeros((len(times), len(symbols)), dtype="int16")
    if not history:
        return rank
    starts = np.array([h["from_ms"] for h in history], dtype="int64")
    which = np.clip(np.searchsorted(starts, times, side="right") - 1, 0, len(history) - 1)
    rows = np.array([[int(h["ranks"].get(s, 0)) for s in symbols] for h in history], dtype="int16")
    return rows[which]


# ------------------------------------------------------------------ market data
def validate_candles(arr: np.ndarray, interval_ms: int) -> np.ndarray:
    """Drop malformed rows; raise if what is left is unusable."""
    if not len(arr):
        return arr
    o, h, l, c = arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
    ok = np.isfinite(arr[:, :5]).all(axis=1) & (l > 0) & (h >= l) & (h >= np.maximum(o, c) * (1 - 1e-9)) \
        & (l <= np.minimum(o, c) * (1 + 1e-9)) & (arr[:, 0] % interval_ms == 0)
    out = arr[ok]
    if len(out) > 1 and not (np.diff(out[:, 0]) > 0).all():
        raise DataError("candles are not strictly increasing in time")
    return out


class CandleCache:
    """Closed candles per symbol, kept on disk between ticks (one file per venue).

    Purely an optimisation: with a warm cache a tick asks each market for the
    few bars it has not seen instead of the whole look-back window. A missing,
    stale or corrupt cache just means a slower tick.
    """

    def __init__(self, root: Path | None):
        self.root = root

    def _path(self, venue: str) -> Path:
        return self.root / f"{venue}-candles.npz"

    def load(self, venue: str) -> dict[str, np.ndarray]:
        if self.root is None or not self._path(venue).exists():
            return {}
        try:
            with np.load(self._path(venue), allow_pickle=False) as z:
                return {k: z[k] for k in z.files}
        except Exception as exc:  # noqa: BLE001 - a bad cache must never stop a tick
            log.warning("ignoring unreadable candle cache: %s", exc)
            return {}

    def save(self, venue: str, data: dict[str, np.ndarray], keep: int) -> None:
        if self.root is None or not data:
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.root / f".{venue}-candles.part.npz"
            np.savez_compressed(tmp, **{k: v[-keep:] for k, v in data.items()})
            tmp.replace(self._path(venue))
        except OSError as exc:
            log.warning("could not write the candle cache: %s", exc)


def fetch_candles(source: MarketSource, inst: Instrument, n_bars: int, step: int, now_ms: int,
                  cached: np.ndarray | None) -> np.ndarray:
    """The last ``n_bars`` candles (plus the forming one), topping up ``cached`` when it is usable."""
    if cached is not None and len(cached) >= 2:
        missing = int((now_ms - int(cached[-1, 0])) // step) + 2
        if 0 < missing <= 250:
            fresh = validate_candles(source.candles(inst, missing + 2, step), step)
            if len(fresh) and int(fresh[0, 0]) <= int(cached[-1, 0]) + step:
                merged = np.concatenate([cached[cached[:, 0] < fresh[0, 0]], fresh])
                if len(merged) >= n_bars or len(merged) >= len(cached):
                    return merged[-(n_bars + 2):]
    return validate_candles(source.candles(inst, n_bars + 2, step), step)


class MarketData:
    """Fetches everything a tick needs from the first healthy source."""

    def __init__(self, cfg: Config, sources: list[MarketSource] | None = None,
                 clock: Callable[[], float] = time.time, cache_dir: Path | None = None):
        self.cfg = cfg
        if sources is None:
            http = HttpClient(timeout_s=cfg.data.request_timeout_s, retries=cfg.data.retries)
            sources = [make_source(name, http) for name in cfg.data.sources]
        self.sources = sources
        self.clock = clock
        self.cache = CandleCache(cache_dir)

    def primary(self) -> MarketSource:
        return self.sources[0]

    def snapshot(self, source: MarketSource, instruments: dict[str, Instrument], symbols: list[str],
                 n_bars: int, now_ms: int) -> Snapshot:
        step = self.cfg.run.interval_ms
        klines: dict[str, np.ndarray] = {}
        funding: dict[str, np.ndarray] = {}
        forming: dict[str, tuple[int, float, float]] = {}
        warnings: list[str] = []
        cutoff = now_ms - CLOSE_GRACE_MS
        cached = self.cache.load(source.name)
        fresh_cache: dict[str, np.ndarray] = {}
        for sym in symbols:
            inst = instruments.get(sym)
            if inst is None:
                warnings.append(f"{sym}: not listed on {source.name} any more")
                continue
            try:
                raw = fetch_candles(source, inst, n_bars, step, now_ms, cached.get(sym))
            except DataError as exc:
                warnings.append(f"{sym}: candles unavailable ({exc})")
                continue
            closed = raw[raw[:, 0] + step <= cutoff]
            if len(closed):
                fresh_cache[sym] = closed     # only final bars are ever cached
            open_bar = raw[(raw[:, 0] + step > cutoff) & (raw[:, 0] <= now_ms)]
            if len(open_bar):
                forming[sym] = (int(open_bar[-1, 0]), float(open_bar[-1, 1]), float(open_bar[-1, 4]))
            if len(closed):
                klines[sym] = closed
            try:
                funding[sym] = source.funding(inst)
            except DataError as exc:
                # Funding over the few bars a tick replays is small; never stall trading on it.
                warnings.append(f"{sym}: funding unavailable, booked as 0 ({exc})")
        if not klines:
            raise DataError(f"{source.name}: no candles for any symbol")
        last_closed = max(int(a[-1, 0]) for a in klines.values())
        if now_ms - (last_closed + step) > 2 * step:
            raise DataError(f"{source.name}: data is stale (latest closed bar opened {iso(last_closed)})")
        self.cache.save(source.name, fresh_cache, n_bars + 2)
        panel = build_panel(step, klines, funding, end_ms=last_closed, symbols=symbols)
        f_ms = last_closed + step
        f_open = np.full(panel.n_symbols, np.nan)
        last = panel.close[-1].copy()
        for j, sym in enumerate(panel.symbols):
            got = forming.get(sym)
            if got and got[0] == f_ms:
                ref = panel.close[-1, j]
                # The forming open is the execution price of paper fills: never trade on an implausible one.
                if math.isfinite(ref) and abs(got[1] / ref - 1.0) <= 0.10:
                    f_open[j] = got[1]
                    last[j] = got[2]
                else:
                    warnings.append(f"{sym}: forming bar rejected (open {got[1]}, last close {ref})")
        have_forming = np.isfinite(f_open).any()
        return Snapshot(source.name, panel, f_ms if have_forming else None, f_open if have_forming else None,
                        last, instruments, warnings)

    def cross_check(self, snap: Snapshot) -> tuple[bool, str]:
        """Compare the latest closes with a second venue. False only on a confirmed disagreement."""
        if not self.cfg.data.cross_check:
            return True, "cross-check disabled"
        step = self.cfg.run.interval_ms
        last_open = int(snap.panel.times[-1])
        for other in self.sources:
            if other.name == snap.provider:
                continue
            try:
                insts = other.instruments()
                devs = []
                for j, sym in enumerate(snap.panel.symbols[:8] if snap.panel.n_symbols > 8 else snap.panel.symbols):
                    mine = snap.panel.close[-1, j]
                    if sym not in insts or not math.isfinite(mine):
                        continue
                    theirs = other.candles(insts[sym], 3, step)
                    row = theirs[theirs[:, 0] == last_open]
                    if len(row):
                        devs.append((abs(mine / float(row[0, 4]) - 1.0), sym))
                if not devs:
                    continue
                # Compare the median: one illiquid outlier is venue basis, a shifted median is bad data.
                med = float(np.median([d for d, _ in devs]))
                worst = max(devs)
                ok = med <= self.cfg.data.max_price_deviation
                return ok, (f"{snap.provider} vs {other.name}: median close deviation {med:.3%} over "
                            f"{len(devs)} symbol(s), worst {worst[0]:.3%} ({worst[1]})")
            except DataError as exc:
                log.warning("cross-check source %s failed: %s", other.name, exc)
        return True, "no second source reachable; cross-check skipped"


# ----------------------------------------------------------------------- tick
def new_run_document(cfg: Config, now_ms: int) -> dict[str, Any]:
    step = cfg.run.interval_ms
    bar0 = now_ms - now_ms % step
    state = EngineState.fresh((), cfg.run.initial_equity)
    # The bar forming at start predates the bot, so it is not tradable: the first
    # decision is made at its close and filled at the open of the next bar.
    state.last_close_ms = bar0 - step
    state.last_open_ms = bar0
    return {
        "run": {"id": "run-" + iso(now_ms).replace("-", "").replace(":", "")[:13] + "Z", "mode": cfg.run.mode,
                "started_ms": now_ms, "start_time": iso(now_ms), "first_bar_ms": bar0,
                "initial_equity": cfg.run.initial_equity, "fingerprint": cfg.fingerprint(),
                "version": __version__, "strategy": cfg.strategy.to_dict()},
        "engine": state.to_dict(),
        "universe": {"history": []},
        "health": {"ticks": 0, "last_tick_at": None, "last_success_at": None, "consecutive_failures": 0,
                   "last_error": None, "last_provider": None},
        "notify": {"last_daily": None},
        "positions": {},
        "created_at": iso(now_ms),
        "updated_at": iso(now_ms),
    }


def tick(cfg: Config, store: StateStore, market: MarketData | None = None, now_ms: int | None = None,
         accept_config_change: bool = False, notifier: Any = None, write_reports: bool = True,
         broker: Any = None) -> TickResult:
    """Advance the run to ``now_ms``. Safe to call any number of times."""
    now_ms = now_ms if now_ms is not None else _now_ms()
    market = market or MarketData(cfg, cache_dir=store.root / "cache")
    params = cfg.engine_params()
    step = cfg.run.interval_ms
    warmup = cfg.strategy.warmup_bars
    events: list[dict[str, Any]] = []

    with store.lock():
        doc = store.load()
        started = doc is None
        if doc is None:
            doc = new_run_document(cfg, now_ms)
            events.append({"ts_ms": now_ms, "kind": "info",
                           "message": f"{cfg.run.mode} run started with {cfg.run.initial_equity:,.2f} USDT"})
        fp = cfg.fingerprint()
        if doc["run"]["fingerprint"] != fp:
            if doc["run"].get("mode") != cfg.run.mode:
                raise StateError("cannot switch a run between paper and live; start a new state directory")
            if not accept_config_change:
                raise StateError(
                    f"configuration changed since this run started (fingerprint {doc['run']['fingerprint']} -> "
                    f"{fp}). Start a new run with `compounder reset`, or pass --accept-config-change.")
            doc["run"]["fingerprint"] = fp
            doc["run"]["strategy"] = cfg.strategy.to_dict()
            events.append({"ts_ms": now_ms, "kind": "info", "message": "configuration change accepted"})

        # 1) market data -------------------------------------------------------
        snap: Snapshot | None = None
        errors: list[str] = []
        prev_state = doc["engine"]
        held = [s for s, q in (prev_state.get("qty") or {}).items() if q]
        for source in market.sources:
            try:
                instruments = source.instruments()
                due = universe_due(doc, now_ms, cfg)
                if due is not None and source is not market.sources[0]:
                    # Only the primary venue defines the universe (it labels each contract's
                    # underlying). A fallback venue keeps trading the stored one.
                    if not doc["universe"]["history"]:
                        raise DataError("no stored universe and the primary source is down")
                    due = None
                if due is not None:
                    ranks = rank_universe(source, instruments, due, cfg)
                    if len(ranks) < min(3, cfg.universe.size):
                        raise DataError(f"universe ranking returned only {len(ranks)} symbols")
                    hist = doc["universe"]["history"]
                    hist.append({"from_ms": due, "ranks": ranks, "source": source.name})
                    del hist[:-UNIVERSE_HISTORY]
                    top = [s for s, r in sorted(ranks.items(), key=lambda kv: kv[1])[: cfg.universe.size]]
                    events.append({"ts_ms": now_ms, "kind": "info",
                                   "message": f"universe refreshed for {iso(due)[:10]}: {', '.join(top)}"})
                current = doc["universe"]["history"][-1]["ranks"]
                # Symbols in the top N at any refresh a replayed bar can fall under, plus anything held.
                wanted: set[str] = set(held)
                for h in doc["universe"]["history"][-3:]:
                    wanted.update(s for s, r in h["ranks"].items() if 0 < r <= cfg.universe.size)
                wanted.update(s for s, r in current.items() if 0 < r <= cfg.universe.size)
                snap = market.snapshot(source, instruments, sorted(wanted), warmup + cfg.data.catchup_bars, now_ms)
                break
            except DataError as exc:
                errors.append(f"{source.name}: {exc}")
                log.warning("market data source failed: %s: %s", source.name, exc)
        if snap is None:
            return _record_failure(store, doc, now_ms, "market data unavailable: " + " | ".join(errors),
                                   notifier, events, persist=not started)
        for w in snap.warnings:
            log.info("data warning: %s", w)

        panel = snap.panel
        state = EngineState.from_dict(doc["engine"], symbols=panel.symbols)
        new = np.nonzero(panel.times > (state.last_close_ms if state.last_close_ms is not None else -1))[0]

        # 2) sanity check new data against a second venue --------------------------
        if len(new):
            ok, msg = market.cross_check(snap)
            if not ok:
                return _record_failure(store, doc, now_ms, f"data sanity check failed: {msg}", notifier, events,
                                       kind="alert", persist=not started)

        # 3) replay newly closed bars -----------------------------------------------
        panel = Panel(**{**panel.__dict__,
                         "rank": rank_rows(panel.times, panel.symbols, doc["universe"]["history"])})
        engine = Engine(params, state, record=True)
        if broker is not None:
            broker.attach(engine, snap, events, now_ms)
        bars_before = state.bars_processed
        if len(new):
            tg = compute_targets(panel, cfg.strategy)
            expected = (state.last_close_ms or int(panel.times[new[0]]) - step) + step
            if int(panel.times[new[0]]) > expected:
                missing = int((int(panel.times[new[0]]) - expected) // step)
                events.append({"ts_ms": now_ms, "kind": "warning",
                               "message": f"{missing} bar(s) missing between ticks; offline longer than the "
                                          f"{cfg.data.catchup_bars}-bar catch-up window"})
            usable = [int(i) for i in new if i >= warmup]
            if len(usable) < len(new):
                events.append({"ts_ms": now_ms, "kind": "warning",
                               "message": f"skipped {len(new) - len(usable)} bar(s) without enough warm-up history"})
            for i in usable:
                if broker is not None:
                    broker.before_close(int(panel.times[i]))
                engine.on_close(int(panel.times[i]), panel.open[i], panel.high[i], panel.low[i], panel.close[i],
                                tg.weights[i], tg.stop_frac[i], panel.funding[i])
            _close_stale_positions(engine, panel, events, now_ms, broker)

        # 4) execute pending orders at the open of the forming bar -------------------
        if state.pending is not None and snap.forming_ms is not None and snap.forming_open is not None:
            if state.last_close_ms is not None and snap.forming_ms == state.last_close_ms + step:
                if broker is not None:
                    broker.execute(snap.forming_ms)
                else:
                    engine.on_open(int(snap.forming_ms), snap.forming_open)

        # 5) persist: ledgers first, then the state document --------------------------
        events.extend(engine.events)
        store.append_trades(engine.trades)
        store.append_daily(engine.daily)
        store.append_equity(engine.equity_curve)
        store.append_events(events)
        live_px = np.where(np.isfinite(snap.last), snap.last, state.last_price)
        equity_now = engine.equity_at(live_px)
        doc["engine"] = state.to_dict()
        doc["positions"] = {
            s: {"qty": float(q), "entry": float(e) if math.isfinite(e) else None,
                "stop": float(sp) if math.isfinite(sp) else None, "price": float(px) if math.isfinite(px) else None,
                "notional": float(q * px) if math.isfinite(px) else None}
            for s, q, e, sp, px in zip(state.symbols, state.qty, state.entry_price, state.stop_price, live_px) if q}
        doc["live_equity"] = equity_now
        doc["health"].update({"ticks": int(doc["health"].get("ticks", 0)) + 1, "last_tick_at": iso(now_ms),
                              "last_success_at": iso(now_ms), "consecutive_failures": 0, "last_error": None,
                              "last_provider": snap.provider})
        doc["updated_at"] = iso(now_ms)
        store.save(_clean(doc))

        if write_reports:
            _reports(store, doc, cfg)
        if notifier:
            notifier.send_trades(engine.trades, equity_now)
            for e in events:
                notifier.send_event(e)
            if engine.daily:
                end_ms, start_eq, end_eq, _ = engine.daily[-1]
                notifier.send_daily(iso(int(end_ms) - 1)[:10], end_eq / start_eq - 1.0 if start_eq else 0.0, end_eq,
                                    end_eq / state.initial_equity - 1.0, 1.0 - end_eq / state.peak_equity
                                    if state.peak_equity else 0.0, params.daily_target, state.status)

    n_bars = state.bars_processed - bars_before
    msg = (f"processed {n_bars} bar(s), {len(engine.trades)} fill(s); equity {equity_now:,.2f} USDT; "
           f"status {state.status}; data {snap.provider}")
    return TickResult(True, state.status, msg, bars_processed=n_bars, trades=engine.trades, events=events,
                      equity=equity_now, provider=snap.provider)


def _close_stale_positions(engine: Engine, panel: Panel, events: list[dict[str, Any]], now_ms: int,
                           broker: Any) -> None:
    """Paper mode: a position in a market that stopped printing bars is closed at its last price."""
    if broker is not None:
        return
    s = engine.state
    recent = panel.close[-STALE_POSITION_BARS:]
    dead = (s.qty != 0.0) & ~np.isfinite(recent).any(axis=0)
    for j in np.nonzero(dead)[0]:
        px = float(s.last_price[j])
        if not math.isfinite(px) or px <= 0:
            continue
        engine._book(now_ms, int(j), -float(s.qty[j]), px, "market closed")
        events.append({"ts_ms": now_ms, "kind": "alert",
                       "message": f"{s.symbols[j]}: no bars for {STALE_POSITION_BARS}h; position closed at last "
                                  f"price {px:.6g}"})


def _record_failure(store: StateStore, doc: dict[str, Any], now_ms: int, message: str, notifier: Any,
                    events: list[dict[str, Any]], kind: str = "warning", persist: bool = True) -> TickResult:
    log.error(message)
    ev = {"ts_ms": now_ms, "kind": kind, "message": message}
    if not persist:
        # Nothing has been stored yet (the run has not started); just fail.
        return TickResult(False, "not started", message, exit_code=2, events=[ev])
    h = doc["health"]
    h["consecutive_failures"] = int(h.get("consecutive_failures", 0)) + 1
    h["last_error"] = message
    h["last_tick_at"] = iso(now_ms)
    h["ticks"] = int(h.get("ticks", 0)) + 1
    doc["updated_at"] = iso(now_ms)
    store.append_events(events + [ev])
    store.save(_clean(doc))
    hard = h["consecutive_failures"] >= MAX_SOFT_FAILURES
    if notifier and (hard or kind == "alert"):
        notifier.send_event({**ev, "kind": "alert"})
    status = EngineState.from_dict(doc["engine"]).status
    return TickResult(False, status, message, exit_code=2 if hard else 0, events=[ev])


def _reports(store: StateStore, doc: dict[str, Any], cfg: Config) -> None:
    try:
        from .report import write_reports
        write_reports(store, doc, cfg)
    except Exception:  # reports must never break trading
        log.exception("report generation failed")


def run_daemon(cfg: Config, store: StateStore, market: MarketData | None = None, notifier: Any = None,
               broker_factory: Callable[[], Any] | None = None, offset_s: float = 15.0,
               max_sleep_s: float = 300.0, max_crashes: int = 20) -> int:
    """Tick shortly after every bar close until SIGTERM.

    Returns a process exit code: 0 after a clean stop, 1 on a configuration or
    state error or after ``max_crashes`` consecutive crashes, so supervisors
    surface the failure instead of silently restarting.
    """
    stop = {"flag": False}

    def _handler(signum, frame):  # noqa: ARG001
        log.info("signal %s received; stopping after this tick", signum)
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
    step_s = cfg.run.interval_ms / 1000
    crashes = 0

    def _alert(msg: str) -> None:
        if notifier:
            try:
                notifier.send_event({"ts_ms": _now_ms(), "kind": "alert", "message": msg})
            except Exception:  # noqa: BLE001
                log.exception("alert failed")

    while not stop["flag"]:
        try:
            res = tick(cfg, store, market, notifier=notifier, broker=broker_factory() if broker_factory else None)
            crashes = 0
            log.info("tick: %s", res.message)
        except StateError as exc:
            log.error("tick refused: %s", exc)
            _alert(f"daemon stopped: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            crashes += 1
            log.exception("tick crashed (%d in a row); will retry", crashes)
            if crashes == 3:
                _alert(f"tick crashed 3 times in a row: {type(exc).__name__}: {exc}")
            if crashes >= max_crashes:
                _alert(f"daemon giving up after {crashes} consecutive crashes")
                return 1
        now = time.time()
        next_close = (math.floor(now / step_s) + 1) * step_s + offset_s
        end = time.time() + min(max(5.0, next_close - now), max_sleep_s)
        while not stop["flag"]:
            remaining = end - time.time()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))
    return 0
