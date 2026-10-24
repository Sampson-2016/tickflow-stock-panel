"""A 股免费实时行情 provider 协议与公共常量。

设计原则（对齐 CONTRIBUTING.md §4 数据源插件化）:
  - provider 只负责"拉取 + 标准化"，不知道上层调度/熔断/存储逻辑。
  - HTTP 调用只活在 provider 实现层，上层服务依赖标准化的 pl.DataFrame。
  - 异常 fail-closed：网络/解析失败返回空 DataFrame，不抛出，由调度器
    统计失败次数触发熔断。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

import polars as pl

# 默认 HTTP 超时（秒）。方案约束"一般不超过 3s"。
DEFAULT_TIMEOUT_SEC = 3.0


class FreeRealtimeProvider(ABC):
    """免费实时行情数据源协议。

    实现方负责:
      1. 符号格式转换（项目 symbol → 源特定 key）
      2. HTTP 请求（带超时，默认 3s）
      3. 字段映射与单位转换 → FREE_REALTIME_SCHEMA
      4. 异常 fail-closed：失败返回空 DataFrame，不抛出

    子类必须设置类属性 ``name``（sina/tencent/eastmoney/mock），输出的
    DataFrame 的 ``source`` 列必须等于该值，便于调度器统计各源失败率。

    ts_ms 口径：实时分时场景下，"本次轮询的本地时间戳"才是正确的 tick
    时间维度——行情源返回的时间戳在收盘后会固定不变，会导致同 ts_ms 的
    多次轮询被 store 去重为一行，无法体现轮询序列。子类 normalize 后
    调用 :meth:`_stamp_poll_time` 用本地时间覆盖 ts_ms。
    """

    name: str

    @abstractmethod
    def get_realtime(self, symbols: list[str]) -> pl.DataFrame:
        """批量获取实时报价。

        Args:
            symbols: 项目格式标的列表，如 ``["600000.SH", "159915.SZ"]``。

        Returns:
            符合 FREE_REALTIME_SCHEMA 的 DataFrame；失败或无数据返回空 df。
            ``source`` 列固定为本 provider 的 ``name``。
        """
        raise NotImplementedError

    @staticmethod
    def _stamp_poll_time(df: pl.DataFrame) -> pl.DataFrame:
        """用本地轮询时间覆盖 ts_ms，确保每次轮询是独立 tick。

        - 空 df 直接返回
        - trade_date 由 ts_ms 派生（北京时间当日日期）
        """
        if df.is_empty():
            return df
        now_ms = int(time.time() * 1000)
        from datetime import datetime, timedelta, timezone

        beijing_tz = timezone(timedelta(hours=8))
        trade_date = datetime.fromtimestamp(now_ms / 1000, tz=beijing_tz).date()
        return df.with_columns([
            pl.lit(now_ms).alias("ts_ms"),
            pl.lit(trade_date).alias("trade_date"),
        ])
