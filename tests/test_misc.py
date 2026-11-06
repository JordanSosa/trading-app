import pytest

from tradebot.config import ConfigError, config_from_dict, load_config
from tradebot.notify import Notifier
from tradebot.config import NotifyConfig


def test_default_config_is_valid():
    cfg = load_config()
    assert cfg.run.mode == "paper" and cfg.run.duration_days == 30


def test_config_rejects_unknown_keys_and_live_mode():
    with pytest.raises(ConfigError):
        config_from_dict({"risk": {"max_leverage": 3}})
    with pytest.raises(ConfigError):
        config_from_dict({"run": {"mode": "live"}})
    with pytest.raises(ConfigError):
        config_from_dict({"risk": {"max_exposure": 2.0}})


def test_repo_config_loads():
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "config" / "paper.toml"
    if p.exists():
        cfg = load_config(p)
        from tradebot.strategies import get_strategy
        get_strategy(cfg.strategy.name, cfg.strategy.params)


def test_notifier_channels_and_failure_isolation():
    calls = []

    def post(url, **kw):
        calls.append(url)
        raise RuntimeError("network down")

    n = Notifier(NotifyConfig(), env={"TRADEBOT_DISCORD_WEBHOOK": "https://discord.example/x",
                                      "TRADEBOT_NTFY_TOPIC": "abc"}, post=post)
    assert n.channels == ["discord", "ntfy"]
    n.send("hello")  # must not raise
    assert len(calls) == 2
    assert Notifier(NotifyConfig(), env={}).channels == []


def test_cli_parser_and_strategies_command(capsys):
    from tradebot.cli import main
    assert main(["strategies"]) == 0
    assert "buy_hold" in capsys.readouterr().out


def test_reset_archives_only_run_artefacts(tmp_path, capsys):
    from tradebot.cli import main
    (tmp_path / ".git").write_text("gitdir: /somewhere/.git/worktrees/state-branch\n")
    (tmp_path / ".gitignore").write_text(".tick.lock\n")
    for name in ("state.json", "trades.csv", "STATUS.md", "README.md"):
        (tmp_path / name).write_text("x")
    assert main(["reset", "--state-dir", str(tmp_path)]) == 1  # needs --yes
    assert main(["reset", "--state-dir", str(tmp_path), "--yes"]) == 0
    assert (tmp_path / ".git").exists() and (tmp_path / ".gitignore").exists()
    assert not (tmp_path / "state.json").exists()
    archived = list((tmp_path / "archive").iterdir())[0]
    assert (archived / "state.json").exists() and (archived / "README.md").exists()


def test_explicit_missing_config_is_an_error(tmp_path, capsys):
    from tradebot.cli import main
    assert main(["status", "--config", str(tmp_path / "nope.toml"), "--state-dir", str(tmp_path)]) == 1
    assert "not found" in capsys.readouterr().err


def test_int_and_float_config_values_fingerprint_identically():
    from tradebot.config import config_from_dict
    a = config_from_dict({"run": {"initial_cash": 10000}})
    b = config_from_dict({"run": {"initial_cash": 10000.0}})
    assert a == b and isinstance(a.run.initial_cash, float)


def test_notifier_logs_http_errors_without_secrets(caplog):
    class R:
        status_code = 404
    n = Notifier(NotifyConfig(), env={"TRADEBOT_SLACK_WEBHOOK": "https://hooks.example/SECRET"},
                 post=lambda *a, **k: R())
    with caplog.at_level("WARNING"):
        n.send("x")
    assert "HTTP 404" in caplog.text and "SECRET" not in caplog.text


def test_cli_perp_simulations_require_and_use_funding(tmp_path, capsys):
    import json as _json

    import numpy as np
    import pandas as pd

    from conftest import synthetic_candles
    from tradebot.cli import main

    df = synthetic_candles(3000)
    data = tmp_path / "candles.csv"
    df.to_csv(data, index_label="open_time")
    cfg = tmp_path / "hybrid.toml"
    cfg.write_text('[run]\ninstrument = "hybrid"\n[risk]\nmax_short = 1.0\n'
                   '[strategy]\nname = "buy_hold"\n[strategy.params]\nexposure = 1.0\n')
    missing = tmp_path / "nope.csv"
    with pytest.raises(SystemExit):
        main(["backtest", "--config", str(cfg), "--data", str(data), "--funding", str(missing)])
    assert "funding history" in capsys.readouterr().err

    # A short-only strategy run through the CLI must book the funding file.
    cfg.write_text('[run]\ninstrument = "hybrid"\n[risk]\nmax_short = 1.0\n'
                   '[strategy]\nname = "_test_scripted"\n')
    import helpers_strategies
    helpers_strategies.Scripted.targets = [-1.0]
    t0 = int(df.index[0].value // 1_000_000)
    times = np.arange(t0 + 8 * 3_600_000, t0 + len(df) * 3_600_000, 8 * 3_600_000)
    fund = pd.Series(0.0005, index=times, name="rate")
    fund.index.name = "time_ms"
    fpath = tmp_path / "funding.csv"
    fund.to_csv(fpath)
    capsys.readouterr()
    assert main(["backtest", "--config", str(cfg), "--data", str(data), "--funding", str(fpath)]) == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["continuous"]["funding_paid"] < 0      # the short received positive funding
