from __future__ import annotations

from app.services import strategy_cache


def _result(*symbols: str) -> dict:
    return {
        "total": len(symbols),
        "as_of": "2026-07-20",
        "rows": [{"symbol": symbol, "close": index + 1.0} for index, symbol in enumerate(symbols)],
    }


def test_same_day_partial_writes_merge_strategy_results(tmp_path):
    strategy_cache.write_cache(tmp_path, "2026-07-20", {"strategy_a": _result("000001.SZ")})
    strategy_cache.write_cache(tmp_path, "2026-07-20", {"strategy_b": _result("600000.SH")})

    cached = strategy_cache.read_cache(tmp_path)

    assert set(cached["results"]) == {"strategy_a", "strategy_b"}
    assert cached["results"]["strategy_a"]["rows"][0]["symbol"] == "000001.SZ"
    assert cached["results"]["strategy_b"]["rows"][0]["symbol"] == "600000.SH"


def test_same_day_update_replaces_only_target_strategy_and_keeps_ever_rows(tmp_path):
    strategy_cache.write_cache(tmp_path, "2026-07-20", {
        "strategy_a": _result("000001.SZ"),
        "strategy_b": _result("600000.SH"),
    })
    strategy_cache.write_cache(tmp_path, "2026-07-20", {"strategy_a": _result("000002.SZ")})

    cached = strategy_cache.read_cache(tmp_path)

    assert [row["symbol"] for row in cached["results"]["strategy_a"]["rows"]] == ["000002.SZ"]
    assert [row["symbol"] for row in cached["results"]["strategy_b"]["rows"]] == ["600000.SH"]
    assert set(cached["today_ever_rows"]["strategy_a"]) == {"000001.SZ", "000002.SZ"}


def test_new_date_resets_results_and_ever_rows(tmp_path):
    strategy_cache.write_cache(tmp_path, "2026-07-20", {"strategy_a": _result("000001.SZ")})
    next_day = _result("600000.SH")
    next_day["as_of"] = "2026-07-21"

    strategy_cache.write_cache(tmp_path, "2026-07-21", {"strategy_b": next_day})
    cached = strategy_cache.read_cache(tmp_path)

    assert cached["as_of"] == "2026-07-21"
    assert set(cached["results"]) == {"strategy_b"}
    assert set(cached["today_ever_rows"]) == {"strategy_b"}


def test_asset_types_are_isolated(tmp_path):
    """股票/ETF 缓存互相隔离: ETF 单跑/批量结果不得覆盖股票缓存 (反之亦然)。"""
    strategy_cache.write_cache(tmp_path, "2026-07-20", {"strategy_a": _result("000001.SZ")}, "cn", "stock")
    strategy_cache.write_cache(tmp_path, "2026-07-20", {"strategy_a": _result("510300.SH")}, "cn", "etf")

    stock = strategy_cache.read_cache(tmp_path, "cn", "stock")
    etf = strategy_cache.read_cache(tmp_path, "cn", "etf")
    assert stock["results"]["strategy_a"]["rows"][0]["symbol"] == "000001.SZ"
    assert etf["results"]["strategy_a"]["rows"][0]["symbol"] == "510300.SH"

    # stock 沿用原文件名 (向后兼容既有用户缓存), etf 独立成文件
    assert (tmp_path / "user_data" / "strategy_cache.json").exists()
    assert (tmp_path / "user_data" / "strategy_cache_etf.json").exists()

    # clear_cache 不带 asset_type → 清全部资产类型变体
    strategy_cache.clear_cache(tmp_path)
    assert strategy_cache.read_cache(tmp_path, "cn", "stock") is None
    assert strategy_cache.read_cache(tmp_path, "cn", "etf") is None
