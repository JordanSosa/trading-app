"""Forward (paper) trading: one idempotent ``tick`` advances the run to *now*.

A tick can be run every few minutes by cron/GitHub Actions or in a daemon
loop. It fetches the latest candles, replays every bar that closed since the
previous tick through the engine (so missed ticks are caught up exactly), then
executes any pending order at the open of the currently forming bar. Running
two ticks back-to-back is a no-op for the second one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import signal
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import pandas as pd

from . import __version__
from .config import Config
from .data.candles import index_ms
from .data.providers import FetchResult, HttpClient, MarketData, ProviderError
from .engine import Engine, EngineParams, EngineState
from .state import StateError, StateStore, iso
from .strategies import get_strategy

log = logging.getLogger(__name__)

MAX_SOFT_FAILURES = 3  # consecutive data failures tolerated before the tick exits non-zero


class DataSource(Protocol):
    def fetch(self, interval: str, closed_bars: int, min_bars: int) -> FetchResult: ...
    def cross_check(self, primary: str, closed: pd.DataFrame, interval: str, max_dev: float,
                    bars: int = 6) -> tuple[bool, str]: ...


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
    price: float | None = None
    provider: str | None = None


def resolved_strategy(cfg: Config):
    return get_strategy(cfg.strategy.name, cfg.strategy.params)


def config_fingerprint(cfg: Config) -> str:
    """Hash of everything that changes trading behaviour. A running campaign
    refuses to continue under a different fingerprint (results would be mixed)."""
    strat = resolved_strategy(cfg)
    material = {
        "strategy": {"name": strat.name, "params": strat.params},
        "costs": asdict(cfg.costs),
        "risk": asdict(cfg.risk),
        "run": {k: getattr(cfg.run, k) for k in ("symbol", "timeframe", "initial_cash", "duration_days",
                                                  "end_action")},
    }
    blob = json.dumps(material, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clean(obj: Any) -> Any:
    """Make a structure strict-JSON safe (NaN/inf -> None)."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def new_run_document(cfg: Config, data: FetchResult, now_ms: int) -> dict[str, Any]:
    strat = resolved_strategy(cfg)
    step = cfg.run.timeframe_ms
    # Anchor the run to the wall-clock bar grid, not to the provider response:
    # bar0 is the bar forming at start. Its open predates the bot, so it is not
    # tradeable; the first decision is made at its close and the campaign ends
    # exactly duration_days later on the bar grid. This is the same definition
    # run_campaigns uses for a campaign starting at bar0.
    bar0 = now_ms - now_ms % step
    end_ms = bar0 + int(cfg.run.duration_days * 86_400_000)
    st = EngineState.fresh(cfg.run.initial_cash, start_ms=bar0, end_ms=end_ms)
    last_closed_ms = int(index_ms(data.candles.index)[-1])
    st.last_close_ms = max(last_closed_ms, bar0 - step)
    st.last_open_ms = bar0
    start_price = data.forming_close if data.forming_close is not None else float(data.candles["close"].iloc[-1])
    st.last_price = start_price
    return {
        "run": {
            "id": pd.Timestamp(now_ms, unit="ms", tz="UTC").strftime("run-%Y%m%dT%H%MZ"),
            "symbol": cfg.run.symbol,
            "timeframe": cfg.run.timeframe,
            "mode": cfg.run.mode,
            "strategy": strat.name,
            "strategy_params": strat.params,
            "strategy_description": strat.describe(),
            "fingerprint": config_fingerprint(cfg),
            "initial_cash": cfg.run.initial_cash,
            "duration_days": cfg.run.duration_days,
            "start_ms": now_ms,
            "start_time": iso(now_ms),
            "first_bar_ms": bar0,
            "end_ms": end_ms,
            "end_time": iso(end_ms),
            "start_price": start_price,
            "interval_ms": step,
            "version": __version__,
            "completed_at": None,
        },
        "engine": st.to_dict(),
        "health": {"ticks": 0, "last_tick_at": None, "last_success_at": None, "consecutive_failures": 0,
                   "last_error": None, "last_provider": data.provider, "last_live_price": start_price,
                   "last_live_price_at": iso(now_ms)},
        "notify": {"last_daily_summary": None},
        "created_at": iso(now_ms),
        "updated_at": iso(now_ms),
    }


