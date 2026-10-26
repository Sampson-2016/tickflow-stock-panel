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
def _clear_closes_cache():
    """清空日线缓存, 防止 FakeRepo 回收后 id 复用导致跨用例串数据。"""
    from app.services.free_realtime import overbought_oversold as _oo

    _oo._closes_cache.clear()
    yield
    _oo._closes_cache.clear()


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

    def test_overbought_list_stopped_symbol_skipped(self, monkeypatch):
        """清单中 enabled=False 的标的，即使随统一行情广播流入也不评估。

        复现用户场景: 超买超卖页停止中际旭创，持仓T页开启同一标的，
        mock/real 广播 df 含该标的 → 超买超卖模块必须跳过，不得推送。
        """
        monkeypatch.setattr(
            fr_config, "list_overbought_symbols",
            lambda: [{
                "symbol": "300308.SZ", "name": "中际旭创", "enabled": False,
                "overbought_pct": None, "oversold_pct": None,
            }],
        )
        repo = FakeRepo({
            "300308.SZ": [10.0, 10.0, 10.0, 10.0],
            "600000.SH": [10.0, 10.0, 10.0, 10.0],
        })
        # last_price=100 → 偏离远超阈值，若不过滤必然触发信号
        df = _df([_row("300308.SZ", 100.0), _row("600000.SH", 12.0)])
        sigs = evaluate(df, repo)
        # 中际旭创被跳过；清单外的 600000.SH（持仓T 流入）用全局默认阈值正常评估
        assert [s.symbol for s in sigs] == ["600000.SH"]

    def test_overbought_list_enabled_symbol_still_evaluated(self, monkeypatch):
        """清单中 enabled=True 的标的阈值评估行为不受影响。"""
        monkeypatch.setattr(
            fr_config, "list_overbought_symbols",
            lambda: [{
                "symbol": "300308.SZ", "name": "中际旭创", "enabled": True,
                "overbought_pct": 3.0, "oversold_pct": None,
            }],
        )
        repo = FakeRepo({"300308.SZ": [10.0, 10.0, 10.0, 10.0]})
        df = _df([_row("300308.SZ", 10.2)])  # ma5=(40+10.2)/5=10.04, 偏离 +1.59% < 3% 不触发
        assert evaluate(df, repo) == []
        df2 = _df([_row("300308.SZ", 10.6)])  # ma5=(40+10.6)/5=10.12, 偏离 +4.74% ≥ 3% 触发
        sigs = evaluate(df2, repo)
        assert [s.symbol for s in sigs] == ["300308.SZ"]
        assert sigs[0].threshold_pct == pytest.approx(3.0)

    def test_module_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fr_config, "get_module_enabled", lambda mod: False)
        repo = FakeRepo({"600000.SH": [10.0, 10.0, 10.0, 10.0]})
        df = _df([_row("600000.SH", 100.0)])
        assert evaluate(df, repo) == []

    def test_empty_df(self):
        repo = FakeRepo({})
        assert evaluate(pl.DataFrame(), repo) == []


class TestLoadRecentClosesCache:
    """_closes_cache TTL 修复回归：日K盘中被更新后，过期缓存必须重读新数据。"""

    def test_stale_cache_invalidated_after_ttl(self):
        from datetime import date as _date
        from datetime import datetime as _datetime

        from app.markets import CN_TZ
        from app.services.free_realtime import overbought_oversold as oo

        class MultiDateRepo:
            """两次 get_daily 返回不同收盘序列，模拟盘中日K被同步更新。"""

            def __init__(self):
                self.calls = 0

            def get_daily(self, symbol, start, end, columns=None):
                self.calls += 1
                closes = [10.0, 10.0, 10.0, 10.0] if self.calls == 1 else [20.0, 20.0, 20.0, 20.0]
                n = len(closes)
                dates = [_date.today() - timedelta(days=n - i) for i in range(n)]
                return pl.DataFrame({"symbol": [symbol] * n, "date": dates, "raw_close": closes})

        repo = MultiDateRepo()
        first = oo._load_recent_closes(repo, "600000.SH", 4)
        assert first == [10.0, 10.0, 10.0, 10.0]
        # 不动时间 → TTL 内命中缓存，不回源
        assert oo._load_recent_closes(repo, "600000.SH", 4) == first
        assert repo.calls == 1
        # 把缓存时间戳回拨超过 TTL（10 分钟）→ 下次调用回源读到新日K
        key = (id(repo), "600000.SH")
        old = oo._closes_cache[key]
        oo._closes_cache[key] = (old[0], old[1], old[2], _datetime.now(CN_TZ).timestamp() - 601)
        second = oo._load_recent_closes(repo, "600000.SH", 4)
        assert second == [20.0, 20.0, 20.0, 20.0], "TTL 过期后必须重读更新后的日K"
        assert repo.calls == 2
