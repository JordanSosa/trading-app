"""Strategy registry.

Strategy modules in this package register themselves with ``@register``.
Every module is imported automatically, so adding a strategy means adding a
file; nothing else needs editing.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from .base import Strategy

REGISTRY: dict[str, type[Strategy]] = {}
_discovered = False


def register(cls: type[Strategy]) -> type[Strategy]:
    if cls.name in REGISTRY and REGISTRY[cls.name] is not cls:
        raise ValueError(f"duplicate strategy name: {cls.name}")
    REGISTRY[cls.name] = cls
    return cls


def _discover() -> None:
    global _discovered
    if _discovered:
        return
    _discovered = True
    for mod in pkgutil.iter_modules(__path__):
        if mod.name not in ("base",) and not mod.name.startswith("_"):
            importlib.import_module(f"{__name__}.{mod.name}")


def get_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    _discover()
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name](**(params or {}))


def available() -> list[str]:
    _discover()
    return sorted(REGISTRY)


__all__ = ["REGISTRY", "Strategy", "available", "get_strategy", "register"]
