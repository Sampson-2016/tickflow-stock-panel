"""Tests for app.services.free_realtime.overbought_oversold."""
from __future__ import annotations

import time
from datetime import date, timedelta

import polars as pl
import pytest

from app.data_providers.free_realtime.schemas import FREE_REALTIME_COLUMNS
from app.services.free_realtime import config as fr_config
from app.services.free_realtime.overbought_oversold import (
    evaluate,
    is_a_stock_candidate,
)


class FakeRepo:
    """最小化 KlineRepository 替身：仅返回固定的 raw_close 列。"""

    def __init__(self, closes_by_symbol: dict[str, list[float]]) -> None:
        self._closes = closes_by_symbol

    def get_daily(self, symbol, start, end, columns=None):
        del start, end, columns  # 接口要求签名，但 stub 仅用 symbol
        closes = self._closes.get(symbol, [])
        if not closes:
            return pl.DataFrame()
        n = len(closes)
        dates = [date.today() - timedelta(days=n - i) for i in range(n)]
        return pl.DataFrame({
            "symbol": [symbol] * n,
            "date": dates,
            "raw_close": closes,
        })


def _row(symbol, last_price, ts_ms=None):
    ts_ms = ts_ms or int(time.time() * 1000)
    return {
        "symbol": symbol,
        "name": symbol,
        "asset_type": "stock",
        "last_price": last_price,
        "open": last_price,
        "prev_close": last_price,
        "high": last_price,
        "low": last_price,
        "change_pct": 0.0,
        "volume": 0.0,
        "amount": 0.0,
        "bid1_price": 0.0,
        "ask1_price": 0.0,
        "bid1_volume": 0.0,
        "ask1_volume": 0.0,
        "source": "sina",
        "ts_ms": ts_ms,
        "trade_date": date.today(),
    }


def _df(rows):
    from app.data_providers.free_realtime.schemas import FREE_REALTIME_SCHEMA
    return pl.DataFrame(rows, schema=FREE_REALTIME_SCHEMA).select(FREE_REALTIME_COLUMNS)


@pytest.fixture(autouse=True)
def _enable_module(monkeypatch):
    """强制开启超买超卖模块，避免被默认关闭拦截。"""
    monkeypatch.setattr(
        fr_config, "get_module_enabled", lambda mod: mod == "overbought_oversold"
    )
    monkeypatch.setattr(fr_config, "list_disabled_symbols", lambda mod: [])
    monkeypatch.setattr(fr_config, "get_overbought_pct", lambda: 5.0)
    monkeypatch.setattr(fr_config, "get_oversold_pct", lambda: 5.0)


class TestIsAStockCandidate:
    def test_stock_passes(self):
        assert is_a_stock_candidate("600000.SH")
        assert is_a_stock_candidate("000001.SZ")
        assert is_a_stock_candidate("688981.SH")

    def test_etf_fails(self):
        # ETF 资产类型不为 stock
        assert not is_a_stock_candidate("510300.SH")
        assert not is_a_stock_candidate("159915.SZ")

    def test_bad_format(self):
        assert not is_a_stock_candidate("600000")


class TestEvaluate:
    def test_overbought_triggers(self):
        """last_price 远高于 ma5 触发超买。"""
        # 4 个交易日 raw_close=10，今日 last_price=12 → ma5=(10*4+12)/5=10.4，偏离 +15.4%
        repo = FakeRepo({"600000.SH": [10.0, 10.0, 10.0, 10.0]})
        df = _df([_row("600000.SH", 12.0)])
        sigs = evaluate(df, repo)
        assert len(sigs) == 1
        assert sigs[0].direction == "overbought"
        assert sigs[0].ma5 == pytest.approx(10.4)

    def test_oversold_triggers(self):
        repo = FakeRepo({"600000.SH": [10.0, 10.0, 10.0, 10.0]})
        df = _df([_row("600000.SH", 8.0)])
        sigs = evaluate(df, repo)
        assert len(sigs) == 1
        assert sigs[0].direction == "oversold"

    def test_within_band_no_signal(self):
        repo = FakeRepo({"600000.SH": [10.0, 10.0, 10.0, 10.0]})
        df = _df([_row("600000.SH", 10.1)])  # 偏离 +0.4%
        assert evaluate(df, repo) == []

    def test_insufficient_history_skipped(self):
        """少于 4 个交易日 raw_close 时跳过。"""
        repo = FakeRepo({"600000.SH": [10.0, 10.0, 10.0]})  # 仅 3 日
        df = _df([_row("600000.SH", 100.0)])
        assert evaluate(df, repo) == []

    def test_etf_excluded(self):
        repo = FakeRepo({"510300.SH": [10.0, 10.0, 10.0, 10.0]})
        df = _df([_row("510300.SH", 100.0)])
        assert evaluate(df, repo) == []

    def test_disabled_symbol_skipped(self, monkeypatch):
        monkeypatch.setattr(
            fr_config, "list_disabled_symbols",
            lambda mod: ["600000.SH"] if mod == "overbought_oversold" else [],
        )
        repo = FakeRepo({"600000.SH": [10.0, 10.0, 10.0, 10.0]})
        df = _df([_row("600000.SH", 100.0)])
        assert evaluate(df, repo) == []

    def test_module_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fr_config, "get_module_enabled", lambda mod: False)
        repo = FakeRepo({"600000.SH": [10.0, 10.0, 10.0, 10.0]})
        df = _df([_row("600000.SH", 100.0)])
        assert evaluate(df, repo) == []

    def test_empty_df(self):
        repo = FakeRepo({})
        assert evaluate(pl.DataFrame(), repo) == []
