"""持仓 T 操作信号 —— 基于统一免费实时行情的盘中提示。

定位:
  - 项目核心监控体系 (MonitorRuleEngine + 数据源插件化) 走 TickFlow,
    适合有 TickFlow 订阅的策略/选股/回测场景。
  - 本模块为独立轻量通道: 行情统一走 free_realtime provider 层
    (新浪/腾讯/东财 + mock)，给持仓 T 跑盘中 T 操作提示, 推送飞书群机器人。
  - 不入 enriched pipeline, 不写 parquet, 不进 MonitorRuleEngine,
    完全独立, 失败不污染主流程。

L2 扩展: 文件落在 backend/app/custom/, 由 app.extensions.loader 自动发现,
不需要改 app/main.py。

历史说明: 原 easyquotation 拉取路径已移除（2026-10-25），持仓 T 仅由
统一行情驱动（real 主调度器 + mock 测试页循环注入），目录/扩展 ID
保留原名仅为兼容前端 import 路径与扩展注册 ID。

已知限制:
  - 统一行情 provider 暂无港股源；.HK 标的的评估逻辑保留（单测覆盖），
    但运行期不会有数据喂入，add() 时会打告警。
  - POST /start 保留为「统一行情不可用时的手动兜底自轮询」路径，
    正常情况应由调度器回调 (on_realtime_df) 驱动，两者勿同时使用
    （同标的会重复请求外部行情源，仅靠 cooldown 去重信号、不去重请求）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

# ── 复用项目现有能力 (不重复实现) ───────────────────────────────
from app.config import settings  # noqa: E402
from app.extensions import (  # noqa: E402
    BACKEND_EXTENSION_API_VERSION,
    BackendExtensionRegistrar,
    ExtensionContext,
)
from app.markets import is_trading_now, market_of  # noqa: E402
from app.services.free_realtime import config as fr_config  # noqa: E402

# ================================================================
# 配置
# ================================================================

DEFAULT_CONFIG: dict[str, Any] = {
    "interval_sec": 3,
    "price_change_pct_threshold": 3.0,
    "volume_ratio_threshold": 5.0,
    "big_order_imbalance_threshold": 0.4,
    "cooldown_seconds": 60,
    "trading_hours_only": True,
    "auto_push_to_feishu": True,
    # 策略选项: 盘中实时计算哪些信号算法 (空列表 = 全部禁用, 只攒分时不推信号)
    # 注意: STRATEGY_KINDS 在下方信号模型处定义, 这里用字面量保持定义顺序
    "strategies": [
        "price_change",
        "volume_surge",
        "big_order_imbalance",
        "vwap_revert",
        "rsi_extreme",
        "range_break",
    ],
}

# 字段白名单, PUT /config 时过滤未知字段 (fail-closed, 避免外部误传)
CONFIG_KEYS = frozenset(DEFAULT_CONFIG.keys())


def _config_path() -> Path:
    p = settings.data_dir / "user_data" / "t_signal_config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_config() -> dict[str, Any]:
    """读取磁盘配置, 缺失字段补默认 (迁移友好)。

    v1 配置的 enabled 字段被丢弃 — 持仓 T 模块改用「每只标的独立 enabled」,
    全局 enabled 已无意义 (添加第一个标的并启用即开始评估)。
    """
    p = _config_path()
    cfg = dict(DEFAULT_CONFIG)
    if p.exists():
        try:
            stored = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                for k in CONFIG_KEYS:
                    if k in stored:
                        cfg[k] = stored[k]
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("t_signal config load failed: %s", e)
    return cfg


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """过滤未知字段后落盘, 返回过滤后的配置。"""
    cleaned = {k: cfg[k] for k in CONFIG_KEYS if k in cfg}
    for k in CONFIG_KEYS:
        cleaned.setdefault(k, DEFAULT_CONFIG[k])
    if not isinstance(cleaned["interval_sec"], int) or cleaned["interval_sec"] < 1:
        raise ValueError("interval_sec 必须是 >= 1 的整数")
    for k in (
        "price_change_pct_threshold",
        "volume_ratio_threshold",
        "big_order_imbalance_threshold",
    ):
        v = cleaned[k]
        if not isinstance(v, (int, float)) or v <= 0 or v > 1000:
            raise ValueError(f"{k} 必须是 > 0 的数字")
    if not isinstance(cleaned["cooldown_seconds"], int) or not 0 <= cleaned["cooldown_seconds"] <= 3600:
        raise ValueError("cooldown_seconds 必须是 0..3600 的整数")
    if not isinstance(cleaned["trading_hours_only"], bool):
        raise ValueError("trading_hours_only 必须是 bool")
    if not isinstance(cleaned["auto_push_to_feishu"], bool):
        raise ValueError("auto_push_to_feishu 必须是 bool")
    strategies = cleaned["strategies"]
    if not isinstance(strategies, list) or any(s not in STRATEGY_KINDS for s in strategies):
        raise ValueError(f"strategies 必须是 {list(STRATEGY_KINDS)} 的子集")
    _config_path().write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    return cleaned


# ================================================================
# 信号模型
# ================================================================

# kind: 涨跌幅阈值 / 量比异动 / 买卖盘失衡 / VWAP均价回归 / RSI极值 / 分时区间突破
KIND_PRICE = "price_change"
KIND_VOLUME = "volume_surge"
KIND_BIG_ORDER = "big_order_imbalance"
KIND_VWAP_REVERT = "vwap_revert"
KIND_RSI_EXTREME = "rsi_extreme"
KIND_RANGE_BREAK = "range_break"
STRATEGY_KINDS = (KIND_PRICE, KIND_VOLUME, KIND_BIG_ORDER, KIND_VWAP_REVERT, KIND_RSI_EXTREME, KIND_RANGE_BREAK)
KIND_LABELS = {
    KIND_PRICE: "涨跌幅阈值",
    KIND_VOLUME: "量比异动",
    KIND_BIG_ORDER: "买卖盘失衡",
    KIND_VWAP_REVERT: "VWAP均价回归",
    KIND_RSI_EXTREME: "RSI极值反转",
    KIND_RANGE_BREAK: "分时区间突破",
}
KIND_SEVERITY = {
    KIND_PRICE: "high",
    KIND_VOLUME: "medium",
    KIND_BIG_ORDER: "medium",
    KIND_VWAP_REVERT: "medium",
    KIND_RSI_EXTREME: "medium",
    KIND_RANGE_BREAK: "high",
}


@dataclass
class TSignal:
    kind: str
    symbol: str
    name: str
    ts_ms: int
    price: float
    prev_close: float
    change_pct: float
    detail: str            # 单条命中规则的详细描述 (用于卡片正文)
    severity: str           # high / medium
    source: str = "real"    # "real" 真实行情 / "mock" 测试页虚拟行情

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ================================================================
# Symbol 转换: 项目格式 <-> easyquotation 格式
# ================================================================

_CN_EXCHANGE_PREFIX = {".SH": "sh", ".SZ": "sz", ".BJ": "bj"}


def to_cn_key(symbol: str) -> str | None:
    """A 股项目 symbol -> easyquotation 新浪 key, 不支持的格式返回 None。"""
    s = symbol.strip().upper()
    for suffix, prefix in _CN_EXCHANGE_PREFIX.items():
        if s.endswith(suffix):
            code = s[: -len(suffix)]
            return f"{prefix}{code}"
    return None


def to_hk_key(symbol: str) -> str | None:
    """港股项目 symbol (00700.HK) -> easyquotation 腾讯 key (00700), 不支持返回 None。"""
    s = symbol.strip().upper()
    if s.endswith(".HK"):
        return s[: -3]
    return None


def is_a_stock(symbol: str) -> bool:
    """仅 A 股 (排除港美股、指数、ETF)。"""
    try:
        return market_of(symbol) == "cn" and symbol.endswith((".SH", ".SZ", ".BJ"))
    except Exception:
        return False


def is_hk_stock(symbol: str) -> bool:
    """仅港股 (.HK 后缀, 港股通 A 股标的也算 A 股, 不算港股)。"""
    try:
        return market_of(symbol) == "hk" and symbol.endswith(".HK")
    except Exception:
        return False


def is_supported_symbol(symbol: str) -> bool:
    """项目当前支持的市场: A 股 + 港股。"""
    return is_a_stock(symbol) or is_hk_stock(symbol)


# ================================================================
# ================================================================
# 行情拉取 — 统一走 free_realtime provider 层 (sina/tencent/eastmoney)
# 原 easyquotation 拉取路径已移除; 持仓T 仅由统一行情驱动
# ================================================================

class QuoteFetcher:
    """基于 free_realtime provider 的行情拉取; 对调用方暴露统一的 fetch(symbols) 接口。

    返回 {项目symbol: quote_dict}，字段对齐原 easyquotation 输出口径:
      now/open/high/low/volume/amount/bid1_price/ask1_price/...
    """

    def __init__(self) -> None:
        self.last_error: str = ""
        self.last_fetch_ms: float = 0.0

    def fetch(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        from app.data_providers.free_realtime.registry import get_provider

        if not symbols:
            return {}
        out: dict[str, dict[str, Any]] = {}
        try:
            # 轮换源在主调度器已做；此兼容路径直接用首个勾选源
            source = fr_config.get_sources()[0] if fr_config.get_sources() else "sina"
            df = get_provider(source).get_realtime(symbols)
        except Exception as e:
            self.last_error = f"free_realtime fetch failed: {e}"
            logger.warning(self.last_error)
            return {}
        if df.is_empty():
            self.last_error = "free_realtime fetch empty"
            return {}
        self.last_fetch_ms = time.time() * 1000
        self.last_error = ""
        for row in df.iter_rows(named=True):
            sym = str(row.get("symbol") or "")
            if sym:
                out[sym] = row
        return out


# ================================================================
# 评估器: 三件套规则
# ================================================================

def _safe_float(value: Any) -> float | None:
    """宽松转 float；None/非法/NaN/inf 一律返回 None（模块级，供 evaluator 与 store 共用）。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


