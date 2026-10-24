"""Mock 实时行情 provider。

用于测试页模拟行情数据。支持:
  - 配置标的开盘价/波动率
  - 平缓随机游走（单步变动 ≤ volatility_pct * price）
  - 概率性失败注入（fail_rate）
  - 连续失败注入（inject_fail）
  - 确定性 seed

PR1 提供基础骨架，完整测试页逻辑（加速倍率/交易时段绕过）在 PR4 完善。
"""
from __future__ import annotations

import random
from datetime import datetime

import polars as pl

from app.data_providers.free_realtime.base import FreeRealtimeProvider
from app.data_providers.free_realtime.schemas import (
    FREE_REALTIME_SCHEMA,
    empty_realtime_df,
    infer_asset_type,
)
from app.markets import CN_TZ

# 兜底开盘价
_DEFAULT_BASE_PRICE = 10.0


class MockProvider(FreeRealtimeProvider):
    """Mock 实时报价 provider。

    config 格式:
        {
            "symbols": ["600000.SH"],            # 可选，仅文档作用
            "base_prices": {"600000.SH": 10.0},  # 开盘价/昨收
            "names": {"600000.SH": "浦发银行"},
            "volatility_pct": 0.01,              # 单步变动幅度 = price * vol
            "fail_rate": 0.0,                    # 0~1 概率性失败
            "seed": 42,                          # 确定性
            "interval_seconds": 1.0,             # 基础间隔（PR4 加速用）
            "acceleration": 1,                   # 1/3/10/30/60/120
        }
    """

    def __init__(self, config: dict | None = None) -> None:
        self.name = "mock"
        self._config: dict = dict(config) if config else {}
        self._rng = random.Random(self._config.get("seed"))
        self._current_prices: dict[str, float] = {}
        # 连续失败注入：剩余次数
        self._fail_remaining: int = 0

    def set_config(self, config: dict) -> None:
        """整体替换配置并重置状态。"""
        self._config = dict(config)
        self._rng = random.Random(self._config.get("seed"))
        self._current_prices.clear()
        self._fail_remaining = 0

    def inject_fail(self, consecutive: int = 1) -> None:
        """注入连续失败次数。接下来 consecutive 次返回空 DataFrame。"""
        self._fail_remaining = max(0, consecutive)

    def _next_price(self, symbol: str, base: float) -> float:
        """随机游走：单步变动 = uniform(-vol*prev, vol*prev)。"""
        prev = self._current_prices.get(symbol)
        if prev is None:
            price = base
        else:
            vol = self._config.get("volatility_pct", 0.01)
            delta = self._rng.uniform(-vol * prev, vol * prev)
            price = prev + delta
            if price <= 0:
                price = base
        self._current_prices[symbol] = price
        return price

    def get_realtime(self, symbols: list[str]) -> pl.DataFrame:
        if not symbols:
            return empty_realtime_df()
        # 连续失败注入优先
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            return empty_realtime_df()
        # 概率性失败
        fail_rate = self._config.get("fail_rate", 0.0)
        if fail_rate > 0 and self._rng.random() < fail_rate:
            return empty_realtime_df()

        now = datetime.now(CN_TZ)
        ts_ms = int(now.timestamp() * 1000)
        trade_date = now.date()
        base_prices = self._config.get("base_prices", {})
        names = self._config.get("names", {})
        rows: list[dict] = []
        for sym in symbols:
            base = base_prices.get(sym, _DEFAULT_BASE_PRICE)
            price = self._next_price(sym, base)
            prev_close = base
            change_pct = (price - prev_close) / prev_close if prev_close else 0.0
            rows.append({
                "symbol": sym,
                "name": names.get(sym, sym),
                "asset_type": infer_asset_type(sym),
                "last_price": price,
                "open": base,
                "prev_close": prev_close,
                "high": max(price, base),
                "low": min(price, base),
                "change_pct": change_pct,
                "volume": float(self._rng.randint(1000, 100000)),
                "amount": float(self._rng.randint(1000000, 100000000)),
                "bid1_price": round(price - 0.01, 4),
                "ask1_price": round(price + 0.01, 4),
                "bid1_volume": float(self._rng.randint(100, 1000)),
                "ask1_volume": float(self._rng.randint(100, 1000)),
                "source": self.name,
                "ts_ms": ts_ms,
                "trade_date": trade_date,
            })
        return pl.DataFrame(rows, schema=FREE_REALTIME_SCHEMA)
