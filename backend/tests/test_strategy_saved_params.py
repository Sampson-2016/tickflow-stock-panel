from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import HTTPException

from app.api import strategy as strategy_api
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyDataContext, StrategyDef, StrategyEngine


def _make_engine() -> tuple[StrategyEngine, StrategyDataContext]:
    df = pl.DataFrame({"symbol": ["A", "B", "C"], "value": [1, 2, 3]})
    engine = StrategyEngine(strategy_dirs=[])
    engine._strategies["saved_params"] = StrategyDef(
        meta={"id": "saved_params", "scoring": {}, "limit": 100},
        basic_filter={"enabled": False},
        entry_signals=[],
        exit_signals=[],
        stop_loss=None,
        trailing_stop=None,
        trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None,
        max_hold_days=None,
        filter_fn=lambda _df, params: pl.col("value") >= params.get("min_value", 1),
        filter_history_fn=None,
        lookback_days=1,
        source="custom",
    )
    return engine, StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=date(2026, 7, 15),
        current=df,
    )


def test_run_applies_saved_strategy_params():
    engine, context = _make_engine()
    result = engine.run(
        "saved_params",
        context,
        overrides={"params": {"min_value": 2}},
    )

    assert [row["symbol"] for row in result.rows] == ["B", "C"]


def test_explicit_params_override_saved_strategy_params():
    engine, context = _make_engine()
    result = engine.run(
        "saved_params",
        context,
        params={"min_value": 3},
        overrides={"params": {"min_value": 2}},
    )

    assert [row["symbol"] for row in result.rows] == ["C"]


def test_patch_config_preserves_other_user_overrides(tmp_path):
    engine, _ = _make_engine()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        strategy_engine=engine,
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
    )))
    strategy_config.save_override(tmp_path, "saved_params", {
        "params": {"min_value": 2},
        "stop_loss": -0.05,
    })

    strategy_api.patch_config(strategy_api.SaveConfigRequest(
        strategy_id="saved_params",
        overrides={
            "scoring": {"rsi_14": 1.0},
            "scoring_directions": {"rsi_14": "low"},
            "scoring_replace": True,
        },
    ), request)

    saved = strategy_config.load_override(tmp_path, "saved_params")
    assert saved["params"] == {"min_value": 2}
    assert saved["stop_loss"] == -0.05
    assert saved["scoring"] == {"rsi_14": 1.0}
    assert saved["scoring_directions"] == {"rsi_14": "low"}


def test_save_config_rejects_invalid_scoring_direction(tmp_path):
    engine, _ = _make_engine()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        strategy_engine=engine,
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
    )))

    with pytest.raises(HTTPException, match="方向无效"):
        strategy_api.save_config(strategy_api.SaveConfigRequest(
            strategy_id="saved_params",
            overrides={"scoring_directions": {"rsi_14": "sideways"}},
        ), request)


def _export_request(engine: StrategyEngine, data_dir) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        strategy_engine=engine,
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=data_dir)),
    )))


def test_export_strategy_includes_code_and_user_overrides(tmp_path):
    """下载策略必须同时带上源代码与用户修改后的全部配置。"""
    code = 'META = {"id": "custom_trend", "name": "趋势", "scoring": {}, "params": []}\n'
    path = tmp_path / "custom_trend.py"
    path.write_text(code, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[])
    engine._strategies["custom_trend"] = StrategyDef(
        meta={"id": "custom_trend", "name": "趋势", "scoring": {}, "params": []},
        basic_filter={"enabled": False},
        entry_signals=[],
        exit_signals=[],
        stop_loss=None,
        trailing_stop=None,
        trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None,
        max_hold_days=None,
        filter_fn=lambda _df, _params: pl.lit(True),
        filter_history_fn=None,
        lookback_days=1,
        source="custom",
        file_path=path,
    )
    data_dir = tmp_path / "data"
    overrides = {
        "name": "我的趋势",
        "basic_filter": {"amount_min": 1e8},
        "params": {"n": 20},
        "scoring": {"rsi_14": 1.0},
        "scoring_directions": {"rsi_14": "low"},
        "scoring_replace": True,
        "stop_loss": -0.05,
        "max_hold_days": 5,
        "entry_signals": ["rsi_oversold"],
        "display_limit": 30,
        "scope": {"mode": "watchlist_group", "group_ids": ["g1"]},
    }
    strategy_config.save_override(data_dir, "custom_trend", overrides)

    out = strategy_api.export_strategy("custom_trend", _export_request(engine, data_dir))

    assert out["version"] == 1
    assert out["strategy_id"] == "custom_trend"
    assert out["source"] == "custom"
    assert out["name"] == "我的趋势"
    assert out["code"] == code
    # 修改后的基础参数、策略参数、评分/方向、信号、止损、范围等全部随文件导出
    assert out["config"] == overrides


def test_export_strategy_rejects_builtin(tmp_path):
    engine = StrategyEngine(strategy_dirs=[])
    engine._strategies["builtin_x"] = StrategyDef(
        meta={"id": "builtin_x", "name": "内置", "scoring": {}, "params": []},
        basic_filter={"enabled": False},
        entry_signals=[],
        exit_signals=[],
        stop_loss=None,
        trailing_stop=None,
        trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None,
        max_hold_days=None,
        filter_fn=None,
        filter_history_fn=None,
        lookback_days=1,
        source="builtin",
    )

    with pytest.raises(HTTPException) as exc:
        strategy_api.export_strategy("builtin_x", _export_request(engine, tmp_path))

    assert exc.value.status_code == 400
