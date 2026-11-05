"""Command-line interface: ``tradebot <command>``."""

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

from .config import ConfigError, load_config

DEFAULT_CONFIG = "config/paper.toml"


def _cfg(args: argparse.Namespace):
    # An explicitly passed --config must exist; without one, use
    # config/paper.toml when present, else the built-in defaults.
    path = args.config if args.config else (DEFAULT_CONFIG if Path(DEFAULT_CONFIG).exists() else None)
    cfg = load_config(path)
    if getattr(args, "state_dir", None):
        cfg = dataclasses.replace(cfg, run=dataclasses.replace(cfg.run, state_dir=args.state_dir))
    if getattr(args, "strategy", None):
        params = json.loads(args.params) if getattr(args, "params", None) else {}
        cfg = dataclasses.replace(cfg, strategy=dataclasses.replace(cfg.strategy, name=args.strategy, params=params))
    elif getattr(args, "params", None):
        cfg = dataclasses.replace(cfg, strategy=dataclasses.replace(
            cfg.strategy, params={**cfg.strategy.params, **json.loads(args.params)}))
    return cfg


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str, allow_nan=True))


def cmd_download(args: argparse.Namespace) -> int:
    from .data.history import download_history
    df = download_history(args.data_dir, args.timeframe, start=args.start)
    print(f"{len(df)} closed {args.timeframe} bars {df.index[0]} .. {df.index[-1]} -> "
          f"{Path(args.data_dir) / f'BTCUSDT-{args.timeframe}.csv'}")
    return 0


def _load_candles(args: argparse.Namespace, cfg):
    from .data.history import load_history
    path = Path(args.data or f"data/BTCUSDT-{cfg.run.timeframe}.csv")
    if not path.exists():
        print(f"history file {path} not found; run `tradebot download` first", file=sys.stderr)
        raise SystemExit(1)
    return load_history(path)


def cmd_backtest(args: argparse.Namespace) -> int:
    from .backtest import run_backtest, run_campaigns
    from .metrics import campaign_summary
    from .strategies import get_strategy
    cfg = _cfg(args)
    df = _load_candles(args, cfg)
    strat = get_strategy(cfg.strategy.name, cfg.strategy.params)
    sig = strat.signals(df)
    res = run_backtest(df, strat, cfg, args.start, args.end, signals=sig)
    out: dict[str, Any] = {"continuous": res.metrics}
    if args.campaigns:
        camp = run_campaigns(df, strat, cfg, args.start, args.end, days=cfg.run.duration_days, signals=sig)
        out["campaigns"] = campaign_summary(camp)
    if args.output:
        Path(args.output).mkdir(parents=True, exist_ok=True)
        res.equity.to_csv(Path(args.output) / "equity.csv")
        res.trades.to_csv(Path(args.output) / "trades.csv", index=False)
        res.trips.to_csv(Path(args.output) / "trips.csv", index=False)
    _print_json(out)
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from .research import evaluate_period
    from .strategies import get_strategy
    cfg = _cfg(args)
    df = _load_candles(args, cfg)
    strat = get_strategy(cfg.strategy.name, cfg.strategy.params)
    _print_json(evaluate_period(df, strat, cfg, period=args.period))
    return 0


def _notifier(cfg):
    from .notify import Notifier
    return Notifier(cfg.notify)


def cmd_tick(args: argparse.Namespace) -> int:
    from .forward import tick
    from .state import StateError, StateStore
    cfg = _cfg(args)
    store = StateStore(cfg.run.state_dir)
    try:
        res = tick(cfg, store, accept_config_change=args.accept_config_change, notifier=_notifier(cfg))
    except StateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception:  # unexpected bug: state was not advanced; fail loudly
        logging.getLogger("tradebot").exception("tick crashed; state left unchanged")
        return 3
    print(res.message)
    for t in res.trades:
        print(f"  fill: {t['side']} {t['qty']:.5f} BTC @ {t['price']:,.2f} ({t['reason']})")
    for e in res.events:
        print(f"  event[{e['kind']}]: {e['message']}")
    summary_path = Path(cfg.run.state_dir) / "STATUS.md"
    if args.github_summary and summary_path.exists():
        import os
        gh = os.environ.get("GITHUB_STEP_SUMMARY")
        if gh:
            with open(gh, "a") as fh:
                fh.write(summary_path.read_text().replace("![equity curve](equity.svg)", ""))
    return res.exit_code


