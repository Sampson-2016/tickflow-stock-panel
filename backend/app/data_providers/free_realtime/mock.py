"""Mock 实时行情 provider。

用于测试页模拟行情数据。支持:
  - 配置标的开盘价/波动率
  - 平缓随机游走（单步变动 ≤ volatility_pct * price）
  - 概率性失败注入（fail_rate）
  - 连续失败注入（inject_fail）
  - 确定性 seed
  - 虚拟时钟（start_time）：从历史起点回放已发生 tick

PR1 提供基础骨架，完整测试页逻辑（加速倍率/交易时段绕过）在 PR4 完善。
"""
from __future__ import annotations

import random
import time
from datetime import date as _date, datetime, time as dt_time, timedelta

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
# 价格围绕基准价的最大波动幅度（±10%），防止随机游走漂移到离谱价位
_PRICE_BAND_PCT = 0.10
# 「查无日线数据」负缓存 TTL（秒）：避免无日线标的每 tick 重复全量扫描
_NEG_CACHE_TTL_SEC = 300.0
# 日线收盘价缓存: symbol -> (date, last_close | None, expire_ts)
# close=None 为负缓存（该标的查无数据），到 expire_ts 前直接返回 None
_close_cache: dict[str, tuple] = {}


def _resolve_last_close(symbol: str) -> float | None:
    """取该标的日线数据中最后一个交易日的 raw_close（当日缓存 + 负缓存）。

    用于 mock 未配置 base_prices 时自动以真实最近收盘价为基准，
    避免 mock 价格（如 10 元）与真实股价（如 345 元）严重脱节。
    取不到时返回 None（调用方兜底 _DEFAULT_BASE_PRICE）；
    「确认查无数据」会缓存 _NEG_CACHE_TTL_SEC 秒，repo 未就绪等瞬态
    错误不缓存（下次重试）。
    """
    today = datetime.now(CN_TZ).date()
    now_ts = time.time()
    cached = _close_cache.get(symbol)
    if cached:
        cdate, close, expire_ts = cached
        if close is not None and cdate == today:
            return close
        if close is None and now_ts < expire_ts:
            return None
    fallback = cached[1] if cached and cached[1] is not None else None
    try:
        from app.main import app as _fastapp

        repo = getattr(_fastapp.state, "repo", None)
        if repo is None:
            return fallback
        end = today
        start = end - timedelta(days=30)
        df = repo.get_daily(symbol, start, end, columns=["date", "raw_close"])
        if df.is_empty() or "raw_close" not in df.columns:
            # 确认查无数据：写负缓存，避免每 tick 重复扫描
            _close_cache[symbol] = (today, None, now_ts + _NEG_CACHE_TTL_SEC)
            return fallback
        vals = [
            v for v in df.sort("date")["raw_close"].to_list()
            if v is not None and v > 0
        ]
        if not vals:
            _close_cache[symbol] = (today, None, now_ts + _NEG_CACHE_TTL_SEC)
            return fallback
        close = float(vals[-1])
        _close_cache[symbol] = (today, close, 0.0)
        return close
    except Exception:
        # 瞬态错误（repo 未就绪等）不缓存，下次 tick 重试
        return fallback