class SignalEvaluator:
    """从原始 quote 评估三件套信号。

    量比计算依赖进程内缓存 (每个标的最近 N 次成交额), 不依赖外部历史。
    冷启动 (前 N 次拉取前) 跳过量比评估, 避免窗口不足的虚假信号。
    """

    WINDOW = 20                  # 量比窗口: 最近 20 次拉取的成交额
    RSI_PERIOD = 14              # RSI 计算周期 (按拉取轮次近似, 非固定 bar)
    RANGE_LOOKBACK = 30          # 分时区间突破: 回看最近 30 轮高低点

    def __init__(self) -> None:
        self._vol_history: dict[str, deque[float]] = {}
        # 新策略依赖的进程内价格窗口 (每标的最近 N 轮价格)
        self._price_history: dict[str, deque[float]] = {}
        # VWAP 累计: 当日累计成交额/成交量 (由行情字段直接给出, 无需自算)
        self._day_key: dict[str, str] = {}  # symbol -> 所属交易日 (跨日清零窗口)

    def _push_price(self, symbol: str, price: float) -> deque[float]:
        hist = self._price_history.setdefault(symbol, deque(maxlen=max(self.RSI_PERIOD + 1, self.RANGE_LOOKBACK)))
        hist.append(price)
        return hist

    def _push_volume(self, symbol: str, volume: float | None) -> None:
        if volume is None or volume <= 0:
            return
        hist = self._vol_history.setdefault(symbol, deque(maxlen=self.WINDOW))
        hist.append(float(volume))

    def _volume_ratio(self, symbol: str, current: float | None) -> float | None:
        """当前成交额 / 历史窗口均值的比值; 窗口未满返回 None。"""
        if current is None or current <= 0:
            return None
        hist = self._vol_history.get(symbol)
        if hist is None or len(hist) < self.WINDOW:
            return None
        avg = sum(hist) / len(hist)
        if avg <= 0:
            return None
        return current / avg

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        # 委托模块级实现，保持 self._safe_float 调用兼容
        return _safe_float(value)

    def evaluate(
        self,
        quotes: dict[str, dict[str, Any]],
        cfg: dict[str, Any],
        now_ms: int,
    ) -> list[TSignal]:
        signals: list[TSignal] = []
        price_th = float(cfg["price_change_pct_threshold"])
        vol_th = float(cfg["volume_ratio_threshold"])
        imb_th = float(cfg["big_order_imbalance_threshold"])
        strategies = set(cfg.get("strategies") or STRATEGY_KINDS)

        for symbol, raw in quotes.items():
            name = str(raw.get("name") or symbol)

            # 按市场分流: A 股走全部策略, 港股仅涨跌幅 (无盘口/无 turnover)
            if is_a_stock(symbol):
                self._evaluate_a_stock(
                    symbol, raw, name, price_th, vol_th, imb_th, strategies, now_ms, signals
                )
            elif is_hk_stock(symbol) and KIND_PRICE in strategies:
                self._evaluate_hk(symbol, raw, name, price_th, now_ms, signals)
            # 其他市场直接跳过

        return signals

    def _evaluate_a_stock(
        self,
        symbol: str,
        raw: dict[str, Any],
        name: str,
        price_th: float,
        vol_th: float,
        imb_th: float,
        strategies: set[str],
        now_ms: int,
        signals: list[TSignal],
    ) -> None:
        now_price = self._safe_float(raw.get("now"))
        # 昨收: 统一行情 schema 键为 prev_close, 旧 easyquotation 口径为 close, 两者兼容
        prev_close = self._safe_float(
            raw.get("prev_close") if raw.get("prev_close") is not None else raw.get("close")
        )
        volume = self._safe_float(raw.get("volume"))
        bid1_v = self._safe_float(raw.get("bid1_volume"))
        ask1_v = self._safe_float(raw.get("ask1_volume"))

        if now_price is None or prev_close is None or prev_close <= 0:
            return

        change_pct = (now_price - prev_close) / prev_close * 100.0

        # ── 1. 涨跌幅阈值 ──
        if KIND_PRICE in strategies and abs(change_pct) >= price_th:
            direction = "上涨" if change_pct > 0 else "下跌"
            signals.append(TSignal(
                kind=KIND_PRICE,
                symbol=symbol,
                name=name,
                ts_ms=now_ms,
                price=now_price,
                prev_close=prev_close,
                change_pct=change_pct,
                detail=f"现价 {now_price:.2f} ({direction} {abs(change_pct):.2f}%, 阈值 ±{price_th:.1f}%)",
                severity=KIND_SEVERITY[KIND_PRICE],
            ))

        # ── 2. 量比异动 ──
        self._push_volume(symbol, volume)
        ratio = self._volume_ratio(symbol, volume)
        if KIND_VOLUME in strategies and ratio is not None and ratio >= vol_th:
            signals.append(TSignal(
                kind=KIND_VOLUME,
                symbol=symbol,
                name=name,
                ts_ms=now_ms,
                price=now_price,
                prev_close=prev_close,
                change_pct=change_pct,
                detail=(
                    f"成交额 {volume / 10000:.1f}万, 即时量比 {ratio:.1f}x "
                    f"(窗口 {self.WINDOW} 轮均值, 阈值 {vol_th:.1f}x)"
                ),
                severity=KIND_SEVERITY[KIND_VOLUME],
            ))

        # ── 3. 买卖盘失衡 ──
        if KIND_BIG_ORDER in strategies and bid1_v is not None and ask1_v is not None and max(bid1_v, ask1_v) > 0:
            imbalance = abs(bid1_v - ask1_v) / max(bid1_v, ask1_v)
            if imbalance >= imb_th:
                if bid1_v > ask1_v:
                    side = "买盘强势"
                    side_label = "买入"
                else:
                    side = "卖盘强势"
                    side_label = "卖出"
                signals.append(TSignal(
                    kind=KIND_BIG_ORDER,
                    symbol=symbol,
                    name=name,
                    ts_ms=now_ms,
                    price=now_price,
                    prev_close=prev_close,
                    change_pct=change_pct,
                    detail=(
                        f"{side} (买一 {bid1_v:g} 手 vs 卖一 {ask1_v:g} 手, "
                        f"失衡度 {imbalance:.0%}, 阈值 {imb_th:.0%}) — 倾向: {side_label}"
                    ),
                    severity=KIND_SEVERITY[KIND_BIG_ORDER],
                ))

        # ── 4-6. 新策略: VWAP均价回归 / RSI极值反转 / 分时区间突破 ──
        self._evaluate_t_strategies(
            symbol, raw, name, now_price, prev_close, change_pct, strategies, now_ms, signals
        )

    # 做T策略参数（进程内窗口口径，拉取轮次 ≈ 数秒，窗口仅用于日内相对比较）
    VWAP_REVERT_PCT = 1.5     # 现价偏离当日 VWAP 超过该百分比视为回归机会
    RSI_OVERSOLD = 25.0       # RSI 低于该值视为超卖
    RSI_OVERBOUGHT = 75.0     # RSI 高于该值视为超买
    RANGE_BREAK_PCT = 0.8     # 突破区间高/低点超过该百分比视为有效突破

    def _evaluate_t_strategies(
        self,
        symbol: str,
        raw: dict[str, Any],
        name: str,
        now_price: float,
        prev_close: float,
        change_pct: float,
        strategies: set[str],
        now_ms: int,
        signals: list[TSignal],
    ) -> None:
        hist = self._push_price(symbol, now_price)

        # ── 4. VWAP 均价回归 ──
        # 当日累计成交额/累计成交量 = 均价 (免费源 amount/volume 为当日累计, 直接可得)。
        # 价格大幅偏离均价后向均价回归是日内做T的经典高胜率形态:
        #   正偏离过大 → 冲高回落倾向 (卖出/做T卖点半径), 负偏离过大 → 超跌反弹倾向 (买入)。
        if KIND_VWAP_REVERT in strategies:
            amount = self._safe_float(raw.get("amount"))
            vol = self._safe_float(raw.get("volume"))
            if amount is not None and vol is not None and vol > 0:
                vwap = amount / vol
                if vwap > 0:
                    dev_pct = (now_price - vwap) / vwap * 100.0
                    if abs(dev_pct) >= self.VWAP_REVERT_PCT:
                        tendency = "卖出" if dev_pct > 0 else "买入"
                        signals.append(TSignal(
                            kind=KIND_VWAP_REVERT,
                            symbol=symbol,
                            name=name,
                            ts_ms=now_ms,
                            price=now_price,
                            prev_close=prev_close,
                            change_pct=change_pct,
                            detail=(
                                f"现价 {now_price:.2f} 偏离当日均价 {vwap:.2f} 达 {dev_pct:+.2f}% "
                                f"(阈值 ±{self.VWAP_REVERT_PCT:.1f}%) — 倾向: {tendency}"
                            ),
                            severity=KIND_SEVERITY[KIND_VWAP_REVERT],
                        ))

        # ── 5. RSI 极值反转 ──
        # 以拉取轮次为序列近似短周期 RSI (窗口≈最近 14 轮报价),
        # 极值反转在震荡市做T胜率较高: 超卖 → 买入倾向, 超买 → 卖出倾向。
        if KIND_RSI_EXTREME in strategies and len(hist) >= self.RSI_PERIOD + 1:
            rsi = self._rsi(list(hist))
            if rsi is not None and (rsi <= self.RSI_OVERSOLD or rsi >= self.RSI_OVERBOUGHT):
                tendency = "买入" if rsi <= self.RSI_OVERSOLD else "卖出"
                state = "超卖" if rsi <= self.RSI_OVERSOLD else "超买"
                signals.append(TSignal(
                    kind=KIND_RSI_EXTREME,
                    symbol=symbol,
                    name=name,
                    ts_ms=now_ms,
                    price=now_price,
                    prev_close=prev_close,
                    change_pct=change_pct,
                    detail=(
                        f"短周期 RSI({self.RSI_PERIOD}) = {rsi:.1f} ({state}, "
                        f"阈值 {self.RSI_OVERSOLD:.0f}/{self.RSI_OVERBOUGHT:.0f}) — 倾向: {tendency}"
                    ),
                    severity=KIND_SEVERITY[KIND_RSI_EXTREME],
                ))

        # ── 6. 分时区间突破 ──
        # 回看最近 N 轮价格形成日内小区间, 现价放量突破区间沿:
        #   突破上沿 → 顺势买入倾向; 跌破下沿 → 顺势卖出倾向。
        if KIND_RANGE_BREAK in strategies and len(hist) >= self.RANGE_LOOKBACK:
            window = list(hist)[:-1]  # 排除当前价, 用前 N 轮构成区间
            hi, lo = max(window), min(window)
            if hi > lo:
                if now_price >= hi * (1 + self.RANGE_BREAK_PCT / 100.0):
                    signals.append(TSignal(
                        kind=KIND_RANGE_BREAK,
                        symbol=symbol,
                        name=name,
                        ts_ms=now_ms,
                        price=now_price,
                        prev_close=prev_close,
                        change_pct=change_pct,
                        detail=(
                            f"突破近 {self.RANGE_LOOKBACK} 轮区间上沿 {hi:.2f} "
                            f"(现价 {now_price:.2f}, 阈值 +{self.RANGE_BREAK_PCT:.1f}%) — 倾向: 买入"
                        ),
                        severity=KIND_SEVERITY[KIND_RANGE_BREAK],
                    ))
                elif now_price <= lo * (1 - self.RANGE_BREAK_PCT / 100.0):
                    signals.append(TSignal(
                        kind=KIND_RANGE_BREAK,
                        symbol=symbol,
                        name=name,
                        ts_ms=now_ms,
                        price=now_price,
                        prev_close=prev_close,
                        change_pct=change_pct,
                        detail=(
                            f"跌破近 {self.RANGE_LOOKBACK} 轮区间下沿 {lo:.2f} "
                            f"(现价 {now_price:.2f}, 阈值 -{self.RANGE_BREAK_PCT:.1f}%) — 倾向: 卖出"
                        ),
                        severity=KIND_SEVERITY[KIND_RANGE_BREAK],
                    ))

    @classmethod
    def _rsi(cls, prices: list[float]) -> float | None:
        """Wilder 平滑近似: 用窗口内涨跌幅均值比计算短周期 RSI。"""
        diffs = [b - a for a, b in pairwise(prices)]
        gains = sum(d for d in diffs if d > 0)
        losses = -sum(d for d in diffs if d < 0)
        total = gains + losses
        if total <= 0:
            return None
        return gains / total * 100.0

    def _evaluate_hk(
        self,
        symbol: str,
        raw: dict[str, Any],
        name: str,
        price_th: float,
        now_ms: int,
        signals: list[TSignal],
    ) -> None:
        """港股: 仅评估涨跌幅阈值。

        字段: price / lastPrice (腾讯 hkquote; 均为字符串)。
        无 bid/ask: 买卖盘规则跳过。
        无 volume turnover: 量比规则跳过 (港股 amount 跨日波动剧烈, 量比无意义)。
        """
        price = self._safe_float(raw.get("price"))
        prev_close = self._safe_float(raw.get("lastPrice"))
        if price is None or prev_close is None or prev_close <= 0:
            return

        change_pct = (price - prev_close) / prev_close * 100.0

        if abs(change_pct) >= price_th:
            direction = "上涨" if change_pct > 0 else "下跌"
            signals.append(TSignal(
                kind=KIND_PRICE,
                symbol=symbol,
                name=name,
                ts_ms=now_ms,
                price=price,
                prev_close=prev_close,
                change_pct=change_pct,
                detail=f"现价 {price:.2f} ({direction} {abs(change_pct):.2f}%, 阈值 ±{price_th:.1f}%)",
                severity=KIND_SEVERITY[KIND_PRICE],
            ))


