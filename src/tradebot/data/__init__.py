"""Market data: candle schema, live providers and historical archives."""

from .candles import COLUMNS, CandleError, resample_causal, split_closed, validate
from .providers import PROVIDERS, FetchResult, HttpClient, MarketData, ProviderError

__all__ = [
    "COLUMNS",
    "CandleError",
    "FetchResult",
    "HttpClient",
    "MarketData",
    "PROVIDERS",
    "ProviderError",
    "resample_causal",
    "split_closed",
    "validate",
]
