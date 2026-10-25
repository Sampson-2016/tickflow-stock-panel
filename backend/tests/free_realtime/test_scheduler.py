"""Tests for app.services.free_realtime.scheduler (CircuitBreaker + SymbolCollector)."""
from __future__ import annotations

import time
from datetime import datetime

from app.services.free_realtime.scheduler import (
    CircuitBreaker,
    FreeRealtimeScheduler,
    SymbolCollector,
    is_capture_now,
)


class TestCaptureSession:
    """is_capture_now 覆盖 9:15-11:30 + 13:00-15:00（含集合竞价）。"""

    def _dt(self, h: int, m: int) -> datetime:
        return datetime(2026, 10, 23, h, m)  # 周五 (工作日)

    def test_before_open_returns_false(self):
        # 9:15 前（含 9:14）不抓
        assert is_capture_now(self._dt(9, 14)) is False
        assert is_capture_now(self._dt(0, 0)) is False

    def test_pre_open_call_auction_captures(self):
        # 9:15-9:30 早盘集合竞价，价格会变化，需抓
        assert is_capture_now(self._dt(9, 15)) is True
        assert is_capture_now(self._dt(9, 20)) is True
        assert is_capture_now(self._dt(9, 29)) is True

    def test_morning_session_captures(self):
        # 9:30-11:30 上午连续竞价
        assert is_capture_now(self._dt(9, 30)) is True
        assert is_capture_now(self._dt(10, 0)) is True
        assert is_capture_now(self._dt(11, 29)) is True

    def test_lunch_break_skipped(self):
        # 11:30-13:00 午休时段不抓
        assert is_capture_now(self._dt(11, 30)) is False
        assert is_capture_now(self._dt(12, 0)) is False
        assert is_capture_now(self._dt(12, 59)) is False

    def test_afternoon_session_captures(self):
        # 13:00-15:00 下午 + 收盘集合竞价
        assert is_capture_now(self._dt(13, 0)) is True
        assert is_capture_now(self._dt(14, 50)) is True
        assert is_capture_now(self._dt(14, 59)) is True

    def test_after_close_skipped(self):
        # 15:00 后竞价价格不再变化，不抓
        assert is_capture_now(self._dt(15, 0)) is False
        assert is_capture_now(self._dt(15, 30)) is False
        assert is_capture_now(self._dt(23, 0)) is False


class TestCircuitBreaker:
    def test_success_resets_fails(self):
        b = CircuitBreaker(threshold=3, cooldown_sec=60)
        b.mark_fail("sina")
        b.mark_fail("sina")
        b.mark_success("sina")
        # 成功清零后需重新累 3 次才熔断
        assert not b.mark_fail("sina")

    def test_triggers_break_on_threshold(self):
        b = CircuitBreaker(threshold=3, cooldown_sec=60)
        assert not b.mark_fail("sina")
        assert not b.mark_fail("sina")
        assert b.mark_fail("sina")  # 第 3 次触发熔断
        assert b.is_broken("sina")

    def test_other_source_unaffected(self):
        b = CircuitBreaker(threshold=3, cooldown_sec=60)
        for _ in range(3):
            b.mark_fail("sina")
        assert not b.is_broken("tencent")

    def test_cooldown_releases(self):
        b = CircuitBreaker(threshold=1, cooldown_sec=0)  # 0 秒即立刻释放
        b.mark_fail("sina")
        # cooldown=0 时下次 is_broken 应认为已过冷却期
        # 留出极小时间让 epoch 推进
        time.sleep(0.01)
        assert not b.is_broken("sina")


class TestSymbolCollector:
    def test_collect_dedupes(self):
        c = SymbolCollector()
        c.register(lambda: ["600000.SH", "000001.SZ"])
        c.register(lambda: ["600000.SH", "300750.SZ"])
        out = c.collect()
        assert out == ["600000.SH", "000001.SZ", "300750.SZ"]

    def test_collect_uppercase_and_skip_empty(self):
        c = SymbolCollector()
        c.register(lambda: ["600000.sh", "", "  000001.SZ  "])
        out = c.collect()
        assert out == ["600000.SH", "000001.SZ"]

    def test_failed_fetcher_isolated(self):
        def bad():
            raise RuntimeError("boom")
        c = SymbolCollector()
        c.register(bad)
        c.register(lambda: ["600000.SH"])
        assert c.collect() == ["600000.SH"]

    def test_max_batch_limit(self):
        """超过 50 只标的截断（外部接口保护）。"""
        c = SymbolCollector()
        c.register(lambda: [f"{i:06d}.SH" for i in range(100)])
        assert len(c.collect()) == 50


class TestSchedulerSingleton:
    def test_get_instance_returns_same(self):
        a = FreeRealtimeScheduler.get_instance()
        b = FreeRealtimeScheduler.get_instance()
        assert a is b

    def test_status_returns_dict(self):
        s = FreeRealtimeScheduler.get_instance()
        st = s.status()
        assert isinstance(st, dict)
        assert "running" in st and "enabled" in st and "breaker_stats" in st

    def test_start_when_disabled_returns_error(self):
        s = FreeRealtimeScheduler.get_instance()
        # 全局开关默认 False
        try:
            from app.services.free_realtime import config as fr_config
            fr_config.set_global_enabled(False)
        except Exception:
            pass
        result = s.start()
        assert result["ok"] is False