# ================================================================
# Cooldown 去重 + 历史落盘
# ================================================================

class CooldownTracker:
    """(symbol, kind) 维度 cooldown, 内存级, 进程重启即清空。"""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def allow(self, symbol: str, kind: str, cooldown_sec: int) -> bool:
        if cooldown_sec <= 0:
            return True
        key = (symbol, kind)
        now = time.time()
        with self._lock:
            last = self._last.get(key, 0.0)
            if now - last < cooldown_sec:
                return False
            self._last[key] = now
            return True


class SignalStore:
    """进程内最近 N 条信号 + JSONL 落盘 (供 /recent 端点读取)。"""

    def __init__(self, data_dir: Path, max_records: int = 200) -> None:
        self._lock = threading.Lock()
        self._max = max_records
        self._recent: deque[dict[str, Any]] = deque(maxlen=max_records)
        self._path = data_dir / "user_data" / "t_signals.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._write_count = 0

    def append(self, signal: TSignal) -> None:
        rec = signal.to_dict()
        with self._lock:
            self._recent.append(rec)
            try:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError as e:
                logger.warning("t_signal.jsonl write failed: %s", e)
            self._write_count += 1
            if self._write_count >= 50:
                self._write_count = 0
                self._prune_locked()

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """返回最近 limit 条, 时间倒序。"""
        with self._lock:
            items = list(self._recent)
        items.reverse()
        return items[: max(1, min(limit, self._max))]

    def _prune_locked(self) -> None:
        """(调用方持锁) 保留最近 max_records 行, 避免文件无限膨胀。"""
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        except OSError as e:
            logger.warning("t_signal.jsonl prune read failed: %s", e)
            return
        if len(lines) <= self._max:
            return
        kept = lines[-self._max:]
        try:
            self._path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        except OSError as e:
            logger.warning("t_signal.jsonl prune write failed: %s", e)


