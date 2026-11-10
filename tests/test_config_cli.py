"""Configuration loading/validation and the command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from compounder import cli
from compounder.config import ConfigError, config_from_dict, load_config
from compounder.data.panel import Panel, liquidity_rank, HOUR_MS
from compounder.state import StateStore

from conftest import make_panel, random_walk

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["paper", "aggressive", "spot", "live.example"])
def test_shipped_configs_load(name):
    cfg = load_config(ROOT / "config" / f"{name}.toml")
    assert cfg.universe.size == cfg.strategy.universe_n == 20
    assert cfg.risk.kill_dd == 0.40 and cfg.risk.target_mode == "off"
    assert cfg.engine_params().daily_target == 0.02


def test_default_config_is_the_frozen_configuration():
    cfg = load_config(ROOT / "config" / "paper.toml")
    s, r = cfg.strategy, cfg.risk
    assert s.trend_horizons == (48, 96, 192, 384, 768) and s.xsmom_lookback == 336 and s.smooth_bars == 12
    assert (s.target_vol, s.short_scale, s.stop_vol_mult, s.carry_weight, s.reversal_weight) == (0.015, 0.5, 4.0, 0, 0)
    assert (r.daily_loss_limit, r.dd_soft, r.dd_hard, r.min_risk_mult, r.max_gross) == (0.03, 0.15, 0.35, 0.5, 3.0)
    assert load_config(ROOT / "config" / "aggressive.toml").strategy.target_vol == 0.03
    spot = load_config(ROOT / "config" / "spot.toml")
    assert not spot.risk.allow_short and spot.risk.max_gross == 1.0 and spot.strategy.short_scale == 0.0


def test_built_in_defaults_equal_the_shipped_default_config():
    assert load_config(None).fingerprint() == load_config(ROOT / "config" / "paper.toml").fingerprint()
    # A partial [strategy] section overrides the frozen strategy, not the bare dataclass defaults.
    cfg = config_from_dict({"strategy": {"target_vol": 0.01}})
    assert cfg.strategy.target_vol == 0.01 and cfg.strategy.xsmom_weight == 1.0 and cfg.strategy.smooth_bars == 12


def test_fingerprint_changes_with_trading_settings_only():
    base = config_from_dict({})
    assert config_from_dict({"notify": {"on_trade": True}}).fingerprint() == base.fingerprint()
    assert config_from_dict({"run": {"initial_equity": 500}}).fingerprint() == base.fingerprint()
    assert config_from_dict({"risk": {"daily_loss_limit": 0.05}}).fingerprint() != base.fingerprint()
    assert config_from_dict({"strategy": {"target_vol": 0.03}}).fingerprint() != base.fingerprint()
    # 1 and 1.0 in TOML must not change the fingerprint.
    assert (config_from_dict({"strategy": {"trend_weight": 1}}).fingerprint()
            == config_from_dict({"strategy": {"trend_weight": 1.0}}).fingerprint())


@pytest.mark.parametrize("raw, fragment", [
    ({"run": {"initial_equity": 50}}, "at least 100"),
    ({"run": {"mode": "yolo"}}, "run.mode"),
    ({"risk": {"kill_dd": 0}}, "kill switch cannot be disabled"),
    ({"risk": {"allow_short": False, "max_gross": 2.0}}, "without shorting"),
    ({"risk": {"max_gross": 50}}, "max_gross"),
    ({"risk": {"target_mode": "maybe"}}, "target_mode"),
    ({"strategy": {"target_vol": 0.9}}, "target_vol"),
    ({"strategy": {"universe_n": 10}}, "universe.size"),
    ({"strategy": {"bogus": 1}}, "unknown keys"),
    ({"bogus": {}}, "unknown config sections"),
    ({"data": {"sources": ["nasdaq"]}}, "unknown data sources"),
    ({"risk": {"allow_short": "yes"}}, "true or false"),
])
def test_invalid_configs_are_rejected(raw, fragment):
    with pytest.raises(ConfigError) as exc:
        config_from_dict(raw)
    assert fragment in str(exc.value)


def test_missing_config_file():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/file.toml")


def small_dataset(tmp_path) -> Path:
    t, n = 24 * 120, 8
    p = make_panel(random_walk(t, n, vol=0.01, seed=5), [f"C{i}USDT" for i in range(n)], wick=0.002)
    vol = np.tile(np.linspace(8e6, 1e6, n), (t, 1))
    p = Panel(**{**p.__dict__, "quote_volume": vol, "rank": liquidity_rank(p.times, vol, p.close, HOUR_MS)})
    path = tmp_path / "panel.npz"
    p.save(path)
    return path


def write_config(tmp_path, **risk) -> Path:
    lines = ["[universe]", "size = 5", "[strategy]", "universe_n = 5", "trend_horizons = [12, 24, 48]",
             "vol_window = 36", "xsmom_lookback = 48", "smooth_bars = 4", "[run]", "initial_equity = 1000.0",
             f'state_dir = "{tmp_path / "state"}"', "[risk]"] + [f"{k} = {v}" for k, v in risk.items()]
    path = tmp_path / "cfg.toml"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_cli_backtest_prints_metrics_and_writes_ledgers(tmp_path, capsys):
    data = small_dataset(tmp_path)
    cfg = write_config(tmp_path)
    out_dir = tmp_path / "bt"
    code = cli.main(["backtest", "--config", str(cfg), "--data", str(data), "--ruin", "--no-months",
                     "--output", str(out_dir)])
    assert code == 0
    m = json.loads(capsys.readouterr().out)
    assert m["days"] > 60 and "compound_daily" in m and "bootstrap_1y" in m and "by_month" not in m
    assert m["initial_equity"] == 1000.0
    for name in ("daily.csv", "equity.csv", "trades.csv", "metrics.json"):
        assert (out_dir / name).exists()
    # Doubling costs can only hurt.
    cli.main(["backtest", "--config", str(cfg), "--data", str(data), "--no-months", "--cost-mult", "2"])
    m2 = json.loads(capsys.readouterr().out)
    assert m2["final_equity"] < m["final_equity"]


def test_cli_backtest_without_dataset_explains(tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main(["backtest", "--config", str(write_config(tmp_path)), "--data-dir", str(tmp_path / "none")])
    assert "compounder download" in capsys.readouterr().err


def test_cli_config_status_reset_resume(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["config", "--config", str(cfg)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["strategy"]["universe_n"] == 5 and len(shown["fingerprint"]) == 16
    assert cli.main(["status", "--config", str(cfg)]) == 1                 # nothing has run yet
    capsys.readouterr()
    store = StateStore(tmp_path / "state")
    from compounder.config import load_config as lc
    from compounder.forward import new_run_document
    doc = new_run_document(lc(cfg), 1_704_067_200_000)
    doc["engine"]["killed"] = True
    doc["engine"]["max_drawdown"] = 0.41
    store.save(doc)
    assert cli.main(["status", "--config", str(cfg), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "killed"
    assert cli.main(["resume", "--config", str(cfg)]) == 1                 # needs --yes
    assert cli.main(["resume", "--config", str(cfg), "--yes"]) == 0
    assert store.load()["engine"]["killed"] is False
    assert cli.main(["reset", "--config", str(cfg)]) == 1                  # needs --yes
    assert cli.main(["reset", "--config", str(cfg), "--yes"]) == 0
    assert store.load() is None and list((tmp_path / "state" / "archive").iterdir())


def test_cli_reset_archives_state_from_the_previous_app(tmp_path):
    cfg = write_config(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "state.json").write_text('{"schema_version": 1, "engine": {"qty": 0.5}}')
    (state / "trips.csv").write_text("entry_ms,exit_ms\n")
    assert cli.main(["reset", "--config", str(cfg), "--yes"]) == 0
    assert not (state / "state.json").exists() and not (state / "trips.csv").exists()


def test_live_mode_requires_the_explicit_flag(tmp_path, capsys):
    cfg = tmp_path / "live.toml"
    cfg.write_text(f'[run]\nmode = "live"\nstate_dir = "{tmp_path / "s"}"\n')
    assert cli.main(["tick", "--config", str(cfg)]) == 1
    assert "--i-understand-live-trading" in capsys.readouterr().err


def test_default_paper_deposit_is_one_thousand_and_the_minimum_is_one_hundred():
    assert load_config(None).run.initial_equity == 1000.0
    assert load_config(ROOT / "config" / "paper.toml").run.initial_equity == 1000.0
    assert config_from_dict({"run": {"initial_equity": 100}}).run.initial_equity == 100.0
