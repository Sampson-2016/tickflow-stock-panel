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


# ── 超买超卖模块标的清单 ──────────────────────────────────
# 持仓T 标的来自持仓数据，自选股标的来自 watchlist 模块；
# 超买超卖标的没有上游来源，必须独立持久化一份用户配置的清单。
_OB_KEY = "free_realtime_overbought_symbols"


def list_overbought_symbols() -> list[dict]:
    """返回超买超卖模块的全部标的清单（顺序保留）。

    每项形如 {
        "symbol": "600000.SH",
        "name": "浦发银行",
        "enabled": True,
        "overbought_pct": 5.0 | None,   # 个股超买阈值；None 表示跟随全局默认
        "oversold_pct": 5.0 | None,     # 个股超卖阈值；None 表示跟随全局默认
    }。
    """
    raw = _load().get(_OB_KEY)
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol", "")).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        # 个股阈值：可选；缺省 None 让评估器回落到全局默认
        ob = item.get("overbought_pct")
        os_ = item.get("oversold_pct")
        out.append({
            "symbol": sym,
            "name": str(item.get("name") or sym),
            "enabled": bool(item.get("enabled", True)),
            "overbought_pct": float(ob) if isinstance(ob, (int, float)) else None,
            "oversold_pct": float(os_) if isinstance(os_, (int, float)) else None,
        })
    return out


def add_overbought_symbol(symbol: str, name: str | None = None) -> list[dict]:
    """新增一个标的到超买超卖清单；symbol 已存在时仅更新 name，不重复加入。

    返回更新后的清单。
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol required")
    items = list_overbought_symbols()
    for it in items:
        if it["symbol"] == sym:
            if name and it.get("name") != name:
                it["name"] = str(name)
            _persist_overbought(items)
            return items
    items.append({"symbol": sym, "name": str(name or sym), "enabled": True})
    _persist_overbought(items)
    return items


def remove_overbought_symbol(symbol: str) -> list[dict]:
    sym = str(symbol or "").strip().upper()
    items = [it for it in list_overbought_symbols() if it["symbol"] != sym]
    _persist_overbought(items)
    return items


def set_overbought_symbol_enabled(symbol: str, enabled: bool) -> list[dict]:
    """切换单个超买超卖标的 enabled 状态；不存在则忽略。"""
    items = list_overbought_symbols()
    found = False
    for it in items:
        if it["symbol"] == symbol.strip().upper():
            it["enabled"] = bool(enabled)
            found = True
            break
    if found:
        _persist_overbought(items)
    return items


def set_overbought_symbol_thresholds(
    symbol: str,
    overbought_pct: float | None,
    oversold_pct: float | None,
) -> list[dict]:
    """设置单个超买超卖标的的阈值；None 表示清除个股覆盖，回落到全局默认。

    阈值范围 (0, 50]；不存在 symbol 时忽略。
    """
    for v in (overbought_pct, oversold_pct):
        if v is not None and not (0 < float(v) <= 50):
            raise ValueError("threshold pct must be in (0, 50]")
    sym = symbol.strip().upper()
    items = list_overbought_symbols()
    found = False
    for it in items:
        if it["symbol"] == sym:
            it["overbought_pct"] = float(overbought_pct) if overbought_pct is not None else None
            it["oversold_pct"] = float(oversold_pct) if oversold_pct is not None else None
            found = True
            break
    if found:
        _persist_overbought(items)
    return items


def _persist_overbought(items: list[dict]) -> None:
    preferences.save({_OB_KEY: items})


def list_overbought_enabled_symbols() -> list[str]:
    """超买超卖模块中 enabled=True 的 symbol 列表（供调度器 collector 调用）。"""
    return [it["symbol"] for it in list_overbought_symbols() if it.get("enabled", True)]


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
