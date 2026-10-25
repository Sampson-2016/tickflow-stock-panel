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


# ── 超买超卖模块端点 ───────────────────────────────────────
# 超买超卖模块维护自己的独立标的清单（不依赖持仓T/自选股）；
# 标的 CRUD 在此路由组下，个股 enabled/disabled 控制是否参与评估
# 阈值/模块开关复用 PUT /api/free-realtime/config (overbought_pct / oversold_pct / modules)


@router.get("/overbought-oversold/symbols")
def get_overbought_oversold_symbols() -> dict:
    """返回超买超卖模块独立的标的清单（不与持仓T/自选股合并）。

    每项形如 {"symbol", "name", "enabled"}；前端用于列表渲染 + 增删开关。
    """
    items = fr_config.list_overbought_symbols()
    return {"items": items, "count": len(items)}


class OverboughtSymbolIn(BaseModel):
    symbol: str
    name: str | None = None


class OverboughtSymbolToggleIn(BaseModel):
    enabled: bool


class OverboughtSymbolThresholdsIn(BaseModel):
    # None 表示清除个股覆盖，回落到全局默认
    overbought_pct: float | None = None
    oversold_pct: float | None = None


@router.post("/overbought-oversold/symbols")
def post_overbought_oversold_symbol(payload: OverboughtSymbolIn) -> dict:
    """添加一个标的到超买超卖清单。

    已存在时仅更新 name，不重复加入；symbol 自动归一化（strip+upper）。
    """
    sym = (payload.symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    items = fr_config.add_overbought_symbol(sym, payload.name)
    return {"items": items, "count": len(items)}


@router.delete("/overbought-oversold/symbols/{symbol}")
def delete_overbought_oversold_symbol(symbol: str) -> dict:
    """从超买超卖清单中删除一个标的。"""
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    items = fr_config.remove_overbought_symbol(sym)
    return {"items": items, "count": len(items)}


@router.put("/overbought-oversold/symbols/{symbol}")
def put_overbought_oversold_symbol(symbol: str, payload: OverboughtSymbolToggleIn) -> dict:
    """切换个股在超买超卖模块的 enabled 状态。"""
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    items = fr_config.set_overbought_symbol_enabled(sym, bool(payload.enabled))
    return {"symbol": sym, "enabled": bool(payload.enabled), "items": items}


@router.put("/overbought-oversold/symbols/{symbol}/thresholds")
def put_overbought_oversold_symbol_thresholds(
    symbol: str, payload: OverboughtSymbolThresholdsIn
) -> dict:
    """设置个股超买/超卖阈值；任一为 null 表示该方向回落到全局默认。"""
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    try:
        items = fr_config.set_overbought_symbol_thresholds(
            sym, payload.overbought_pct, payload.oversold_pct
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"symbol": sym, "items": items}


@router.get("/overbought-oversold/signals")
def get_overbought_oversold_signals(source: str = "real") -> dict:
    """在线评估当前已启用标的的超买超卖信号。

    source: "real"（默认，real tick 优先 mock 兜底）或 "mock"（mock tick 优先）。

    评估流程：
      1. 通过 fr_config.list_overbought_enabled_symbols() 拉取本模块 enabled 标的
      2. 从 RealtimeStore.list_today 取每个标的的最新 tick（real 优先，mock 兜底）
      3. 调用 overbought_oversold.evaluate，从 KlineRepository 取最近 4 日 raw_close
      4. 返回命中信号列表

    Returns:
        {"items": [Signal...], "count": N, "evaluated": N}
    """
    import polars as pl
    from app.data_providers.free_realtime.schemas import FREE_REALTIME_SCHEMA

    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()
    repo = _get_repo_from_app()
    if repo is None:
        return {"items": [], "count": 0, "evaluated": 0, "error": "repository not ready"}

    # 1. 拉取超买超卖模块自己的 enabled 标的
    symbols = fr_config.list_overbought_enabled_symbols()
    if not symbols:
        return {"items": [], "count": 0, "evaluated": 0}

    # 2. 取每个标的的最新 tick（source=mock 时 mock 优先，real 时 real 优先，互为兜底）
    latest_rows: list[dict] = []
    kinds = ("mock", "real") if source == "mock" else ("real", "mock")
    for sym in symbols:
        for kind in kinds:
            df = scheduler.store().list_today(sym, kind=kind)
            if not df.is_empty():
                latest_rows.append(df.tail(1).row(0, named=True))
                break
    if not latest_rows:
        return {"items": [], "count": 0, "evaluated": 0}

    tick_df = pl.DataFrame(latest_rows, schema=FREE_REALTIME_SCHEMA)

    # 3. 在线评估
    from app.services.free_realtime import overbought_oversold
    try:
        sigs = overbought_oversold.evaluate(tick_df, repo)
    except Exception as e:
        logger.warning("overbought-oversold evaluate failed: %s", e)
        return {"items": [], "count": 0, "evaluated": len(latest_rows), "error": str(e)}

    return {
        "items": [overbought_oversold.to_dict(s) for s in sigs],
        "count": len(sigs),
        "evaluated": len(latest_rows),
    }


# ── 超买超卖收盘报告端点 ───────────────────────────────────
class OoReportGenerateIn(BaseModel):
    categories: list[str] | None = None  # 缺省用已存偏好
    push: bool = True


class OoReportConfigIn(BaseModel):
    schedule_enabled: bool | None = None
    schedule_time: str | None = None  # "HH:MM"
    categories: list[str] | None = None
    ai_commentary: bool | None = None
    prompt: str | None = None  # 空串 = 恢复内置默认


@router.post("/overbought-oversold/report")
def post_oo_report(payload: OoReportGenerateIn) -> dict:
    """手动生成收盘报告（评估 → AI 总结 → 归档 → 推送）。"""
    from app.services.free_realtime import report as oo_report

    try:
        return oo_report.generate_report(payload.categories, push=payload.push)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/overbought-oversold/reports")
def get_oo_reports() -> dict:
    """收盘报告归档列表（按日期倒序）。"""
    from app.services.free_realtime import report as oo_report

    return {"reports": oo_report.list_archives()}


@router.get("/overbought-oversold/reports/{trade_date}")
def get_oo_report_detail(trade_date: str) -> dict:
    """单份收盘报告归档详情。"""
    from app.services.free_realtime import report as oo_report

    data = oo_report.load_archive(trade_date)
    if data is None:
        raise HTTPException(status_code=404, detail="report not found")
    return data


@router.get("/overbought-oversold/report-config")
def get_oo_report_config() -> dict:
    """收盘报告配置（定时/类别/AI/提示词）。"""
    from app.services import ai_provider
    from app.services.free_realtime import config as fr_config

    return {
        "schedule_enabled": fr_config.get_oo_report_schedule_enabled(),
        "schedule_time": fr_config.get_oo_report_schedule_time(),
        "categories": fr_config.get_oo_report_categories(),
        "ai_commentary": fr_config.get_oo_report_ai_enabled(),
        "prompt": fr_config.get_oo_report_prompt(),
        "ai_configured": ai_provider.ai_configured(),
    }


@router.put("/overbought-oversold/report-config")
def put_oo_report_config(payload: OoReportConfigIn) -> dict:
    """更新收盘报告配置。"""
    from app.services.free_realtime import config as fr_config

    try:
        if payload.schedule_enabled is not None:
            fr_config.set_oo_report_schedule_enabled(payload.schedule_enabled)
        if payload.schedule_time is not None:
            fr_config.set_oo_report_schedule_time(payload.schedule_time)
        if payload.categories is not None:
            fr_config.set_oo_report_categories(payload.categories)
        if payload.ai_commentary is not None:
            fr_config.set_oo_report_ai_enabled(payload.ai_commentary)
        if payload.prompt is not None:
            fr_config.set_oo_report_prompt(payload.prompt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return get_oo_report_config()


# ── 测试页端点 ─────────────────────────────────────────────
class MockConfigIn(BaseModel):
    symbols: list[str] | None = None
    base_prices: dict[str, float] | None = None
    names: dict[str, str] | None = None
    volatility_pct: float | None = None
    fail_rate: float | None = None
    seed: int | None = None
    acceleration: int | None = None
    start_time: str | None = None  # 虚拟时钟起点 "HH:MM"（默认 "09:15"）


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
        "start_time": "09:15",
    },
    "mock_thread": None,
    "real_thread": None,
    "lock": threading.Lock(),
    "last_tick_ms": 0.0,
    # mock 与 real 各自独立计 tick，方便前端按落盘模块分别展示
    "tick_count": 0,
    "mock_tick_count": 0,
    "real_tick_count": 0,
}
# 加速倍率白名单
_ACCELERATIONS = (1, 3, 10, 30, 60, 120)
# mock 虚拟时钟起点白名单（"09:15" 早盘集合竞价起 / "10:30" 等其他交易时段点）
_ALLOWED_START_TIMES = ("09:15", "09:30", "10:30", "11:00", "13:00", "14:00")