# ================================================================
# 持仓 T 列表 (独立配置, 每只独立 enabled)
# ================================================================


@dataclass
class PositionEntry:
    """持仓 T 标的条目。"""
    symbol: str
    name: str
    enabled: bool
    added_at: str                # ISO 时间戳, add 时填一次

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PositionTStore:
    """持仓 T 列表: data/user_data/position_t.json。

    与 watchlist 隔离:
      - watchlist 是用户浏览用的全集, 数量大, 不全做 T 监控;
      - position_t 是用户主动维护的「要做 T 的持仓」, 数量少, 持续监控。
    每个标的独立 enabled, 列表里 enabled=True 的标的才会被 poller 评估。

    数据拉取状态 (内存, 不持久化, 进程重启即清):
      - 'ok'      上轮 fetcher 返回了该 symbol 的 quote
      - 'failed'  上轮 enabled 且被请求但 fetcher 没返回 (代码错误/接口无数据/网络问题)
      - 'never'   从未拉过 (刚添加 / 全局未启动)
    """

    def __init__(self) -> None:
        # RLock: 同一线程可重入; 避免 add() / list_all() 等从内部调 _with_status() 时死锁
        self._lock = threading.RLock()
        self._path = settings.data_dir / "user_data" / "position_t.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[PositionEntry] = self._load()
        # 上次成功拉到数据的 ts_ms (供前端显示「最近一次拉到」)
        self._last_success_ms: dict[str, float] = {}
        # 每只标的的拉取状态: 'ok' / 'failed' / 'never'
        self._data_status: dict[str, str] = {}

    def _load(self) -> list[PositionEntry]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("position_t.json load failed: %s", e)
            return []
        if not isinstance(raw, dict) or not isinstance(raw.get("positions"), list):
            return []
        out: list[PositionEntry] = []
        for item in raw["positions"]:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            out.append(PositionEntry(
                symbol=symbol,
                name=str(item.get("name") or symbol),
                enabled=bool(item.get("enabled", True)),
                added_at=str(item.get("added_at") or ""),
            ))
        return out

    def _save_locked(self) -> None:
        payload = {"positions": [e.to_dict() for e in self._entries]}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def list_all(self) -> list[dict[str, Any]]:
        """所有持仓 T 标的 (含 enabled=False 的); 用于 UI 展示。

        每条额外附 data_status + last_success_ms (供前端显示诊断徽章)。
        """
        with self._lock:
            return [
                {
                    **e.to_dict(),
                    "data_status": self._data_status.get(e.symbol, "never"),
                    "last_success_ms": self._last_success_ms.get(e.symbol, 0.0),
                }
                for e in self._entries
            ]

    def list_enabled_symbols(self) -> list[str]:
        """仅返回 enabled=True 的标的 symbol, 用于 poller 评估。"""
        with self._lock:
            return [e.symbol for e in self._entries if e.enabled]

    def get(self, symbol: str) -> dict[str, Any] | None:
        sym = symbol.strip().upper()
        with self._lock:
            for e in self._entries:
                if e.symbol == sym:
                    return e.to_dict()
        return None

    def _with_status(self, entry_dict: dict[str, Any]) -> dict[str, Any]:
        """给返回的 entry dict 补上 data_status + last_success_ms (前端期望 0 值兜底)。"""
        sym = entry_dict["symbol"]
        with self._lock:
            return {
                **entry_dict,
                "data_status": self._data_status.get(sym, "never"),
                "last_success_ms": self._last_success_ms.get(sym, 0.0),
            }

    def add(self, symbol: str, name: str | None = None) -> dict[str, Any]:
        """添加标的; 重复则保留原条目, 返回现有。"""
        sym = symbol.strip().upper()
        if not sym:
            raise ValueError("symbol 不能为空")
        if not is_supported_symbol(sym):
            raise ValueError(f"不支持的市场或格式: {sym} (本模块仅支持 A 股 / 港股)")
        if is_hk_stock(sym):
            # 统一行情通道（free_realtime provider）暂无港股源，.HK 标的会一直
            # 停在 data_status=failed；这里显式告警而非静默失败。
            logger.warning(
                "t-signal: %s 为港股，统一行情通道暂不支持港股行情，"
                "该标的将无法产生信号（评估逻辑保留，待接入港股源后生效）",
                sym,
            )
        with self._lock:
            for e in self._entries:
                if e.symbol == sym:
                    return self._with_status(e.to_dict())  # 已存在, 返回原条目
            entry = PositionEntry(
                symbol=sym,
                name=name or sym,
                enabled=True,
                added_at=datetime.now().isoformat(timespec="seconds"),
            )
            self._entries.append(entry)
            self._save_locked()
            return self._with_status(entry.to_dict())

    def remove(self, symbol: str) -> bool:
        sym = symbol.strip().upper()
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.symbol != sym]
            self._data_status.pop(sym, None)
            self._last_success_ms.pop(sym, None)
            if len(self._entries) < before:
                self._save_locked()
                return True
        return False

    def set_enabled(self, symbol: str, enabled: bool) -> dict[str, Any] | None:
        sym = symbol.strip().upper()
        with self._lock:
            for e in self._entries:
                if e.symbol == sym:
                    e.enabled = bool(enabled)
                    self._save_locked()
                    return self._with_status(e.to_dict())
        return None

    def mark_tick_result(self, requested: set[str], succeeded: set[str], ts_ms: float) -> None:
        """每轮 poller 后调用: 更新每只标的的拉取状态。

        requested: 本轮请求过的所有 symbol (来自 enabled positions)
        succeeded: fetcher 实际返回了 quote 的 symbol
        ts_ms:    当前时间戳 (毫秒), 标记成功时间
        """
        with self._lock:
            for s in succeeded:
                self._data_status[s] = "ok"
                self._last_success_ms[s] = ts_ms
            for s in requested - succeeded:
                # 仅把 enabled 且拉失败的标 'failed' (不要覆盖刚加还没拉的)
                if self._data_status.get(s) != "never":
                    self._data_status[s] = "failed"

    def get_data_status(self, symbol: str) -> str:
        sym = symbol.strip().upper()
        with self._lock:
            return self._data_status.get(sym, "never")


