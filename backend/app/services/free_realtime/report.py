"""超买超卖收盘报告 — 基于日 K 收盘价的三类偏离报告。

与盘中实时评估（overbought_oversold.evaluate）同一 MA5 口径：
  - MA5 = (前 4 个交易日 raw_close + 当日收盘价) / 5
  - 偏离 = (close - ma5) / ma5 × 100

三类判定（单股阈值优先，全局阈值兜底）:
  ① reached  到达阈值: |偏离| ≥ 阈值（与盘中 signals 同口径）
  ② near     接近阈值: 阈值×NEAR_RATIO(默认0.8) ≤ |偏离| < 阈值
  ③ far      远离阈值: |偏离| < 阈值×NEAR_RATIO

收盘价来源：优先日 K raw_close（收盘后管道落盘），缺失时降级用当日
最新 tick 的 last_price，并在行内标注 price_source="tick"。
报告归档: DATA_DIR/free_realtime/oo_reports/YYYY-MM-DD.json（同日覆盖）。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta
from typing import Any

import polars as pl

from app.markets import CN_TZ
from app.services.free_realtime import config as fr_config

logger = logging.getLogger(__name__)

NEAR_RATIO = 0.8  # 「接近阈值」系数：|偏离| ≥ 阈值×0.8 且未到达 归为接近

CATEGORY_LABELS = {"reached": "已到达阈值", "near": "接近阈值", "far": "远离阈值"}

DEFAULT_PROMPT = """你是一名 A 股量化助手。以下是基于收盘数据计算的超买超卖监测报告，
数据已按 5 日均线偏离度规则分好三类，数字以输入为准，禁止修改或新增任何数字。

【报告输入】
{input}

【任务】
1. 总体概况：一句话概括当前监测池整体偏离状态（超买多还是超卖多、集中在哪些方向）。
2. 重点提示：若有多个标的同方向聚集，指出这种聚集可能代表的含义；
   若某标的偏离度远超阈值（≥ 阈值 × 1.5），单独点名提示波动风险。
3. 风险声明：结尾固定加一句"以上为规则信号解读，不构成投资建议"。

