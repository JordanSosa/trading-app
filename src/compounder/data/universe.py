"""Which perpetuals count as tradable crypto assets.

Exchanges list more than crypto on their USDT-margined perpetual venues:
stablecoins, composite indexes, tokenised equities, ETFs, metals and FX. The
strategy is researched on crypto assets only, so everything else is excluded
by underlying. The universe at any moment is then the top-N remaining symbols
by trailing volume (see :func:`compounder.data.panel.liquidity_rank`).
"""

from __future__ import annotations

# Stablecoins and FX: no volatility to trade.
STABLE = frozenset({"USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "EUR", "GBP", "AUD", "USDBRL", "USDE", "PYUSD"})

# Composite indexes published by the exchange.
INDEX = frozenset({"BTCDOM", "DEFI", "FOOTBALL", "BLUEBIRD"})

# Metals, energy and gold-pegged tokens.
COMMODITY = frozenset({"XAU", "XAG", "XPD", "XPT", "PAXG", "XAUT", "COPPER", "NATGAS", "CL", "BZ"})

# Tokenised equities, ETFs and pre-IPO markets (Binance "TradFi" perpetuals, 2025 onwards).
EQUITY = frozenset({
    "AAOI", "AAPL", "ABBV", "ACN", "ADBE", "AKAM", "ALAB", "AMAT", "AMC", "AMD", "AMGN", "AMZN", "ANET",
    "ANTHROPIC", "APLD", "APP", "ARM", "ASML", "ASTS", "AVGO", "AXTI", "BABA", "BAC", "BILL", "BITO", "BMNR",
    "BRKB", "BYD", "CBRS", "CEG", "CIEN", "COHR", "COIN", "COST", "CRCL", "CRDO", "CRM", "CRML", "CRWD",
    "CRWV", "CSCO", "CSOPSAMSUNG2L", "CSOPSKHYNIX2L", "CVNA", "DDOG", "DELL", "DIS", "DJT", "DKNG", "DRAM",
    "EBAY", "EQT", "EWJ", "EWT", "EWY", "EWZ", "EWZS", "FLNC", "GDX", "GEV", "GIGADEV", "GLW", "GME", "GOOGL",
    "GPRO", "GS", "GTLB", "HANMI", "HIMS", "HK0625", "HK0700", "HK0992", "HK1810", "HOOD", "HPE", "HUT",
    "HYUNDAI", "IBKR", "IBM", "INTC", "IONQ", "IREN", "IWM", "JPM", "KLAC", "KODEX200", "KSTR", "KUAISHOU",
    "LGELECTRONICS", "LITE", "LLY", "LRCX", "MARA", "MCD", "MDB", "MEITUAN", "MELI", "META", "MINIMAX",
    "MRK", "MRNA", "MRVL", "MSFT", "MSTR", "MSTU", "MU", "MUU", "NAVER", "NBIS", "NET", "NFLX", "NKE", "NOK",
    "NOW", "NVDA", "NVDL", "NVO", "OKLO", "ONDS", "OPENAI", "ORCL", "OURA", "PANW", "PATH", "PDD", "PFE",
    "PLTR", "POPMART", "PYPL", "QCOM", "QQQ", "RDDT", "RDW", "RIVN", "RKLB", "RUM", "SAMSUNG", "SAMSUNGEM",
    "SHOP", "SKHY", "SKHYNIX", "SMCI", "SMH", "SMR", "SNDK", "SNOW", "SNPS", "SOFI", "SONY", "SOXL", "SOXS",
    "SPCX", "SPY", "SQQQ", "STM", "STRC", "TEM", "TENCENT", "TER", "TMF", "TQQQ", "TSLA", "TSLL", "TSM",
    "TTWO", "TXN", "TZA", "UBER", "UNH", "UNITREE", "URNM", "USAR", "UVXY", "VKTX", "VRT", "WDC", "WMT",
    "XBI", "XLE", "XOM", "ZHIPU", "ZHONGJI", "ZM", "ZS",
})

NON_CRYPTO = STABLE | INDEX | COMMODITY | EQUITY


def base_asset(symbol: str) -> str:
    """'BTCUSDT' -> 'BTC', 'BTC-USDT-SWAP' -> 'BTC'."""
    s = symbol.upper()
    if s.endswith("-USDT-SWAP"):
        return s[: -len("-USDT-SWAP")]
    if s.endswith("USDT"):
        return s[:-4]
    return s


def is_crypto_perp(symbol: str) -> bool:
    """True for a USDT perpetual on a crypto asset (not a stablecoin, index, equity or commodity)."""
    if "_" in symbol:
        return False  # dated futures
    base = base_asset(symbol)
    return bool(base) and base not in NON_CRYPTO and base.isascii()