def _latest_ts_ms(df: Any) -> float:
    """从 df 中取最新 tick 的 ts_ms。

    - 优先用 df 自带的 ts_ms（mock 虚拟时钟 / real provider 已 stamp 过的轮询时间）
    - 若 df 为空或 ts_ms 列异常，回退到 time.time() * 1000
    """
    try:
        if df.is_empty():
            return time.time() * 1000
        series = df["ts_ms"]
        if series.len() == 0:
            return time.time() * 1000
        # 取最后一行（最新 tick）；polars Series 末项即最新
        last_val = series.to_list()[-1]
        if last_val is None or int(last_val) <= 0:
            return time.time() * 1000
        return float(last_val)
    except Exception:
        return time.time() * 1000



@test_router.get("/mock/config")
def get_mock_config() -> dict:
    with _test_state["lock"]:
        cfg = dict(_test_state["config"])
    return {
        "config": cfg,
        "running": _test_state["mock_running"],
        "accelerations": list(_ACCELERATIONS),
    }


@test_router.get("/collect-symbols")
def get_collect_symbols() -> dict:
    """返回三个模块当前已启用的标的列表（mock 与真实循环共用）。"""
    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()
    symbols: list[str] = []
    if scheduler._collector is not None:
        try:
            symbols = scheduler._collector.collect()
        except Exception as e:
            logger.warning("collect-symbols failed: %s", e)
    return {"symbols": symbols, "running_mock": _test_state["mock_running"], "running_real": _test_state["real_loop_running"]}


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
        if payload.start_time is not None:
            raw = str(payload.start_time).strip()
            if raw not in _ALLOWED_START_TIMES:
                raise HTTPException(
                    status_code=400,
                    detail=f"start_time must be in {list(_ALLOWED_START_TIMES)}",
                )
            cfg["start_time"] = raw
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
        _test_state["mock_tick_count"] = 0
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
        _test_state["real_tick_count"] = 0
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
            "mock_tick_count": _test_state["mock_tick_count"],
            "real_tick_count": _test_state["real_tick_count"],
        }


