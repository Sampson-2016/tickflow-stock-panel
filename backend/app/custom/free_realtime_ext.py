"""免费实时行情 L2 扩展 — 路由 + 启动钩子。

职责:
  - 提供 /api/free-realtime/* 配置、状态、单日分时查询端点
  - 提供 /api/free-realtime/test/* 测试页端点（mock 控制 + 循环查询 + 一键清空）
  - 启动时自动启动调度器（仅当全局开关开启且有源）

不重复实现持仓T/超买超卖/自选股业务；这三个模块各自实现自己的 store/CRUD，
仅通过 register_collector + register_module_callback 接入调度器。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.extensions import (
    BACKEND_EXTENSION_API_VERSION,
    BackendExtensionRegistrar,
    ExtensionContext,
)
from app.services.free_realtime import config as fr_config
from app.services.free_realtime import scheduler as fr_scheduler

logger = logging.getLogger(__name__)

EXTENSION_ID = "free_realtime.realtime"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION

router = APIRouter(prefix="/api/free-realtime", tags=["free-realtime"])
test_router = APIRouter(prefix="/api/free-realtime/test", tags=["free-realtime-test"])


# ── 配置端点 ───────────────────────────────────────────────
class ConfigIn(BaseModel):
    enabled: bool | None = None
    interval_sec: int | None = None
    sources: list[str] | None = None
    trading_hours_only: bool | None = None
    retention_days: int | None = None
    overbought_pct: float | None = None
    oversold_pct: float | None = None
    modules: dict[str, bool] | None = None


@router.get("/config")
def get_config() -> dict:
    return fr_config.snapshot()


@router.put("/config")
def put_config(payload: ConfigIn) -> dict:
    try:
        if payload.enabled is not None:
            fr_config.set_global_enabled(payload.enabled)
        if payload.interval_sec is not None:
            fr_config.set_interval_sec(payload.interval_sec)
        if payload.sources is not None:
            fr_config.set_sources(payload.sources)
        if payload.trading_hours_only is not None:
            fr_config.set_trading_hours_only(payload.trading_hours_only)
        if payload.retention_days is not None:
            fr_config.set_retention_days(payload.retention_days)
        if payload.overbought_pct is not None and payload.oversold_pct is not None:
            fr_config.set_overbought_thresholds(payload.overbought_pct, payload.oversold_pct)
        if payload.modules is not None:
            for mod, flag in payload.modules.items():
                fr_config.set_module_enabled(mod, flag)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return fr_config.snapshot()


@router.get("/status")
def get_status() -> dict:
    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()
    return scheduler.status()


@router.post("/start")
def post_start() -> dict:
    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()
    if not fr_config.get_global_enabled():
        raise HTTPException(status_code=400, detail="全局开关未开启")
    return scheduler.start()


@router.post("/stop")
def post_stop() -> dict:
    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()
    return scheduler.stop()


@router.post("/prune")
def post_prune() -> dict:
    """手动触发清理（默认惰性）。"""
    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()
    removed = scheduler.store().prune()
    return {"ok": True, "removed_dirs": removed}


# ── 分时查询 ───────────────────────────────────────────────
@router.get("/intraday/{symbol}")
def get_intraday(symbol: str, kind: str = "real", days: int = 1) -> dict:
    """单标的当日内分时数据（默认 kind=real，测试页可传 kind=mock）。

    days=1 时走 list_day(today)，合并 parquet + 内存窗口，确保拿到完整 tick 历史。
    """
    if kind not in ("real", "mock"):
        raise HTTPException(status_code=400, detail="kind must be real/mock")
    from datetime import date as _date

    sym = symbol.strip().upper()
    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()
    if days <= 1:
        df = scheduler.store().list_day(sym, _date.today(), kind=kind)
    else:
        df = scheduler.store().list_recent_days(sym, days=min(int(days), 30), kind=kind)
    return {
        "symbol": sym,
        "count": df.height,
        "rows": df.to_dicts() if not df.is_empty() else [],
    }


# ── 测试页端点 ─────────────────────────────────────────────
class MockConfigIn(BaseModel):
    symbols: list[str] | None = None
    base_prices: dict[str, float] | None = None
    names: dict[str, str] | None = None
    volatility_pct: float | None = None
    fail_rate: float | None = None
    seed: int | None = None
    acceleration: int | None = None


_test_state: dict[str, Any] = {
    "mock_running": False,
    "real_loop_running": False,
    "config": {
        "symbols": ["600000.SH"],
        "base_prices": {"600000.SH": 10.0},
        "names": {"600000.SH": "浦发银行"},
        "volatility_pct": 0.01,
        "fail_rate": 0.0,
        "seed": 42,
        "interval_seconds": 1.0,
        "acceleration": 1,
    },
    "mock_thread": None,
    "real_thread": None,
    "lock": threading.Lock(),
    "last_tick_ms": 0.0,
    "tick_count": 0,
}
# 加速倍率白名单
_ACCELERATIONS = (1, 3, 10, 30, 60, 120)


@test_router.get("/mock/config")
def get_mock_config() -> dict:
    with _test_state["lock"]:
        cfg = dict(_test_state["config"])
    return {
        "config": cfg,
        "running": _test_state["mock_running"],
        "accelerations": list(_ACCELERATIONS),
    }


@test_router.put("/mock/config")
def put_mock_config(payload: MockConfigIn) -> dict:
    with _test_state["lock"]:
        cfg = dict(_test_state["config"])
        if payload.symbols is not None:
            cfg["symbols"] = list(payload.symbols)
        if payload.base_prices is not None:
            cfg["base_prices"] = dict(payload.base_prices)
        if payload.names is not None:
            cfg["names"] = dict(payload.names)
        if payload.volatility_pct is not None:
            cfg["volatility_pct"] = max(0.0, min(0.2, float(payload.volatility_pct)))
        if payload.fail_rate is not None:
            cfg["fail_rate"] = max(0.0, min(1.0, float(payload.fail_rate)))
        if payload.seed is not None:
            cfg["seed"] = int(payload.seed)
        if payload.acceleration is not None:
            if int(payload.acceleration) not in _ACCELERATIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"acceleration must be in {list(_ACCELERATIONS)}",
                )
            cfg["acceleration"] = int(payload.acceleration)
        _test_state["config"] = cfg
        # 重置 MockProvider 实例状态
        from app.data_providers.free_realtime.registry import get_provider

        provider = get_provider("mock")
        provider.set_config(cfg)
    return {"config": cfg, "running": _test_state["mock_running"]}


@test_router.post("/mock/start")
def start_mock_loop() -> dict:
    """启动 mock 循环查询：固定线程按 mock interval 倒推真实间隔拉取。"""
    with _test_state["lock"]:
        if _test_state["mock_running"]:
            return {"ok": True, "already_running": True}
        from app.data_providers.free_realtime.registry import get_provider

        provider = get_provider("mock")
        provider.set_config(_test_state["config"])
        _test_state["mock_running"] = True
        _test_state["tick_count"] = 0
        t = threading.Thread(target=_mock_loop, daemon=True, name="free-realtime-mock")
        _test_state["mock_thread"] = t
    t.start()
    return {"ok": True}


@test_router.post("/mock/stop")
def stop_mock_loop() -> dict:
    with _test_state["lock"]:
        _test_state["mock_running"] = False
        thread = _test_state["mock_thread"]
    if thread:
        thread.join(timeout=5)
    with _test_state["lock"]:
        _test_state["mock_thread"] = None
    return {"ok": True}


@test_router.post("/real/start")
def start_real_loop() -> dict:
    """启动真实循环查询（绕过交易时段与全局开关，仅测试页用）。"""
    with _test_state["lock"]:
        if _test_state["real_loop_running"]:
            return {"ok": True, "already_running": True}
        _test_state["real_loop_running"] = True
        _test_state["tick_count"] = 0
        t = threading.Thread(target=_real_loop, daemon=True, name="free-realtime-test")
        _test_state["real_thread"] = t
    t.start()
    return {"ok": True}


@test_router.post("/real/stop")
def stop_real_loop() -> dict:
    with _test_state["lock"]:
        _test_state["real_loop_running"] = False
        thread = _test_state["real_thread"]
    if thread:
        thread.join(timeout=5)
    with _test_state["lock"]:
        _test_state["real_thread"] = None
    return {"ok": True}


@test_router.get("/status")
def test_status() -> dict:
    with _test_state["lock"]:
        return {
            "mock_running": _test_state["mock_running"],
            "real_loop_running": _test_state["real_loop_running"],
            "last_tick_ms": _test_state["last_tick_ms"],
            "tick_count": _test_state["tick_count"],
        }


@test_router.post("/clear")
def clear_storage(kind: str = "real") -> dict:
    """一键清空指定 kind 的所有落盘 + 内存窗口。"""
    if kind not in ("real", "mock"):
        raise HTTPException(status_code=400, detail="kind must be real/mock")
    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()
    removed = scheduler.store().clear_kind(kind)
    return {"ok": True, "removed": removed, "kind": kind}


# ── 后台循环（mock） ──────────────────────────────────────
def _mock_loop() -> None:
    from app.data_providers.free_realtime.registry import get_provider

    provider = get_provider("mock")
    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()
    while True:
        with _test_state["lock"]:
            if not _test_state["mock_running"]:
                break
            cfg = dict(_test_state["config"])
        symbols = cfg.get("symbols") or []
        if symbols:
            df = provider.get_realtime(symbols)
            if not df.is_empty():
                scheduler.store().append(df, kind="mock")
                with _test_state["lock"]:
                    _test_state["last_tick_ms"] = time.time() * 1000
                    _test_state["tick_count"] += 1
        # 真实间隔 = base interval / acceleration
        base = float(cfg.get("interval_seconds", 1.0))
        accel = max(1, int(cfg.get("acceleration", 1)))
        sleep_sec = max(0.05, base / accel)
        slept = 0.0
        while slept < sleep_sec:
            with _test_state["lock"]:
                if not _test_state["mock_running"]:
                    break
            time.sleep(0.05)
            slept += 0.05


def _real_loop() -> None:
    """真实循环：按调度器配置 interval 轮询勾选源。"""
    from app.data_providers.free_realtime.registry import get_provider

    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()
    while True:
        with _test_state["lock"]:
            if not _test_state["real_loop_running"]:
                break
        sources = fr_config.get_sources()
        if not sources:
            time.sleep(1.0)
            continue
        idx = _test_state.get("real_idx", 0) % len(sources)
        _test_state["real_idx"] = idx + 1
        source = sources[idx]
        try:
            provider = get_provider(source)
            symbols = scheduler._collector.collect()
            if symbols:
                df = provider.get_realtime(symbols)
                if not df.is_empty():
                    scheduler.store().append(df, kind="real")
                    with _test_state["lock"]:
                        _test_state["last_tick_ms"] = time.time() * 1000
                        _test_state["tick_count"] += 1
        except Exception as e:
            logger.warning("test real loop tick failed: %s", e)
        interval = fr_config.get_interval_sec()
        slept = 0.0
        while slept < interval:
            with _test_state["lock"]:
                if not _test_state["real_loop_running"]:
                    break
            time.sleep(0.2)
            slept += 0.2


# ── 扩展注册 ───────────────────────────────────────────────
def setup(registrar: BackendExtensionRegistrar) -> None:
    registrar.include_router(router)
    registrar.include_router(test_router)


def startup(context: ExtensionContext) -> None:
    """启动钩子：注册各模块 collector + 调度器自动启动。"""
    del context  # 未使用
    try:
        scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()

        # 注册自选股 collector（如果该模块开启）
        try:
            from app.services import watchlist
            from app.services.free_realtime import config as fr_config

            if fr_config.get_module_enabled("watchlist"):

                def _watchlist_collector() -> list[str]:
                    rows = watchlist.list_symbols()
                    return [str(r.get("symbol") or "") for r in rows if r.get("symbol")]

                scheduler.register_collector(_watchlist_collector)
                logger.info("watchlist collector attached to free_realtime")
        except Exception as e:
            logger.warning("watchlist collector attach failed: %s", e)

        # 注册超买超卖回调
        try:
            from app.services.free_realtime import config as fr_config
            from app.services.free_realtime import overbought_oversold

            if fr_config.get_module_enabled("overbought_oversold"):
                # repository 由 main 注入到 app.state，启动时已就绪；这里延后取
                def _overbought_callback(df) -> None:
                    try:
                        repo = _get_repo_from_app()
                    except Exception:
                        return
                    sigs = overbought_oversold.evaluate(df, repo)
                    if sigs:
                        _push_overbought_signals(sigs)

                fr_scheduler.FreeRealtimeScheduler.register_module_callback(_overbought_callback)

                # 超买超卖 collector：与持仓T 标的列表共用即可
                # 用户在超买超卖模块中通过个股 enabled 控制参与
                def _overbought_collector() -> list[str]:
                    # 复用持仓T + 自选股 enabled 列表（避免引入新存储）
                    syms: list[str] = []
                    try:
                        from app.custom.easyquotation_t_signal import POLLER
                        syms.extend(POLLER.positions().list_enabled_symbols())
                    except Exception:
                        pass
                    try:
                        from app.services import watchlist
                        rows = watchlist.list_symbols()
                        syms.extend(str(r.get("symbol") or "") for r in rows if r.get("symbol"))
                    except Exception:
                        pass
                    # 去重
                    seen: set[str] = set()
                    out: list[str] = []
                    for s in syms:
                        s = str(s or "").strip().upper()
                        if s and s not in seen:
                            seen.add(s)
                            out.append(s)
                    return out

                scheduler.register_collector(_overbought_collector)
                logger.info("overbought_oversold attached to free_realtime")
        except Exception as e:
            logger.warning("overbought_oversold attach failed: %s", e)

        fr_scheduler.auto_start_if_enabled()
    except Exception as e:
        logger.warning("free_realtime scheduler auto-start failed: %s", e)


def _get_repo_from_app():
    """从 FastAPI app.state 取 KlineRepository（仅在请求上下文外调用）。"""
    from app.extensions.loader import current_extension_context

    ctx = current_extension_context()
    if ctx and getattr(ctx, "repository", None):
        return ctx.repository
    # fallback: 直接从 main 模块全局拿（启动后已就绪）
    from app.main import app

    return getattr(app.state, "repo", None)


def _push_overbought_signals(sigs) -> None:
    """超买超卖信号推送 — 复用 review_push_channels。"""
    try:
        from app.services import preferences, webhook_adapter
        from app.services.free_realtime.overbought_oversold import to_dict

        channels = preferences.get_review_push_channels()
        if not channels:
            return
        lines = []
        for s in sigs:
            d = to_dict(s)
            arrow = "↑超买" if d["direction"] == "overbought" else "↓超卖"
            lines.append(
                f"- {d['name']}({d['symbol']}) 现 {d['last_price']:.2f} "
                f"MA5 {d['ma5']:.2f} 偏离 {d['deviation_pct']:+.2f}% {arrow}"
            )
        body = "\n".join(lines)
        title = "超买超卖提示"
        for ch in channels:
            if ch == "feishu":
                url = preferences.get_feishu_webhook_url()
                if not url:
                    continue
                secret = preferences.get_feishu_webhook_secret()
                webhook_adapter.send_feishu_card(url, title, "", body, secret)
            elif ch == "wecom":
                url = preferences.get_wecom_webhook_url()
                if not url:
                    continue
                webhook_adapter.send_wecom_markdown(url, title, body)
    except Exception as e:
        logger.warning("overbought_oversold push failed: %s", e)
