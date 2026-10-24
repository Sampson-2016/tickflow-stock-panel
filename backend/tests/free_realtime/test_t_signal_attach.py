"""Tests for app.custom.easyquotation_t_signal - 统一行情模块接入路径。

验证 on_realtime_df 在 free_realtime.position_t 模块开启时正确处理
统一调度器推送的标准化 DataFrame。
"""
from __future__ import annotations

import time
from datetime import date

import polars as pl

from app.custom import easyquotation_t_signal as ts_mod
from app.data_providers.free_realtime.schemas import FREE_REALTIME_SCHEMA


def _row(symbol="600000.SH", last_price=10.0, ts_ms=None):
    ts_ms = ts_ms or int(time.time() * 1000)
    return {
        "symbol": symbol, "name": "测试股", "asset_type": "stock",
        "last_price": last_price, "open": last_price, "prev_close": last_price,
        "high": last_price, "low": last_price, "change_pct": 0.0,
        "volume": 0.0, "amount": 0.0,
        "bid1_price": 0.0, "ask1_price": 0.0, "bid1_volume": 0.0, "ask1_volume": 0.0,
        "source": "sina", "ts_ms": ts_ms, "trade_date": date.today(),
    }


def _df(rows):
    return pl.DataFrame(rows, schema=FREE_REALTIME_SCHEMA)


class TestOnRealtimeDf:
    def test_empty_df_noop(self):
        ts_mod.POLLER.on_realtime_df(pl.DataFrame())

    def test_module_disabled_skipped(self, monkeypatch):
        """free_realtime.position_t 关闭时直接 return。"""
        from app.services.free_realtime import config as fr_config
        monkeypatch.setattr(fr_config, "get_module_enabled", lambda m: False)

        # 即便 enabled 列表有标的，也不应触发评估
        called = {"v": False}

        def _bad_eval(*_args, **_kwargs):
            called["v"] = True

        monkeypatch.setattr(ts_mod.POLLER._evaluator, "evaluate", _bad_eval)
        # 清空持仓T列表避免 enabled_symbols 返回非空
        monkeypatch.setattr(
            ts_mod.POLLER._positions, "list_enabled_symbols", lambda: ["600000.SH"]
        )
        ts_mod.POLLER.on_realtime_df(_df([_row()]))
        assert called["v"] is False

    def test_skips_symbols_not_in_positions(self, monkeypatch):
        from app.services.free_realtime import config as fr_config
        monkeypatch.setattr(fr_config, "get_module_enabled", lambda m: m == "position_t")
        monkeypatch.setattr(ts_mod.POLLER._positions, "list_enabled_symbols", lambda: ["000001.SZ"])

        seen = {"symbols": []}
        original = ts_mod.POLLER._evaluator.evaluate

        def _spy(quotes, cfg, now_ms):
            seen["symbols"] = list(quotes.keys())
            return original(quotes, cfg, now_ms)

        monkeypatch.setattr(ts_mod.POLLER._evaluator, "evaluate", _spy)
        ts_mod.POLLER.on_realtime_df(_df([_row("600000.SH")]))
        assert seen["symbols"] == []  # 600000.SH 不在 enabled 列表

    def test_marks_tick_result_for_enabled(self, monkeypatch):
        from app.services.free_realtime import config as fr_config
        monkeypatch.setattr(fr_config, "get_module_enabled", lambda m: m == "position_t")
        monkeypatch.setattr(ts_mod.POLLER._positions, "list_enabled_symbols", lambda: ["600000.SH"])

        seen = {"succeeded": set()}
        def _mark(requested, succeeded, ts_ms):
            seen["succeeded"] = set(succeeded)
        monkeypatch.setattr(ts_mod.POLLER._positions, "mark_tick_result", _mark)
        ts_mod.POLLER.on_realtime_df(_df([_row("600000.SH")]))
        assert seen["succeeded"] == {"600000.SH"}
