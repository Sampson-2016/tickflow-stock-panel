"""回归测试: A 股日K pipeline 的 batch 拉取 end_date 必须是 today+1 day。

根因: K 线 API 是 [start, end) 半开区间; 若 end=today 0:00, 今天 15:00 收盘那根
K 线时间戳 > end_time, 永远不在请求窗口内, 导致任何"今天还没拉到"场景下管道
都漏写今天那一行。修法是 end_date = today + 1 day at 0:00, 让半开区间盖到
today 23:59:59 的最后一根日K。

本测试覆盖 daily_pipeline.run_now 里走 batch 拉取的所有分支 (gap fill /
initial fetch / override), 并断言 end_date 必须是 today+1 day 的 0:00:00。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.market_time import CN_TZ
from app.tickflow.repository import DataStore, KlineRepository


def _write_daily_partition(root, day: date) -> None:
    """写一个最小 kline_daily 分区, 模拟本地已存在某交易日 K 线。"""
    part = root / "kline_daily" / f"date={day.isoformat()}"
    part.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": ["600001.SH", "600002.SH"],
        "date": [day, day],
        "open": [10.0, 20.0], "high": [10.1, 20.1],
        "low": [9.9, 19.9], "close": [10.0, 20.0],
        "volume": [100.0, 200.0], "amount": [1000.0, 4000.0],
        "quote_ts": [None, None],
    }).write_parquet(part / "part.parquet")


def _capture_batch(monkeypatch, instrument_sync, kline_sync, capset):
    """离线化: 捕获每次 sync_and_persist_daily_batch 的 start_date / end_date。"""
    monkeypatch.setattr(instrument_sync, "sync_instruments", lambda data_dir: 0)

    batch_calls: list[dict] = []

    def _fake_batch(universe, repo, capset_arg, start_date=None, end_date=None,
                    on_chunk_done=None, **_):
        batch_calls.append({
            "start": start_date.date() if hasattr(start_date, "date") else start_date,
            "end": end_date.date() if hasattr(end_date, "date") else end_date,
            "end_time": end_date.time() if hasattr(end_date, "time") else None,
        })
        return 0

    monkeypatch.setattr(kline_sync, "sync_and_persist_daily_batch", _fake_batch)
    return batch_calls


def test_gap_fill_branch_uses_today_plus_one(tmp_path, monkeypatch):
    """分支 3: 本地最新日 < today → 补缺口 batch 拉取。end 必须是 today+1。"""
    from app.config import settings as app_settings
    from app.jobs import daily_pipeline
    from app.services import instrument_sync, kline_sync

    today = datetime.now(CN_TZ).date()
    yesterday = today - timedelta(days=1)
    _write_daily_partition(tmp_path, yesterday)

    monkeypatch.setattr(app_settings, "data_dir", tmp_path)
    batch_calls = _capture_batch(monkeypatch, instrument_sync, kline_sync, None)

    repo = KlineRepository(DataStore(tmp_path))
    # 强制走分支 3 (有历史但无 today): 不开 QUOTE_POOL + latest < today。
    capset = SimpleNamespace(has=lambda key: False)

    daily_pipeline.run_now(repo, capset)  # type: ignore[arg-type]

    assert batch_calls, "应该至少调用一次 batch 拉取"
    call = batch_calls[0]
    assert call["end"] == today + timedelta(days=1), (
        f"end_date 必须是 today+1 才能让 [start, end) 盖到 today 23:59:59 的日K, "
        f"实际是 {call['end']} (today={today})"
    )
    assert call["end_time"] == time(0, 0, 0), (
        f"end_time 应为 0:00:00, 实际是 {call['end_time']}"
    )


def test_initial_fetch_branch_uses_today_plus_one(tmp_path, monkeypatch):
    """分支 4: 本地无任何日K → 首次拉 1 年。end 同样必须是 today+1。"""
    from app.config import settings as app_settings
    from app.jobs import daily_pipeline
    from app.services import instrument_sync, kline_sync

    today = datetime.now(CN_TZ).date()
    # 不写任何 kline_daily 分区, 触发首次拉取分支

    monkeypatch.setattr(app_settings, "data_dir", tmp_path)
    batch_calls = _capture_batch(monkeypatch, instrument_sync, kline_sync, None)

    repo = KlineRepository(DataStore(tmp_path))
    capset = SimpleNamespace(has=lambda key: False)

    daily_pipeline.run_now(repo, capset)  # type: ignore[arg-type]

    assert batch_calls, "首次拉取应该调用一次 batch"
    call = batch_calls[0]
    assert call["end"] == today + timedelta(days=1)
    assert call["start"] == today - timedelta(days=365)
    assert call["end_time"] == time(0, 0, 0)


def test_override_branch_uses_today_plus_one(tmp_path, monkeypatch):
    """override_start_date 模式 (数据修正): end 同样必须是 today+1。"""
    from app.config import settings as app_settings
    from app.jobs import daily_pipeline
    from app.services import instrument_sync, kline_sync

    today = datetime.now(CN_TZ).date()
    override_start = today - timedelta(days=3)
    _write_daily_partition(tmp_path, override_start)

    monkeypatch.setattr(app_settings, "data_dir", tmp_path)
    batch_calls = _capture_batch(monkeypatch, instrument_sync, kline_sync, None)

    repo = KlineRepository(DataStore(tmp_path))
    capset = SimpleNamespace(has=lambda key: False)

    daily_pipeline.run_now(repo, capset, override_start_date=override_start)  # type: ignore[arg-type]

    assert batch_calls, "override 应该调用一次 batch"
    call = batch_calls[0]
    assert call["end"] == today + timedelta(days=1)
    assert call["start"] == override_start