def cmd_run(args: argparse.Namespace) -> int:
    from .forward import run_daemon
    from .state import StateStore
    cfg = _cfg(args)
    return run_daemon(cfg, StateStore(cfg.run.state_dir), notifier=_notifier(cfg))


def cmd_status(args: argparse.Namespace) -> int:
    from .report import build_summary
    from .state import StateStore
    cfg = _cfg(args)
    store = StateStore(cfg.run.state_dir)
    doc = store.load()
    if doc is None:
        print(f"no paper run in {cfg.run.state_dir}; start one with `tradebot tick`")
        return 1
    s = build_summary(store, doc)
    if args.json:
        _print_json(s)
        return 0
    print(f"status       {s['status']}  (run {s['run_id']}, day {s['days_elapsed']:.1f}/{s['days_total']:g})")
    print(f"equity       {s['equity']:,.2f} USDT  ({s['return']:+.2%})   buy&hold {s['buy_hold_return']:+.2%}")
    print(f"position     {s['qty']:.5f} BTC  exposure {s['exposure']:.1%}  cash {s['cash']:,.2f}")
    print(f"max drawdown {s['max_drawdown']:.2%}   fees {s['fees_paid']:,.2f}   trades {s['trades']}")
    print(f"last tick    {s['health'].get('last_tick_at')}  via {s['health'].get('last_provider')}  "
          f"failures {s['health'].get('consecutive_failures', 0)}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .report import write_reports
    from .state import StateStore
    cfg = _cfg(args)
    store = StateStore(cfg.run.state_dir)
    doc = store.load()
    if doc is None:
        print("no run state found", file=sys.stderr)
        return 1
    write_reports(store, doc, cfg)
    print(f"reports written to {store.root}")
    return 0


RUN_ARTEFACTS = ("state.json", "trades.csv", "trips.csv", "equity.csv", "events.jsonl", "STATUS.md",
                 "README.md", "equity.svg", "dashboard.html", "summary.json")


def cmd_reset(args: argparse.Namespace) -> int:
    from .state import StateError, StateStore
    cfg = _cfg(args)
    root = Path(cfg.run.state_dir)
    present = [root / n for n in RUN_ARTEFACTS if (root / n).exists()]
    if not present:
        print("nothing to reset")
        return 0
    if not args.yes:
        print("refusing to reset without --yes (the current run will be archived, not deleted)", file=sys.stderr)
        return 1
    try:
        with StateStore(root).lock():
            archive = root / "archive" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            archive.mkdir(parents=True)
            # Only known run artefacts move; dotfiles (.git of a worktree,
            # .gitignore, the lock) and anything else stay where they are.
            for p in present:
                shutil.move(str(p), archive / p.name)
    except StateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"previous run archived to {archive}; the next `tradebot tick` starts a new run")
    return 0


