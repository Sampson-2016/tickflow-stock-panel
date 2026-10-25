"""免费实时行情调度器 — 多源交替轮询 + 随机起点 + 连续失败熔断。

设计要点 (遵循 CONTRIBUTING.md §6.2 并发与 §5.2 监控要求):
  - 后台线程按用户配置的 interval 轮询；多源勾选时交替使用，每轮选一个源
  - 起始 sleep 随机（避免整数秒对齐外部接口）
  - 单源连续失败 N 次熔断，自动从轮询池摘除，复用 review_push_channels 推送告警
  - 拉取符号仅来自持仓T / 超买超卖 / 自选股 三个模块中已开启的标的（去重）
  - 失败 fail-closed：单源整批失败仅记录，不影响其他源；整批成功才落盘
"""
from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from datetime import datetime, time as dt_time
from typing import ClassVar

from app.config import settings
from app.data_providers.free_realtime.registry import get_provider
from app.data_providers.free_realtime.schemas import empty_realtime_df
from app.markets import CN_TZ
from app.services.free_realtime import config as fr_config
from app.services.free_realtime.store import RealtimeStore

logger = logging.getLogger(__name__)

# 单标的最多 50 只（避免外部接口被批量拉死）
_MAX_BATCH_SYMBOLS = 50

# A 股行情抓取时段（北京时间）
# - 9:15-9:30 早盘集合竞价（虚拟参考价会变化，需抓）
# - 9:30-11:30 上午连续竞价
# - 13:00-15:00 下午连续竞价 + 14:57-15:00 收盘集合竞价
# 3 点后竞价价格不再变化，停止抓取节省配额。
_CAPTURE_SESSIONS = (
    (dt_time(9, 15), dt_time(11, 30)),
    (dt_time(13, 0), dt_time(15, 0)),
)


def is_capture_now(dt: datetime | None = None) -> bool:
    """当前是否处于 A 股免费行情抓取时段（9:15-11:30 + 13:00-15:00，含集合竞价）。"""
    t = (dt or datetime.now(CN_TZ)).time()
    for start, end in _CAPTURE_SESSIONS:
        if start <= t < end:
            return True
    return False


class CircuitBreaker:
    """每源独立熔断器：连续失败 N 次后熔断。

    成功一次即清零；熔断后冷却期可恢复尝试。
    """

    def __init__(self, threshold: int = fr_config.FAIL_BREAK_THRESHOLD, cooldown_sec: int = 300) -> None:
        self._threshold = max(1, int(threshold))
        self._cooldown_sec = max(0, int(cooldown_sec))
        self._fails: dict[str, int] = {}    # source -> consecutive fails
        self._broken_until: dict[str, float] = {}  # source -> epoch seconds
        self._lock = threading.Lock()

    def is_broken(self, source: str) -> bool:
        with self._lock:
            until = self._broken_until.get(source, 0.0)
            if until and time.time() < until:
                return True
            if until and time.time() >= until:
                # 冷却期结束：允许尝试，清零计数
                self._broken_until.pop(source, None)
                self._fails.pop(source, None)
            return False

    def mark_success(self, source: str) -> None:
        with self._lock:
            self._fails.pop(source, None)
            self._broken_until.pop(source, None)

    def mark_fail(self, source: str) -> bool:
        """记录一次失败；返回是否触发熔断。"""
        with self._lock:
            cur = self._fails.get(source, 0) + 1
            self._fails[source] = cur
            if cur >= self._threshold:
                self._broken_until[source] = time.time() + self._cooldown_sec
                return True
            return False

    def stats(self) -> dict:
        with self._lock:
            return {
                "fails": dict(self._fails),
                "broken_until": dict(self._broken_until),
            }


