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