@test_router.post("/clear")
def clear_storage(kind: str = "real") -> dict:
    """一键清空指定 kind 的所有落盘 + 内存窗口 + 测试状态计数。"""
    if kind not in ("real", "mock"):
        raise HTTPException(status_code=400, detail="kind must be real/mock")
    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()
    removed = scheduler.store().clear_kind(kind)
    # 同步清空测试页状态：对应 kind 的 tick_count 归零 + last_tick_ms 归零
    # 否则前端 status query 仍返回旧值，用户看到「tick 数量不为 0」
    with _test_state["lock"]:
        if kind == "mock":
            _test_state["mock_tick_count"] = 0
        else:
            _test_state["real_tick_count"] = 0
        # mock 和 real 共用 last_tick_ms 字段；清空任一 kind 都归零更直观
        _test_state["last_tick_ms"] = 0.0
    return {"ok": True, "removed": removed, "kind": kind}


# ── 后台循环（mock） ──────────────────────────────────────
def _mock_loop() -> None:
    """Mock 循环：按 acceleration 节奏生成 tick。

    两阶段（"两个接口"）:
      1. 启动时一次性 backfill 9:15 → start_time 段（如果 start_time > 09:15），
         一批推送进 store——让分时图打开就有 9:15-10:00 的完整曲线
      2. 之后按 interval_sec / acceleration 节奏生成 1 个 tick/循环，逐步推进
         ——让分时图随时间向右延伸

    时间轴约定（与 MockProvider.backfill 一致）:
      - backfill 末尾 mock_clock 停在 start_time - interval_sec
      - 流式第一次 get_realtime 推进到 start_time，tick 在 start_time
      - 无重叠、无间隙
    """
    from datetime import datetime, time as dt_time, timedelta

    from app.data_providers.free_realtime.registry import get_provider
    from app.markets import CN_TZ

    provider = get_provider("mock")
    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()

    # ── 计算回放范围与起点 ─
    # start_time 仅作为 mock 时钟起点。如果 start_time > 09:15（如 10:00），
    # 先 backfill 9:15 → start_time 段（一气呵成、不睡眠），再进入 1-tick-per-interval 流式。
    interval_sec = max(1, int(fr_config.get_interval_sec()))
    try:
        with _test_state["lock"]:
            cfg = dict(_test_state["config"])
        start_raw = cfg.get("start_time")
        now_real = datetime.now(CN_TZ)
        today_0915 = datetime(
            now_real.year, now_real.month, now_real.day, 9, 15, tzinfo=CN_TZ
        )
        if start_raw:
            h, m = str(start_raw).strip().split(":")
            t_start = dt_time(int(h), int(m))
            mock_start = datetime(
                now_real.year, now_real.month, now_real.day,
                t_start.hour, t_start.minute, tzinfo=CN_TZ,
            )
        else:
            mock_start = today_0915
    except Exception as e:
        logger.warning("mock clock init failed: %s", e)
        # 兜底：直接进入流式（不 backfill），clock 由 provider 默认值决定
        mock_start = today_0915

    # ── 接口 1：backfill 9:15 → start_time（一气呵成） ─
    # 仅当 start_time > 09:15 时回放，避免起点本身在 09:15 的无意义回放。
    # backfill 后 mock_clock 停在 mock_start - interval_sec，留给流式接管。
    try:
        if mock_start > today_0915:
            try:
                symbols_bf = scheduler._collector.collect() if scheduler._collector else []
            except Exception as e:
                logger.warning("mock backfill collector failed: %s", e)
                symbols_bf = []
            if symbols_bf:
                bf_df = provider.backfill(
                    symbols_bf, today_0915, mock_start, float(interval_sec)
                )
                if not bf_df.is_empty():
                    scheduler.store().append(bf_df, kind="mock")
                    with _test_state["lock"]:
                        # tick_count 按"每 symbol 一行"计数（与流式循环口径一致）
                        added = bf_df.height // max(1, len(symbols_bf))
                        _test_state["tick_count"] += added
                        _test_state["mock_tick_count"] += added
                        _test_state["last_tick_ms"] = _latest_ts_ms(bf_df)
                    logger.info(
                        "mock backfill: %d ticks from %s to %s",
                        bf_df.height // max(1, len(symbols_bf)),
                        today_0915.strftime("%H:%M:%S"),
                        mock_start.strftime("%H:%M:%S"),
                    )
        else:
            # start_time <= 09:15：不需要 backfill，把 clock 设到 mock_start - interval
            # 让流式第一次 tick 落在 mock_start（与 backfill 后的衔接约定一致）
            provider.set_mock_clock(mock_start - timedelta(seconds=interval_sec))
    except Exception as e:
        logger.warning("mock backfill failed: %s", e)
        provider.set_mock_clock(mock_start - timedelta(seconds=interval_sec))

    # ── 接口 2：1-tick-per-interval 流式生成 ─
    # 已 backfill 过的标的集合：mock 运行中新加入的标的（三个模块开关/清单变化）
    # 需要增量补抓 9:15 → 当前虚拟时钟 段，否则新标的分时图前段为空。
    backfilled: set[str] = set()
    # 记录启动 backfill 覆盖过的标的（含空清单情形下跳过的）
    try:
        backfilled.update(symbols_bf)
    except NameError:
        pass

    def _backfill_new_symbols(new_syms: list[str], until_dt) -> None:
        """对 mock 运行中新加入的标的增量回填 9:15 → until_dt 段。

        backfill 会重置共享虚拟时钟，这里先保存后恢复，保证主循环时间轴无缝。
        """
        saved_clock = provider.mock_clock()
        try:
            bf_df = provider.backfill(
                new_syms, today_0915, until_dt, float(interval_sec)
            )
            if not bf_df.is_empty():
                scheduler.store().append(bf_df, kind="mock")
                try:
                    import polars as pl
                    scheduler.dispatch_mock(
                        bf_df.filter(
                            pl.col("ts_ms") == bf_df["ts_ms"].max()
                        )
                    )
                except Exception:
                    pass
                logger.info(
                    "mock incremental backfill: %d symbols × %d ticks to %s",
                    len(new_syms),
                    bf_df.height // max(1, len(new_syms)),
                    until_dt.strftime("%H:%M:%S"),
                )
        except Exception as e:
            logger.warning("mock incremental backfill failed: %s", e)
        finally:
            if saved_clock is not None:
                provider.set_mock_clock(saved_clock)

    while True:
        with _test_state["lock"]:
            if not _test_state["mock_running"]:
                break
            cfg = dict(_test_state["config"])
        # tick 间隔跟随用户在「A股免费实时行情」配置面板设置的刷新间隔
        # （interval_sec，范围 6..60 秒），与真实循环查询同口径。
        # acceleration 仍是测试页加速倍率：真实睡眠 = interval_sec / acceleration。
        # 虚拟时钟推进步长 = interval_sec（每个 tick 代表 interval_sec 秒虚拟时间）。
        interval_sec = max(1, int(fr_config.get_interval_sec()))
        provider.set_tick_interval(float(interval_sec))
        # Mock 标的来自三个模块 collector（持仓T / 自选股 / 超买超卖）
        # 这样测试页与真实循环共享同一份标的列表，避免重复配置
        try:
            symbols = scheduler._collector.collect() if scheduler._collector else []
        except Exception as e:
            logger.warning("mock loop collector failed: %s", e)
            symbols = []
        # 增量回填：mock 运行中新加入的标的补抓 9:15 → 当前虚拟时钟 段
        new_syms = [s for s in symbols if s not in backfilled]
        if new_syms:
            backfilled.update(new_syms)
            try:
                current_clock = provider.mock_clock()
                if current_clock is not None and current_clock > today_0915:
                    _backfill_new_symbols(new_syms, current_clock)
            except Exception as e:
                logger.warning("mock new-symbol backfill check failed: %s", e)
        if symbols:
            df = provider.get_realtime(symbols)
            if not df.is_empty():
                scheduler.store().append(df, kind="mock")
                # mock 全链路：把虚拟 tick 广播给模块回调（超买超卖/持仓T 评估 + 推送）
                try:
                    scheduler.dispatch_mock(df)
                except Exception as e:
                    logger.warning("mock dispatch to modules failed: %s", e)
                # 用最新 tick 的 ts_ms（虚拟时钟推进后的时间戳）作为「最近 tick」，
                # 而不是 time.time()（真实挂钟时间戳）。这样前端显示就能反映 mock
                # 虚拟时钟推进到的时间（如 9:15、9:16…），与起点 start_time 一致。
                last_ms = _latest_ts_ms(df)
                with _test_state["lock"]:
                    _test_state["last_tick_ms"] = last_ms
                    _test_state["tick_count"] += 1
                    _test_state["mock_tick_count"] += 1
        # 真实睡眠间隔 = interval_sec / acceleration
        # interval_sec 与真实循环查询同口径（用户在「A股免费实时行情」配置面板设置）
        accel = max(1, int(cfg.get("acceleration", 1)))
        sleep_sec = max(0.05, interval_sec / accel)
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
                    # 与 mock loop 一致：用最新 tick 自带的 ts_ms 作为「最近 tick」，
                    # 而不是 time.time()。base provider 已用 _stamp_poll_time 覆盖
                    # ts_ms 为本地轮询时间，与原行为等价但更精确。
                    last_ms = _latest_ts_ms(df)
                    with _test_state["lock"]:
                        _test_state["last_tick_ms"] = last_ms
                        _test_state["tick_count"] += 1
                        _test_state["real_tick_count"] += 1
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
                def _overbought_callback(df, source: str = "real") -> None:
                    try:
                        repo = _get_repo_from_app()
                    except Exception:
                        return
                    sigs = overbought_oversold.evaluate(df, repo)
                    if sigs:
                        _push_overbought_signals(sigs, source=source)

                fr_scheduler.FreeRealtimeScheduler.register_module_callback(_overbought_callback)

                # 超买超卖 collector：只返回本模块 enabled 标的清单。
                # 调度器内部 collector 会自动把 持仓T + 自选股 + 超买超卖 三者去重合并。
                def _overbought_collector() -> list[str]:
                    return fr_config.list_overbought_enabled_symbols()

                scheduler.register_collector(_overbought_collector)
                logger.info("overbought_oversold attached to free_realtime")
        except Exception as e:
            logger.warning("overbought_oversold attach failed: %s", e)

        fr_scheduler.auto_start_if_enabled()

        # 收盘报告定时守护线程（内部判断开关，未开启时空转）
        from app.services.free_realtime import report as oo_report
        oo_report.auto_start_report_scheduler()
    except Exception as e:
        logger.warning("free_realtime scheduler auto-start failed: %s", e)