# ================================================================
# 当日分时 K (easyquotation 每轮拉一次攒点, 不依赖项目 TickFlow 分钟 K)
# ================================================================


class IntradayKlineStore:
    """当日内 K 缓存 — 每个标的独立 deque, 按 ts_ms 排序。

    设计要点:
      - 不依赖项目 TickFlow 分钟 K (kline_minute parquet); 用户用免费行情时也能用。
      - 数据点由统一行情驱动链路写入 (on_realtime_df / _tick)；同分钟覆盖
        (行情 volume 是全日累计量, 覆盖为正确行为)。
      - 仅保留当日数据: 凌晨 0 点清空 (进程持续运行也能跨日); 进程重启后清空。
    """

    MAX_POINTS = 480  # 4 小时 * 60 分钟 * 2 (每 30s 一个点) 上限

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bars: dict[str, deque[dict[str, Any]]] = {}
        self._current_date = ""

    def append(self, symbol: str, ts_ms: int, price: float, volume: float | None = None) -> None:
        if price is None or price <= 0:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            if today != self._current_date:
                # 跨日: 清空, 重新开始
                self._bars.clear()
                self._current_date = today
            dq = self._bars.setdefault(symbol, deque(maxlen=self.MAX_POINTS))
            # 同一分钟已有更新就覆盖 (避免重复)
            bar = {
                "ts_ms": ts_ms,
                "price": float(price),
                "volume": float(volume) if volume is not None else None,
            }
            if dq and dq[-1]["ts_ms"] // 60000 == ts_ms // 60000:
                dq[-1] = bar
            else:
                dq.append(bar)

    def list_today(self, symbol: str) -> list[dict[str, Any]]:
        with self._lock:
            dq = self._bars.get(symbol)
            if not dq:
                return []
            return list(dq)


