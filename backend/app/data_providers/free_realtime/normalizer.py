"""三家免费实时行情源的标准化逻辑。

每家源的字段映射与单位转换都集中在此，是 provider 层唯一能出现供应商
字段名的地方。上层调度/存储/信号评估只认 FREE_REALTIME_SCHEMA。

关键单位转换（P0 风险点，单测必须覆盖）:
  - 新浪 volume 是"股"，需 /100 转"手"
  - 东财价格字段（f43/f44/f45/f46/f60）是"分"，需 /100 转"元"
  - 东财 f170 涨跌幅是百分数（0.48 表示 0.48%），需 /100 转小数制
  - 新浪/腾讯无现成涨跌幅，需自算 (last-prev_close)/prev_close
  - 新浪返回 GBK 编码，provider 层负责解码后传文本进来
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

import polars as pl

from app.data_providers.free_realtime.schemas import (
    FREE_REALTIME_SCHEMA,
    empty_realtime_df,
    infer_asset_type,
)
from app.markets import CN_TZ


def _to_float(val, default: float | None = None) -> float | None:
    """安全转 float。None/空串/"--" 返回 default。"""
    if val is None:
        return default
    if isinstance(val, str):
        val = val.strip()
        if val in ("", "--", "---"):
            return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _change_pct(last: float | None, prev_close: float | None) -> float | None:
    """自算涨跌幅（小数制）。prev_close<=0 或缺失返回 None。"""
    if last is None or prev_close is None or prev_close <= 0:
        return None
    return (last - prev_close) / prev_close


def _parse_sina_time(date_str: str, time_str: str) -> tuple[int, date] | None:
    """新浪日期+时间 → (毫秒, 交易日)。

    date_str: "2024-01-02", time_str: "15:00:00"
    """
    if not date_str or not time_str:
        return None
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    dt = dt.replace(tzinfo=CN_TZ)
    return int(dt.timestamp() * 1000), dt.date()


def _parse_tencent_time(ts: str) -> tuple[int, date] | None:
    """腾讯时间戳 → (毫秒, 交易日)。

    ts: "20240102150000"
    """
    if not ts or len(ts) < 14:
        return None
    try:
        dt = datetime.strptime(ts[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    dt = dt.replace(tzinfo=CN_TZ)
    return int(dt.timestamp() * 1000), dt.date()


# ── 符号转换 ───────────────────────────────────────────────────
def sina_key_to_symbol(key: str) -> str:
    """sh600000 → 600000.SH；sz000001 → 000001.SZ。"""
    k = key.lower()
    if k.startswith(("sh", "sz", "bj")):
        return f"{k[2:]}.{k[:2].upper()}"
    return key


def project_symbol_to_sina_key(symbol: str) -> str:
    """600000.SH → sh600000。"""
    code, _, ex = symbol.partition(".")
    return f"{ex.lower()}{code}"


def project_symbol_to_tencent_key(symbol: str) -> str:
    """600000.SH → sh600000（与新浪 key 同形）。"""
    code, _, ex = symbol.partition(".")
    return f"{ex.lower()}{code}"


def tencent_key_to_symbol(key: str) -> str:
    """sh600000 → 600000.SH。"""
    return sina_key_to_symbol(key)


def project_symbol_to_eastmoney_secid(symbol: str) -> str:
    """600000.SH → 1.600000；000001.SZ → 0.000001；920344.BJ → 0.920344。

    东财 secid 前缀：沪市=1，深市=0，北交所=0（实测）。
    """
    code, _, ex = symbol.partition(".")
    if ex == "SH":
        return f"1.{code}"
    if ex in ("SZ", "BJ"):
        return f"0.{code}"
    return f"1.{code}"


# ── 新浪 normalizer ─────────────────────────────────────────────
# var hq_str_sh600000="名称,今开,昨收,最新,最高,最低,买1价,卖1价,成交量(股),成交额,
#   买1量(股),买1价,买2量,买2价,...,买5量,买5价,卖1量,卖1价,...,卖5量,卖5价,日期,时间,状态";
_SINA_RE = re.compile(r'hq_str_(\w{2,10})="([^"]*)"')

# 新浪字段索引
_SINA_NAME = 0
_SINA_OPEN = 1
_SINA_PREV_CLOSE = 2
_SINA_LAST = 3
_SINA_HIGH = 4
_SINA_LOW = 5
_SINA_BID1_PRICE = 6
_SINA_ASK1_PRICE = 7
_SINA_VOLUME = 8          # 股
_SINA_AMOUNT = 9
_SINA_BID1_VOLUME = 10    # 股（买1量）
_SINA_ASK1_VOLUME = 20    # 股（卖1量，在五档买盘之后）
_SINA_DATE = 30
_SINA_TIME = 31


def normalize_sina(raw_text: str, source: str = "sina") -> pl.DataFrame:
    """新浪 hq.sinajs.cn 响应 → 标准化 DataFrame。

    输入是 GBK 解码后的文本。成交量字段是"股"，这里 /100 转"手"。
    涨跌幅自算（新浪不返回涨跌幅字段）。
    """
    if not raw_text:
        return empty_realtime_df()
    rows: list[dict] = []
    for m in _SINA_RE.finditer(raw_text):
        key, payload = m.group(1), m.group(2)
        if not payload:
            continue
        f = payload.split(",")
        if len(f) < 10:
            continue
        symbol = sina_key_to_symbol(key)
        name = f[_SINA_NAME]
        open_p = _to_float(f[_SINA_OPEN])
        prev_close = _to_float(f[_SINA_PREV_CLOSE])
        last = _to_float(f[_SINA_LAST])
        high = _to_float(f[_SINA_HIGH])
        low = _to_float(f[_SINA_LOW])
        bid1_price = _to_float(f[_SINA_BID1_PRICE])
        ask1_price = _to_float(f[_SINA_ASK1_PRICE])
        volume_shares = _to_float(f[_SINA_VOLUME])
        amount = _to_float(f[_SINA_AMOUNT])
        bid1_vol_shares = _to_float(f[_SINA_BID1_VOLUME]) if len(f) > _SINA_BID1_VOLUME else None
        ask1_vol_shares = _to_float(f[_SINA_ASK1_VOLUME]) if len(f) > _SINA_ASK1_VOLUME else None
        date_str = f[_SINA_DATE] if len(f) > _SINA_DATE else ""
        time_str = f[_SINA_TIME] if len(f) > _SINA_TIME else ""
        ts_result = _parse_sina_time(date_str, time_str)
        ts_ms = ts_result[0] if ts_result else None
        trade_date = ts_result[1] if ts_result else None
        # 股 → 手
        volume = volume_shares / 100 if volume_shares is not None else None
        bid1_vol = bid1_vol_shares / 100 if bid1_vol_shares is not None else None
        ask1_vol = ask1_vol_shares / 100 if ask1_vol_shares is not None else None
        rows.append({
            "symbol": symbol,
            "name": name,
            "asset_type": infer_asset_type(symbol),
            "last_price": last,
            "open": open_p,
            "prev_close": prev_close,
            "high": high,
            "low": low,
            "change_pct": _change_pct(last, prev_close),
            "volume": volume,
            "amount": amount,
            "bid1_price": bid1_price,
            "ask1_price": ask1_price,
            "bid1_volume": bid1_vol,
            "ask1_volume": ask1_vol,
            "source": source,
            "ts_ms": ts_ms,
            "trade_date": trade_date,
        })
    if not rows:
        return empty_realtime_df()
    return pl.DataFrame(rows, schema=FREE_REALTIME_SCHEMA)


# ── 腾讯 normalizer ─────────────────────────────────────────────
# v_sh600000="1~名称~代码~最新~昨收~今开~最高~最低~买1价~卖1价~成交量(手)~成交额~
#   买1量~买1价~买2量~买2价~...~买5量~买5价~卖1量~卖1价~...~卖5量~卖5价~时间戳~...";
_TENCENT_RE = re.compile(r'v_(\w{2,10})="([^"]*)"')

# 腾讯字段索引
_TENCENT_NAME = 1
_TENCENT_CODE = 2
_TENCENT_LAST = 3
_TENCENT_PREV_CLOSE = 4
_TENCENT_OPEN = 5
_TENCENT_HIGH = 6
_TENCENT_LOW = 7
_TENCENT_BID1_PRICE = 8
_TENCENT_ASK1_PRICE = 9
_TENCENT_VOLUME = 10       # 手
_TENCENT_AMOUNT = 11
_TENCENT_BID1_VOLUME = 12  # 手（买1量）
_TENCENT_ASK1_VOLUME = 22  # 手（卖1量，在五档买盘之后）
_TENCENT_TIME = 45         # "20240102150000"（位置不固定，45 为常见值）


def normalize_tencent(raw_text: str, source: str = "tencent") -> pl.DataFrame:
    """腾讯 qt.gtimg.cn 响应 → 标准化 DataFrame。

    输入是 UTF-8 文本。成交量字段已是"手"，无需转换。
    涨跌幅自算（腾讯不直接返回涨跌幅）。
    """
    if not raw_text:
        return empty_realtime_df()
    rows: list[dict] = []
    for m in _TENCENT_RE.finditer(raw_text):
        key, payload = m.group(1), m.group(2)
        if not payload:
            continue
        f = payload.split("~")
        if len(f) < 11:
            continue
        symbol = tencent_key_to_symbol(key)
        name = f[_TENCENT_NAME]
        last = _to_float(f[_TENCENT_LAST])
        prev_close = _to_float(f[_TENCENT_PREV_CLOSE])
        open_p = _to_float(f[_TENCENT_OPEN])
        high = _to_float(f[_TENCENT_HIGH])
        low = _to_float(f[_TENCENT_LOW])
        bid1_price = _to_float(f[_TENCENT_BID1_PRICE])
        ask1_price = _to_float(f[_TENCENT_ASK1_PRICE])
        volume = _to_float(f[_TENCENT_VOLUME])          # 手
        amount = _to_float(f[_TENCENT_AMOUNT])
        bid1_vol = _to_float(f[_TENCENT_BID1_VOLUME]) if len(f) > _TENCENT_BID1_VOLUME else None
        ask1_vol = _to_float(f[_TENCENT_ASK1_VOLUME]) if len(f) > _TENCENT_ASK1_VOLUME else None
        time_str = f[_TENCENT_TIME] if len(f) > _TENCENT_TIME else ""
        ts_result = _parse_tencent_time(time_str)
        ts_ms = ts_result[0] if ts_result else None
        trade_date = ts_result[1] if ts_result else None
        rows.append({
            "symbol": symbol,
            "name": name,
            "asset_type": infer_asset_type(symbol),
            "last_price": last,
            "open": open_p,
            "prev_close": prev_close,
            "high": high,
            "low": low,
            "change_pct": _change_pct(last, prev_close),
            "volume": volume,
            "amount": amount,
            "bid1_price": bid1_price,
            "ask1_price": ask1_price,
            "bid1_volume": bid1_vol,
            "ask1_volume": ask1_vol,
            "source": source,
            "ts_ms": ts_ms,
            "trade_date": trade_date,
        })
    if not rows:
        return empty_realtime_df()
    return pl.DataFrame(rows, schema=FREE_REALTIME_SCHEMA)


# ── 东财 normalizer ─────────────────────────────────────────────
# push2.eastmoney.com 返回 JSON，价格字段是"分"（整数），需 /100 转元。
# f170 涨跌幅是百分数（0.48 表示 0.48%），需 /100 转小数制。

# 东财字段名
_EM_F_LAST = "f43"          # 最新价（分）
_EM_F_HIGH = "f44"           # 最高（分）
_EM_F_LOW = "f45"            # 最低（分）
_EM_F_OPEN = "f46"           # 今开（分）
_EM_F_VOLUME = "f47"         # 成交量（手）
_EM_F_AMOUNT = "f48"         # 成交额（元）
_EM_F_CODE = "f57"           # 代码
_EM_F_NAME = "f58"           # 名称
_EM_F_PREV_CLOSE = "f60"     # 昨收（分）
_EM_F_CHANGE_PCT = "f170"   # 涨跌幅（百分数）
_EM_F_BID1_VOLUME = "f19"   # 买1量（手）
_EM_F_BID1_PRICE = "f20"     # 买1价（分）
_EM_F_ASK1_VOLUME = "f17"   # 卖1量（手）
_EM_F_ASK1_PRICE = "f18"     # 卖1价（分）
# f86 市场代码（1=沪，0=深），不稳定，这里不依赖


def _em_price_to_yuan(val, default: float | None = None) -> float | None:
    """东财价格字段（分）→ 元。"""
    f = _to_float(val, default=None)
    if f is None:
        return default
    return f / 100


def _em_change_pct(val, last, prev_close, default: float | None = None) -> float | None:
    """东财涨跌幅优先用 f170，缺失则自算。

    f170 是百分数（0.48 = 0.48%），需 /100 转小数制。
    """
    f = _to_float(val, default=None)
    if f is not None:
        return f / 100
    return _change_pct(last, prev_close) if default is None else default


def _em_build_row(item: dict, symbol: str, source: str) -> dict:
    """单条东财数据 → 标准化 row dict。"""
    last = _em_price_to_yuan(item.get(_EM_F_LAST))
    prev_close = _em_price_to_yuan(item.get(_EM_F_PREV_CLOSE))
    return {
        "symbol": symbol,
        "name": item.get(_EM_F_NAME) or symbol,
        "asset_type": infer_asset_type(symbol),
        "last_price": last,
        "open": _em_price_to_yuan(item.get(_EM_F_OPEN)),
        "prev_close": prev_close,
        "high": _em_price_to_yuan(item.get(_EM_F_HIGH)),
        "low": _em_price_to_yuan(item.get(_EM_F_LOW)),
        "change_pct": _em_change_pct(item.get(_EM_F_CHANGE_PCT), last, prev_close),
        "volume": _to_float(item.get(_EM_F_VOLUME)),      # 手
        "amount": _to_float(item.get(_EM_F_AMOUNT)),      # 元
        "bid1_price": _em_price_to_yuan(item.get(_EM_F_BID1_PRICE)),
        "ask1_price": _em_price_to_yuan(item.get(_EM_F_ASK1_PRICE)),
        "bid1_volume": _to_float(item.get(_EM_F_BID1_VOLUME)),   # 手
        "ask1_volume": _to_float(item.get(_EM_F_ASK1_VOLUME)),  # 手
        "source": source,
        "ts_ms": None,   # 东财单只接口无稳定时间戳，由 provider 层填充
        "trade_date": None,
    }


def normalize_eastmoney(
    raw_json: dict | str,
    secid_map: dict[str, str],
    source: str = "eastmoney",
) -> pl.DataFrame:
    """东财 push2 响应 → 标准化 DataFrame。

    Args:
        raw_json: 东财返回的 JSON（dict 或字符串）。
        secid_map: secid → 项目 symbol 映射，由 provider 层构建。

    支持两种返回结构:
      - 单只: {"rc":0, "data": {"f43":..., "f57":"600000", ...}}
      - 批量: {"rc":0, "data": {"diff": [{"f43":..., "f57":"600000", ...}, ...]}}
    """
    if isinstance(raw_json, str):
        try:
            raw_json = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            return empty_realtime_df()
    if not isinstance(raw_json, dict):
        return empty_realtime_df()

    data = raw_json.get("data")
    if not isinstance(data, dict):
        return empty_realtime_df()

    rows: list[dict] = []

    def _resolve_symbol(item: dict) -> str | None:
        """从 item 找出 secid 并映射到 symbol，回退用 f57+启发。"""
        # 优先用 secid_map 反查；item 里 secid 不一定返回，靠 provider 顺序对齐。
        code = item.get(_EM_F_CODE)
        if not code:
            return None
        # secid_map 是 {"1.600000": "600000.SH", ...}
        # item 里通常不含 secid 字段，靠 f57 + 在 secid_map 里查 code 后缀
        for secid, sym in secid_map.items():
            if secid.split(".")[-1] == code:
                return sym
        # 兜底：用代码 + 市场启发（不推荐，仅最后手段）
        return None

    diff = data.get("diff")
    if isinstance(diff, list):
        # 批量
        for item in diff:
            if not isinstance(item, dict):
                continue
            symbol = _resolve_symbol(item)
            if not symbol:
                continue
            rows.append(_em_build_row(item, symbol, source))
    elif isinstance(data, dict) and _EM_F_CODE in data:
        # 单只
        symbol = _resolve_symbol(data)
        if symbol:
            rows.append(_em_build_row(data, symbol, source))
    else:
        # data 是 {secid: {...}} 的批量变体（部分接口）
        for secid, item in data.items():
            if secid in ("diff",) or not isinstance(item, dict):
                continue
            symbol = secid_map.get(secid)
            if not symbol:
                continue
            rows.append(_em_build_row(item, symbol, source))

    if not rows:
        return empty_realtime_df()
    return pl.DataFrame(rows, schema=FREE_REALTIME_SCHEMA)