def cmd_strategies(args: argparse.Namespace) -> int:  # noqa: ARG001
    from .strategies import REGISTRY, available
    for name in available():
        cls = REGISTRY[name]
        print(f"{name:28s} {cls.description}")
        print(f"{'':28s} defaults: {json.dumps(cls.default_params)}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    import pandas as pd
    from .report import write_reports
    from .simulate import replay
    from .state import StateStore
    cfg = _cfg(args)
    df = _load_candles(args, cfg)
    end = df.index[-1] + pd.Timedelta(hours=1)
    start = pd.Timestamp(args.start, tz="UTC") if args.start else end - pd.Timedelta(days=cfg.run.duration_days + 1)
    if not (df.index[0] < start < end):
        print(f"--start must lie inside the data ({df.index[0]} .. {end})", file=sys.stderr)
        return 1
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        print(f"{out} is not empty; choose another --out", file=sys.stderr)
        return 1
    res = replay(cfg, df, start, end, out, tick_minutes=args.tick_minutes)
    store = StateStore(out)
    doc = store.load()
    s = write_reports(store, doc, cfg)
    print(f"replayed {len(res)} ticks from {start} -> status {s['status']}, equity {s['equity']:,.2f} "
          f"({s['return']:+.2%}), buy&hold {s['buy_hold_return']:+.2%}; reports in {out}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import functools
    import http.server
    cfg = _cfg(args)
    root = Path(cfg.run.state_dir).resolve()
    if not root.exists():
        print(f"state directory {root} does not exist yet; run `tradebot tick` first", file=sys.stderr)
        return 1

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path in ("/", ""):
                self.path = "/dashboard.html"
            return super().do_GET()

        def log_message(self, fmt, *a):  # quieter logs
            logging.getLogger("tradebot.serve").info(fmt, *a)

    handler = functools.partial(Handler, directory=str(root))
    with http.server.ThreadingHTTPServer((args.host, args.port), handler) as httpd:
        print(f"serving {root} on http://{args.host}:{args.port}/ (Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tradebot", description="BTC/USDT paper-trading bot")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, strategy=False, data=False):
        sp.add_argument("--config", default=None,
                        help=f"TOML config (default: {DEFAULT_CONFIG} if present, else built-in defaults)")
        if strategy:
            sp.add_argument("--strategy", help="override strategy name")
            sp.add_argument("--params", help="override strategy params as JSON")
        if data:
            sp.add_argument("--data", help="history CSV (default: data/BTCUSDT-<tf>.csv)")
        return sp

    sp = sub.add_parser("download", help="download BTCUSDT history from data.binance.vision")
    sp.add_argument("--data-dir", default="data")
    sp.add_argument("--timeframe", default="1h")
    sp.add_argument("--start", default="2017-08-01")
    sp.set_defaults(func=cmd_download)

    sp = common(sub.add_parser("backtest", help="backtest a strategy on history"), strategy=True, data=True)
    sp.add_argument("--start")
    sp.add_argument("--end")
    sp.add_argument("--campaigns", action="store_true", help="also simulate rolling 30-day campaigns")
    sp.add_argument("--output", help="write equity/trades CSVs to this directory")
    sp.set_defaults(func=cmd_backtest)

    sp = common(sub.add_parser("evaluate", help="research score for a fixed period"), strategy=True, data=True)
    sp.add_argument("--period", choices=["train", "valid", "holdout"], default="train")
    sp.set_defaults(func=cmd_evaluate)

    sp = common(sub.add_parser("tick", help="advance the paper run to now (idempotent)"))
    sp.add_argument("--state-dir")
    sp.add_argument("--accept-config-change", action="store_true")
    sp.add_argument("--github-summary", action="store_true", help="append STATUS.md to $GITHUB_STEP_SUMMARY")
    sp.set_defaults(func=cmd_tick)

    sp = common(sub.add_parser("run", help="daemon: tick after every bar close until the run completes"))
    sp.add_argument("--state-dir")
    sp.set_defaults(func=cmd_run)

    sp = common(sub.add_parser("status", help="print the current paper run status"))
    sp.add_argument("--state-dir")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = common(sub.add_parser("report", help="regenerate STATUS.md / dashboard.html"))
    sp.add_argument("--state-dir")
    sp.set_defaults(func=cmd_report)

    sp = common(sub.add_parser("reset", help="archive the current run so the next tick starts a new one"))
    sp.add_argument("--state-dir")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_reset)

    sp = common(sub.add_parser("serve", help="serve the dashboard of a state directory over HTTP"))
    sp.add_argument("--state-dir")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8080)
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("strategies", help="list available strategies")
    sp.set_defaults(func=cmd_strategies)

    sp = common(sub.add_parser("replay", help="simulate the forward run tick-by-tick on history"),
                strategy=True, data=True)
    sp.add_argument("--start", help="simulated start time (default: duration+1 days before end of data)")
    sp.add_argument("--tick-minutes", type=int, default=15)
    sp.add_argument("--out", default="replay-state")
    sp.set_defaults(func=cmd_replay)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # urllib3 logs full request URLs at DEBUG, which would leak webhook tokens.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