def _get_repo_from_app():
    """从 FastAPI app.state 取 KlineRepository（仅在请求上下文外调用）。

    优先从 main 模块全局 app.state 取（启动后已就绪）；
    若该路径失败（如测试环境未初始化 app.state），返回 None 由调用方兜底。
    """
    try:
        from app.main import app

        repo = getattr(app.state, "repo", None)
        if repo is not None:
            return repo
    except Exception as e:
        logger.debug("get repo from app.state failed: %s", e)
    return None


# 同股同方向推送冷却：60s 内同标的同方向只推一次（mock 高频轮询会反复触发）
_oo_push_cooldown: dict[str, float] = {}
_OO_PUSH_COOLDOWN_SEC = 60.0


def _push_overbought_signals(sigs, source: str = "real") -> None:
    """超买超卖信号推送 — 复用 review_push_channels。mock 链路带 [MOCK] 前缀。

    消息抑制：同股同方向 60s 内只推送一次。
    """
    try:
        import time as _time

        from app.services import preferences, webhook_adapter
        from app.services.free_realtime.overbought_oversold import to_dict

        channels = preferences.get_review_push_channels()
        if not channels:
            return
        now_s = _time.time()
        pending: list[dict] = []
        for s in sigs:
            d = to_dict(s)
            key = f"{d['symbol']}:{d['direction']}"
            last = _oo_push_cooldown.get(key, 0.0)
            if now_s - last < _OO_PUSH_COOLDOWN_SEC:
                continue
            _oo_push_cooldown[key] = now_s
            pending.append(d)
        if not pending:
            return
        mock_tag = "[MOCK] " if source == "mock" else ""
        # 中文名兜底：mock 链路 name 可能回落为代码本身；
        # 这里从超买超卖标的清单（含 name 字段）反查中文名，避免 "300757.SZ(300757.SZ)"
        try:
            from app.services.free_realtime import config as fr_config

            name_map = {
                it.get("symbol"): it.get("name")
                for it in fr_config.list_overbought_symbols()
                if it.get("symbol") and it.get("name")
            }
        except Exception:
            name_map = {}
        lines = []
        for d in pending:
            arrow = "↑超买" if d["direction"] == "overbought" else "↓超卖"
            name = d["name"]
            if not name or name == d["symbol"]:
                name = name_map.get(d["symbol"], "")
            # 中文名在前；确实查不到中文名时只展示一次代码，不重复
            head = (
                f"{name}({d['symbol']})" if name and name != d["symbol"]
                else d["symbol"]
            )
            lines.append(
                f"- {head} 现 {d['last_price']:.2f} "
                f"MA5 {d['ma5']:.2f} 偏离 {d['deviation_pct']:+.2f}% "
                f"阈值 {d['threshold_pct']:.2f}% {arrow}"
            )
        body = "\n".join(lines)
        title = f"{mock_tag}超买超卖提示"
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