class SymbolCollector:
    """从三个模块收集 enabled symbols 的去重集合。

    每轮调度开始前调用，避免持有过期的 symbol 列表。
    """

    def __init__(self) -> None:
        self._fetchers: list[Callable[[], list[str]]] = []

    def register(self, fetcher: Callable[[], list[str]]) -> None:
        self._fetchers.append(fetcher)

    def collect(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for fn in self._fetchers:
            try:
                syms = fn() or []
            except Exception as e:
                logger.warning("symbol collector fetcher %s failed: %s", fn, e)
                continue
            for s in syms:
                s = str(s or "").strip().upper()
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
        return out[:_MAX_BATCH_SYMBOLS]


class FreeRealtimeScheduler:
    """单例调度器。线程安全；启动后一个后台线程跑完整循环。

    Usage:
        scheduler = FreeRealtimeScheduler.get_instance()
        scheduler.start()
    """

    _instance: FreeRealtimeScheduler | None = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> FreeRealtimeScheduler:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._store = RealtimeStore(
            settings.data_dir, retention_days=fr_config.get_retention_days()
        )
        self._breakers = CircuitBreaker()
        self._collector = SymbolCollector()
        # 轮询顺序的 round-robin 指针
        self._source_idx = 0
        # 最近一次拉取诊断
        self._last_tick_ms: float = 0.0
        self._last_error: str = ""
        self._last_symbol_count: int = 0
        self._last_source: str = ""
        self._alerted_sources: set[str] = set()  # 已告警过熔断的源（避免重复推送）

    # ── 生命周期 ─────────────────────────────────────────
    def status(self) -> dict:
        with self._lock:
            running = self._running
        return {
            "running": running,
            "enabled": fr_config.get_global_enabled(),
            "interval_sec": fr_config.get_interval_sec(),
            "sources": fr_config.get_sources(),
            "trading_hours_only": fr_config.get_trading_hours_only(),
            "last_tick_ms": round(self._last_tick_ms, 0),
            "last_error": self._last_error,
            "last_symbol_count": self._last_symbol_count,
            "last_source": self._last_source,
            "breaker_stats": self._breakers.stats(),
            "store_stats": self._store.stats(),
            "modules": fr_config.snapshot()["modules"],
        }

    def register_collector(self, fetcher: Callable[[], list[str]]) -> None:
        """外部模块（持仓T/超买超卖/自选股）注册 symbol 采集回调。"""
        self._collector.register(fetcher)

    def store(self) -> RealtimeStore:
        return self._store

    def start(self) -> dict:
        with self._lock:
            if self._running:
                return {"ok": True, "already_running": True}
            if not fr_config.get_global_enabled():
                return {"ok": False, "reason": "global disabled"}
            self._running = True
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="free-realtime-scheduler"
            )
            self._thread.start()
        logger.info("free_realtime scheduler started")
        return {"ok": True}

    def stop(self) -> dict:
        with self._lock:
            if not self._running:
                return {"ok": True, "already_stopped": True}
            self._running = False
            thread = self._thread
        if thread:
            thread.join(timeout=10)
        self._thread = None
        logger.info("free_realtime scheduler stopped")
        return {"ok": True}

    # ── 主循环 ─────────────────────────────────────────
    def _loop(self) -> None:
        # 随机起点：避免整数秒对齐（外部接口对齐 N 个客户端易触发限流）
        time.sleep(random.uniform(0.1, 1.0))
        while True:
            with self._lock:
                running = self._running
            if not running:
                break
            try:
                self._tick()
            except Exception as e:
                logger.warning("free_realtime tick failed: %s", e)
                self._last_error = str(e)
            interval = fr_config.get_interval_sec()
            slept = 0.0
            while slept < interval:
                with self._lock:
                    if not self._running:
                        break
                time.sleep(0.2)
                slept += 0.2

    def _tick(self) -> None:
        # 全局开关；用户随时关掉就立即生效
        if not fr_config.get_global_enabled():
            return
        # 抓取时段过滤（默认 True）：A 股 9:15-11:30 + 13:00-15:00（含集合竞价）
        if fr_config.get_trading_hours_only() and not is_capture_now():
            return
        # 选源：交替使用未熔断的源
        sources = fr_config.get_sources()
        if not sources:
            return
        active = [s for s in sources if not self._breakers.is_broken(s)]
        if not active:
            self._last_error = "all sources broken"
            return
        # round-robin 选下一个可用源
        self._source_idx %= len(active)
        source = active[self._source_idx]
        self._source_idx += 1

        # 收集标的
        symbols = self._collector.collect()
        self._last_symbol_count = len(symbols)
        if not symbols:
            return

        provider = get_provider(source)
        try:
            df = provider.get_realtime(symbols)
        except Exception as e:
            self._last_error = f"{source} fetch failed: {e}"
            broken = self._breakers.mark_fail(source)
            if broken:
                self._on_circuit_break(source, "fetch exception")
            return

        if df.is_empty():
            broken = self._breakers.mark_fail(source)
            if broken:
                self._on_circuit_break(source, "empty response")
            return

        # 成功 → 清零 + 落盘 + 通知模块
        self._breakers.mark_success(source)
        self._last_tick_ms = time.time() * 1000
        self._last_source = source
        self._last_error = ""
        self._store.append(df, kind="real")
        # 惰性清理：每天落盘后顺带清理
        # 注意：此处不每轮调用 prune，避免 IO 抢占；改为调用方按需触发
        self._dispatch_to_modules(df)

    # ── 模块派发 ───────────────────────────────────────
    def _dispatch_to_modules(self, df) -> None:
        """把最新报价广播给已注册的模块评估器（持仓T/超买超卖/自选股）。"""
        for callback in self._module_callbacks:
            try:
                callback(df)
            except Exception as e:
                logger.warning("module callback %s failed: %s", callback, e)

    _module_callbacks: ClassVar[list[Callable]] = []  # 类级共享，便于注册

    @classmethod
    def register_module_callback(cls, callback: Callable) -> None:
        """注册模块评估回调。模块在启动时调用一次即可。"""
        cls._module_callbacks.append(callback)

    # ── 熔断告警 ───────────────────────────────────────
    def _on_circuit_break(self, source: str, reason: str) -> None:
        """连续失败触发熔断 → 复用 review_push_channels 推送告警。"""
        msg = (
            f"免费实时行情源 {source} 连续 {fr_config.FAIL_BREAK_THRESHOLD} 次失败 "
            f"(原因: {reason})，已暂停该源 5 分钟。请检查接口可用性。"
        )
        logger.warning("free_realtime breaker tripped: %s", msg)
        if source in self._alerted_sources:
            return
        self._alerted_sources.add(source)
        # 复用 daily_pipeline 的推送通道
        try:
            from app.services import preferences, webhook_adapter

            channels = preferences.get_review_push_channels()
            for ch in channels:
                if ch == "feishu":
                    url = preferences.get_feishu_webhook_url()
                    if not url:
                        continue
                    secret = preferences.get_feishu_webhook_secret()
                    webhook_adapter.send_feishu_card(
                        url, "免费实时行情告警", f"数据源 {source}", msg, secret
                    )
                elif ch == "wecom":
                    url = preferences.get_wecom_webhook_url()
                    if not url:
                        continue
                    webhook_adapter.send_wecom_markdown(url, "免费实时行情告警", msg)
        except Exception as e:
            logger.warning("free_realtime alert failed: %s", e)

    def reset_alert(self, source: str) -> None:
        """源恢复后允许重新告警。"""
        self._alerted_sources.discard(source)


# ── 启动钩子（供 main.py 或扩展 startup 调用） ─────────────────
def auto_start_if_enabled() -> None:
    """服务启动时调用：仅当全局开关开启且至少有一个 source 时启动。"""
    scheduler = FreeRealtimeScheduler.get_instance()
    if not fr_config.get_global_enabled():
        return
    if not fr_config.get_sources():
        return
    scheduler.start()


def now_ms() -> int:
    return int(datetime.now(CN_TZ).timestamp() * 1000)


def empty_df():
    return empty_realtime_df()
