"""免费实时行情用户偏好持久化。

存储: data/user_data/preferences.json 内嵌 free_realtime_* 字段。

设计要点:
  - 全局开关 + 多源勾选 + 轮询间隔 + 模块开关 + 个股开关
  - 三个模块（持仓T / 超买超卖 / 自选股）默认全局关闭
  - 轮询间隔范围 6..60 秒
  - 数据源白名单 sina/tencent/eastmoney；勾选多源时调度器交替轮询
  - 失败熔断阈值 3 次连续失败，复用 review_push_channels 推送告警
"""
from __future__ import annotations

from app.services import preferences

# 默认轮询间隔（秒）；与盘前盘后无关，仅控制拉取节奏
DEFAULT_INTERVAL_SEC = 6
INTERVAL_MIN_SEC = 6
INTERVAL_MAX_SEC = 60
# 连续失败 N 次后熔断该源
FAIL_BREAK_THRESHOLD = 3

_AVAILABLE_SOURCES = ("sina", "tencent", "eastmoney")


def _load() -> dict:
    return preferences.load()


def get_global_enabled() -> bool:
    """全局开关；默认 False（三个模块默认关闭）。"""
    return bool(_load().get("free_realtime_enabled", False))


def set_global_enabled(enabled: bool) -> bool:
    preferences.save({"free_realtime_enabled": bool(enabled)})
    return get_global_enabled()


def get_interval_sec() -> int:
    raw = _load().get("free_realtime_interval_sec", DEFAULT_INTERVAL_SEC)
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = DEFAULT_INTERVAL_SEC
    return max(INTERVAL_MIN_SEC, min(INTERVAL_MAX_SEC, v))


def set_interval_sec(seconds: int) -> int:
    v = max(INTERVAL_MIN_SEC, min(INTERVAL_MAX_SEC, int(seconds)))
    preferences.save({"free_realtime_interval_sec": v})
    return v


def get_sources() -> list[str]:
    """勾选的数据源（顺序保留）。空列表视为全部禁用（调度器不工作）。"""
    raw = _load().get("free_realtime_sources")
    if not isinstance(raw, list):
        # 默认仅新浪
        return ["sina"]
    seen: set[str] = set()
    out: list[str] = []
    for s in raw:
        if s in _available_set() and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def set_sources(sources: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for s in sources or []:
        if s in _available_set() and s not in seen:
            seen.add(s)
            cleaned.append(s)
    preferences.save({"free_realtime_sources": cleaned})
    return cleaned


def get_trading_hours_only() -> bool:
    """是否仅在 A 股交易时段拉取。默认 True。"""
    return bool(_load().get("free_realtime_trading_hours_only", True))


def set_trading_hours_only(flag: bool) -> bool:
    preferences.save({"free_realtime_trading_hours_only": bool(flag)})
    return get_trading_hours_only()


def get_retention_days() -> int:
    raw = _load().get("free_realtime_retention_days", 7)
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = 7
    return max(1, min(30, v))


def set_retention_days(days: int) -> int:
    v = max(1, min(30, int(days)))
    preferences.save({"free_realtime_retention_days": v})
    return v


# ── 模块开关 ────────────────────────────────────────────
def get_module_enabled(module: str) -> bool:
    """模块全局开关：module ∈ {position_t, overbought_oversold, watchlist}。

    默认 False：用户主动开启后才进入调度器采集范围。
    """
    if module not in ("position_t", "overbought_oversold", "watchlist"):
        return False
    return bool(_load().get(f"free_realtime_module_{module}", False))


def set_module_enabled(module: str, enabled: bool) -> bool:
    if module not in ("position_t", "overbought_oversold", "watchlist"):
        raise ValueError(f"unknown free realtime module: {module}")
    preferences.save({f"free_realtime_module_{module}": bool(enabled)})
    return get_module_enabled(module)


# ── 个股开关 ────────────────────────────────────────────
def _stock_key(module: str, symbol: str) -> str:
    return f"free_realtime_{module}_symbol_{symbol.strip().upper()}"


def get_symbol_enabled(module: str, symbol: str) -> bool:
    """个股开关；默认 True（只要模块开了即监控该标的）。"""
    return bool(_load().get(_stock_key(module, symbol), True))


def set_symbol_enabled(module: str, symbol: str, enabled: bool) -> bool:
    preferences.save({_stock_key(module, symbol): bool(enabled)})
    return get_symbol_enabled(module, symbol)


def list_disabled_symbols(module: str) -> list[str]:
    """模块下被关闭的 symbol 列表（避免每只标的读盘）。"""
    prefix = f"free_realtime_{module}_symbol_"
    data = _load()
    out: list[str] = []
    for k, v in data.items():
        if k.startswith(prefix) and v is False:
            out.append(k[len(prefix):])
    return out


# ── 超买超卖阈值 ────────────────────────────────────────────
def get_overbought_pct() -> float:
    """超买：向上偏离 5 日线的百分比阈值（默认 5.0）。"""
    raw = _load().get("free_realtime_overbought_pct", 5.0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 5.0


def get_oversold_pct() -> float:
    """超卖：向下偏离 5 日线的百分比阈值（默认 5.0）。"""
    raw = _load().get("free_realtime_oversold_pct", 5.0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 5.0


def set_overbought_thresholds(overbought: float, oversold: float) -> dict:
    if not (0 < float(overbought) <= 50) or not (0 < float(oversold) <= 50):
        raise ValueError("overbought/oversold pct must be in (0, 50]")
    preferences.save({
        "free_realtime_overbought_pct": float(overbought),
        "free_realtime_oversold_pct": float(oversold),
    })
    return {"overbought_pct": get_overbought_pct(), "oversold_pct": get_oversold_pct()}


def _available_set() -> set[str]:
    try:
        from app.data_providers.free_realtime.registry import AVAILABLE_SOURCES
        return set(AVAILABLE_SOURCES) - {"mock"}  # mock 不允许生产环境配置
    except Exception:
        return set(_AVAILABLE_SOURCES)


def snapshot() -> dict:
    """一次性返回完整配置（避免前端轮询多次读盘）。"""
    return {
        "enabled": get_global_enabled(),
        "interval_sec": get_interval_sec(),
        "sources": get_sources(),
        "trading_hours_only": get_trading_hours_only(),
        "retention_days": get_retention_days(),
        "modules": {
            "position_t": get_module_enabled("position_t"),
            "overbought_oversold": get_module_enabled("overbought_oversold"),
            "watchlist": get_module_enabled("watchlist"),
        },
        "overbought_pct": get_overbought_pct(),
        "oversold_pct": get_oversold_pct(),
        "available_sources": sorted(_available_set()),
    }
