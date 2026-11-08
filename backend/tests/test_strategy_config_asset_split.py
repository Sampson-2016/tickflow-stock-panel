"""策略配置按资产类型区分保存 (stock/etf) + 公共配置兜底 + 旧配置迁移。"""
import json

from app.strategy import config as strategy_config


def _override_path(tmp_path, sid):
    return tmp_path / "user_data" / "strategy_overrides" / f"{sid}.json"


def test_legacy_flat_override_applies_to_stock_only(tmp_path):
    """旧版单份配置只作为 stock 配置, ETF 从代码默认值开始。"""
    p = _override_path(tmp_path, "custom_x")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"params": {"price_min": 20, "price_max": 200}}), encoding="utf-8")

    assert strategy_config.load_override(tmp_path, "custom_x", "stock") == {
        "params": {"price_min": 20, "price_max": 200},
    }
    assert strategy_config.load_override(tmp_path, "custom_x", "etf") == {}
    # 兼容旧调用: 不传资产类型按 stock
    assert strategy_config.load_override(tmp_path, "custom_x") == {
        "params": {"price_min": 20, "price_max": 200},
    }


def test_save_asset_override_is_independent(tmp_path):
    sid = "custom_y"
    strategy_config.save_override(
        tmp_path, sid, {"params": {"price_min": 20, "price_max": 200}}, "stock"
    )
    strategy_config.save_override(
        tmp_path, sid, {"params": {"price_min": 0.1, "price_max": 3}}, "etf"
    )

    assert strategy_config.load_override(tmp_path, sid, "stock")["params"] == {
        "price_min": 20, "price_max": 200,
    }
    assert strategy_config.load_override(tmp_path, sid, "etf")["params"] == {
        "price_min": 0.1, "price_max": 3,
    }

    raw = json.loads(_override_path(tmp_path, sid).read_text(encoding="utf-8"))
    assert raw["schema"] == 2
    assert raw["asset"]["stock"]["params"]["price_min"] == 20
    assert raw["asset"]["etf"]["params"]["price_min"] == 0.1


def test_common_config_is_field_level_fallback(tmp_path):
    """公共配置与资产配置按字段深度合并, 资产配置优先。"""
    p = _override_path(tmp_path, "custom_z")
    p.parent.mkdir(parents=True)
    p.write_text(
        json.dumps({
            "schema": 2,
            "common": {"params": {"price_min": 10, "volume_min": 5000}, "stop_loss": -0.08},
            "asset": {"etf": {"params": {"price_min": 0.1, "price_max": 3}}},
        }),
        encoding="utf-8",
    )

    etf = strategy_config.load_override(tmp_path, "custom_z", "etf")
    assert etf["params"] == {"price_min": 0.1, "volume_min": 5000, "price_max": 3}
    assert etf["stop_loss"] == -0.08
    stock = strategy_config.load_override(tmp_path, "custom_z", "stock")
    assert stock["params"] == {"price_min": 10, "volume_min": 5000}


def test_reset_one_asset_keeps_other(tmp_path):
    sid = "custom_r"
    strategy_config.save_override(tmp_path, sid, {"params": {"price_min": 20}}, "stock")
    strategy_config.save_override(tmp_path, sid, {"params": {"price_min": 0.1}}, "etf")

    strategy_config.delete_override(tmp_path, sid, "etf")
    assert strategy_config.load_override(tmp_path, sid, "etf") == {}
    assert strategy_config.load_override(tmp_path, sid, "stock")["params"]["price_min"] == 20

    strategy_config.delete_override(tmp_path, sid)
    assert not _override_path(tmp_path, sid).exists()
    assert strategy_config.load_override(tmp_path, sid, "stock") == {}


def test_list_overrides_resolves_by_asset_type(tmp_path):
    strategy_config.save_override(tmp_path, "custom_a", {"params": {"p": 1}}, "stock")
    strategy_config.save_override(tmp_path, "custom_a", {"params": {"p": 0.01}}, "etf")
    strategy_config.save_override(tmp_path, "custom_b", {"params": {"p": 2}}, "stock")

    stock = strategy_config.list_overrides(tmp_path, "stock")
    etf = strategy_config.list_overrides(tmp_path, "etf")
    assert stock["custom_a"]["params"]["p"] == 1
    assert stock["custom_b"]["params"]["p"] == 2
    assert etf["custom_a"]["params"]["p"] == 0.01
    assert "custom_b" not in etf


def test_load_config_bundle_contains_all_variants(tmp_path):
    sid = "custom_bundle"
    strategy_config.save_override(tmp_path, sid, {"params": {"price_min": 20}}, "stock")
    strategy_config.save_override(tmp_path, sid, {"params": {"price_min": 0.1}}, "etf")

    bundle = strategy_config.load_config_bundle(tmp_path, sid)
    assert bundle["schema"] == 2
    assert bundle["asset"]["stock"]["params"]["price_min"] == 20
    assert bundle["asset"]["etf"]["params"]["price_min"] == 0.1