def _parse_start_time(raw: str | None) -> dt_time | None:
    """解析 "HH:MM" 格式的虚拟时钟起点，失败返回 None。"""
    if not raw:
        return None
    try:
        h, m = str(raw).strip().split(":")
        return dt_time(int(h), int(m))
    except Exception:
        return None


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
            "start_time": "09:15",              # 虚拟时钟起点（可选）
        }
    """

    def __init__(self, config: dict | None = None) -> None:
        self.name = "mock"
        self._config: dict = dict(config) if config else {}
        self._rng = random.Random(self._config.get("seed"))
        self._current_prices: dict[str, float] = {}
        # 日内高低价累计: symbol -> (trade_date, day_high, day_low)
        # high/low 不只相对 base 单点计算，分时图日高日低与实际曲线一致
        self._day_extremes: dict[str, tuple] = {}
        # 连续失败注入：剩余次数
        self._fail_remaining: int = 0
        # 虚拟时钟：若 start_time 配置则每次 get_realtime 推进一个 base interval
        self._mock_clock: datetime | None = None
        self._init_mock_clock()

    def set_config(self, config: dict) -> None:
        """整体替换配置并重置状态。"""
        self._config = dict(config)
        self._rng = random.Random(self._config.get("seed"))
        self._current_prices.clear()
        self._day_extremes.clear()
        self._fail_remaining = 0
        self._init_mock_clock()

    def set_tick_interval(self, seconds: float) -> None:
        """仅更新 tick 间隔（虚拟时钟推进步长），不重置价格序列与 RNG。

        用于测试页 mock 循环跟随用户在「A股免费实时行情」配置面板修改的
        刷新间隔（interval_sec）——避免 set_config 重置 _current_prices
        导致价格序列断裂。
        """
        if seconds <= 0:
            return
        self._config["interval_seconds"] = float(seconds)

    def _init_mock_clock(self) -> None:
        """根据 start_time 配置初始化虚拟时钟（今日 + start_time）。"""
        t = _parse_start_time(self._config.get("start_time"))
        if t is None:
            self._mock_clock = None
            return
        now = datetime.now(CN_TZ)
        self._mock_clock = datetime(
            now.year, now.month, now.day, t.hour, t.minute, tzinfo=CN_TZ
        )

    def mock_clock(self) -> datetime | None:
        """当前虚拟时钟；None 表示使用真实时间。"""
        return self._mock_clock

    def set_mock_clock(self, dt: datetime) -> None:
        """外部强制设置虚拟时钟（用于回放控制）。"""
        self._mock_clock = dt

    def step_mock_clock(self, delta_seconds: float) -> None:
        """推进虚拟时钟；仅当 start_time 已配置时生效。"""
        if self._mock_clock is not None:
            self._mock_clock = self._mock_clock + timedelta(seconds=delta_seconds)

    def backfill(self, symbols: list[str], from_dt: datetime, to_dt: datetime,
                 interval_sec: float) -> pl.DataFrame:
        """快速回放 [from_dt, to_dt) 段所有 tick，按 interval_sec 间隔，单次返回。

        用于 mock 启动时一次性补齐 9:15 → start_time 段的历史数据，
        之后 _mock_loop 进入 1-tick-per-interval 的流式生成（"两个接口"中的另一个）。

        约定（与 _mock_loop 流式路径保持一致，保证时间轴连续无重叠）:
          - 第一个 tick 时间戳 = from_dt（如 09:15:00）
          - 最后一个 tick 时间戳 = to_dt - interval_sec（如 09:59:54，interval=6s, to=10:00）
          - 返回时 mock_clock 停在最后一个 tick 的时间戳，留给 _mock_loop 流式循环接管：
            下一次 get_realtime 推进到 to_dt，生成 tick 在 to_dt（如 10:00:00），无缝衔接

        返回单 DataFrame，包含所有 symbols 在所有时间点的 tick（已合并）。
        """
        if not symbols or interval_sec <= 0 or to_dt <= from_dt:
            return empty_realtime_df()

        # 让第一个 tick 落在 from_dt：get_realtime 内部先 advance +interval 再生成 tick
        # 所以 clock 设为 from_dt - interval，第一次调用后 advance 到 from_dt
        self._mock_clock = from_dt - timedelta(seconds=interval_sec)
        self._config["interval_seconds"] = float(interval_sec)

        rows: list[pl.DataFrame] = []
        # 循环：检查"下一次推进后是否会越界"，避免生成多余的 tick（也避免浪费 RNG step）
        while self._mock_clock is not None and \
                self._mock_clock + timedelta(seconds=interval_sec) < to_dt:
            df = self.get_realtime(symbols)
            if not df.is_empty():
                rows.append(df)

        if not rows:
            return empty_realtime_df()
        return pl.concat(rows)

    def inject_fail(self, consecutive: int = 1) -> None:
        """注入连续失败次数。接下来 consecutive 次返回空 DataFrame。"""
        self._fail_remaining = max(0, consecutive)

    def _next_price(self, symbol: str, base: float) -> float:
        """随机游走：单步变动 = uniform(-vol*prev, vol*prev)，并钳制在基准价 ±10%。"""
        prev = self._current_prices.get(symbol)
        lo = base * (1.0 - _PRICE_BAND_PCT)
        hi = base * (1.0 + _PRICE_BAND_PCT)
        if prev is None:
            price = base
        else:
            vol = self._config.get("volatility_pct", 0.01)
            delta = self._rng.uniform(-vol * prev, vol * prev)
            price = prev + delta
        price = min(hi, max(lo, price))
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

        # 虚拟时钟优先：每次调用前推进一个 base interval（仅回放/循环节奏一致）
        now = datetime.now(CN_TZ)
        if self._mock_clock is not None:
            base = float(self._config.get("interval_seconds", 1.0))
            self._mock_clock = self._mock_clock + timedelta(seconds=base)
            now = self._mock_clock
        ts_ms = int(now.timestamp() * 1000)
        trade_date = now.date()
        base_prices = self._config.get("base_prices", {})
        names = self._config.get("names", {})
        rows: list[dict] = []
        for sym in symbols:
            # base_prices 未配置（或非法）时，自动取该标的日线最近收盘价为基准，
            # 避免 mock 价格与真实股价脱节（如真实 345 元 mock 出 10 元）。
            base = base_prices.get(sym)
            if base is None or base <= 0:
                base = _resolve_last_close(sym) or _DEFAULT_BASE_PRICE
            price = self._next_price(sym, base)
            # 日内 high/low 累计（跨 tick、按交易日重置）
            ext = self._day_extremes.get(sym)
            if ext is None or ext[0] != trade_date:
                day_high, day_low = max(price, base), min(price, base)
            else:
                day_high, day_low = max(price, ext[1]), min(price, ext[2])
            self._day_extremes[sym] = (trade_date, day_high, day_low)
            prev_close = base
            change_pct = (price - prev_close) / prev_close if prev_close else 0.0
            rows.append({
                "symbol": sym,
                "name": names.get(sym, sym),
                "asset_type": infer_asset_type(sym),
                "last_price": price,
                "open": base,
                "prev_close": prev_close,
                "high": day_high,
                "low": day_low,
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
