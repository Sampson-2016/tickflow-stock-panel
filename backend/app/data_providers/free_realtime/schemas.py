"""A 股免费实时行情标准化 schema 与列定义。

设计要点（对齐 CONTRIBUTING.md §3.1 单位口径）:
  - change_pct 必须是小数制（0.0366 表示 3.66%），新浪/腾讯/东财返回的是
    百分数，normalizer 必须显式 /100，禁止"数值<1就乘100"启发式。
  - volume 统一为"手"（1 手 = 100 股）。新浪返回"股"需 /100，腾讯/东财
    返回"手"直接用。
  - amount 统一为"元"。
  - ts_ms 是毫秒级行情时间戳（北京时间），用行情自带时间，非服务端时间。
  - asset_type 仅供实时报价展示与 ma5 计算使用，存储路由不依赖此判断
    （CONTRIBUTING.md §3.2 禁止仅凭代码格式判断分钟K资产类型）。
"""
from __future__ import annotations

import polars as pl

# 标准化列定义（顺序即输出顺序）
FREE_REALTIME_COLUMNS: list[str] = [
    "symbol",
    "name",
    "asset_type",
    "last_price",
    "open",
    "prev_close",
    "high",
    "low",
    "change_pct",
    "volume",
    "amount",
    "bid1_price",
    "ask1_price",
    "bid1_volume",
    "ask1_volume",
    "source",
    "ts_ms",
    "trade_date",
]

# Polars schema（用于 provider 输出校验）
FREE_REALTIME_SCHEMA: pl.Schema = pl.Schema({
    "symbol": pl.Utf8,
    "name": pl.Utf8,
    "asset_type": pl.Utf8,
    "last_price": pl.Float64,
    "open": pl.Float64,
    "prev_close": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "change_pct": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "bid1_price": pl.Float64,
    "ask1_price": pl.Float64,
    "bid1_volume": pl.Float64,
    "ask1_volume": pl.Float64,
    "source": pl.Utf8,
    "ts_ms": pl.Int64,
    "trade_date": pl.Date,
})

# 空 schema DataFrame（provider 无数据时返回）
def empty_realtime_df() -> pl.DataFrame:
    """返回符合 FREE_REALTIME_SCHEMA 的空 DataFrame。"""
    return pl.DataFrame(schema=FREE_REALTIME_SCHEMA)


def infer_asset_type(symbol: str) -> str:
    """从 symbol 启发式推断资产类型（stock/etf/index）。

    仅用于实时报价展示与 ma5 计算，不参与存储路由。判断规则:
      - 000xxx.SH → index（上证系列指数，如 000001.SH 上证综指）
      - 399xxx.SZ → index（深证系列指数，如 399001.SZ 深成指）
      - 510/511/512/513/515/516/517/518/501/502 开头 → etf（沪市 ETF）
      - 159 开头 → etf（深市 ETF）
      - 其他 → stock

    注意: 000001.SZ 是平安银行（股票），000001.SH 是上证指数，必须结合
    交易所后缀判断，不能仅凭代码前缀。
    """
    s = symbol.strip().upper()
    code = s.split(".")[0]
    if s.endswith(".SH") and code.startswith("000"):
        return "index"
    if s.endswith(".SZ") and code.startswith("399"):
        return "index"
    if code.startswith(("510", "511", "512", "513", "515", "516", "517", "518", "501", "502")):
        return "etf"
    if code.startswith("159"):
        return "etf"
    return "stock"
