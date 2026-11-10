"""Live smoke tests against the public exchange APIs (deselect with -m "not network")."""

from __future__ import annotations

import dataclasses
import time

import numpy as np
import pytest

from compounder.config import load_config
from compounder.data.live import GateData, HOUR_MS, OkxData
from compounder.forward import tick
from compounder.state import StateStore

pytestmark = pytest.mark.network


@pytest.mark.parametrize("cls", [OkxData, GateData])
def test_source_serves_btc(cls):
    src = cls()
    inst = src.instruments()
    assert "BTCUSDT" in inst and "ETHUSDT" in inst
    c = src.candles(inst["BTCUSDT"], 400)
    assert len(c) == 400 and (np.diff(c[:, 0]) == HOUR_MS).all()
    assert time.time() * 1000 - c[-1, 0] < 2 * HOUR_MS
    assert (c[:, 3] > 0).all() and (c[:, 2] >= c[:, 3]).all()
    assert len(src.funding(inst["BTCUSDT"])) > 10
    assert src.last_prices()["BTCUSDT"] > 0


def test_paper_tick_end_to_end(tmp_path):
    cfg = load_config(None)
    cfg = dataclasses.replace(cfg, run=dataclasses.replace(cfg.run, state_dir=str(tmp_path / "state")))
    store = StateStore(cfg.run.state_dir)
    first = tick(cfg, store)
    assert first.ok and first.status == "running"
    again = tick(cfg, store)
    assert again.ok and again.bars_processed <= 1
    doc = store.load()
    assert len(doc["universe"]["history"][-1]["ranks"]) >= 20
    assert (store.root / "STATUS.md").exists()