【要求】
- 中文，120~250 字，不分小标题，用连贯段落。
- 三类清单为空时只输出"今日监测标的均未触发显著偏离"。
- 不使用 markdown 标题格式，纯文本（要推送到飞书）。"""


# ── 收盘价获取 ─────────────────────────────────────────────

def _daily_close(repository, symbol: str) -> float | None:
    """取最近一个交易日的日 K raw_close（不含今日之前15日窗口内）。"""
    end = date.today()
    start = end - timedelta(days=15)
    try:
        df: pl.DataFrame = repository.get_daily(
            symbol, start, end, columns=["date", "raw_close"]
        )
    except Exception as e:
        logger.warning("oo_report load daily %s failed: %s", symbol, e)
        return None
    if df.is_empty() or "raw_close" not in df.columns:
        return None
    series = df.sort("date")["raw_close"].to_list()
    for v in reversed(series):
        if v is not None and v > 0:
            return float(v)
    return None


def _tick_close(scheduler, symbol: str) -> float | None:
    """降级口径：当日最新 tick 的 last_price（real 优先，mock 兜底）。"""
    for kind in ("real", "mock"):
        try:
            df = scheduler.store().list_today(symbol, kind=kind)
        except Exception:
            continue
        if not df.is_empty():
            row = df.tail(1).row(0, named=True)
            last = row.get("last_price")
            if last is not None and float(last) > 0:
                return float(last)
    return None


def _recent_closes(repository, symbol: str, n: int = 4) -> list[float]:
    """前 n 个交易日 raw_close（不含今日），与 overbought_oversold 同实现。"""
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=15)
    try:
        df: pl.DataFrame = repository.get_daily(
            symbol, start, end, columns=["date", "raw_close"]
        )
    except Exception as e:
        logger.warning("oo_report load %s failed: %s", symbol, e)
        return []
    if df.is_empty() or "raw_close" not in df.columns:
        return []
    series = df.sort("date").tail(n)["raw_close"].to_list()
    return [v for v in series if v is not None and v > 0]


# ── 三类评估 ───────────────────────────────────────────────

def evaluate_close(
    symbol: str,
    name: str,
    close: float,
    closes_prev: list[float],
    overbought: float,
    oversold: float,
) -> dict[str, Any] | None:
    """对单个标的按收盘价评估，返回三类之一（含方向），数据不足返回 None。"""
    if len(closes_prev) < 4 or close <= 0:
        return None
    ma5 = (sum(closes_prev) + close) / 5.0
    if ma5 <= 0:
        return None
    deviation = (close - ma5) / ma5 * 100.0
    # 方向与阈值：偏离为正看超买阈值，为负看超卖阈值
    direction = "overbought" if deviation >= 0 else "oversold"
    threshold = overbought if deviation >= 0 else oversold
    abs_dev = abs(deviation)
    if abs_dev >= threshold:
        category = "reached"
    elif abs_dev >= threshold * NEAR_RATIO:
        category = "near"
    else:
        category = "far"
    return {
        "symbol": symbol,
        "name": name,
        "close": close,
        "ma5": round(ma5, 4),
        "deviation_pct": round(deviation, 4),
        "threshold_pct": threshold,
        "direction": direction,
        "category": category,
    }


def collect_report_rows(
    scheduler,
    repository,
) -> tuple[list[dict[str, Any]], int]:
    """对超买超卖模块全部 enabled 标的做收盘评估。

    Returns:
        (rows, evaluated)：rows 为含 category 的评估行（含数据不足被跳过的
        不在 rows 里），evaluated 为参与评估的标的数。
    """
    from app.services.free_realtime.overbought_oversold import (
        is_a_stock_candidate,
    )

    default_ob = fr_config.get_overbought_pct()
    default_os = fr_config.get_oversold_pct()
    per_symbol: dict[str, tuple[float, float]] = {}
    for it in fr_config.list_overbought_symbols():
        ob = it.get("overbought_pct")
        os_ = it.get("oversold_pct")
        per_symbol[it["symbol"]] = (
            float(ob) if ob is not None else default_ob,
            float(os_) if os_ is not None else default_os,
        )

    rows: list[dict[str, Any]] = []
    evaluated = 0
    for item in fr_config.list_overbought_symbols():
        symbol = str(item.get("symbol") or "")
        if not item.get("enabled", False):
            continue
        if not is_a_stock_candidate(symbol):
            continue
        overbought, oversold = per_symbol.get(symbol, (default_ob, default_os))
        # 收盘价：日 K 优先，缺失降级 tick
        close = _daily_close(repository, symbol)
        price_source = "daily"
        if close is None:
            close = _tick_close(scheduler, symbol)
            price_source = "tick"
        if close is None:
            continue
        closes_prev = _recent_closes(repository, symbol, 4)
        evaluated += 1
        row = evaluate_close(
            symbol,
            str(item.get("name") or symbol),
            close,
            closes_prev,
            overbought,
            oversold,
        )
        if row is None:
            continue
        row["price_source"] = price_source
        rows.append(row)
    return rows, evaluated


# ── 报告组装 ───────────────────────────────────────────────

def _row_line(row: dict[str, Any]) -> str:
    hint = ""
    if row["category"] == "reached":
        hint = (
            "→ 可考虑卖出/止盈"
            if row["direction"] == "overbought"
            else "→ 可考虑买入/关注"
        )
    elif row["category"] == "near":
        hint = "→ 加快观察"
    src = "" if row.get("price_source") == "daily" else " (tick口径)"
    return (
        f"{row['symbol']} {row['name']} | 收盘 {row['close']:.2f} | "
        f"MA5 {row['ma5']:.2f} | 偏离 {row['deviation_pct']:+.2f}% | "
        f"阈值 {row['threshold_pct']:.2f}%{src}{hint}"
    )


def render_text(report: dict[str, Any], categories: list[str]) -> str:
    """把结构化报告渲染为推送文本（只包含勾选的类别）。"""
    now = report.get("as_of", "")
    lines = [f"【超买超卖收盘报告】{now}", ""]
    for cat in ("reached", "near", "far"):
        if cat not in categories:
            continue
        items = report.get(cat) or []
        label = CATEGORY_LABELS[cat]
        if not items:
            lines.append(f"◆ {label}：无")
        else:
            lines.append(f"◆ {label}（{len(items)} 只）")
            lines.extend(_row_line(r) for r in items)
        lines.append("")
    if report.get("ai_commentary"):
        lines.append("【AI 总结】")
        lines.append(report["ai_commentary"])
    return "\n".join(lines).strip()


def _ai_commentary(rows: list[dict[str, Any]], categories: list[str]) -> str | None:
    """按配置提示词生成 AI 总结；未配置 Key / 失败返回 None（降级）。"""
    from app.services import ai_provider

    if not ai_provider.ai_configured():
        return None
    prompt = fr_config.get_oo_report_prompt().strip() or DEFAULT_PROMPT
    fact_lines: list[str] = []
    for cat in categories:
        items = [r for r in rows if r["category"] == cat]
        fact_lines.append(f"{CATEGORY_LABELS[cat]}（{len(items)} 只）")
        fact_lines.extend(_row_line(r) for r in items)
    try:
        import asyncio

        from app.services.ai_provider import Message

        # 端点跑在 AnyIO worker 线程（无事件循环），必须用 asyncio.run 自建循环；
        # get_event_loop().run_until_complete() 会抛 "no current event loop"
        text = asyncio.run(
            ai_provider.generate_ai_text(
                [
                    Message(role="system", content=prompt),
                    Message(role="user", content="\n".join(fact_lines)),
                ],
                # 超时与复盘模块一致：不传，走 ai_provider 默认 180s
            )
        )
        return text.strip() or None
    except Exception as e:
        logger.warning("oo_report ai commentary failed: %s", e)
        return None


def generate_report(
    categories: list[str] | None = None,
    *,
    push: bool = True,
) -> dict[str, Any]:
    """生成收盘报告：评估 → 组装 → AI 总结 → 归档 → 推送。

    Args:
        categories: 勾选的类别子集，缺省用已存偏好。
        push: 是否尝试推送飞书（未配置 webhook 时静默跳过）。

    Returns:
        完整报告 dict（三类清单 + ai_commentary + as_of + text）。
    """
    from app.custom.free_realtime_ext import _get_repo_from_app
    from app.services.free_realtime import scheduler as fr_scheduler

    if categories is None:
        categories = fr_config.get_oo_report_categories()
    categories = [c for c in ("reached", "near", "far") if c in categories]

    scheduler = fr_scheduler.FreeRealtimeScheduler.get_instance()
    repo = _get_repo_from_app()
    if repo is None:
        raise RuntimeError("repository not ready")

    rows, evaluated = collect_report_rows(scheduler, repo)
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    report: dict[str, Any] = {
        "as_of": now,
        "trade_date": now[:10],
        "categories": categories,
        "evaluated": evaluated,
        "reached": [r for r in rows if r["category"] == "reached"],
        "near": [r for r in rows if r["category"] == "near"],
        "far": [r for r in rows if r["category"] == "far"],
        "ai_commentary": None,
    }
    if fr_config.get_oo_report_ai_enabled():
        report["ai_commentary"] = _ai_commentary(rows, categories)

    report["text"] = render_text(report, categories)
    archive(report)
    if push:
        push_report(report["text"])
    return report


# ── 归档 ───────────────────────────────────────────────────

def _archive_dir():
    from app.config import settings

    return settings.data_dir / "free_realtime" / "oo_reports"


def archive(report: dict[str, Any]) -> None:
    """按生成时间归档（每次生成一份，不覆盖；文件名 YYYY-MM-DD_HHMMSS.json）。"""
    d = _archive_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CN_TZ).strftime("%Y-%m-%d_%H%M%S")
    path = d / f"{stamp}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_archives() -> list[dict[str, Any]]:
    """归档列表（按文件名时间戳倒序，含摘要）。"""
    d = _archive_dir()
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append(
            {
                "file": p.stem,
                "trade_date": data.get("trade_date", p.stem[:10]),
                "as_of": data.get("as_of"),
                "categories": data.get("categories"),
                "evaluated": data.get("evaluated"),
                "counts": {
                    c: len(data.get(c) or []) for c in ("reached", "near", "far")
                },
                "has_ai": bool(data.get("ai_commentary")),
            }
        )
    return out


def load_archive(file_or_date: str) -> dict[str, Any] | None:
    """按归档文件名（YYYY-MM-DD_HHMM）或旧版日期名加载。"""
    p = _archive_dir() / f"{file_or_date}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── 推送 ───────────────────────────────────────────────────

def push_report(text: str) -> bool:
    """推送飞书；未配置 webhook 静默跳过，失败仅告警。"""
    try:
        from app.services import preferences, webhook_adapter

        url = preferences.get_feishu_webhook_url()
        if not url:
            return False
        secret = preferences.get_feishu_webhook_secret()
        webhook_adapter.send_feishu_card(
            url, "超买超卖收盘报告", datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M"), text, secret
        )
        return True
    except Exception as e:
        logger.warning("oo_report push failed: %s", e)
        return False


# ── 定时守护线程 ───────────────────────────────────────────

_report_thread: threading.Thread | None = None
_report_thread_lock = threading.Lock()


def _is_trading_day_today() -> bool:
    """简化判断：周一~周五（法定节假日不做校验，与 free_realtime 交易时段口径一致）。"""
    return datetime.now(CN_TZ).weekday() < 5


def _scheduled_fire_today() -> bool:
    """今天到点后是否已生成过（按归档文件名日期前缀匹配）。"""
    today = date.today().isoformat()
    return any(a.get("trade_date") == today for a in list_archives())


def _report_watch_loop(stop_event: threading.Event) -> None:
    """每 60s 检查一次：开关开 + 交易日 + 到点 + 今日未生成 → 生成并推送。"""
    while not stop_event.wait(60):
        try:
            if not fr_config.get_oo_report_schedule_enabled():
                continue
            if not _is_trading_day_today():
                continue
            target = fr_config.get_oo_report_schedule_time()
            now_hm = datetime.now(CN_TZ).strftime("%H:%M")
            if now_hm < target:
                continue
            if _scheduled_fire_today():
                continue
            logger.info("oo_report scheduled generation fired at %s", now_hm)
            generate_report()
        except Exception as e:
            logger.warning("oo_report schedule loop error: %s", e)


def auto_start_report_scheduler() -> None:
    """启动收盘报告守护线程（幂等；进程内单例）。"""
    global _report_thread
    with _report_thread_lock:
        if _report_thread is not None and _report_thread.is_alive():
            return
        stop_event = threading.Event()
        _report_thread = threading.Thread(
            target=_report_watch_loop, args=(stop_event,),
            name="oo-report-scheduler", daemon=True,
        )
        _report_thread.start()
        logger.info("oo_report scheduler thread started")
