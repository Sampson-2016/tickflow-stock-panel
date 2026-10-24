"""Tests for app.services.free_realtime.scheduler (CircuitBreaker + SymbolCollector)."""
from __future__ import annotations

import time

from app.services.free_realtime.scheduler import (
    CircuitBreaker,
    FreeRealtimeScheduler,
    SymbolCollector,
)


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
