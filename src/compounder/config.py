"""Configuration: a TOML file mapped onto frozen dataclasses.

Unknown keys fail loudly instead of being silently ignored. The strategy and
risk sections map directly onto :class:`compounder.strategy.StrategyParams`
and :class:`compounder.engine.EngineParams`, so a backtest and a forward run
started from the same file trade identically.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .engine import EngineParams
from .strategy import StrategyParams

TIMEFRAME_MS = {"1h": 3_600_000}
MIN_DEPOSIT = 100.0

# The strategy frozen in research/SELECTION.md. A [strategy] section in a config
# file overrides individual values of this, not of the bare StrategyParams
# defaults (which are the neutral starting point the research scripts use).
DEFAULT_STRATEGY = StrategyParams(
    universe_n=20, vol_window=336, trend_weight=1.0, trend_horizons=(48, 96, 192, 384, 768),
    trend_response="linear", xsmom_weight=1.0, xsmom_lookback=336, carry_weight=0.0, reversal_weight=0.0,
    short_scale=0.5, target_vol=0.015, vol_mode="target", rho=0.7, max_symbol_risk=0.5, smooth_bars=12,
    stop_vol_mult=4.0)


class ConfigError(ValueError):
    """Raised when the configuration is invalid."""


@dataclass(frozen=True)
class RunConfig:
    mode: str = "paper"              # "paper": simulated fills on live data; "live": real orders (opt-in)
    timeframe: str = "1h"
    initial_equity: float = 1000.0   # paper deposit in USDT, minimum 100 (live runs use the account's own equity)
    state_dir: str = "state"

    @property
    def interval_ms(self) -> int:
        return TIMEFRAME_MS[self.timeframe]


@dataclass(frozen=True)
class UniverseConfig:
    size: int = 20                   # trade the N most liquid crypto perpetuals
    refresh_days: int = 7            # re-rank on this grid of UTC days
    volume_window_days: int = 30
    min_history_days: int = 30
    exclude: tuple[str, ...] = ()    # base assets never traded, e.g. ["LUNA"]


@dataclass(frozen=True)
class CostConfig:
    fee_rate: float = 0.0005         # taker fee per side
    slippage_bps: float = 3.0
    stop_slippage_bps: float = 10.0
    stop_overshoot: float = 0.25
    min_notional: float = 5.0
    min_position_mult: float = 2.0
    maint_margin: float = 0.025
    liquidation_fee: float = 0.015


@dataclass(frozen=True)
class RiskConfig:
    allow_short: bool = True
    max_gross: float = 3.0
    max_weight: float = 0.6
    rebalance_band: float = 0.25
    stop_cooldown_bars: int = 0
    stale_bars: int = 48             # settle a position whose market has printed no bar for this many hours
    daily_target: float = 0.02       # the 2% a day target, measured on every completed day
    target_mode: str = "off"         # "off": measure only; "flatten" / "trail": close winners at the target
    trail_giveback: float = 0.5
    daily_loss_limit: float = 0.03
    dd_soft: float = 0.15
    dd_hard: float = 0.35
    min_risk_mult: float = 0.5
    kill_dd: float = 0.40
    hwm_window_days: float = 0.0
    day_offset_hours: float = 0.0


@dataclass(frozen=True)
class DataConfig:
    sources: tuple[str, ...] = ("okx", "gate")   # first = primary, others = fallback and cross-check
    cross_check: bool = True
    max_price_deviation: float = 0.02
    request_timeout_s: float = 15.0
    retries: int = 3
    catchup_bars: int = 168          # bars of downtime that can be replayed exactly
    # Paper fills use the open of the bar that has just started. A tick that runs
    # later than this many minutes into the bar fills at the latest price instead,
    # as a live order sent at that moment would.
    late_fill_minutes: float = 5.0


@dataclass(frozen=True)
class NotifyConfig:
    on_trade: bool = False
    on_alert: bool = True
    daily_summary: bool = True


@dataclass(frozen=True)
class LiveConfig:
    exchange: str = "okx"            # ccxt exchange id (OKX is the only supported venue)
    sandbox: bool = True             # use the exchange's demo/testnet environment
    dry_run: bool = True             # log orders instead of sending them
    margin_mode: str = "cross"
    leverage: int = 5                # leverage set on each traded contract (a cap, not a target)
    max_order_notional: float = 2_000.0   # refuse any single order above this many USDT
    max_account_equity: float = 25_000.0  # refuse to trade an account larger than this


@dataclass(frozen=True)
class Config:
    run: RunConfig = field(default_factory=RunConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    strategy: StrategyParams = field(default_factory=lambda: DEFAULT_STRATEGY)
    risk: RiskConfig = field(default_factory=RiskConfig)
    data: DataConfig = field(default_factory=DataConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    live: LiveConfig = field(default_factory=LiveConfig)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["strategy"] = self.strategy.to_dict()
        return d

    def engine_params(self) -> EngineParams:
        c, r = self.costs, self.risk
        return EngineParams(
            interval_ms=self.run.interval_ms, fee_rate=c.fee_rate, slippage=c.slippage_bps / 1e4,
            stop_slippage=c.stop_slippage_bps / 1e4, stop_overshoot=c.stop_overshoot,
            stop_cooldown_bars=r.stop_cooldown_bars, stale_bars=r.stale_bars,
            min_notional=c.min_notional, min_position_mult=c.min_position_mult, allow_short=r.allow_short,
            maint_margin=c.maint_margin, liquidation_fee=c.liquidation_fee, max_gross=r.max_gross,
            max_weight=r.max_weight, rebalance_band=r.rebalance_band,
            daily_target=r.daily_target if r.daily_target > 0 else None, target_mode=r.target_mode,
            trail_giveback=r.trail_giveback,
            daily_loss_limit=r.daily_loss_limit if r.daily_loss_limit > 0 else None,
            dd_soft=r.dd_soft, dd_hard=r.dd_hard, min_risk_mult=r.min_risk_mult,
            kill_dd=r.kill_dd if r.kill_dd > 0 else None, hwm_window_days=r.hwm_window_days,
            day_offset_ms=int(r.day_offset_hours * 3_600_000))

    def fingerprint(self) -> str:
        """Hash of everything that changes trading behaviour.

        A running account refuses to continue under a different fingerprint
        unless the operator accepts the change explicitly.
        """
        material = {"strategy": self.strategy.to_dict(), "risk": dataclasses.asdict(self.risk),
                    "costs": dataclasses.asdict(self.costs), "universe": dataclasses.asdict(self.universe),
                    "mode": self.run.mode, "timeframe": self.run.timeframe}
        return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()[:16]


_SECTIONS: dict[str, type] = {
    "run": RunConfig, "universe": UniverseConfig, "costs": CostConfig, "strategy": StrategyParams,
    "risk": RiskConfig, "data": DataConfig, "notify": NotifyConfig, "live": LiveConfig,
}


def _build(cls: type, raw: dict[str, Any], section: str) -> Any:
    known = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(raw) - set(known)
    if unknown:
        raise ConfigError(f"[{section}] unknown keys: {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        default = known[key].default
        if isinstance(value, list):
            value = tuple(value)
        # TOML distinguishes 1 from 1.0; normalise so an unchanged file always
        # produces the same fingerprint.
        if isinstance(default, float) and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if isinstance(default, bool) and not isinstance(value, bool):
            raise ConfigError(f"[{section}] {key} must be true or false")
        kwargs[key] = value
    try:
        if cls is StrategyParams:
            return dataclasses.replace(DEFAULT_STRATEGY, **kwargs)
        return cls(**kwargs)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"[{section}] {exc}") from exc


def config_from_dict(raw: dict[str, Any]) -> Config:
    unknown = set(raw) - set(_SECTIONS)
    if unknown:
        raise ConfigError(f"unknown config sections: {sorted(unknown)}")
    cfg = Config(**{name: _build(cls, raw.get(name, {}), name) for name, cls in _SECTIONS.items()})
    validate(cfg)
    return cfg


def load_config(path: str | Path | None = None) -> Config:
    if path is None:
        return config_from_dict({})
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    with p.open("rb") as fh:
        return config_from_dict(tomllib.load(fh))


def validate(cfg: Config) -> None:
    r, u, c, k, d, s, lv = cfg.run, cfg.universe, cfg.costs, cfg.risk, cfg.data, cfg.strategy, cfg.live
    if r.mode not in ("paper", "live"):
        raise ConfigError("run.mode must be 'paper' or 'live'")
    if r.timeframe not in TIMEFRAME_MS:
        raise ConfigError(f"unsupported timeframe {r.timeframe!r}; supported: {sorted(TIMEFRAME_MS)}")
    if r.initial_equity < MIN_DEPOSIT:
        raise ConfigError(f"initial_equity must be at least {MIN_DEPOSIT:.0f} USDT: below that, positions "
                          "fall under exchange minimum order sizes")
    if u.size < 1 or u.size != s.universe_n:
        raise ConfigError("universe.size must be >= 1 and equal to strategy.universe_n")
    if u.refresh_days < 1 or u.volume_window_days < 1 or u.min_history_days < 1:
        raise ConfigError("universe windows must be at least one day")
    if not 0 <= c.fee_rate < 0.01 or not 0 <= c.slippage_bps < 200:
        raise ConfigError("fee_rate must be in [0, 0.01) and slippage_bps in [0, 200)")
    if c.min_notional < 0 or c.min_position_mult < 1:
        raise ConfigError("min_notional must be >= 0 and min_position_mult >= 1")
    if not 0 < k.max_gross <= 10:
        raise ConfigError("risk.max_gross must be in (0, 10]")
    if not k.allow_short and k.max_gross > 1:
        raise ConfigError("without shorting (spot) max_gross cannot exceed 1")
    if not 0 < k.max_weight <= k.max_gross:
        raise ConfigError("risk.max_weight must be in (0, max_gross]")
    if not 0 <= k.rebalance_band < 1:
        raise ConfigError("risk.rebalance_band must be in [0, 1)")
    if k.target_mode not in ("flatten", "trail", "off"):
        raise ConfigError("risk.target_mode must be 'flatten', 'trail' or 'off'")
    if not 0 <= k.daily_target < 1 or not 0 <= k.daily_loss_limit < 1:
        raise ConfigError("daily_target and daily_loss_limit must be in [0, 1)")
    if not 0 <= k.dd_soft <= k.dd_hard < 1 or not 0 < k.min_risk_mult <= 1:
        raise ConfigError("need 0 <= dd_soft <= dd_hard < 1 and 0 < min_risk_mult <= 1")
    if not 0 < k.kill_dd < 1:
        raise ConfigError("risk.kill_dd must be in (0, 1): the kill switch cannot be disabled")
    if not 0 < s.target_vol <= 0.25:
        raise ConfigError("strategy.target_vol must be in (0, 0.25] (daily volatility)")
    if not d.sources:
        raise ConfigError("at least one data source is required")
    from .data.live import SOURCES
    bad = [x for x in d.sources if x not in SOURCES]
    if bad:
        raise ConfigError(f"unknown data sources {bad}; available: {sorted(SOURCES)}")
    if d.catchup_bars < 1 or d.late_fill_minutes < 0:
        raise ConfigError("data.catchup_bars must be >= 1 and data.late_fill_minutes >= 0")
    if r.mode == "live" and not k.allow_short:
        raise ConfigError("live trading supports perpetual swaps only (risk.allow_short = true)")
    if lv.margin_mode not in ("cross", "isolated"):
        raise ConfigError("live.margin_mode must be 'cross' or 'isolated'")
    if lv.leverage < 1 or lv.max_order_notional <= 0:
        raise ConfigError("live.leverage must be >= 1 and live.max_order_notional > 0")
