"""免费实时行情分时存储 — 按交易日落盘 parquet，自动清理 N 日外的目录。

设计要点 (与 CONTRIBUTING.md 第 6 节缓存与性能要求一致):
  - 按 trade_date 分目录，跨日清理：data/user_data/free_realtime/{kind}/{YYYY-MM-DD}/{symbol}.parquet
  - kind ∈ {"real", "mock"}：mock 与真实落盘完全隔离（避免 mock 污染历史）
  - 单标的单日 parquet，append 模式（按 ts_ms 去重 + 排序）
  - 保留 N 日（默认 7），清理在每轮落盘前惰性触发
  - 进程内最新窗口缓存：每个标的保留最近 N 个点，避免每次落盘都全量读盘
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from app.data_providers.free_realtime.schemas import (
    FREE_REALTIME_COLUMNS,
    FREE_REALTIME_SCHEMA,
    empty_realtime_df,
)
from app.markets import CN_TZ


def _today_cn() -> date:
    """交易日口径统一用 Asia/Shanghai，避免非 CST 时区服务器跨午夜取错「今日」。"""
    return datetime.now(CN_TZ).date()

logger = logging.getLogger(__name__)

# 默认保留天数（与项目既有 daily parquet 同口径，避免无限堆积）
DEFAULT_RETENTION_DAYS = 7
# 进程内窗口：单标的最多缓存最近 N 个点，超出后从头部丢弃
_IN_MEMORY_WINDOW = 240


def _root(data_dir: Path, kind: str) -> Path:
    return data_dir / "user_data" / "free_realtime" / kind


class RealtimeStore:
    """免费实时分时数据落盘 + 进程内最新窗口缓存。

    线程安全：写盘与读盘共用 RLock，但所有 IO 在锁外执行（避免锁阻塞网络线程）。
    实际实现：内存窗口用锁保护；落盘动作在锁内收集 row，锁外 append 写盘。
    """

    def __init__(
        self,
        data_dir: Path,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._retention_days = max(1, int(retention_days))
        # kind -> symbol -> deque[row dict]
        self._buffers: dict[str, dict[str, deque[dict]]] = {"real": {}, "mock": {}}
        self._lock = threading.RLock()
        # 已落盘的 (kind, trade_date, symbol) 集合，避免重复检查文件存在
        self._touched: set[tuple[str, str, str]] = set()

    # ── 写入 ───────────────────────────────────────────────
    def append(self, df: pl.DataFrame, kind: str = "real") -> None:
        """把一批实时报价落到内存窗口 + parquet。

        df 必须符合 FREE_REALTIME_SCHEMA。空 DataFrame 直接返回。
        """
        if df.is_empty():
            return
        if kind not in self._buffers:
            logger.warning("unknown free_realtime kind: %s (drop)", kind)
            return
        # 收集 row → 落盘；锁内仅操作内存窗口
        rows_by_symbol: dict[str, list[dict]] = {}
        with self._lock:
            buf = self._buffers[kind]
            for row in df.iter_rows(named=True):
                sym = str(row.get("symbol") or "")
                if not sym:
                    continue
                dq = buf.setdefault(sym, deque(maxlen=_IN_MEMORY_WINDOW))
                # 同分钟去重：最后一分钟覆盖前一条（保持点稀疏）
                ts_ms = int(row.get("ts_ms") or 0)
                if dq and ts_ms > 0 and dq[-1].get("ts_ms", 0) // 60000 == ts_ms // 60000:
                    dq[-1] = dict(row)
                else:
                    dq.append(dict(row))
                rows_by_symbol.setdefault(sym, []).append(dict(row))

        # 锁外落盘（每标的一文件）
        for sym, rows in rows_by_symbol.items():
            self._flush_to_parquet(rows, sym, kind)

    def _flush_to_parquet(self, rows: list[dict], symbol: str, kind: str) -> None:
        try:
            trade_date = rows[0].get("trade_date")
            if not trade_date:
                return
            day_dir = _root(self._data_dir, kind) / str(trade_date)
            day_dir.mkdir(parents=True, exist_ok=True)
            path = day_dir / f"{symbol}.parquet"
            new_df = pl.DataFrame(rows, schema=FREE_REALTIME_SCHEMA).select(FREE_REALTIME_COLUMNS)
            if path.exists():
                # 读取已有 + 合并 + 按 ts_ms 去重 + 排序
                old = pl.read_parquet(path)
                merged = pl.concat([old, new_df]).unique(
                    subset=["symbol", "ts_ms"], keep="last"
                ).sort("ts_ms")
                merged.write_parquet(path)
            else:
                new_df.write_parquet(path)
        except Exception as e:
            logger.warning("free_realtime flush failed kind=%s sym=%s: %s", kind, symbol, e)

    # ── 读取 ───────────────────────────────────────────────
    def list_today(self, symbol: str, kind: str = "real") -> pl.DataFrame:
        """读取某标的当日内存窗口。"""
        with self._lock:
            dq = self._buffers.get(kind, {}).get(symbol)
            if not dq:
                return empty_realtime_df()
            return pl.DataFrame(list(dq), schema=FREE_REALTIME_SCHEMA).select(
                FREE_REALTIME_COLUMNS
            )

    def list_day(self, symbol: str, trade_date: date, kind: str = "real") -> pl.DataFrame:
        """读取某标的某交易日的全部 parquet 数据 + 内存窗口合并。"""
        path = _root(self._data_dir, kind) / str(trade_date) / f"{symbol}.parquet"
        disk_df: pl.DataFrame
        if path.exists():
            try:
                disk_df = pl.read_parquet(path)
            except Exception as e:
                logger.warning("free_realtime read parquet failed %s: %s", path, e)
                disk_df = empty_realtime_df()
        else:
            disk_df = empty_realtime_df()
        with self._lock:
            dq = self._buffers.get(kind, {}).get(symbol)
            mem_rows = list(dq) if dq else []
        if not mem_rows:
            return disk_df.select(FREE_REALTIME_COLUMNS) if not disk_df.is_empty() else empty_realtime_df()
        mem_df = pl.DataFrame(mem_rows, schema=FREE_REALTIME_SCHEMA).select(FREE_REALTIME_COLUMNS)
        if disk_df.is_empty():
            return mem_df
        merged = pl.concat([disk_df, mem_df]).unique(subset=["symbol", "ts_ms"], keep="last").sort("ts_ms")
        return merged.select(FREE_REALTIME_COLUMNS)

    def list_recent_days(
        self, symbol: str, days: int = 1, kind: str = "real"
    ) -> pl.DataFrame:
        """读取最近 N 个交易日的合并数据（用于前端分时图与回看）。"""
        if days <= 0:
            return empty_realtime_df()
        today = _today_cn()
        parts: list[pl.DataFrame] = []
        for i in range(days):
            d = today - timedelta(days=i)
            df = self.list_day(symbol, d, kind=kind)
            if not df.is_empty():
                parts.append(df)
        if not parts:
            return empty_realtime_df()
        return pl.concat(parts).sort("ts_ms").select(FREE_REALTIME_COLUMNS)

    # ── 清理 ───────────────────────────────────────────────
    def prune(self, *, now_date: date | None = None) -> int:
        """惰性清理：删除超过 retention_days 的交易日目录。返回已删除的目录数。"""
        now_date = now_date or _today_cn()
        cutoff = now_date - timedelta(days=self._retention_days)
        removed = 0
        for kind in ("real", "mock"):
            root = _root(self._data_dir, kind)
            if not root.exists():
                continue
            for sub in root.iterdir():
                if not sub.is_dir():
                    continue
                try:
                    d = date.fromisoformat(sub.name)
                except ValueError:
                    continue
                if d < cutoff:
                    try:
                        # rmtree 风险控制：仅删 free_realtime 子目录
                        import shutil

                        shutil.rmtree(sub)
                        removed += 1
                    except Exception as e:
                        logger.warning("free_realtime prune %s failed: %s", sub, e)
        return removed

    def clear_kind(self, kind: str) -> int:
        """一键清空某 kind 的所有落盘 + 内存缓冲（测试页用）。"""
        if kind not in ("real", "mock"):
            return 0
        removed = 0
        with self._lock:
            self._buffers[kind].clear()
        root = _root(self._data_dir, kind)
        if root.exists():
            try:
                import shutil

                for sub in list(root.iterdir()):
                    if sub.is_dir():
                        shutil.rmtree(sub)
                        removed += 1
                    elif sub.is_file():
                        sub.unlink()
                        removed += 1
            except Exception as e:
                logger.warning("free_realtime clear_kind %s failed: %s", kind, e)
        return removed

    def stats(self) -> dict:
        """给 /api/free-realtime/status 返回存储概览。"""
        sizes: dict[str, int] = {}
        for kind in ("real", "mock"):
            root = _root(self._data_dir, kind)
            cnt = 0
            if root.exists():
                for _ in root.rglob("*.parquet"):
                    cnt += 1
            with self._lock:
                buf_count = sum(len(buf) for buf in self._buffers[kind].values())
            sizes[kind] = cnt
            sizes[f"{kind}_in_memory"] = buf_count
        return {
            "retention_days": self._retention_days,
            "files": sizes,
        }
