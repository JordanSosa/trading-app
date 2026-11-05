"""Configuration loading.

Configuration lives in a TOML file (default: ``config/paper.toml``). Every
section maps onto a frozen dataclass so that typos fail loudly instead of being
silently ignored.
"""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


class ConfigError(ValueError):
    """Raised when the configuration file is invalid."""


@dataclass(frozen=True)
class RunConfig:
    symbol: str = "BTCUSDT"
    timeframe: str = "1h"
    initial_cash: float = 10_000.0
    duration_days: float = 30.0
    # What to do when the campaign ends: "flatten" sells everything to USDT so
    # the result is fully realised; "hold" keeps the position and just stops.
    end_action: str = "flatten"
    state_dir: str = "state"
    # Only paper trading is implemented. Anything else is refused.
    mode: str = "paper"
    # "spot": long/flat BTC against USDT. "perp": USDT-margined perpetual
    # futures (needed for short positions), which pays/receives funding.
    instrument: str = "spot"

    @property
    def timeframe_ms(self) -> int:
        return TIMEFRAME_MS[self.timeframe]


@dataclass(frozen=True)
class CostConfig:
    # Binance spot taker fee without BNB discount.
    fee_rate: float = 0.001
    # Slippage applied against us on every fill, in basis points.
    slippage_bps: float = 3.0
    # Binance BTCUSDT filters: NOTIONAL min 5 USDT, LOT_SIZE step 0.00001 BTC.
    min_notional: float = 10.0
    qty_step: float = 0.00001

    @property
    def slippage(self) -> float:
        return self.slippage_bps / 10_000.0


@dataclass(frozen=True)
class RiskConfig:
    # Hard cap on BTC exposure as a fraction of equity (spot, no leverage).
    max_exposure: float = 1.0
    # Largest short exposure as a fraction of equity (0 = long/flat only).
    # Requires run.instrument = "perp".
    max_short: float = 0.0
    # Skip rebalances smaller than this fraction of equity (fee control).
    # Entries from flat and full exits are never skipped.
    min_rebalance: float = 0.10
    # Circuit breaker: if equity falls this far below its high-water mark,
    # go flat and pause new entries for `circuit_breaker_hours`.
    circuit_breaker_dd: float = 0.10
    circuit_breaker_hours: float = 48.0
    # Campaign stop: if equity falls this far below initial capital, flatten
    # and stop trading for the rest of the run.
    campaign_stop_loss: float = 0.15
    # After an intrabar protective stop fires, block re-entry until the
    # strategy target has dropped to zero at least once (prevents churn).
    stop_rearm: bool = True
    # Additionally block re-entry for this many bars after a stop-out.
    stop_cooldown_bars: int = 0


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "buy_hold"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DataConfig:
    providers: tuple[str, ...] = ("binance_vision", "okx", "kucoin", "gate", "coinbase")
    # Closed bars fetched on every tick to compute indicators.
    lookback_bars: int = 3000
    # Cross-check the latest close against a second provider.
    sanity_check: bool = True
    max_price_deviation: float = 0.015
    request_timeout_s: float = 15.0
    retries: int = 3


@dataclass(frozen=True)
class NotifyConfig:
    # Webhook URLs and tokens are read from environment variables only:
    # TRADEBOT_DISCORD_WEBHOOK, TRADEBOT_SLACK_WEBHOOK,
    # TRADEBOT_TELEGRAM_BOT_TOKEN + TRADEBOT_TELEGRAM_CHAT_ID.
    on_trade: bool = True
    on_alert: bool = True
    daily_summary: bool = True


@dataclass(frozen=True)
class Config:
    run: RunConfig = field(default_factory=RunConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    data: DataConfig = field(default_factory=DataConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


_SECTIONS = {
    "run": RunConfig,
    "costs": CostConfig,
    "risk": RiskConfig,
    "strategy": StrategyConfig,
    "data": DataConfig,
    "notify": NotifyConfig,
}


def _build(cls: type, raw: dict[str, Any], section: str) -> Any:
    known = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(raw) - set(known)
    if unknown:
        raise ConfigError(f"[{section}] unknown keys: {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "providers":
            value = tuple(value)
        default = known[key].default
        # TOML distinguishes 1 from 1.0; normalise so an unchanged config always
        # produces the same run fingerprint.
        if isinstance(default, float) and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        kwargs[key] = value
    return cls(**kwargs)


def config_from_dict(raw: dict[str, Any]) -> Config:
    unknown = set(raw) - set(_SECTIONS)
    if unknown:
        raise ConfigError(f"unknown config sections: {sorted(unknown)}")
    parts = {name: _build(cls, raw.get(name, {}), name) for name, cls in _SECTIONS.items()}
    cfg = Config(**parts)
    validate(cfg)
    return cfg


def load_config(path: str | Path | None = None) -> Config:
    if path is None:
        return config_from_dict({})
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    with p.open("rb") as fh:
        raw = tomllib.load(fh)
    return config_from_dict(raw)


def validate(cfg: Config) -> None:
    r, c, k, d = cfg.run, cfg.costs, cfg.risk, cfg.data
    if r.mode != "paper":
        raise ConfigError("only mode = 'paper' is supported; live trading is not implemented")
    if r.timeframe not in TIMEFRAME_MS:
        raise ConfigError(f"unsupported timeframe {r.timeframe!r}; choose from {sorted(TIMEFRAME_MS)}")
    if r.symbol != "BTCUSDT":
        raise ConfigError("only BTCUSDT is supported")
    if r.initial_cash <= 0:
        raise ConfigError("initial_cash must be positive")
    if r.duration_days <= 0:
        raise ConfigError("duration_days must be positive")
    if r.end_action not in ("flatten", "hold"):
        raise ConfigError("end_action must be 'flatten' or 'hold'")
    if not 0 <= c.fee_rate < 0.05:
        raise ConfigError("fee_rate must be in [0, 0.05)")
    if not 0 <= c.slippage_bps < 500:
        raise ConfigError("slippage_bps must be in [0, 500)")
    if c.qty_step <= 0 or c.min_notional < 0:
        raise ConfigError("qty_step must be > 0 and min_notional >= 0")
    if not 0 < k.max_exposure <= 1.0:
        raise ConfigError("max_exposure must be in (0, 1] (no leverage)")
    if r.instrument not in ("spot", "perp"):
        raise ConfigError("instrument must be 'spot' or 'perp'")
    if not 0 <= k.max_short <= 1.0:
        raise ConfigError("max_short must be in [0, 1] (no leverage)")
    if k.max_short > 0 and r.instrument != "perp":
        raise ConfigError("short exposure (max_short > 0) requires run.instrument = 'perp'")
    if not 0 <= k.min_rebalance < 1:
        raise ConfigError("min_rebalance must be in [0, 1)")
    if not 0 < k.circuit_breaker_dd < 1 or k.circuit_breaker_hours < 0:
        raise ConfigError("circuit_breaker_dd must be in (0, 1) and circuit_breaker_hours >= 0")
    if not 0 < k.campaign_stop_loss < 1:
        raise ConfigError("campaign_stop_loss must be in (0, 1)")
    if k.stop_cooldown_bars < 0:
        raise ConfigError("stop_cooldown_bars must be >= 0")
    if d.lookback_bars < 100:
        raise ConfigError("lookback_bars must be >= 100")
    if not d.providers:
        raise ConfigError("at least one data provider is required")
    from .strategies import available  # local import: strategies depend on config
    if cfg.strategy.name not in available():
        raise ConfigError(f"unknown strategy {cfg.strategy.name!r}; available: {available()}")