# ================================================================
# 飞书推送 (复用项目 webhook_adapter)
# ================================================================

class FeishuPusher:
    """通过 webhook_adapter.send_feishu_card 推送卡片; fire-and-forget。"""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="t-signal-feishu")

    def push(self, signals: list[TSignal]) -> None:
        if not signals:
            return
        # 按 symbol 分组: 同一标的同一轮多条信号合并到一张卡片, 避免刷屏
        grouped: dict[str, list[TSignal]] = {}
        for s in signals:
            grouped.setdefault(s.symbol, []).append(s)
        for sym, group in grouped.items():
            self._executor.submit(self._send_group, sym, group)

    def _send_group(self, symbol: str, group: list[TSignal]) -> None:
        # 延迟导入: 减少模块加载时耦合, 推送失败也不影响主流程
        try:
            from app.services import preferences
            from app.services.webhook_adapter import send_feishu_card
        except ImportError as e:
            logger.debug("webhook_adapter import failed: %s", e)
            return

        url = preferences.get_feishu_webhook_url()
        secret = preferences.get_feishu_webhook_secret()
        if not url:
            return  # 未配置就不推 (主流程不报错)

        first = group[0]
        ts = datetime.fromtimestamp(first.ts_ms / 1000).strftime("%H:%M:%S")
        sign = "+" if first.change_pct > 0 else ""
        mock_tag = "[MOCK] " if first.source == "mock" else ""
        title = f"{mock_tag}[T 信号] {first.name} ({symbol}) {sign}{first.change_pct:.2f}%"
        subtitle = f"{ts} · 现价 {first.price:.2f} · {KIND_LABELS[first.kind]}"
        lines = [
            f"- **{KIND_LABELS[s.kind]}** ({s.severity})\n  {s.detail}"
            for s in group
        ]
        body = "\n".join(lines)
        # 失败静默 (webhook_adapter 内部有重试 + WARNING 日志)
        send_feishu_card(url, title, subtitle, body, secret)


# ================================================================
# 后台轮询线程
# ================================================================