def tick(cfg: Config, store: StateStore, md: DataSource | None = None, now_ms: int | None = None,
         accept_config_change: bool = False, notifier: Any = None, write_reports: bool = True) -> TickResult:
    """Advance the paper run to ``now_ms``. Safe to call any number of times."""
    now_ms = now_ms if now_ms is not None else _now_ms()
    md = md or MarketData(cfg.data.providers, http=HttpClient(timeout_s=cfg.data.request_timeout_s,
                                                              retries=cfg.data.retries))
    strategy = resolved_strategy(cfg)
    params = EngineParams.from_config(cfg)
    step = cfg.run.timeframe_ms
    need = max(strategy.warmup_bars + 2, 100)
    lookback = max(cfg.data.lookback_bars, need)

    with store.lock():
        doc = store.load()
        if doc is not None and EngineState.from_dict(doc["engine"]).status == "completed":
            # Finished runs are frozen: no data fetch, no re-marking of results.
            if write_reports:
                _reports(store, doc, cfg)
            st_done = EngineState.from_dict(doc["engine"])
            return TickResult(True, "completed", "run already completed; nothing to do",
                              equity=st_done.cash + st_done.qty * (doc["run"].get("final_price") or 0.0),
                              price=doc["run"].get("final_price"))
        if doc is not None:
            fp = config_fingerprint(cfg)
            if doc["run"]["fingerprint"] != fp:
                fixed = {"symbol": cfg.run.symbol, "timeframe": cfg.run.timeframe,
                         "initial_cash": cfg.run.initial_cash, "duration_days": cfg.run.duration_days}
                changed = [k for k, v in fixed.items() if doc["run"].get(k) != v]
                if changed:
                    raise StateError(f"cannot change {', '.join(changed)} of a running campaign; "
                                     "start a new run with `tradebot reset`")
                if not accept_config_change:
                    raise StateError(
                        "configuration changed since this run started (fingerprint "
                        f"{doc['run']['fingerprint']} -> {fp}). Start a new run with `tradebot reset`, "
                        "or pass --accept-config-change to continue the same run under the new settings.")
                doc["run"]["fingerprint"] = fp
                doc["run"]["strategy"] = strategy.name
                doc["run"]["strategy_params"] = strategy.params
                doc["run"]["strategy_description"] = strategy.describe()
                store.append_events([{"ts_ms": now_ms, "kind": "info",
                                      "message": f"configuration change accepted: now {strategy.describe()}"}])

        # 1) market data -----------------------------------------------------
        try:
            data = md.fetch(cfg.run.timeframe, lookback, need)
        except ProviderError as exc:
            return _record_failure(store, doc, now_ms, f"market data unavailable: {exc}", notifier)

        if doc is None:
            doc = new_run_document(cfg, data, now_ms)
            ev = {"ts_ms": now_ms, "kind": "info",
                  "message": f"paper run started: {strategy.describe()}, {cfg.run.initial_cash:,.2f} USDT, "
                             f"{cfg.run.duration_days:g} days, data via {data.provider}"}
            store.append_events([ev])
            doc["health"].update({"ticks": 1, "last_tick_at": iso(now_ms), "last_success_at": iso(now_ms)})
            store.save(_clean(doc))
            if write_reports:
                _reports(store, doc, cfg)
            if notifier:
                notifier.send_event(ev, doc)
            return TickResult(True, "running", ev["message"], events=[ev], equity=cfg.run.initial_cash,
                              price=doc["run"]["start_price"], provider=data.provider)

        state = EngineState.from_dict(doc["engine"])

        candles = data.candles
        open_ms = index_ms(candles.index)
        new_mask = open_ms > (state.last_close_ms if state.last_close_ms is not None else -1)
        events: list[dict[str, Any]] = []
        for w in data.warnings:
            log.info("data warning: %s", w)

        # 2) sanity check new data against a second venue ---------------------
        if new_mask.any() and cfg.data.sanity_check:
            ok, msg = md.cross_check(data.provider, candles[new_mask], cfg.run.timeframe,
                                     cfg.data.max_price_deviation)
            if not ok:
                return _record_failure(store, doc, now_ms, f"data sanity check failed: {msg}", notifier,
                                       kind="alert")

        # 3) replay newly closed bars through the engine ----------------------
        sig = strategy.signals(candles)
        tgt = sig["target"].to_numpy()
        sd = sig["stop_dist"].to_numpy()
        engine = Engine(params, state)
        bars_before = state.bars_processed
        idx = [i for i in range(len(candles)) if new_mask[i]]
        if idx:
            expected_first = (state.last_close_ms or open_ms[idx[0]] - step) + step
            if open_ms[idx[0]] > expected_first:
                missing = int((open_ms[idx[0]] - expected_first) // step)
                engine._event(now_ms, "warning", f"{missing} bar(s) missing between ticks "
                              f"({iso(expected_first)} .. {iso(int(open_ms[idx[0]]))}); processed what exists")
            # Bars too close to the start of the fetched window lack indicator
            # warm-up; only possible after an outage longer than the lookback.
            usable = [i for i in idx if i >= strategy.warmup_bars]
            if len(usable) < len(idx):
                engine._event(now_ms, "warning", f"skipped {len(idx) - len(usable)} bar(s) without enough "
                              "warm-up history (bot was offline longer than the lookback window)")
            for i in usable:
                r = candles.iloc[i]
                engine.on_close(int(open_ms[i]), float(r["open"]), float(r["high"]), float(r["low"]),
                                float(r["close"]), float(tgt[i]), float(sd[i]))

        # 4) execute pending order at the open of the forming bar -------------
        if (data.forming_open_ms is not None and data.forming_open is not None
                and math.isfinite(data.forming_open) and data.forming_open > 0):
            if state.last_close_ms is not None and data.forming_open_ms == state.last_close_ms + step:
                engine.on_open(int(data.forming_open_ms), float(data.forming_open))

        events.extend(engine.events)
        # 5) persist: ledgers first, then the state document -------------------
        store.append_trades(engine.trades)
        store.append_trips(engine.trips)
        store.append_equity(engine.equity)
        store.append_events(events)
        live = data.forming_close if data.forming_close is not None else state.last_price
        doc["engine"] = state.to_dict()
        doc["health"].update({
            "ticks": doc["health"].get("ticks", 0) + 1,
            "last_tick_at": iso(now_ms),
            "last_success_at": iso(now_ms),
            "consecutive_failures": 0,
            "last_error": None,
            "last_provider": data.provider,
            "last_live_price": live,
            "last_live_price_at": iso(now_ms),
        })
        if state.status == "completed" and not doc["run"].get("completed_at"):
            doc["run"]["completed_at"] = iso(now_ms)
            # Freeze the mark used for the final result and the B&H comparison.
            doc["run"]["final_price"] = state.last_price
            fin = {"ts_ms": now_ms, "kind": "end",
                   "message": f"run completed: final equity {state.cash + state.qty * (live or 0):,.2f} USDT "
                              f"({(state.cash + state.qty * (live or 0)) / state.initial_equity - 1:+.2%})"}
            events.append(fin)
            store.append_events([fin])
        doc["updated_at"] = iso(now_ms)
        store.save(_clean(doc))

        if write_reports:
            _reports(store, doc, cfg)
        if notifier:
            for t in engine.trades:
                notifier.send_trade(t, doc)
            for e in events:
                notifier.send_event(e, doc)
            notifier.maybe_daily_summary(doc, store, now_ms)

    equity_now = state.cash + state.qty * (live or 0.0)
    n_bars = state.bars_processed - bars_before
    msg = (f"processed {n_bars} bar(s), {len(engine.trades)} trade(s); equity {equity_now:,.2f} USDT; "
           f"status {state.status}; data {data.provider}")
    return TickResult(True, state.status, msg, bars_processed=n_bars, trades=engine.trades, events=events,
                      equity=equity_now, price=live, provider=data.provider)


def _record_failure(store: StateStore, doc: dict[str, Any] | None, now_ms: int, message: str,
                    notifier: Any, kind: str = "warning") -> TickResult:
    log.error(message)
    ev = {"ts_ms": now_ms, "kind": kind, "message": message}
    if doc is None:
        # Nothing to persist yet (run not started); just fail.
        return TickResult(False, "not started", message, exit_code=2, events=[ev])
    h = doc["health"]
    h["consecutive_failures"] = int(h.get("consecutive_failures", 0)) + 1
    h["last_error"] = message
    h["last_tick_at"] = iso(now_ms)
    h["ticks"] = int(h.get("ticks", 0)) + 1
    doc["updated_at"] = iso(now_ms)
    store.append_events([ev])
    store.save(_clean(doc))
    hard = h["consecutive_failures"] >= MAX_SOFT_FAILURES
    if notifier and (hard or kind == "alert"):
        notifier.send_event({**ev, "kind": "alert"}, doc)
    status = EngineState.from_dict(doc["engine"]).status
    return TickResult(False, status, message, exit_code=2 if hard else 0, events=[ev])


def _reports(store: StateStore, doc: dict[str, Any], cfg: Config) -> None:
    try:
        from .report import write_reports
        write_reports(store, doc, cfg)
    except Exception:  # reports must never break trading
        log.exception("report generation failed")


def run_daemon(cfg: Config, store: StateStore, md: DataSource | None = None, notifier: Any = None,
               offset_s: float = 20.0, max_sleep_s: float = 300.0, max_crashes: int = 20) -> int:
    """Tick shortly after every bar close until SIGTERM.

    Returns a process exit code: 0 after a clean stop, 1 on a configuration or
    state error or after ``max_crashes`` consecutive crashes (so supervisors
    surface the failure instead of silently restarting). Once the run has
    completed the daemon idles (no API calls) until it is stopped.
    """
    stop = {"flag": False}

    def _handler(signum, frame):  # noqa: ARG001
        log.info("signal %s received; stopping after this tick", signum)
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
    step_s = cfg.run.timeframe_ms / 1000
    crashes = 0
    completed = False

    def _alert(msg: str) -> None:
        if notifier:
            try:
                notifier.send_event({"ts_ms": _now_ms(), "kind": "alert", "message": msg}, {})
            except Exception:  # noqa: BLE001
                log.exception("alert failed")

    while not stop["flag"]:
        if not completed:
            try:
                res = tick(cfg, store, md, notifier=notifier)
                crashes = 0
                log.info("tick: %s", res.message)
                if res.status == "completed":
                    log.info("run completed; idling until stopped (no further API calls)")
                    completed = True
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
        if completed:
            sleep_for = 60.0
        else:
            next_close = (math.floor(now / step_s) + 1) * step_s + offset_s
            sleep_for = min(max(5.0, next_close - now), max_sleep_s)
        end = time.time() + sleep_for
        while not stop["flag"]:
            remaining = end - time.time()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))
    return 0
