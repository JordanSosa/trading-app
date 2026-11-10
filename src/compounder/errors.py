"""Exceptions shared between modules."""

from __future__ import annotations


class BrokerError(RuntimeError):
    """The exchange account cannot be read, or is in a state the bot must not trade."""
