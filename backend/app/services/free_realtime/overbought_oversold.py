"""超买超卖评估器 — 基于实时价偏离 5 日线百分比。

口径（与用户最新确认一致）:
  - MA5 = (前 4 个交易日 raw_close + 当日实时价) / 5
  - 偏离 = (last - ma5) / ma5
  - 向上偏离 ≥ overbought_pct → 超买
  - 向下偏离 ≥ oversold_pct → 超卖

注:
  - raw_close 来源: KlineRepository.get_daily(symbol, ...) 的 raw_close 列
  - 跨夜/早盘前 4 个交易日可能不足；不足时返回 None（不评估）
  - 仅评估 A 股标的；ETF/指数不参与
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import polars as pl

from app.services.free_realtime import config as fr_config

logger = logging.getLogger(__name__)


def is_a_stock_candidate(symbol: str) -> bool:
    """仅 A 股个股（不含 ETF/指数）。"""
    from app.data_providers.free_realtime.schemas import infer_asset_type

    try:
        if not symbol.endswith((".SH", ".SZ", ".BJ")):
            return False
        return infer_asset_type(symbol) == "stock"
    except Exception:
        return False


@dataclass
class OverboughtSignal:
    symbol: str
    name: str
    ts_ms: int
    last_price: float
    ma5: float
    deviation_pct: float   # 正=超买方向, 负=超卖方向
    direction: str          # "overbought" / "oversold"
    threshold_pct: float


def _load_recent_closes(repository, symbol: str, n: int = 4) -> list[float]:
    """从仓库取最近 n 个交易日的 raw_close（不含今日）。"""
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=15)  # 多取一些防止停牌
    try:
        df: pl.DataFrame = repository.get_daily(
            symbol, start, end, columns=["date", "raw_close"]
        )
    except Exception as e:
        logger.warning("overbought_oversold load %s failed: %s", symbol, e)
        return []
    if df.is_empty() or "raw_close" not in df.columns:
        return []
    series = df.sort("date").tail(n)["raw_close"].to_list()
    return [v for v in series if v is not None and v > 0]


def evaluate(
    df: pl.DataFrame,
    repository,
    name_map: dict[str, str] | None = None,
) -> list[OverboughtSignal]:
    """对一批实时报价评估超买超卖规则。

    Args:
        df: FREE_REALTIME_SCHEMA DataFrame
        repository: KlineRepository（用于取最近 N 日 raw_close）
        name_map: 可选的 symbol→name 映射；缺省用 df 的 name 列

    Returns:
        命中超买/超卖规则的信号列表。
    """
    if df.is_empty():
        return []
    # 全局阈值作为兜底默认；个股 overbought_pct/oversold_pct 优先覆盖
    default_ob = fr_config.get_overbought_pct()
    default_os = fr_config.get_oversold_pct()
    # 个股阈值表：symbol -> (overbought_pct, oversold_pct)
    per_symbol: dict[str, tuple[float, float]] = {}
    for it in fr_config.list_overbought_symbols():
        ob = it.get("overbought_pct")
        os_ = it.get("oversold_pct")
        per_symbol[it["symbol"]] = (
            float(ob) if ob is not None else default_ob,
            float(os_) if os_ is not None else default_os,
        )
    disabled = set(fr_config.list_disabled_symbols("overbought_oversold"))
    if not fr_config.get_module_enabled("overbought_oversold"):
        return []

    out: list[OverboughtSignal] = []
    for row in df.iter_rows(named=True):
        symbol = str(row.get("symbol") or "")
        if not is_a_stock_candidate(symbol):
            continue
        if symbol in disabled:
            continue
        last = row.get("last_price")
        ts_ms = int(row.get("ts_ms") or 0)
        if last is None or last <= 0:
            continue
        closes = _load_recent_closes(repository, symbol, 4)
        if len(closes) < 4:
            continue
        ma5 = (sum(closes) + float(last)) / 5.0
        if ma5 <= 0:
            continue
        deviation = (float(last) - ma5) / ma5 * 100.0  # 百分比
        name = (name_map or {}).get(symbol) or str(row.get("name") or symbol)
        # 个股阈值优先；不在个股清单里的标的用全局默认（兼容持仓T/自选股流入的标的）
        overbought, oversold = per_symbol.get(symbol, (default_ob, default_os))
        if deviation >= overbought:
            out.append(OverboughtSignal(
                symbol=symbol, name=name, ts_ms=ts_ms,
                last_price=float(last), ma5=ma5,
                deviation_pct=deviation, direction="overbought",
                threshold_pct=overbought,
            ))
        elif -deviation >= oversold:
            out.append(OverboughtSignal(
                symbol=symbol, name=name, ts_ms=ts_ms,
                last_price=float(last), ma5=ma5,
                deviation_pct=deviation, direction="oversold",
                threshold_pct=oversold,
            ))
    return out


def to_dict(sig: OverboughtSignal) -> dict[str, Any]:
    return {
        "symbol": sig.symbol,
        "name": sig.name,
        "ts_ms": sig.ts_ms,
        "last_price": sig.last_price,
        "ma5": sig.ma5,
        "deviation_pct": sig.deviation_pct,
        "direction": sig.direction,
        "threshold_pct": sig.threshold_pct,
    }
