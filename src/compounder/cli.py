"""Command-line interface: ``compounder <command>``."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from .config import Config, ConfigError, load_config

DEFAULT_CONFIG = "config/paper.toml"


def _cfg(args: argparse.Namespace) -> Config:
    # An explicitly passed --config must exist; without one, use config/paper.toml
    # when present, else the built-in defaults.
    path = args.config if args.config else (DEFAULT_CONFIG if Path(DEFAULT_CONFIG).exists() else None)
    cfg = load_config(path)
    if getattr(args, "state_dir", None):
        cfg = dataclasses.replace(cfg, run=dataclasses.replace(cfg.run, state_dir=args.state_dir))
    return cfg


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


# ----------------------------------------------------------------------- data
def cmd_download(args: argparse.Namespace) -> int:
    from .data.dataset import build_daily, build_hourly, daily_path
    if args.refresh_daily or not daily_path(args.data_dir).exists():
        build_daily(args.data_dir, args.end, args.workers)
    panel = build_hourly(args.data_dir, args.top, args.end, args.workers)
    print(f"{panel.n_bars} hourly bars x {panel.n_symbols} symbols -> {args.data_dir}/research/hourly.npz")
    return 0


def _load_panel(args: argparse.Namespace):
    from .data.dataset import hourly_path
    from .data.panel import Panel
    path = Path(args.data) if getattr(args, "data", None) else hourly_path(args.data_dir)
    if not path.exists():
        print(f"dataset {path} not found; run `compounder download` first", file=sys.stderr)
        raise SystemExit(1)
    return Panel.load(path)


# ------------------------------------------------------------------- backtest
def cmd_backtest(args: argparse.Namespace) -> int:
    from .backtest import run_backtest
    from .metrics import ruin_report
    from .strategy import compute_targets
    cfg = _cfg(args)
    panel = _load_panel(args)
    params = cfg.engine_params()
    if args.cost_mult != 1.0:
        params = dataclasses.replace(params, fee_rate=params.fee_rate * args.cost_mult,
                                     slippage=params.slippage * args.cost_mult)
    tg = compute_targets(panel, cfg.strategy)
    equity = args.equity if args.equity else cfg.run.initial_equity
    res = run_backtest(panel, tg.weights, params, equity, args.start, args.end, stop_frac=tg.stop_frac)
    out: dict[str, Any] = dict(res.metrics)
    if args.no_months:
        out.pop("by_month", None)
    if args.ruin and len(res.daily_returns) >= 60:
        out["bootstrap_1y"] = ruin_report(res.daily_returns, 365)
    if args.output:
        _write_backtest(Path(args.output), res)
    _print_json(out)
    return 0


def _write_backtest(out: Path, res) -> None:
    import csv
    from .state import iso
    out.mkdir(parents=True, exist_ok=True)
    with (out / "daily.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "return", "flags"])
        for t, r, f in zip(res.day_end_ms, res.daily_returns, res.day_flags):
            w.writerow([iso(int(t) - 1)[:10], f"{r:.8f}", f])
    with (out / "equity.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "equity", "gross", "net", "risk_mult"])
        for row in zip(res.equity_ms, res.equity, res.gross, res.net, res.risk_mult):
            w.writerow([iso(int(row[0])), f"{row[1]:.4f}", f"{row[2]:.4f}", f"{row[3]:.4f}", f"{row[4]:.3f}"])
    with (out / "trades.csv").open("w", newline="") as fh:
        fields = ["ts_ms", "symbol", "side", "qty", "price", "notional", "fee", "reason", "qty_after"]
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(res.trades)
    (out / "metrics.json").write_text(json.dumps(res.metrics, indent=2, default=str) + "\n")
    (out / "events.json").write_text(json.dumps(res.events, indent=1, default=str) + "\n")


# -------------------------------------------------------------------- forward
def _notifier(cfg: Config):
    from .notify import Notifier
    return Notifier(cfg.notify, tag=cfg.run.mode)


def _broker_factory(cfg: Config, args: argparse.Namespace):
    if cfg.run.mode != "live":
        return None
    from .broker import make_live_broker
    if not getattr(args, "i_understand_live_trading", False):
        raise ConfigError("live mode sends real orders: pass --i-understand-live-trading to confirm")
    return lambda: make_live_broker(cfg)


def cmd_tick(args: argparse.Namespace) -> int:
    from .forward import tick
    from .state import StateError, StateStore
    cfg = _cfg(args)
    store = StateStore(cfg.run.state_dir)
    try:
        factory = _broker_factory(cfg, args)
        res = tick(cfg, store, accept_config_change=args.accept_config_change, notifier=_notifier(cfg),
                   broker=factory() if factory else None)
    except (StateError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception:  # unexpected bug: state was not advanced; fail loudly
        logging.getLogger("compounder").exception("tick crashed; state left unchanged")
        return 3
    print(res.message)
    for t in res.trades:
        print(f"  fill: {t['side']} {t['symbol']} {t['notional']:,.2f} USDT @ {t['price']:.6g} ({t['reason']})")
    for e in res.events:
        print(f"  event[{e['kind']}]: {e['message']}")
    summary = Path(cfg.run.state_dir) / "STATUS.md"
    if args.github_summary and summary.exists():
        import os
        gh = os.environ.get("GITHUB_STEP_SUMMARY")
        if gh:
            with open(gh, "a") as fh:
                fh.write(summary.read_text().replace("![equity curve](equity.svg)", ""))
    return res.exit_code


def cmd_run(args: argparse.Namespace) -> int:
    from .forward import run_daemon
    from .state import StateStore
    cfg = _cfg(args)
    try:
        factory = _broker_factory(cfg, args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return run_daemon(cfg, StateStore(cfg.run.state_dir), notifier=_notifier(cfg), broker_factory=factory)


def cmd_status(args: argparse.Namespace) -> int:
    from .report import build_summary, status_markdown
    from .state import StateStore
    cfg = _cfg(args)
    store = StateStore(cfg.run.state_dir)
    doc = store.load()
    if doc is None:
        print(f"no run found in {cfg.run.state_dir}; start one with `compounder tick` or `compounder run`")
        return 1
    s = build_summary(store, doc, cfg)
    if args.json:
        _print_json(s)
    else:
        print(status_markdown(s, store).replace("![equity curve](equity.svg)\n", ""))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    from .state import StateStore
    cfg = _cfg(args)
    store = StateStore(cfg.run.state_dir)
    if not store.exists():
        print("nothing to reset")
        return 0
    if cfg.run.mode == "live":
        print("refusing to reset a live run: close positions on the exchange and use a new state directory",
              file=sys.stderr)
        return 1
    if not args.yes:
        print("this archives the current run and starts a new one; pass --yes to confirm", file=sys.stderr)
        return 1
    with store.lock():
        dest = store.root / "archive" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("state.json", "trades.csv", "daily.csv", "equity.csv", "events.jsonl", "STATUS.md",
                     "equity.svg", "dashboard.html", "summary.json"):
            src = store.root / name
            if src.exists():
                shutil.move(str(src), str(dest / name))
    print(f"run archived to {dest}; the next tick starts a new run")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Operator action: clear the kill switch after reviewing what happened."""
    from .engine import EngineState
    from .forward import _clean
    from .state import StateStore
    cfg = _cfg(args)
    store = StateStore(cfg.run.state_dir)
    with store.lock():
        doc = store.load()
        if doc is None:
            print("no run found", file=sys.stderr)
            return 1
        st = EngineState.from_dict(doc["engine"])
        if not st.killed:
            print("the kill switch is not active")
            return 0
        if not args.yes:
            print(f"the kill switch fired at a {st.max_drawdown:.1%} drawdown. Resuming restarts trading with the "
                  "drawdown measured from current equity; pass --yes to confirm", file=sys.stderr)
            return 1
        st.killed = False
        st.liquidated = False
        st.hwm = st.last_equity
        st.hwm_history = []
        doc["engine"] = st.to_dict()
        store.append_events([{"ts_ms": int(time.time() * 1000), "kind": "info",
                              "message": f"kill switch reset by operator at equity {st.last_equity:,.2f}"}])
        store.save(_clean(doc))
    print("kill switch cleared; trading resumes on the next tick")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import functools
    import http.server
    cfg = _cfg(args)
    root = Path(cfg.run.state_dir).resolve()
    if not (root / "dashboard.html").exists():
        print(f"no dashboard in {root} yet; run a tick first", file=sys.stderr)
        return 1

    class Handler(http.server.SimpleHTTPRequestHandler):
        allowed = {"/dashboard.html", "/equity.svg", "/summary.json", "/STATUS.md"}

        def do_GET(self):  # noqa: N802
            if self.path in ("/", ""):
                self.path = "/dashboard.html"
            if self.path.split("?")[0] not in self.allowed:
                self.send_error(404)
                return
            super().do_GET()

    handler = functools.partial(Handler, directory=str(root))
    with http.server.ThreadingHTTPServer((args.host, args.port), handler) as srv:
        print(f"serving {root} on http://{args.host}:{args.port}/ (Ctrl+C to stop)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    out = cfg.to_dict()
    out["fingerprint"] = cfg.fingerprint()
    out["warmup_bars"] = cfg.strategy.warmup_bars
    _print_json(out)
    return 0


def cmd_live_check(args: argparse.Namespace) -> int:
    from .broker import make_live_broker
    cfg = _cfg(args)
    try:
        broker = make_live_broker(cfg)
        _print_json(broker.describe())
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="compounder", description="Risk-governed multi-asset crypto trading bot "
                                 "with a daily profit target.")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, state: bool = True) -> None:
        p.add_argument("--config", help=f"TOML config (default: {DEFAULT_CONFIG} if present)")
        if state:
            p.add_argument("--state-dir", help="override run.state_dir")

    p = sub.add_parser("download", help="download and verify the backtest dataset from Binance's archive")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--end", default="9999-12", help="last month to include, YYYY-MM (default: latest)")
    p.add_argument("--top", type=int, default=30, help="keep every symbol that was ever in the top K by volume")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--refresh-daily", action="store_true", help="rebuild the daily ranking panel too")
    p.set_defaults(fn=cmd_download)

    p = sub.add_parser("backtest", help="run the configured strategy and risk settings over history")
    common(p, state=False)
    p.add_argument("--data-dir", default="data")
    p.add_argument("--data", help="path to a panel .npz (default: <data-dir>/research/hourly.npz)")
    p.add_argument("--start", help="first bar date, e.g. 2025-01-01")
    p.add_argument("--end", help="end date (exclusive)")
    p.add_argument("--equity", type=float, help="starting equity in USDT (default: run.initial_equity)")
    p.add_argument("--cost-mult", type=float, default=1.0, help="multiply fees and slippage (stress test)")
    p.add_argument("--ruin", action="store_true", help="add block-bootstrap one-year outcome distribution")
    p.add_argument("--no-months", action="store_true", help="omit the month-by-month table")
    p.add_argument("--output", help="directory for daily.csv, equity.csv, trades.csv, metrics.json")
    p.set_defaults(fn=cmd_backtest)

    for name, fn, hlp in (("tick", cmd_tick, "advance the run to now (idempotent; schedule every 5-15 min)"),
                          ("run", cmd_run, "daemon: tick shortly after every hourly close")):
        p = sub.add_parser(name, help=hlp)
        common(p)
        p.add_argument("--i-understand-live-trading", action="store_true",
                       help="required when run.mode = 'live': real orders will be sent")
        if name == "tick":
            p.add_argument("--accept-config-change", action="store_true")
            p.add_argument("--github-summary", action="store_true")
        p.set_defaults(fn=fn)

    p = sub.add_parser("status", help="show the current run")
    common(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("reset", help="archive the current paper run and start fresh")
    common(p)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_reset)

    p = sub.add_parser("resume", help="clear the kill switch after a review")
    common(p)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_resume)

    p = sub.add_parser("serve", help="serve the status dashboard over HTTP")
    common(p)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("config", help="print the resolved configuration")
    common(p)
    p.set_defaults(fn=cmd_config)

    p = sub.add_parser("live-check", help="connect to the exchange and report the account (sends no orders)")
    common(p)
    p.set_defaults(fn=cmd_live_check)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return int(args.fn(args))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
