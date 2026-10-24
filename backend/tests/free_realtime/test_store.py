"""Tests for app.services.free_realtime.store."""
from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from app.data_providers.free_realtime.schemas import FREE_REALTIME_COLUMNS
from app.services.free_realtime.store import RealtimeStore


def _make_row(
    symbol: str = "600000.SH",
    ts_ms: int | None = None,
    last_price: float = 10.0,
) -> dict:
    ts_ms = ts_ms or int(time.time() * 1000)
    return {
        "symbol": symbol,
        "name": "测试",
        "asset_type": "stock",
        "last_price": last_price,
        "open": 9.5,
        "prev_close": 9.8,
        "high": 10.2,
        "low": 9.4,
        "change_pct": 0.0204,
        "volume": 1000.0,
        "amount": 10000.0,
        "bid1_price": 9.99,
        "ask1_price": 10.01,
        "bid1_volume": 50.0,
        "ask1_volume": 60.0,
        "source": "sina",
        "ts_ms": ts_ms,
        "trade_date": date.today(),
    }


def _df(rows: list[dict]) -> pl.DataFrame:
    from app.data_providers.free_realtime.schemas import FREE_REALTIME_SCHEMA
    return pl.DataFrame(rows, schema=FREE_REALTIME_SCHEMA).select(FREE_REALTIME_COLUMNS)


class TestRealtimeStore:
    def test_append_and_read_today(self, tmp_path: Path):
        store = RealtimeStore(tmp_path)
        store.append(_df([_make_row(ts_ms=1_700_000_000_000)]), kind="real")
        df = store.list_today("600000.SH", kind="real")
        assert df.height == 1
        assert df.row(0, named=True)["last_price"] == 10.0

    def test_append_empty_is_noop(self, tmp_path: Path):
        store = RealtimeStore(tmp_path)
        store.append(pl.DataFrame(schema={
            c: pl.Utf8 for c in FREE_REALTIME_COLUMNS
        }), kind="real")
        assert store.list_today("600000.SH").is_empty()

    def test_unknown_kind_dropped(self, tmp_path: Path):
        store = RealtimeStore(tmp_path)
        store.append(_df([_make_row()]), kind="invalid")
        assert store.list_today("600000.SH", kind="invalid").is_empty()

    def test_same_minute_overwrites(self, tmp_path: Path):
        """同分钟内 ts_ms 落到同一分钟桶，最后一条覆盖前一条。"""
        store = RealtimeStore(tmp_path)
        base = 1_700_000_000_000  # 整分钟起点
        store.append(_df([_make_row(ts_ms=base, last_price=10.0)]), kind="real")
        store.append(_df([_make_row(ts_ms=base + 30_000, last_price=11.0)]), kind="real")
        df = store.list_today("600000.SH")
        assert df.height == 1
        assert df.row(0, named=True)["last_price"] == 11.0

    def test_different_minutes_append(self, tmp_path: Path):
        store = RealtimeStore(tmp_path)
        base = 1_700_000_000_000
        store.append(_df([_make_row(ts_ms=base, last_price=10.0)]), kind="real")
        # 同标的不同分钟应作为新点
        store.append(_df([_make_row(ts_ms=base + 90_000, last_price=11.0)]), kind="real")
        df = store.list_today("600000.SH")
        assert df.height == 2

    def test_list_day_merges_disk_and_memory(self, tmp_path: Path):
        store = RealtimeStore(tmp_path)
        today = date.today()
        base = int(time.time() * 1000)
        # 落盘到磁盘
        store.append(_df([_make_row(ts_ms=base, last_price=10.0)]), kind="real")
        # 再追加内存点（不同分钟）
        store.append(_df([_make_row(ts_ms=base + 90_000, last_price=11.0)]), kind="real")
        df = store.list_day("600000.SH", today)
        assert df.height == 2
        assert df["last_price"].to_list() == [10.0, 11.0]

    def test_prune_old_dirs(self, tmp_path: Path):
        store = RealtimeStore(tmp_path, retention_days=2)
        old_date = date.today() - timedelta(days=10)
        # 手动创建老日期目录 + 占位文件
        d = tmp_path / "user_data" / "free_realtime" / "real" / str(old_date)
        d.mkdir(parents=True)
        (d / "600000.SH.parquet").write_bytes(b"")
        removed = store.prune()
        assert removed >= 1
        assert not d.exists()

    def test_clear_kind(self, tmp_path: Path):
        store = RealtimeStore(tmp_path)
        store.append(_df([_make_row()]), kind="mock")
        removed = store.clear_kind("mock")
        assert removed >= 1
        assert store.list_today("600000.SH", kind="mock").is_empty()

    def test_clear_kind_invalid(self, tmp_path: Path):
        store = RealtimeStore(tmp_path)
        assert store.clear_kind("invalid") == 0

    def test_mock_real_isolation(self, tmp_path: Path):
        """mock 与 real 落盘目录隔离：互不污染。"""
        store = RealtimeStore(tmp_path)
        store.append(_df([_make_row(ts_ms=1, last_price=10.0)]), kind="real")
        store.append(_df([_make_row(ts_ms=1, last_price=20.0)]), kind="mock")
        real_df = store.list_today("600000.SH", kind="real")
        mock_df = store.list_today("600000.SH", kind="mock")
        assert real_df.row(0, named=True)["last_price"] == 10.0
        assert mock_df.row(0, named=True)["last_price"] == 20.0