class TSignalPoller:
    """单例: 后台线程轮询持仓 T 列表 → 评估 → cooldown → 推送 → 落盘 + 攒日内 K。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._fetcher = QuoteFetcher()
        self._evaluator = SignalEvaluator()
        self._cooldown = CooldownTracker()
        self._store = SignalStore(settings.data_dir)
        self._positions = PositionTStore()
        self._intraday = IntradayKlineStore()
        self._pusher = FeishuPusher()
        # 今日信号计数会被内部轮询线程与调度器线程并发读写，用锁保护
        self._signals_lock = threading.Lock()
        self._signals_today = 0
        self._signals_today_date = ""

    @property
    def running(self) -> bool:
        return self._running

    def positions(self) -> PositionTStore:
        return self._positions

    def intraday(self) -> IntradayKlineStore:
        return self._intraday

    def signal_store(self) -> SignalStore:
        return self._store

    def _count_signals(self, n: int) -> None:
        """今日信号计数（跨日重置），线程安全。"""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._signals_lock:
            if self._signals_today_date != today:
                self._signals_today_date = today
                self._signals_today = 0
            self._signals_today += n

    def status(self) -> dict[str, Any]:
        cfg = load_config()
        all_positions = self._positions.list_all()
        with self._signals_lock:
            signals_today = self._signals_today
        return {
            "running": self._running,
            "interval_sec": cfg["interval_sec"],
            "last_fetch_ms": round(self._fetcher.last_fetch_ms, 0),
            "last_fetch_error": self._fetcher.last_error,
            "signals_today": signals_today,
            "position_count": len(all_positions),
            "enabled_position_count": sum(1 for p in all_positions if p.get("enabled")),
            "auto_push_to_feishu": cfg["auto_push_to_feishu"],
            "feishu_webhook_configured": self._feishu_configured(),
        }

    @staticmethod
    def _feishu_configured() -> bool:
        try:
            from app.services import preferences

            return bool(preferences.get_feishu_webhook_url())
        except Exception:
            return False

    def start(self) -> dict[str, Any]:
        """启动轮询线程: 不再需要全局 enabled (由每只标的 enabled 决定)。"""
        with self._lock:
            if self._running:
                return {"ok": True, "already_running": True}
            cfg = load_config()
            self._interval = max(1, int(cfg["interval_sec"]))
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="t-signal-poll")
            self._thread.start()
        logger.info("T signal poller started, interval=%ds", self._interval)
        return {"ok": True, "interval_sec": self._interval}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._running:
                return {"ok": True, "already_stopped": True}
            self._running = False
            thread = self._thread
        if thread:
            thread.join(timeout=10)
        self._thread = None
        logger.info("T signal poller stopped")
        return {"ok": True}

    def _loop(self) -> None:
        """主循环: 间隔由 config.interval_sec 决定, 退出条件 self._running=False。

        单标的 fetch 失败时继续下一轮 (失败静默), 不让单点拉取失败杀死轮询。
        """
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.warning("t_signal poll tick failed: %s", e)
            # 可中断 sleep: 每 200ms 检查一次停止标志
            slept = 0.0
            while slept < self._interval and self._running:
                time.sleep(0.2)
                slept += 0.2

    def _tick(self) -> None:
        cfg = load_config()
        if cfg["trading_hours_only"] and not (is_trading_now("cn") or is_trading_now("hk")):
            return

        # 持仓 T 列表 (仅 enabled=True)
        symbols = self._positions.list_enabled_symbols()
        if not symbols:
            return

        quotes = self._fetcher.fetch(symbols)
        now_ms = int(time.time() * 1000)

        # 标记每只标的的拉取状态 (失败也能被前端感知, 避免静默无数据)
        self._positions.mark_tick_result(
            requested=set(symbols),
            succeeded=set(quotes.keys()),
            ts_ms=now_ms,
        )

        if not quotes:
            return  # 整批失败, 但 mark_tick_result 已记录失败状态, 可以 return

        # ── 攒日内 K: 每个标的最后一个价格作为最新点 ──
        # 同一分钟内的多次拉取覆盖到同一个 bar（volume 为全日累计量，覆盖即最新），与 _store 同模式
        for sym, raw in quotes.items():
            now_price = self._safe_float(raw.get("now"))
            if now_price is None and sym.endswith(".HK"):
                now_price = self._safe_float(raw.get("price"))
            volume = self._safe_float(raw.get("volume"))
            if now_price is not None:
                self._intraday.append(sym, now_ms, now_price, volume)

        signals = self._evaluator.evaluate(quotes, cfg, now_ms)

        # cooldown 过滤 + 落盘 + 计数
        cooldown = int(cfg["cooldown_seconds"])
        accepted: list[TSignal] = []
        for s in signals:
            if self._cooldown.allow(s.symbol, s.kind, cooldown):
                accepted.append(s)
                self._store.append(s)

        # 今日计数
        self._count_signals(len(accepted))

        # 推送 (异步 fire-and-forget; 受 auto_push_to_feishu 控制)
        if accepted and cfg["auto_push_to_feishu"]:
            self._pusher.push(accepted)

    # ── 统一行情模块接入 ───────────────────────────────────────
    # 当 free_realtime 模块的 position_t 开关开启时，由统一调度器驱动评估，
    # 不再使用内部 _tick 自轮询路径（避免重复拉取与节奏冲突）。
    def on_realtime_df(self, df, source: str = "real") -> None:
        """从统一行情调度器接收标准化 DataFrame 并评估三件套信号。

        与 _tick 共用评估/落盘/推送逻辑，仅替换数据源 (调度器→本模块)。
        source: "real"（主调度器）或 "mock"（测试页 mock 循环）。
        线程：由调度器后台线程调用，无需另起线程。
        """
        if df is None or df.is_empty():
            return
        cfg = load_config()
        # 与 _tick 不同：trading_hours_only 由统一调度器负责，此处不再二次过滤
        # 仅校验：统一行情模块下 position_t 全局开关必须开启
        if not fr_config.get_module_enabled("position_t"):
            return

        # 转换：标准化 DataFrame → {symbol: raw_dict}（与 _tick 输入格式一致）
        # 同时只处理本模块持仓 T 列表中的标的
        enabled_symbols = set(self._positions.list_enabled_symbols())
        if not enabled_symbols:
            return
        quotes: dict[str, dict[str, Any]] = {}
        # mock tick 的 ts_ms 是虚拟时钟时间，分时攒点用它；real 用真实挂钟
        first_ts_ms = 0
        for row in df.iter_rows(named=True):
            sym = str(row.get("symbol") or "")
            if sym not in enabled_symbols:
                continue
            if not first_ts_ms:
                first_ts_ms = int(row.get("ts_ms") or 0)
            quotes[sym] = {
                "now": row.get("last_price"),
                # 昨收: 评估器涨跌幅口径依赖 prev_close; 缺失会导致全部信号被跳过
                "close": row.get("prev_close"),
                "name": row.get("name"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "volume": row.get("volume"),
                "amount": row.get("amount"),
                # 大单买卖盘（三件套原依赖 easyquotation 字段）
                "bid1_price": row.get("bid1_price"),
                "ask1_price": row.get("ask1_price"),
                "bid1_volume": row.get("bid1_volume"),
                "ask1_volume": row.get("ask1_volume"),
            }
        if not quotes:
            return
        # mock tick 带虚拟时钟 ts_ms，分时攒点用它（与真实挂钟区分，曲线时间轴正确）
        now_ms = first_ts_ms if (source == "mock" and first_ts_ms) else int(time.time() * 1000)
        self._positions.mark_tick_result(
            requested=enabled_symbols,
            succeeded=set(quotes.keys()),
            ts_ms=now_ms,
        )
        # 日内 K 攒点（与 _tick 同模式：同分钟覆盖）
        for sym, raw in quotes.items():
            now_price = _safe_float(raw.get("now"))
            volume = _safe_float(raw.get("volume"))
            if now_price is not None:
                self._intraday.append(sym, now_ms, now_price, volume)

        signals = self._evaluator.evaluate(quotes, cfg, now_ms)
        # mock 链路：虚拟行情产生的信号打 source 标记（落库/推送均可区分）
        if source != "real":
            for s in signals:
                s.source = source
        cooldown = int(cfg["cooldown_seconds"])
        accepted: list[TSignal] = []
        for s in signals:
            if self._cooldown.allow(s.symbol, s.kind, cooldown):
                accepted.append(s)
                self._store.append(s)

        # 今日计数
        self._count_signals(len(accepted))

        if accepted and cfg["auto_push_to_feishu"]:
            self._pusher.push(accepted)


# ── 模块单例 ─────────────────────────────────────────────────────
POLLER = TSignalPoller()


# ================================================================
# FastAPI 路由 (L2 扩展点: include_router)
# ================================================================

router = APIRouter(prefix="/api/custom/t-signal", tags=["custom-t-signal"])


@router.get("/status")
def get_status() -> dict[str, Any]:
    """运行状态: 是否在跑、轮询间隔、最近拉取时间、今日信号数等。"""
    return POLLER.status()


@router.post("/start")
def post_start() -> dict[str, Any]:
    """启动后台轮询; 没有 enabled 的持仓 T 标的也可以启动 (加标后即生效)。"""
    try:
        return POLLER.start()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/stop")
def post_stop() -> dict[str, Any]:
    """停止后台轮询 (保留 cooldown + 历史 + 持仓 T 列表)。"""
    return POLLER.stop()


@router.get("/config")
def get_config() -> dict[str, Any]:
    """当前配置 (含默认值)。"""
    return load_config()


@router.put("/config")
def put_config(payload: dict[str, Any]) -> dict[str, Any]:
    """更新配置, 返回更新后的完整配置。未知字段被丢弃, 非法值 400。"""
    try:
        cleaned = save_config(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return cleaned


@router.get("/recent")
def get_recent(limit: int = 50, symbol: str | None = None) -> dict[str, Any]:
    """最近 N 条信号, 时间倒序。可选 ?symbol= 过滤单票。"""
    items = POLLER._store.list_recent(min(limit, 500))
    if symbol:
        sym = symbol.strip().upper()
        items = [it for it in items if str(it.get("symbol") or "").upper() == sym]
    return {"items": items, "count": len(items)}


# ── 持仓 T 列表 CRUD ──────────────────────────────────────────


@router.get("/positions")
def list_positions() -> dict[str, Any]:
    """持仓 T 列表 (含 enabled 状态)。"""
    items = POLLER.positions().list_all()
    return {"items": items, "count": len(items)}


@router.post("/positions")
def add_position(payload: dict[str, Any]) -> dict[str, Any]:
    """添加持仓 T 标的: {"symbol": "300308.SZ", "name": "中际旭创"?}。

    重复 symbol 直接返回现有条目 (幂等); 不支持的市场返回 400。
    """
    symbol = str(payload.get("symbol") or "").strip()
    name = payload.get("name")
    name = str(name).strip() if isinstance(name, str) and name.strip() else None
    try:
        entry = POLLER.positions().add(symbol, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return entry


@router.delete("/positions/{symbol}")
def remove_position(symbol: str) -> dict[str, Any]:
    """移除持仓 T 标的。"""
    ok = POLLER.positions().remove(symbol)
    if not ok:
        raise HTTPException(status_code=404, detail=f"持仓 T 中不存在: {symbol}")
    return {"ok": True, "symbol": symbol.strip().upper()}


@router.put("/positions/{symbol}")
def update_position(symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
    """更新单只标的 enabled 状态: {"enabled": true|false}。"""
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled 必须是 bool")
    entry = POLLER.positions().set_enabled(symbol, enabled)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"持仓 T 中不存在: {symbol}")
    return entry


@router.get("/positions/{symbol}/intraday")
def get_position_intraday(symbol: str) -> dict[str, Any]:
    """单票当日内分时点 (来自 IntradayKlineStore, 当日数据)。

    数据粒度: 约每 3s 一个点 (取决于 interval_sec); 跨分钟时合并到同一分钟。
    """
    sym = symbol.strip().upper()
    bars = POLLER.intraday().list_today(sym)
    return {"symbol": sym, "bars": bars, "count": len(bars)}


@router.get("/positions/{symbol}/signals")
def get_position_signals(symbol: str, limit: int = 100) -> dict[str, Any]:
    """单票当日信号历史 (来自 SignalStore 进程内队列, 当日数据)。"""
    items = POLLER.signal_store().list_recent(limit=500)
    sym = symbol.strip().upper()
    items = [it for it in items if str(it.get("symbol") or "").upper() == sym]
    return {"symbol": sym, "items": items, "count": len(items)}


# ================================================================
# 扩展注册 (L2 标准入口)
# ================================================================

EXTENSION_ID = "easyquotation.t_signal"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar: BackendExtensionRegistrar) -> None:
    """L2 扩展注册: 仅挂载 API 路由。"""
    registrar.include_router(router)


def startup(context: ExtensionContext) -> None:
    """可选启动钩子: 接入免费实时行情统一调度器。

    持仓T 仅由统一行情驱动（real 主调度器 + mock 测试页循环），
    原 easyquotation 自轮询回退路径已移除；未开启统一行情模块时
    持仓T 页面会提示先开启。
    """
    del context  # 未使用, 仅签名要求
    try:
        from app.services.free_realtime import config as fr_config
        from app.services.free_realtime import scheduler as fr_scheduler

        # 无条件注册回调与采集器，开关在运行时判断：
        # 此前仅在启动时两开关同时开启才注册，用户后开开关（或后启用持仓T标的）
        # 会导致调度器/测试页 mock 循环永远收不到本模块标的，分时图无数据。
        fr_scheduler.FreeRealtimeScheduler.register_module_callback(
            POLLER.on_realtime_df
        )
        scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()

        def _position_t_collector() -> list[str]:
            # 运行时校验：仅看模块开关，不看全局开关。
            # 全局开关只约束 real 调度器主循环（_tick 自查）；mock 测试页循环
            # 设计上绕过全局开关，若 collector 也卡全局开关，持仓T标的会被漏掉。
            if not fr_config.get_module_enabled("position_t"):
                return []
            return list(POLLER.positions().list_enabled_symbols())

        scheduler.register_collector(_position_t_collector)
        logger.info("t_signal attached to free_realtime scheduler")
    except Exception as e:
        logger.warning("t_signal attach to free_realtime failed: %s", e)