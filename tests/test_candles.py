import numpy as np
import pandas as pd
import pytest

from tradebot.data.candles import CandleError, make_frame, resample_causal, split_closed, validate

H = 3_600_000


def test_validate_rejects_bad_ohlc_and_reports_gaps(candles):
    assert validate(candles, interval_ms=H) == []
    gappy = candles.drop(candles.index[[10, 11, 50]])
    warns = validate(gappy, interval_ms=H)
    assert warns and "3 missing" in warns[0]
    bad = candles.copy()
    bad.iloc[5, bad.columns.get_loc("high")] = bad.iloc[5]["low"] - 1
    with pytest.raises(CandleError):
        validate(bad, interval_ms=H)
    neg = candles.copy()
    neg.iloc[3, 0] = -1
    with pytest.raises(CandleError):
        validate(neg)


def test_split_closed_separates_forming_bar(candles):
    t5 = int(candles.index[5].value // 1_000_000)
    closed, forming = split_closed(candles.iloc[:10], t5 + 10 * 60_000, H)
    assert len(closed) == 5 and forming is not None and forming.name == candles.index[5]
    closed, forming = split_closed(candles.iloc[:10], t5 + H, H)
    assert len(closed) == 6


def test_make_frame_dedupes_and_sorts():
    rows = [(2 * H, 1, 2, 0.5, 1.5, 1), (H, 1, 2, 0.5, 1.5, 1), (2 * H, 1, 3, 0.5, 2.5, 1)]
    df = make_frame(rows)
    assert list(df.index.view("int64") // 1_000_000) == [H, 2 * H]
    assert df.iloc[-1]["close"] == 2.5


def test_resample_causal_never_exposes_forming_bar(candles):
    htf = resample_causal(candles, "4h", H)
    # 2021-01-01 00:00 start: first 4h bar [00:00, 04:00) is visible on the 03:00 bar (closes 04:00)
    assert htf.iloc[:3]["close"].isna().all()
    assert htf.iloc[3]["close"] == pytest.approx(candles.iloc[3]["close"])
    assert htf.iloc[3]["high"] == pytest.approx(candles.iloc[:4]["high"].max())
    assert htf.iloc[6]["close"] == pytest.approx(candles.iloc[3]["close"])  # still the old bar
    assert htf.iloc[7]["close"] == pytest.approx(candles.iloc[7]["close"])
    # prefix property: computing on a prefix gives the same last row
    for k in (17, 250, 1001, 3333):
        part = resample_causal(candles.iloc[:k], "4h", H)
        a, b = part.iloc[-1].to_numpy(), htf.iloc[k - 1].to_numpy()
        assert np.allclose(a, b, equal_nan=True)
    daily = resample_causal(candles, "1D", H)
    assert daily.iloc[:23]["close"].isna().all() and not np.isnan(daily.iloc[23]["close"])
